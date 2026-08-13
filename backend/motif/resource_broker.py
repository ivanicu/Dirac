"""Atomic host/campaign resource leases with fencing.

Kueue answers when a Kubernetes workload may run.  This broker separately protects
the host and campaign budgets that Kueue cannot see: VRAM, scratch, persistent growth,
process/SCF slots and campaign credits.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import threading
from typing import Any, Callable, Mapping
from uuid import uuid4


RESOURCE_DIMENSIONS = (
    "cpu_cores", "ram_bytes", "gpus", "gpu_vram_bytes", "scratch_bytes",
    "persistent_growth_bytes", "process_slots", "scf_slots", "campaign_credits",
)


@dataclass(frozen=True)
class ResourceLease:
    lease_id: str
    owner: str
    campaign_id: str
    request: Mapping[str, float]
    fencing_token: int
    expires_at: datetime
    heartbeat_at: datetime
    backend: str
    state: str = "active"
    actual: Mapping[str, float] = field(default_factory=dict)


class InsufficientCapacity(RuntimeError):
    def __init__(self, deficits: Mapping[str, float]):
        super().__init__(f"insufficient capacity: {dict(deficits)}")
        self.deficits = dict(deficits)


class AtomicResourceBroker:
    def __init__(self, observed_capacity: Mapping[str, float], *,
                 external_usage: Mapping[str, float] | None = None) -> None:
        self._capacity = _vector(observed_capacity)
        self._external = _vector(external_usage or {})
        self._leases: dict[str, ResourceLease] = {}
        self._owner_tokens: dict[str, int] = {}
        self._lock = threading.RLock()

    def available(self, *, now: datetime | None = None) -> dict[str, float]:
        instant = now or datetime.now(timezone.utc)
        with self._lock:
            self._expire(instant)
            reserved = {key: 0.0 for key in RESOURCE_DIMENSIONS}
            for lease in self._leases.values():
                if lease.state == "active":
                    for key, value in lease.request.items():
                        reserved[key] += float(value)
            return {key: max(0.0, self._capacity[key] - self._external[key] - reserved[key])
                    for key in RESOURCE_DIMENSIONS}

    def acquire(self, owner: str, campaign_id: str, request: Mapping[str, float], *,
                ttl_seconds: float, backend: str,
                now: datetime | None = None) -> ResourceLease:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        demand = _vector(request)
        instant = now or datetime.now(timezone.utc)
        with self._lock:
            available = self.available(now=instant)
            deficits = {key: demand[key] - available[key] for key in RESOURCE_DIMENSIONS
                        if demand[key] > available[key]}
            if deficits:
                raise InsufficientCapacity(deficits)
            token = self._owner_tokens.get(owner, 0) + 1
            self._owner_tokens[owner] = token
            lease = ResourceLease(
                lease_id=str(uuid4()), owner=owner, campaign_id=campaign_id,
                request={key: value for key, value in demand.items() if value},
                fencing_token=token, expires_at=instant + timedelta(seconds=ttl_seconds),
                heartbeat_at=instant, backend=backend,
            )
            self._leases[lease.lease_id] = lease
            return lease

    def heartbeat(self, lease_id: str, fencing_token: int, *, ttl_seconds: float,
                  actual: Mapping[str, float] | None = None,
                  now: datetime | None = None) -> ResourceLease:
        instant = now or datetime.now(timezone.utc)
        with self._lock:
            lease = self._require_active(lease_id, fencing_token, instant)
            updated = replace(lease, heartbeat_at=instant,
                              expires_at=instant + timedelta(seconds=ttl_seconds),
                              actual=_vector(actual or lease.actual))
            self._leases[lease_id] = updated
            return updated

    def release(self, lease_id: str, fencing_token: int, *,
                actual: Mapping[str, float] | None = None,
                now: datetime | None = None) -> ResourceLease:
        instant = now or datetime.now(timezone.utc)
        with self._lock:
            lease = self._require_active(lease_id, fencing_token, instant)
            released = replace(lease, state="released", heartbeat_at=instant,
                               actual=_vector(actual or lease.actual))
            self._leases[lease_id] = released
            return released

    def _require_active(self, lease_id: str, token: int, now: datetime) -> ResourceLease:
        self._expire(now)
        lease = self._leases.get(lease_id)
        if lease is None or lease.state != "active" or lease.fencing_token != token:
            raise RuntimeError("stale or inactive resource lease")
        return lease

    def _expire(self, now: datetime) -> None:
        for identifier, lease in list(self._leases.items()):
            if lease.state == "active" and lease.expires_at <= now:
                self._leases[identifier] = replace(lease, state="expired")


class PostgresResourceBroker:
    """Cross-process resource admission serialized by a PostgreSQL advisory lock."""

    def __init__(self, connect: Callable[[], Any], observed_capacity: Mapping[str, float], *,
                 external_usage: Mapping[str, float] | None = None) -> None:
        self._connect = connect
        self._capacity = _vector(observed_capacity)
        self._external = _vector(external_usage or {})

    def acquire(self, owner: str, campaign_id: str | None,
                request: Mapping[str, float], *, ttl_seconds: float,
                backend: str) -> ResourceLease:
        from uuid import UUID

        UUID(str(owner))
        if campaign_id is not None:
            UUID(str(campaign_id))
        demand = _vector(request)
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('dirac-global-resource-broker'))")
            cur.execute(
                "UPDATE app.resource_lease SET state='expired',released_at=now() "
                "WHERE state='active' AND expires_at <= now()")
            cur.execute("SELECT request FROM app.resource_lease WHERE state='active' FOR UPDATE")
            reserved = {key: 0.0 for key in RESOURCE_DIMENSIONS}
            for (raw,) in cur.fetchall():
                for key, value in _vector(raw).items():
                    reserved[key] += value
            available = {key: max(0.0, self._capacity[key] - self._external[key]
                                  - reserved[key]) for key in RESOURCE_DIMENSIONS}
            deficits = {key: demand[key] - available[key] for key in RESOURCE_DIMENSIONS
                        if demand[key] > available[key]}
            if deficits:
                raise InsufficientCapacity(deficits)
            cur.execute(
                "SELECT coalesce(max(fencing_token),0)+1 FROM app.resource_lease "
                "WHERE owner_kind='job' AND owner_id=%s", (owner,))
            token = int(cur.fetchone()[0])
            lease_owner = f"dirac-resource-broker:{uuid4()}"
            cur.execute(
                "INSERT INTO app.resource_lease "
                "(owner_kind,owner_id,campaign_id,backend,request,fencing_token,lease_owner,"
                " expires_at,heartbeat_at) VALUES ('job',%s,%s,%s,%s,%s,%s,"
                " now()+(%s*interval '1 second'),now()) "
                "RETURNING id,expires_at,heartbeat_at",
                (owner, campaign_id, backend, __import__('json').dumps({
                    key: value for key, value in demand.items() if value}),
                 token, lease_owner, ttl_seconds))
            row = cur.fetchone()
        return ResourceLease(
            lease_id=str(row[0]), owner=owner, campaign_id=campaign_id or "",
            request={key: value for key, value in demand.items() if value},
            fencing_token=token, expires_at=row[1], heartbeat_at=row[2],
            backend=backend)

    def heartbeat(self, lease_id: str, fencing_token: int, *, ttl_seconds: float,
                  actual: Mapping[str, float] | None = None) -> ResourceLease:
        import json
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE app.resource_lease SET heartbeat_at=now(),"
                "expires_at=now()+(%s*interval '1 second'),actual_usage=%s "
                "WHERE id=%s AND fencing_token=%s AND state='active' AND expires_at>now() "
                "RETURNING owner_id,campaign_id,request,expires_at,heartbeat_at,backend,lease_owner",
                (ttl_seconds, json.dumps(dict(actual or {})), lease_id, fencing_token))
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("stale or inactive resource lease")
        return ResourceLease(str(lease_id), str(row[0]), str(row[1] or ""), row[2],
                             fencing_token, row[3], row[4], row[5], actual=actual or {})

    def release(self, lease_id: str, fencing_token: int, *,
                actual: Mapping[str, float] | None = None) -> ResourceLease:
        import json
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE app.resource_lease SET state='released',released_at=now(),"
                "actual_usage=%s WHERE id=%s AND fencing_token=%s AND state='active' "
                "RETURNING owner_id,campaign_id,request,expires_at,heartbeat_at,backend",
                (json.dumps(dict(actual or {})), lease_id, fencing_token))
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("stale or inactive resource lease")
        return ResourceLease(str(lease_id), str(row[0]), str(row[1] or ""), row[2],
                             fencing_token, row[3], row[4], row[5], state="released",
                             actual=actual or {})


def _vector(values: Mapping[str, float]) -> dict[str, float]:
    unknown = set(values) - set(RESOURCE_DIMENSIONS)
    if unknown:
        raise ValueError(f"unknown resource dimensions: {sorted(unknown)}")
    result = {key: float(values.get(key, 0)) for key in RESOURCE_DIMENSIONS}
    if any(value < 0 for value in result.values()):
        raise ValueError("resource values must be non-negative")
    return result


__all__ = ["AtomicResourceBroker", "PostgresResourceBroker", "ResourceLease", "InsufficientCapacity",
           "RESOURCE_DIMENSIONS"]
