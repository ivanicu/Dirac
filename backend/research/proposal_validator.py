from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import jsonschema

from .provider_registry import canonical_json, sha256_digest


ROOT = pathlib.Path(__file__).resolve().parents[2]
PROPOSAL_SCHEMA = json.loads(
    (ROOT / "contracts/domain/research/proposal.schema.json").read_text(encoding="utf-8")
)
CONTEXT_SCHEMA = json.loads(
    (ROOT / "contracts/domain/research/context-snapshot.schema.json").read_text(
        encoding="utf-8"
    )
)
MAX_CONTEXT_BYTES = 262_144
MAX_PROPOSAL_BYTES = 262_144
_URL = re.compile(r"\b(?:https?|file|gopher|unix)://", re.IGNORECASE)
_HTTP_REQUEST = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/\S+", re.IGNORECASE)
_SHELL = re.compile(r"(?:^|[\s;|&])(?:curl|wget|bash|sh|sudo|rm|scp|ssh)\s+", re.IGNORECASE)
_SQL = re.compile(
    r"\b(?:SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM|DROP\s+(?:TABLE|DATABASE))\b",
    re.IGNORECASE | re.DOTALL,
)
_FORBIDDEN_KEYS = frozenset(
    {
        "command_id", "method_id", "tool_name", "url", "http_request",
        "shell_command", "sql", "provider_profile", "provider_profile_id",
        "approval", "approval_decision", "full_command_payload",
    }
)


@dataclass(frozen=True)
class ValidatedProposal:
    document: dict[str, Any]
    canonical_bytes: bytes
    proposal_digest: str


class ProposalValidationError(ValueError):
    def __init__(
        self, reason: str, *, pointer: Iterable[Any] = (),
        schema_keyword: str | None = None,
        schema_pointer: Iterable[Any] = (),
        expected: Iterable[Any] = (),
        unexpected_keys: Iterable[str] = (),
    ):
        super().__init__(reason)
        self.reason = reason
        self.pointer = tuple(pointer)
        self.schema_keyword = schema_keyword
        self.schema_pointer = tuple(schema_pointer)
        self.expected = tuple(expected)
        self.unexpected_keys = tuple(unexpected_keys)

    def bounded_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "reason": self.reason,
            "pointer": list(self.pointer)[:16],
        }
        if self.schema_keyword:
            summary["schema_keyword"] = self.schema_keyword[:64]
        if self.schema_pointer:
            summary["schema_pointer"] = list(self.schema_pointer)[:16]
        if self.expected:
            summary["expected"] = list(self.expected)[:32]
        if self.unexpected_keys:
            summary["unexpected_keys"] = [
                str(key)[:128] for key in self.unexpected_keys[:16]
            ]
        return summary


def _validate_schema(document: Any, schema: Mapping[str, Any], name: str) -> None:
    try:
        jsonschema.Draft202012Validator(schema).validate(document)
    except jsonschema.ValidationError as error:
        keyword = str(error.validator or "") or None
        expected: tuple[Any, ...] = ()
        unexpected_keys: tuple[str, ...] = ()
        if keyword == "required" and isinstance(error.validator_value, list):
            expected = tuple(error.validator_value)
        if (keyword == "additionalProperties"
                and isinstance(error.instance, Mapping)
                and isinstance(error.schema.get("properties"), Mapping)):
            allowed = set(error.schema["properties"])
            unexpected_keys = tuple(sorted(str(key) for key in error.instance
                                           if key not in allowed))
        raise ProposalValidationError(
            f"{name}_schema_invalid", pointer=error.absolute_path,
            schema_keyword=keyword,
            schema_pointer=error.absolute_schema_path,
            expected=expected,
            unexpected_keys=unexpected_keys,
        ) from None


def _ref_key(ref: Mapping[str, Any]) -> tuple[str, str]:
    return str(ref.get("kind")), str(ref.get("id"))


def _walk(value: Any, pointer: tuple[Any, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield pointer + (key,), key, item
            yield from _walk(item, pointer + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, pointer + (index,))


def _load_executable_ids() -> tuple[str, ...]:
    command_registry = json.loads(
        (ROOT / "contracts/commands/registry.json").read_text(encoding="utf-8")
    )
    command_ids = [item["id"] for item in command_registry["commands"]]
    method_ids = []
    for path in (ROOT / "contracts/methods").glob("*.method.json"):
        method_ids.append(json.loads(path.read_text(encoding="utf-8"))["method_id"])
    return tuple(sorted({*command_ids, *method_ids}, key=len, reverse=True))


EXECUTABLE_IDS = _load_executable_ids()


def _reject_executable_semantics(document: Mapping[str, Any]) -> None:
    for pointer, key, value in _walk(document):
        if key in _FORBIDDEN_KEYS:
            raise ProposalValidationError("proposal_contains_forbidden_field", pointer=pointer)
        if not isinstance(value, str):
            continue
        if _URL.search(value):
            raise ProposalValidationError("proposal_contains_url", pointer=pointer)
        if _HTTP_REQUEST.search(value):
            raise ProposalValidationError("proposal_contains_http_request", pointer=pointer)
        if _SHELL.search(value):
            raise ProposalValidationError("proposal_contains_shell_command", pointer=pointer)
        if _SQL.search(value):
            raise ProposalValidationError("proposal_contains_sql", pointer=pointer)
        if any(executable_id in value for executable_id in EXECUTABLE_IDS):
            raise ProposalValidationError(
                "proposal_contains_direct_executable_id", pointer=pointer
            )


def _fact_references(document: Mapping[str, Any]):
    for index, item in enumerate(document["hypothesis_drafts"]):
        for field in ("supporting_fact_ids", "contradicting_fact_ids"):
            for fact_id in item[field]:
                yield fact_id, ("hypothesis_drafts", index, field), item["assumptions"]
    for index, item in enumerate(document["claim_assessments"]):
        for field in ("supporting_fact_ids", "contradicting_fact_ids"):
            for fact_id in item[field]:
                yield fact_id, ("claim_assessments", index, field), item["limitations"]
    for index, item in enumerate(document["candidate_actions"]):
        for field in ("supporting_fact_ids", "contradicting_fact_ids"):
            for fact_id in item[field]:
                yield fact_id, ("candidate_actions", index, field), document["warnings"]


def validate_proposal(
    document: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
    action_catalog: Mapping[str, Mapping[str, Any]],
    rejected_candidate_keys: set[str] | None = None,
) -> ValidatedProposal:
    context_bytes = canonical_json(context)
    proposal_bytes = canonical_json(document)
    if len(context_bytes) > MAX_CONTEXT_BYTES:
        raise ProposalValidationError("context_exceeds_hard_byte_bound")
    if len(proposal_bytes) > MAX_PROPOSAL_BYTES:
        raise ProposalValidationError("proposal_exceeds_hard_byte_bound")
    _validate_schema(context, CONTEXT_SCHEMA, "context")
    _validate_schema(document, PROPOSAL_SCHEMA, "proposal")
    _reject_executable_semantics(document)

    if document["context_digest"] != context["digest"]:
        raise ProposalValidationError("proposal_context_digest_mismatch")

    facts = {item["fact_id"]: item for item in context["facts"]}
    object_refs = {_ref_key(item["ref"]) for item in context["objects"]}
    object_refs.update(_ref_key(item["subject_ref"]) for item in context["facts"])
    for action in context["available_actions"]:
        object_refs.update(_ref_key(ref) for ref in action["subject_refs"])

    stale_fact_ids = {key for key, fact in facts.items() if fact["freshness"]["stale"]}
    for fact_id, pointer, limitation_text in _fact_references(document):
        if fact_id not in facts:
            raise ProposalValidationError("proposal_references_unknown_fact", pointer=pointer)
        if fact_id in stale_fact_ids and not any(
            fact_id in text and "stale" in text.lower() for text in limitation_text
        ):
            raise ProposalValidationError(
                "stale_fact_not_identified_as_limitation", pointer=pointer
            )

    question_ids: set[str] = set()
    for index, question in enumerate(document["scientific_questions"]):
        question_id = question["question_id"]
        if question_id in question_ids:
            raise ProposalValidationError(
                "duplicate_scientific_question_id",
                pointer=("scientific_questions", index, "question_id"),
            )
        question_ids.add(question_id)
        if _ref_key(question["subject_ref"]) not in object_refs:
            raise ProposalValidationError(
                "proposal_references_unknown_subject",
                pointer=("scientific_questions", index, "subject_ref"),
            )

    action_ids: set[str] = set()
    candidate_keys: set[str] = set()
    for index, action in enumerate(document["candidate_actions"]):
        action_id = action["proposal_action_id"]
        if action_id in action_ids:
            raise ProposalValidationError(
                "duplicate_proposal_action_id",
                pointer=("candidate_actions", index, "proposal_action_id"),
            )
        action_ids.add(action_id)
        if action["scientific_question_id"] not in question_ids:
            raise ProposalValidationError(
                "proposal_action_references_unknown_question",
                pointer=("candidate_actions", index, "scientific_question_id"),
            )
        if _ref_key(action["subject_ref"]) not in object_refs:
            raise ProposalValidationError(
                "proposal_references_unknown_subject",
                pointer=("candidate_actions", index, "subject_ref"),
            )
        template = action_catalog.get(action["template_id"])
        if template is None:
            raise ProposalValidationError(
                "proposal_references_unknown_template",
                pointer=("candidate_actions", index, "template_id"),
            )
        try:
            jsonschema.Draft202012Validator(template["model_hint_schema"]).validate(
                action["parameter_hints"]
            )
        except (KeyError, jsonschema.ValidationError) as error:
            pointer = ("candidate_actions", index, "parameter_hints")
            if isinstance(error, jsonschema.ValidationError):
                pointer += tuple(error.absolute_path)
            raise ProposalValidationError("proposal_parameter_hints_invalid", pointer=pointer) from None
        candidate_key = sha256_digest(
            {
                "template_id": action["template_id"],
                "subject_ref": action["subject_ref"],
                "parameter_hints": action["parameter_hints"],
            }
        )
        if candidate_key in candidate_keys:
            raise ProposalValidationError(
                "duplicate_candidate_action", pointer=("candidate_actions", index)
            )
        candidate_keys.add(candidate_key)
        if candidate_key in (rejected_candidate_keys or set()):
            raise ProposalValidationError(
                "proposal_repeats_rejected_action", pointer=("candidate_actions", index)
            )

    preferred = document["preferred_action_id"]
    if preferred is not None and preferred not in action_ids:
        raise ProposalValidationError("preferred_action_does_not_exist")

    for index, assessment in enumerate(document["claim_assessments"]):
        if assessment["interpretation"] != "supported":
            continue
        supporting = [facts[item] for item in assessment["supporting_fact_ids"]]
        if supporting and not any(
            item["source_class"] == "typed_evidence"
            and item["claim_boundary"]["eligible_as_scientific_evidence"]
            for item in supporting
        ):
            raise ProposalValidationError(
                "ineligible_method_result_cannot_support_scientific_claim",
                pointer=("claim_assessments", index, "interpretation"),
            )

    canonical = canonical_json(document)
    return ValidatedProposal(
        document=dict(document),
        canonical_bytes=canonical,
        proposal_digest=sha256_digest(canonical),
    )


def parse_and_validate_proposal(
    raw: str | bytes,
    *,
    context: Mapping[str, Any],
    action_catalog: Mapping[str, Mapping[str, Any]],
    rejected_candidate_keys: set[str] | None = None,
) -> ValidatedProposal:
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(encoded) > MAX_PROPOSAL_BYTES:
        raise ProposalValidationError("proposal_exceeds_hard_byte_bound")
    try:
        document = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProposalValidationError("proposal_is_not_json") from None
    if not isinstance(document, dict):
        raise ProposalValidationError("proposal_root_is_not_object")
    return validate_proposal(
        document,
        context=context,
        action_catalog=action_catalog,
        rejected_candidate_keys=rejected_candidate_keys,
    )
