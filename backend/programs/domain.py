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
MEMBER_ROLES = frozenset({
    "program_lead", "medicinal_chemistry", "computational_chemistry", "biology",
    "dmpk", "toxicology", "synthesis", "data_science", "operations", "reviewer",
    "observer",
})
GATE_STATUSES = frozenset({"planned", "ready", "approved", "rejected"})
WORK_STATUSES = frozenset({"backlog", "ready", "active", "blocked", "done", "cancelled"})
WORKFLOW_LANES = frozenset({"understand", "design", "decide", "make", "test_learn"})
EVIDENCE_RELATIONS = frozenset({"supports", "contradicts", "tests", "explains"})
EVIDENCE_KINDS = frozenset({
    "evidence", "measurement", "dataset", "artifact", "literature_reference",
    "prediction", "complex", "pose", "field", "batch", "sample",
})
LINEAGE_SHAPES = frozenset({
    ("compound", "has_form", "compound_form"),
    ("compound_form", "produced_as", "batch"),
    ("sample", "sampled_from", "batch"),
    ("sample", "formulated_as", "formulation"),
    ("batch", "released_by", "quality_release"),
    ("sample", "assayed_under", "protocol"),
    ("sample", "has_measurement", "measurement"),
})
REFERENCE_JOB_KINDS = frozenset({
    "target_disease", "substance_registration", "sample", "sample_transfer",
    "work_comment", "work_attachment", "gate_criterion", "protocol_version",
    "dataset_version", "experiment", "structure_observation", "annotation",
    "review", "analysis_snapshot", "evidence_release", "external_evidence",
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
        "portfolio_ref": (ref(value["portfolio_ref"], "portfolio")
                          if value.get("portfolio_ref") is not None else None),
    }


def update_patch(current: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise failures.DiracInvalidParameters("patch must contain at least one field")
    allowed = {"name", "summary", "indication", "modality", "owner_id", "lifecycle", "stage", "target_ref", "portfolio_ref"}
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
    if "portfolio_ref" in value:
        out["portfolio_ref"] = (ref(value["portfolio_ref"], "portfolio")
                                if value["portfolio_ref"] is not None else None)
    return out


def portfolio(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("portfolio must be an object")
    lifecycle = value.get("lifecycle", "active")
    if lifecycle not in LIFECYCLES:
        raise failures.DiracInvalidParameters("unknown Portfolio lifecycle")
    return {
        "code": key(value.get("code"), "portfolio.code").upper(),
        "name": nonempty(value.get("name"), "portfolio.name", maximum=256),
        "mandate": _optional_text(value.get("mandate"), 4000),
        "lifecycle": lifecycle,
    }


def member(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("member must be an object")
    principal = actor(value.get("principal"))
    role = value.get("role")
    if role not in MEMBER_ROLES:
        raise failures.DiracInvalidParameters("unknown Program member role")
    return {"principal": principal, "role": role,
            "responsibility": _optional_text(value.get("responsibility"), 2000)}


def stage_gate(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("stage_gate must be an object")
    stage = value.get("stage")
    status = value.get("status", "planned")
    if stage not in STAGES:
        raise failures.DiracInvalidParameters("unknown stage gate stage")
    if status not in GATE_STATUSES:
        raise failures.DiracInvalidParameters("unknown stage gate status")
    criteria = copy.deepcopy(value.get("criteria"))
    if not isinstance(criteria, list) or not criteria:
        raise failures.DiracInvalidParameters("stage_gate.criteria must be a non-empty array")
    normalized = []
    for index, criterion in enumerate(criteria):
        if isinstance(criterion, str):
            normalized.append({"criterion": nonempty(criterion, f"criteria[{index}]"), "status": "unmet"})
        elif isinstance(criterion, dict):
            state = criterion.get("status", "unmet")
            if state not in {"unmet", "met", "waived"}:
                raise failures.DiracInvalidParameters("criterion status must be unmet, met, or waived")
            normalized.append({"criterion": nonempty(criterion.get("criterion"), f"criteria[{index}].criterion"),
                               "status": state})
        else:
            raise failures.DiracInvalidParameters("each stage gate criterion must be text or an object")
    decision_ref = value.get("decision_ref")
    if status in {"approved", "rejected"} and decision_ref is None:
        raise failures.DiracInvalidParameters("approved or rejected stage gate requires decision_ref")
    target_date = _iso_date(value.get("target_date"), "stage_gate.target_date")
    return {"key": key(value.get("key"), "stage_gate.key"),
            "stage": stage, "title": nonempty(value.get("title"), "stage_gate.title", maximum=256),
            "criteria": normalized, "status": status,
            "evidence_summary": _optional_text(value.get("evidence_summary"), 4000),
            "decision_ref": ref(decision_ref, "decision") if decision_ref is not None else None,
            "target_date": target_date}


def work_package(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("work_package must be an object")
    status = value.get("status", "backlog")
    if status not in WORK_STATUSES:
        raise failures.DiracInvalidParameters("unknown work package status")
    lane = value.get("lane", "understand")
    if lane not in WORKFLOW_LANES:
        raise failures.DiracInvalidParameters("unknown Program workflow lane")
    priority = value.get("priority", 3)
    if not isinstance(priority, int) or isinstance(priority, bool) or not 1 <= priority <= 5:
        raise failures.DiracInvalidParameters("work_package.priority must be an integer from 1 to 5")
    owner = value.get("owner")
    deliverables = copy.deepcopy(value.get("deliverable_refs", []))
    dependencies = copy.deepcopy(value.get("depends_on_refs", []))
    if not isinstance(deliverables, list) or not isinstance(dependencies, list):
        raise failures.DiracInvalidParameters("deliverable_refs and depends_on_refs must be arrays")
    return {"key": key(value.get("key"), "work_package.key"),
            "title": nonempty(value.get("title"), "work_package.title", maximum=256),
            "description": nonempty(value.get("description"), "work_package.description"),
            "lane": lane, "status": status, "priority": priority,
            "owner": actor(owner) if owner is not None else None,
            "due_on": _iso_date(value.get("due_on"), "work_package.due_on"),
            "deliverable_refs": [ref(item) for item in deliverables],
            "depends_on_refs": [ref(item, "work_item") for item in dependencies]}


def work_transition(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("transition must be an object")
    lane = value.get("to_lane")
    if lane not in WORKFLOW_LANES:
        raise failures.DiracInvalidParameters("unknown Program workflow lane")
    return {"work_item_ref": ref(value.get("work_item_ref"), "work_item"),
            "to_lane": lane,
            "reason": nonempty(value.get("reason"), "transition.reason", maximum=4000)}


def work_execution(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("execution binding must be an object")
    return {"work_item_ref": ref(value.get("work_item_ref"), "work_item"),
            "job_ref": ref(value.get("job_ref"), "job"),
            "purpose": _optional_text(value.get("purpose"), 2000)}


def evidence_binding(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("binding must be an object")
    subject = ref(value.get("subject_ref"))
    evidence = ref(value.get("evidence_ref"))
    if subject["kind"] not in {"program", "objective", "hypothesis", "decision", "milestone", "stage_gate", "work_item", "work_package"}:
        raise failures.DiracInvalidParameters("unsupported evidence subject kind")
    if evidence["kind"] not in EVIDENCE_KINDS:
        raise failures.DiracInvalidParameters("unsupported evidence object kind")
    relation = value.get("relation")
    if relation not in EVIDENCE_RELATIONS:
        raise failures.DiracInvalidParameters("evidence relation must support, contradict, test, or explain")
    strength = value.get("strength")
    if strength is not None and (not isinstance(strength, (int, float)) or isinstance(strength, bool)
                                 or not 0 <= strength <= 1):
        raise failures.DiracInvalidParameters("evidence strength must be between 0 and 1")
    return {"subject_ref": subject, "evidence_ref": evidence, "relation": relation,
            "claim": nonempty(value.get("claim"), "binding.claim"),
            "strength": float(strength) if strength is not None else None}


def lineage(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters("lineage must be an object")
    source = ref(value.get("source_ref")); target = ref(value.get("target_ref"))
    relation = value.get("relation")
    if (source["kind"], relation, target["kind"]) not in LINEAGE_SHAPES:
        raise failures.DiracInvalidParameters("lineage edge does not match the canonical compound-to-result chain",
            details={"source_kind": source["kind"], "relation": relation, "target_kind": target["kind"]})
    return {"source_ref": source, "relation": relation, "target_ref": target}


def reference_job(kind: str, value: dict[str, Any]) -> dict[str, Any]:
    """Validate one native reference-system job without weakening its semantics."""
    if kind not in REFERENCE_JOB_KINDS or not isinstance(value, dict):
        raise failures.DiracInvalidParameters("unknown or invalid reference job")
    if kind == "target_disease":
        ontology = value.get("ontology")
        if ontology is not None and (not isinstance(ontology, dict)
                                     or not ontology.get("namespace") or not ontology.get("id")):
            raise failures.DiracInvalidParameters("disease ontology requires namespace and id")
        role = value.get("role", "primary")
        if role not in {"primary", "secondary", "safety", "biomarker", "exploratory"}:
            raise failures.DiracInvalidParameters("unknown target-disease role")
        return {"disease_key": key(value.get("disease_key"), "disease_key"),
                "name": nonempty(value.get("name"), "disease.name", maximum=256),
                "description": _optional_text(value.get("description"), 4000),
                "ontology": copy.deepcopy(ontology), "target_ref": ref(value.get("target_ref"), "target"),
                "role": role, "rationale": nonempty(value.get("rationale"), "rationale")}
    if kind == "substance_registration":
        status = value.get("status", "draft")
        if status not in {"draft", "candidate_match", "conflict", "validated", "approved", "rejected"}:
            raise failures.DiracInvalidParameters("unknown registration status")
        decision = _optional_text(value.get("decision"), 4000)
        if status == "approved" and decision is None:
            raise failures.DiracInvalidParameters("approved registration requires a decision")
        return {"compound_ref": ref(value.get("compound_ref"), "compound"), "status": status,
                "definition": _object(value.get("definition"), "definition"),
                "validation": _object(value.get("validation", {}), "validation"), "decision": decision}
    if kind == "sample":
        amount = _positive_number(value.get("amount_value"), "amount_value", allow_zero=True)
        unit = str(value.get("amount_unit", ""))
        if unit not in {"g", "mg", "ug", "mol", "mmol", "umol"}:
            raise failures.DiracInvalidParameters("sample amount_unit must be a mass or amount unit")
        parent = value.get("parent_sample_ref")
        return {"sample_code": key(value.get("sample_code"), "sample_code").upper(),
                "batch_ref": ref(value.get("batch_ref"), "batch"),
                "parent_sample_ref": ref(parent, "sample") if parent else None,
                "amount_value": amount, "amount_unit": unit,
                "container": _optional_text(value.get("container"), 256),
                "location": _optional_text(value.get("location"), 512)}
    if kind == "sample_transfer":
        return {"sample_ref": ref(value.get("sample_ref"), "sample"),
                "to_location": nonempty(value.get("to_location"), "to_location", maximum=512),
                "reason": nonempty(value.get("reason"), "reason", maximum=2000)}
    if kind == "work_comment":
        return {"work_item_ref": ref(value.get("work_item_ref"), "work_item"),
                "body": nonempty(value.get("body"), "comment.body", maximum=20000)}
    if kind == "work_attachment":
        return {"work_item_ref": ref(value.get("work_item_ref"), "work_item"),
                "artifact_ref": ref(value.get("artifact_ref"), "artifact"),
                "role": key(value.get("role"), "attachment.role")}
    if kind == "gate_criterion":
        status = value.get("status")
        if status not in {"met", "not_met", "waived", "unknown"}:
            raise failures.DiracInvalidParameters("unknown criterion assessment status")
        evidence = value.get("evidence_ref")
        if status == "met" and evidence is None:
            raise failures.DiracInvalidParameters("met criterion requires evidence_ref")
        explanation = nonempty(value.get("explanation"), "assessment.explanation")
        if status == "waived" and len(explanation) < 8:
            raise failures.DiracInvalidParameters("waiver explanation is too short")
        return {"stage_gate_ref": ref(value.get("stage_gate_ref"), "stage_gate"),
                "criterion_key": key(value.get("criterion_key"), "criterion_key"),
                "status": status, "evidence_ref": ref(evidence) if evidence else None,
                "explanation": explanation}
    if kind == "protocol_version":
        return {"protocol_key": key(value.get("protocol_key"), "protocol_key"),
                "title": nonempty(value.get("title"), "protocol.title", maximum=256),
                "assay_ref": ref(value["assay_ref"], "assay") if value.get("assay_ref") else None,
                "specification": _object(value.get("specification"), "specification")}
    if kind == "dataset_version":
        parents = _array(value.get("parent_refs", []), "parent_refs")
        return {"dataset_key": key(value.get("dataset_key"), "dataset_key"),
                "manifest_artifact_ref": ref(value.get("manifest_artifact_ref"), "artifact"),
                "manifest": _object(value.get("manifest"), "manifest"),
                "schema_version": nonempty(value.get("schema_version"), "schema_version", maximum=128),
                "access_scope": _choice(value.get("access_scope", "internal"),
                    {"public", "program", "internal", "partner_confidential", "restricted", "regulated"},
                    "access_scope"),
                "experiment_ref": ref(value["experiment_ref"], "experiment") if value.get("experiment_ref") else None,
                "parent_refs": [ref(item, "dataset_version") for item in parents],
                "producer_job_ref": ref(value["producer_job_ref"], "job") if value.get("producer_job_ref") else None,
                "derivation": _optional_text(value.get("derivation"), 4000)}
    if kind == "experiment":
        status = _choice(value.get("status", "planned"),
            {"planned", "running", "completed", "failed", "cancelled"}, "experiment.status")
        started = _iso_datetime(value.get("started_at"), "started_at")
        completed = _iso_datetime(value.get("completed_at"), "completed_at")
        if status in {"completed", "failed", "cancelled"} and completed is None:
            raise failures.DiracInvalidParameters("terminal experiment requires completed_at")
        samples = _array(value.get("samples", []), "samples")
        normalized_samples = []
        for item in samples:
            if not isinstance(item, dict):
                raise failures.DiracInvalidParameters("experiment samples must be objects")
            normalized_samples.append({"sample_ref": ref(item.get("sample_ref"), "sample"),
                "role": _choice(item.get("role", "test"), {"test", "control", "reference", "matrix", "reagent"}, "sample.role")})
        return {"experiment_key": key(value.get("experiment_key"), "experiment_key"),
                "work_item_ref": ref(value.get("work_item_ref"), "work_item"),
                "protocol_version_ref": ref(value.get("protocol_version_ref"), "protocol_version"),
                "title": nonempty(value.get("title"), "experiment.title", maximum=256),
                "status": status, "started_at": started, "completed_at": completed,
                "samples": normalized_samples}
    if kind == "structure_observation":
        return {"observation_key": key(value.get("observation_key"), "observation_key"),
                "structure_ref": ref(value.get("structure_ref"), "protein_structure"),
                "compound_ref": ref(value["compound_ref"], "compound") if value.get("compound_ref") else None,
                "experiment_ref": ref(value["experiment_ref"], "experiment") if value.get("experiment_ref") else None,
                "dataset_version_ref": ref(value.get("dataset_version_ref"), "dataset_version"),
                "canonical_site": _optional_text(value.get("canonical_site"), 256)}
    if kind == "annotation":
        return {"subject_ref": ref(value.get("subject_ref")),
                "annotation_kind": _choice(value.get("annotation_kind"), {"tag", "site", "merge_hypothesis", "note", "quality"}, "annotation_kind"),
                "label": nonempty(value.get("label"), "annotation.label", maximum=256),
                "value": _object(value.get("value", {}), "annotation.value")}
    if kind == "review":
        return {"subject_ref": ref(value.get("subject_ref")),
                "review_role": _choice(value.get("review_role", "peer"), {"main", "peer"}, "review_role"),
                "status": _choice(value.get("status"), {"accepted", "questionable", "rejected"}, "review.status"),
                "comment": nonempty(value.get("comment"), "review.comment", maximum=4000)}
    if kind == "analysis_snapshot":
        mode = _choice(value.get("snapshot_mode", "preserved"), {"live", "preserved"}, "snapshot_mode")
        datasets = [ref(item, "dataset_version") for item in _array(value.get("dataset_version_refs", []), "dataset_version_refs")]
        channel = _optional_text(value.get("release_channel"), 256)
        if mode == "live" and channel is None:
            raise failures.DiracInvalidParameters("live snapshot requires release_channel")
        if mode == "preserved" and not datasets:
            raise failures.DiracInvalidParameters("preserved snapshot requires dataset versions")
        return {"work_item_ref": ref(value["work_item_ref"], "work_item") if value.get("work_item_ref") else None,
                "title": nonempty(value.get("title"), "snapshot.title", maximum=256),
                "snapshot_mode": mode, "release_channel": channel,
                "dataset_version_refs": datasets, "state": _object(value.get("state"), "snapshot.state")}
    if kind == "evidence_release":
        return {"source_name": nonempty(value.get("source_name"), "source_name", maximum=128),
                "release_name": nonempty(value.get("release_name"), "release_name", maximum=128),
                "source_url": _optional_text(value.get("source_url"), 2000),
                "retrieved_at": _iso_datetime(value.get("retrieved_at"), "retrieved_at", required=True),
                "payload_artifact_ref": ref(value.get("payload_artifact_ref"), "artifact")}
    release = ref(value.get("release_ref"), "external_evidence_release")
    score = value.get("score")
    if score is not None and (not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 1):
        raise failures.DiracInvalidParameters("evidence score must be between 0 and 1")
    return {"release_ref": release, "source_record_id": nonempty(value.get("source_record_id"), "source_record_id", maximum=512),
            "target_ref": ref(value.get("target_ref"), "target"), "disease_ref": ref(value.get("disease_ref"), "disease"),
            "data_type": key(value.get("data_type"), "data_type"),
            "evidence_source": key(value.get("evidence_source"), "evidence_source"),
            "score": float(score) if score is not None else None, "is_direct": bool(value.get("is_direct", True)),
            "payload": _object(value.get("payload"), "payload")}


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


def _iso_date(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise failures.DiracInvalidParameters(f"{field} must be YYYY-MM-DD") from exc


def _iso_datetime(value: Any, field: str, *, required: bool = False) -> str | None:
    if value in (None, ""):
        if required:
            raise failures.DiracInvalidParameters(f"{field} is required")
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except ValueError as exc:
        raise failures.DiracInvalidParameters(f"{field} must be an ISO-8601 timestamp") from exc


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failures.DiracInvalidParameters(f"{field} must be an object")
    return copy.deepcopy(value)


def _array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise failures.DiracInvalidParameters(f"{field} must be an array")
    return copy.deepcopy(value)


def _choice(value: Any, choices: set[str], field: str) -> str:
    if value not in choices:
        raise failures.DiracInvalidParameters(f"unknown {field}")
    return str(value)


def _positive_number(value: Any, field: str, *, allow_zero: bool = False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise failures.DiracInvalidParameters(f"{field} must be numeric")
    number = float(value)
    if number < 0 or (number == 0 and not allow_zero):
        raise failures.DiracInvalidParameters(f"{field} must be positive")
    return number


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)
