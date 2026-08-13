"""End-to-end Motif predictor mesh: simple baselines, trees, censored head and D-MPNN."""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any, Iterable

from motif.chemprop_adapter import predict_chemprop_ensemble, train_chemprop_ensemble
from motif.features import fit_feature_release, transform
from motif.labels import fit_censored_tobit, fit_pairwise_ranker, predict_censored_tobit
from motif.models import predict as predict_linear
from motif.models import train_baselines
from motif.tree_models import predict_tree_models, train_tree_models
from motif.uncertainty import (assess_domain, ensemble_summary,
                               fit_conditional_conformal, fit_domain)
from motif.validation import specification_curve


_SOURCE_ROOT = pathlib.Path(__file__).resolve().parent
PREDICTOR_SOURCE_DIGESTS = {
    name: hashlib.sha256((_SOURCE_ROOT / name).read_bytes()).hexdigest()
    for name in ("chemprop_adapter.py", "features.py", "labels.py", "models.py",
                 "tree_models.py", "uncertainty.py", "validation.py", "methods.py")
}


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _exact(rows, *, splits):
    return [row for row in rows if row.get("split", "train") in splits
            and row.get("qualifier", "equal") == "equal" and row.get("value") is not None]


def train_predictor_mesh(rows: Iterable[dict[str, Any]], *, endpoint_key: str,
                         radius: int = 2, n_bits: int = 2048, seed: int = 0,
                         include_chemprop: bool = True, chemprop_ensemble_size: int = 5,
                         chemprop_epochs: int = 30, accelerator: str = "auto",
                         bootstrap_samples: int = 500) -> tuple[dict[str, Any], dict[str, Any]]:
    source = [dict(row) for row in rows if row.get("endpoint_key") == endpoint_key]
    train_exact = _exact(source, splits={"train"})
    if len(train_exact) < 3:
        raise ValueError("predictor mesh requires at least three exact train labels")
    feature_release = fit_feature_release(
        [row["smiles"] for row in train_exact], radius=radius, n_bits=n_bits,
        use_chirality=True)
    train_features, _ = transform([row["smiles"] for row in train_exact], feature_release)
    linear, _ = train_baselines(source, endpoint_key=endpoint_key, radius=radius,
                                n_bits=n_bits, ridge_alpha=1.0)
    trees = train_tree_models(train_features, [row["value"] for row in train_exact], seed=seed)
    train_all = [row for row in source if row.get("split", "train") == "train"]
    all_features, _ = transform([row["smiles"] for row in train_all], feature_release)
    descriptor_start = feature_release["morgan"]["n_bits"]
    tobit = fit_censored_tobit(all_features[:, descriptor_start:], train_all, l2=1.0)
    ranker = fit_pairwise_ranker(train_features, [row["value"] for row in train_exact], l2=1.0)
    # Fit covariance on the compact continuous descriptor tail. A dense
    # covariance over 2,048 sparse fingerprint bits is unstable for ordinary
    # medicinal-chemistry dataset sizes and creates enormous checkpoints.
    descriptor_count = len(feature_release["descriptors"]["names"])
    domain = fit_domain(train_features[:, -descriptor_count:])
    chemprop = None
    chemprop_validation = {"available": False}
    if include_chemprop:
        chemprop, chemprop_validation = train_chemprop_ensemble(
            source, endpoint_keys=[endpoint_key], ensemble_size=chemprop_ensemble_size,
            epochs=chemprop_epochs, seed=seed, accelerator=accelerator)
    checkpoint = {
        "schema_version": "1.0", "algorithm": "motif_predictor_mesh",
        "endpoint_key": endpoint_key, "seed": seed,
        "feature_release": feature_release, "linear_baselines": linear,
        "tree_baselines": trees, "censored_tobit": tobit,
        "censored_feature_slice": [descriptor_start, feature_release["feature_count"]],
        "pairwise_ranker": ranker, "domain_release": domain,
        "chemprop": chemprop, "calibration": None,
        "members": ["ridge", "nearest_neighbor", "random_forest", "xgboost",
                    "censored_tobit", *( ["chemprop_dpmpnn"] if chemprop else [])],
    }
    checkpoint["digest"] = _digest(checkpoint)
    calibration_rows = _exact(source, splits={"calibration"})
    if len(calibration_rows) >= 2:
        predicted = predict_predictor_mesh(
            checkpoint, [row["smiles"] for row in calibration_rows], accelerator=accelerator)
        checkpoint["calibration"] = fit_conditional_conformal(
            [row["ensemble"]["mean"] for row in predicted],
            [float(row["value"]) for row in calibration_rows],
            [row["applicability_domain"]["status"] for row in predicted], min_group=5)
        checkpoint.pop("digest")
        checkpoint["digest"] = _digest(checkpoint)
    heldout = _exact(source, splits={"validation", "test", "external"})
    records = []
    if heldout:
        predicted = predict_predictor_mesh(
            checkpoint, [row["smiles"] for row in heldout], accelerator=accelerator)
        for source_row, prediction in zip(heldout, predicted):
            for model, value in prediction["models"].items():
                records.append({"model": model, "endpoint": endpoint_key,
                                "split": source_row["split"], "prediction": value,
                                "observation": float(source_row["value"])})
            ensemble = prediction["ensemble"]
            record = {"model": "ensemble", "endpoint": endpoint_key,
                      "split": source_row["split"], "prediction": ensemble["mean"],
                      "observation": float(source_row["value"])}
            if prediction.get("interval"):
                record.update(prediction["interval"])
            records.append(record)
    validation = (specification_curve(records, bootstrap_samples=bootstrap_samples, seed=seed)
                  if records else {"schema_version": "1.0", "cell_count": 0, "cells": [],
                                   "warning": "no held-out exact labels"})
    validation["chemprop"] = chemprop_validation
    validation["mandatory_members"] = checkpoint["members"]
    validation["digest"] = _digest({key: value for key, value in validation.items()
                                     if key != "digest"})
    return checkpoint, validation


def predict_predictor_mesh(checkpoint: dict[str, Any], smiles: Iterable[str], *,
                           accelerator: str = "auto") -> list[dict[str, Any]]:
    material = dict(checkpoint)
    expected = material.pop("digest", None)
    if expected != _digest(material):
        raise ValueError("predictor mesh checkpoint digest mismatch")
    values = list(smiles)
    features, canonical = transform(values, checkpoint["feature_release"])
    linear = predict_linear(checkpoint["linear_baselines"], values)
    trees = predict_tree_models(checkpoint["tree_baselines"], features)
    start, stop = checkpoint["censored_feature_slice"]
    tobit = predict_censored_tobit(checkpoint["censored_tobit"], features[:, start:stop])
    descriptor_count = len(checkpoint["feature_release"]["descriptors"]["names"])
    domain = assess_domain(
        checkpoint["domain_release"], features[:, -descriptor_count:])
    chemprop = (predict_chemprop_ensemble(checkpoint["chemprop"], values,
                                          accelerator=accelerator)
                if checkpoint.get("chemprop") else None)
    output = []
    for index, value in enumerate(values):
        members = {
            "ridge": linear[index]["ridge"],
            "nearest_neighbor": linear[index]["nearest_neighbor"],
            "random_forest": trees["random_forest"][index],
            "xgboost": trees["xgboost"][index],
            "censored_tobit": tobit[index]["mean"],
        }
        if chemprop:
            members["chemprop_dpmpnn"] = chemprop[index]["endpoints"][
                checkpoint["endpoint_key"]]["mean"]
        ensemble = ensemble_summary({key: [number] for key, number in members.items()})[0]
        item = {"smiles": value, "canonical_smiles": canonical[index],
                "endpoint_key": checkpoint["endpoint_key"], "models": members,
                "ensemble": ensemble, "applicability_domain": domain[index]}
        calibration = checkpoint.get("calibration")
        if calibration:
            status = domain[index]["status"]
            width = calibration["domain_widths"].get(status, calibration["global_width"])
            item["interval"] = {"lower": ensemble["mean"] - width,
                                "upper": ensemble["mean"] + width,
                                "nominal_coverage": calibration["nominal_coverage"]}
        output.append(item)
    return output


__all__ = ["predict_predictor_mesh", "train_predictor_mesh"]
