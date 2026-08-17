"""Fixed entrypoint for official OpenFE ligand-network and mapping planning.

Executed by the pinned OpenFE runtime.  It accepts one bounded JSON document and emits
one JSON document; callers never supply a command or module name.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import openfe
from openfe.setup import ligand_network_planning
from rdkit import Chem
from rdkit.Chem import AllChem

try:
    from motif.rbfe_chemistry_evidence import (
        CHANGED, CONFIRMED, UNVERIFIED,
        automorphism_mapping_comparison,
        mapping_depiction_contract,
        mapping_direction_audit,
        require_resolved_stereochemistry,
    )
except ModuleNotFoundError:  # fixed-entrypoint execution adds this directory only
    from rbfe_chemistry_evidence import (
        CHANGED, CONFIRMED, UNVERIFIED,
        automorphism_mapping_comparison,
        mapping_depiction_contract,
        mapping_direction_audit,
        require_resolved_stereochemistry,
    )


_MIN_EXECUTION_MAPPING_SCORE = 0.8
_CHEMISTRY_LEDGER_DIMENSIONS = {
    "SCOPE", "ELEMENT", "CONNECTIVITY", "BOND_ORDER", "FORMAL_CHARGE",
    "STEREO", "RING_CYCLE_RANK", "UNMAPPED", "PROTONATION_TAUTOMER",
}


def _component(row: dict, seed: int):
    molecule = Chem.MolFromSmiles(row["smiles"])
    if molecule is None:
        raise ValueError(f"cannot parse {row['id']!r}")
    identity = require_resolved_stereochemistry(molecule, str(row["id"]))
    molecule = Chem.AddHs(molecule)
    if AllChem.EmbedMolecule(molecule, randomSeed=seed) != 0:
        raise ValueError(f"3D embedding failed for {row['id']!r}")
    return (openfe.SmallMoleculeComponent.from_rdkit(
        molecule, name=row["id"]), Chem.RemoveHs(molecule), identity)


def _pairs(mapping) -> set[tuple[int, int]]:
    return {(int(left), int(right))
            for left, right in mapping.componentA_to_componentB.items()}


def _disagreement(left: set[tuple[int, int]],
                  right: set[tuple[int, int]]) -> float:
    union = left | right
    agreement = left & right
    return 1.0 - (len(agreement) / len(union) if union else 1.0)


def _digest(value) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode()).hexdigest()


def _connected_components(compound_ids: list[str], edges: list[dict]) -> list[list[str]]:
    adjacency = {compound_id: set() for compound_id in compound_ids}
    for edge in edges:
        left, right = edge["left_id"], edge["right_id"]
        adjacency[left].add(right)
        adjacency[right].add(left)
    remaining = set(compound_ids)
    components: list[list[str]] = []
    while remaining:
        seed = min(remaining)
        stack, component = [seed], set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency[current] - component)
        remaining -= component
        components.append(sorted(component))
    return sorted(components, key=lambda row: (len(row), row))


def _execution_eligibility(*, selected_heavy: set[tuple[int, int]],
                           mapping_score: float, left_charge: int,
                           right_charge: int, chemistry_evidence: dict,
                           direction_audit: dict) -> tuple[dict, bool]:
    """Separate a usable planning candidate from an executable mapping."""
    reasons: list[dict[str, str]] = []
    hard_rejection = False
    if not selected_heavy:
        hard_rejection = True
        reasons.append({
            "code": "ZERO_HEAVY_ATOM_MAP",
            "message": "selected mapping contains zero heavy-atom pairs",
        })
    if left_charge != right_charge:
        hard_rejection = True
        reasons.append({
            "code": "FORMAL_CHARGE_CHANGE",
            "message": f"formal charge changes {left_charge} -> {right_charge}",
        })
    if mapping_score <= 0:
        hard_rejection = True
        reasons.append({
            "code": "NON_POSITIVE_MAPPING_SCORE",
            "message": f"OpenFE mapping score is {mapping_score:.3f}",
        })
    elif mapping_score < _MIN_EXECUTION_MAPPING_SCORE:
        hard_rejection = True
        reasons.append({
            "code": "MAPPING_SCORE_BELOW_EXECUTION_FLOOR",
            "message": (
                f"OpenFE mapping score {mapping_score:.3f} is below the "
                f"execution floor {_MIN_EXECUTION_MAPPING_SCORE:.3f}"),
        })

    ledger = chemistry_evidence.get("ledger")
    ledger_by_dimension = {
        row.get("dimension"): row for row in ledger or []
        if isinstance(row, dict)
    }
    chemistry_well_formed = (
        chemistry_evidence.get("schema_version") == "rbfe-chemistry-change.v1"
        and isinstance(ledger, list)
        and len(ledger) == len(_CHEMISTRY_LEDGER_DIMENSIONS)
        and set(ledger_by_dimension) == _CHEMISTRY_LEDGER_DIMENSIONS
        and chemistry_evidence.get("verdict") in {
            CONFIRMED, CHANGED, UNVERIFIED}
        and all(row.get("verdict") in {CONFIRMED, CHANGED, UNVERIFIED}
                and isinstance(row.get("witnesses"), list)
                for row in ledger if isinstance(row, dict))
    )
    if (not chemistry_well_formed
            or chemistry_evidence.get("verdict") == UNVERIFIED):
        reasons.append({
            "code": "CHEMISTRY_EVIDENCE_UNVERIFIED",
            "message": (
                "selected mapping chemistry evidence is incomplete or "
                "UNVERIFIED until exact endpoint microstates are attached"),
        })
    stereo = ledger_by_dimension.get("STEREO") or {}
    if stereo.get("verdict") == UNVERIFIED or "STEREO" not in ledger_by_dimension:
        reasons.append({
            "code": "STEREOCHEMISTRY_UNVERIFIED",
            "message": "selected mapping stereochemistry is UNVERIFIED",
        })
    if direction_audit.get("verdict") != CONFIRMED:
        reasons.append({
            "code": "MAPPING_DIRECTION_UNVERIFIED",
            "message": (
                "selected mapping lacks a CONFIRMED A-to-B/B-to-A direction audit"),
        })

    unverified_codes = {
        "ZERO_HEAVY_ATOM_MAP", "NON_POSITIVE_MAPPING_SCORE",
        "MAPPING_SCORE_BELOW_EXECUTION_FLOOR",
        "CHEMISTRY_EVIDENCE_UNVERIFIED", "STEREOCHEMISTRY_UNVERIFIED",
        "MAPPING_DIRECTION_UNVERIFIED",
    }
    verdict = (UNVERIFIED if any(
        reason["code"] in unverified_codes for reason in reasons) else
        (CHANGED if reasons else CONFIRMED))
    return {"verdict": verdict, "reasons": reasons}, hard_rejection


def plan(document: dict) -> dict:
    campaign_keys = (
        "campaign_id", "campaign_scientific_generation",
        "campaign_scientific_digest", "prepared_system_id",
    )
    campaign_contract = {
        key: document[key] for key in campaign_keys
        if document.get(key) is not None
    }
    if campaign_contract and set(campaign_contract) != set(campaign_keys):
        missing = sorted(set(campaign_keys) - set(campaign_contract))
        raise ValueError(
            "campaign-bound network planning requires one complete scientific "
            f"context; missing {missing}")
    compounds = document["compounds"]
    if not 2 <= len(compounds) <= 256:
        raise ValueError("OpenFE network planning requires 2..256 compounds")
    compound_ids = [str(row.get("id") or "") for row in compounds]
    if (any(not compound_id for compound_id in compound_ids)
            or len(set(compound_ids)) != len(compound_ids)):
        raise ValueError(
            "OpenFE network planning requires unique non-empty compound ids")
    built = [_component(row, int(document.get("seed", 0)) + index)
             for index, row in enumerate(compounds)]
    components = [row[0] for row in built]
    chemistry = {compound["id"]: built[index][1]
                 for index, compound in enumerate(compounds)}
    identities = {compound["id"]: built[index][2]
                  for index, compound in enumerate(compounds)}
    # Chem.AddHs appends explicit hydrogens after the original heavy-atom order.
    # Mapper comparison must use the same atom domain: Kartograf deliberately
    # omits H while LoMap may include it, so raw-pair Jaccard is a scope mismatch.
    heavy_counts = {
        row["id"]: Chem.MolFromSmiles(row["smiles"]).GetNumHeavyAtoms()
        for row in compounds
    }
    lomap = openfe.LomapAtomMapper(time=int(document.get("lomap_timeout", 20)),
                                  threed=False)
    kartograf = openfe.KartografAtomMapper(atom_map_hydrogens=False,
                                           allow_bond_breaks=False)
    network = ligand_network_planning.generate_minimal_redundant_network(
        ligands=components, mappers=[lomap, kartograf],
        scorer=openfe.lomap_scorers.default_lomap_score,
        progress=False, mst_num=int(document.get("mst_num", 2)), n_processes=1)
    by_name = {component.name: component for component in components}
    edges = []
    rejected_edges = []
    for mapping in sorted(network.edges,
                          key=lambda edge: (edge.componentA.name, edge.componentB.name)):
        left, right = mapping.componentA.name, mapping.componentB.name
        proposals = {}
        reverse_proposals = {}
        for name, mapper in (("lomap", lomap), ("kartograf", kartograf)):
            proposed = list(mapper.suggest_mappings(by_name[left], by_name[right]))
            if proposed:
                proposals[name] = _pairs(proposed[0])
            proposed_reverse = list(mapper.suggest_mappings(
                by_name[right], by_name[left]))
            if proposed_reverse:
                reverse_proposals[name] = _pairs(proposed_reverse[0])
        names = sorted(proposals)
        raw_disagreement = None
        heavy_disagreement = None
        automorphism_comparison = None
        heavy_proposals = {
            name: {pair for pair in pairs
                   if pair[0] < heavy_counts[left]
                   and pair[1] < heavy_counts[right]}
            for name, pairs in proposals.items()
        }
        if len(names) >= 2:
            raw_disagreement = _disagreement(
                proposals[names[0]], proposals[names[1]])
            automorphism_comparison = automorphism_mapping_comparison(
                chemistry[left], chemistry[right],
                heavy_proposals[names[0]], heavy_proposals[names[1]])
            heavy_disagreement = automorphism_comparison[
                "automorphism_aware_jaccard"]
        selected_pairs = _pairs(mapping)
        selected_heavy = {pair for pair in selected_pairs
                          if pair[0] < heavy_counts[left]
                          and pair[1] < heavy_counts[right]}
        direction_audits = {}
        for name in sorted(set(proposals) | set(reverse_proposals)):
            forward = heavy_proposals.get(name)
            reverse = reverse_proposals.get(name)
            if not forward or not reverse:
                direction_audits[name] = {
                    "schema_version": "rbfe-mapping-direction-audit.v1",
                    "verdict": UNVERIFIED,
                    "reason": "mapper did not return both A->B and B->A proposals",
                }
                continue
            reverse_heavy = {
                pair for pair in reverse
                if pair[0] < heavy_counts[right]
                and pair[1] < heavy_counts[left]
            }
            direction_audits[name] = mapping_direction_audit(
                chemistry[left], chemistry[right], forward, reverse_heavy)
        selected_direction_audits = []
        for name, reverse in reverse_proposals.items():
            reverse_heavy = {
                pair for pair in reverse
                if pair[0] < heavy_counts[right]
                and pair[1] < heavy_counts[left]
            }
            selected_direction_audits.append((
                name, mapping_direction_audit(
                    chemistry[left], chemistry[right],
                    selected_heavy, reverse_heavy)))
        selected_direction = next((audit for _, audit in selected_direction_audits
                                   if audit["verdict"] == CONFIRMED), None)
        if selected_direction is None and selected_direction_audits:
            selected_direction = selected_direction_audits[0][1]
        if selected_direction is None:
            selected_direction = {
                "schema_version": "rbfe-mapping-direction-audit.v1",
                "verdict": UNVERIFIED,
                "reason": "no reverse mapper proposal was available",
            }
        depiction = mapping_depiction_contract(
            chemistry[left], chemistry[right], selected_heavy,
            microstate_contract_attached=False)
        mapping_score = float(openfe.lomap_scorers.default_lomap_score(mapping))
        left_charge = identities[left]["formal_charge"]
        right_charge = identities[right]["formal_charge"]
        execution_eligibility, hard_rejection = _execution_eligibility(
            selected_heavy=selected_heavy,
            mapping_score=mapping_score,
            left_charge=left_charge,
            right_charge=right_charge,
            chemistry_evidence=depiction["chemistry_evidence"],
            direction_audit=selected_direction,
        )
        eligibility_reasons = execution_eligibility["reasons"]
        edge = {
            "left_id": left, "right_id": right,
            "selected_atom_mapping": sorted([list(pair) for pair in selected_pairs]),
            "selected_heavy_atom_mapping": sorted(
                [list(pair) for pair in selected_heavy]),
            "mapped_heavy_atom_count": len(selected_heavy),
            "mapping_score": mapping_score,
            "mapping_methods": names,
            "mapping_disagreement_jaccard": heavy_disagreement,
            "mapping_disagreement_index_exact_jaccard": (
                automorphism_comparison["index_exact_jaccard"]
                if automorphism_comparison else None),
            "mapping_disagreement_all_atoms_jaccard": raw_disagreement,
            "mapping_automorphism_audit": automorphism_comparison,
            "mapping_direction_audits": direction_audits,
            "selected_mapping_direction_audit": selected_direction,
            "mapping_proposals": {name: sorted([list(pair) for pair in pairs])
                                  for name, pairs in proposals.items()},
            "heavy_atom_mapping_proposals": {
                name: sorted([list(pair) for pair in pairs])
                for name, pairs in heavy_proposals.items()
            },
            "depiction_contract": depiction,
            "chemistry_evidence": depiction["chemistry_evidence"],
            "execution_eligibility": execution_eligibility,
            "culprit_endpoints": [left, right] if eligibility_reasons else [],
            "status": ("rejected" if hard_rejection else
                       ("candidate" if eligibility_reasons else "planned")),
        }
        (rejected_edges if hard_rejection else edges).append(edge)
    executable_edges = [
        edge for edge in edges
        if edge["execution_eligibility"]["verdict"] == CONFIRMED
    ]
    execution_components = _connected_components(compound_ids, executable_edges)
    network_ready = len(execution_components) == 1 and bool(executable_edges)
    all_reviewed_edges = edges + rejected_edges
    identity_contract = {
        "schema_version": "rbfe-network-identity.v1",
        "compounds": identities,
    }
    identity_contract["digest"] = _digest(identity_contract)
    depiction_contract = {
        "schema_version": "rbfe-network-depictions.v1",
        "edges": [{"left_id": edge["left_id"], "right_id": edge["right_id"],
                   "status": edge["status"],
                   "contract": edge["depiction_contract"]}
                  for edge in all_reviewed_edges],
    }
    depiction_contract["digest"] = _digest(depiction_contract)
    chemistry_verdicts = {
        edge["chemistry_evidence"]["verdict"] for edge in all_reviewed_edges
    }
    chemistry_summary = {
        "schema_version": "rbfe-network-chemistry-evidence.v1",
        "verdict": (UNVERIFIED if not all_reviewed_edges
                    or UNVERIFIED in chemistry_verdicts else
                    (CHANGED if CHANGED in chemistry_verdicts else CONFIRMED)),
        "edges": [{"left_id": edge["left_id"], "right_id": edge["right_id"],
                   "status": edge["status"],
                   "evidence": edge["chemistry_evidence"]}
                  for edge in all_reviewed_edges],
    }
    chemistry_summary["digest"] = _digest(chemistry_summary)
    culprit_edges = [
        {"left_id": edge["left_id"], "right_id": edge["right_id"],
         "reasons": edge["execution_eligibility"]["reasons"]}
        for edge in all_reviewed_edges
        if edge["execution_eligibility"]["verdict"] != CONFIRMED
    ]
    result = {
        "schema_version": "1.0", "engine": "OpenFE",
        "engine_version": openfe.__version__,
        "planner": "generate_minimal_redundant_network",
        "mappers": ["LomapAtomMapper", "KartografAtomMapper"],
        "ligand_network": json.loads(network.to_json()),
        "compound_identities": identities,
        "identity_contract": identity_contract,
        "depiction_contract": depiction_contract,
        "chemistry_evidence": chemistry_summary,
        "edges": edges,
        "candidate_edges": [
            edge for edge in edges
            if edge["execution_eligibility"]["verdict"] != CONFIRMED
        ],
        "rejected_edges": rejected_edges,
        "execution_network_gate": {
            "verdict": CONFIRMED if network_ready else UNVERIFIED,
            "connected_components": execution_components,
            "culprit_edges": culprit_edges,
            "reason": ("all compounds are connected by executable mappings"
                       if network_ready else
                       "unverified or rejected mappings leave no connected "
                       "executable graph; culprit endpoint pairs are listed in "
                       "culprit_edges"),
        },
        "planner_diagnostics": {
            "reviewed_edge_count": len(all_reviewed_edges),
            "executable_edge_count": len(executable_edges),
            "candidate_edge_count": len(edges) - len(executable_edges),
            "rejected_edge_count": len(rejected_edges),
            "network_gate": CONFIRMED if network_ready else UNVERIFIED,
            "minimum_execution_mapping_score": _MIN_EXECUTION_MAPPING_SCORE,
            "culprit_edges": culprit_edges,
        },
        "campaign_contract": campaign_contract,
    }
    result.update(campaign_contract)
    return result


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: openfe_network_planner.py INPUT.json OUTPUT.json")
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    if source.stat().st_size > 4 << 20:
        raise ValueError("planner input exceeds 4 MiB")
    document = json.loads(source.read_text())
    target.write_text(json.dumps(plan(document), sort_keys=True,
                                 separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
