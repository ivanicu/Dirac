"""InvocationService handlers for the first executable Motif scientific core."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform

import failures
from invocation import HandlerResult, InvocationContext
from motif.acquisition import rank_portfolio
from motif.datasets import create_snapshot
from motif.models import calibrate, predict, train_baselines
from motif.proposals import generator_metrics, local_edits, reaction_enumerate


def snapshot_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        manifest, data = create_snapshot(
            payload["rows"], selection_query=payload["selection_query"],
            endpoint_definitions=payload["endpoint_definitions"],
            split_key=payload.get("split_key", "split"))
    except ValueError as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    ctx.on_progress("snapshot_frozen", 1.0)
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    normalized_rows = json.loads(data)
    split_key = payload.get("split_key", "split")
    split_manifest = {
        "schema_version": "1.0", "split_key": split_key,
        "assignments": [
            {"measurement_id": row["measurement_id"],
             "compound_id": row["compound_id"],
             "split": row[split_key]}
            for row in normalized_rows
        ],
    }
    split_bytes = json.dumps(split_manifest, sort_keys=True, separators=(",", ":")).encode()
    leakage_bytes = json.dumps(
        manifest["leakage"], sort_keys=True, separators=(",", ":")).encode()
    return HandlerResult(
        result={"manifest": manifest},
        artifacts=[("dataset.rows", data), ("dataset.manifest", manifest_bytes),
                   ("dataset.split_manifest", split_bytes),
                   ("dataset.leakage_report", leakage_bytes)],
        provenance={"lineage": "measurement_id+compound_id+protocol_id",
                    "immutable": True},
        parameters_used={"split_key": payload.get("split_key", "split")},
    )


def snapshot_estimate(payload: dict) -> dict:
    rows = len(payload.get("rows", ()))
    return {"available": True, "rows": rows,
            "estimated_seconds": round(max(0.01, rows / 100000), 3),
            "resource_class": "cpu"}


def acquire_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        partitions = rank_portfolio(
            payload["candidates"], objectives=payload["objectives"],
            hard_constraints=payload.get("hard_constraints", ()),
            capacity=payload["capacity"])
    except ValueError as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    artifact = json.dumps(partitions, sort_keys=True, separators=(",", ":")).encode()
    counts = {key: len(value) for key, value in partitions.items()}
    return HandlerResult(
        result={"partitions": partitions, "counts": counts,
                "policy": "deterministic_constrained_pareto_v1"},
        artifacts=[("portfolio.ranking", artifact)],
        provenance={"hidden_total_score": False,
                    "tie_break": ["pareto_rank", "missing_evidence", "failure_risk",
                                  "cost", "proposal_id"]},
        parameters_used={"capacity": payload["capacity"]},
    )


def acquire_estimate(payload: dict) -> dict:
    count = len(payload.get("candidates", ()))
    return {"available": True, "candidates": count,
            "estimated_seconds": round(max(0.01, count * count / 2_000_000), 3),
            "resource_class": "cpu"}


def train_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        checkpoint, report = train_baselines(
            payload["rows"], endpoint_key=payload["endpoint_key"],
            radius=payload.get("radius", 2), n_bits=payload.get("n_bits", 2048),
            ridge_alpha=payload.get("ridge_alpha", 1.0))
    except ValueError as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    checkpoint_bytes = json.dumps(checkpoint, sort_keys=True, separators=(",", ":")).encode()
    report_bytes = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    runtime_lock = {
        "schema_version": "1.0", "kind": "local_python_environment",
        "python": {"implementation": platform.python_implementation(),
                   "version": platform.python_version()},
        "platform": platform.platform(), "method_version": ctx.version,
        "distributions": sorted(
            ({"name": (dist.metadata.get("Name") or "unknown").lower(),
              "version": dist.version}
             for dist in importlib.metadata.distributions()),
            key=lambda item: (item["name"], item["version"])),
        "limitation": "installed versions are frozen; wheel/source archive hashes are unavailable",
    }
    runtime_bytes = json.dumps(
        runtime_lock, sort_keys=True, separators=(",", ":")).encode()
    featurizer_digest = "sha256:" + hashlib.sha256(json.dumps(
        checkpoint["featurizer"], sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return HandlerResult(
        result={"checkpoint_digest": checkpoint["digest"], "validation": report,
                "algorithm": checkpoint["algorithm"],
                "featurizer_digest": featurizer_digest,
                "runtime_lock_digest": "sha256:" + hashlib.sha256(runtime_bytes).hexdigest()},
        artifacts=[("model.checkpoint", checkpoint_bytes),
                   ("model.validation", report_bytes),
                   ("model.runtime_lock", runtime_bytes)],
        provenance={"censored_policy": "retained_not_point_converted",
                    "mandatory_baselines": ["ridge", "nearest_neighbor"]},
        parameters_used={"radius": payload.get("radius", 2),
                         "n_bits": payload.get("n_bits", 2048),
                         "ridge_alpha": payload.get("ridge_alpha", 1.0)},
    )


def train_estimate(payload: dict) -> dict:
    count = len(payload.get("rows", ()))
    return {"available": True, "rows": count,
            "estimated_seconds": round(max(.1, count * count / 500000), 3),
            "resource_class": "cpu"}


def predict_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        rows = predict(payload["checkpoint"], payload["smiles"],
                       calibration=payload.get("calibration"))
    except ValueError as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    artifact = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return HandlerResult(
        result={"predictions": rows, "count": len(rows)},
        artifacts=[("model.predictions", artifact)],
        provenance={"checkpoint_digest": payload["checkpoint"]["digest"],
                    "calibration_digest": (payload.get("calibration") or {}).get("digest")},
    )


def predict_estimate(payload: dict) -> dict:
    count = len(payload.get("smiles", ()))
    return {"available": True, "molecules": count,
            "estimated_seconds": round(max(.01, count / 2000), 3),
            "resource_class": "cpu"}


def calibrate_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        release = calibrate(payload["predictions"], payload["observations"],
                            nominal_coverage=payload.get("nominal_coverage", .9))
    except ValueError as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    artifact = json.dumps(release, sort_keys=True, separators=(",", ":")).encode()
    return HandlerResult(
        result={"calibration": release}, artifacts=[("model.calibration", artifact)],
        provenance={"kind": "finite_sample_split_conformal"})


def proposal_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    common = dict(
        generator_release_id=payload["generator_release_id"],
        strategy_release_id=payload["strategy_release_id"],
        identity_policy_release_id=payload["identity_policy_release_id"],
        root_seed=payload["root_seed"], constraints=payload.get("constraints", {}),
        created_at=payload["created_at"],
        max_proposals=payload.get("max_proposals", 50000))
    try:
        if ctx.method_id == "design.motif.local_edits":
            proposals = local_edits(payload["parents"], transforms=payload["transforms"],
                                    **common)
        elif ctx.method_id == "design.motif.reaction_enumerate":
            proposals = reaction_enumerate(payload["reactants"], templates=payload["templates"],
                                           **common)
        else:
            raise failures.DiracInternal(f"unexpected proposal Method {ctx.method_id}")
    except ValueError as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    metrics = generator_metrics(proposals)
    artifact = json.dumps(proposals, sort_keys=True, separators=(",", ":")).encode()
    trace = json.dumps({"metrics": metrics}, sort_keys=True).encode()
    return HandlerResult(
        result={"proposal_count": len(proposals), "generator_metrics": metrics},
        artifacts=[("design.proposals", artifact), ("design.generator_report", trace)],
        provenance={"naked_smiles": False, "cap": common["max_proposals"]},
        parameters_used={"root_seed": common["root_seed"],
                         "max_proposals": common["max_proposals"]})


def proposal_estimate(payload: dict) -> dict:
    parents = len(payload.get("parents", payload.get("reactants", ())))
    actions = len(payload.get("transforms", payload.get("templates", ())))
    raw = min(payload.get("max_proposals", 50000), parents * max(actions, 1) ** 2)
    return {"available": True, "estimated_raw_products": raw,
            "estimated_seconds": round(max(.05, raw / 500), 3),
            "resource_class": "cpu"}
