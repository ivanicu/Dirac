"""Auditable constrained Pareto selection with no hidden scalar score."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class Objective:
    key: str
    direction: str


def _dominates(left: dict[str, float], right: dict[str, float],
               objectives: tuple[Objective, ...]) -> bool:
    no_worse = True
    better = False
    for objective in objectives:
        lv, rv = left[objective.key], right[objective.key]
        if objective.direction == "maximize":
            no_worse &= lv >= rv
            better |= lv > rv
        elif objective.direction == "minimize":
            no_worse &= lv <= rv
            better |= lv < rv
        else:
            raise ValueError(f"unsupported direction {objective.direction!r}")
    return no_worse and better


def nondominated_ranks(rows: Iterable[dict[str, Any]],
                       objectives: Iterable[Objective]) -> dict[str, int]:
    """Exact O(n²) baseline; intentionally simple and replayable."""
    values = sorted(rows, key=lambda row: row["proposal_id"])
    axes = tuple(objectives)
    remaining = list(values)
    ranks: dict[str, int] = {}
    rank = 0
    while remaining:
        front = [candidate for candidate in remaining if not any(
            other is not candidate and _dominates(other["objectives"],
                                                   candidate["objectives"], axes)
            for other in remaining)]
        if not front:
            raise RuntimeError("Pareto front calculation made no progress")
        for candidate in front:
            ranks[candidate["proposal_id"]] = rank
        selected_ids = {candidate["proposal_id"] for candidate in front}
        remaining = [candidate for candidate in remaining
                     if candidate["proposal_id"] not in selected_ids]
        rank += 1
    return ranks


def _constraint_failures(candidate: dict[str, Any],
                         constraints: Iterable[dict[str, Any]]) -> list[str]:
    failed: list[str] = []
    values = candidate.get("constraints", {})
    for rule in constraints:
        key = rule["key"]
        value = values.get(key)
        if value is None:
            failed.append(f"MISSING_{key.upper()}")
        elif "minimum" in rule and value < rule["minimum"]:
            failed.append(f"BELOW_{key.upper()}_MIN")
        elif "maximum" in rule and value > rule["maximum"]:
            failed.append(f"ABOVE_{key.upper()}_MAX")
        elif "equals" in rule and value != rule["equals"]:
            failed.append(f"FAILED_{key.upper()}")
    return failed


def rank_portfolio(candidates: Iterable[dict[str, Any]], *,
                   objectives: Iterable[dict[str, str]],
                   hard_constraints: Iterable[dict[str, Any]],
                   capacity: int) -> dict[str, list[dict[str, Any]]]:
    """Partition every input into selected/reserve/rejected/refused.

    Ordering is lexicographic and visible: Pareto rank, missing evidence,
    failure risk, cost, then proposal UUID. There is deliberately no weighted total.
    """
    if capacity < 0:
        raise ValueError("capacity cannot be negative")
    axes = tuple(Objective(item["key"], item["direction"]) for item in objectives)
    if not axes:
        raise ValueError("at least one objective is required")
    rows = sorted((dict(row) for row in candidates), key=lambda row: row["proposal_id"])
    refused: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for row in rows:
        missing = [axis.key for axis in axes if row.get("objectives", {}).get(axis.key) is None]
        failures = _constraint_failures(row, hard_constraints)
        if missing or failures:
            refused.append(_item(row, "refused", None,
                                 [*(f"MISSING_{key.upper()}" for key in missing), *failures]))
        else:
            eligible.append(row)

    ranks = nondominated_ranks(eligible, axes) if eligible else {}
    ordered = sorted(eligible, key=lambda row: (
        ranks[row["proposal_id"]],
        float(row.get("components", {}).get("missing_evidence", 0)),
        float(row.get("components", {}).get("failure_risk", 0)),
        float(row.get("components", {}).get("cost", 0)),
        row["proposal_id"],
    ))
    selected_rows = ordered[:capacity]
    reserve_rows = ordered[capacity:capacity * 2]
    rejected_rows = ordered[capacity * 2:]
    return {
        "selected": [_item(row, "selected", ranks[row["proposal_id"]],
                           ["PARETO_CAPACITY_SELECTED"]) for row in selected_rows],
        "reserve": [_item(row, "reserve", ranks[row["proposal_id"]],
                          ["CAPACITY_RESERVE"]) for row in reserve_rows],
        "rejected": [_item(row, "rejected", ranks[row["proposal_id"]],
                           ["OUTRANKED_OR_CAPACITY"]) for row in rejected_rows],
        "refused": refused,
    }


def _item(row: dict[str, Any], status: str, rank: int | None,
          reasons: list[str]) -> dict[str, Any]:
    components = row.get("components", {})
    return {
        "proposal_id": row["proposal_id"], "status": status,
        "pareto_rank": rank, "selection_probability": 1.0 if status == "selected" else 0.0,
        "components": {
            key: components.get(key) for key in (
                "feasibility", "pareto_improvement", "information_value", "diversity",
                "cost", "failure_risk", "missing_evidence")
        },
        "reason_codes": sorted(set(reasons)),
        "why": ("Passed hard constraints and placed by the disclosed lexicographic "
                "Pareto policy." if status != "refused" else
                "Required evidence or a hard constraint failed."),
        "what_changes_decision": (["Increase experimental capacity or improve Pareto rank."]
                                  if status in {"reserve", "rejected"} else
                                  ["Supply the missing evidence or satisfy the failed constraint."]
                                  if status == "refused" else []),
        "evidence_artifact_ids": list(row.get("evidence_artifact_ids", ())),
    }
