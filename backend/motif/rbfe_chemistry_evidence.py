"""Strict, JSON-serializable chemistry evidence for RBFE planning and execution.

The functions in this module never infer that missing evidence is conserved
chemistry.  Every decision is three-valued: CONFIRMED, CHANGED, or UNVERIFIED.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Iterable

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors


CONFIRMED = "CONFIRMED"
CHANGED = "CHANGED"
UNVERIFIED = "UNVERIFIED"
VERDICTS = {CONFIRMED, CHANGED, UNVERIFIED}


def _heavy(molecule: Chem.Mol) -> Chem.Mol:
    result = Chem.RemoveHs(Chem.Mol(molecule))
    Chem.AssignStereochemistry(result, cleanIt=True, force=True)
    return result


def molecule_from_smiles(smiles: str, label: str = "molecule") -> Chem.Mol:
    molecule = Chem.MolFromSmiles(str(smiles))
    if molecule is None:
        raise ValueError(f"cannot parse {label} SMILES {smiles!r}")
    Chem.AssignStereochemistry(molecule, cleanIt=True, force=True)
    return molecule


def _stereo_contract(molecule: Chem.Mol) -> list[dict]:
    mol = _heavy(molecule)
    rows: list[dict] = []
    for info in Chem.FindPotentialStereo(mol):
        specified = str(info.specified).split(".")[-1].upper()
        descriptor = str(info.descriptor).split(".")[-1]
        if info.type == Chem.StereoType.Atom_Tetrahedral:
            atom = mol.GetAtomWithIdx(int(info.centeredOn))
            rows.append({
                "kind": "ATOM_TETRAHEDRAL",
                "center_atom_index": atom.GetIdx(),
                "element": atom.GetSymbol(),
                "specified": specified,
                "descriptor": descriptor,
                "cip": atom.GetProp("_CIPCode") if atom.HasProp("_CIPCode") else None,
                "controlling_atom_indices": [
                    int(index) for index in info.controllingAtoms
                    if int(index) != 2**32 - 1
                ],
            })
        elif info.type == Chem.StereoType.Bond_Double:
            bond = mol.GetBondWithIdx(int(info.centeredOn))
            rows.append({
                "kind": "BOND_DOUBLE",
                "center_bond_index": bond.GetIdx(),
                "begin_atom_index": bond.GetBeginAtomIdx(),
                "end_atom_index": bond.GetEndAtomIdx(),
                "specified": specified,
                "descriptor": descriptor,
                "ez": str(bond.GetStereo()).replace("STEREO", "") or None,
                "controlling_atom_indices": [
                    int(index) for index in info.controllingAtoms
                    if int(index) != 2**32 - 1
                ],
            })
        else:
            rows.append({
                "kind": str(info.type).split(".")[-1].upper(),
                "centered_on": int(info.centeredOn),
                "specified": specified,
                "descriptor": descriptor,
            })
    return rows


def canonical_isomeric_identity(molecule: Chem.Mol | str,
                                label: str = "molecule") -> dict:
    mol = (molecule_from_smiles(molecule, label)
           if isinstance(molecule, str) else _heavy(molecule))
    Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
    stereo = _stereo_contract(mol)
    unspecified = [row for row in stereo if row["specified"] != "SPECIFIED"]
    charge_witnesses = [
        {"atom_index": atom.GetIdx(), "element": atom.GetSymbol(),
         "formal_charge": atom.GetFormalCharge()}
        for atom in mol.GetAtoms() if atom.GetFormalCharge()
    ]
    canonical_isomeric = Chem.MolToSmiles(
        mol, canonical=True, isomericSmiles=True)
    canonical_connectivity = Chem.MolToSmiles(
        mol, canonical=True, isomericSmiles=False)
    formal_charge = int(sum(atom.GetFormalCharge() for atom in mol.GetAtoms()))
    total_hydrogen_count = int(sum(
        atom.GetTotalNumHs(includeNeighbors=True) for atom in mol.GetAtoms()))
    microstate_material = {
        "canonical_isomeric_smiles": canonical_isomeric,
        "formal_charge": formal_charge,
        "total_hydrogen_count": total_hydrogen_count,
    }
    microstate_digest = "sha256:" + hashlib.sha256(json.dumps(
        microstate_material, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": "rbfe-chemical-identity.v1",
        "label": label,
        "canonical_isomeric_smiles": canonical_isomeric,
        "canonical_connectivity_smiles": canonical_connectivity,
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "formal_charge": formal_charge,
        "total_hydrogen_count": total_hydrogen_count,
        "heavy_atom_count": mol.GetNumHeavyAtoms(),
        "bond_count": mol.GetNumBonds(),
        "formal_charge_witnesses": charge_witnesses,
        "stereo": stereo,
        "unspecified_stereo": unspecified,
        "stereo_policy_verdict": UNVERIFIED if unspecified else CONFIRMED,
        "microstate": {
            "schema_version": "rbfe-microstate-identity.v1",
            **microstate_material,
            "identity_digest": microstate_digest,
            "representation_verdict": CONFIRMED,
            "solution_population_verdict": UNVERIFIED,
            "reason": (
                "the exact graph/protomer representation is identified; pH, pKa, "
                "and solution-population evidence are not implied"),
        },
    }


def require_resolved_stereochemistry(molecule: Chem.Mol | str,
                                     label: str) -> dict:
    identity = canonical_isomeric_identity(molecule, label)
    if identity["unspecified_stereo"]:
        centers = ", ".join(
            f"{row['kind']}@{row.get('center_atom_index', row.get('center_bond_index'))}"
            for row in identity["unspecified_stereo"])
        raise ValueError(
            f"{label} has unspecified stereochemistry ({centers}); enumerate each "
            "stereoisomer as a distinct compound or provide a resolved isomeric SMILES")
    return identity


def input_pose_identity_witness(expected_smiles: str, posed: Chem.Mol,
                                label: str) -> dict:
    expected = canonical_isomeric_identity(expected_smiles, f"{label} input")
    observed = canonical_isomeric_identity(posed, f"{label} posed SDF")
    if expected["unspecified_stereo"]:
        verdict, reason = UNVERIFIED, "input stereochemistry is unspecified"
    elif (expected["canonical_isomeric_smiles"]
          != observed["canonical_isomeric_smiles"]):
        verdict, reason = CHANGED, "input and posed-SDF isomeric identities differ"
    else:
        verdict, reason = CONFIRMED, "input and posed-SDF isomeric identities match"

    geometry_identity = None
    if posed.GetNumConformers() and posed.GetConformer().Is3D():
        from_geometry = Chem.Mol(posed)
        Chem.AssignStereochemistryFrom3D(
            from_geometry, confId=posed.GetConformer().GetId(),
            replaceExistingTags=True)
        geometry_identity = canonical_isomeric_identity(
            from_geometry, f"{label} coordinates")
        expected_has_stereo = bool(expected["stereo"])
        if (verdict == CONFIRMED and expected_has_stereo
                and expected["canonical_isomeric_smiles"]
                != geometry_identity["canonical_isomeric_smiles"]):
            verdict, reason = CHANGED, "3D coordinates invert or erase input CIP/E/Z"
    elif expected["stereo"] and verdict == CONFIRMED:
        verdict, reason = UNVERIFIED, "posed SDF has no 3D CIP/E/Z witness"
    return {
        "schema_version": "rbfe-input-pose-identity.v1",
        "verdict": verdict,
        "reason": reason,
        "input": expected,
        "posed_sdf": observed,
        "coordinates": geometry_identity,
    }


def depiction_index_contract(molecule: Chem.Mol) -> dict:
    """Bind source heavy-atom indices to a visible, reparsable SMILES order."""
    full = Chem.Mol(molecule)
    source_heavy = [atom.GetIdx() for atom in full.GetAtoms()
                    if atom.GetAtomicNum() > 1]
    heavy = _heavy(full)
    if heavy.GetNumAtoms() != len(source_heavy):
        raise ValueError("could not establish a heavy-atom depiction index contract")
    for heavy_index, atom in enumerate(heavy.GetAtoms()):
        atom.SetAtomMapNum(heavy_index + 1)
    mapped_smiles = Chem.MolToSmiles(
        heavy, canonical=False, isomericSmiles=True)
    parsed = Chem.MolFromSmiles(mapped_smiles)
    if parsed is None:
        raise ValueError("could not parse mapped depiction SMILES")
    heavy_to_depiction = {
        int(atom.GetAtomMapNum()) - 1: atom.GetIdx()
        for atom in parsed.GetAtoms() if atom.GetAtomMapNum() > 0
    }
    if len(heavy_to_depiction) != heavy.GetNumAtoms():
        raise ValueError("depiction SMILES did not preserve every heavy-atom index")
    visible = Chem.Mol(parsed)
    for atom in visible.GetAtoms():
        atom.SetAtomMapNum(0)
    visible_smiles = Chem.MolToSmiles(
        visible, canonical=False, isomericSmiles=True)
    reparsed = Chem.MolFromSmiles(visible_smiles)
    if reparsed is None or reparsed.GetNumAtoms() != heavy.GetNumAtoms():
        raise ValueError("visible depiction SMILES changed the ligand graph")
    return {
        "visible_smiles": visible_smiles,
        "source_to_depiction": {
            int(source_index): int(heavy_to_depiction[heavy_index])
            for heavy_index, source_index in enumerate(source_heavy)
        },
    }


def _heavy_pairs(left: Chem.Mol, right: Chem.Mol,
                 pairs: Iterable[Iterable[int]]) -> list[tuple[int, int]]:
    normalized: list[tuple[int, int]] = []
    for raw in pairs:
        a, b = (int(value) for value in raw)
        if not 0 <= a < left.GetNumAtoms() or not 0 <= b < right.GetNumAtoms():
            raise ValueError(f"atom mapping index is out of range: {a}->{b}")
        if (left.GetAtomWithIdx(a).GetAtomicNum() > 1
                and right.GetAtomWithIdx(b).GetAtomicNum() > 1):
            normalized.append((a, b))
    if len({a for a, _ in normalized}) != len(normalized):
        raise ValueError("atom mapping is not one-to-one on the parent")
    if len({b for _, b in normalized}) != len(normalized):
        raise ValueError("atom mapping is not one-to-one on the proposal")
    return sorted(set(normalized))


def _dimension(name: str, verdict: str, summary: str,
               witnesses: list[dict] | None = None) -> dict:
    if verdict not in VERDICTS:
        raise ValueError(f"invalid chemistry verdict {verdict!r}")
    return {"dimension": name, "verdict": verdict, "summary": summary,
            "witnesses": witnesses or []}


def _cycle_rank(molecule: Chem.Mol) -> int:
    mol = _heavy(molecule)
    components = len(Chem.GetMolFrags(mol))
    return max(0, mol.GetNumBonds() - mol.GetNumAtoms() + components)


def _stereo_by_center(molecule: Chem.Mol) -> dict[tuple, dict]:
    # ``_stereo_contract`` removes explicit hydrogens so CIP/E/Z assignment is
    # representation-invariant. RDKit then renumbers the surviving heavy atoms
    # when hydrogens are interleaved in an SDF, while mapper pairs remain in the
    # source molecule's index space. Project every witness back into that source
    # space before comparing it with a mapper-owned correspondence.
    source_heavy = [atom.GetIdx() for atom in molecule.GetAtoms()
                    if atom.GetAtomicNum() > 1]
    rows = _stereo_contract(molecule)
    result = {}
    for raw in rows:
        row = dict(raw)
        if "controlling_atom_indices" in row:
            row["controlling_atom_indices"] = [
                source_heavy[int(index)]
                for index in row["controlling_atom_indices"]
            ]
        if row["kind"] == "ATOM_TETRAHEDRAL":
            row["center_atom_index"] = source_heavy[
                int(row["center_atom_index"])]
            result[("atom", row["center_atom_index"])] = row
        elif row["kind"] == "BOND_DOUBLE":
            begin = source_heavy[int(row["begin_atom_index"])]
            end = source_heavy[int(row["end_atom_index"])]
            source_bond = molecule.GetBondBetweenAtoms(begin, end)
            if source_bond is None:
                raise ValueError(
                    "stereo bond could not be projected into source atom indices")
            row["begin_atom_index"] = begin
            row["end_atom_index"] = end
            row["center_bond_index"] = source_bond.GetIdx()
            result[("bond", tuple(sorted((begin, end))))] = row
    return result


def mapping_change_evidence(left: Chem.Mol, right: Chem.Mol,
                            pairs: Iterable[Iterable[int]], *,
                            microstate_contract_attached: bool = False) -> dict:
    mapped = _heavy_pairs(left, right, pairs)
    left_heavy = [atom.GetIdx() for atom in left.GetAtoms()
                  if atom.GetAtomicNum() > 1]
    right_heavy = [atom.GetIdx() for atom in right.GetAtoms()
                   if atom.GetAtomicNum() > 1]
    forward = dict(mapped)
    reverse = {b: a for a, b in mapped}
    left_unmapped = [index for index in left_heavy if index not in forward]
    right_unmapped = [index for index in right_heavy if index not in reverse]
    full_coverage = not left_unmapped and not right_unmapped and bool(mapped)
    coverage = len(mapped) / max(1, len(left_heavy), len(right_heavy))

    element_changes = []
    charge_changes = []
    for a, b in mapped:
        left_atom, right_atom = left.GetAtomWithIdx(a), right.GetAtomWithIdx(b)
        if left_atom.GetAtomicNum() != right_atom.GetAtomicNum():
            element_changes.append({
                "parent_atom_index": a, "proposal_atom_index": b,
                "parent_element": left_atom.GetSymbol(),
                "proposal_element": right_atom.GetSymbol(),
            })
        if left_atom.GetFormalCharge() != right_atom.GetFormalCharge():
            charge_changes.append({
                "parent_atom_index": a, "proposal_atom_index": b,
                "parent_formal_charge": left_atom.GetFormalCharge(),
                "proposal_formal_charge": right_atom.GetFormalCharge(),
            })

    bond_changes: list[dict] = []
    mapped_left_bonds = 0
    seen_right_bonds: set[int] = set()
    for bond in left.GetBonds():
        a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if a not in forward or b not in forward:
            continue
        mapped_left_bonds += 1
        proposal_bond = right.GetBondBetweenAtoms(forward[a], forward[b])
        if proposal_bond is None:
            bond_changes.append({
                "change": "REMOVED", "parent_atom_indices": [a, b],
                "proposal_atom_indices": [forward[a], forward[b]],
                "parent_order": str(bond.GetBondType()),
            })
            continue
        seen_right_bonds.add(proposal_bond.GetIdx())
        if (bond.GetBondType() != proposal_bond.GetBondType()
                or bond.GetIsAromatic() != proposal_bond.GetIsAromatic()):
            bond_changes.append({
                "change": "BOND_ORDER", "parent_atom_indices": [a, b],
                "proposal_atom_indices": [forward[a], forward[b]],
                "parent_order": str(bond.GetBondType()),
                "proposal_order": str(proposal_bond.GetBondType()),
                "parent_aromatic": bond.GetIsAromatic(),
                "proposal_aromatic": proposal_bond.GetIsAromatic(),
            })
    for bond in right.GetBonds():
        a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if a in reverse and b in reverse and bond.GetIdx() not in seen_right_bonds:
            bond_changes.append({
                "change": "ADDED", "parent_atom_indices": [reverse[a], reverse[b]],
                "proposal_atom_indices": [a, b],
                "proposal_order": str(bond.GetBondType()),
            })
    connectivity_changes = [row for row in bond_changes
                            if row["change"] in {"ADDED", "REMOVED"}]
    bond_order_changes = [row for row in bond_changes
                          if row["change"] == "BOND_ORDER"]

    left_charge = int(sum(atom.GetFormalCharge() for atom in left.GetAtoms()))
    right_charge = int(sum(atom.GetFormalCharge() for atom in right.GetAtoms()))
    if left_charge != right_charge:
        charge_changes.append({"scope": "TOTAL", "parent": left_charge,
                               "proposal": right_charge})
    left_rank, right_rank = _cycle_rank(left), _cycle_rank(right)

    left_stereo, right_stereo = _stereo_by_center(left), _stereo_by_center(right)
    stereo_changes: list[dict] = []
    stereo_unverified: list[dict] = []
    seen_right_stereo: set[tuple] = set()
    for key, row in left_stereo.items():
        if row["specified"] != "SPECIFIED":
            stereo_unverified.append({"side": "parent", **row})
            continue
        if key[0] == "atom":
            target = ("atom", forward.get(key[1], -1))
        else:
            a, b = key[1]
            target = ("bond", tuple(sorted((forward.get(a, -1),
                                             forward.get(b, -1)))))
        proposal_row = right_stereo.get(target)
        if proposal_row is not None:
            seen_right_stereo.add(target)
        target_is_mapped = (
            target[1] != -1 if target[0] == "atom" else
            all(index != -1 for index in target[1]))
        if proposal_row is None and target_is_mapped and full_coverage:
            stereo_changes.append({
                "change": "REMOVED_STEREOCENTER",
                "parent": row, "proposal": None,
            })
        elif proposal_row is None or proposal_row.get("specified") != "SPECIFIED":
            stereo_unverified.append({"side": "proposal", "expected_from": row,
                                      "observed": proposal_row})
        elif ((row.get("cip") or row.get("ez") or row.get("descriptor"))
              != (proposal_row.get("cip") or proposal_row.get("ez")
                  or proposal_row.get("descriptor"))):
            stereo_changes.append({"parent": row, "proposal": proposal_row})
    for key, row in right_stereo.items():
        if row["specified"] != "SPECIFIED" and key not in seen_right_stereo:
            stereo_unverified.append({"side": "proposal", **row})
        elif key not in seen_right_stereo:
            if key[0] == "atom":
                source_is_mapped = key[1] in reverse
            else:
                source_is_mapped = all(index in reverse for index in key[1])
            if source_is_mapped and full_coverage:
                stereo_changes.append({
                    "change": "ADDED_STEREOCENTER",
                    "parent": None, "proposal": row,
                })
            else:
                stereo_unverified.append({
                    "side": "proposal", "observed": row,
                    "reason": "stereocenter is outside the confirmed mapped subgraph",
                })

    ledger = [
        _dimension(
            "SCOPE", CONFIRMED if mapped else UNVERIFIED,
            f"mapped heavy subgraph {len(mapped)}/{max(len(left_heavy), len(right_heavy))} atoms; "
            f"coverage {coverage:.3f}",
            [{"mapped_heavy_atom_pairs": [list(pair) for pair in mapped],
              "parent_heavy_atoms": len(left_heavy),
              "proposal_heavy_atoms": len(right_heavy),
              "full_coverage": full_coverage}]),
        _dimension(
            "ELEMENT", CHANGED if element_changes else (
                CONFIRMED if full_coverage else UNVERIFIED),
            (f"{len(element_changes)} mapped element changes" if element_changes
             else "no mapped element change"), element_changes),
        _dimension(
            "CONNECTIVITY", CHANGED if connectivity_changes else (
                CONFIRMED if full_coverage and mapped_left_bonds else UNVERIFIED),
            (f"{len(connectivity_changes)} mapped adjacency changes"
             if connectivity_changes else
             f"no adjacency change across {mapped_left_bonds} mapped bonds"),
            connectivity_changes),
        _dimension(
            "BOND_ORDER", CHANGED if bond_order_changes else (
                CONFIRMED if full_coverage and mapped_left_bonds else UNVERIFIED),
            (f"{len(bond_order_changes)} mapped bond-order changes"
             if bond_order_changes else
             f"no bond-order change across {mapped_left_bonds} mapped bonds"),
            bond_order_changes),
        _dimension(
            "FORMAL_CHARGE", CHANGED if charge_changes else (
                CONFIRMED if full_coverage else UNVERIFIED),
            (f"{len(charge_changes)} formal-charge changes" if charge_changes
             else f"total formal charge {left_charge} in both endpoints"), charge_changes),
        _dimension(
            "STEREO", CHANGED if stereo_changes else (
                UNVERIFIED if stereo_unverified else CONFIRMED),
            (f"{len(stereo_changes)} CIP/E/Z changes" if stereo_changes else
             (f"{len(stereo_unverified)} unresolved CIP/E/Z witnesses"
              if stereo_unverified else
              ("none in either structure" if not left_stereo and not right_stereo
               else "mapped CIP/E/Z witnesses agree"))),
            stereo_changes + stereo_unverified),
        _dimension(
            "RING_CYCLE_RANK", CHANGED if left_rank != right_rank else (
                CONFIRMED if full_coverage else UNVERIFIED),
            f"cycle rank {left_rank} -> {right_rank}",
            [{"parent_cycle_rank": left_rank, "proposal_cycle_rank": right_rank}]),
        _dimension(
            "UNMAPPED", CHANGED if left_unmapped or right_unmapped else (
                CONFIRMED if mapped else UNVERIFIED),
            f"{len(left_unmapped)} parent / {len(right_unmapped)} proposal heavy atoms unmapped",
            [{"parent_atom_indices": left_unmapped,
              "proposal_atom_indices": right_unmapped}]),
    ]
    left_h = canonical_isomeric_identity(left)["total_hydrogen_count"]
    right_h = canonical_isomeric_identity(right)["total_hydrogen_count"]
    microstate_comparable = (
        full_coverage and not element_changes and not connectivity_changes)
    hydrogen_delta = right_h - left_h
    charge_delta = right_charge - left_charge
    microstate_changes = []
    microstate_ambiguity = []
    if microstate_comparable and (hydrogen_delta or charge_delta):
        if hydrogen_delta == charge_delta:
            microstate_changes.append({
                "kind": "PROTONATION",
                "parent_total_hydrogen_count": left_h,
                "proposal_total_hydrogen_count": right_h,
                "parent_formal_charge": left_charge,
                "proposal_formal_charge": right_charge,
            })
        else:
            microstate_ambiguity.append({
                "kind": "HYDROGEN_CHARGE_DELTA_NOT_DIAGNOSTIC_OF_PROTONATION",
                "hydrogen_delta": hydrogen_delta,
                "formal_charge_delta": charge_delta,
            })
    elif microstate_comparable and bond_order_changes:
        microstate_changes.append({
            "kind": "TAUTOMER_OR_VALENCE_STATE",
            "bond_order_witnesses": bond_order_changes,
        })
    elif not microstate_comparable and (hydrogen_delta or charge_delta):
        microstate_ambiguity.append({
            "kind": "ENDPOINTS_NOT_MICROSTATE_COMPARABLE",
            "reason": "element, coverage, or heavy-atom adjacency differs",
            "hydrogen_delta": hydrogen_delta,
            "formal_charge_delta": charge_delta,
        })
    microstate_verdict = (
        CHANGED if microstate_changes else
        (UNVERIFIED if microstate_ambiguity else
         (CONFIRMED if microstate_contract_attached else UNVERIFIED)))
    ledger.append(_dimension(
        "PROTONATION_TAUTOMER",
        microstate_verdict,
        (f"{len(microstate_changes)} protonation/microstate changes" if microstate_changes
         else (f"{len(microstate_ambiguity)} non-diagnostic hydrogen/charge changes"
               if microstate_ambiguity else
               ("explicit endpoint microstate contracts attached"
                if microstate_contract_attached else
                "explicit endpoint protonation/tautomer contract not attached"))),
        microstate_changes + microstate_ambiguity))

    verdicts = {row["verdict"] for row in ledger}
    overall = (UNVERIFIED if not mapped or UNVERIFIED in verdicts else
               (CHANGED if CHANGED in verdicts else CONFIRMED))
    return {
        "schema_version": "rbfe-chemistry-change.v1",
        "verdict": overall,
        "mapped_heavy_atom_count": len(mapped),
        "mapped_bond_count": mapped_left_bonds,
        "heavy_atom_coverage": coverage,
        "full_heavy_atom_coverage": full_coverage,
        "selected_heavy_atom_mapping": [list(pair) for pair in mapped],
        "ledger": ledger,
    }


def mapping_depiction_contract(left: Chem.Mol, right: Chem.Mol,
                               pairs: Iterable[Iterable[int]], *,
                               microstate_contract_attached: bool = False) -> dict:
    mapped = _heavy_pairs(left, right, pairs)
    left_depiction = depiction_index_contract(left)
    right_depiction = depiction_index_contract(right)
    depiction_pairs = [
        [left_depiction["source_to_depiction"][a],
         right_depiction["source_to_depiction"][b]]
        for a, b in mapped
    ]
    return {
        "schema_version": "rbfe-depiction-index.v2",
        "parent_smiles": left_depiction["visible_smiles"],
        "proposal_smiles": right_depiction["visible_smiles"],
        "parent_source_to_depiction": left_depiction["source_to_depiction"],
        "proposal_source_to_depiction": right_depiction["source_to_depiction"],
        "selected_heavy_atom_mapping": depiction_pairs,
        "chemistry_evidence": mapping_change_evidence(
            left, right, mapped,
            microstate_contract_attached=microstate_contract_attached),
    }


def _full_to_heavy(molecule: Chem.Mol) -> dict[int, int]:
    return {atom.GetIdx(): heavy_index
            for heavy_index, atom in enumerate(
                atom for atom in molecule.GetAtoms() if atom.GetAtomicNum() > 1)}


def _jaccard(left: set[tuple[int, int]],
             right: set[tuple[int, int]]) -> float:
    union = left | right
    return 1.0 - (len(left & right) / len(union) if union else 1.0)


def automorphism_mapping_comparison(left: Chem.Mol, right: Chem.Mol,
                                    first: Iterable[Iterable[int]],
                                    second: Iterable[Iterable[int]], *,
                                    max_automorphisms: int = 4096) -> dict:
    first_pairs = _heavy_pairs(left, right, first)
    second_pairs = _heavy_pairs(left, right, second)
    raw = _jaccard(set(first_pairs), set(second_pairs))
    if set(first_pairs) == set(second_pairs):
        return {"verdict": CONFIRMED, "equivalent": True,
                "method": "INDEX_EXACT", "index_exact_jaccard": raw,
                "automorphism_aware_jaccard": 0.0,
                "automorphism_search_truncated": False}
    if len(first_pairs) != len(second_pairs) or not first_pairs:
        return {"verdict": CHANGED, "equivalent": False,
                "method": "CARDINALITY_MISMATCH", "index_exact_jaccard": raw,
                "automorphism_aware_jaccard": raw,
                "automorphism_search_truncated": False}

    left_index, right_index = _full_to_heavy(left), _full_to_heavy(right)
    first_heavy = [(left_index[a], right_index[b]) for a, b in first_pairs]
    second_heavy = [(left_index[a], right_index[b]) for a, b in second_pairs]
    first_by_right = {b: a for a, b in first_heavy}
    second_by_left = dict(second_heavy)
    left_mol, right_mol = _heavy(left), _heavy(right)
    left_autos = left_mol.GetSubstructMatches(
        left_mol, uniquify=False, useChirality=True,
        maxMatches=max_automorphisms)
    right_autos = right_mol.GetSubstructMatches(
        right_mol, uniquify=False, useChirality=True,
        maxMatches=max_automorphisms)
    truncated = (len(left_autos) >= max_automorphisms
                 or len(right_autos) >= max_automorphisms)
    right_domain = sorted(first_by_right)
    right_restrictions = {
        tuple(auto[index] for index in right_domain) for auto in right_autos
    }
    equivalent = False
    for left_auto in left_autos:
        desired = []
        for right_atom in right_domain:
            transformed_left = left_auto[first_by_right[right_atom]]
            if transformed_left not in second_by_left:
                break
            desired.append(second_by_left[transformed_left])
        else:
            if tuple(desired) in right_restrictions:
                equivalent = True
                break
    verdict = CONFIRMED if equivalent else (UNVERIFIED if truncated else CHANGED)
    return {
        "verdict": verdict,
        "equivalent": equivalent,
        "method": "RDKIT_GRAPH_AUTOMORPHISM",
        "index_exact_jaccard": raw,
        "automorphism_aware_jaccard": 0.0 if equivalent else raw,
        "left_automorphisms_examined": len(left_autos),
        "right_automorphisms_examined": len(right_autos),
        "automorphism_search_truncated": truncated,
    }


def mapping_direction_audit(left: Chem.Mol, right: Chem.Mol,
                            forward_pairs: Iterable[Iterable[int]],
                            reverse_pairs: Iterable[Iterable[int]]) -> dict:
    inverted_reverse = [(int(right_index), int(left_index))
                        for left_index, right_index in reverse_pairs]
    comparison = automorphism_mapping_comparison(
        left, right, forward_pairs, inverted_reverse)
    return {
        "schema_version": "rbfe-mapping-direction-audit.v1",
        **comparison,
        "forward_mapping": [list(pair) for pair in _heavy_pairs(
            left, right, forward_pairs)],
        "inverse_reverse_mapping": [list(pair) for pair in _heavy_pairs(
            left, right, inverted_reverse)],
    }


def pose_geometry_evidence(protein: Chem.Mol, ligand: Chem.Mol, label: str,
                           *, hard_clash_floor_angstrom: float = 1.5,
                           contact_ceiling_angstrom: float = 6.0) -> dict:
    if not protein.GetNumConformers() or not ligand.GetNumConformers():
        raise ValueError(f"{label} pose or receptor has no 3D coordinates")
    protein_conf, ligand_conf = protein.GetConformer(), ligand.GetConformer()
    minimum = math.inf
    nearest = None
    contacts = 0
    for ligand_atom in ligand.GetAtoms():
        if ligand_atom.GetAtomicNum() <= 1:
            continue
        ligand_point = ligand_conf.GetAtomPosition(ligand_atom.GetIdx())
        for protein_atom in protein.GetAtoms():
            if protein_atom.GetAtomicNum() <= 1:
                continue
            protein_point = protein_conf.GetAtomPosition(protein_atom.GetIdx())
            distance = math.dist(
                (ligand_point.x, ligand_point.y, ligand_point.z),
                (protein_point.x, protein_point.y, protein_point.z))
            if distance < minimum:
                minimum = distance
                info = protein_atom.GetPDBResidueInfo()
                nearest = {
                    "ligand_atom_index": ligand_atom.GetIdx(),
                    "ligand_element": ligand_atom.GetSymbol(),
                    "protein_atom_index": protein_atom.GetIdx(),
                    "protein_element": protein_atom.GetSymbol(),
                    "protein_atom_name": info.GetName().strip() if info else None,
                    "residue_name": info.GetResidueName().strip() if info else None,
                    "residue_number": info.GetResidueNumber() if info else None,
                    "chain_id": info.GetChainId().strip() if info else None,
                    "distance_angstrom": round(distance, 3),
                }
            if distance <= contact_ceiling_angstrom:
                contacts += 1
    if nearest is None:
        raise ValueError(f"{label} pose or receptor has no heavy atoms")
    nearest["hard_clash_floor_angstrom"] = hard_clash_floor_angstrom
    nearest["contact_ceiling_angstrom"] = contact_ceiling_angstrom
    verdict = (CHANGED if minimum < hard_clash_floor_angstrom else
               (UNVERIFIED if contacts == 0 else CONFIRMED))
    return {
        "schema_version": "rbfe-pose-geometry.v1",
        "verdict": verdict,
        "minimum_heavy_atom_distance_angstrom": round(minimum, 3),
        "heavy_atom_contacts_within_6_angstrom": contacts,
        "nearest_pair_witness": nearest,
    }


__all__ = [
    "CHANGED", "CONFIRMED", "UNVERIFIED",
    "automorphism_mapping_comparison", "canonical_isomeric_identity",
    "depiction_index_contract", "input_pose_identity_witness",
    "mapping_change_evidence", "mapping_depiction_contract",
    "mapping_direction_audit", "molecule_from_smiles",
    "pose_geometry_evidence", "require_resolved_stereochemistry",
]
