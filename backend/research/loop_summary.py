"""Deterministic immutable summaries for completed research loops."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

import failures
from research.context_builder import canonical_bytes


SCHEMA_PATH = (Path(__file__).resolve().parents[2]
               / "contracts/domain/research/loop-summary.schema.json")


class LoopSummaryBuilder:
    """Project a frozen Context Artifact into an auditable terminal record."""

    def __init__(self, schema_path: Path = SCHEMA_PATH) -> None:
        self.validator = Draft202012Validator(
            json.loads(schema_path.read_text(encoding="utf-8")),
            format_checker=FormatChecker(),
        )

    def build(
        self, loop: Mapping[str, Any], context: Mapping[str, Any], *,
        context_ref: Mapping[str, Any], completion_reason: str,
    ) -> bytes:
        claims = [{
            "fact_id": str(fact["fact_id"]),
            "source_class": str(fact["source_class"]),
            "source_ref": dict(fact["source_ref"]),
            "freshness": dict(fact["freshness"]),
            "claim_boundary": dict(fact["claim_boundary"]),
        } for fact in context["facts"]]
        document = {
            "schema_version": "1.0",
            "run_ref": {"kind": "run", "id": str(loop["run_id"])},
            "program_ref": {"kind": "program", "id": str(loop["program_id"])},
            "campaign_ref": {"kind": "campaign", "id": str(loop["campaign_id"])},
            "iteration": int(loop["iteration"]),
            "completion_reason": completion_reason,
            "context_ref": dict(context_ref),
            "context_digest": str(context["digest"]),
            "source_classes": sorted({claim["source_class"] for claim in claims}),
            "claims": claims,
            "action_history": list(context["action_history"]),
            "budget": {
                "remaining": dict(loop.get("budget_remaining") or {}),
                "spent": dict(loop.get("budget_spent") or {}),
            },
        }
        errors = sorted(self.validator.iter_errors(document),
                        key=lambda error: list(error.path))
        if errors:
            first = errors[0]
            raise failures.DiracInternal(
                "research loop summary violates its frozen schema",
                details={"path": list(first.path), "message": first.message})
        return canonical_bytes(document)
