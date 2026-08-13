"""Compile frozen Motif inputs into an immutable, digest-addressed RunPlan."""
from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import failures


ACTION_LOOP_STEPS = {
    "inputs.freeze": None,
    "evidence.snapshot": None,
    "decision.evaluate": "design.motif.acquire",
    "action.plan": None,
    "resource.lease": None,
    "action.execute": None,
    "outcome.assess": None,
    "evidence.assemble": None,
    "decision.refresh": "design.motif.acquire",
    "loop.guard": None,
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def compile_run_plan(spec: dict[str, Any]) -> dict[str, Any]:
    """Build a bounded evidence/action loop; method choice is planner output.

    Cost-tier labels may appear in action metadata, but are not state transitions.
    """
    required = {
        "run_id", "root_seed", "objective_spec_id", "program_snapshot_id",
        "policies", "resource_envelope", "approval_gates",
    }
    missing = required - set(spec)
    if missing:
        raise failures.DiracInvalidParameters(f"RunPlan input misses {sorted(missing)}")
    for key in ("run_id", "objective_spec_id", "program_snapshot_id"):
        try:
            UUID(str(spec[key]))
        except ValueError as exc:
            raise failures.DiracInvalidParameters(f"{key} must be a UUID") from exc

    names = list(ACTION_LOOP_STEPS)
    steps = [{"index": index, "kind": name, "method_id": ACTION_LOOP_STEPS[name]}
             for index, name in enumerate(names)]
    edges = []
    for index in range(len(names) - 1):
        condition = {"type": "on_success"}
        edges.append({"from": index, "to": index + 1, "condition": condition})
    edges.extend([
        {"from": names.index("loop.guard"), "to": names.index("evidence.snapshot"),
         "condition": {"type": "if_continue", "bounded_by": [
             "planner.max_iterations", "planner.max_actions_per_subject_question",
             "resource_envelope", "planner.minimum_net_value"]}},
        {"from": names.index("loop.guard"), "to": None,
         "condition": {"type": "if_stop", "emits": "immutable_decision_snapshot"}},
    ])

    plan = {
        "run_id": spec["run_id"], "schema_version": "3.0",
        "root_seed": int(spec["root_seed"]),
        "objective_spec_id": spec["objective_spec_id"],
        "program_snapshot_id": spec["program_snapshot_id"],
        "steps": steps, "edges": edges,
        "policies": spec["policies"],
        "resource_envelope": spec["resource_envelope"],
        "approval_gates": spec["approval_gates"],
        "planner_semantics": "expected_utility_delta_minus_priced_resource_cost",
        "fidelity_labels_are_reporting_only": True,
    }
    plan["digest"] = "sha256:" + hashlib.sha256(_canonical(plan)).hexdigest()
    return plan


def verify_run_plan(plan: dict[str, Any]) -> bool:
    candidate = dict(plan)
    expected = candidate.pop("digest", None)
    actual = "sha256:" + hashlib.sha256(_canonical(candidate)).hexdigest()
    return expected == actual
