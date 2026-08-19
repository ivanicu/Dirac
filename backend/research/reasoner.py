from __future__ import annotations

import copy
import hashlib
import json
import pathlib
from typing import Any, Mapping

import jsonschema

import failures
from invocation import HandlerResult, InvocationContext

from .action_catalog import ResearchActionCatalog, default_action_catalog
from .openai_compatible import (
    ModelOutputInvalid,
    OpenAICompatibleChatProvider,
    ProviderChatResult,
    ProviderUnavailable,
)
from .proposal_validator import (
    MAX_CONTEXT_BYTES,
    MAX_PROPOSAL_BYTES,
    ProposalValidationError,
    ValidatedProposal,
    validate_proposal as _validate_proposal,
)
from .provider_registry import (
    AiProviderConfigurationError,
    canonical_json,
    sha256_digest,
)
from .metrics import METRICS


ROOT = pathlib.Path(__file__).resolve().parents[2]
PROMPT_DIRECTORY = ROOT / "contracts/research/prompts"
PROMPT_RELEASE_ID = "fep-action-proposal-v1"
PROMPT_PATH = PROMPT_DIRECTORY / f"{PROMPT_RELEASE_ID}.system.txt"
GOAL_INTERPRETER_PROMPT_PATH = (
    PROMPT_DIRECTORY / "fep-goal-interpreter-v1.system.txt"
)
GOAL_MODE_PROMPT_PATH = PROMPT_DIRECTORY / "fep-goal-mode-v1.system.txt"
MANIFEST_PATH = PROMPT_DIRECTORY / f"{PROMPT_RELEASE_ID}.manifest.json"
CONTEXT_SCHEMA_PATH = ROOT / "contracts/domain/research/context-snapshot.schema.json"
PROPOSAL_SCHEMA_PATH = ROOT / "contracts/domain/research/proposal.schema.json"
GOAL_AUTHORITY_MARKERS = (
    "revision v2 [current", "what i actually mean now is:",
    "current human request", "final operative objective",
    "latest human decision", "authoritative request",
    "最终有效目标", "最新人工决定", "权威请求",
    "objectif final", "dernière décision humaine", "verbindliche anfrage",
    "decision-relevant next step",
)


def _raw_digest(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _prompt_release() -> tuple[dict[str, Any], str, str]:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as error:
        raise failures.DiracInternal(
            f"research prompt release cannot be loaded: {type(error).__name__}"
        ) from None
    expected = {
        "system_prompt_sha256": _raw_digest(PROMPT_PATH),
        "goal_interpreter_prompt_sha256": _raw_digest(
            GOAL_INTERPRETER_PROMPT_PATH),
        "goal_mode_prompt_sha256": _raw_digest(GOAL_MODE_PROMPT_PATH),
        "context_schema_sha256": _raw_digest(CONTEXT_SCHEMA_PATH),
        "proposal_schema_sha256": _raw_digest(PROPOSAL_SCHEMA_PATH),
    }
    for field, digest in expected.items():
        if manifest.get(field) != digest:
            raise failures.DiracInternal(
                f"research prompt release {field} does not match its current source"
            )
    return manifest, sha256_digest(manifest), system_prompt


def _goal_interpreter_prompt() -> str:
    try:
        return GOAL_INTERPRETER_PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise failures.DiracInternal(
            "research goal interpreter prompt cannot be loaded: "
            f"{type(error).__name__}"
        ) from None


def _goal_mode_prompt() -> str:
    try:
        return GOAL_MODE_PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise failures.DiracInternal(
            "research goal mode prompt cannot be loaded: "
            f"{type(error).__name__}") from None


def build_goal_interpretation_schema(
    context: Mapping[str, Any],
) -> dict[str, Any]:
    choices = _goal_action_choices(context)
    if not choices:
        raise failures.DiracInternal(
            "goal interpreter requires at least one available action")
    return {
        "type": "object", "additionalProperties": False,
        "required": ["selected_action_id"],
        "properties": {
            "selected_action_id": {
                "enum": [item["action_id"] for item in choices]},
        },
    }


def _goal_action_choices(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    operation_kinds = {
        "fep.run_selected_edge.v1": "physical_fep_execution",
        "fep.prepare_selected_edge.v1": "computational_edge_system_preparation",
        "fep.replan_network.v1": "computational_network_planning",
        "fep.defer_for_experiment.v1": "wet_lab_followup_draft",
        "fep.stop.v1": "close_research_loop",
    }
    choices: list[dict[str, Any]] = []
    for action in context["available_actions"]:
        for subject in action["subject_refs"]:
            choices.append({
                "action_id": f"action_{len(choices) + 1:04d}",
                "template_id": str(action["template_id"]),
                "operation_kind": operation_kinds.get(
                    str(action["template_id"]), "unknown"),
                "intent": str(action["intent"]),
                "subject_ref": dict(subject),
            })
    return choices


def _narrow_acquisition_choices(
    context: Mapping[str, Any], choices: list[dict[str, Any]], operative_text: str,
) -> list[dict[str, Any]]:
    """Bind explicit Campaign entities before asking the model to pick an edge."""
    folded = operative_text.casefold()
    cut_points = [folded.find(marker) for marker in (
        "discarded", "作废", "annulé", "archived", "[quoted:",
        "quoted appendix", "quoted noise") if folded.find(marker) >= 0]
    if cut_points:
        folded = folded[:min(cut_points)]
    project = next((item["structured_value"] for item in context["facts"]
                    if item["category"] == "project_decision_context"), {})
    priorities = list(project.get("compound_priorities") or [])
    compound_ids = [str(row.get("compound_id") or "") for row in priorities]
    mention_counts = {compound: folded.count(compound.casefold())
                      for compound in compound_ids if compound}
    ranked_mentions = sorted(
        ((count, compound) for compound, count in mention_counts.items() if count > 0),
        reverse=True)
    mentioned = {compound for _count, compound in ranked_mentions[:2]}
    if ("high-priority" in folded or "high priority" in folded
            or "高优先" in folded):
        high_ready = [str(row.get("compound_id")) for row in priorities
                      if str(row.get("priority") or "").casefold() == "high"
                      and "ready" in str(row.get("synthesis_status") or "").casefold()]
        if len(high_ready) == 1:
            mentioned.add(high_ready[0])
    reference = str(project.get("reference_ligand") or "")
    if reference and (reference.casefold() in folded or len(mentioned) == 1):
        mentioned.add(reference)
    if len(mentioned) < 2:
        return choices
    objects = {(item["ref"]["kind"], item["ref"]["id"]): item
               for item in context["objects"]}
    narrowed = []
    for choice in choices:
        item = objects.get((choice["subject_ref"]["kind"],
                            choice["subject_ref"]["id"]), {})
        state = item.get("state") or {}
        endpoints = {str(state.get("left_id") or ""),
                     str(state.get("right_id") or "")}
        if not all(endpoints) and item.get("label"):
            endpoints.update(compound for compound in mentioned
                             if compound in str(item["label"]))
        if mentioned.issubset(endpoints):
            narrowed.append(choice)
    return narrowed or choices


def build_goal_interpretation_messages(
    context: Mapping[str, Any], *, system_prompt: str,
) -> tuple[str, str]:
    goal_intent = str(context["goal"]["intent"])
    folded = goal_intent.casefold()
    authority_hints: list[str] = []
    for marker in GOAL_AUTHORITY_MARKERS:
        cursor = 0
        while len(authority_hints) < 8:
            index = folded.find(marker.casefold(), cursor)
            if index < 0:
                break
            end_marker = folded.find("end final objective", index + len(marker))
            end = min(len(goal_intent), index + 512)
            if 0 <= end_marker < end:
                end = min(len(goal_intent), end_marker + len("end final objective"))
            snippet = goal_intent[index:end].strip()
            if snippet and snippet not in authority_hints:
                authority_hints.append(snippet)
            cursor = index + len(marker)
    objects = {
        (item["ref"]["kind"], item["ref"]["id"]): item
        for item in context["objects"]
    }
    choices = _goal_action_choices(context)
    project_context = next((
        item["structured_value"] for item in context["facts"]
        if item["category"] == "project_decision_context"
    ), {})
    request = {
        "goal_intent": ("\n".join(authority_hints)
                        if authority_hints else goal_intent),
        "goal_intent_sha256": sha256_digest(goal_intent),
        "operative_attention_windows": authority_hints,
        "project_decision_context": project_context,
        "available_actions": [
            {
                **item,
                "subject": {
                    "ref": item["subject_ref"],
                    "label": objects.get((item["subject_ref"]["kind"],
                                          item["subject_ref"]["id"]), {}).get(
                                              "label"),
                    "state": objects.get((item["subject_ref"]["kind"],
                                          item["subject_ref"]["id"]), {}).get(
                                              "state", {}),
                },
            }
            for item in choices
        ],
    }
    return system_prompt, "JSON goal interpretation input:\n" + canonical_json(
        request).decode("utf-8")


def interpret_goal(
    provider: OpenAICompatibleChatProvider,
    profile: Any,
    context: Mapping[str, Any],
) -> tuple[dict[str, Any], ProviderChatResult | None]:
    choices = _goal_action_choices(context)
    if len(choices) == 1:
        return {
            **choices[0],
            "selected_template_id": choices[0]["template_id"],
            "source": "single_available_template",
        }, None
    templates = {item["template_id"] for item in choices}
    classes = {
        "acquire_fep": {"fep.run_selected_edge.v1",
                        "fep.prepare_selected_edge.v1"},
        "replan_network": {"fep.replan_network.v1"},
        "defer_for_experiment": {"fep.defer_for_experiment.v1"},
        "stop": {"fep.stop.v1"},
    }
    available_classes = [name for name, members in classes.items()
                         if templates & members]
    _, action_user = build_goal_interpretation_messages(
        context, system_prompt="Return JSON only.")
    mode_request = json.loads(action_user.removeprefix(
        "JSON goal interpretation input:\n"))
    mode_request["intent_classes"] = {
        "acquire_fep": "Acquire FEP evidence; exact edge state later decides prepare versus run.",
        "replan_network": "Construct or revise the computational RBFE network.",
        "defer_for_experiment": "Request synthesis, assay, wet-lab work, or an external observation.",
        "stop": "Close the loop and start no new action.",
    }
    external_verbs = (
        "synthesize", "synthesise", "measure", "wet-lab", "wet lab",
        "合成", "测定", "測定", "synthét", "mesur", "synthetis", "miss zuerst",
        "sintet", "medir", "합성", "측정",
    )
    mode_request["external_action_predicate_hits"] = [
        verb for verb in external_verbs
        if verb in str(mode_request["goal_intent"]).casefold()]
    mode_request.pop("available_actions", None)
    mode_result = provider.complete_json(
        profile, system_prompt=_goal_mode_prompt(),
        context_json="JSON goal mode input:\n" + canonical_json(
            mode_request).decode("utf-8"),
        output_schema={
            "type": "object", "additionalProperties": False,
            "required": ["selected_intent_class"],
            "properties": {"selected_intent_class": {
                "enum": available_classes}},
        },
        request_profile_fields="classifier_request_fields",
    )
    try:
        mode_document = json.loads(mode_result.content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ModelOutputInvalid("goal_mode_is_not_json") from None
    if (not isinstance(mode_document, dict)
            or set(mode_document) != {"selected_intent_class"}
            or mode_document["selected_intent_class"] not in available_classes):
        raise ModelOutputInvalid("goal_mode_shape_invalid")
    selected_class = str(mode_document["selected_intent_class"])
    eligible_templates = classes[selected_class]
    eligible_choices = [item for item in choices
                        if item["template_id"] in eligible_templates]
    if selected_class == "acquire_fep":
        eligible_choices = _narrow_acquisition_choices(
            context, eligible_choices, str(mode_request["goal_intent"]))
    if len(eligible_choices) == 1:
        choice = eligible_choices[0]
        return {**choice, "selected_template_id": choice["template_id"],
                "intent_class": selected_class,
                "source": "bounded_hierarchical_model_interpretation"}, mode_result
    filtered_context = copy.deepcopy(context)
    eligible_keys = {(item["template_id"], item["subject_ref"]["kind"],
                      item["subject_ref"]["id"]) for item in eligible_choices}
    filtered_context["available_actions"] = []
    for item in context["available_actions"]:
        refs = [ref for ref in item["subject_refs"]
                if (item["template_id"], ref["kind"], ref["id"]) in eligible_keys]
        if refs:
            filtered_context["available_actions"].append({**item, "subject_refs": refs})
    system, user = build_goal_interpretation_messages(
        filtered_context, system_prompt=_goal_interpreter_prompt())
    action_result = provider.complete_json(
        profile, system_prompt=system, context_json=user,
        output_schema=build_goal_interpretation_schema(filtered_context),
        request_profile_fields="classifier_request_fields")
    try:
        document = json.loads(action_result.content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ModelOutputInvalid("goal_interpretation_is_not_json") from None
    if not isinstance(document, dict) or set(document) != {"selected_action_id"}:
        raise ModelOutputInvalid("goal_interpretation_shape_invalid")
    filtered_choices = _goal_action_choices(filtered_context)
    selected = document.get("selected_action_id")
    filtered_choice = next((item for item in filtered_choices
                            if item["action_id"] == selected), None)
    if filtered_choice is None:
        raise ModelOutputInvalid("goal_interpretation_value_invalid")
    choice = next((item for item in choices
                   if item["template_id"] == filtered_choice["template_id"]
                   and item["subject_ref"] == filtered_choice["subject_ref"]), None)
    if choice is None:
        raise ModelOutputInvalid("goal_interpretation_value_invalid")
    combined_usage: dict[str, Any] = {}
    _sum_usage(combined_usage, mode_result.usage)
    _sum_usage(combined_usage, action_result.usage)
    result = ProviderChatResult(
        content=action_result.content,
        configured_model=action_result.configured_model,
        resolved_model=action_result.resolved_model,
        provider_request_id=action_result.provider_request_id,
        usage=combined_usage,
        attempts=mode_result.attempts + action_result.attempts,
        transport_events=mode_result.transport_events + action_result.transport_events)
    return {
        **choice,
        "selected_template_id": choice["template_id"],
        "intent_class": selected_class,
        "source": "bounded_hierarchical_model_interpretation",
    }, result


def build_generation_schema(
    context: Mapping[str, Any], catalog: Mapping[str, Mapping[str, Any]],
    *, selected_template_id: str | None = None,
    selected_subject_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind provider grammar to IDs and action shapes in the frozen context."""

    try:
        schema = json.loads(PROPOSAL_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise failures.DiracInternal(
            f"research proposal contract cannot be loaded: {type(error).__name__}"
        ) from None

    schema["properties"]["context_digest"] = {"const": context["digest"]}
    singleton_ids = {
        "hypothesis_drafts": ("hypothesis_id", "h1"),
        "claim_assessments": ("claim_id", "c1"),
        "scientific_questions": ("question_id", "q1"),
        "candidate_actions": ("proposal_action_id", "a1"),
    }
    for section, (field, value) in singleton_ids.items():
        section_schema = schema["properties"][section]
        section_schema["minItems"] = 1
        section_schema["items"]["properties"][field] = {"const": value}
    schema["properties"]["candidate_actions"]["items"]["properties"][
        "scientific_question_id"
    ] = {"const": "q1"}
    schema["properties"]["preferred_action_id"] = {"const": "a1"}

    has_eligible_evidence = any(
        item["source_class"] == "typed_evidence"
        and item["claim_boundary"]["eligible_as_scientific_evidence"]
        for item in context["facts"]
    )
    if not has_eligible_evidence:
        schema["properties"]["claim_assessments"]["items"]["properties"][
            "interpretation"
        ] = {"const": "unresolved"}
    fact_ids = sorted({str(item["fact_id"]) for item in context["facts"]})
    fact_id_schema = schema["$defs"]["factIds"]
    if fact_ids:
        fact_id_schema["items"] = {"enum": fact_ids}
    else:
        fact_id_schema["maxItems"] = 0

    refs: dict[tuple[str, str], dict[str, str]] = {}
    for item in context["objects"]:
        ref = item["ref"]
        refs[(ref["kind"], ref["id"])] = dict(ref)
    for item in context["facts"]:
        ref = item["subject_ref"]
        refs[(ref["kind"], ref["id"])] = dict(ref)
    for available in context["available_actions"]:
        for ref in available["subject_refs"]:
            refs[(ref["kind"], ref["id"])] = dict(ref)
    subject_refs = [refs[key] for key in sorted(refs)]
    if subject_refs:
        schema["properties"]["scientific_questions"]["items"]["properties"][
            "subject_ref"
        ] = {"enum": subject_refs}
    if selected_subject_ref is not None:
        schema["properties"]["scientific_questions"]["items"]["properties"][
            "subject_ref"
        ] = {"const": dict(selected_subject_ref)}

    candidate = schema["properties"]["candidate_actions"]["items"]
    branches: list[dict[str, Any]] = []
    for available in context["available_actions"]:
        template_id = str(available["template_id"])
        if selected_template_id is not None and template_id != selected_template_id:
            continue
        template = catalog.get(template_id)
        if template is None:
            raise failures.DiracInternal(
                f"research context exposes unknown action template {template_id}"
            )
        for subject_ref in available["subject_refs"]:
            if (selected_subject_ref is not None
                    and dict(subject_ref) != dict(selected_subject_ref)):
                continue
            branch = copy.deepcopy(candidate)
            branch["properties"]["proposal_action_id"] = {"const": "a1"}
            branch["properties"]["scientific_question_id"] = {"const": "q1"}
            branch["properties"]["template_id"] = {"const": template_id}
            branch["properties"]["subject_ref"] = {"const": dict(subject_ref)}
            hints = copy.deepcopy(template["model_hint_schema"])
            edge = hints.get("properties", {}).get("edge_id")
            if (
                isinstance(edge, dict)
                and subject_ref["kind"] == "free_energy_transformation"
            ):
                hints["properties"]["edge_id"] = {"const": subject_ref["id"]}
            branch["properties"]["parameter_hints"] = hints
            branches.append(branch)
    if not branches:
        schema["properties"]["candidate_actions"]["maxItems"] = 0
    else:
        schema["properties"]["candidate_actions"]["items"] = {"oneOf": branches}
    return schema


def build_action_semantics_schema() -> dict[str, Any]:
    """Small model-owned WHY surface; IDs and governance stay server-owned."""
    fields = ("summary", "scientific_question", "rationale",
              "expected_observation", "falsifier")
    return {
        "type": "object", "additionalProperties": False,
        "required": list(fields),
        "properties": {
            field: {"type": "string", "minLength": 1, "maxLength": 96}
            for field in fields
        },
    }


def _proposal_from_semantics(
    semantics: Mapping[str, Any], *, context: Mapping[str, Any],
    goal_interpretation: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    template_id = str(goal_interpretation["selected_template_id"])
    subject_ref = dict(goal_interpretation["subject_ref"])
    template = catalog[template_id]
    hint_schema = template["model_hint_schema"]
    parameter_hints: dict[str, Any] = {}
    if ("edge_id" in (hint_schema.get("properties") or {})
            and subject_ref["kind"] == "free_energy_transformation"):
        parameter_hints["edge_id"] = subject_ref["id"]
    if template_id == "fep.stop.v1":
        parameter_hints["reason_code"] = "HUMAN_GOAL_REQUESTS_STOP"
    elif template_id == "fep.defer_for_experiment.v1":
        parameter_hints["reason"] = str(semantics["rationale"])
    project_fact = next((
        item["fact_id"] for item in context["facts"]
        if item["category"] == "project_decision_context"
    ), None)
    supporting = [project_fact] if project_fact else []
    stop = template_id == "fep.stop.v1"
    return {
        "schema_version": "1.0", "context_digest": context["digest"],
        "summary": str(semantics["summary"]),
        "hypothesis_drafts": [{
            "hypothesis_id": "h1", "statement": str(semantics["summary"]),
            "testable_prediction": str(semantics["expected_observation"]),
            "falsifier": str(semantics["falsifier"]),
            "supporting_fact_ids": supporting, "contradicting_fact_ids": [],
            "assumptions": [
                "The frozen Campaign context remains current through approval."],
            "confidence_band": "low",
        }],
        "claim_assessments": [{
            "claim_id": "c1", "claim": str(semantics["summary"]),
            "interpretation": "unresolved", "supporting_fact_ids": [],
            "contradicting_fact_ids": [],
            "limitations": ["This is a model proposal, not scientific evidence."],
        }],
        "scientific_questions": [{
            "question_id": "q1", "question": str(semantics["scientific_question"]),
            "subject_ref": subject_ref,
            "decision_relevance": str(semantics["rationale"]),
        }],
        "candidate_actions": [{
            "proposal_action_id": "a1", "template_id": template_id,
            "subject_ref": subject_ref, "scientific_question_id": "q1",
            "rationale": str(semantics["rationale"]),
            "expected_observation": str(semantics["expected_observation"]),
            "falsifier": str(semantics["falsifier"]),
            "supporting_fact_ids": supporting, "contradicting_fact_ids": [],
            "parameter_hints": parameter_hints, "qualitative_priority": "high",
        }],
        "preferred_action_id": "a1",
        "stop_recommendation": {
            "recommended": stop,
            "reason_codes": ["HUMAN_GOAL_REQUESTS_STOP"] if stop else [],
        },
        "unknowns": [], "conflicts": [], "warnings": [],
    }


def _read_context(payload: Mapping[str, Any], ctx: InvocationContext) -> dict[str, Any]:
    if ctx.artifact_reader is None:
        raise failures.DiracUnsupported(
            "ai.research.propose requires a research-context Artifact reader",
            details={"reason": "research_context_reader_unavailable"},
        )
    reference = payload["context_snapshot_ref"]
    artifact, raw = ctx.artifact_reader.read(reference["id"])
    actual_sha = "sha256:" + artifact.sha256
    if artifact.role != "research.context_snapshot":
        raise failures.DiracInvalidParameters(
            "context_snapshot_ref does not point to a research.context_snapshot",
            details={"actual_role": artifact.role},
        )
    if actual_sha != reference["sha256"]:
        raise failures.DiracInvalidParameters(
            "context_snapshot_ref SHA does not match the stored Artifact"
        )
    if len(raw) != payload["context_size_bytes"] or len(raw) > MAX_CONTEXT_BYTES:
        raise failures.DiracInvalidParameters(
            "context_snapshot_ref size does not match the bounded Method input",
            details={"actual_size_bytes": len(raw)},
        )
    try:
        context = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise failures.DiracInvalidParameters(
            "research.context_snapshot Artifact is not JSON"
        ) from None
    if not isinstance(context, dict):
        raise failures.DiracInvalidParameters(
            "research.context_snapshot Artifact root must be an object"
        )
    try:
        from .proposal_validator import _validate_schema, CONTEXT_SCHEMA

        _validate_schema(context, CONTEXT_SCHEMA, "context")
    except ProposalValidationError as error:
        raise failures.DiracInvalidParameters(
            "research.context_snapshot Artifact violates its contract",
            details=error.bounded_summary(),
        ) from None
    if context.get("digest") != payload["context_digest"]:
        raise failures.DiracInvalidParameters(
            "context_digest does not match the frozen context Artifact"
        )
    return context


def attest_reasoner_admission(payload: dict, ctx: InvocationContext) -> dict[str, Any]:
    registry = ctx.ai_provider_registry
    if registry is None:
        raise failures.DiracUnsupported(
            "AI provider registry is not configured",
            details={"reason": "ai_provider_registry_unavailable"},
        )
    manifest, prompt_digest, _ = _prompt_release()
    catalog = default_action_catalog()
    if payload["prompt_release_id"] != manifest["prompt_release_id"]:
        raise failures.DiracUnsupported(
            "the requested prompt release is not current",
            details={"reason": "prompt_release_not_current"},
        )
    digest_checks = {
        "prompt_release_digest": prompt_digest,
        "output_schema_digest": manifest["proposal_schema_sha256"],
        "action_catalog_digest": catalog.digest,
    }
    for field, current in digest_checks.items():
        if payload[field] != current:
            raise failures.DiracUnsupported(
                f"{field} is stale",
                details={"reason": f"{field}_mismatch", "current": current},
            )
    context = _read_context(payload, ctx)
    if context["loop_version"] != payload["loop_version"]:
        raise failures.DiracInvalidParameters("context loop_version is not frozen")
    if context["iteration"] != payload["iteration"]:
        raise failures.DiracInvalidParameters("context iteration is not frozen")
    try:
        witness = registry.attest(
            payload["provider_profile_id"],
            payload["provider_profile_digest"],
            payload["data_classification"],
        )
    except AiProviderConfigurationError as error:
        raise failures.DiracUnsupported(
            "AI provider admission was refused",
            details={"reason": error.reason, **error.details},
        ) from None
    # v0 executes under the initiating human principal. This is the minimum
    # authority witness until the durable loop repository binds the same actor
    # to its frozen session grant; service/agent callers fail closed here.
    if witness["external_egress"] and (ctx.actor or {}).get("kind") != "human":
        raise failures.DiracUnsupported(
            "external provider egress requires the initiating human loop actor",
            details={"reason": "cloud_egress_human_authorization_required"},
        )
    return {
        **witness,
        "prompt_release_digest": prompt_digest,
        "output_schema_digest": manifest["proposal_schema_sha256"],
        "action_catalog_digest": catalog.digest,
        "context_digest": context["digest"],
    }


def build_messages(
    context: Mapping[str, Any],
    *,
    system_prompt: str,
    validation_error: Mapping[str, Any] | None = None,
    goal_interpretation: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    selected_template = str(
        (goal_interpretation or {}).get("selected_template_id") or "")
    selected_subject = (goal_interpretation or {}).get("subject_ref")
    selected_actions = [
        item for item in context["available_actions"]
        if not selected_template or item["template_id"] == selected_template
        if selected_subject is None or any(
            dict(ref) == dict(selected_subject) for ref in item["subject_refs"])
    ]
    selected_refs = {
        (ref["kind"], ref["id"])
        for action in selected_actions for ref in action["subject_refs"]
    }
    campaign_ref = context["campaign_ref"]
    model_objects = []
    for item in context["objects"]:
        if ((item["ref"]["kind"], item["ref"]["id"]) not in selected_refs
                and item["ref"] != campaign_ref):
            continue
        projected = copy.deepcopy(item)
        if item["ref"] == campaign_ref:
            projected["state"].pop("project_context", None)
        model_objects.append(projected)
    # The response_format already carries the complete strict JSON Schema.  The
    # user message carries the scientific decision projection once, rather than
    # repeating the schema and every unrelated object until local-model context
    # is exhausted.  Final validation still runs against the full frozen context.
    model_facts = [
        {key: item[key] for key in (
            "fact_id", "category", "source_class", "subject_ref",
            "structured_value", "freshness", "claim_boundary")}
        for item in context["facts"]
        if item["category"] == "project_decision_context"
    ]
    reasoning_context = {
        "schema_version": context["schema_version"],
        "context_digest": context["digest"],
        "goal": context["goal"],
        "campaign_ref": campaign_ref,
        "campaign_binding": context["campaign_binding"],
        "budget": context["budget"],
        "objects": model_objects,
        "facts": model_facts,
        "available_actions": selected_actions,
        "open_attention": context["open_attention"],
    }
    wrapper: dict[str, Any] = {
        "instruction": (
            "Return only the compact scientific action semantics requested by "
            "response_format. The server owns all identifiers, references, "
            "governance fields, and the final proposal envelope."
        ),
        "output_mode": "compact_action_semantics_v1",
        "proposal_contract_sha256": _raw_digest(PROPOSAL_SCHEMA_PATH),
        "research_context_projection": reasoning_context,
    }
    if goal_interpretation is not None:
        wrapper["goal_interpretation"] = dict(goal_interpretation)
    if validation_error is not None:
        wrapper["previous_validation_error"] = dict(validation_error)
        wrapper["regeneration_instruction"] = (
            "Return a complete replacement JSON object; do not patch the previous output."
        )
    return system_prompt, "JSON research context:\n" + canonical_json(wrapper).decode("utf-8")


def validate_proposal(
    document: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
    action_catalog: Mapping[str, Mapping[str, Any]],
) -> ValidatedProposal:
    return _validate_proposal(
        document, context=context, action_catalog=action_catalog
    )


def redact_provider_response(result: ProviderChatResult) -> dict[str, Any]:
    return {
        "configured_model": result.configured_model,
        "resolved_model": result.resolved_model,
        "provider_request_id": result.provider_request_id,
        "usage": dict(result.usage),
        "attempts": result.attempts,
        "transport_events": list(result.transport_events),
    }


def _sum_usage(total: dict[str, Any], current: Mapping[str, Any]) -> None:
    for key, value in current.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total[key] = total.get(key, 0) + value


def propose_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    registry = ctx.ai_provider_registry
    if registry is None:
        raise failures.DiracUnsupported("AI provider registry is not configured")
    context = _read_context(payload, ctx)
    manifest, _, system_prompt = _prompt_release()
    catalog: ResearchActionCatalog = default_action_catalog()
    try:
        profile = registry.resolve(payload["provider_profile_id"])
    except AiProviderConfigurationError as error:
        raise failures.DiracUnsupported(
            "AI provider profile cannot be resolved",
            details={"reason": error.reason, **error.details},
        ) from None
    provider = OpenAICompatibleChatProvider()
    usage: dict[str, Any] = {}
    provider_http_attempts = 0
    try:
        goal_interpretation, interpreter_result = interpret_goal(
            provider, profile, context)
    except ProviderUnavailable as error:
        raise failures.DiracProviderUnavailable(
            "bounded AI goal interpretation failed",
            details={"reason": error.reason, "attempts": error.attempts},
        ) from None
    except ModelOutputInvalid as error:
        raise failures.DiracModelOutputInvalid(
            "AI goal interpretation remained invalid",
            details={"reason": error.reason, "attempts": error.attempts},
        ) from None
    interpreter_metadata: dict[str, Any] = {
        "selected_action_id": goal_interpretation["action_id"],
        "selected_template_id": goal_interpretation["selected_template_id"],
        "selected_subject_ref": goal_interpretation["subject_ref"],
        "source": goal_interpretation["source"],
        "raw_provider_response_stored": False,
    }
    if interpreter_result is not None:
        interpreter_metadata["provider"] = redact_provider_response(
            interpreter_result)
        provider_http_attempts += interpreter_result.attempts
        _sum_usage(usage, interpreter_result.usage)
    output_schema = build_action_semantics_schema()
    maximum_regenerations = int(profile.document["bounds"]["max_schema_regenerations"])
    validation_error: dict[str, Any] | None = None
    latest_metadata: dict[str, Any] = {}

    for validation_attempt in range(1, maximum_regenerations + 2):
        ctx.check_budget()
        system, user = build_messages(
            context, system_prompt=system_prompt, validation_error=validation_error,
            goal_interpretation=goal_interpretation,
        )
        try:
            result = provider.complete_json(
                profile, system_prompt=system, context_json=user,
                output_schema=output_schema,
            )
        except ProviderUnavailable as error:
            raise failures.DiracProviderUnavailable(
                "bounded AI provider request failed",
                details={"reason": error.reason, "attempts": error.attempts},
            ) from None
        except ModelOutputInvalid as error:
            METRICS.counter("dirac_research_loop_proposal_validation_total", {
                "result": "transport_invalid",
            })
            validation_error = {
                "reason": error.reason,
                "pointer": [],
            }
            if validation_attempt <= maximum_regenerations:
                continue
            raise failures.DiracModelOutputInvalid(
                "provider response remained invalid after bounded regeneration: "
                f"{error.reason}",
                details={"validation": validation_error, "attempts": validation_attempt},
            ) from None
        latest_metadata = redact_provider_response(result)
        provider_http_attempts += result.attempts
        _sum_usage(usage, result.usage)
        try:
            semantics = json.loads(result.content)
            jsonschema.Draft202012Validator(output_schema).validate(semantics)
            proposal = _proposal_from_semantics(
                semantics, context=context,
                goal_interpretation=goal_interpretation, catalog=catalog)
            validated = _validate_proposal(
                proposal, context=context, action_catalog=catalog)
        except (json.JSONDecodeError, jsonschema.ValidationError,
                ProposalValidationError) as error:
            METRICS.counter("dirac_research_loop_proposal_validation_total", {
                "result": "schema_invalid",
            })
            validation_error = (error.bounded_summary()
                                if isinstance(error, ProposalValidationError)
                                else {"reason": "action_semantics_schema_invalid",
                                      "pointer": []})
            if validation_attempt <= maximum_regenerations:
                continue
            raise failures.DiracModelOutputInvalid(
                "model proposal remained invalid after bounded regeneration",
                details={"validation": validation_error, "attempts": validation_attempt},
            ) from None

        METRICS.counter("dirac_research_loop_proposal_validation_total", {
            "result": "accepted",
        })
        return HandlerResult(
            result={
                "context_digest": payload["context_digest"],
                "proposal_digest": validated.proposal_digest,
                "provider_profile_id": profile.profile_id,
                "provider_profile_digest": profile.profile_digest,
                "configured_model": result.configured_model,
                "resolved_model": result.resolved_model,
                "provider_request_id": result.provider_request_id,
                "usage": usage,
                "validation_attempts": validation_attempt,
                "claim_boundary": "model_proposal_not_scientific_evidence",
            },
            artifacts=[("research.proposal", validated.canonical_bytes)],
            provenance={
                "provider": latest_metadata,
                "goal_interpreter": interpreter_metadata,
                "provider_http_attempts": provider_http_attempts,
                "prompt_release_id": manifest["prompt_release_id"],
                "prompt_release_digest": payload["prompt_release_digest"],
                "output_schema_digest": payload["output_schema_digest"],
                "action_catalog_digest": payload["action_catalog_digest"],
                "context_digest": payload["context_digest"],
                "claim_boundary": "model_proposal_not_scientific_evidence",
                "raw_provider_response_stored": False,
                "reasoning_content_stored": False,
            },
            parameters_used={
                "provider_profile_id": profile.profile_id,
                "configured_model": profile.configured_model,
                "validation_attempts": validation_attempt,
            },
        )

    raise failures.DiracModelOutputInvalid("model proposal validation exhausted")
