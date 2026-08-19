"""Compile one bounded model proposal into an exact existing Command preview."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

import failures
from dirac_app.registry import CommandRegistry
from research.action_catalog import ResearchActionCatalog, default_action_catalog
from research.context_builder import canonical_bytes, canonical_digest


ROOT = Path(__file__).resolve().parents[2]
PREVIEW_SCHEMA = ROOT / "contracts/domain/research/action-preview.schema.json"


@dataclass(frozen=True)
class CompiledAction:
    preview: Mapping[str, Any]
    preview_bytes: bytes
    preview_digest: str
    command_input: Mapping[str, Any] | None


class ActionCompiler:
    def __init__(
        self, resolver: Any, *, command_registry: CommandRegistry | None = None,
        action_catalog: ResearchActionCatalog | None = None,
        preview_ttl_seconds: int = 1800,
    ) -> None:
        self.resolver = resolver
        self.commands = command_registry or CommandRegistry.load()
        self.actions = action_catalog or default_action_catalog()
        self.preview_ttl_seconds = int(preview_ttl_seconds)
        if self.preview_ttl_seconds <= 0:
            raise ValueError("preview_ttl_seconds must be positive")
        schema = json.loads(PREVIEW_SCHEMA.read_text(encoding="utf-8"))
        self.preview_validator = Draft202012Validator(
            schema, format_checker=FormatChecker())

    def compile(
        self, *, loop: Mapping[str, Any], context: Mapping[str, Any],
        proposal: Mapping[str, Any], now: datetime | None = None,
    ) -> CompiledAction:
        candidate = self._preferred_candidate(proposal)
        template_id = str(candidate["template_id"])
        try:
            template = self.actions[template_id]
        except KeyError:
            raise failures.DiracModelOutputInvalid(
                "proposal selected an unknown action template") from None
        self._authorize_template(loop, template)
        question = self._question(proposal, candidate["scientific_question_id"])
        command_id = template["execution"]["command_id"]
        resolution = self.resolver.resolve(
            template_id=template_id, candidate=candidate, loop=loop,
            context=context,
        )
        if not isinstance(resolution, Mapping):
            raise failures.DiracInternal("action resolver returned no bounded result")
        command_input = resolution.get("command_input")
        if command_id is None:
            if command_input not in (None, {}):
                raise failures.DiracInternal(
                    "non-executing action resolver returned Command input")
            resolved_command = None
            input_digest = canonical_digest({})
            command_input = None
            command_version = None
        else:
            if not isinstance(command_input, Mapping):
                raise failures.DiracInternal(
                    "executing action resolver did not return Command input")
            definition = self.commands.get(str(command_id))
            exact_input = dict(command_input)
            self.commands.validate_input(definition, exact_input)
            input_digest = canonical_digest(exact_input)
            command_input = exact_input
            command_version = definition.version
            resolved_command = {
                "command_id": definition.id,
                "command_version": definition.version,
                "input_digest": input_digest,
            }
        source_versions = resolution.get("source_versions")
        if not isinstance(source_versions, Mapping) or not source_versions:
            raise failures.DiracInternal(
                "action resolver omitted source version witnesses")
        estimate = resolution.get("estimate") or {
            "available": False, "gpu_hours_upper_bound": None,
            "external_cost_upper_bound": None,
        }
        self._check_budget(loop, estimate, template_id)
        clock = now or datetime.now(timezone.utc)
        if clock.tzinfo is None:
            raise ValueError("ActionPreview clock must be timezone-aware")
        consequence = {
            "risk_class": template["consequence"]["risk_class"],
            "approval": template["consequence"]["approval"],
            "reversible": bool(template["consequence"]["reversible"]),
            "summary": str(resolution.get("consequence_summary") or template["intent"]),
        }
        required_acknowledgements = (
            ["physical_fep_compute", "completed_unvalidated_claim_boundary"]
            if consequence["risk_class"] == "R3" else [])
        fingerprint_payload = {
            "template_id": template_id,
            "subject_ref": candidate["subject_ref"],
            "scientific_question": question,
            "command_version": command_version,
            "input_digest": input_digest,
            "source_versions": dict(source_versions),
        }
        fingerprint = canonical_digest(fingerprint_payload)
        history = list(context.get("action_history") or [])
        if any(
            row.get("action_fingerprint") == fingerprint
            and bool(row.get("human_rejected"))
            for row in history
        ):
            raise failures.DiracUnsupported(
                "the exact action fingerprint was already rejected by a human")
        same_subject = sum(
            1 for row in history
            if row.get("subject_ref") == candidate["subject_ref"]
            and not bool(row.get("human_rejected"))
        )
        maximum = int((loop.get("policy") or {}).get(
            "max_same_subject_actions", 1))
        if same_subject >= maximum:
            raise failures.DiracUnsupported(
                "the loop has reached its frozen same-subject action limit",
                details={"subject_ref": candidate["subject_ref"],
                         "completed_or_approved": same_subject,
                         "maximum": maximum})
        preview = {
            "schema_version": "1.0",
            "run_ref": {"kind": "run", "id": str(loop["run_id"])},
            # Mint for the state that will exist after prepare_action transitions.
            "loop_version": int(loop["version"]) + 1,
            "context_digest": str(context["digest"]),
            "template_id": template_id,
            "subject_ref": dict(candidate["subject_ref"]),
            "scientific_question": question,
            "resolved_command": resolved_command,
            "source_versions": dict(source_versions),
            "estimate": dict(estimate),
            "consequence": consequence,
            "required_acknowledgements": required_acknowledgements,
            "expires_at": (
                clock + timedelta(seconds=self.preview_ttl_seconds)
            ).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "action_fingerprint": fingerprint,
        }
        errors = sorted(
            self.preview_validator.iter_errors(preview), key=lambda error: list(error.path))
        if errors:
            error = errors[0]
            raise failures.DiracInternal(
                "compiled ActionPreview violates its frozen contract",
                details={"path": list(error.path), "message": error.message})
        encoded = canonical_bytes(preview)
        return CompiledAction(
            preview=preview, preview_bytes=encoded,
            preview_digest="sha256:" + hashlib.sha256(encoded).hexdigest(),
            command_input=command_input,
        )

    def revalidate(
        self, compiled: Mapping[str, Any], *, loop: Mapping[str, Any],
        current_context_digest: str, current_source_versions: Mapping[str, Any],
        acknowledgements: list[str], actor: Mapping[str, Any],
        now: datetime | None = None,
    ) -> None:
        preview = compiled.get("preview")
        if not isinstance(preview, Mapping):
            raise failures.DiracStalePreview("pending ActionPreview is absent")
        clock = now or datetime.now(timezone.utc)
        expected_acks = set(preview.get("required_acknowledgements") or [])
        failures_seen = []
        preview_version = int(preview.get("loop_version", -1))
        loop_version = int(loop["version"])
        approved_per_action = (
            preview.get("consequence", {}).get("approval") == "per_action"
            and bool((compiled.get("approved_acknowledgements") or [])))
        if preview_version != loop_version and not (
                approved_per_action and preview_version + 1 == loop_version):
            failures_seen.append("loop_version")
        if preview.get("context_digest") != current_context_digest:
            failures_seen.append("context_digest")
        if dict(preview.get("source_versions") or {}) != dict(current_source_versions):
            failures_seen.append("source_versions")
        expires = datetime.fromisoformat(str(preview["expires_at"]).replace("Z", "+00:00"))
        if clock >= expires:
            failures_seen.append("expiry")
        if not expected_acks.issubset(set(acknowledgements)):
            failures_seen.append("acknowledgements")
        if preview.get("consequence", {}).get("approval") == "per_action":
            if actor.get("kind") != "human" or not str(actor.get("id") or "").strip():
                failures_seen.append("human_actor")
        resolved = preview.get("resolved_command")
        if resolved and canonical_digest(dict(compiled.get("command_input") or {})) != resolved.get(
                "input_digest"):
            failures_seen.append("command_input_digest")
        if failures_seen:
            raise failures.DiracStalePreview(
                "scientific context changed after the action preview was created",
                details={"stale_witnesses": failures_seen})

    @staticmethod
    def _preferred_candidate(proposal: Mapping[str, Any]) -> Mapping[str, Any]:
        preferred = proposal.get("preferred_action_id")
        candidates = proposal.get("candidate_actions") or []
        candidate = next(
            (item for item in candidates if item.get("proposal_action_id") == preferred),
            None,
        )
        if candidate is None:
            raise failures.DiracModelOutputInvalid(
                "proposal does not identify one preferred candidate action")
        return candidate

    @staticmethod
    def _question(proposal: Mapping[str, Any], question_id: str) -> str:
        for item in proposal.get("scientific_questions") or []:
            if item.get("question_id") == question_id:
                return str(item["question"])
        raise failures.DiracModelOutputInvalid(
            "preferred action points to an absent scientific question")

    @staticmethod
    def _authorize_template(loop: Mapping[str, Any], template: Mapping[str, Any]) -> None:
        consequence = template["consequence"]
        risk = consequence["risk_class"]
        if risk == "R4":
            raise failures.DiracUnsupported(
                "R4 actions remain human-only and cannot be compiled by the loop")
        grant = (loop.get("policy") or {}).get("session_grant") or {}
        if template["template_id"] not in set(grant.get("allowed_template_ids") or []):
            raise failures.DiracUnsupported(
                "action template is outside the frozen loop session grant")
        if risk in {"R0", "R1", "R2"} and risk not in set(
                grant.get("allowed_risk_classes") or []):
            raise failures.DiracUnsupported(
                "action risk class is outside the frozen loop session grant")

    @staticmethod
    def _check_budget(loop: Mapping[str, Any], estimate: Mapping[str, Any],
                      template_id: str) -> None:
        remaining = loop.get("budget_remaining") or {}
        checks = (
            ("gpu_hours", estimate.get("gpu_hours_upper_bound")),
            ("external_cost", estimate.get("external_cost_upper_bound")),
        )
        exceeded = {}
        for key, needed in checks:
            available = remaining.get(key)
            if needed is not None and available is not None and float(needed) > float(available):
                exceeded[key] = {"needed_upper_bound": needed, "remaining": available}
        if template_id == "fep.run_selected_edge.v1" and int(
                remaining.get("fep_runsets", 0)) < 1:
            exceeded["fep_runsets"] = {"needed_upper_bound": 1,
                                        "remaining": remaining.get("fep_runsets", 0)}
        if exceeded:
            raise failures.DiracBudgetExceeded(
                "compiled action exceeds the frozen loop budget",
                details={"template_id": template_id, "exceeded": exceeded})
