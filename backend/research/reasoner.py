from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any, Mapping

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
    parse_and_validate_proposal,
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
MANIFEST_PATH = PROMPT_DIRECTORY / f"{PROMPT_RELEASE_ID}.manifest.json"
CONTEXT_SCHEMA_PATH = ROOT / "contracts/domain/research/context-snapshot.schema.json"
PROPOSAL_SCHEMA_PATH = ROOT / "contracts/domain/research/proposal.schema.json"


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
        "context_schema_sha256": _raw_digest(CONTEXT_SCHEMA_PATH),
        "proposal_schema_sha256": _raw_digest(PROPOSAL_SCHEMA_PATH),
    }
    for field, digest in expected.items():
        if manifest.get(field) != digest:
            raise failures.DiracInternal(
                f"research prompt release {field} does not match its current source"
            )
    return manifest, sha256_digest(manifest), system_prompt


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
) -> tuple[str, str]:
    wrapper: dict[str, Any] = {
        "instruction": "Return the complete bounded proposal as one JSON object.",
        "research_context": context,
    }
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
    maximum_regenerations = int(profile.document["bounds"]["max_schema_regenerations"])
    validation_error: dict[str, Any] | None = None
    usage: dict[str, Any] = {}
    provider_http_attempts = 0
    latest_metadata: dict[str, Any] = {}

    for validation_attempt in range(1, maximum_regenerations + 2):
        ctx.check_budget()
        system, user = build_messages(
            context, system_prompt=system_prompt, validation_error=validation_error
        )
        try:
            result = provider.complete_json(
                profile, system_prompt=system, context_json=user
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
                "provider response remained invalid after bounded regeneration",
                details={"validation": validation_error, "attempts": validation_attempt},
            ) from None
        latest_metadata = redact_provider_response(result)
        provider_http_attempts += result.attempts
        _sum_usage(usage, result.usage)
        try:
            validated = parse_and_validate_proposal(
                result.content, context=context, action_catalog=catalog
            )
        except ProposalValidationError as error:
            METRICS.counter("dirac_research_loop_proposal_validation_total", {
                "result": "schema_invalid",
            })
            validation_error = error.bounded_summary()
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
