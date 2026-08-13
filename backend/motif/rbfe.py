"""RBFE network design and evidence aggregation without fabricated simulations."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Any, Iterable

import numpy as np


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _chemistry(compounds: list[dict[str, str]]) -> tuple[list[Any], list[Any]]:
    from rdkit import Chem
    from rdkit.Chem import rdFingerprintGenerator
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    molecules, fingerprints = [], []
    for row in compounds:
        molecule = Chem.MolFromSmiles(row["smiles"])
        if molecule is None:
            raise ValueError(f"cannot parse SMILES for {row['id']!r}")
        molecules.append(molecule)
        fingerprints.append(generator.GetFingerprint(molecule))
    return molecules, fingerprints


def plan_rbfe_network(compounds: Iterable[dict[str, str]], *,
                      extra_edge_fraction: float = 0.35,
                      minimum_similarity: float = 0.15) -> dict[str, Any]:
    """Build a connected, auditable maximum-similarity RBFE network."""
    from rdkit import Chem, DataStructs
    from rdkit.Chem import rdFMCS
    rows = [dict(row) for row in compounds]
    if len(rows) < 2 or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("RBFE network requires at least two uniquely identified compounds")
    molecules, fingerprints = _chemistry(rows)
    possible = []
    for left in range(len(rows)):
        for right in range(left + 1, len(rows)):
            similarity = float(DataStructs.TanimotoSimilarity(
                fingerprints[left], fingerprints[right]))
            possible.append((similarity, left, right))
    possible.sort(key=lambda item: (-item[0], rows[item[1]]["id"], rows[item[2]]["id"]))

    parent = list(range(len(rows)))
    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item
    selected: list[tuple[float, int, int, str]] = []
    for similarity, left, right in possible:
        a, b = find(left), find(right)
        if a != b:
            parent[a] = b
            selected.append((similarity, left, right, "connectivity"))
    target_extra = max(0, round(extra_edge_fraction * len(rows)))
    used = {(left, right) for _, left, right, _ in selected}
    for similarity, left, right in possible:
        if len(selected) >= len(rows) - 1 + target_extra:
            break
        if (left, right) not in used and similarity >= minimum_similarity:
            selected.append((similarity, left, right, "redundancy"))
            used.add((left, right))

    edges = []
    for index, (similarity, left, right, purpose) in enumerate(selected):
        mcs = rdFMCS.FindMCS(
            [molecules[left], molecules[right]], timeout=10,
            ringMatchesRingOnly=True, completeRingsOnly=True,
            atomCompare=rdFMCS.AtomCompare.CompareElements,
            bondCompare=rdFMCS.BondCompare.CompareOrderExact)
        smarts = mcs.smartsString
        atom_count = int(mcs.numAtoms)
        edges.append({
            "edge_id": f"rbfe-edge-{index:04d}",
            "left_id": rows[left]["id"], "right_id": rows[right]["id"],
            "tanimoto": similarity, "mcs_smarts": smarts,
            "mapped_atom_count": atom_count,
            "left_heavy_atom_fraction": atom_count / max(1, molecules[left].GetNumHeavyAtoms()),
            "right_heavy_atom_fraction": atom_count / max(1, molecules[right].GetNumHeavyAtoms()),
            "purpose": purpose, "status": "planned",
        })
    document = {
        "schema_version": "1.0", "kind": "rbfe_network_plan",
        "compounds": [{"id": row["id"], "canonical_smiles":
                       Chem.MolToSmiles(molecules[index], canonical=True,
                                        isomericSmiles=True)}
                      for index, row in enumerate(rows)],
        "edges": edges,
        "policy": {"extra_edge_fraction": extra_edge_fraction,
                   "minimum_similarity": minimum_similarity,
                   "mapping": "RDKit FMCS element/order, complete rings"},
        "claim_boundary": (
            "Network and atom-mapping proposal only. No free energy is inferred "
            "until a separately versioned engine produces edge observations."),
    }
    document["digest"] = _digest(document)
    return document


def aggregate_rbfe_results(network: dict[str, Any],
                           observations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Weighted graph fit with partial-edge and cycle-closure diagnostics."""
    compound_ids = [row["id"] for row in network["compounds"]]
    index = {compound_id: position for position, compound_id in enumerate(compound_ids)}
    known_edges = {edge["edge_id"]: edge for edge in network["edges"]}
    rows, accepted, failed = [], [], []
    for source in observations:
        row = dict(source)
        edge_id = row.get("edge_id")
        if edge_id not in known_edges:
            raise ValueError(f"observation references unknown edge {edge_id!r}")
        if row.get("status") != "completed":
            failed.append({"edge_id": edge_id, "status": row.get("status", "failed"),
                           "reason": row.get("reason", "unspecified")})
            continue
        sigma = float(row["uncertainty_kcal_mol"])
        value = float(row["ddg_kcal_mol"])
        if sigma <= 0 or not math.isfinite(sigma) or not math.isfinite(value):
            raise ValueError(f"edge {edge_id!r} has invalid value or uncertainty")
        edge = known_edges[edge_id]
        vector = np.zeros(len(compound_ids) - 1)
        if index[edge["left_id"]] > 0:
            vector[index[edge["left_id"]] - 1] = -1
        if index[edge["right_id"]] > 0:
            vector[index[edge["right_id"]] - 1] = 1
        rows.append((vector, value, sigma))
        accepted.append({**row, "left_id": edge["left_id"],
                         "right_id": edge["right_id"]})
    if len(rows) < len(compound_ids) - 1:
        return {
            "schema_version": "1.0", "kind": "rbfe_network_result",
            "network_digest": network["digest"], "status": "partial",
            "completed_edges": accepted, "failed_edges": failed,
            "reason": "completed edges do not span the compound network",
            "node_estimates": [], "cycle_closure": [],
            "claim_boundary": "No fabricated estimates for disconnected nodes.",
            "digest": _digest({"network": network["digest"], "accepted": accepted,
                               "failed": failed, "status": "partial"}),
        }
    design = np.vstack([row[0] for row in rows])
    values = np.asarray([row[1] for row in rows])
    weights = np.diag([1.0 / row[2] ** 2 for row in rows])
    information = design.T @ weights @ design
    if np.linalg.matrix_rank(information) < len(compound_ids) - 1:
        raise ValueError("completed edge graph is disconnected")
    covariance = np.linalg.inv(information)
    fitted = covariance @ design.T @ weights @ values
    node_values = np.concatenate(([0.0], fitted))
    node_sigma = np.concatenate(([0.0], np.sqrt(np.diag(covariance))))
    residuals = values - design @ fitted
    for item, residual, source in zip(accepted, residuals, rows):
        item["residual_kcal_mol"] = float(residual)
        item["standardized_residual"] = float(residual / source[2])

    # A graph's redundant edges are exactly its independent cycle-closure degrees
    # of freedom; weighted fit residuals expose those inconsistencies without
    # inventing a preferred path.
    cycle_closure = [
        {"edge_id": item["edge_id"],
         "closure_residual_kcal_mol": item["residual_kcal_mol"],
         "standardized_residual": item["standardized_residual"]}
        for item in accepted if known_edges[item["edge_id"]]["purpose"] == "redundancy"
    ]
    document = {
        "schema_version": "1.0", "kind": "rbfe_network_result",
        "network_digest": network["digest"], "status": "complete",
        "reference_compound_id": compound_ids[0],
        "node_estimates": [
            {"compound_id": compound_id, "relative_dg_kcal_mol": float(node_values[i]),
             "uncertainty_kcal_mol": float(node_sigma[i])}
            for i, compound_id in enumerate(compound_ids)],
        "completed_edges": accepted, "failed_edges": failed,
        "cycle_closure": cycle_closure,
        "claim_boundary": (
            "Statistical aggregation of supplied edge observations. Reliability "
            "depends on the separately governed alchemical engine and convergence."),
    }
    document["digest"] = _digest(document)
    return document


__all__ = ["aggregate_rbfe_results", "plan_rbfe_network"]
