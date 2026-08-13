"""Dirac Method handlers for Motif structure and physics evidence."""
from __future__ import annotations

import json

import failures
from invocation import HandlerResult, InvocationContext
from motif.docking import dock_vina
from motif.physics import run_openmm_md
from motif.rbfe import aggregate_rbfe_results, plan_rbfe_network
from motif.structure import generate_conformers


def _json(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def conformer_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        report, sdf = generate_conformers(
            payload["smiles"], count=payload.get("count", 50),
            seed=payload.get("seed", 0),
            prune_rms_thresh=payload.get("prune_rms_thresh", .5),
            max_attempts=payload.get("max_attempts", 1000),
            minimize=payload.get("minimize", True))
    except (ValueError, RuntimeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    return HandlerResult(
        result={"ensemble_digest": report["digest"],
                "generated_count": report["generated_count"],
                "cluster_count": report["cluster_count"],
                "force_field": report["force_field"]},
        artifacts=[("structure.conformers_sdf", sdf),
                   ("structure.conformer_report", _json(report))],
        provenance={"algorithm": "RDKit ETKDGv3 + MMFF94s/UFF + Butina",
                    "seed": payload.get("seed", 0)})


def conformer_estimate(payload: dict) -> dict:
    count = int(payload.get("count", 50))
    return {"available": True, "resource_class": "cpu",
            "estimated_seconds": max(.2, count / 20),
            "estimated_peak_memory_bytes": max(256 << 20, count * (2 << 20))}


def vina_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        report, poses = dock_vina(
            payload["receptor_pdbqt"], payload["ligands"],
            center=payload["center"], box_size=payload["box_size"],
            seed=payload.get("seed", 0),
            exhaustiveness=payload.get("exhaustiveness", 16),
            n_poses=payload.get("n_poses", 9),
            energy_range=payload.get("energy_range", 3.0),
            cpu=payload.get("cpu", 1))
    except (ValueError, RuntimeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    return HandlerResult(
        result={"docking_digest": report["digest"],
                "ligand_count": len(report["results"]),
                "results": report["results"]},
        artifacts=[("structure.poses_pdbqt", poses),
                   ("structure.docking_report", _json(report))],
        provenance={"algorithm": "AutoDock Vina", "receptor_pdbqt_sha256":
                    report["receptor_pdbqt_sha256"]},
        warnings=[{"code": "POSE_HYPOTHESIS_ONLY",
                   "message": report["claim_boundary"]}])


def vina_estimate(payload: dict) -> dict:
    count = len(payload.get("ligands", ()))
    exhaustiveness = int(payload.get("exhaustiveness", 16))
    return {"available": True, "resource_class": "cpu",
            "estimated_seconds": max(1, count * exhaustiveness / 2)}


def openmm_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        report, artifacts = run_openmm_md(**payload)
    except (ValueError, RuntimeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    return HandlerResult(
        result={"run_digest": report["digest"], "platform": report["platform"],
                "resumed": report["resumed"],
                "observables": report["observables"]},
        artifacts=[*(artifacts.items()), ("md.run_report", _json(report))],
        provenance={"engine": "OpenMM", "openmm_version":
                    report["openmm_version"], "system_sha256": report["system_sha256"]},
        warnings=[{"code": "SAMPLING_NOT_VALIDATED",
                   "message": report["claim_boundary"]}])


def openmm_estimate(payload: dict) -> dict:
    steps = int(payload.get("steps", 0))
    return {"available": True, "resource_class": "gpu",
            "estimated_seconds": max(.5, steps / 10000),
            "checkpointable": True}


def rbfe_plan_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        network = plan_rbfe_network(
            payload["compounds"],
            extra_edge_fraction=payload.get("extra_edge_fraction", .35),
            minimum_similarity=payload.get("minimum_similarity", .15))
    except (ValueError, RuntimeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    return HandlerResult(
        result={"network_digest": network["digest"],
                "compound_count": len(network["compounds"]),
                "edge_count": len(network["edges"]), "network": network},
        artifacts=[("rbfe.network", _json(network))],
        provenance={"algorithm": "maximum-similarity spanning network + FMCS"},
        warnings=[{"code": "NETWORK_PLAN_ONLY", "message": network["claim_boundary"]}])


def rbfe_aggregate_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        result = aggregate_rbfe_results(payload["network"], payload["observations"])
    except (ValueError, RuntimeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    return HandlerResult(
        result={"result_digest": result["digest"], "status": result["status"],
                "node_estimates": result["node_estimates"],
                "failed_edges": result["failed_edges"],
                "cycle_closure": result["cycle_closure"]},
        artifacts=[("rbfe.result", _json(result))],
        provenance={"algorithm": "weighted graph least squares",
                    "network_digest": result["network_digest"]},
        warnings=([{"code": "PARTIAL_NETWORK", "message": result["reason"]}]
                  if result["status"] == "partial" else []))


def rbfe_estimate(payload: dict) -> dict:
    count = len(payload.get("compounds", payload.get("observations", ())))
    return {"available": True, "resource_class": "cpu",
            "estimated_seconds": max(.1, count / 100)}


__all__ = [name for name in globals() if name.endswith("_handler") or
           name.endswith("_estimate")]
