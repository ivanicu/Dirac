"""PostgreSQL Attempt ownership with transactional lease and fencing semantics."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from execution_control.leases import StaleAttemptError
from execution_control.completion import validate_output_manifest


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

    def commit_success(self, claim: ClaimedAttempt, *, manifest: dict[str, Any],
                       manifest_artifact_id: str, required_roles: list[str],
                       artifact_reader: Any, event_key: str) -> bool:
        """Atomically publish one terminal scientific manifest for a LogicalJob.

        Workers may execute at least once.  Only the current fenced Attempt can insert
        ``app.artifact_commit`` and transition terminal state.  A replay of the exact
        same commit is idempotent; a late or conflicting result is rejected.
        """
        expected_digest = manifest.get("execution_digest")
        validate_output_manifest(
            manifest, expected_execution_digest=expected_digest,
            expected_fencing_token=claim.fencing_token,
            required_roles=required_roles, artifact_reader=artifact_reader,
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT job_id,state,execution_digest,fencing_token,lease_owner,lease_expires_at "
                "FROM app.job_attempt WHERE id=%s FOR UPDATE", (claim.attempt_id,))
            row = cur.fetchone()
            if row is None:
                raise StaleAttemptError("Attempt no longer exists")
            database_digest = "sha256:" + bytes(row[2]).hex()
            if database_digest != expected_digest:
                raise StaleAttemptError("manifest execution identity differs from Attempt")
            cur.execute(
                "SELECT attempt_id,manifest_artifact_id,fencing_token "
                "FROM app.artifact_commit WHERE logical_job_id=%s FOR UPDATE",
                (claim.job_id,))
            existing = cur.fetchone()
            if existing is not None:
                if (str(existing[0]), str(existing[1]), int(existing[2])) == (
                        claim.attempt_id, manifest_artifact_id, claim.fencing_token):
                    return False
                raise StaleAttemptError(
                    "LogicalJob already has a different terminal artifact commit")
            if (row[1] != "running" or int(row[3]) != claim.fencing_token
                    or row[4] != claim.lease_owner or row[5] is None):
                raise StaleAttemptError("terminal artifact commit rejected by fencing barrier")
            cur.execute("SELECT %s::timestamptz > now()", (row[5],))
            if not cur.fetchone()[0]:
                raise StaleAttemptError("terminal artifact commit lease has expired")
            cur.execute(
                "INSERT INTO app.artifact_commit "
                "(logical_job_id,attempt_id,fencing_token,manifest_artifact_id,terminal_event_key) "
                "VALUES (%s,%s,%s,%s,%s)",
                (claim.job_id, claim.attempt_id, claim.fencing_token,
                 manifest_artifact_id, event_key))
            cur.execute(
                "UPDATE app.job_attempt SET state='succeeded',finished_at=now(),"
                "lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL WHERE id=%s",
                (claim.attempt_id,))
            cur.execute(
                "INSERT INTO design.motif_method_outcome "
                "(attempt_id,execution_state,manifest_artifact_id) "
                "VALUES (%s,'succeeded',%s) ON CONFLICT (attempt_id) DO NOTHING",
                (claim.attempt_id, manifest_artifact_id))
            cur.execute(
                "INSERT INTO app.outbox_event "
                "(event_key,aggregate_kind,aggregate_id,event_type,payload) "
                "VALUES (%s,'job_attempt',%s,'attempt.succeeded',%s) "
                "ON CONFLICT (event_key) DO NOTHING",
                (event_key, claim.attempt_id, json.dumps({
                    "logical_job_id": claim.job_id,
                    "attempt_id": claim.attempt_id,
                    "fencing_token": claim.fencing_token,
                    "manifest_artifact_id": manifest_artifact_id,
                    "execution_digest": expected_digest,
                })))
        return True
