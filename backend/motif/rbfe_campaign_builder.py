#!/usr/bin/env python3
"""Prepare one user-supplied receptor and an aligned ligand series for RBFE.

This fixed subprocess runs inside the pinned OpenFE environment.  The browser
supplies ordinary scientific inputs (PDB text and SMILES); this module owns the
conversion to a cleaned/protonated receptor and receptor-frame SDF poses.

The alignment route is intentionally conservative: it requires a bound
reference ligand in the experimental coordinates and a substantial MCS to each
analogue.  It produces pose hypotheses, never a binding or FEP claim.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
import sys
import tempfile

from openmm.app import PDBFile
from pdbfixer import PDBFixer
from rdkit import Chem
from rdkit.Chem import AllChem, rdFMCS

from rbfe_campaign_state import (canonical_digest_bundle, dependency_dag,
                                 normalize_ligand_series, sha256_digest,
                                 stage_payload)


_MAX_PDB_BYTES = 8 << 20
_WATER_NAMES = {"HOH", "WAT", "DOD"}
_ION_NAMES = {
    "NA", "CL", "K", "CA", "MG", "MN", "ZN", "FE", "CU", "CO", "NI",
    "CD", "HG", "BR", "IOD", "SO4", "PO4", "GOL", "EDO",
}
_METAL_ELEMENTS = {
    "LI", "NA", "K", "RB", "CS", "BE", "MG", "CA", "SR", "BA",
    "AL", "GA", "IN", "SN", "PB", "BI", "SC", "TI", "V", "CR",
    "MN", "FE", "CO", "NI", "CU", "ZN", "Y", "ZR", "NB", "MO",
    "TC", "RU", "RH", "PD", "AG", "CD", "HF", "TA", "W", "RE",
    "OS", "IR", "PT", "AU", "HG",
}
_PAIR_WITNESS_LIMIT = 32
_SUPPORTED_FORCEFIELD_CONTRACT = {
    "protein": "AMBER ff14SB",
    "ligand": "OpenFF 2.2.1",
    "water": "TIP3P",
    "ionic_strength_molar": 0.15,
    "release": "openfe-rfe-standard-v1",
}


def _sha(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical(molecule: Chem.Mol) -> str:
    return Chem.MolToSmiles(Chem.RemoveHs(molecule), canonical=True,
                            isomericSmiles=True)


def _selector_key(selector: dict) -> tuple[str, str, str]:
    return (str(selector.get("resname") or "").upper(),
            str(selector.get("chain") or "").strip(),
            str(selector.get("residue_number") or "").strip())


def _pdb_atom(line: str) -> dict | None:
    """Decode the fixed-width fields used by every receptor-policy witness."""
    if not line.startswith(("ATOM  ", "HETATM")) or len(line) < 54:
        return None
    try:
        xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
    except ValueError:
        return None
    occupancy = None
    try:
        occupancy = float(line[54:60])
    except (ValueError, IndexError):
        pass
    element = (line[76:78].strip() if len(line) >= 78 else
               line[12:14].strip()).upper()
    serial = None
    try:
        serial = int(line[6:11])
    except ValueError:
        pass
    return {
        "record": line[:6].strip(), "serial": serial,
        "atom_name": line[12:16].strip(), "altloc": line[16:17].strip(),
        "residue_name": line[17:20].strip().upper(),
        "chain_id": line[21:22].strip(),
        "residue_number": line[22:26].strip(),
        "insertion_code": line[26:27].strip(),
        "occupancy": occupancy, "element": element, "xyz": xyz,
        "line": line,
    }


def _entity_key(atom: dict) -> tuple[str, str, str, str]:
    return (atom["residue_name"], atom["chain_id"], atom["residue_number"],
            atom["insertion_code"])


def _site_id(key: tuple[str, str, str, str]) -> str:
    resname, chain, residue, insertion = key
    return f"{resname}:{chain or '_'}:{residue}{insertion}"


def _atom_witness(atom: dict) -> dict:
    return {key: atom.get(key) for key in (
        "record", "serial", "atom_name", "element", "residue_name",
        "chain_id", "residue_number", "insertion_code", "altloc",
        "occupancy",
    )}


def _entity_witness(key: tuple[str, str, str, str], atoms: list[dict]) -> dict:
    elements = sorted({str(atom.get("element") or "") for atom in atoms
                       if atom.get("element")})
    return {
        "site_id": _site_id(key), "residue_name": key[0],
        "chain_id": key[1], "residue_number": key[2],
        "insertion_code": key[3], "atom_count": len(atoms),
        "heavy_atom_count": sum(atom.get("element") not in {"H", "D"}
                                for atom in atoms),
        "elements": elements,
    }


def _format_entity_witnesses(label: str, witnesses: list[dict]) -> str:
    sites = ", ".join(row["site_id"] for row in witnesses[:10])
    suffix = f" (+{len(witnesses) - 10} more)" if len(witnesses) > 10 else ""
    return f"{label}: {sites or 'none'}{suffix}"


def _decision_for_water(witness: dict, decisions: list[dict]) -> str | None:
    matches = []
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("each water_site_decision must be an object")
        if decision.get("site_id") is not None:
            matched = str(decision["site_id"]) == witness["site_id"]
        else:
            matched = (
                str(decision.get("chain") if decision.get("chain") is not None
                    else decision.get("chain_id") or "").strip()
                == witness["chain_id"]
                and str(decision.get("residue_number") or "").strip()
                == witness["residue_number"]
                and str(decision.get("insertion_code") or "").strip()
                == witness["insertion_code"]
                and (not decision.get("resname")
                     or str(decision["resname"]).upper() == witness["residue_name"])
            )
        if matched:
            matches.append(str(decision.get("decision") or "").lower())
    if len(matches) > 1:
        raise ValueError(
            f"water {witness['site_id']} has duplicate site decisions")
    if not matches:
        return None
    if matches[0] not in {"keep", "remove"}:
        raise ValueError(
            f"water {witness['site_id']} decision must be keep or remove")
    return matches[0]


def _apply_coordinate_policy(pdb_text: str, receptor_policy: dict,
                             selector: dict) -> tuple[str, dict]:
    """Apply every coordinate-carrier policy before PDBFixer can erase evidence.

    Successful return is deliberately strong: every axis handled here has an
    observed action and a witness.  Any policy that would require an unavailable
    human or parameterisation decision fails before pose hypotheses are created.
    """
    assembly_id = str(receptor_policy.get("assembly_id") or "")
    if assembly_id != "deposited_asymmetric_unit":
        raise ValueError(
            "receptor_policy.assembly_id is UNVERIFIED: this release consumes only "
            "the deposited_asymmetric_unit coordinate carrier; biological assembly "
            f"{assembly_id!r} was not generated")
    model_count = sum(line.startswith("MODEL") for line in pdb_text.splitlines())
    if model_count > 1:
        raise ValueError(
            "multi-model coordinate carrier is UNVERIFIED: choose and persist one "
            f"explicit receptor model before preparation (observed {model_count} MODEL records)")
    chain_ids = [str(value) for value in receptor_policy.get("chain_ids") or []]
    atoms = [atom for line in pdb_text.splitlines()
             if (atom := _pdb_atom(line)) is not None]
    available_chains = sorted({atom["chain_id"] for atom in atoms
                               if atom["record"] == "ATOM"})
    if chain_ids:
        absent = sorted(set(chain_ids).difference(available_chains))
        if absent:
            raise ValueError(
                "receptor_policy.chain_ids are absent from the coordinate carrier: "
                f"{absent}")
        # chain_ids selects polymer chains. Experimental ligands, waters and
        # cofactors frequently occupy their own PDB chain IDs (1CBS: protein A,
        # retinoic-acid reference B); keep HETATM records until their entity
        # policies decide them explicitly.
        atoms = [atom for atom in atoms
                 if atom["record"] != "ATOM" or atom["chain_id"] in chain_ids]
    selected_chains = chain_ids or available_chains
    if not atoms:
        raise ValueError("receptor_policy.chain_ids select no coordinate atoms")

    # Resolve alternate conformers at the residue level; per-atom selection can
    # create a nonphysical hybrid of A and B conformers.
    altloc_groups: dict[tuple[str, str, str, str], dict[str, list[dict]]] = {}
    for atom in atoms:
        if not atom["altloc"]:
            continue
        residue_key = (atom["residue_name"], atom["chain_id"],
                       atom["residue_number"], atom["insertion_code"])
        altloc_groups.setdefault(residue_key, {}).setdefault(
            atom["altloc"], []).append(atom)
    altloc_policy = str(receptor_policy.get("altloc") or "")
    if altloc_policy not in {"highest_occupancy", "highest_occupancy_report",
                             "review_each", "block_ambiguous"}:
        raise ValueError(f"unsupported receptor_policy.altloc={altloc_policy!r}")
    if altloc_groups and altloc_policy in {"review_each", "block_ambiguous"}:
        witnesses = [{
            "residue_name": key[0], "chain_id": key[1],
            "residue_number": key[2], "insertion_code": key[3],
            "altlocs": sorted(options),
        } for key, options in sorted(altloc_groups.items())]
        raise ValueError(
            f"receptor_policy.altloc={altloc_policy} is UNVERIFIED for "
            f"{len(witnesses)} alternate-conformer residues; first witnesses "
            f"{witnesses[:10]}")
    altloc_choices: dict[tuple[str, str, str, str], str] = {}
    altloc_witnesses = []
    for key, options in sorted(altloc_groups.items()):
        scores = {
            altloc: sum(atom["occupancy"] if atom["occupancy"] is not None else 0.0
                        for atom in group) / max(len(group), 1)
            for altloc, group in options.items()
        }
        chosen = sorted(scores, key=lambda value: (-scores[value], value))[0]
        altloc_choices[key] = chosen
        altloc_witnesses.append({
            "residue_name": key[0], "chain_id": key[1],
            "residue_number": key[2], "insertion_code": key[3],
            "chosen_altloc": chosen,
            "mean_occupancy_by_altloc": {name: round(value, 4)
                                          for name, value in sorted(scores.items())},
        })
    retained_atoms = []
    retained_lines: dict[int, str] = {}
    for atom in atoms:
        residue_key = (atom["residue_name"], atom["chain_id"],
                       atom["residue_number"], atom["insertion_code"])
        chosen = altloc_choices.get(residue_key)
        if atom["altloc"] and chosen and atom["altloc"] != chosen:
            continue
        line = atom["line"]
        if atom["altloc"]:
            line = line[:16] + " " + line[17:]
        kept = {**atom, "altloc": "", "line": line}
        retained_atoms.append(kept)
        if atom["serial"] is not None:
            retained_lines[atom["serial"]] = line

    zero_occupancy = [atom for atom in retained_atoms
                      if atom["occupancy"] is not None
                      and atom["occupancy"] <= 0.0]
    occupancy_policy = str(receptor_policy.get("occupancy") or "")
    if occupancy_policy not in {"review_zero", "reject_zero", "keep_reported",
                                "preserve_report"}:
        raise ValueError(
            f"unsupported receptor_policy.occupancy={occupancy_policy!r}")
    if zero_occupancy and occupancy_policy in {"review_zero", "reject_zero"}:
        raise ValueError(
            f"receptor_policy.occupancy={occupancy_policy} is UNVERIFIED for "
            f"{len(zero_occupancy)} zero-occupancy atoms; first witnesses "
            f"{[_atom_witness(atom) for atom in zero_occupancy[:10]]}")

    groups: dict[tuple[str, str, str, str], list[dict]] = {}
    for atom in retained_atoms:
        groups.setdefault(_entity_key(atom), []).append(atom)
    reference_key = _selector_key(selector)
    reference_atoms = [atom for atom in retained_atoms
                       if (atom["residue_name"], atom["chain_id"],
                           atom["residue_number"]) == reference_key]
    reference_heavy = [atom for atom in reference_atoms
                       if atom["element"] not in {"H", "D"}]
    if not reference_heavy:
        raise ValueError(
            "reference ligand has no retained heavy-atom coordinates after chain/altloc policy")

    water_groups = {key: group for key, group in groups.items()
                    if key[0] in _WATER_NAMES}
    water_witnesses = []
    for key, group in sorted(water_groups.items()):
        minimum = min(
            math.dist(atom["xyz"], reference_atom["xyz"])
            for atom in group for reference_atom in reference_heavy)
        witness = _entity_witness(key, group)
        witness["minimum_distance_to_reference_angstrom"] = round(minimum, 3)
        water_witnesses.append(witness)
    pocket_waters = [row for row in water_witnesses
                     if row["minimum_distance_to_reference_angstrom"] <= 5.0]
    water_policy = receptor_policy.get("waters") or {}
    water_mode = str(water_policy.get("mode") or "")
    water_mode = {"review": "review_pocket", "keep": "keep_all",
                  "remove": "remove_all"}.get(water_mode, water_mode)
    decisions = water_policy.get("site_decisions") or []
    if water_mode not in {"review_pocket", "keep_all", "remove_all"}:
        raise ValueError(f"unsupported receptor_policy.waters.mode={water_mode!r}")
    kept_water_sites: set[str] = set()
    resolved_water_witnesses = []
    if water_mode == "review_pocket":
        for witness in pocket_waters:
            decision = _decision_for_water(witness, decisions)
            if decision is None:
                raise ValueError(
                    "receptor_policy.waters=review_pocket is UNVERIFIED because "
                    "a pocket-water entity lacks a keep/remove decision; "
                    + _format_entity_witnesses("candidate witnesses", pocket_waters))
            resolved_water_witnesses.append({**witness, "decision": decision})
            if decision == "keep":
                kept_water_sites.add(witness["site_id"])
        unmatched = [decision for decision in decisions
                     if not any(_decision_for_water(witness, [decision]) is not None
                                for witness in pocket_waters)]
        if unmatched:
            raise ValueError(
                "water_site_decisions contain entities outside the <=5 A pocket-water "
                f"candidate set: {unmatched[:10]}")
    elif decisions:
        raise ValueError(
            f"water_site_decisions are not consumed by waters.mode={water_mode}; "
            "remove them or select review_pocket")
    elif water_mode == "keep_all":
        kept_water_sites = {row["site_id"] for row in water_witnesses}

    cofactor_groups: dict[tuple[str, str, str, str], list[dict]] = {}
    metal_groups: dict[tuple[str, str, str, str], list[dict]] = {}
    for key, group in groups.items():
        if key in water_groups or ((key[0], key[1], key[2]) == reference_key):
            continue
        if not any(atom["record"] == "HETATM" for atom in group):
            continue
        elements = {atom["element"] for atom in group}
        is_metal_or_ion = (bool(elements.intersection(_METAL_ELEMENTS))
                           or (len(group) == 1 and key[0] in _ION_NAMES))
        (metal_groups if is_metal_or_ion else cofactor_groups)[key] = group
    drop_sites: set[str] = {
        row["site_id"] for row in water_witnesses
        if row["site_id"] not in kept_water_sites
    }
    entity_reports = {}
    for label, entity_groups, policy_field in (
            ("cofactors", cofactor_groups, "cofactors"),
            ("metals", metal_groups, "metals")):
        witnesses = [_entity_witness(key, group)
                     for key, group in sorted(entity_groups.items())]
        policy = str(receptor_policy.get(policy_field) or "")
        if policy not in {"remove", "review_each", "keep_parameter_gate"}:
            raise ValueError(f"unsupported receptor_policy.{policy_field}={policy!r}")
        if witnesses and policy == "review_each":
            raise ValueError(
                f"receptor_policy.{policy_field}=review_each is UNVERIFIED; "
                + _format_entity_witnesses("entity witnesses", witnesses))
        if witnesses and policy == "keep_parameter_gate":
            raise ValueError(
                f"receptor_policy.{policy_field}=keep_parameter_gate is UNVERIFIED: "
                "the supplied forcefield contract contains no executable, entity-bound "
                "parameterisation witness; "
                + _format_entity_witnesses("entity witnesses", witnesses))
        if policy == "remove":
            drop_sites.update(row["site_id"] for row in witnesses)
        entity_reports[label] = {
            "verdict": "CONFIRMED", "policy": policy,
            "observed_action": ("removed_all" if witnesses and policy == "remove"
                                else "not_applicable_no_entities"),
            "entity_count": len(witnesses), "entity_witnesses": witnesses,
        }

    selected_serials = {
        atom["serial"] for atom in retained_atoms
        if atom["serial"] is not None and _site_id(_entity_key(atom)) not in drop_sites
    }
    output_lines = []
    for line in pdb_text.splitlines():
        atom = _pdb_atom(line)
        if atom is not None:
            if (atom["record"] == "ATOM"
                    and atom["chain_id"] not in selected_chains):
                continue
            if atom["serial"] not in selected_serials:
                continue
            output_lines.append(retained_lines.get(atom["serial"], line))
            continue
        if line.startswith("CONECT"):
            try:
                values = [int(line[index:index + 5])
                          for index in range(6, len(line), 5)
                          if line[index:index + 5].strip()]
            except ValueError:
                values = []
            if values and values[0] in selected_serials:
                connected = [value for value in values[1:]
                             if value in selected_serials]
                if connected:
                    output_lines.append(
                        "CONECT" + f"{values[0]:5d}"
                        + "".join(f"{value:5d}" for value in connected))
            continue
        if line.startswith("ANISOU"):
            continue
        output_lines.append(line)
    filtered = "\n".join(output_lines).rstrip() + "\n"
    kept_water_count = sum(row["site_id"] in kept_water_sites
                           for row in water_witnesses)
    return filtered, {
        "assembly": {
            "verdict": "CONFIRMED", "requested": assembly_id,
            "observed_action": "deposited_asymmetric_unit_used",
        },
        "chains": {
            "verdict": "CONFIRMED", "available_chain_ids": available_chains,
            "selected_chain_ids": selected_chains,
            "observed_action": "unselected_polymer_chains_removed",
            "heterogen_chain_handling": (
                "HETATM entities retained across chain IDs until the explicit "
                "water/cofactor/metal policy consumes them"),
        },
        "altloc": {
            "verdict": "CONFIRMED", "policy": altloc_policy,
            "observed_action": ("highest_occupancy_residue_conformer_selected"
                                if altloc_witnesses else "not_applicable_no_altlocs"),
            "residue_witnesses": altloc_witnesses,
        },
        "occupancy": {
            "verdict": "CONFIRMED", "policy": occupancy_policy,
            "observed_action": "source_occupancies_preserved_and_reported",
            "zero_occupancy_atom_count": len(zero_occupancy),
            "zero_occupancy_atom_witnesses": [
                _atom_witness(atom) for atom in zero_occupancy[:_PAIR_WITNESS_LIMIT]],
        },
        "waters": {
            "verdict": "CONFIRMED", "policy": water_mode,
            "observed_action": ("all_kept" if water_mode == "keep_all" else
                                "all_removed" if water_mode == "remove_all" else
                                "pocket_decisions_applied_bulk_removed"),
            "water_entity_count": len(water_witnesses),
            "pocket_candidate_count": len(pocket_waters),
            "kept_water_count": kept_water_count,
            "removed_water_count": len(water_witnesses) - kept_water_count,
            "pocket_water_witnesses": resolved_water_witnesses or pocket_waters,
        },
        **entity_reports,
    }


def inspect_bound_ligands(pdb_text: str) -> list[dict]:
    groups: dict[tuple[str, str, str], list[str]] = {}
    for line in pdb_text.splitlines():
        if not line.startswith("HETATM") or len(line) < 54:
            continue
        resname, chain, residue = line[17:20].strip().upper(), line[21:22].strip(), line[22:27].strip()
        element = (line[76:78].strip() if len(line) >= 78 else line[12:14].strip()).upper()
        if resname in _WATER_NAMES or resname in _ION_NAMES or element in {"H", "D"}:
            continue
        groups.setdefault((resname, chain, residue), []).append(line)
    rows = []
    for (resname, chain, residue), lines in groups.items():
        elements = []
        for line in lines:
            element = (line[76:78].strip() if len(line) >= 78 else line[12:14].strip()).title()
            if element:
                elements.append(element)
        rows.append({
            "resname": resname, "chain": chain, "residue_number": residue,
            "heavy_atom_count": len(lines),
            "label": f"{resname} · chain {chain or '—'} · residue {residue} · {len(lines)} heavy atoms",
            "elements": elements,
        })
    return sorted(rows, key=lambda row: (-row["heavy_atom_count"], row["resname"],
                                         row["chain"], row["residue_number"]))


def _extract_reference(pdb_text: str, selector: dict, parent_smiles: str) -> Chem.Mol:
    wanted = _selector_key(selector)
    atom_lines, serials = [], set()
    for line in pdb_text.splitlines():
        if not line.startswith("HETATM") or len(line) < 54:
            continue
        key = (line[17:20].strip().upper(), line[21:22].strip(), line[22:27].strip())
        if key == wanted:
            atom_lines.append(line)
            try:
                serials.add(int(line[6:11]))
            except ValueError:
                pass
    if not atom_lines:
        raise ValueError("selected bound reference ligand is absent from the supplied PDB")
    conect = []
    for line in pdb_text.splitlines():
        if not line.startswith("CONECT"):
            continue
        try:
            values = [int(line[index:index + 5]) for index in range(6, len(line), 5)
                      if line[index:index + 5].strip()]
        except ValueError:
            continue
        if values and values[0] in serials:
            filtered = [value for value in values if value in serials]
            if len(filtered) > 1:
                conect.append("CONECT" + "".join(f"{value:5d}" for value in filtered))
    block = "\n".join([*atom_lines, *conect, "END", ""])
    observed = Chem.MolFromPDBBlock(block, sanitize=False, removeHs=False,
                                    proximityBonding=True)
    template = Chem.MolFromSmiles(parent_smiles)
    if observed is None or template is None:
        raise ValueError("bound reference ligand or Parent SMILES could not be parsed")
    # Experimental PDBs vary in whether ligand hydrogens are explicit.  Parent
    # identity and common-core coverage are heavy-atom contracts, so normalise the
    # reference to that same domain before assigning bond orders.
    observed = Chem.RemoveHs(observed, sanitize=False)
    if observed.GetNumAtoms() != template.GetNumAtoms():
        raise ValueError(
            "Parent does not match the selected crystallographic ligand: "
            f"PDB residue has {observed.GetNumAtoms()} atoms while Parent has "
            f"{template.GetNumAtoms()}. Choose the matching Parent or a different reference ligand.")
    try:
        assigned = AllChem.AssignBondOrdersFromTemplate(template, observed)
        Chem.SanitizeMol(assigned)
    except Exception as error:
        raise ValueError(
            "Parent cannot assign a chemically valid bond graph to the selected bound ligand") from error
    return assigned


def _validate_forcefield_contract(contract: dict) -> dict:
    if not isinstance(contract, dict):
        raise ValueError("receptor_policy.forcefield_contract must be an object")
    missing = sorted(set(_SUPPORTED_FORCEFIELD_CONTRACT).difference(contract))
    mismatches = {}
    for key, expected in _SUPPORTED_FORCEFIELD_CONTRACT.items():
        if key not in contract:
            continue
        observed = contract[key]
        if isinstance(expected, float):
            try:
                matches = math.isclose(float(observed), expected, abs_tol=1e-9)
            except (TypeError, ValueError):
                matches = False
        else:
            matches = str(observed) == expected
        if not matches:
            mismatches[key] = {"received": observed, "supported": expected}
    if missing or mismatches:
        raise ValueError(
            "receptor_policy.forcefield_contract is UNVERIFIED for the pinned "
            f"OpenFE system builder; missing={missing}, mismatches={mismatches}")
    return {
        "verdict": "CONFIRMED", "requested": contract,
        "observed_action": "exact_pinned_system_builder_contract_matched",
        "parameterization_boundary": (
            "Contract compatibility is confirmed here; actual ForceField.createSystem "
            "parameterisation remains a fail-closed downstream system-build gate."),
    }


def _prepare_receptor(pdb_text: str, *, ph: float, keep_waters: bool,
                      chain_ids: list[str] | None = None,
                      missing_atoms_policy: str = "auto_repair_report",
                      missing_residues_policy: str = "auto_repair_report",
                      histidines_policy: str = "server_assign_review",
                      termini_policy: str = "server_assign_review",
                      forcefield_contract: dict | None = None) -> tuple[str, dict]:
    forcefield_report = _validate_forcefield_contract(forcefield_contract or {})
    fixer = PDBFixer(pdbfile=io.StringIO(pdb_text))
    chains = list(fixer.topology.chains())
    available_chain_ids = sorted({
        atom["chain_id"] for line in pdb_text.splitlines()
        if (atom := _pdb_atom(line)) is not None and atom["record"] == "ATOM"
    })
    selected_chain_ids = [str(value) for value in (chain_ids or [])]
    if selected_chain_ids:
        absent = sorted(set(selected_chain_ids).difference(available_chain_ids))
        if absent:
            raise ValueError(
                f"receptor_policy.chain_ids are absent from the coordinate carrier: {absent}")
        # Only polymer chains are controlled by chain_ids. Heterogen-only chains
        # survive until removeHeterogens so reviewed pocket waters cannot vanish
        # merely because the PDB assigned them a separate chain identifier.
        remove_indices = [index for index, chain in enumerate(chains)
                          if str(chain.id) in available_chain_ids
                          and str(chain.id) not in selected_chain_ids]
        if remove_indices:
            fixer.removeChains(remove_indices)
    topology_chains = [chain for chain in fixer.topology.chains()
                       if str(chain.id) in available_chain_ids]
    termini_witnesses = []
    for chain in topology_chains:
        residues = list(chain.residues())
        if not residues:
            continue
        termini_witnesses.append({
            "chain_id": str(chain.id),
            "n_terminus": {"residue_name": str(residues[0].name),
                           "residue_number": str(residues[0].id)},
            "c_terminus": {"residue_name": str(residues[-1].name),
                           "residue_number": str(residues[-1].id)},
        })
    if termini_policy not in {"server_assign_review", "server_assign_report", "manual"}:
        raise ValueError(
            f"unsupported receptor_policy.termini={termini_policy!r}")
    if termini_witnesses and termini_policy == "manual":
        raise ValueError(
            "receptor_policy.termini=manual is UNVERIFIED because no per-chain "
            f"terminal-state decisions were supplied; witnesses {termini_witnesses[:10]}")
    histidine_input_witnesses = []
    for chain in topology_chains:
        for residue in chain.residues():
            if str(residue.name).upper() in {"HIS", "HID", "HIE", "HIP"}:
                histidine_input_witnesses.append({
                    "chain_id": str(chain.id), "residue_name": str(residue.name),
                    "residue_number": str(residue.id),
                })
    if histidines_policy not in {"server_assign_review", "server_assign_report", "manual"}:
        raise ValueError(
            f"unsupported receptor_policy.histidines={histidines_policy!r}")
    if histidine_input_witnesses and histidines_policy == "manual":
        raise ValueError(
            "receptor_policy.histidines=manual is UNVERIFIED because no per-residue "
            f"tautomer/protonation decisions were supplied; witnesses "
            f"{histidine_input_witnesses[:10]}")
    fixer.findMissingResidues()
    unresolved_residues = [
        {"chain_index": int(chain), "insertion_index": int(index),
         "residue_names": list(names)}
        for (chain, index), names in sorted(fixer.missingResidues.items())
    ]
    if missing_residues_policy not in {"auto_repair_report", "review_each", "block"}:
        raise ValueError(
            f"unsupported receptor_policy.missing_residues={missing_residues_policy!r}")
    if unresolved_residues:
        raise ValueError(
            f"receptor_policy.missing_residues={missing_residues_policy} is UNVERIFIED: "
            f"the structure has {len(unresolved_residues)} absent residue segments; "
            "PDBFixer loop coordinates are deliberately not invented; first witnesses "
            f"{unresolved_residues[:10]}")
    # Building absent loops would invent coordinates. Existing-residue heavy atoms
    # are repairable; absent residues stay explicitly unresolved.
    fixer.missingResidues = {}
    fixer.findNonstandardResidues()
    nonstandard = [{"residue": str(residue), "replacement": replacement}
                   for residue, replacement in fixer.nonstandardResidues]
    if nonstandard:
        raise ValueError(
            "nonstandard-residue replacement is UNVERIFIED and cannot be silently "
            f"applied; first witnesses {nonstandard[:10]}")
    fixer.replaceNonstandardResidues()
    fixer.removeHeterogens(keepWater=keep_waters)
    fixer.findMissingAtoms()
    missing_atom_count = sum(len(atoms) for atoms in fixer.missingAtoms.values())
    missing_terminal_count = sum(len(atoms) for atoms in fixer.missingTerminals.values())
    missing_atom_witnesses = [{
        "chain_id": str(residue.chain.id), "residue_name": str(residue.name),
        "residue_number": str(residue.id),
        "atom_names": sorted(str(atom.name) for atom in atoms),
    } for residue, atoms in fixer.missingAtoms.items()]
    missing_terminal_witnesses = [{
        "chain_id": str(residue.chain.id), "residue_name": str(residue.name),
        "residue_number": str(residue.id),
        "atom_names": sorted(str(atom.name) for atom in atoms),
    } for residue, atoms in fixer.missingTerminals.items()]
    if missing_atoms_policy not in {"auto_repair_report", "review_each", "block"}:
        raise ValueError(
            f"unsupported receptor_policy.missing_atoms={missing_atoms_policy!r}")
    if ((missing_atom_count or missing_terminal_count)
            and missing_atoms_policy in {"block", "review_each"}):
        raise ValueError(
            f"receptor_policy.missing_atoms={missing_atoms_policy} but "
            f"{missing_atom_count} internal and {missing_terminal_count} terminal "
            "atoms require an explicit decision")
    fixer.addMissingAtoms()
    fixer.addMissingHydrogens(ph)
    stream = io.StringIO()
    PDBFile.writeFile(fixer.topology, fixer.positions, stream, keepIds=True)
    prepared = stream.getvalue()
    prepared_atoms = [atom for line in prepared.splitlines()
                      if (atom := _pdb_atom(line)) is not None]
    prepared_histidines: dict[tuple[str, str], list[dict]] = {}
    for atom in prepared_atoms:
        if atom["residue_name"] in {"HIS", "HID", "HIE", "HIP"}:
            prepared_histidines.setdefault(
                (atom["chain_id"], atom["residue_number"]), []).append(atom)
    histidine_assignments = []
    for (chain, residue), atoms in sorted(prepared_histidines.items()):
        names = {atom["atom_name"] for atom in atoms}
        hd1, he2 = "HD1" in names, "HE2" in names
        state = "HIP" if hd1 and he2 else "HID" if hd1 else "HIE" if he2 else None
        if state is None:
            raise ValueError(
                "server histidine assignment is UNVERIFIED: no HD1/HE2 witness for "
                f"HIS:{chain or '_'}:{residue}")
        histidine_assignments.append({
            "site_id": f"HIS:{chain or '_'}:{residue}", "chain_id": chain,
            "residue_number": residue, "assigned_state": state,
            "hydrogen_atom_witnesses": sorted(names.intersection({"HD1", "HE2"})),
            "ph": ph,
        })
    for chain_witness in termini_witnesses:
        chain = chain_witness["chain_id"]
        for terminus_name in ("n_terminus", "c_terminus"):
            terminus = chain_witness[terminus_name]
            atoms = [atom for atom in prepared_atoms
                     if atom["chain_id"] == chain
                     and atom["residue_number"] == terminus["residue_number"]]
            terminus["atom_name_witnesses"] = sorted({
                atom["atom_name"] for atom in atoms})
            terminus["hydrogen_atom_witnesses"] = sorted({
                atom["atom_name"] for atom in atoms
                if atom["element"] in {"H", "D"}})
            terminus["terminal_template_atom_witnesses"] = sorted(
                {atom["atom_name"] for atom in atoms}.intersection(
                    {"H", "H1", "H2", "H3", "O", "OXT"}))
    return prepared, {
        "engine": "PDBFixer", "engine_version": "1.12", "ph": ph,
        "keep_waters": keep_waters,
        "available_chain_ids": available_chain_ids,
        "selected_chain_ids": selected_chain_ids or available_chain_ids,
        "missing_atoms_policy": missing_atoms_policy,
        "missing_residues_policy": missing_residues_policy,
        "missing_existing_residue_atoms_added": missing_atom_count,
        "missing_terminal_atoms_added": missing_terminal_count,
        "unresolved_missing_residues": unresolved_residues,
        "nonstandard_residue_replacements": nonstandard,
        "policy_execution": {
            "missing_atoms": {
                "verdict": "CONFIRMED", "policy": missing_atoms_policy,
                "observed_action": "existing_residue_atoms_repaired_and_counted",
                "internal_atom_count_added": missing_atom_count,
                "terminal_atom_count_added": missing_terminal_count,
                "internal_atom_witnesses": missing_atom_witnesses,
                "terminal_atom_witnesses": missing_terminal_witnesses,
            },
            "missing_residues": {
                "verdict": "CONFIRMED", "policy": missing_residues_policy,
                "observed_action": "not_applicable_no_missing_residue_segments",
                "unresolved_segment_count": 0,
            },
            "histidines": {
                "verdict": "CONFIRMED", "policy": histidines_policy,
                "observed_action": ("PDBFixer_ph_assignment_witnessed"
                                    if histidine_assignments else
                                    "not_applicable_no_histidines"),
                "assignment_witnesses": histidine_assignments,
            },
            "termini": {
                "verdict": "CONFIRMED", "policy": termini_policy,
                "observed_action": "PDBFixer_terminal_atoms_and_hydrogens_assigned",
                "terminal_witnesses": termini_witnesses, "ph": ph,
            },
            "forcefield_contract": forcefield_report,
        },
        "claim_boundary": (
            "Automated structural preparation with entity-level policy witnesses. "
            "Force-field parameterisation remains a separate fail-closed system-build gate."),
    }


def _mcs_atom_map(target: Chem.Mol, reference: Chem.Mol) -> tuple[list[list[tuple[int, int]]], dict]:
    target_heavy, reference_heavy = Chem.RemoveHs(target), Chem.RemoveHs(reference)
    result = rdFMCS.FindMCS(
        [target_heavy, reference_heavy], timeout=20, ringMatchesRingOnly=True,
        completeRingsOnly=True, matchValences=True,
        bondCompare=rdFMCS.BondCompare.CompareOrderExact,
        atomCompare=rdFMCS.AtomCompare.CompareElements)
    if result.canceled or result.numAtoms < 3:
        raise ValueError("analogue has no usable common 3D core with the bound reference ligand")
    query = Chem.MolFromSmarts(result.smartsString)
    target_matches = target_heavy.GetSubstructMatches(
        query, uniquify=False, maxMatches=256)
    reference_matches = reference_heavy.GetSubstructMatches(
        query, uniquify=False, maxMatches=256)
    if not target_matches or not reference_matches:
        raise ValueError("common-core atom correspondence could not be recovered")
    mappings = [list(zip(target_match, reference_match))
                for target_match in target_matches
                for reference_match in reference_matches][:4096]
    coverage = result.numAtoms / max(target_heavy.GetNumAtoms(), reference_heavy.GetNumAtoms())
    return mappings, {
        "mcs_smarts": result.smartsString, "mapped_heavy_atoms": result.numAtoms,
        "target_heavy_atoms": target_heavy.GetNumAtoms(),
        "reference_heavy_atoms": reference_heavy.GetNumAtoms(),
        "minimum_bidirectional_coverage": coverage,
    }


def _aligned_pose(smiles: str, reference: Chem.Mol, *, seed: int,
                  minimum_coverage: float,
                  preserve_reference: bool = False) -> tuple[str, dict]:
    base = Chem.MolFromSmiles(smiles)
    if base is None:
        raise ValueError(f"cannot parse ligand SMILES {smiles!r}")
    if preserve_reference:
        # Parent is the crystallographic ligand: preserve measured heavy-atom
        # coordinates exactly instead of regenerating and realigning them.  The
        # chemical graph comes from the reviewed Parent SMILES because a PDB
        # coordinate carrier usually omits E/Z and chiral annotations.
        heavy = Chem.Mol(base)
        reference_match = reference.GetSubstructMatch(heavy, useChirality=False)
        if len(reference_match) != heavy.GetNumAtoms():
            raise ValueError(
                "Parent graph cannot be placed onto the crystallographic atom order")
        measured = reference.GetConformer()
        conformer = Chem.Conformer(heavy.GetNumAtoms())
        for parent_index, reference_index in enumerate(reference_match):
            conformer.SetAtomPosition(parent_index,
                                      measured.GetAtomPosition(reference_index))
        heavy.AddConformer(conformer)
        molecule = Chem.AddHs(heavy, addCoords=True)
        mcs = {
            "mcs_smarts": Chem.MolToSmarts(Chem.RemoveHs(reference)),
            "mapped_heavy_atoms": Chem.RemoveHs(reference).GetNumAtoms(),
            "target_heavy_atoms": Chem.RemoveHs(reference).GetNumAtoms(),
            "reference_heavy_atoms": Chem.RemoveHs(reference).GetNumAtoms(),
            "minimum_bidirectional_coverage": 1.0,
        }
        rmsd, forcefield = 0.0, "CRYSTALLOGRAPHIC_PARENT"
    else:
        molecule = Chem.AddHs(base)
        atom_maps, mcs = _mcs_atom_map(molecule, reference)
        if mcs["minimum_bidirectional_coverage"] < minimum_coverage:
            raise ValueError(
                "analogue is outside the reference-alignment domain: common-core coverage "
                f"{mcs['minimum_bidirectional_coverage']:.3f} < {minimum_coverage:.3f}")
        query = Chem.MolFromSmarts(mcs["mcs_smarts"])
        reference_match = Chem.RemoveHs(reference).GetSubstructMatch(query)
        core = Chem.Mol(query)
        core_conf = Chem.Conformer(core.GetNumAtoms())
        reference_conf = reference.GetConformer()
        for query_index, reference_index in enumerate(reference_match):
            core_conf.SetAtomPosition(query_index,
                                      reference_conf.GetAtomPosition(reference_index))
        core.AddConformer(core_conf)
        try:
            molecule = AllChem.ConstrainedEmbed(
                molecule, core, randomseed=int(seed), useTethers=True)
            forcefield = "MMFF/UFF_CONSTRAINED_TO_CRYSTAL_CORE"
        except ValueError as error:
            raise ValueError(
                "reference-constrained 3D embedding failed for this analogue") from error
        # Evaluate symmetry-equivalent correspondences instead of accepting the
        # first substructure match.  The latter can report a multi-Angstrom "RMSD"
        # for the same symmetric molecule and would turn atom-order lottery into a
        # scientific gate.
        rmsd = float(AllChem.GetBestRMS(molecule, reference, map=atom_maps,
                                        maxMatches=len(atom_maps)))
    molecule.SetProp("DIRAC_POSE_METHOD", "ETKDG_MMFF_MCS_ALIGN")
    molecule.SetProp("DIRAC_CORE_RMSD_ANGSTROM", f"{rmsd:.6f}")
    molecule.SetProp("DIRAC_REFERENCE_CORE_SMARTS", mcs["mcs_smarts"])
    with tempfile.NamedTemporaryFile(suffix=".sdf") as handle:
        writer = Chem.SDWriter(handle.name)
        writer.write(molecule)
        writer.close()
        sdf = Path(handle.name).read_text()
    return sdf, {
        **mcs, "core_rmsd_angstrom": rmsd, "forcefield": forcefield,
        # Chemical identity is the reviewed campaign input.  PDB coordinates do
        # not carry complete alkene/chiral annotations, so deriving identity from
        # the coordinate carrier would silently erase stereo and break endpoint
        # matching even when the measured Parent coordinates are correct.
        "canonical_smiles": _canonical(base),
        "formal_charge": int(sum(atom.GetFormalCharge() for atom in base.GetAtoms())),
    }


def _heavy_xyz_from_pdb(pdb_text: str) -> list[dict]:
    points = []
    for index, line in enumerate(pdb_text.splitlines()):
        atom = _pdb_atom(line)
        if atom is None:
            continue
        if atom["element"] in {"H", "D"}:
            continue
        points.append({
            **_atom_witness(atom), "coordinate_index": index,
            "xyz": atom["xyz"],
        })
    return points


def _pose_geometry(receptor_points: list[dict | tuple[float, float, float]],
                   sdf: str) -> dict:
    molecule = Chem.MolFromMolBlock(sdf, removeHs=False, sanitize=False)
    if molecule is None or molecule.GetNumConformers() != 1:
        raise ValueError("generated pose has no readable 3D conformer")
    conformer = molecule.GetConformer()
    ligand_points = []
    for atom in molecule.GetAtoms():
        if atom.GetAtomicNum() <= 1:
            continue
        point = conformer.GetAtomPosition(atom.GetIdx())
        ligand_points.append({
            "ligand_atom_index": int(atom.GetIdx()),
            "ligand_element": str(atom.GetSymbol()),
            "ligand_atom_name": (
                atom.GetPDBResidueInfo().GetName().strip()
                if atom.GetPDBResidueInfo() is not None else None),
            "xyz": (float(point.x), float(point.y), float(point.z)),
        })
    normalized_receptor_points = []
    for index, point in enumerate(receptor_points):
        if isinstance(point, dict):
            xyz = point.get("xyz")
            if not isinstance(xyz, (list, tuple)) or len(xyz) != 3:
                continue
            normalized_receptor_points.append({
                **point, "protein_atom_index": index,
                "xyz": tuple(float(value) for value in xyz),
            })
        else:
            normalized_receptor_points.append({
                "protein_atom_index": index, "serial": None,
                "atom_name": None, "element": None, "residue_name": None,
                "chain_id": None, "residue_number": None,
                "insertion_code": None,
                "xyz": tuple(float(value) for value in point),
            })
    def witness(ligand: dict, receptor: dict, distance: float) -> dict:
        return {
            "distance_angstrom": round(distance, 6),
            "ligand_atom_index": ligand["ligand_atom_index"],
            "ligand_element": ligand["ligand_element"],
            "ligand_atom_name": ligand["ligand_atom_name"],
            "protein_atom_index": receptor["protein_atom_index"],
            "protein_serial": receptor.get("serial"),
            "protein_atom_name": receptor.get("atom_name"),
            "protein_element": receptor.get("element"),
            "protein_residue_name": receptor.get("residue_name"),
            "protein_chain_id": receptor.get("chain_id"),
            "protein_residue_number": receptor.get("residue_number"),
            "protein_insertion_code": receptor.get("insertion_code"),
        }

    def retain_nearest(rows: list[dict], row: dict) -> None:
        rows.append(row)
        rows.sort(key=lambda value: (
            value["distance_angstrom"], value["ligand_atom_index"],
            value["protein_atom_index"]))
        if len(rows) > _PAIR_WITNESS_LIMIT:
            rows.pop()

    minimum = math.inf
    nearest = None
    contact_total = 0
    hard_clash_total = 0
    contact_witnesses: list[dict] = []
    hard_clash_witnesses: list[dict] = []
    for ligand in ligand_points:
        for receptor in normalized_receptor_points:
            distance = math.dist(ligand["xyz"], receptor["xyz"])
            row = None
            if distance < minimum:
                minimum = distance
                row = witness(ligand, receptor, distance)
                nearest = row
            if distance <= 6.0:
                contact_total += 1
                row = row or witness(ligand, receptor, distance)
                retain_nearest(contact_witnesses, row)
            if distance < 1.5:
                hard_clash_total += 1
                row = row or witness(ligand, receptor, distance)
                retain_nearest(hard_clash_witnesses, row)
    if (not ligand_points or not normalized_receptor_points
            or not math.isfinite(minimum)):
        raise ValueError("pose/receptor geometry could not be measured")
    if minimum < 1.5:
        raise ValueError(
            "reference-aligned pose clashes with the prepared receptor "
            f"(nearest pair witness {nearest}; require distance >= 1.500 A; "
            f"hard_clash_pair_total={hard_clash_total})")
    if contact_total == 0:
        raise ValueError(
            "reference-aligned pose is outside the receptor pocket "
            f"(nearest pair witness {nearest}; no contacts within 6.0 A)")
    return {
        "minimum_heavy_atom_distance_angstrom": round(minimum, 3),
        "protein_contacts_within_6_angstrom": contact_total,
        "nearest_pair_witness": nearest,
        "contact_cutoff_angstrom": 6.0,
        "contact_pair_total": contact_total,
        "contact_pair_witnesses": contact_witnesses,
        "contact_witnesses_truncated": contact_total > _PAIR_WITNESS_LIMIT,
        "hard_clash_cutoff_angstrom": 1.5,
        "hard_clash_pair_total": hard_clash_total,
        "hard_clash_pair_witnesses": hard_clash_witnesses,
        "hard_clash_witnesses_truncated": hard_clash_total > _PAIR_WITNESS_LIMIT,
        "pair_witness_limit": _PAIR_WITNESS_LIMIT,
        "geometry_gate": "passed",
    }


def build(document: dict) -> dict:
    document, stereo_enumeration = normalize_ligand_series(document)
    pdb_text = str(document.get("receptor_pdb") or "")
    if not pdb_text.strip() or not any(line.startswith(("ATOM  ", "HETATM"))
                                       for line in pdb_text.splitlines()):
        raise ValueError("a coordinate-bearing PDB structure is required")
    if len(pdb_text.encode()) > _MAX_PDB_BYTES:
        raise ValueError("receptor PDB exceeds 8 MiB")
    compounds = document.get("compounds") or []
    if not 2 <= len(compounds) <= 64:
        raise ValueError("campaign preparation requires 2..64 compounds")
    if document.get("pose_strategy") != "align_to_reference":
        raise ValueError("this preparation release supports align_to_reference only")
    candidates = inspect_bound_ligands(pdb_text)
    if not candidates:
        raise ValueError("no bound organic reference ligand was found in the supplied PDB")
    selector = document.get("reference_ligand") or candidates[0]
    if selector.get("role", "experimental_ligand") != "experimental_ligand":
        raise ValueError(
            "reference_ligand.role must be experimental_ligand for RBFE pose alignment")
    parent_id = str(document.get("parent_id") or compounds[0].get("id") or "")
    parent = next((row for row in compounds if str(row.get("id")) == parent_id), None)
    if parent is None:
        raise ValueError("Parent identity is absent from the campaign compounds")
    digest_bundle = canonical_digest_bundle(document)
    receptor_policy = digest_bundle["receptor_policy"]
    policy_pdb, coordinate_policy_report = _apply_coordinate_policy(
        pdb_text, receptor_policy, selector)
    reference = _extract_reference(
        policy_pdb, selector, str(parent.get("smiles") or ""))
    keep_waters = bool(coordinate_policy_report["waters"]["kept_water_count"])
    prepared_pdb, receptor_report = _prepare_receptor(
        policy_pdb, ph=float(receptor_policy.get("ph", document.get("ph", 7.4))),
        keep_waters=keep_waters,
        chain_ids=receptor_policy.get("chain_ids") or [],
        missing_atoms_policy=receptor_policy.get(
            "missing_atoms", "auto_repair_report"),
        missing_residues_policy=receptor_policy.get(
            "missing_residues", "auto_repair_report"),
        histidines_policy=receptor_policy.get(
            "histidines", "server_assign_review"),
        termini_policy=receptor_policy.get(
            "termini", "server_assign_review"),
        forcefield_contract=receptor_policy.get("forcefield_contract"))
    receptor_report["receptor_policy"] = receptor_policy
    receptor_report["ligand_policy"] = digest_bundle["ligand_policy"]
    receptor_report["policy_execution"] = {
        **coordinate_policy_report,
        **receptor_report.get("policy_execution", {}),
    }
    unresolved_axes = sorted(
        name for name, evidence in receptor_report["policy_execution"].items()
        if not isinstance(evidence, dict) or evidence.get("verdict") != "CONFIRMED")
    if unresolved_axes:
        raise ValueError(
            "receptor preparation cannot create pose hypotheses while policy axes "
            f"remain UNVERIFIED: {unresolved_axes}")
    receptor_report["qualification_gate"] = {
        "verdict": "CONFIRMED", "eligible_for_pose_review": True,
        "eligible_for_physical_system_build": True,
        "confirmed_axes": sorted(receptor_report["policy_execution"]),
        "boundary": (
            "Eligibility means every receptor-preparation policy was executed or "
            "proved not applicable. It is not an RBFE result; force-field system "
            "construction and human pose review remain later gates."),
    }
    receptor_points = _heavy_xyz_from_pdb(prepared_pdb)
    poses = []
    for index, row in enumerate(compounds):
        ligand_id = str(row.get("id") or "")
        supplied_smiles = str(row.get("smiles") or "")
        parsed = Chem.MolFromSmiles(supplied_smiles)
        canonical_identity = (_canonical(parsed) if parsed is not None
                              else "UNPARSEABLE")
        try:
            sdf, report = _aligned_pose(
                supplied_smiles, reference,
                seed=int(document.get("seed", 20260816)) + index,
                minimum_coverage=float(document.get("minimum_core_coverage", .5)),
                preserve_reference=ligand_id == parent_id)
            report.update(_pose_geometry(receptor_points, sdf))
        except Exception as error:
            raise ValueError(
                f"ligand {ligand_id!r} ({canonical_identity}) pose preparation failed: "
                f"{error}") from error
        poses.append({"id": str(row.get("id")), "sdf": sdf, "report": report})
    digest_bundle["reference_digest"] = sha256_digest({
        "selector": {key: selector.get(key) for key in
                     ("resname", "chain", "residue_number", "role",
                      "altloc", "occupancy")},
        "parent_id": parent_id,
        "reference_canonical_smiles": _canonical(reference),
    })
    digest_bundle["prepared_receptor_digest"] = _sha(prepared_pdb)
    digest_bundle["poses_digest"] = sha256_digest([{
        "id": pose["id"], "sdf_sha256": _sha(pose["sdf"]),
        "report_digest": sha256_digest(pose["report"]),
    } for pose in poses])
    digest_bundle["bundle_digest"] = sha256_digest({
        key: value for key, value in digest_bundle.items()
        if key.endswith("_digest") and key != "bundle_digest"
    })
    dag = dependency_dag(digest_bundle)
    stages = {
        "prepare": stage_payload(
            "prepare", "CONFIRMED",
            digests={
                "prepared_receptor_digest": digest_bundle["prepared_receptor_digest"],
                "poses_digest": digest_bundle["poses_digest"],
                "bundle_digest": digest_bundle["bundle_digest"],
            }),
        "pose_review": stage_payload(
            "pose_review", "UNVERIFIED",
            digests={"pose_review_digest": digest_bundle["pose_review_digest"]},
            recovery={
                "retryable": True, "resume_from_stage": "pose_review",
                "required_actions": [
                    "view_every_pose", "record_reviewer_identity",
                    "record_reason_and_attestation",
                ],
            }),
    }
    return {
        "schema_version": "rbfe-campaign-preparation.v2",
        "prepared_receptor_pdb": prepared_pdb,
        "prepared_receptor_sha256": _sha(prepared_pdb),
        "raw_receptor_sha256": _sha(pdb_text),
        "reference_ligand": {key: selector.get(key) for key in
                             ("resname", "chain", "residue_number", "role",
                              "altloc", "occupancy")},
        "reference_ligand_candidates": candidates,
        "reference_canonical_smiles": _canonical(reference),
        "receptor_report": receptor_report,
        "stereo_enumeration": stereo_enumeration,
        "poses": poses,
        "digest_bundle": digest_bundle,
        "artifact_dag": dag,
        "stages": stages,
        "verdict": "UNVERIFIED",
        "claim_boundary": (
            "Automated receptor preparation and reference-constrained pose hypotheses. "
            "Human pose review is required before physical RBFE execution."),
    }


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: rbfe_campaign_builder.py INPUT.json OUTPUT.json")
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        if source.stat().st_size > 10 << 20:
            raise ValueError("campaign preparation input exceeds 10 MiB")
        result = build(json.loads(source.read_text()))
    except Exception as error:  # fixed process boundary; return a typed source error
        stage = stage_payload(
            "prepare", "OVERTURNED",
            error={"code": "INVALID_SCIENTIFIC_SOURCE", "message": str(error)},
            recovery={
                "retryable": True, "resume_from_stage": "inputs",
                "required_actions": ["correct_named_source_ligand_or_policy"],
            })
        target.write_text(json.dumps({
            "ok": False,
            "error": {"code": "INVALID_SCIENTIFIC_SOURCE", "message": str(error)},
            "stage": stage, "verdict": "OVERTURNED",
        }, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 2
    target.write_text(json.dumps(result, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
