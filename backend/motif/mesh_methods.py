"""Dirac Method handlers for the advanced Motif predictor and acquisition mesh."""
from __future__ import annotations

import hashlib
import json

import failures
from invocation import HandlerResult, InvocationContext
from motif.advanced_acquisition import (botorch_qehvi, information_value,
                                        selection_sensitivity)
from motif.acquisition import rank_portfolio
from motif.mesh import predict_predictor_mesh, train_predictor_mesh
from motif.methods import _runtime_manifest


def train_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        checkpoint, validation = train_predictor_mesh(
            payload["rows"], endpoint_key=payload["endpoint_key"],
            radius=payload.get("radius", 2), n_bits=payload.get("n_bits", 2048),
            seed=payload.get("seed", 0),
            include_chemprop=payload.get("include_chemprop", True),
            chemprop_ensemble_size=payload.get("chemprop_ensemble_size", 5),
            chemprop_epochs=payload.get("chemprop_epochs", 30),
            accelerator=payload.get("accelerator", "auto"),
            bootstrap_samples=payload.get("bootstrap_samples", 500))
    except (ValueError, RuntimeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    checkpoint_bytes = json.dumps(
        checkpoint, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    validation_bytes = json.dumps(
        validation, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    runtime = _runtime_manifest(ctx)
    runtime_bytes = json.dumps(
        runtime, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    runtime_digest = "sha256:" + hashlib.sha256(runtime_bytes).hexdigest()
    return HandlerResult(
        result={"checkpoint_digest": checkpoint["digest"], "validation": validation,
                "algorithm": checkpoint["algorithm"],
                "members": checkpoint["members"],
                "featurizer_digest": checkpoint["feature_release"]["digest"],
                "runtime_lock_digest": runtime_digest},
        artifacts=[("model.checkpoint", checkpoint_bytes),
                   ("model.validation", validation_bytes),
                   ("model.runtime_lock", runtime_bytes)],
        provenance={"kind": "motif_predictor_mesh", "seed": payload.get("seed", 0),
                    "censored_bounds_as_points": False,
                    "mandatory_simple_baselines": True},
        parameters_used={key: payload.get(key) for key in (
            "radius", "n_bits", "seed", "include_chemprop", "chemprop_ensemble_size",
            "chemprop_epochs", "bootstrap_samples")})


def train_estimate(payload: dict) -> dict:
    rows = len(payload.get("rows", ()))
    ensemble = payload.get("chemprop_ensemble_size", 5)
    epochs = payload.get("chemprop_epochs", 30)
    deep = payload.get("include_chemprop", True)
    return {"available": True,
            "estimated_seconds": round(max(2.0, rows * ensemble * epochs / 1200), 3),
            "estimated_peak_memory_bytes": max(1 << 30, rows * 4096),
            "resource_class": "gpu" if deep else "cpu",
            "ensemble_members": ensemble if deep else 0}


def predict_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        predictions = predict_predictor_mesh(
            payload["checkpoint"], payload["smiles"],
            accelerator=payload.get("accelerator", "auto"))
    except (ValueError, RuntimeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    artifact = json.dumps(
        predictions, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return HandlerResult(
        result={"predictions": predictions, "count": len(predictions)},
        artifacts=[("model.predictions", artifact)],
        provenance={"checkpoint_digest": payload["checkpoint"]["digest"],
                    "ensemble": True, "applicability_domain": True})


def predict_estimate(payload: dict) -> dict:
    count = len(payload.get("smiles", ()))
    deep = bool(payload.get("checkpoint", {}).get("chemprop"))
    return {"available": True, "estimated_seconds": round(max(.1, count / 100), 3),
            "resource_class": "gpu" if deep else "cpu"}


def acquire_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        release = botorch_qehvi(
            payload["train_features"], payload["train_objectives"],
            [row["features"] for row in payload["candidates"]],
            reference_point=payload["reference_point"],
            mc_samples=payload.get("mc_samples", 128), seed=payload.get("seed", 0))
        voi = information_value(
            release["posterior_variance"],
            [float(row.get("components", {}).get("cost", 1.0))
             for row in payload["candidates"]])
        candidates = []
        for index, source in enumerate(payload["candidates"]):
            row = {key: value for key, value in source.items() if key != "features"}
            components = dict(row.get("components", {}))
            components.update({"pareto_improvement": release["scores"][index],
                               "information_value": voi[index]})
            row["components"] = components
            candidates.append(row)
        portfolio = rank_portfolio(
            candidates, objectives=payload["objectives"],
            hard_constraints=payload.get("hard_constraints", ()),
            capacity=payload["capacity"])
        sensitivity = selection_sensitivity(
            candidates, objectives=payload["objectives"],
            hard_constraints=payload.get("hard_constraints", ()),
            capacity=payload["capacity"])
    except (ValueError, RuntimeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    report = {"acquisition": release, "sensitivity": sensitivity}
    return HandlerResult(
        result={"portfolio": portfolio, "counts": {
            key: len(value) for key, value in portfolio.items()},
            "acquisition_digest": release["digest"],
            "sensitivity_digest": sensitivity["digest"]},
        artifacts=[
            ("portfolio.ranking", json.dumps(
                portfolio, sort_keys=True, separators=(",", ":")).encode()),
            ("portfolio.acquisition_report", json.dumps(
                report, sort_keys=True, separators=(",", ":")).encode()),
        ],
        provenance={"policy": "botorch_qlogehvi_plus_exact_constrained_pareto",
                    "hidden_total_score": False})


def acquire_estimate(payload: dict) -> dict:
    count = len(payload.get("candidates", ()))
    return {"available": True, "estimated_seconds": round(max(.5, count / 50), 3),
            "resource_class": "cpu"}


__all__ = [
    "acquire_estimate", "acquire_handler", "predict_estimate", "predict_handler",
    "train_estimate", "train_handler",
]
