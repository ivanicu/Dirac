"""Cost-aware Scientific Action Planner for Motif.

Fidelity labels are reporting metadata.  Planning chooses the next scientific action
from explicit posterior outcome scenarios, decision utilities, costs, budgets and
anti-oscillation constraints.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping

from motif.semantics import canonical_digest


RESOURCE_KEYS = (
    "walltime_seconds", "cpu_core_hours", "gpu_hours", "gpu_vram_gib_hours",
    "scratch_gib_hours", "persistent_growth_bytes", "external_cost",
)


@dataclass(frozen=True)
class PlannerPolicy:
    policy_release_id: str
    utility_contract_id: str
    outcome_model_release_id: str
    cost_model_release_id: str
    resource_prices: Mapping[str, float]
    max_iterations: int
    max_actions_per_subject_question: int
    minimum_net_value: float = 0.0

    def __post_init__(self) -> None:
        if self.max_iterations < 1 or self.max_actions_per_subject_question < 1:
            raise ValueError("planner loop bounds must be positive")
        unknown = set(self.resource_prices) - set(RESOURCE_KEYS)
        if unknown:
            raise ValueError(f"unknown resource price keys: {sorted(unknown)}")


def _resource_cost(resources: Mapping[str, float], prices: Mapping[str, float]) -> float:
    unknown = set(resources) - set(RESOURCE_KEYS)
    if unknown:
        raise ValueError(f"unknown resource estimate keys: {sorted(unknown)}")
    if any(float(value) < 0 or not math.isfinite(float(value)) for value in resources.values()):
        raise ValueError("resource estimates must be finite and non-negative")
    return sum(float(resources.get(key, 0)) * float(prices.get(key, 0))
               for key in RESOURCE_KEYS)


def _evsi(action: Mapping[str, Any], current_utilities: Mapping[str, float]) -> float:
    if not current_utilities:
        raise ValueError("current decision utilities are required")
    baseline = max(float(value) for value in current_utilities.values())
    scenarios = action.get("outcome_scenarios") or []
    if not scenarios:
        raise ValueError("action requires outcome_scenarios")
    probability = sum(float(row["probability"]) for row in scenarios)
    if not math.isclose(probability, 1.0, abs_tol=1e-9):
        raise ValueError("outcome scenario probabilities must sum to 1")
    with_information = 0.0
    for row in scenarios:
        utilities = row.get("posterior_utilities") or {}
        if set(utilities) != set(current_utilities):
            raise ValueError("each outcome must score exactly the current decision alternatives")
        with_information += float(row["probability"]) * max(
            float(value) for value in utilities.values())
    return max(0.0, with_information - baseline)


def plan_actions(*, evidence_snapshot_ref: Mapping[str, str],
                 current_utilities: Mapping[str, float],
                 candidates: Iterable[Mapping[str, Any]],
                 remaining_budget: Mapping[str, float], policy: PlannerPolicy,
                 iteration: int, action_history: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Return a ranked action plan or an explicit stop decision."""
    if iteration >= policy.max_iterations:
        return _stop(evidence_snapshot_ref, policy, "MAX_ITERATIONS_REACHED")
    counts: dict[tuple[str, str], int] = {}
    fingerprints: set[str] = set()
    for previous in action_history:
        key = (str(previous["subject_ref"]["id"]), str(previous["scientific_question"]))
        counts[key] = counts.get(key, 0) + 1
        fingerprints.add(str(previous.get("action_fingerprint")))

    ranked: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for raw in candidates:
        action = dict(raw)
        required = {"action_kind", "subject_ref", "scientific_question",
                    "required_input_refs", "resource_estimate", "outcome_scenarios"}
        missing = required - set(action)
        if missing:
            raise ValueError(f"action misses {sorted(missing)}")
        key = (str(action["subject_ref"]["id"]), str(action["scientific_question"]))
        fingerprint = canonical_digest({
            "action_kind": action["action_kind"], "subject_ref": action["subject_ref"],
            "scientific_question": action["scientific_question"],
            "required_input_refs": action["required_input_refs"],
        })
        if counts.get(key, 0) >= policy.max_actions_per_subject_question:
            excluded.append({"action_fingerprint": fingerprint,
                             "reason_code": "SUBJECT_QUESTION_ACTION_LIMIT"})
            continue
        if fingerprint in fingerprints:
            excluded.append({"action_fingerprint": fingerprint,
                             "reason_code": "ANTI_OSCILLATION_REPEAT"})
            continue
        over = [resource for resource, estimate in action["resource_estimate"].items()
                if float(estimate) > float(remaining_budget.get(resource, 0))]
        if over:
            excluded.append({"action_fingerprint": fingerprint,
                             "reason_code": "BUDGET_INSUFFICIENT:" + ",".join(sorted(over))})
            continue
        evsi = _evsi(action, current_utilities)
        cost = _resource_cost(action["resource_estimate"], policy.resource_prices)
        net = evsi - cost
        ranked.append({
            **action, "action_fingerprint": fingerprint,
            "expected_utility_delta": evsi, "priced_resource_cost": cost,
            "expected_net_value": net,
            "p_decision_change": action.get("p_decision_change"),
        })
    ranked.sort(key=lambda row: (-row["expected_net_value"], row["action_fingerprint"]))
    viable = [row for row in ranked if row["expected_net_value"] >= policy.minimum_net_value]
    if not viable:
        result = _stop(evidence_snapshot_ref, policy, "NO_POSITIVE_VALUE_ACTION")
        result["ranked_candidates"] = ranked
        result["excluded"] = excluded
        return result
    selected = viable[0]
    return {
        "schema_version": "3.0", "decision": "act",
        "evidence_snapshot_ref": dict(evidence_snapshot_ref),
        "selected_action": selected, "ranked_candidates": ranked,
        "excluded": excluded,
        "policy": _policy_record(policy),
        "budget_lease_request": dict(selected["resource_estimate"]),
        "reason_codes": ["MAX_EXPECTED_NET_VALUE"],
    }


def _policy_record(policy: PlannerPolicy) -> dict[str, Any]:
    return {
        "policy_release_id": policy.policy_release_id,
        "utility_contract_id": policy.utility_contract_id,
        "outcome_model_release_id": policy.outcome_model_release_id,
        "cost_model_release_id": policy.cost_model_release_id,
    }


def _stop(evidence_snapshot_ref: Mapping[str, str], policy: PlannerPolicy,
          reason: str) -> dict[str, Any]:
    return {
        "schema_version": "3.0", "decision": "stop",
        "evidence_snapshot_ref": dict(evidence_snapshot_ref),
        "selected_action": None, "ranked_candidates": [], "excluded": [],
        "policy": _policy_record(policy), "reason_codes": [reason],
    }


__all__ = ["PlannerPolicy", "plan_actions", "RESOURCE_KEYS"]
