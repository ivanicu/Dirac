"""Pure Program invariants shared by durable and in-memory repositories."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date, datetime
from typing import Any

import failures

LIFECYCLES = frozenset({"draft", "active", "paused", "completed", "archived"})
STAGES = frozenset({
    "discovery", "target_validation", "hit_discovery", "hit_to_lead",
    "lead_optimization", "candidate_selection", "preclinical",
})
OBJECTIVE_CATEGORIES = frozenset({
    "efficacy", "selectivity", "developability", "safety", "synthesis", "evidence",
})
OBJECTIVE_DIRECTIONS = frozenset({
    "maximize", "minimize", "at_least", "at_most", "within", "qualitative",
})
DECISION_TYPES = frozenset({
    "scope", "scientific", "portfolio", "stage_gate", "resource", "risk",
})

_LIFECYCLE_TRANSITIONS = {
    "draft": {"draft", "active", "archived"},
    "active": {"active", "paused", "completed", "archived"},
    "paused": {"paused", "active", "completed", "archived"},
    "completed": {"completed", "active", "archived"},
    "archived": {"archived", "active"},
}


def actor(value: dict[str, Any]) -> dict[str, str]:
    if not isinstance(value, dict) or value.get("kind") not in {"human", "agent", "service"}:
        raise failures.DiracInvalidParameters("actor.kind must be human, agent, or service")
    identifier = str(value.get("id", "")).strip()
    if not identifier:
        raise failures.DiracInvalidParameters("actor.id is required")
    return {"kind": value["kind"], "id": identifier}


def key(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 96 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", text):
        raise failures.DiracInvalidParameters(
            f"{field} must be 1-96 letters, numbers, dots, colons, underscores, or dashes")
    return text


def nonempty(value: Any, field: str, *, maximum: int = 4000) -> str:
    text = str(value or "").strip()
    if not text:
        raise failures.DiracInvalidParameters(f"{field} is required")
    if len(text) > maximum:
        raise failures.DiracInvalidParameters(f"{field} exceeds {maximum} characters")
    return text


def ref(value: dict[str, Any], expected_kind: str | None = None) -> dict[str, str]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("ObjectRef must be an object")
    kind_value = str(value.get("kind", ""))
    identifier = str(value.get("id", "")).strip()
    if expected_kind is not None and kind_value != expected_kind:
        raise failures.DiracInvalidParameters(
            f"expected a {expected_kind} reference", details={"received_kind": kind_value})
    if not kind_value or not identifier:
        raise failures.DiracInvalidParameters("ObjectRef.kind and ObjectRef.id are required")
    return {"kind": kind_value, "id": identifier}


def create_spec(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("program must be an object")
    lifecycle = value.get("lifecycle", "active")
    stage = value.get("stage", "discovery")
    if lifecycle not in LIFECYCLES:
        raise failures.DiracInvalidParameters("unknown Program lifecycle")
    if stage not in STAGES:
        raise failures.DiracInvalidParameters("unknown Program stage")
    target = value.get("target_ref")
    return {
        "code": key(value.get("code"), "program.code").upper(),
        "name": nonempty(value.get("name"), "program.name", maximum=256),
        "summary": _optional_text(value.get("summary"), 4000),
        "indication": _optional_text(value.get("indication"), 256),
        "modality": _optional_text(value.get("modality"), 256),
        "owner_id": _optional_text(value.get("owner_id"), 256),
        "lifecycle": lifecycle,
        "stage": stage,
        "target_ref": ref(target, "target") if target is not None else None,
    }


def update_patch(current: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise failures.DiracInvalidParameters("patch must contain at least one field")
    allowed = {"name", "summary", "indication", "modality", "owner_id", "lifecycle", "stage", "target_ref"}
    unknown = set(value) - allowed
    if unknown:
        raise failures.DiracInvalidParameters(
            "Program patch contains unsupported fields", details={"fields": sorted(unknown)})
    out: dict[str, Any] = {}
    if "name" in value:
        out["name"] = nonempty(value["name"], "patch.name", maximum=256)
    for field_name, maximum in (("summary", 4000), ("indication", 256),
                                ("modality", 256), ("owner_id", 256)):
        if field_name in value:
            out[field_name] = _optional_text(value[field_name], maximum)
    if "stage" in value:
        if value["stage"] not in STAGES:
            raise failures.DiracInvalidParameters("unknown Program stage")
        out["stage"] = value["stage"]
    if "lifecycle" in value:
        lifecycle = value["lifecycle"]
        if lifecycle not in LIFECYCLES:
            raise failures.DiracInvalidParameters("unknown Program lifecycle")
        old = current["lifecycle"]
        if lifecycle not in _LIFECYCLE_TRANSITIONS[old]:
            raise failures.DiracInvalidParameters(
                f"Program lifecycle cannot move from {old} to {lifecycle}")
        out["lifecycle"] = lifecycle
    if "target_ref" in value:
        out["target_ref"] = (ref(value["target_ref"], "target")
                             if value["target_ref"] is not None else None)
    return out


def objective(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("objective must be an object")
    category = value.get("category")
    if category not in OBJECTIVE_CATEGORIES:
        raise failures.DiracInvalidParameters("unknown objective category")
    metric = _optional_text(value.get("metric"), 256)
    direction = value.get("direction")
    if (metric is None) != (direction is None):
        raise failures.DiracInvalidParameters("objective metric and direction must be supplied together")
    if direction is not None and direction not in OBJECTIVE_DIRECTIONS:
        raise failures.DiracInvalidParameters("unknown objective direction")
    threshold = copy.deepcopy(value.get("threshold", {}))
    if not isinstance(threshold, dict):
        raise failures.DiracInvalidParameters("objective.threshold must be an object")
    priority = value.get("priority", 3)
    if not isinstance(priority, int) or not 1 <= priority <= 5:
        raise failures.DiracInvalidParameters("objective.priority must be an integer from 1 to 5")
    hardness = value.get("hardness", "soft")
    if hardness not in {"hard", "soft"}:
        raise failures.DiracInvalidParameters("objective.hardness must be hard or soft")
    return {
        "key": key(value.get("key"), "objective.key"),
        "title": nonempty(value.get("title"), "objective.title", maximum=256),
        "rationale": nonempty(value.get("rationale"), "objective.rationale"),
        "category": category, "metric": metric, "direction": direction,
        "threshold": threshold, "priority": priority, "hardness": hardness,
    }


def hypothesis(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("hypothesis must be an object")
    confidence = value.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        raise failures.DiracInvalidParameters("hypothesis.confidence must be between 0 and 1")
    return {
        "key": key(value.get("key"), "hypothesis.key"),
        "title": nonempty(value.get("title"), "hypothesis.title", maximum=256),
        "statement": nonempty(value.get("statement"), "hypothesis.statement"),
        "falsification_criterion": nonempty(
            value.get("falsification_criterion"), "hypothesis.falsification_criterion"),
        "confidence": float(confidence),
    }


def decision(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("decision must be an object")
    decision_type = value.get("type")
    if decision_type not in DECISION_TYPES:
        raise failures.DiracInvalidParameters("unknown decision type")
    alternatives = copy.deepcopy(value.get("alternatives", []))
    if not isinstance(alternatives, list):
        raise failures.DiracInvalidParameters("decision.alternatives must be an array")
    return {
        "key": key(value.get("key"), "decision.key"),
        "type": decision_type,
        "action": nonempty(value.get("action"), "decision.action"),
        "outcome": nonempty(value.get("outcome"), "decision.outcome"),
        "rationale": nonempty(value.get("rationale"), "decision.rationale"),
        "alternatives": alternatives,
    }


def milestone(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("milestone must be an object")
    criteria = copy.deepcopy(value.get("criteria"))
    if not isinstance(criteria, list) or not criteria:
        raise failures.DiracInvalidParameters("milestone.criteria must be a non-empty array")
    target_date = value.get("target_date")
    if target_date is not None:
        try:
            date.fromisoformat(str(target_date))
        except ValueError as exc:
            raise failures.DiracInvalidParameters("milestone.target_date must be YYYY-MM-DD") from exc
    return {
        "key": key(value.get("key"), "milestone.key"),
        "title": nonempty(value.get("title"), "milestone.title", maximum=256),
        "description": _optional_text(value.get("description"), 4000),
        "target_date": target_date, "criteria": criteria,
    }


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default).encode()


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_json_default))


def _optional_text(value: Any, maximum: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > maximum:
        raise failures.DiracInvalidParameters(f"text exceeds {maximum} characters")
    return text


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)
