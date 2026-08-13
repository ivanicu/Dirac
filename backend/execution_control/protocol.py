"""Framework-neutral ExecutionRequest and scheduler protocol."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any, Protocol

import failures
from contracts.validation import violations

ROOT = Path(__file__).resolve().parents[2]
EXECUTION_SCHEMA = json.loads(
    (ROOT / "contracts/execution/execution-request.schema.json").read_text(encoding="utf-8")
)


@dataclass(frozen=True)
class AllocationStatus:
    allocation_id: str
    state: str
    scheduler_summary: dict[str, Any]


@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    code: str
    reason: str
    available: dict[str, Any]


@dataclass(frozen=True)
class EventPage:
    events: tuple[dict[str, Any], ...]
    cursor: str | None


class SchedulerAdapter(Protocol):
    kind: str

    def admit(self, request: dict[str, Any]) -> AdmissionDecision: ...
    def submit(self, request: dict[str, Any]) -> AllocationStatus: ...
    def inspect(self, allocation_id: str) -> AllocationStatus: ...
    def request_cancel(self, allocation_id: str, *, grace_seconds: int) -> None: ...
    def suspend(self, allocation_id: str) -> None: ...
    def resume(self, allocation_id: str) -> None: ...
    def collect_events(self, cursor: str | None) -> EventPage: ...
    def reconcile(self, allocation_id: str) -> AllocationStatus: ...


def validate_execution_request(request: dict[str, Any]) -> dict[str, Any]:
    errors = violations(EXECUTION_SCHEMA, request)
    if errors:
        first = errors[0]
        raise failures.DiracInvalidParameters(
            f"ExecutionRequest {first.pointer or '(root)'} {first.message}",
            details={"violations": [error.to_dict() for error in errors[:8]]},
        )
    return request


class CancellationToken:
    """Thread-safe cooperative cancellation shared with a running handler."""

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._reason: str | None = None

    def request(self, reason: str = "requested") -> bool:
        with self._lock:
            first = not self._event.is_set()
            if first:
                self._reason = reason
                self._event.set()
            return first

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def check(self) -> None:
        if self.requested:
            raise failures.DiracCancelled(
                f"execution cancelled: {self.reason}",
                details={"reason": self.reason},
            )
