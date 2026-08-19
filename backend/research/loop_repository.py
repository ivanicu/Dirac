"""Durable checkpoint and audit ledger for the AI-guided FEP research loop.

The repository owns persistence mechanics only.  It never calls a provider or a
scientific command.  Every semantic state transition updates the mutable
checkpoint and appends its audit event in the same PostgreSQL transaction.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

import failures


LOOP_STATES = frozenset({
    "active", "waiting_approval", "blocked", "paused", "completed",
    "cancelled", "failed",
})
LOOP_STAGES = frozenset({
    "bootstrap", "snapshot_context", "reason", "validate_proposal",
    "select_action", "prepare_action", "await_approval", "dispatch",
    "wait_job", "observe", "refresh", "guard", "completed",
})
TERMINAL_STATES = frozenset({"completed", "cancelled", "failed"})
DATA_CLASSIFICATIONS = frozenset({
    "public", "internal", "partner_confidential", "restricted", "regulated",
})
ARTIFACT_ROLES = frozenset({
    "research.context_snapshot", "research.proposal", "research.action_preview",
    "research.action_receipt", "research.loop_summary", "research.followup_draft",
})
AUTONOMY_CLASSES = frozenset({"A0", "A1", "A2", "A3"})


@dataclass(frozen=True)
class LoopClaim:
    run_id: str
    version: int
    lease_owner: str
    lease_expires_at: datetime
    state: Mapping[str, Any]


def stage_request_key(run_id: str, iteration: int, stage: str, attempt: int) -> str:
    """Return the sole durable idempotency key shape allowed for loop stages."""
    if not run_id or iteration < 0 or stage not in LOOP_STAGES or attempt < 0:
        raise ValueError("invalid research-loop stage request-key components")
    return f"research-loop:{run_id}:{iteration}:{stage}:{attempt}"


def _actor(value: Mapping[str, Any]) -> tuple[str, str]:
    kind = str(value.get("kind") or "")
    actor_id = str(value.get("id") or "").strip()
    if kind not in {"human", "agent", "service"} or not actor_id:
        raise failures.DiracInvalidParameters(
            "research loop requires a named human, agent, or service actor")
    return kind, actor_id


def _digest(value: bytes, name: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        raise failures.DiracInvalidParameters(f"{name} must contain 32 bytes")
    return value


def _json(value: Mapping[str, Any], name: str) -> str:
    if not isinstance(value, Mapping):
        raise failures.DiracInvalidParameters(f"{name} must be an object")
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise failures.DiracInvalidParameters(
            f"{name} must be canonical JSON", details={"error": str(exc)}) from exc


_STATE_COLUMNS = (
    "run_id", "request_key", "program_id", "campaign_id", "actor_kind",
    "actor_id", "state", "stage", "iteration", "version", "intent",
    "autonomy_class", "provider_profile_id", "provider_profile_digest",
    "prompt_release_id", "prompt_release_digest", "action_catalog_digest",
    "data_classification", "policy", "budget_remaining", "budget_spent",
    "context_artifact_id", "context_digest", "proposal_artifact_id",
    "proposal_context_digest", "pending_action", "stage_jobs",
    "stage_attempts", "outputs", "attention", "next_wake_at", "lease_owner",
    "lease_expires_at", "created_at", "updated_at", "finished_at",
)
_STATE_SELECT = ",".join(_STATE_COLUMNS)
_JSON_FIELDS = frozenset({
    "policy", "budget_remaining", "budget_spent", "pending_action", "stage_jobs",
    "stage_attempts", "outputs", "attention",
})
_DIGEST_FIELDS = frozenset({
    "provider_profile_digest", "prompt_release_digest", "action_catalog_digest",
    "context_digest", "proposal_context_digest",
})


def _state(row: Any) -> dict[str, Any]:
    if row is None:
        raise failures.DiracNotFound("research loop does not exist")
    values = dict(zip(_STATE_COLUMNS, row))
    for key in _JSON_FIELDS:
        value = values[key]
        values[key] = None if value is None else dict(value)
    for key in _DIGEST_FIELDS:
        value = values[key]
        values[key] = None if value is None else "sha256:" + bytes(value).hex()
    for key in ("run_id", "program_id", "campaign_id", "context_artifact_id",
                "proposal_artifact_id"):
        value = values[key]
        values[key] = None if value is None else str(value)
    for key in ("next_wake_at", "lease_expires_at", "created_at", "updated_at",
                "finished_at"):
        value = values[key]
        values[key] = None if value is None else value.isoformat()
    return values


class ResearchLoopRepository:
    """PostgreSQL repository with lease, optimistic-version, and audit semantics."""

    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    def create(
        self, *, request_key: str, program_id: str, campaign_id: str,
        actor: Mapping[str, Any], intent: str, autonomy_class: str,
        provider_profile_id: str, provider_profile_digest: bytes,
        prompt_release_id: str, prompt_release_digest: bytes,
        action_catalog_digest: bytes, data_classification: str,
        policy: Mapping[str, Any], budget_remaining: Mapping[str, Any],
    ) -> dict[str, Any]:
        kind, actor_id = _actor(actor)
        if not request_key.strip() or not intent.strip():
            raise failures.DiracInvalidParameters(
                "request_key and intent must be non-empty")
        if autonomy_class not in AUTONOMY_CLASSES:
            raise failures.DiracInvalidParameters("unsupported autonomy_class")
        if data_classification not in DATA_CLASSIFICATIONS:
            raise failures.DiracInvalidParameters("unsupported data classification")
        provider_digest = _digest(provider_profile_digest, "provider_profile_digest")
        prompt_digest = _digest(prompt_release_digest, "prompt_release_digest")
        catalog_digest = _digest(action_catalog_digest, "action_catalog_digest")
        policy_json = _json(policy, "policy")
        budget_json = _json(budget_remaining, "budget_remaining")
        create_identity = {
            "request_key": request_key, "program_id": program_id,
            "campaign_id": campaign_id, "actor_kind": kind, "actor_id": actor_id,
            "intent": intent, "autonomy_class": autonomy_class,
            "provider_profile_id": provider_profile_id,
            "provider_profile_digest": "sha256:" + provider_digest.hex(),
            "prompt_release_id": prompt_release_id,
            "prompt_release_digest": "sha256:" + prompt_digest.hex(),
            "action_catalog_digest": "sha256:" + catalog_digest.hex(),
            "data_classification": data_classification,
            "policy": dict(policy), "budget": dict(budget_remaining),
        }
        create_digest = "sha256:" + hashlib.sha256(
            _json(create_identity, "create identity").encode()).hexdigest()
        lock_key = f"research-loop:{kind}:{actor_id}:{request_key}"

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock_key,))
            cur.execute(
                f"SELECT {_STATE_SELECT} FROM app.research_loop_state "
                "WHERE actor_kind=%s AND actor_id=%s AND request_key=%s",
                (kind, actor_id, request_key),
            )
            existing = cur.fetchone()
            if existing is not None:
                result = _state(existing)
                cur.execute(
                    "SELECT payload->>'create_request_digest' "
                    "FROM app.research_loop_event WHERE run_id=%s "
                    "AND event_type='loop_created' ORDER BY sequence LIMIT 1",
                    (result["run_id"],),
                )
                recorded = cur.fetchone()
                if recorded is None or recorded[0] != create_digest:
                    raise failures.DiracIdempotencyConflict(
                        "request_key already identifies a different research loop",
                        details={"run_id": result["run_id"]})
                cur.execute(
                    "SELECT mission_id::text FROM app.run WHERE id=%s",
                    (result["run_id"],),
                )
                result["mission_id"] = cur.fetchone()[0]
                result["created"] = False
                return result

            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"research-loop-campaign:{campaign_id}",),
            )
            cur.execute(
                "SELECT c.program_id,c.status,c.created_by_kind,c.created_by_id "
                "FROM design.campaign c WHERE c.id=%s FOR SHARE", (campaign_id,))
            campaign = cur.fetchone()
            if campaign is None:
                cur.execute(
                    "SELECT status,state,created_by_kind,created_by_id "
                    "FROM app.rbfe_campaign WHERE id=%s FOR SHARE", (campaign_id,),
                )
                rbfe_campaign = cur.fetchone()
                if rbfe_campaign is None or (
                        str(rbfe_campaign[2]), str(rbfe_campaign[3])) != (kind, actor_id):
                    raise failures.DiracNotFound(
                        "Campaign does not exist", details={"campaign_id": campaign_id})
                if str(rbfe_campaign[0]) != "planned":
                    raise failures.DiracInvalidParameters(
                        "research loop requires a planned FEP Campaign",
                        details={"campaign_id": campaign_id,
                                 "campaign_status": str(rbfe_campaign[0])})
                cur.execute("SELECT id FROM design.project WHERE id=%s FOR SHARE", (program_id,))
                if cur.fetchone() is None:
                    raise failures.DiracNotFound(
                        "Program does not exist", details={"program_id": program_id})
                client_state = dict(rbfe_campaign[1]).get("client_state") or {}
                label = str(client_state.get("name") or client_state.get("campaign_name")
                            or f"FEP {campaign_id[:8]}").strip()
                cur.execute(
                    "INSERT INTO design.campaign "
                    "(id,program_id,name,objective,status,created_by_kind,created_by_id) "
                    "VALUES (%s,%s,%s,%s,'active',%s,%s)",
                    (campaign_id, program_id, f"{label} · {campaign_id[:8]}",
                     intent, kind, actor_id),
                )
                campaign = (program_id, "active", kind, actor_id)
            if str(campaign[0]) != str(program_id):
                raise failures.DiracInvalidParameters(
                    "Campaign does not belong to the requested Program")
            if (str(campaign[2]), str(campaign[3])) != (kind, actor_id):
                raise failures.DiracNotFound("Campaign does not exist")

            cur.execute(
                "INSERT INTO app.mission "
                "(program_id,objective,state,autonomy_class,actor_kind,actor_id) "
                "VALUES (%s,%s,'active',%s,%s,%s) RETURNING id",
                (program_id, intent, autonomy_class, kind, actor_id),
            )
            mission_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO app.run "
                "(mission_id,attempt,state,started_at,actor_kind,actor_id) "
                "VALUES (%s,1,'active',now(),%s,%s) RETURNING id",
                (mission_id, kind, actor_id),
            )
            run_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO app.research_loop_state "
                "(run_id,request_key,program_id,campaign_id,actor_kind,actor_id,"
                "state,stage,intent,autonomy_class,provider_profile_id,"
                "provider_profile_digest,prompt_release_id,prompt_release_digest,"
                "action_catalog_digest,data_classification,policy,budget_remaining) "
                "VALUES (%s,%s,%s,%s,%s,%s,'active','bootstrap',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, request_key, program_id, campaign_id, kind, actor_id,
                 intent, autonomy_class, provider_profile_id, provider_digest,
                 prompt_release_id, prompt_digest, catalog_digest,
                 data_classification, policy_json, budget_json),
            )
            self._append_event(
                cur, str(run_id), "loop_created", "bootstrap", kind, actor_id,
                {"request_key": request_key, "campaign_id": campaign_id,
                 "program_id": program_id,
                 "create_request_digest": create_digest},
            )
            cur.execute(
                f"SELECT {_STATE_SELECT} FROM app.research_loop_state WHERE run_id=%s",
                (run_id,),
            )
            result = _state(cur.fetchone())
            result["mission_id"] = str(mission_id)
            result["created"] = True
            return result

    def get(self, run_id: str, *, actor: Mapping[str, Any] | None = None,
            for_update: bool = False) -> dict[str, Any]:
        suffix = " FOR UPDATE" if for_update else ""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_STATE_SELECT} FROM app.research_loop_state "
                f"WHERE run_id=%s{suffix}", (run_id,),
            )
            result = _state(cur.fetchone())
        if actor is not None:
            kind, actor_id = _actor(actor)
            if (result["actor_kind"], result["actor_id"]) != (kind, actor_id):
                raise failures.DiracNotFound("research loop does not exist")
        return result

    def claim(self, *, owner: str) -> LoopClaim | None:
        owner = str(owner).strip()
        if not owner:
            raise ValueError("lease owner is required")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_STATE_SELECT} FROM app.research_loop_state "
                "WHERE state='active' AND next_wake_at<=now() "
                "AND (lease_owner IS NULL OR lease_expires_at<now()) "
                "ORDER BY next_wake_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1")
            row = cur.fetchone()
            if row is None:
                return None
            current = _state(row)
            cur.execute(
                "UPDATE app.research_loop_state SET lease_owner=%s,"
                "lease_expires_at=now()+(30 * interval '1 second'),updated_at=now() "
                "WHERE run_id=%s RETURNING lease_expires_at",
                (owner, current["run_id"]),
            )
            expires = cur.fetchone()[0]
        current["lease_owner"] = owner
        current["lease_expires_at"] = expires.isoformat()
        return LoopClaim(current["run_id"], int(current["version"]), owner, expires, current)

    def transition(
        self, claim: LoopClaim, *, expected_version: int, stage: str,
        state: str = "active", event_type: str, actor: Mapping[str, Any],
        payload: Mapping[str, Any] | None = None, artifact_id: str | None = None,
        next_wake_seconds: float = 0.0, updates: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if state not in LOOP_STATES or stage not in LOOP_STAGES:
            raise ValueError("invalid research loop state or stage")
        if expected_version != claim.version:
            raise failures.DiracInvalidParameters(
                "claim version does not match expected_version")
        kind, actor_id = _actor(actor)
        safe_updates = self._prepare_updates(updates or {})
        terminal = state in TERMINAL_STATES
        assignments = ["state=%s", "stage=%s", "version=version+1",
                       "updated_at=now()", "lease_owner=NULL", "lease_expires_at=NULL",
                       "next_wake_at=now()+(%s * interval '1 second')",
                       "finished_at=" + ("now()" if terminal else "NULL")]
        params: list[Any] = [state, stage, max(0.0, float(next_wake_seconds))]
        for column, value in safe_updates:
            assignments.append(f"{column}=%s")
            params.append(value)
        params.extend((claim.run_id, expected_version, claim.lease_owner))
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE app.research_loop_state SET " + ",".join(assignments) +
                " WHERE run_id=%s AND version=%s AND lease_owner=%s "
                "AND lease_expires_at>now() RETURNING version",
                tuple(params),
            )
            changed = cur.fetchone()
            if changed is None:
                self._raise_stale(cur, claim.run_id, expected_version)
            self._sync_run(cur, claim.run_id, state)
            self._append_event(cur, claim.run_id, event_type, stage, kind, actor_id,
                               dict(payload or {}), artifact_id=artifact_id)
            cur.execute(
                f"SELECT {_STATE_SELECT} FROM app.research_loop_state WHERE run_id=%s",
                (claim.run_id,),
            )
            return _state(cur.fetchone())

    def decide(
        self, *, run_id: str, expected_version: int, action_fingerprint: bytes,
        preview_artifact_id: str, command_input_digest: bytes,
        source_versions: Mapping[str, Any], decision: str,
        actor: Mapping[str, Any], rationale: str,
        acknowledgements: list[str] | None = None,
    ) -> dict[str, Any]:
        kind, actor_id = _actor(actor)
        if kind != "human":
            raise failures.DiracInvalidParameters(
                "research loop approval requires a named human actor")
        if decision not in {"approved", "rejected"} or not rationale.strip():
            raise failures.DiracInvalidParameters(
                "approval decision and rationale are required")
        fingerprint = _digest(action_fingerprint, "action_fingerprint")
        input_digest = _digest(command_input_digest, "command_input_digest")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_STATE_SELECT} FROM app.research_loop_state "
                "WHERE run_id=%s FOR UPDATE", (run_id,),
            )
            current = _state(cur.fetchone())
            if (current["actor_kind"], current["actor_id"]) != (kind, actor_id):
                raise failures.DiracNotFound("research loop does not exist")
            if current["version"] != expected_version:
                raise failures.DiracStalePreview(
                    "research action preview is stale",
                    details={"expected_version": expected_version,
                             "current_version": current["version"]})
            if (current["state"], current["stage"]) != (
                    "waiting_approval", "await_approval"):
                raise failures.DiracStalePreview(
                    "research loop is no longer awaiting this action")
            cur.execute(
                "INSERT INTO app.research_loop_approval "
                "(run_id,loop_version,action_fingerprint,preview_artifact_id,"
                "command_input_digest,source_versions,decision,actor_kind,actor_id,rationale) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING "
                "RETURNING id",
                (run_id, expected_version, fingerprint, preview_artifact_id,
                 input_digest, _json(source_versions, "source_versions"), decision,
                 kind, actor_id, rationale),
            )
            inserted = cur.fetchone()
            if inserted is None:
                cur.execute(
                    "SELECT decision,actor_kind,actor_id,rationale FROM "
                    "app.research_loop_approval WHERE run_id=%s AND action_fingerprint=%s",
                    (run_id, fingerprint),
                )
                prior = cur.fetchone()
                if prior != (decision, kind, actor_id, rationale):
                    raise failures.DiracIdempotencyConflict(
                        "action fingerprint already has a different decision")
                return current
            new_stage = "dispatch" if decision == "approved" else "refresh"
            acknowledgement_json = json.dumps(sorted(set(acknowledgements or [])))
            cur.execute(
                "UPDATE app.research_loop_state SET state='active',stage=%s,"
                "version=version+1,updated_at=now(),next_wake_at=now(),"
                "pending_action=CASE WHEN %s='approved' THEN "
                "jsonb_set(coalesce(pending_action,'{}'::jsonb),"
                "'{approved_acknowledgements}',%s::jsonb,true) ELSE pending_action END "
                "WHERE run_id=%s AND version=%s RETURNING version",
                (new_stage, decision, acknowledgement_json, run_id, expected_version),
            )
            if cur.fetchone() is None:
                self._raise_stale(cur, run_id, expected_version)
            cur.execute("UPDATE app.run SET state='active' WHERE id=%s", (run_id,))
            self._append_event(
                cur, run_id, f"action_{decision}", new_stage, kind, actor_id,
                {"action_fingerprint": "sha256:" + fingerprint.hex(),
                 "preview_artifact_id": preview_artifact_id, "rationale": rationale,
                 "acknowledgements": sorted(set(acknowledgements or []))},
                artifact_id=preview_artifact_id,
            )
            cur.execute(
                f"SELECT {_STATE_SELECT} FROM app.research_loop_state WHERE run_id=%s",
                (run_id,),
            )
            return _state(cur.fetchone())

    def control(
        self, *, run_id: str, expected_version: int, action: str,
        actor: Mapping[str, Any], rationale: str,
        revised_intent: str | None = None,
        provider_profile_id: str | None = None,
        provider_profile_digest: bytes | None = None,
    ) -> dict[str, Any]:
        kind, actor_id = _actor(actor)
        allowed = {"pause", "resume", "cancel", "retry", "revise_intent",
                   "change_provider"}
        if action not in allowed or not rationale.strip():
            raise failures.DiracInvalidParameters(
                "control action and rationale are required")
        if action == "revise_intent" and not (revised_intent or "").strip():
            raise failures.DiracInvalidParameters(
                "revise_intent requires a non-empty revised_intent")
        new_provider_digest = None
        if action == "change_provider":
            if kind != "human" or not (provider_profile_id or "").strip():
                raise failures.DiracInvalidParameters(
                    "change_provider requires a named human and provider_profile_id")
            new_provider_digest = _digest(
                provider_profile_digest or b"", "provider_profile_digest")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_STATE_SELECT} FROM app.research_loop_state "
                "WHERE run_id=%s FOR UPDATE", (run_id,),
            )
            current = _state(cur.fetchone())
            if (current["actor_kind"], current["actor_id"]) != (kind, actor_id):
                raise failures.DiracNotFound("research loop does not exist")
            if current["version"] != expected_version:
                self._raise_version(current, expected_version)
            state, stage, event = self._control_target(current, action)
            mutable_sql = ""
            params: list[Any] = [state, stage]
            if action == "revise_intent":
                mutable_sql = (
                    ",intent=%s,proposal_artifact_id=NULL,"
                    "proposal_context_digest=NULL,pending_action=NULL")
                params.append(str(revised_intent).strip())
            elif action == "change_provider":
                mutable_sql = (
                    ",provider_profile_id=%s,provider_profile_digest=%s,"
                    "proposal_artifact_id=NULL,proposal_context_digest=NULL,"
                    "pending_action=NULL")
                params.extend((str(provider_profile_id).strip(), new_provider_digest))
            elif action == "retry":
                attempts = dict(current["stage_attempts"])
                attempts[stage] = int(attempts.get(stage, 0)) + 1
                jobs = dict(current["stage_jobs"])
                jobs.pop("active", None)
                mutable_sql = ",stage_attempts=%s,stage_jobs=%s,attention='{}'::jsonb"
                params.extend((_json(attempts, "stage_attempts"),
                               _json(jobs, "stage_jobs")))
            params.extend((run_id, expected_version))
            terminal = state in TERMINAL_STATES
            cur.execute(
                "UPDATE app.research_loop_state SET state=%s,stage=%s,"
                "version=version+1,updated_at=now(),next_wake_at=now(),"
                "lease_owner=NULL,lease_expires_at=NULL,finished_at=" +
                ("now()" if terminal else "NULL") + mutable_sql +
                " WHERE run_id=%s AND version=%s RETURNING version",
                tuple(params),
            )
            if cur.fetchone() is None:
                self._raise_stale(cur, run_id, expected_version)
            self._sync_run(cur, run_id, state)
            payload = {"rationale": rationale, "control": action}
            if action == "revise_intent":
                payload["intent_revised"] = True
            elif action == "change_provider":
                payload.update({
                    "provider_profile_id": str(provider_profile_id).strip(),
                    "provider_profile_digest": "sha256:" + new_provider_digest.hex(),
                })
            self._append_event(cur, run_id, event, stage, kind, actor_id, payload)
            cur.execute(
                f"SELECT {_STATE_SELECT} FROM app.research_loop_state WHERE run_id=%s",
                (run_id,),
            )
            return _state(cur.fetchone())

    def link_artifact(self, *, run_id: str, artifact_id: str, role: str,
                      data_classification: str, cursor: Any | None = None) -> None:
        if role not in ARTIFACT_ROLES or data_classification not in DATA_CLASSIFICATIONS:
            raise failures.DiracInvalidParameters(
                "unsupported research loop Artifact role or classification")

        def link(cur: Any) -> None:
            cur.execute(
                "INSERT INTO app.research_loop_artifact "
                "(run_id,artifact_id,role,data_classification) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (run_id, artifact_id, role, data_classification),
            )

        if cursor is not None:
            link(cursor)
            return
        with self._connect() as conn, conn.cursor() as cur:
            link(cur)

    def events(self, run_id: str, *, actor: Mapping[str, Any]) -> list[dict[str, Any]]:
        self.get(run_id, actor=actor)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT sequence,event_type,stage,actor_kind,actor_id,automation_actor,"
                "payload,artifact_id,occurred_at FROM app.research_loop_event "
                "WHERE run_id=%s ORDER BY sequence", (run_id,),
            )
            rows = cur.fetchall()
        return [{
            "sequence": row[0], "event_type": row[1], "stage": row[2],
            "actor": {"kind": str(row[3]), "id": row[4]},
            "automation_actor": None if row[5] is None else dict(row[5]),
            "payload": dict(row[6]),
            "artifact_id": None if row[7] is None else str(row[7]),
            "occurred_at": row[8].isoformat(),
        } for row in rows]

    def link_job(self, *, run_id: str, job_id: str, purpose: str) -> None:
        if not str(purpose).strip():
            raise ValueError("run Job purpose is required")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (run_id,))
            cur.execute(
                "INSERT INTO app.run_job(run_id,job_id,ordinal,purpose) "
                "SELECT %s,%s,coalesce(max(ordinal)+1,0),%s FROM app.run_job "
                "WHERE run_id=%s ON CONFLICT (run_id,job_id) DO NOTHING",
                (run_id, job_id, str(purpose).strip(), run_id),
            )

    def next_wake_delay(self, *, maximum_seconds: float = 30.0) -> float | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT greatest(0,extract(epoch FROM min(next_wake_at)-now())) "
                "FROM app.research_loop_state WHERE state='active' "
                "AND (lease_owner IS NULL OR lease_expires_at<now())")
            row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        return min(maximum_seconds, max(0.0, float(row[0])))

    @staticmethod
    def _append_event(cur: Any, run_id: str, event_type: str, stage: str,
                      actor_kind: str, actor_id: str, payload: Mapping[str, Any],
                      *, artifact_id: str | None = None,
                      automation_actor: Mapping[str, Any] | None = None) -> None:
        cur.execute(
            "INSERT INTO app.research_loop_event "
            "(run_id,sequence,event_type,stage,actor_kind,actor_id,automation_actor,"
            "payload,artifact_id) SELECT %s,coalesce(max(sequence)+1,0),%s,%s,%s,%s,%s,%s,%s "
            "FROM app.research_loop_event WHERE run_id=%s",
            (run_id, event_type, stage, actor_kind, actor_id,
             None if automation_actor is None else _json(automation_actor, "automation_actor"),
             _json(payload, "event payload"), artifact_id, run_id),
        )

    @staticmethod
    def _prepare_updates(updates: Mapping[str, Any]) -> list[tuple[str, Any]]:
        allowed_json = {
            "policy", "budget_remaining", "budget_spent", "pending_action",
            "stage_jobs", "stage_attempts", "outputs", "attention",
        }
        allowed_scalar = {
            "iteration", "context_artifact_id", "proposal_artifact_id", "intent",
        }
        allowed_digest = {"context_digest", "proposal_context_digest"}
        unknown = set(updates) - allowed_json - allowed_scalar - allowed_digest
        if unknown:
            raise ValueError(f"unsupported loop update fields: {sorted(unknown)}")
        prepared: list[tuple[str, Any]] = []
        for key, value in updates.items():
            if key in allowed_json:
                if value is None and key == "pending_action":
                    prepared.append((key, None))
                else:
                    prepared.append((key, _json(value, key)))
            elif key in allowed_digest:
                prepared.append((key, None if value is None else _digest(value, key)))
            elif key == "iteration":
                number = int(value)
                if number < 0:
                    raise ValueError("iteration must be non-negative")
                prepared.append((key, number))
            elif key == "intent":
                text = str(value).strip()
                if not text:
                    raise ValueError("intent must be non-empty")
                prepared.append((key, text))
            else:
                prepared.append((key, value))
        return prepared

    @staticmethod
    def _sync_run(cur: Any, run_id: str, state: str) -> None:
        run_state = {
            "waiting_approval": "waiting_approval", "completed": "completed",
            "cancelled": "cancelled", "failed": "failed",
        }.get(state, "active")
        if run_state in {"completed", "cancelled", "failed"}:
            cur.execute(
                "UPDATE app.run SET state=%s,finished_at=coalesce(finished_at,now()) "
                "WHERE id=%s", (run_state, run_id),
            )
        else:
            cur.execute(
                "UPDATE app.run SET state=%s,finished_at=NULL WHERE id=%s",
                (run_state, run_id),
            )

    @staticmethod
    def _raise_version(current: Mapping[str, Any], expected: int) -> None:
        raise failures.DiracInvalidParameters(
            "research loop version conflict",
            details={"expected_version": expected,
                     "current_version": current["version"]})

    @classmethod
    def _raise_stale(cls, cur: Any, run_id: str, expected: int) -> None:
        cur.execute("SELECT version FROM app.research_loop_state WHERE run_id=%s", (run_id,))
        row = cur.fetchone()
        if row is None:
            raise failures.DiracNotFound("research loop does not exist")
        cls._raise_version({"version": row[0]}, expected)

    @staticmethod
    def _control_target(current: Mapping[str, Any], action: str) -> tuple[str, str, str]:
        state = current["state"]
        stage = current["stage"]
        if action == "pause":
            if state not in {"active", "blocked", "waiting_approval"}:
                raise failures.DiracInvalidParameters("only an open loop can be paused")
            return "paused", stage, "loop_paused"
        if action == "resume":
            if state != "paused":
                raise failures.DiracInvalidParameters("only a paused loop can be resumed")
            return "active", stage, "loop_resumed"
        if action == "cancel":
            if state in TERMINAL_STATES:
                raise failures.DiracInvalidParameters("terminal loop cannot be cancelled again")
            return "cancelled", "completed", "loop_cancelled"
        if action == "retry":
            if state != "blocked":
                raise failures.DiracInvalidParameters("only a blocked loop can be retried")
            retry_stage = str((current.get("attention") or {}).get("retry_stage") or stage)
            return "active", retry_stage, "loop_retried"
        if state in TERMINAL_STATES:
            raise failures.DiracInvalidParameters("terminal loop intent cannot be revised")
        if action == "change_provider":
            return "active", "snapshot_context", "loop_provider_changed"
        return "active", "snapshot_context", "loop_intent_revised"
