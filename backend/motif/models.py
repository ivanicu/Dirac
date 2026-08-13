"""Mandatory transparent molecular predictor baselines.

These are not a substitute for a promoted deep ensemble. They are the simple,
replayable comparators that every later model must beat on exactly the same split.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable


def _rdkit():
    try:
        from rdkit import Chem
        from rdkit.Chem import rdFingerprintGenerator
    except ImportError as exc:
        raise RuntimeError("RDKit is required for the Morgan baseline") from exc
    return Chem, rdFingerprintGenerator


def fingerprint(smiles: str, *, radius: int = 2, n_bits: int = 2048) -> list[int]:
    Chem, generators = _rdkit()
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"cannot parse SMILES {smiles!r}")
    generator = generators.GetMorganGenerator(radius=radius, fpSize=n_bits)
    return sorted(generator.GetFingerprint(molecule).GetOnBits())


def _dense(bitsets: list[list[int]], n_bits: int):
    import numpy as np
    matrix = np.zeros((len(bitsets), n_bits), dtype=np.float64)
    for row, bits in enumerate(bitsets):
        matrix[row, bits] = 1.0
    return matrix


def tanimoto(left: list[int], right: list[int]) -> float:
    a, b = set(left), set(right)
    union = len(a | b)
    return len(a & b) / union if union else 1.0


def train_baselines(rows: Iterable[dict[str, Any]], *, endpoint_key: str,
                    radius: int = 2, n_bits: int = 2048,
                    ridge_alpha: float = 1.0) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit dual-form ridge and retain a 1-NN comparator.

    Bounded/censored observations are retained in counts and never converted to
    points. The first baseline release trains only on explicit equality labels.
    """
    import numpy as np
    source = [dict(row) for row in rows]
    exact = [row for row in source if row.get("qualifier", "equal") == "equal"
             and row.get("value") is not None]
    censored = [row for row in source if row.get("qualifier", "equal") != "equal"]
    train = [row for row in exact if row.get("split", "train") == "train"]
    test = [row for row in exact if row.get("split", "train") != "train"]
    if len(train) < 2:
        raise ValueError("at least two exact training labels are required")
    train_bits = [fingerprint(row["smiles"], radius=radius, n_bits=n_bits) for row in train]
    x = _dense(train_bits, n_bits)
    y = np.asarray([float(row["value"]) for row in train], dtype=np.float64)
    intercept = float(y.mean())
    centered = y - intercept
    # Dual ridge makes runtime depend on compounds, not the 2048 feature width.
    dual = np.linalg.solve(x @ x.T + ridge_alpha * np.eye(len(train)), centered)
    weights = x.T @ dual
    checkpoint = {
        "schema_version": "1.0", "algorithm": "morgan_ridge_and_1nn",
        "endpoint_key": endpoint_key,
        "featurizer": {"kind": "morgan", "radius": radius, "n_bits": n_bits,
                       "use_chirality": False},
        "ridge": {"alpha": ridge_alpha, "intercept": intercept,
                  "weights": weights.tolist()},
        "nearest_neighbor": {
            "fingerprints": train_bits,
            "values": y.tolist(),
            "compound_ids": [row.get("compound_id") for row in train],
        },
        "training": {"exact_count": len(train), "censored_excluded_count": len(censored),
                     "censored_policy": "retained_not_point_converted"},
    }
    checkpoint["digest"] = _digest(checkpoint)
    predictions = predict(checkpoint, [row["smiles"] for row in test]) if test else []
    report = validation_report(
        predictions, [float(row["value"]) for row in test],
        split_names=[str(row.get("split")) for row in test])
    report.update({"train_count": len(train), "test_count": len(test),
                   "censored_excluded_count": len(censored),
                   "simple_baselines": ["ridge", "nearest_neighbor"]})
    return checkpoint, report


def predict(checkpoint: dict[str, Any], smiles: Iterable[str],
            *, calibration: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    import numpy as np
    expected = checkpoint.get("digest")
    material = dict(checkpoint)
    material.pop("digest", None)
    if expected != _digest(material):
        raise ValueError("checkpoint digest mismatch")
    feature = checkpoint["featurizer"]
    rows = []
    weights = np.asarray(checkpoint["ridge"]["weights"], dtype=np.float64)
    train_bits = checkpoint["nearest_neighbor"]["fingerprints"]
    train_values = checkpoint["nearest_neighbor"]["values"]
    for value in smiles:
        bits = fingerprint(value, radius=feature["radius"], n_bits=feature["n_bits"])
        ridge = float(checkpoint["ridge"]["intercept"] + weights[bits].sum())
        similarities = [tanimoto(bits, known) for known in train_bits]
        nearest_index = max(range(len(similarities)), key=lambda index: similarities[index])
        similarity = float(similarities[nearest_index])
        domain = "in_domain" if similarity >= 0.6 else "borderline" if similarity >= 0.35 else "out_of_domain"
        item = {
            "smiles": value, "ridge": ridge,
            "nearest_neighbor": float(train_values[nearest_index]),
            "nearest_similarity": similarity, "applicability_domain": domain,
        }
        if calibration:
            width = float(calibration["absolute_residual_quantile"])
            item["interval"] = {"lower": ridge - width, "upper": ridge + width,
                                "nominal_coverage": calibration["nominal_coverage"]}
        rows.append(item)
    return rows


def calibrate(predictions: Iterable[float], observations: Iterable[float], *,
              nominal_coverage: float = 0.9) -> dict[str, Any]:
    import numpy as np
    predicted = np.asarray(list(predictions), dtype=np.float64)
    observed = np.asarray(list(observations), dtype=np.float64)
    if predicted.shape != observed.shape or predicted.size < 2:
        raise ValueError("calibration requires matching prediction/observation arrays")
    if not 0.5 <= nominal_coverage < 1:
        raise ValueError("nominal_coverage must be in [0.5, 1)")
    residuals = np.abs(observed - predicted)
    # Finite-sample split-conformal correction, conservative 'higher' quantile.
    level = min(1.0, math.ceil((len(residuals) + 1) * nominal_coverage) / len(residuals))
    width = float(np.quantile(residuals, level, method="higher"))
    result = {"schema_version": "1.0", "kind": "split_conformal_absolute_residual",
              "nominal_coverage": nominal_coverage,
              "absolute_residual_quantile": width, "sample_count": len(residuals)}
    result["digest"] = _digest(result)
    return result


def validation_report(predictions: list[dict[str, Any]], observations: list[float],
                      *, split_names: list[str]) -> dict[str, Any]:
    if not observations:
        return {"metrics": {}, "by_split": {}, "warning": "no held-out exact labels"}
    by_split: dict[str, dict[str, float]] = {}
    for split in sorted(set(split_names)):
        indices = [index for index, value in enumerate(split_names) if value == split]
        by_split[split] = _metrics([predictions[index]["ridge"] for index in indices],
                                   [observations[index] for index in indices])
    return {"metrics": _metrics([row["ridge"] for row in predictions], observations),
            "nearest_neighbor_metrics": _metrics(
                [row["nearest_neighbor"] for row in predictions], observations),
            "by_split": by_split,
            "domain_counts": _counts(row["applicability_domain"] for row in predictions)}


def _metrics(predictions: list[float], observations: list[float]) -> dict[str, float]:
    import numpy as np
    predicted = np.asarray(predictions)
    observed = np.asarray(observations)
    error = predicted - observed
    return {"mae": float(np.abs(error).mean()),
            "rmse": float(np.sqrt(np.square(error).mean())),
            "count": int(len(observations))}


def _counts(values: Iterable[str]) -> dict[str, int]:
    output: dict[str, int] = {}
    for value in values:
        output[value] = output.get(value, 0) + 1
    return dict(sorted(output.items()))


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
