"""Auditable Bayesian multi-objective acquisition and portfolio sensitivity."""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any, Iterable

from motif.acquisition import rank_portfolio


_SOURCE_ROOT = pathlib.Path(__file__).resolve().parent
ACQUISITION_SOURCE_DIGESTS = {
    name: hashlib.sha256((_SOURCE_ROOT / name).read_bytes()).hexdigest()
    for name in ("acquisition.py", "advanced_acquisition.py")
}


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _validate_posterior_contract(contract: dict[str, Any], *,
                                 observations: int, objectives: int) -> None:
    required = {
        "model_release_ref", "validation_evidence_ref", "lifecycle",
        "posterior_kind", "objective_semantics", "likelihoods",
        "candidate_domain", "pending_conditioning", "minimum_observations",
    }
    missing = required - set(contract)
    if missing:
        raise ValueError(f"posterior contract misses {sorted(missing)}")
    if contract["lifecycle"] not in {"validated_release", "promoted_release"}:
        raise ValueError("Bayesian acquisition requires a validated model release")
    if contract["posterior_kind"] != "exact_gp_independent_outputs":
        raise ValueError("unsupported posterior_kind for this acquisition implementation")
    if contract["candidate_domain"] != "finite_discrete_set":
        raise ValueError("qLogEHVI is restricted to the submitted discrete candidate set")
    if contract["pending_conditioning"] not in {"none", "explicitly_conditioned"}:
        raise ValueError("pending-point conditioning must be explicit")
    if observations < int(contract["minimum_observations"]):
        raise ValueError("posterior contract minimum observation count is not met")
    if len(contract["objective_semantics"]) != objectives:
        raise ValueError("posterior objective semantics do not match objective matrix")
    if len(contract["likelihoods"]) != objectives:
        raise ValueError("posterior likelihoods do not match objective matrix")


def botorch_qehvi(train_features, train_objectives, candidate_features, *,
                  posterior_contract: dict[str, Any],
                  reference_point: Iterable[float], mc_samples: int = 128,
                  seed: int = 0) -> dict[str, Any]:
    """Fit an exact multitask GP and score each candidate by q=1 noisy EHVI.

    Objective columns must already be oriented so larger is better. Hard constraints
    are intentionally outside this function and remain exact Dirac gates.
    """
    import torch
    from botorch.acquisition.multi_objective import qLogExpectedHypervolumeImprovement
    from botorch.fit import fit_gpytorch_mll
    from botorch.models import SingleTaskGP
    from botorch.sampling.normal import SobolQMCNormalSampler
    from botorch.utils.multi_objective.box_decompositions.non_dominated import (
        NondominatedPartitioning,
    )
    from gpytorch.mlls import ExactMarginalLogLikelihood

    torch.manual_seed(seed)
    x = torch.as_tensor(train_features, dtype=torch.double)
    y = torch.as_tensor(train_objectives, dtype=torch.double)
    candidates = torch.as_tensor(candidate_features, dtype=torch.double)
    ref = torch.as_tensor(list(reference_point), dtype=torch.double)
    if x.ndim != 2 or y.ndim != 2 or candidates.ndim != 2:
        raise ValueError("qEHVI requires 2D train/candidate matrices")
    if len(x) != len(y) or x.shape[1] != candidates.shape[1] or y.shape[1] != len(ref):
        raise ValueError("qEHVI matrix dimensions are inconsistent")
    if len(x) < 3 or y.shape[1] < 2:
        raise ValueError("qEHVI requires >=3 observations and >=2 objectives")
    _validate_posterior_contract(
        posterior_contract, observations=len(x), objectives=y.shape[1])
    model = SingleTaskGP(x, y)
    fit_gpytorch_mll(ExactMarginalLogLikelihood(model.likelihood, model))
    partitioning = NondominatedPartitioning(ref_point=ref, Y=y)
    sampler = SobolQMCNormalSampler(sample_shape=torch.Size([mc_samples]), seed=seed)
    acquisition = qLogExpectedHypervolumeImprovement(
        model=model, ref_point=ref.tolist(), partitioning=partitioning, sampler=sampler)
    with torch.no_grad():
        log_scores = acquisition(candidates.unsqueeze(1)).cpu()
        scores = torch.exp(log_scores).tolist()
        posterior = model.posterior(candidates)
        mean = posterior.mean.cpu().tolist()
        variance = posterior.variance.cpu().tolist()
    release = {
        "schema_version": "1.0", "kind": "botorch_qlogehvi",
        "botorch_version": __import__("botorch").__version__,
        "reference_point": ref.tolist(), "mc_samples": mc_samples, "seed": seed,
        "scores": [float(value) for value in scores],
        "posterior_mean": mean, "posterior_variance": variance,
        "posterior_contract": posterior_contract,
        "policy": "q=1 independent candidate scoring; batch diversity applied separately",
    }
    release["digest"] = _digest(release)
    return release


def information_value(posterior_variance: Iterable[Iterable[float]],
                      costs: Iterable[float]) -> list[float]:
    """Transparent variance-reduction-per-cost proxy used beside, never inside, EHVI."""
    import numpy as np

    variance = np.asarray(list(posterior_variance), dtype=float)
    cost = np.asarray(list(costs), dtype=float)
    if variance.ndim != 2 or len(variance) != len(cost) or (cost <= 0).any():
        raise ValueError("VOI requires objective variances and strictly positive costs")
    return (variance.sum(axis=1) / cost).astype(float).tolist()


def greedy_diversity(similarity, *, order: Iterable[int]) -> list[float]:
    """Score novelty against the already selected batch using a disclosed greedy order."""
    import numpy as np

    matrix = np.asarray(similarity, dtype=float)
    indices = list(order)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError("diversity requires a square similarity matrix")
    scores = [0.0] * len(matrix)
    selected: list[int] = []
    for index in indices:
        scores[index] = 1.0 if not selected else 1.0 - float(matrix[index, selected].max())
        selected.append(index)
    return scores


def selection_sensitivity(candidates: Iterable[dict[str, Any]], *,
                          objectives: Iterable[dict[str, str]],
                          hard_constraints: Iterable[dict[str, Any]], capacity: int,
                          relative_perturbations: Iterable[float] = (-0.1, 0.1)) -> dict[str, Any]:
    """Re-run the disclosed policy over objective perturbations and capacity ±1."""
    rows = [dict(row) for row in candidates]
    axes = list(objectives)
    baseline = rank_portfolio(rows, objectives=axes, hard_constraints=hard_constraints,
                              capacity=capacity)
    baseline_ids = {row["proposal_id"] for row in baseline["selected"]}
    scenarios = []
    capacities = sorted({max(0, capacity - 1), capacity, capacity + 1})
    for scenario_capacity in capacities:
        result = rank_portfolio(rows, objectives=axes, hard_constraints=hard_constraints,
                                capacity=scenario_capacity)
        selected = {row["proposal_id"] for row in result["selected"]}
        union = baseline_ids | selected
        scenarios.append({"kind": "capacity", "value": scenario_capacity,
                          "selected_ids": sorted(selected),
                          "jaccard": len(baseline_ids & selected) / len(union) if union else 1.0})
    for axis in axes:
        for perturbation in relative_perturbations:
            changed = []
            for row in rows:
                copy = {**row, "objectives": dict(row["objectives"])}
                value = float(copy["objectives"][axis["key"]])
                copy["objectives"][axis["key"]] = value * (1.0 + perturbation)
                changed.append(copy)
            result = rank_portfolio(changed, objectives=axes,
                                    hard_constraints=hard_constraints, capacity=capacity)
            selected = {row["proposal_id"] for row in result["selected"]}
            union = baseline_ids | selected
            scenarios.append({"kind": "objective", "axis": axis["key"],
                              "relative_perturbation": perturbation,
                              "selected_ids": sorted(selected),
                              "jaccard": len(baseline_ids & selected) / len(union) if union else 1.0})
    minimum = min((row["jaccard"] for row in scenarios), default=1.0)
    report = {"schema_version": "1.0", "kind": "portfolio_sensitivity",
              "baseline_selected_ids": sorted(baseline_ids), "scenarios": scenarios,
              "minimum_jaccard": minimum,
              "unstable": minimum < 0.5}
    report["digest"] = _digest(report)
    return report


__all__ = [
    "botorch_qehvi", "greedy_diversity", "information_value", "selection_sensitivity",
]
