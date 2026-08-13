"""Model × endpoint × split specification curves with deterministic bootstrap CIs."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Iterable

from motif.features import canonical_smiles


_SPLIT_ORDER = {name: index for index, name in enumerate(
    ("train", "calibration", "validation", "test", "external"))}


def prepare_training_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Canonicalize semantic row order and reject cross-split leakage.

    Method payload array order is transport noise, not model identity. Compound,
    canonical structure and optional medicinal-chemistry series are all leakage
    keys; any key shared by train and a held-out split invalidates the release
    before feature fitting or GPU allocation.
    """
    source = []
    for value in rows:
        row = dict(value)
        row["smiles"] = canonical_smiles(str(row["smiles"]))
        source.append(row)

    train_keys: dict[tuple[str, str], set[str]] = defaultdict(set)
    heldout_keys: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in source:
        split = str(row.get("split", "train"))
        target = train_keys if split == "train" else heldout_keys
        keys = {
            ("compound_id", str(row.get("compound_id", ""))),
            ("canonical_smiles", row["smiles"]),
        }
        if row.get("series_id") not in (None, ""):
            keys.add(("series_id", str(row["series_id"])))
        for key in keys:
            if key[1]:
                target[key].add(split)
    collisions = sorted(set(train_keys) & set(heldout_keys))
    if collisions:
        details = [
            {"kind": kind, "value": value,
             "heldout_splits": sorted(heldout_keys[(kind, value)])}
            for kind, value in collisions[:20]
        ]
        raise ValueError(f"cross-split leakage detected: {details}")

    return sorted(source, key=lambda row: (
        _SPLIT_ORDER.get(str(row.get("split", "train")), len(_SPLIT_ORDER)),
        str(row.get("measurement_id", "")),
        str(row.get("compound_id", "")),
        row["smiles"],
        str(row.get("qualifier", "equal")),
        json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False),
    ))


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def regression_metrics(predicted: Iterable[float], observed: Iterable[float]) -> dict[str, float]:
    import numpy as np
    from scipy.stats import spearmanr

    prediction = np.asarray(list(predicted), dtype=np.float64)
    truth = np.asarray(list(observed), dtype=np.float64)
    if prediction.shape != truth.shape or not len(truth):
        raise ValueError("metrics require non-empty matching vectors")
    error = prediction - truth
    total = float(np.square(truth - truth.mean()).sum())
    rho = (float(spearmanr(prediction, truth).statistic)
           if len(truth) > 1 and np.ptp(prediction) > 0 and np.ptp(truth) > 0 else 0.0)
    if not np.isfinite(rho):
        rho = 0.0
    return {
        "mae": float(np.abs(error).mean()),
        "rmse": float(np.sqrt(np.square(error).mean())),
        "r2": float(1.0 - np.square(error).sum() / total) if total > 0 else 0.0,
        "spearman": rho, "count": int(len(truth)),
    }


def interval_metrics(lower: Iterable[float], upper: Iterable[float],
                     observed: Iterable[float], *, nominal_coverage: float) -> dict[str, float]:
    import numpy as np

    lo, hi, truth = (np.asarray(list(value), dtype=np.float64)
                     for value in (lower, upper, observed))
    if not (lo.shape == hi.shape == truth.shape) or not len(truth):
        raise ValueError("interval metrics require matching non-empty vectors")
    covered = (lo <= truth) & (truth <= hi)
    empirical = float(covered.mean())
    return {"coverage": empirical, "coverage_error": empirical - nominal_coverage,
            "mean_width": float((hi - lo).mean()), "count": int(len(truth))}


def bootstrap_ci(predicted: Iterable[float], observed: Iterable[float], *,
                 metric: str, samples: int = 1000, seed: int = 0,
                 confidence: float = 0.95) -> dict[str, float]:
    import numpy as np

    prediction = np.asarray(list(predicted), dtype=np.float64)
    truth = np.asarray(list(observed), dtype=np.float64)
    if len(truth) < 2:
        raise ValueError("bootstrap CI requires at least two observations")
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(samples):
        indices = rng.integers(0, len(truth), size=len(truth))
        values.append(regression_metrics(prediction[indices], truth[indices])[metric])
    alpha = (1.0 - confidence) / 2.0
    return {"estimate": regression_metrics(prediction, truth)[metric],
            "lower": float(np.quantile(values, alpha)),
            "upper": float(np.quantile(values, 1.0 - alpha)),
            "confidence": confidence, "bootstrap_samples": samples, "seed": seed}


def specification_curve(records: Iterable[dict[str, Any]], *, bootstrap_samples: int = 500,
                        seed: int = 0) -> dict[str, Any]:
    """Produce every observed model × endpoint × split cell; no flattering-cell selection."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        for required in ("model", "endpoint", "split", "prediction", "observation"):
            if required not in row:
                raise ValueError(f"validation record misses {required}")
        groups[(str(row["model"]), str(row["endpoint"]), str(row["split"]))].append(row)
    cells = []
    for index, ((model, endpoint, split), rows) in enumerate(sorted(groups.items())):
        predicted = [float(row["prediction"]) for row in rows]
        observed = [float(row["observation"]) for row in rows]
        metrics = regression_metrics(predicted, observed)
        intervals = None
        if all(row.get("lower") is not None and row.get("upper") is not None for row in rows):
            intervals = interval_metrics(
                [row["lower"] for row in rows], [row["upper"] for row in rows], observed,
                nominal_coverage=float(rows[0].get("nominal_coverage", 0.9)))
        cis = {}
        if len(rows) >= 2:
            for offset, metric in enumerate(("mae", "rmse", "r2", "spearman")):
                cis[metric] = bootstrap_ci(predicted, observed, metric=metric,
                                           samples=bootstrap_samples,
                                           seed=seed + index * 17 + offset)
        cells.append({"model": model, "endpoint": endpoint, "split": split,
                      "metrics": metrics, "interval_metrics": intervals,
                      "bootstrap_ci": cis})
    report = {"schema_version": "1.0", "kind": "validation_specification_curve",
              "cell_count": len(cells), "cells": cells,
              "policy": "all_model_endpoint_split_cells_reported",
              "bootstrap_samples": bootstrap_samples, "seed": seed}
    report["digest"] = _digest(report)
    return report


__all__ = [
    "bootstrap_ci", "interval_metrics", "regression_metrics", "specification_curve",
    "prepare_training_rows",
]
