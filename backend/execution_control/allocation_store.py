"""Durable PostgreSQL mirror for external scheduler allocations."""
from __future__ import annotations

import json
from typing import Any, Callable

from execution_control.protocol import AllocationStatus, EventPage, SchedulerAdapter


_DATABASE_STATES = {
    "created": "created",
    "submitted": "submitted",
    "pending": "pending",
    "suspended": "pending",
    "running": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "unknown": "lost",
    "lost": "lost",
}
_TERMINAL = {"succeeded", "failed", "cancelled", "lost"}


class PostgresAllocationStore:
    """Persist scheduler identity without making it Dirac's public Job identity."""

    def __init__(self, connect: Callable[[], Any], *, site: str) -> None:
        if not site:
            raise ValueError("site is required so scheduler identity is globally unambiguous")
        self._connect = connect
        self.site = site

    def record_submission(self, request: dict[str, Any],
                          status: AllocationStatus, *, backend: str) -> str:
        state = _database_state(status.state)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.execution_allocation "
                "(attempt_id,backend,site,scheduler_identifier,state,resource_request,"
                " placement,scheduler_summary,submitted_at,started_at,finished_at) "
                "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,now(),"
                " CASE WHEN %s IN ('running','succeeded','failed','cancelled','lost') THEN now() END,"
                " CASE WHEN %s IN ('succeeded','failed','cancelled','lost') THEN now() END) "
                "ON CONFLICT (backend,site,scheduler_identifier) DO UPDATE SET "
                "state=excluded.state, scheduler_summary=excluded.scheduler_summary,"
                "started_at=coalesce(app.execution_allocation.started_at,excluded.started_at),"
                "finished_at=coalesce(app.execution_allocation.finished_at,excluded.finished_at) "
                "WHERE app.execution_allocation.attempt_id=excluded.attempt_id "
                "RETURNING id",
                (
                    request["attempt_id"], backend, self.site, status.allocation_id, state,
                    json.dumps(request["resource_request"], sort_keys=True),
                    json.dumps(request["placement"], sort_keys=True),
                    json.dumps(status.scheduler_summary, sort_keys=True), state, state,
                ),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("scheduler allocation identity already belongs to another attempt")
        return str(row[0])

    def update_status(self, status: AllocationStatus, *, backend: str) -> bool:
        state = _database_state(status.state)
        terminal = state in _TERMINAL
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE app.execution_allocation SET state=%s,"
                " scheduler_summary=%s::jsonb,"
                " started_at=CASE WHEN %s='running' OR %s THEN "
                "   coalesce(started_at,submitted_at,now()) ELSE started_at END,"
                " finished_at=CASE WHEN %s THEN coalesce(finished_at,now()) ELSE NULL END "
                "WHERE backend=%s AND site=%s AND scheduler_identifier=%s "
                "AND state NOT IN ('succeeded','failed','cancelled','lost') RETURNING id",
                (state, json.dumps(status.scheduler_summary, sort_keys=True),
                 state, terminal, terminal, backend, self.site, status.allocation_id),
            )
            return cur.fetchone() is not None

    def active_scheduler_ids(self, *, backend: str) -> tuple[str, ...]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT scheduler_identifier FROM app.execution_allocation "
                "WHERE backend=%s AND site=%s AND state IN "
                "('created','submitted','pending','running') ORDER BY created_at,id",
                (backend, self.site),
            )
            return tuple(str(row[0]) for row in cur.fetchall())


class DurableSchedulerAdapter:
    """SchedulerAdapter decorator that mirrors every observable transition."""

    def __init__(self, adapter: SchedulerAdapter, store: PostgresAllocationStore) -> None:
        self.adapter = adapter
        self.store = store
        self.kind = adapter.kind

    def admit(self, request: dict[str, Any]):
        return self.adapter.admit(request)

    def submit(self, request: dict[str, Any]) -> AllocationStatus:
        status = self.adapter.submit(request)
        try:
            self.store.record_submission(request, status, backend=self.kind)
        except Exception:
            # Never leave expensive scheduler work running without its durable
            # Dirac allocation identity. Kubernetes deletion is idempotent.
            self.adapter.request_cancel(status.allocation_id, grace_seconds=0)
            raise
        return status

    def inspect(self, allocation_id: str) -> AllocationStatus:
        status = self.adapter.inspect(allocation_id)
        self.store.update_status(status, backend=self.kind)
        return status

    def request_cancel(self, allocation_id: str, *, grace_seconds: int) -> None:
        self.adapter.request_cancel(allocation_id, grace_seconds=grace_seconds)
        self.store.update_status(
            AllocationStatus(allocation_id, "cancelled", {
                "reason": "cancel_requested", "grace_seconds": grace_seconds,
            }),
            backend=self.kind,
        )

    def suspend(self, allocation_id: str) -> None:
        self.adapter.suspend(allocation_id)
        self.inspect(allocation_id)

    def resume(self, allocation_id: str) -> None:
        self.adapter.resume(allocation_id)
        self.inspect(allocation_id)

    def collect_events(self, cursor: str | None) -> EventPage:
        return self.adapter.collect_events(cursor)

    def reconcile(self, allocation_id: str) -> AllocationStatus:
        status = self.adapter.reconcile(allocation_id)
        self.store.update_status(status, backend=self.kind)
        return status

    def reconcile_active(self) -> tuple[AllocationStatus, ...]:
        return tuple(self.reconcile(allocation_id) for allocation_id in
                     self.store.active_scheduler_ids(backend=self.kind))


def _database_state(state: str) -> str:
    try:
        return _DATABASE_STATES[state]
    except KeyError as error:
        raise ValueError(f"unknown scheduler state {state!r}") from error
