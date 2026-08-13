"""Authoritative, channel-neutral action offer/preview/commit reference protocol.

This module deliberately owns no HTML or route concepts. Human, CLI, agent, and
automation projections can all call it. Production adapters must replace the
in-memory ledgers with a transactionally durable repository; the state machine,
signed preconditions, reauthorization, version checks, and receipt semantics are
the executable v2.1 reference contract.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping


class ActionRefusal(Exception):
    code = "ACTION_REFUSED"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class Unauthorized(ActionRefusal):
    code = "UNAUTHORIZED"


class StalePreview(ActionRefusal):
    code = "STALE_PREVIEW"


class InvalidPreview(ActionRefusal):
    code = "INVALID_PREVIEW"


class IdempotencyConflict(ActionRefusal):
    code = "IDEMPOTENCY_CONFLICT"


@dataclass(frozen=True)
class ApplicationActionDefinition:
    id: str
    version: int
    intent: str
    input_schema: Mapping[str, Any]
    consequence_class: str
    authorization_policy: str
    precondition_policy: str
    idempotency_policy: str
    conflict_policy: str
    transaction_policy: str
    receipt_schema: Mapping[str, Any]

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"


@dataclass(frozen=True)
class ActionImplementation:
    definition: ApplicationActionDefinition
    preview: Callable[[dict[str, Any]], dict[str, Any]]
    commit: Callable[[dict[str, Any]], dict[str, Any]]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class ActionAuthority:
    """Executable reference authority with injected authorization and versions."""

    def __init__(self, *, secret: bytes,
                 authorize: Callable[[dict[str, str], str, str, list[dict[str, str]]], bool],
                 read_versions: Callable[[list[dict[str, str]]], Mapping[str, int]],
                 now: Callable[[], float] = time.time,
                 token_ttl_seconds: int = 300) -> None:
        if len(secret) < 32:
            raise ValueError("action preview signing secret must be at least 32 bytes")
        self._secret = secret
        self._authorize = authorize
        self._read_versions = read_versions
        self._now = now
        self._ttl = token_ttl_seconds
        self._actions: dict[str, ActionImplementation] = {}
        self._offers: dict[str, dict[str, Any]] = {}
        self._previews: dict[str, dict[str, Any]] = {}
        self._operations: dict[str, tuple[str, dict[str, Any]]] = {}

    def register(self, implementation: ActionImplementation) -> None:
        key = implementation.definition.key
        if key in self._actions:
            raise ValueError(f"duplicate application action {key}")
        self._actions[key] = implementation

    def offer(self, action_key: str, *, actor: dict[str, str],
              subjects: list[dict[str, str]], selection_digest: str | None = None,
              permission_envelope: str) -> dict[str, Any]:
        implementation = self._actions.get(action_key)
        if implementation is None:
            # Unknown and unauthorized are intentionally indistinguishable at this boundary.
            raise Unauthorized("action is not available")
        if not self._authorize(actor, action_key, "offer", subjects):
            raise Unauthorized("action is not available")
        now = self._now()
        offer = {
            "offer_id": str(uuid.uuid4()), "action": action_key,
            "actor": dict(actor), "subjects": [dict(item) for item in subjects],
            "selection_digest": selection_digest,
            "permission_envelope": permission_envelope,
            "preconditions": implementation.definition.precondition_policy,
            "expires_at": now + self._ttl,
        }
        self._offers[offer["offer_id"]] = offer
        return dict(offer)

    def preview(self, offer_id: str, *, input: dict[str, Any]) -> dict[str, Any]:
        offer = self._offers.get(offer_id)
        if offer is None or offer["expires_at"] < self._now():
            raise InvalidPreview("offer is missing or expired")
        if not self._authorize(offer["actor"], offer["action"], "preview", offer["subjects"]):
            raise Unauthorized("action is not available")
        implementation = self._actions[offer["action"]]
        source_versions = dict(self._read_versions(offer["subjects"]))
        proposed = implementation.preview({
            "actor": offer["actor"], "subjects": offer["subjects"],
            "input": input, "source_versions": source_versions,
        })
        now = self._now()
        preview_id = str(uuid.uuid4())
        token_payload = {
            "preview_id": preview_id, "offer_id": offer_id,
            "action": offer["action"], "actor": offer["actor"],
            "subjects": offer["subjects"], "source_versions": source_versions,
            "input_digest": _digest(input), "expires_at": now + self._ttl,
        }
        token = self._sign(token_payload)
        preview = {
            **token_payload, "precondition_token": token,
            "proposed_effects": proposed.get("proposed_effects", []),
            "warnings": proposed.get("warnings", []),
            "required_acknowledgements": proposed.get("required_acknowledgements", []),
        }
        self._previews[preview_id] = preview
        return dict(preview)

    def commit(self, precondition_token: str, *, input: dict[str, Any],
               idempotency_key: str, attempt_id: str,
               acknowledgements: list[str] | None = None) -> dict[str, Any]:
        token = self._verify(precondition_token)
        preview = self._previews.get(token.get("preview_id", ""))
        if preview is None:
            raise InvalidPreview("preview is missing")
        input_digest = _digest(input)
        operation_digest = _digest({
            "action": token["action"], "input": input,
            "preview": token["preview_id"], "attempt_id": attempt_id,
        })
        previous = self._operations.get(idempotency_key)
        if previous:
            if previous[0] != operation_digest:
                raise IdempotencyConflict(
                    "idempotency key was already used for a different operation")
            return dict(previous[1])
        if token.get("expires_at", 0) < self._now():
            raise InvalidPreview("preview is expired")
        if input_digest != token.get("input_digest"):
            raise InvalidPreview("commit input differs from previewed input")
        if not self._authorize(token["actor"], token["action"], "commit", token["subjects"]):
            raise Unauthorized("action is not available")
        required = set(preview.get("required_acknowledgements", []))
        accepted = set(acknowledgements or [])
        missing_ack = sorted(required - accepted)
        if missing_ack:
            raise InvalidPreview("required acknowledgement is missing",
                                 details={"missing": missing_ack})
        current_versions = dict(self._read_versions(token["subjects"]))
        if current_versions != token["source_versions"]:
            raise StalePreview("source objects changed after preview", details={
                "preview_versions": token["source_versions"],
                "current_versions": current_versions,
                "diff": self._version_diff(token["source_versions"], current_versions),
            })
        implementation = self._actions[token["action"]]
        result = implementation.commit({
            "actor": token["actor"], "subjects": token["subjects"],
            "input": input, "source_versions": current_versions,
            "attempt_id": attempt_id, "idempotency_key": idempotency_key,
        })
        receipt = {
            "operation_id": result.get("operation_id", str(uuid.uuid4())),
            "action": token["action"], "attempt_id": attempt_id,
            "actor": token["actor"], "status": result.get("status", "committed"),
            "applied_effects": result.get("applied_effects", []),
            "failed_effects": result.get("failed_effects", []),
            "compensation": result.get("compensation", []),
            "output_refs": result.get("output_refs", []),
            "source_versions": current_versions,
            "committed_at": self._now(),
            "recovery_actions": result.get("recovery_actions", []),
        }
        self._operations[idempotency_key] = (operation_digest, receipt)
        return dict(receipt)

    def _sign(self, payload: dict[str, Any]) -> str:
        body = base64.urlsafe_b64encode(_canonical(payload)).rstrip(b"=")
        signature = hmac.new(self._secret, body, hashlib.sha256).digest()
        return f"{body.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"

    def _verify(self, token: str) -> dict[str, Any]:
        try:
            body_text, signature_text = token.split(".", 1)
            body = body_text.encode()
            padded = signature_text + "=" * (-len(signature_text) % 4)
            signature = base64.urlsafe_b64decode(padded)
            expected = hmac.new(self._secret, body, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise InvalidPreview("preview token signature is invalid")
            body_padded = body_text + "=" * (-len(body_text) % 4)
            return json.loads(base64.urlsafe_b64decode(body_padded))
        except ActionRefusal:
            raise
        except Exception as exc:  # noqa: BLE001
            raise InvalidPreview("preview token is malformed") from exc

    @staticmethod
    def _version_diff(before: Mapping[str, int], after: Mapping[str, int]) -> list[dict[str, Any]]:
        return [{"subject": item, "before": before.get(item), "after": after.get(item)}
                for item in sorted(set(before) | set(after))
                if before.get(item) != after.get(item)]
