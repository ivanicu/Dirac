"""Lease and fencing semantics independent of any scheduler implementation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Lock


class StaleAttemptError(RuntimeError):
    pass


@dataclass(frozen=True)
class AttemptLease:
    job_id: str
    attempt: int
    fencing_token: int
    owner: str
    expires_at: datetime
    state: str = "running"


class InMemoryLeaseStore:
    """Reference state machine used by adapters and fault-injection tests."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._current: dict[str, AttemptLease] = {}

    def claim(
        self, job_id: str, owner: str, *, lease_seconds: float,
        now: datetime | None = None,
    ) -> AttemptLease:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        instant = now or datetime.now(timezone.utc)
        with self._lock:
            current = self._current.get(job_id)
            if current is not None and current.state == "running" and current.expires_at > instant:
                raise RuntimeError(f"job {job_id} already has a live lease")
            lease = AttemptLease(
                job_id=job_id,
                attempt=1 if current is None else current.attempt + 1,
                fencing_token=1 if current is None else current.fencing_token + 1,
                owner=owner,
                expires_at=instant + timedelta(seconds=lease_seconds),
            )
            self._current[job_id] = lease
            return lease

    def renew(
        self, lease: AttemptLease, *, lease_seconds: float,
        now: datetime | None = None,
    ) -> AttemptLease:
        instant = now or datetime.now(timezone.utc)
        with self._lock:
            current = self._require_current(lease)
            if current.expires_at <= instant:
                raise StaleAttemptError("lease expired before renewal")
            renewed = replace(current, expires_at=instant + timedelta(seconds=lease_seconds))
            self._current[lease.job_id] = renewed
            return renewed

    def complete(self, lease: AttemptLease, *, now: datetime | None = None) -> AttemptLease:
        instant = now or datetime.now(timezone.utc)
        with self._lock:
            current = self._require_current(lease)
            if current.expires_at <= instant:
                raise StaleAttemptError("expired attempt cannot commit a terminal result")
            completed = replace(current, state="succeeded", expires_at=instant)
            self._current[lease.job_id] = completed
            return completed

    def current(self, job_id: str) -> AttemptLease | None:
        return self._current.get(job_id)

    def _require_current(self, lease: AttemptLease) -> AttemptLease:
        current = self._current.get(lease.job_id)
        if current is None or current.fencing_token != lease.fencing_token or current.owner != lease.owner:
            raise StaleAttemptError(
                f"stale attempt token {lease.fencing_token} for job {lease.job_id}"
            )
        if current.state != "running":
            raise StaleAttemptError(f"attempt is already {current.state}")
        return current
