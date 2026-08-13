"""Endpoint-aware exact, censored, ordinal and ranking label semantics."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def interval_from_row(row: dict[str, Any]) -> tuple[float, float] | None:
    """Map qualifiers to mathematical intervals without bound-to-point coercion."""
    qualifier = row.get("qualifier", "equal")
    value = row.get("value")
    if qualifier in {"not_tested", "invalid"}:
        return None
    if qualifier == "interval":
        lower, upper = row.get("lower"), row.get("upper")
        if lower is None or upper is None or float(lower) > float(upper):
            raise ValueError("interval label requires ordered lower and upper bounds")
        return float(lower), float(upper)
    if value is None:
        raise ValueError(f"{qualifier} label requires value")
    point = float(value)
    if qualifier == "equal":
        return point, point
    if qualifier in {"less_than", "less_or_equal"}:
        return -math.inf, point
    if qualifier in {"greater_than", "greater_or_equal"}:
        return point, math.inf
    raise ValueError(f"unsupported qualifier {qualifier!r}")


def fit_censored_tobit(features, rows: Iterable[dict[str, Any]], *,
                       l2: float = 1.0, max_iter: int = 500) -> dict[str, Any]:
    """Fit a Gaussian Tobit linear head using every exact and censored observation."""
    import numpy as np
    from scipy.optimize import minimize
    from scipy.special import log_ndtr

    source = list(rows)
    intervals = [interval_from_row(row) for row in source]
    keep = [index for index, value in enumerate(intervals) if value is not None]
    if len(keep) < 2:
        raise ValueError("censored Tobit head requires at least two usable labels")
    x = np.asarray(features, dtype=np.float64)[keep]
    bounds = [intervals[index] for index in keep]
    exact = [index for index, (lower, upper) in enumerate(bounds) if lower == upper]
    initial_mean = float(np.mean([bounds[index][0] for index in exact])) if exact else 0.0
    initial = np.zeros(x.shape[1] + 2, dtype=np.float64)
    initial[-2] = initial_mean
    initial[-1] = 0.0

    def objective(parameters):
        weights, intercept, log_sigma = parameters[:-2], parameters[-2], parameters[-1]
        sigma = np.exp(log_sigma) + 1e-8
        means = x @ weights + intercept
        loss = 0.5 * l2 * float(weights @ weights)
        for mean, (lower, upper) in zip(means, bounds):
            if lower == upper:
                z = (lower - mean) / sigma
                loss += 0.5 * z * z + log_sigma + 0.5 * math.log(2 * math.pi)
            elif math.isinf(lower):
                loss -= float(log_ndtr((upper - mean) / sigma))
            elif math.isinf(upper):
                loss -= float(log_ndtr((mean - lower) / sigma))
            else:
                log_hi = float(log_ndtr((upper - mean) / sigma))
                log_lo = float(log_ndtr((lower - mean) / sigma))
                if log_lo >= log_hi:
                    return 1e100
                loss -= log_hi + math.log1p(-math.exp(log_lo - log_hi))
        return loss

    fitted = minimize(objective, initial, method="L-BFGS-B",
                      options={"maxiter": max_iter, "ftol": 1e-10})
    if not fitted.success:
        raise ValueError(f"censored Tobit optimization failed: {fitted.message}")
    model = {
        "schema_version": "1.0", "kind": "gaussian_tobit_linear",
        "weights": fitted.x[:-2].tolist(), "intercept": float(fitted.x[-2]),
        "sigma": float(math.exp(fitted.x[-1])), "l2": l2,
        "fit_count": len(keep), "exact_count": len(exact),
        "censored_count": len(keep) - len(exact),
        "excluded_count": len(source) - len(keep),
        "optimization": {"iterations": int(fitted.nit), "objective": float(fitted.fun)},
    }
    model["digest"] = _digest(model)
    return model


def predict_censored_tobit(model: dict[str, Any], features) -> list[dict[str, float]]:
    import numpy as np

    material = dict(model)
    expected = material.pop("digest", None)
    if expected != _digest(material):
        raise ValueError("censored model digest mismatch")
    matrix = np.asarray(features, dtype=np.float64)
    mean = matrix @ np.asarray(model["weights"]) + model["intercept"]
    sigma = float(model["sigma"])
    return [{"mean": float(value), "aleatoric_std": sigma} for value in mean]


def fit_pairwise_ranker(features, values: Iterable[float], *, l2: float = 1.0,
                        tie_tolerance: float = 0.0) -> dict[str, Any]:
    """Fit a deterministic pairwise ridge ranker from within-endpoint comparisons."""
    import numpy as np

    matrix = np.asarray(features, dtype=np.float64)
    labels = np.asarray(list(values), dtype=np.float64)
    differences, targets = [], []
    for left in range(len(labels)):
        for right in range(left + 1, len(labels)):
            delta = labels[left] - labels[right]
            if abs(delta) <= tie_tolerance:
                continue
            differences.append(matrix[left] - matrix[right])
            targets.append(1.0 if delta > 0 else -1.0)
    if not differences:
        raise ValueError("ranking head requires at least one non-tied pair")
    design = np.asarray(differences)
    # Dual form keeps the solve proportional to observed pairs rather than the
    # thousands-wide molecular feature vector.
    dual = np.linalg.solve(design @ design.T + l2 * np.eye(len(design)),
                           np.asarray(targets))
    weights = design.T @ dual
    model = {"schema_version": "1.0", "kind": "pairwise_ridge_ranker",
             "weights": weights.tolist(), "l2": l2, "pair_count": len(targets)}
    model["digest"] = _digest(model)
    return model


__all__ = [
    "fit_censored_tobit", "fit_pairwise_ranker", "interval_from_row",
    "predict_censored_tobit",
]
