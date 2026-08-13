"""Ensemble uncertainty, domain estimation and conditional conformal calibration."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Any, Iterable


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def ensemble_summary(predictions: dict[str, Iterable[float]], *,
                     member_semantics: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """Summarize commensurate model outputs without relabelling spread as uncertainty.

    Every member must declare the same endpoint, canonical output space and unit.
    Sample dispersion between algorithms is useful diagnostics, but it is not a
    calibrated epistemic posterior.
    """
    import numpy as np

    names = sorted(predictions)
    if len(names) < 2:
        raise ValueError("ensemble requires at least two model members")
    if set(member_semantics) != set(names):
        raise ValueError("member_semantics must describe exactly every ensemble member")
    required = {"endpoint_key", "output_space", "unit"}
    normalized = []
    for name in names:
        semantics = member_semantics[name]
        if not required <= set(semantics):
            raise ValueError(f"ensemble member {name} misses prediction semantics")
        normalized.append(tuple(str(semantics[key]) for key in sorted(required)))
    if len(set(normalized)) != 1:
        raise ValueError("ensemble members are not commensurate in endpoint, space and unit")
    matrix = np.asarray([list(predictions[name]) for name in names], dtype=np.float64)
    if matrix.ndim != 2 or len({len(row) for row in matrix}) != 1:
        raise ValueError("ensemble members must have equal prediction counts")
    return [{"mean": float(matrix[:, index].mean()),
             "model_dispersion_std": float(matrix[:, index].std(ddof=1)),
             "dispersion_claim": "algorithmic_model_dispersion_not_epistemic_uncertainty",
             "prediction_semantics": dict(member_semantics[names[0]]),
             "members": {name: float(matrix[row, index])
                         for row, name in enumerate(names)}}
            for index in range(matrix.shape[1])]


def fit_domain(features, *, model_release_ref: dict[str, str], endpoint_key: str,
               representation_release_ref: dict[str, str],
               neighbor_quantile: float = 0.95,
               mahalanobis_quantile: float = 0.99) -> dict[str, Any]:
    """Fit kNN-distance and shrinkage Mahalanobis domain thresholds."""
    import numpy as np
    from sklearn.covariance import LedoitWolf
    from sklearn.neighbors import NearestNeighbors

    matrix = np.asarray(features, dtype=np.float64)
    if matrix.ndim != 2 or len(matrix) < 3:
        raise ValueError("domain estimator requires at least three training rows")
    neighbors = NearestNeighbors(n_neighbors=2, metric="euclidean").fit(matrix)
    distances = neighbors.kneighbors(matrix, return_distance=True)[0][:, 1]
    covariance = LedoitWolf().fit(matrix)
    centered = matrix - covariance.location_
    mahalanobis = np.sqrt(np.einsum(
        "ij,jk,ik->i", centered, covariance.precision_, centered).clip(min=0))
    release = {
        "schema_version": "1.0", "kind": "knn_mahalanobis_domain",
        "scope": {"model_release_ref": dict(model_release_ref),
                  "endpoint_key": endpoint_key,
                  "representation_release_ref": dict(representation_release_ref)},
        "training_features": matrix.astype(float).tolist(),
        "location": covariance.location_.astype(float).tolist(),
        "precision": covariance.precision_.astype(float).tolist(),
        "knn_threshold": float(np.quantile(distances, neighbor_quantile, method="higher")),
        "mahalanobis_threshold": float(np.quantile(
            mahalanobis, mahalanobis_quantile, method="higher")),
        "neighbor_quantile": neighbor_quantile,
        "mahalanobis_quantile": mahalanobis_quantile,
    }
    release["digest"] = _digest(release)
    return release


def assess_domain(release: dict[str, Any], features) -> list[dict[str, Any]]:
    import numpy as np

    material = dict(release)
    expected = material.pop("digest", None)
    if expected != _digest(material):
        raise ValueError("domain release digest mismatch")
    query = np.asarray(features, dtype=np.float64)
    training = np.asarray(release["training_features"], dtype=np.float64)
    location = np.asarray(release["location"], dtype=np.float64)
    precision = np.asarray(release["precision"], dtype=np.float64)
    output = []
    for row in query:
        nearest = float(np.linalg.norm(training - row, axis=1).min())
        delta = row - location
        mahalanobis = float(math.sqrt(max(0.0, delta @ precision @ delta)))
        ratios = (nearest / max(release["knn_threshold"], 1e-12),
                  mahalanobis / max(release["mahalanobis_threshold"], 1e-12))
        worst = max(ratios)
        status = "in_domain" if worst <= 1 else "borderline" if worst <= 1.5 else "out_of_domain"
        output.append({"status": status, "nearest_distance": nearest,
                       "mahalanobis_distance": mahalanobis, "threshold_ratio": worst,
                       "ratio_definition": "max(knn_distance/knn_threshold,mahalanobis_distance/mahalanobis_threshold)",
                       "scope": dict(release["scope"])})
    return output


def fit_conditional_conformal(predictions: Iterable[float], observations: Iterable[float],
                              domains: Iterable[str], *, coverage: float = 0.9,
                              min_group: int = 5) -> dict[str, Any]:
    """Fit finite-sample absolute-residual widths globally and by domain stratum."""
    import numpy as np

    predicted, observed, groups = list(predictions), list(observations), list(domains)
    if not (len(predicted) == len(observed) == len(groups)) or len(predicted) < 2:
        raise ValueError("conditional conformal inputs must have equal length >= 2")
    residuals = [abs(float(y) - float(p)) for p, y in zip(predicted, observed)]

    def quantile(values):
        level = min(1.0, math.ceil((len(values) + 1) * coverage) / len(values))
        return float(np.quantile(values, level, method="higher"))

    global_width = quantile(residuals)
    buckets: dict[str, list[float]] = defaultdict(list)
    for residual, group in zip(residuals, groups):
        buckets[str(group)].append(residual)
    widths = {group: quantile(values) for group, values in sorted(buckets.items())
              if len(values) >= min_group}
    release = {"schema_version": "1.0", "kind": "mondrian_absolute_residual",
               "nominal_coverage": coverage, "global_width": global_width,
               "domain_widths": widths, "counts": {
                   group: len(values) for group, values in sorted(buckets.items())},
               "min_group": min_group}
    release["digest"] = _digest(release)
    return release


__all__ = [
    "assess_domain", "ensemble_summary", "fit_conditional_conformal", "fit_domain",
]
