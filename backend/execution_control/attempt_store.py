"""PostgreSQL Attempt ownership with transactional lease and fencing semantics."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from execution_control.leases import StaleAttemptError


@dataclass(frozen=True)
class ClaimedAttempt:
    attempt_id: str
    job_id: str
    attempt: int
    fencing_token: int
    lease_owner: str
    lease_expires_at: datetime


class PostgresAttemptStore:
    """All ownership transitions lock the semantic Job before choosing a token."""

    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    def claim(self, *, job_id: str, execution_digest: bytes, owner: str,
              lease_seconds: int) -> ClaimedAttempt:
        if len(execution_digest) != 32:
            raise ValueError("execution_digest must contain 32 bytes")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (job_id,))
            cur.execute(
                "SELECT id, attempt_no, fencing_token, lease_owner, lease_expires_at "
                "FROM app.job_attempt WHERE job_id=%s "
                "ORDER BY fencing_token DESC LIMIT 1 FOR UPDATE", (job_id,))
            current = cur.fetchone()
            if current and current[4] is not None:
                cur.execute("SELECT %s::timestamptz > now()", (current[4],))
                if cur.fetchone()[0]:
                    raise RuntimeError(f"job {job_id} already has a live lease")
                cur.execute(
                    "UPDATE app.job_attempt SET state='lost', finished_at=coalesce(started_at, now()) "
                    "WHERE id=%s AND state NOT IN ('succeeded','failed','cancelled','superseded')",
                    (current[0],))
            attempt = 1 if current is None else int(current[1]) + 1
            token = 1 if current is None else int(current[2]) + 1
            cur.execute(
                "INSERT INTO app.job_attempt "
                "(job_id, attempt_no, state, execution_digest, fencing_token, lease_owner, "
                " lease_expires_at, heartbeat_at, started_at) "
                "VALUES (%s,%s,'running',%s,%s,%s,now()+(%s * interval '1 second'),now(),now()) "
                "RETURNING id, lease_expires_at",
                (job_id, attempt, execution_digest, token, owner, lease_seconds))
            row = cur.fetchone()
        return ClaimedAttempt(str(row[0]), job_id, attempt, token, owner, row[1])

    def renew(self, claim: ClaimedAttempt, *, lease_seconds: int) -> ClaimedAttempt:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE app.job_attempt SET lease_expires_at=now()+(%s * interval '1 second'), "
                "heartbeat_at=now() WHERE id=%s AND lease_owner=%s AND fencing_token=%s "
                "AND lease_expires_at > now() AND state IN ('running','checkpointing','cancel_requested') "
                "RETURNING lease_expires_at",
                (lease_seconds, claim.attempt_id, claim.lease_owner, claim.fencing_token))
            row = cur.fetchone()
            if row is None:
                raise StaleAttemptError("Attempt renewal rejected by current fencing state")
        return ClaimedAttempt(claim.attempt_id, claim.job_id, claim.attempt,
                              claim.fencing_token, claim.lease_owner, row[0])

    def complete(self, claim: ClaimedAttempt, *, state: str,
                 event_key: str, payload: dict[str, Any]) -> bool:
        if state not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("terminal state required")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE app.job_attempt SET state=%s, finished_at=now(), lease_owner=NULL, "
                "lease_expires_at=NULL, heartbeat_at=NULL WHERE id=%s AND lease_owner=%s AND fencing_token=%s "
                "AND lease_expires_at > now() AND state IN "
                "('running','checkpointing','cancel_requested') RETURNING job_id",
                (state, claim.attempt_id, claim.lease_owner, claim.fencing_token))
            row = cur.fetchone()
            if row is None:
                # Duplicate completion of the same terminal Attempt is idempotent.
                cur.execute("SELECT state, fencing_token FROM app.job_attempt WHERE id=%s",
                            (claim.attempt_id,))
                existing = cur.fetchone()
                if existing == (state, claim.fencing_token):
                    return False
                raise StaleAttemptError("terminal completion rejected by fencing barrier")
            cur.execute(
                "INSERT INTO app.outbox_event "
                "(event_key, aggregate_kind, aggregate_id, event_type, payload) "
                "VALUES (%s,'job_attempt',%s,%s,%s) ON CONFLICT (event_key) DO NOTHING",
                (event_key, claim.attempt_id, f"attempt.{state}", json.dumps(payload)))
        return True
