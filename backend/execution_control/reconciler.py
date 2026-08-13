"""Scheduler-neutral recovery decisions after process/control-plane restart."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


@dataclass(frozen=True)
class ReconciliationAction:
    allocation_id: str
    action: str
    reason: str


TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
ACTIVE = frozenset({"submitted", "starting", "running", "checkpointing"})


def reconcile_allocations(allocations: Iterable[dict[str, Any]], adapter: Any,
                          *, now: datetime | None = None) -> list[ReconciliationAction]:
    """Compare durable allocation truth with scheduler truth without mutating either."""
    instant = now or datetime.now(timezone.utc)
    actions: list[ReconciliationAction] = []
    for row in allocations:
        allocation_id = row["allocation_id"]
        remote = adapter.inspect(allocation_id)
        local_state = row["state"]
        if local_state in TERMINAL and remote.state in ACTIVE:
            actions.append(ReconciliationAction(allocation_id, "cancel", "terminal_local_orphan"))
            continue
        lease_expires = row.get("lease_expires_at")
        if (local_state in ACTIVE and lease_expires is not None
                and lease_expires <= instant and remote.state in TERMINAL):
            actions.append(ReconciliationAction(allocation_id, "finalize", "expired_lease_remote_terminal"))
        elif local_state in ACTIVE and remote.state == "unknown":
            actions.append(ReconciliationAction(allocation_id, "retry", "scheduler_allocation_missing"))
        elif local_state != remote.state:
            actions.append(ReconciliationAction(allocation_id, "synchronize", "state_drift"))
    return actions
