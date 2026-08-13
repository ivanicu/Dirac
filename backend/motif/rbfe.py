"""RBFE network design and evidence aggregation without fabricated simulations."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
import subprocess
import tempfile
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
                      minimum_similarity: float = 0.15,
                      mode: str = "pilot",
                      planner: str = "openfe",
                      openfe_runtime: str | None = None) -> dict[str, Any]:
    """Build a connected, auditable maximum-similarity RBFE network."""
    from rdkit import Chem, DataStructs
    from rdkit.Chem import rdFMCS
    rows = [dict(row) for row in compounds]
    if len(rows) < 2 or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("RBFE network requires at least two uniquely identified compounds")
    if mode not in {"pilot", "full"}:
        raise ValueError("RBFE mode must be pilot or full")
    if mode == "full" and len(rows) < 4:
        raise ValueError("full RBFE requires at least four nodes; pilot edges are separate")
    if planner not in {"openfe", "rdkit_fallback"}:
        raise ValueError("planner must be openfe or rdkit_fallback")
    official = None
    if planner == "openfe":
        official = _plan_with_openfe(rows, runtime=openfe_runtime)
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
    if official is not None:
        diagnostic = {frozenset((edge["left_id"], edge["right_id"])): edge
                      for edge in edges}
        official_edges = []
        for index, edge in enumerate(official["edges"]):
            pair = frozenset((edge["left_id"], edge["right_id"]))
            fmcs = diagnostic.get(pair, {})
            official_edges.append({
                "edge_id": f"rbfe-edge-{index:04d}",
                "left_id": edge["left_id"], "right_id": edge["right_id"],
                "mapping_score": edge["mapping_score"],
                "mapped_atom_count": len(edge["selected_atom_mapping"]),
                "selected_atom_mapping": edge["selected_atom_mapping"],
                "mapping_methods": edge["mapping_methods"],
                "mapping_disagreement_jaccard": edge["mapping_disagreement_jaccard"],
                "mapping_proposals": edge["mapping_proposals"],
                "rdkit_fmcs_diagnostic": {
                    key: fmcs.get(key) for key in (
                        "tanimoto", "mcs_smarts", "mapped_atom_count",
                        "left_heavy_atom_fraction", "right_heavy_atom_fraction")
                },
                "purpose": "openfe_redundant_network", "status": "planned",
            })
        edges = official_edges
        if mode == "full" and len(edges) < 4:
            raise RuntimeError("full four-node OpenFE network must contain at least four edges")
    document = {
        "schema_version": "1.0", "kind": "rbfe_network_plan",
        "compounds": [{"id": row["id"], "canonical_smiles":
                       Chem.MolToSmiles(molecules[index], canonical=True,
                                        isomericSmiles=True)}
                      for index, row in enumerate(rows)],
        "edges": edges, "mode": mode,
        "official_openfe_plan": official,
        "policy": {"extra_edge_fraction": extra_edge_fraction,
                   "minimum_similarity": minimum_similarity,
                   "mapping": ("OpenFE LigandNetwork with Lomap+Kartograf disagreement; "
                               "RDKit FMCS retained as independent diagnostic")
                              if official else "RDKit FMCS fallback",
                   "planner": planner},
        "claim_boundary": (
            "Network and atom-mapping proposal only. No free energy is inferred "
            "until a separately versioned engine produces edge observations."),
    }
    if mode == "full":
        document["execution_matrix"] = expand_rbfe_execution_matrix(
            {"compounds": document["compounds"], "edges": edges}, repeats=3)
    document["digest"] = _digest(document)
    return document


def expand_rbfe_execution_matrix(network: dict[str, Any], *, repeats: int = 3,
                                 include_pilot: bool = False) -> dict[str, Any]:
    """Expand a release network to edge × two legs × independent repeats."""
    if len(network.get("compounds", [])) < 4 or len(network.get("edges", [])) < 4:
        raise ValueError("release RBFE matrix requires >=4 nodes and >=4 edges")
    if repeats < 3:
        raise ValueError("release RBFE requires at least three independent repeats")
    executions = [
        {"edge_id": edge["edge_id"], "leg": leg, "repeat_index": repeat,
         "role": "production"}
        for edge in network["edges"] for leg in ("complex", "solvent")
        for repeat in range(1, repeats + 1)
    ]
    return {
        "edge_count": len(network["edges"]), "legs_per_edge": 2,
        "repeat_count": repeats, "production_execution_count": len(executions),
        "pilot_included_in_production_count": include_pilot,
        "executions": executions,
    }


def _plan_with_openfe(compounds: list[dict[str, str]], *,
                      runtime: str | None = None) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    executable = Path(runtime) if runtime else root / "openfe-runtime-v2/bin/python"
    script = Path(__file__).with_name("openfe_network_planner.py")
    if not executable.is_file():
        raise RuntimeError("pinned OpenFE runtime is absent; choose rdkit_fallback explicitly")
    with tempfile.TemporaryDirectory(prefix="motif-openfe-plan-") as temporary:
        source, target = Path(temporary) / "input.json", Path(temporary) / "output.json"
        source.write_text(json.dumps({"compounds": compounds, "seed": 1729, "mst_num": 2},
                                     sort_keys=True, separators=(",", ":")))
        completed = subprocess.run(
            [str(executable), str(script), str(source), str(target)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
            check=False, text=True)
        if completed.returncode != 0 or not target.is_file():
            raise RuntimeError("official OpenFE network planning failed: "
                               + completed.stderr[-2000:])
        result = json.loads(target.read_text())
    if result.get("engine") != "OpenFE" or not result.get("edges"):
        raise RuntimeError("official OpenFE planner returned no auditable edges")
    return result


def aggregate_rbfe_results(network: dict[str, Any],
                           observations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Covariance-aware graph fit with repeat/leg and closure diagnostics.

    The sign convention is frozen as ``right minus left``.  Observations may carry
    complex/solvent legs and independent repeats; those are first paired into edge
    ΔΔG values (complex minus solvent) and repeat disagreement remains visible.
    """
    compound_ids = [row["id"] for row in network["compounds"]]
    index = {compound_id: position for position, compound_id in enumerate(compound_ids)}
    known_edges = {edge["edge_id"]: edge for edge in network["edges"]}
    source_rows = [dict(source) for source in observations]
    source_rows, repeat_diagnostics = _pair_legs_and_repeats(source_rows)
    rows, accepted, failed = [], [], []
    covariance_group_by_row: list[str | None] = []
    for source in source_rows:
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
        covariance_group_by_row.append(row.get("covariance_group"))
        accepted.append({**row, "left_id": edge["left_id"],
                         "right_id": edge["right_id"]})
    if len(rows) < len(compound_ids) - 1:
        return {
            "schema_version": "1.0", "kind": "rbfe_network_result",
            "network_digest": network["digest"], "status": "partial",
            "completed_edges": accepted, "failed_edges": failed,
            "reason": "completed edges do not span the compound network",
            "node_estimates": [], "node_covariance_kcal2_mol2": [],
            "cycle_closure": [], "repeat_diagnostics": repeat_diagnostics,
            "sign_convention": "ddg = dg_right - dg_left; edge = complex - solvent",
            "claim_boundary": "No fabricated estimates for disconnected nodes.",
            "digest": _digest({"network": network["digest"], "accepted": accepted,
                               "failed": failed, "status": "partial"}),
        }
    design = np.vstack([row[0] for row in rows])
    values = np.asarray([row[1] for row in rows])
    observation_covariance = np.diag([row[2] ** 2 for row in rows])
    supplied = network.get("observation_covariance_kcal2_mol2")
    if supplied is not None:
        supplied_matrix = np.asarray(supplied, dtype=float)
        if supplied_matrix.shape != observation_covariance.shape:
            raise ValueError("observation covariance shape does not match accepted edges")
        if not np.allclose(supplied_matrix, supplied_matrix.T, atol=1e-12):
            raise ValueError("observation covariance must be symmetric")
        if np.min(np.linalg.eigvalsh(supplied_matrix)) < -1e-10:
            raise ValueError("observation covariance must be positive semidefinite")
        observation_covariance = supplied_matrix
    weights = np.linalg.pinv(observation_covariance)
    information = design.T @ weights @ design
    if np.linalg.matrix_rank(information) < len(compound_ids) - 1:
        raise ValueError("completed edge graph is disconnected")
    covariance = np.linalg.pinv(information)
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
        "reference_gauge": {"compound_id": compound_ids[0], "relative_dg_kcal_mol": 0.0},
        "sign_convention": "ddg = dg_right - dg_left; edge = complex - solvent",
        "node_estimates": [
            {"compound_id": compound_id, "relative_dg_kcal_mol": float(node_values[i]),
             "uncertainty_kcal_mol": float(node_sigma[i])}
            for i, compound_id in enumerate(compound_ids)],
        "completed_edges": accepted, "failed_edges": failed,
        "repeat_diagnostics": repeat_diagnostics,
        "observation_covariance_kcal2_mol2": observation_covariance.tolist(),
        "node_covariance_kcal2_mol2": covariance.tolist(),
        "cycle_closure": cycle_closure,
        "claim_boundary": (
            "Statistical aggregation of supplied edge observations. Reliability "
            "depends on the separately governed alchemical engine and convergence."),
    }
    document["digest"] = _digest(document)
    return document


def _pair_legs_and_repeats(observations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert completed OpenFE legs into repeat-level edges, then combine repeats."""
    if not any("leg" in row for row in observations):
        return observations, []
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    passthrough, failed = [], []
    for row in observations:
        if "leg" not in row:
            passthrough.append(row)
            continue
        edge_id = str(row.get("edge_id"))
        repeat = int(row.get("repeat_index", 0))
        leg = str(row["leg"])
        if leg not in {"complex", "solvent"}:
            failed.append({"edge_id": edge_id, "repeat_index": repeat,
                           "status": "unsupported_leg", "reason": leg})
            continue
        grouped[(edge_id, repeat)][leg] = row
    repeat_edges: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = list(failed)
    for (edge_id, repeat), legs in sorted(grouped.items()):
        if set(legs) != {"complex", "solvent"}:
            diagnostics.append({"edge_id": edge_id, "repeat_index": repeat,
                                "status": "incomplete_legs",
                                "present_legs": sorted(legs)})
            continue
        if any(row.get("status") != "completed" for row in legs.values()):
            diagnostics.append({"edge_id": edge_id, "repeat_index": repeat,
                                "status": "failed_leg"})
            continue
        complex_row, solvent_row = legs["complex"], legs["solvent"]
        value = float(complex_row["dg_kcal_mol"]) - float(solvent_row["dg_kcal_mol"])
        variance = (float(complex_row["uncertainty_kcal_mol"]) ** 2
                    + float(solvent_row["uncertainty_kcal_mol"]) ** 2)
        repeat_edges.append({
            "edge_id": edge_id, "repeat_index": repeat, "status": "completed",
            "ddg_kcal_mol": value, "uncertainty_kcal_mol": math.sqrt(variance),
            "covariance_group": complex_row.get("covariance_group")
                                or solvent_row.get("covariance_group"),
        })
    by_edge: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in repeat_edges:
        by_edge[row["edge_id"]].append(row)
    aggregated = list(passthrough)
    for edge_id, repeats in sorted(by_edge.items()):
        values = np.asarray([row["ddg_kcal_mol"] for row in repeats])
        variances = np.asarray([row["uncertainty_kcal_mol"] ** 2 for row in repeats])
        weights = 1.0 / variances
        estimate = float(np.sum(values * weights) / np.sum(weights))
        within_variance = float(1.0 / np.sum(weights))
        between_variance = float(np.var(values, ddof=1)) if len(values) > 1 else 0.0
        aggregated.append({
            "edge_id": edge_id, "status": "completed", "ddg_kcal_mol": estimate,
            "uncertainty_kcal_mol": math.sqrt(within_variance + between_variance),
            "repeat_count": len(repeats),
            "covariance_group": repeats[0].get("covariance_group"),
        })
        diagnostics.append({
            "edge_id": edge_id, "status": "aggregated",
            "repeat_count": len(repeats), "repeat_values_kcal_mol": values.tolist(),
            "within_repeat_variance": within_variance,
            "between_repeat_variance": between_variance,
            "effective_sample_size": float((np.sum(weights) ** 2) / np.sum(weights ** 2)),
        })
    return aggregated, diagnostics


__all__ = ["aggregate_rbfe_results", "plan_rbfe_network", "_pair_legs_and_repeats",
           "expand_rbfe_execution_matrix",
           "_plan_with_openfe"]
