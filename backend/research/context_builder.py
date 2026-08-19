"""Deterministic, bounded context snapshots for the FEP research reasoner."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

import failures


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts/domain/research/context-snapshot.schema.json"
MAX_CONTEXT_BYTES = 262_144


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class BuiltContext:
    document: Mapping[str, Any]
    canonical_bytes: bytes
    digest: str
    size_bytes: int


class ContextBuilder:
    """Turn a current server-owned FEP snapshot into one reasoner Artifact.

    Domain facts are ordered before they enter this class.  If the byte ceiling is
    crossed, only complete low-priority facts are omitted; facts are never sliced or
    summarized because that would sever values from provenance and claim boundary.
    """

    def __init__(self, schema_path: Path = SCHEMA_PATH,
                 max_bytes: int = MAX_CONTEXT_BYTES) -> None:
        self.schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.validator = Draft202012Validator(
            self.schema, format_checker=FormatChecker())
        self.max_bytes = int(max_bytes)
        if self.max_bytes <= 0 or self.max_bytes > MAX_CONTEXT_BYTES:
            raise ValueError(f"context max_bytes must be in 1..{MAX_CONTEXT_BYTES}")

    def build(self, loop: Mapping[str, Any],
              domain: Mapping[str, Any]) -> BuiltContext:
        required_domain = {
            "campaign_binding", "objects", "facts", "human_attestations",
            "action_history", "available_actions", "open_attention", "source_clock",
        }
        missing = sorted(required_domain - set(domain))
        if missing:
            raise failures.DiracInternal(ValueError(
                "FEP adapter returned an incomplete research context; missing="
                + ",".join(missing)))
        facts = self._ordered_facts(domain["facts"])
        base = {
            "schema_version": "1.0",
            "run_ref": {"kind": "run", "id": str(loop["run_id"])},
            "program_ref": {"kind": "program", "id": str(loop["program_id"])},
            "campaign_ref": {"kind": "campaign", "id": str(loop["campaign_id"])},
            "loop_version": int(loop["version"]),
            "iteration": int(loop["iteration"]),
            "goal": {
                "intent": str(loop["intent"]),
                "constraints": list(domain.get("goal_constraints") or []),
                "success_definition": list(domain.get("success_definition") or []),
                "revised_at": domain.get("intent_revised_at"),
            },
            "campaign_binding": dict(domain["campaign_binding"]),
            "budget": {
                "remaining": self._budget(loop.get("budget_remaining") or {}),
                "spent": self._budget(loop.get("budget_spent") or {}),
            },
            "objects": list(domain["objects"]),
            "facts": facts,
            "human_attestations": list(domain["human_attestations"]),
            "action_history": list(domain["action_history"]),
            "available_actions": list(domain["available_actions"]),
            "open_attention": list(domain["open_attention"]),
            "truncation": {
                "applied": False, "omitted_fact_count": 0,
                "omitted_fact_ids": [],
                "policy": "research-context-v1",
            },
            "created_at": str(domain["source_clock"]),
        }
        omitted = 0
        while True:
            document = dict(base)
            document["digest"] = canonical_digest(base)
            encoded = canonical_bytes(document)
            if len(encoded) <= self.max_bytes:
                self._validate(document)
                return BuiltContext(
                    document=document, canonical_bytes=encoded,
                    digest=document["digest"], size_bytes=len(encoded),
                )
            if not base["facts"]:
                raise failures.DiracTooLarge(
                    "bounded research context exceeds the byte ceiling even after "
                    "whole-fact omission",
                    details={"max_bytes": self.max_bytes, "size_bytes": len(encoded),
                             "omitted_fact_count": omitted})
            omitted_fact = base["facts"].pop()
            omitted += 1
            base["truncation"] = {
                "applied": True, "omitted_fact_count": omitted,
                "omitted_fact_ids": [
                    *base["truncation"]["omitted_fact_ids"],
                    str(omitted_fact["fact_id"]),
                ],
                "policy": "research-context-v1",
            }

    def _validate(self, document: Mapping[str, Any]) -> None:
        errors = sorted(self.validator.iter_errors(document), key=lambda error: list(error.path))
        if errors:
            first = errors[0]
            raise failures.DiracInternal(ValueError(
                "deterministic FEP context violates its frozen schema at "
                f"{list(first.path)}: {first.message}"))

    @staticmethod
    def _ordered_facts(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise failures.DiracInternal("FEP context facts must be an array")
        ranked = []
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise failures.DiracInternal("FEP context fact must be an object")
            priority = int(item.get("_priority", 0))
            fact = {key: val for key, val in item.items() if key != "_priority"}
            ranked.append((-priority, str(fact.get("fact_id") or ""), index, fact))
        ranked.sort()
        return [item[3] for item in ranked]

    @staticmethod
    def _budget(value: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "reasoner_calls": max(0, int(value.get("reasoner_calls", 0))),
            "fep_runsets": max(0, int(value.get("fep_runsets", 0))),
            "gpu_hours": value.get("gpu_hours"),
            "external_cost": value.get("external_cost"),
        }
