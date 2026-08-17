"""Durable server-owned state machine for one six-leg RBFE edge RunSet."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import re
import threading
import time
from typing import Any, Callable
from uuid import UUID

import failures
from motif.rbfe_binding import (campaign_scientific_ref,
                                validate_campaign_binding)


_ACTIVE = {"queued", "running"}
_TERMINAL = {"done", "failed", "cancelled"}
_ARTIFACT_REF_KEYS = frozenset({"kind", "id", "sha256"})
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class _CampaignGateClosed(Exception):
    """Internal control flow: no new child or completion may cross this gate."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _exact_artifact_ref(value: Any, field: str) -> dict[str, str]:
    """Revalidate persisted execution provenance before exposing it."""
    if not isinstance(value, dict) or set(value) != _ARTIFACT_REF_KEYS:
        raise failures.DiracInternal(
            f"persisted RBFE RunSet {field} is not an exact artifact ref")
    artifact_id = value.get("id")
    try:
        parsed_id = UUID(str(artifact_id))
    except (AttributeError, TypeError, ValueError) as error:
        raise failures.DiracInternal(
            f"persisted RBFE RunSet {field} id is malformed") from error
    if str(parsed_id) != artifact_id:
        raise failures.DiracInternal(
            f"persisted RBFE RunSet {field} id is not canonical")
    digest = value.get("sha256")
    if value.get("kind") != "artifact" or not isinstance(digest, str) \
            or _SHA256.fullmatch(digest) is None:
        raise failures.DiracInternal(
            f"persisted RBFE RunSet {field} is malformed")
    return {"kind": "artifact", "id": artifact_id, "sha256": digest}


class RbfeRunSetController:
    """Own six OpenFE Jobs and their aggregation independently of any browser."""

    kind = "postgres"
    durability = "durable"

    def __init__(self, service: Any, connect: Callable[[], Any]) -> None:
        self.service = service
        self._connect = connect
        self._pool = ThreadPoolExecutor(max_workers=1,
                                        thread_name_prefix="rbfe-runset-controller")
        self._lock = threading.Lock()
        self._running = False
        self._wake_requested = False
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('app.rbfe_run_set'),EXISTS ("
                " SELECT 1 FROM information_schema.columns "
                " WHERE table_schema='app' AND table_name='rbfe_run_set' "
                " AND column_name='cancellation_requested_at') AND EXISTS ("
                " SELECT 1 FROM pg_constraint "
                " WHERE conrelid=to_regclass('app.rbfe_run_set') AND contype='c' "
                " AND conname='rbfe_run_set_state_check' "
                " AND pg_get_constraintdef(oid) LIKE '%%cancel_requested%%'),"
                "EXISTS (SELECT 1 FROM pg_index "
                " WHERE indexrelid=to_regclass('app.job_one_inflight') "
                " AND indisunique "
                " AND pg_get_indexdef(indexrelid) LIKE "
                " 'CREATE UNIQUE INDEX job_one_inflight ON app.job USING btree "
                "(actor_kind, actor_id, method_row_id, request_digest) WHERE %%' "
                " AND pg_get_expr(indpred, indrelid) = "
                " '((request_key IS NULL) AND (state = ANY (ARRAY[''queued''::app.job_state, ''running''::app.job_state])))'),"
                "EXISTS (SELECT 1 FROM pg_constraint "
                " WHERE conrelid=to_regclass('app.rbfe_run_set') "
                " AND contype='u' "
                " AND conname='rbfe_run_set_actor_request_key_key' "
                " AND pg_get_constraintdef(oid) LIKE "
                " '%%actor_kind, actor_id, request_key%%'),"
                "EXISTS (SELECT 1 FROM pg_constraint "
                " WHERE conrelid=to_regclass('app.rbfe_run_set') "
                " AND contype='c' "
                " AND conname='rbfe_run_set_state_timestamps_check'),"
                "EXISTS (SELECT 1 FROM information_schema.columns "
                " WHERE table_schema='app' AND table_name='job' "
                " AND column_name='request_key') AND EXISTS ("
                " SELECT 1 FROM pg_constraint "
                " WHERE conrelid=to_regclass('app.job') AND contype='c' "
                " AND conname='job_request_key_nonempty' "
                " AND pg_get_constraintdef(oid) = format("
                "'CHECK (((request_key IS NULL) OR (request_key ~ "
                "((%L::text || %L::text) || %L::text))))', "
                "'[^[:space:]', U&'\\00A0\\2007\\202F\\FEFF', ']')) "
                "AND EXISTS (SELECT 1 FROM pg_constraint "
                " WHERE conrelid=to_regclass('app.job') AND contype='c' "
                " AND conname='job_request_key_length' "
                " AND pg_get_constraintdef(oid) = "
                " 'CHECK (((request_key IS NULL) OR "
                "(length(request_key) <= 256)))') "
                "AND EXISTS (SELECT 1 FROM pg_constraint "
                " WHERE conrelid=to_regclass('app.job') AND contype='c' "
                " AND conname='job_request_key_has_command' "
                " AND pg_get_constraintdef(oid) = "
                " 'CHECK (((request_key IS NULL) OR "
                "(command_id IS NOT NULL)))') "
                "AND EXISTS ("
                " SELECT 1 FROM pg_index "
                " WHERE indexrelid=to_regclass('app.job_command_request_key_once') "
                " AND indisunique "
                " AND pg_get_indexdef(indexrelid) LIKE "
                " 'CREATE UNIQUE INDEX job_command_request_key_once ON app.job USING btree "
                "(actor_kind, actor_id, command_id, request_key) WHERE %%' "
                " AND pg_get_expr(indpred, indrelid) = '(request_key IS NOT NULL)'),"
                "to_regclass('app.job_dispatch') IS NOT NULL")
            (table, cancellation_capability, job_tenant_capability,
             tenant_key_capability, state_timestamp_capability,
             command_key_capability, dispatch_capability) = cur.fetchone()
            if table is None:
                raise failures.DiracFailure(
                    "DB_UNAVAILABLE", "migration 039_rbfe_run_set.sql is not applied")
            if not cancellation_capability:
                raise failures.DiracFailure(
                    "DB_UNAVAILABLE",
                    "migration 041_rbfe_runset_cancellation.sql is not applied")
            if not job_tenant_capability:
                raise failures.DiracFailure(
                    "DB_UNAVAILABLE",
                    "migration 042_job_tenant_isolation.sql is not applied")
            if not tenant_key_capability:
                raise failures.DiracFailure(
                    "DB_UNAVAILABLE",
                    "migration 043_rbfe_runset_tenant_request_key.sql is not applied")
            if not state_timestamp_capability:
                raise failures.DiracFailure(
                    "DB_UNAVAILABLE",
                    "migration 044_rbfe_runset_state_integrity.sql is not applied")
            if not command_key_capability:
                raise failures.DiracFailure(
                    "DB_UNAVAILABLE",
                    "migration 046_job_command_request_key.sql is not applied")
            if not dispatch_capability:
                raise failures.DiracFailure(
                    "DB_UNAVAILABLE",
                    "migration 047_job_dispatch_fence.sql is not applied")
        self.wake()

    def _read_json(self, reference: dict, role: str) -> tuple[Any, dict]:
        artifact, data = self.service.store.read(reference["id"])
        actual = "sha256:" + artifact.sha256
        if reference.get("sha256") != actual or artifact.role != role:
            raise failures.DiracInvalidParameters(
                f"artifact capability must resolve to server-owned {role}")
        try:
            value = json.loads(data)
        except json.JSONDecodeError as error:
            raise failures.DiracInvalidParameters(f"{role} is not valid JSON") from error
        if not isinstance(value, (dict, list)):
            raise failures.DiracInvalidParameters(f"{role} has an invalid JSON root")
        return artifact, value

    @staticmethod
    def _principal(actor: dict[str, str] | None) -> dict[str, str]:
        if not isinstance(actor, dict):
            raise failures.DiracInvalidParameters(
                "RBFE RunSet requires an authenticated actor")
        kind = str(actor.get("kind") or "")
        actor_id = str(actor.get("id") or "").strip()
        if kind not in {"human", "agent", "service"} or not actor_id:
            raise failures.DiracInvalidParameters(
                "RBFE RunSet actor must be a human, agent, or service")
        return {"kind": kind, "id": actor_id}

    def _assert_campaign_current(self, definition: dict,
                                 edge_spec: dict,
                                 edge_network: dict | None = None,
                                 actor: dict[str, str] | None = None) -> dict:
        try:
            binding = validate_campaign_binding(
                edge_spec.get("campaign_binding"))
        except (TypeError, ValueError) as error:
            raise failures.DiracInvalidParameters(
                f"RBFE execution requires one exact Campaign binding: {error}") from error
        expected = {
            "campaign_id": str(definition.get("campaign_id") or ""),
            "campaign_scientific_generation": int(
                definition.get("campaign_scientific_generation") or 0),
            "campaign_scientific_digest": str(
                definition.get("campaign_scientific_digest") or ""),
        }
        observed = {key: binding.get(key) for key in expected}
        if observed != expected:
            raise failures.DiracInvalidParameters(
                "RunSet campaign binding is missing, stale or internally inconsistent")
        if edge_network is not None and edge_network.get("campaign_binding") != binding:
            raise failures.DiracInvalidParameters(
                "edge network and edge specification do not share one campaign binding")
        resolver = getattr(self.service, "rbfe_reference_resolver", None)
        if resolver is None:
            raise failures.DiracFailure(
                "DB_UNAVAILABLE", "campaign generation resolver is unavailable")
        principal = self._principal(actor or definition.get("run_actor"))
        resolver.assert_campaign_generation(
            expected["campaign_id"],
            expected["campaign_scientific_generation"],
            expected["campaign_scientific_digest"], principal)
        return binding

    def _require_campaign_current(self, definition: dict,
                                  edge_spec: dict) -> None:
        try:
            self._assert_campaign_current(definition, edge_spec)
        except failures.DiracFailure as error:
            raise _CampaignGateClosed(str(error)) from error

    def start(self, definition: dict, actor: dict[str, str]) -> dict[str, Any]:
        principal = self._principal(actor)
        _, edge_spec = self._read_json(definition["edge_spec_ref"], "rbfe.edge_spec")
        _, edge_network = self._read_json(
            definition["edge_network_ref"], "rbfe.edge_network")
        _, complex_transformation = self._read_json(
            definition["complex_transformation_ref"],
            "rbfe.openfe.complex_transformation")
        _, solvent_transformation = self._read_json(
            definition["solvent_transformation_ref"],
            "rbfe.openfe.solvent_transformation")
        if (edge_spec.get("digest") != _digest({
                key: value for key, value in edge_spec.items() if key != "digest"})
                or edge_network.get("digest") != _digest({
                    key: value for key, value in edge_network.items()
                    if key != "digest"})
                or edge_spec.get("edge_network_digest") != edge_network.get("digest")
                or edge_spec.get("complex_transformation_digest") != _digest(complex_transformation)
                or edge_spec.get("solvent_transformation_digest") != _digest(solvent_transformation)):
            raise failures.DiracInvalidParameters(
                "RunSet inputs are not the artifacts frozen by one RBFE preflight")
        capabilities = (self.service.capabilities()
                        if callable(getattr(self.service, "capabilities", None))
                        else {})
        executor = dict(capabilities.get("executor") or {})
        if not bool(executor.get("gpu_execution")):
            raise failures.DiracUnsupported(
                "RBFE RunSet cannot start without a configured OpenFE GPU executor",
                details={
                    "executor_adapter": str(
                        executor.get("adapter") or "unconfigured"),
                    "required_capability": "executor.gpu_execution",
                })
        self._assert_campaign_current(
            definition, edge_spec, edge_network, actor=principal)
        matrix = edge_spec.get("execution_matrix") or []
        identities = {(row.get("leg"), row.get("repeat_index")) for row in matrix}
        required = {(leg, repeat) for repeat in range(1, 4)
                    for leg in ("complex", "solvent")}
        if identities != required or len(matrix) != 6:
            raise failures.DiracInvalidParameters(
                "RBFE edge spec must authorize exactly complex+solvent x three repeats")
        document = {**definition, "run_actor": principal,
                    "edge_id": edge_spec["edge_id"],
                    "edge_spec_digest": edge_spec["digest"]}
        digest = _digest(document)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.rbfe_run_set "
                "(request_key,specification_digest,specification,actor_kind,actor_id) "
                "VALUES (%s,decode(%s,'hex'),%s,%s,%s) "
                "ON CONFLICT (actor_kind,actor_id,request_key) "
                "DO NOTHING RETURNING id",
                (definition["request_key"], digest.removeprefix("sha256:"),
                 json.dumps(document), principal["kind"], principal["id"]))
            row = cur.fetchone()
            created = row is not None
            if row is None:
                cur.execute(
                    "SELECT id,encode(specification_digest,'hex') FROM app.rbfe_run_set "
                    "WHERE actor_kind=%s AND actor_id=%s AND request_key=%s",
                    (principal["kind"], principal["id"],
                     definition["request_key"]))
                row = cur.fetchone()
                if row is None or row[1] != digest.removeprefix("sha256:"):
                    raise failures.DiracInvalidParameters(
                        "request_key already identifies a different RBFE RunSet")
        self.wake()
        result = self.get(str(row[0]), principal)
        result["created"] = created
        return result

    def _get(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id,request_key,specification,state,leg_jobs,aggregate_job_id,"
                "aggregate_output,attention,actor_kind,actor_id,created_at,updated_at,"
                "finished_at,encode(specification_digest,'hex'),"
                "cancellation_requested_at "
                "FROM app.rbfe_run_set WHERE id=%s", (run_id,))
            row = cur.fetchone()
        if row is None:
            raise failures.DiracNotFound(
                "RBFE RunSet does not exist", details={"run_id": run_id})
        specification = dict(row[2])
        try:
            scientific_ref = campaign_scientific_ref(
                campaign_id=specification["campaign_id"],
                generation=specification["campaign_scientific_generation"],
                digest=specification["campaign_scientific_digest"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise failures.DiracInternal(
                "persisted RBFE RunSet scientific campaign ref is malformed") from error
        edge_id = specification.get("edge_id")
        if not isinstance(edge_id, str) or not edge_id.strip():
            raise failures.DiracInternal(
                "persisted RBFE RunSet edge_id is malformed")
        execution_refs = {
            field: _exact_artifact_ref(specification.get(field), field)
            for field in (
                "edge_spec_ref",
                "edge_network_ref",
                "complex_transformation_ref",
                "solvent_transformation_ref",
            )
        }
        return {
            "ref": {"kind": "run", "id": str(row[0])},
            "request_key": row[1], "edge_id": edge_id,
            **execution_refs,
            "campaign_scientific_ref": scientific_ref,
            "state": row[3], "jobs": dict(row[4]),
            "aggregate_job_ref": ({"kind": "job", "id": str(row[5])}
                                  if row[5] else None),
            "aggregate_output": dict(row[6]), "attention": dict(row[7]),
            "actor": {"kind": str(row[8]), "id": row[9]},
            "created_at": row[10].isoformat(), "updated_at": row[11].isoformat(),
            "finished_at": row[12].isoformat() if row[12] else None,
            "specification_digest": "sha256:" + row[13],
            "cancellation_requested_at": row[14].isoformat() if row[14] else None,
        }

    def get(self, run_id: str, actor: dict[str, str]) -> dict[str, Any]:
        principal = self._principal(actor)
        current = self._get(run_id)
        owner = current["actor"]
        if (owner.get("kind") != principal["kind"]
                or not hmac.compare_digest(
                    str(owner.get("id") or ""), principal["id"])):
            # UUID possession is an integrity capability, not authorization.
            raise failures.DiracNotFound("RBFE RunSet does not exist")
        return current

    def cancel(self, run_id: str, actor: dict[str, str]) -> dict[str, Any]:
        principal = self._principal(actor)
        current = self.get(run_id, principal)
        if current["state"] in {"completed", "cancelled"}:
            return current
        self._request_cancellation(run_id, "USER_REQUEST")
        self.wake()
        return self.get(run_id, principal)

    def _request_cancellation(self, run_id: str, reason: str, *,
                              terminal_state: str = "cancelled",
                              detail: dict[str, Any] | None = None) -> bool:
        """Cancel every child before exposing a terminal RunSet state.

        The RunSet row is the serialization point for submission and cancellation.
        Holding it while cancellation is issued means a leg cannot be submitted but
        omitted from the persisted child set, and a stale controller cannot overwrite
        the resulting state afterwards.
        """
        if terminal_state not in {"blocked", "cancelled"}:
            raise ValueError(f"invalid cancellation terminal state {terminal_state!r}")
        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "SELECT state,leg_jobs,aggregate_job_id,attention,specification "
                "FROM app.rbfe_run_set WHERE id=%s FOR UPDATE", (run_id,))
            row = cur.fetchone()
            if row is None:
                raise failures.DiracNotFound("RBFE RunSet does not exist")
            if row[0] in {"completed", "cancelled"}:
                return True

            jobs = dict(row[1])
            definition = dict(row[4])
            principal = self._principal(definition.get("run_actor"))
            previous_attention = dict(row[3])
            previous_terminal = str(
                previous_attention.get("terminal_state") or "")
            if (row[0] == "cancel_requested"
                    and previous_terminal == "cancelled"
                    and terminal_state == "blocked"):
                terminal_state = "cancelled"
                previous_detail = {
                    key: value for key, value in previous_attention.items()
                    if key not in {"code", "message", "terminal_state",
                                   "pending_children"}}
                detail = {**previous_detail,
                          "secondary_reasons": sorted(set(
                              previous_detail.get("secondary_reasons", []))
                              | {reason})}
                reason = str(previous_attention.get("code") or reason)
            attempt = int(definition.get("retry_count", 0))
            for repeat in range(1, 4):
                for leg in ("complex", "solvent"):
                    key = f"{leg}:{repeat}"
                    if key in jobs:
                        continue
                    request_id = f"rbfe-runset:{run_id}:{key}:attempt:{attempt}"
                    cur.execute(
                        "SELECT id FROM app.job WHERE request_id=%s "
                        "AND actor_kind=%s AND actor_id=%s "
                        "ORDER BY created_at DESC LIMIT 1",
                        (request_id, principal["kind"], principal["id"]))
                    recovered = cur.fetchone()
                    if recovered:
                        jobs[key] = {
                            "leg": leg, "repeat_index": repeat,
                            "job_id": str(recovered[0]), "state": "queued",
                            "attempt": attempt, "request_id": request_id,
                        }
            pending: list[str] = []
            for key, item in jobs.items():
                if item.get("state") in _TERMINAL:
                    continue
                try:
                    observed = self.service.get_job(
                        item["job_id"], actor=principal)
                    if observed.get("state") in _ACTIVE:
                        observed = self.service.cancel_job(
                            item["job_id"], actor=principal)
                    item["state"] = str(observed.get("state") or item["state"])
                    item["cancel_requested_at"] = observed.get(
                        "cancel_requested_at")
                    if item["state"] not in _TERMINAL:
                        pending.append(key)
                except failures.DiracFailure as error:
                    item["cancel_error"] = str(error)
                    pending.append(key)

            aggregate_id = str(row[2]) if row[2] else None
            if aggregate_id is None:
                aggregate_request_id = self._aggregate_request_id(run_id, attempt)
                cur.execute(
                    "SELECT id FROM app.job WHERE request_id=%s "
                    "AND actor_kind=%s AND actor_id=%s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (aggregate_request_id, principal["kind"], principal["id"]))
                recovered = cur.fetchone()
                aggregate_id = str(recovered[0]) if recovered else None
            if aggregate_id:
                try:
                    aggregate = self.service.get_job(
                        aggregate_id, actor=principal)
                    if aggregate.get("state") in _ACTIVE:
                        aggregate = self.service.cancel_job(
                            aggregate_id, actor=principal)
                    if aggregate.get("state") not in _TERMINAL:
                        pending.append("aggregate")
                except failures.DiracFailure:
                    pending.append("aggregate")

            state = "cancel_requested" if pending else terminal_state
            attention = {
                "code": reason,
                "terminal_state": terminal_state,
                "message": ("cancellation requested; waiting for child jobs to stop"
                            if pending else "all child jobs are terminal"),
                **(detail or {}),
            }
            if pending:
                attention["pending_children"] = pending
            cur.execute(
                "UPDATE app.rbfe_run_set SET state=%s,leg_jobs=%s,attention=%s,"
                "aggregate_job_id=COALESCE(aggregate_job_id,%s),"
                "cancellation_requested_at=COALESCE(cancellation_requested_at,now()),"
                "finished_at=CASE WHEN %s IN ('blocked','cancelled') "
                "THEN now() ELSE NULL END,updated_at=now() WHERE id=%s "
                "AND state NOT IN ('completed','cancelled') RETURNING id",
                (state, json.dumps(jobs), json.dumps(attention), aggregate_id,
                 state, run_id))
            return cur.fetchone() is not None and not pending

    def retry(self, run_id: str, actor: dict[str, str]) -> dict[str, Any]:
        principal = self._principal(actor)
        self.get(run_id, principal)
        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "SELECT state,leg_jobs,aggregate_job_id,specification "
                "FROM app.rbfe_run_set "
                "WHERE id=%s FOR UPDATE", (run_id,))
            row = cur.fetchone()
            if row is None:
                raise failures.DiracNotFound("RBFE RunSet does not exist")
            if row[0] != "blocked":
                raise failures.DiracInvalidParameters(
                    "only a blocked RBFE RunSet can retry failed/cancelled legs")
            observed_jobs = dict(row[1])
            attempt = int(dict(row[3]).get("retry_count", 0))
            for repeat in range(1, 4):
                for leg in ("complex", "solvent"):
                    key = f"{leg}:{repeat}"
                    if key in observed_jobs:
                        continue
                    request_id = f"rbfe-runset:{run_id}:{key}:attempt:{attempt}"
                    cur.execute(
                        "SELECT id FROM app.job WHERE request_id=%s "
                        "AND actor_kind=%s AND actor_id=%s "
                        "ORDER BY created_at DESC LIMIT 1",
                        (request_id, principal["kind"], principal["id"]))
                    recovered = cur.fetchone()
                    if recovered:
                        observed_jobs[key] = {
                            "leg": leg, "repeat_index": repeat,
                            "job_id": str(recovered[0]), "state": "queued",
                            "attempt": attempt, "request_id": request_id,
                        }
            active_children = []
            for key, item in observed_jobs.items():
                try:
                    job = self.service.get_job(
                        item["job_id"], actor=principal)
                    item["state"] = str(job.get("state") or item.get("state"))
                except failures.DiracFailure:
                    if item.get("state") not in _TERMINAL:
                        active_children.append(key)
                    continue
                if item["state"] not in _TERMINAL:
                    active_children.append(key)
            aggregate_id = str(row[2]) if row[2] else None
            if aggregate_id is None:
                request_id = self._aggregate_request_id(run_id, attempt)
                cur.execute(
                    "SELECT id FROM app.job WHERE request_id=%s "
                    "AND actor_kind=%s AND actor_id=%s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (request_id, principal["kind"], principal["id"]))
                recovered = cur.fetchone()
                aggregate_id = str(recovered[0]) if recovered else None
            if aggregate_id:
                try:
                    aggregate = self.service.get_job(
                        aggregate_id, actor=principal)
                    if aggregate.get("state") not in _TERMINAL:
                        active_children.append("aggregate")
                except failures.DiracFailure:
                    active_children.append("aggregate")
            if active_children:
                raise failures.DiracInvalidParameters(
                    "cannot retry until every previous-attempt child Job is terminal",
                    details={"active_children": active_children})
            jobs = {key: value for key, value in observed_jobs.items()
                    if value.get("state") == "done"}
            cur.execute(
                "UPDATE app.rbfe_run_set SET state='pending',leg_jobs=%s,"
                "specification=jsonb_set(specification,'{retry_count}',"
                "to_jsonb(COALESCE((specification->>'retry_count')::int,0)+1),true),"
                "aggregate_job_id=NULL,aggregate_output='{}'::jsonb,"
                "attention='{}'::jsonb,cancellation_requested_at=NULL,"
                "finished_at=NULL,updated_at=now() WHERE id=%s AND state='blocked' "
                "RETURNING id", (json.dumps(jobs), run_id))
            if cur.fetchone() is None:
                raise failures.DiracInvalidParameters(
                    "RBFE RunSet changed state while retry was requested")
        self.wake()
        return self.get(run_id, principal)

    def wake(self) -> None:
        with self._lock:
            self._wake_requested = True
            if self._running:
                return
            self._running = True
            self._wake_requested = False
            self._pool.submit(self._drain)

    def _drain(self) -> None:
        clean_exit = False
        try:
            while True:
                run_id = self._next_active()
                if run_id is None:
                    with self._lock:
                        if self._wake_requested:
                            self._wake_requested = False
                            continue
                        self._running = False
                        clean_exit = True
                        return
                self._advance(run_id)
        finally:
            if not clean_exit:
                with self._lock:
                    if self._running:
                        self._running = False
                        if self._wake_requested:
                            self._wake_requested = False
                            self._running = True
                            self._pool.submit(self._drain)

    def _next_active(self) -> str | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM app.rbfe_run_set WHERE state IN "
                "('pending','running','aggregating','cancel_requested') ORDER BY updated_at "
                "LIMIT 1")
            row = cur.fetchone()
        return str(row[0]) if row else None

    @staticmethod
    def _aggregate_request_id(run_id: str, attempt: int) -> str:
        return f"rbfe-runset:{run_id}:aggregate:attempt:{attempt}"

    def _submit_leg_if_active(self, run_id: str, edge_spec: dict,
                              row: dict, transformation: dict) -> tuple[dict, str]:
        """Atomically gate one child submission on the current RunSet state."""
        leg, repeat = str(row["leg"]), int(row["repeat_index"])
        key = f"{leg}:{repeat}"
        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "SELECT state,leg_jobs,specification,actor_kind,actor_id "
                "FROM app.rbfe_run_set WHERE id=%s FOR UPDATE", (run_id,))
            locked = cur.fetchone()
            if locked is None:
                raise failures.DiracNotFound("RBFE RunSet does not exist")
            state, jobs = str(locked[0]), dict(locked[1])
            if state not in {"pending", "running"} or key in jobs:
                return jobs, state

            definition = dict(locked[2])
            principal = self._principal(
                {"kind": str(locked[3]), "id": str(locked[4])})
            self._require_campaign_current(definition, edge_spec)
            attempt = int(definition.get("retry_count", 0))
            request_id = f"rbfe-runset:{run_id}:{key}:attempt:{attempt}"
            cur.execute(
                "SELECT id FROM app.job WHERE request_id=%s "
                "AND actor_kind=%s AND actor_id=%s "
                "ORDER BY created_at DESC LIMIT 1",
                (request_id, principal["kind"], principal["id"]))
            existing = cur.fetchone()
            job_id = str(existing[0]) if existing else None
            if job_id is None:
                response = self.service.submit(
                    "physics.motif.openfe_edge", {
                        "edge_spec_ref": definition["edge_spec_ref"],
                        "edge_id": edge_spec["edge_id"], "leg": leg,
                        "repeat_index": repeat,
                        "seed": int(row["orchestration_seed"]),
                        "transformation": transformation,
                        "transformation_digest": edge_spec[
                            f"{leg}_transformation_digest"],
                        "target_ref": edge_spec["target_ref"],
                        "protein_structure_ref": edge_spec["protein_structure_ref"],
                        "thermodynamic_cycle_id": edge_spec["thermodynamic_cycle_id"],
                        "ligand_charge_digest": edge_spec["ligand_charge_digest"],
                        "charge_invariant": {
                            "edge_id": edge_spec["edge_id"],
                            "digest": edge_spec["ligand_charge_digest"]},
                        "analysis_bootstraps": int(
                            definition.get("analysis_bootstraps", 1000)),
                        "resume": True,
                    }, request_id=request_id,
                    actor=principal,
                    command_id="physics.rbfe-run.start")
                if not response.get("ok"):
                    raise failures.DiracInternal(
                        f"OpenFE leg submission failed: {response.get('error')}")
                job_id = str(response["meta"]["job_id"])
            jobs[key] = {"leg": leg, "repeat_index": repeat,
                         "job_id": job_id, "state": "queued",
                         "attempt": attempt, "request_id": request_id}
            cur.execute(
                "UPDATE app.rbfe_run_set SET leg_jobs=%s,state='running',"
                "updated_at=now() WHERE id=%s AND state IN ('pending','running') "
                "RETURNING id", (json.dumps(jobs), run_id))
            if cur.fetchone() is None:
                raise failures.DiracInternal(
                    "RunSet state changed while an OpenFE leg was being registered")
            return jobs, "running"

    def _submit_aggregate_if_active(self, run_id: str, edge_spec: dict,
                                    runs: list[dict]) -> tuple[str | None, str]:
        """Register one attempt-specific aggregate Job under the RunSet row lock."""
        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "SELECT state,aggregate_job_id,specification,actor_kind,actor_id "
                "FROM app.rbfe_run_set WHERE id=%s FOR UPDATE", (run_id,))
            locked = cur.fetchone()
            if locked is None:
                raise failures.DiracNotFound("RBFE RunSet does not exist")
            state = str(locked[0])
            if state == "aggregating" and locked[1]:
                return str(locked[1]), state
            if state != "running":
                return None, state

            definition = dict(locked[2])
            principal = self._principal(
                {"kind": str(locked[3]), "id": str(locked[4])})
            self._require_campaign_current(definition, edge_spec)
            attempt = int(definition.get("retry_count", 0))
            request_id = self._aggregate_request_id(run_id, attempt)
            cur.execute(
                "SELECT id FROM app.job WHERE request_id=%s "
                "AND actor_kind=%s AND actor_id=%s "
                "ORDER BY created_at DESC LIMIT 1",
                (request_id, principal["kind"], principal["id"]))
            existing = cur.fetchone()
            aggregate_id = str(existing[0]) if existing else None
            if aggregate_id is None:
                response = self.service.submit(
                    "physics.motif.rbfe_aggregate", {
                        "network_ref": definition["edge_network_ref"],
                        "edge_spec_ref": definition["edge_spec_ref"],
                        "runs": runs,
                    }, request_id=request_id,
                    actor=principal,
                    command_id="physics.rbfe-run.start")
                if not response.get("ok"):
                    raise failures.DiracInternal(
                        f"RBFE aggregation submission failed: {response.get('error')}")
                aggregate_id = str(response["meta"]["job_id"])
            cur.execute(
                "UPDATE app.rbfe_run_set SET state='aggregating',"
                "aggregate_job_id=%s,updated_at=now() WHERE id=%s "
                "AND state='running' RETURNING id", (aggregate_id, run_id))
            if cur.fetchone() is None:
                raise failures.DiracInternal(
                    "RunSet state changed while aggregation was being registered")
            return aggregate_id, "aggregating"

    def _complete_aggregate_if_current(self, run_id: str, aggregate_id: str,
                                       aggregate: dict, edge_spec: dict) -> bool:
        """Linearize the final generation gate and completion state transition."""
        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "SELECT state,aggregate_job_id,specification "
                "FROM app.rbfe_run_set WHERE id=%s FOR UPDATE", (run_id,))
            locked = cur.fetchone()
            if (locked is None or str(locked[0]) != "aggregating"
                    or str(locked[1]) != aggregate_id):
                return False
            completion_definition = dict(locked[2])
            self._require_campaign_current(completion_definition, edge_spec)
            cur.execute(
                "UPDATE app.rbfe_run_set SET state='completed',aggregate_output=%s,"
                "attention='{}'::jsonb,updated_at=now(),finished_at=now() "
                "WHERE id=%s AND state='aggregating' AND aggregate_job_id=%s "
                "RETURNING id",
                (json.dumps({"job_id": aggregate_id,
                             "result": aggregate.get("result_summary") or {},
                             "artifacts": aggregate.get("artifacts") or []}),
                 run_id, aggregate_id))
            return cur.fetchone() is not None

    def _settle_requested_cancellation(self, run_id: str,
                                       current: dict[str, Any]) -> bool:
        attention = dict(current.get("attention") or {})
        terminal_state = str(attention.get("terminal_state") or "cancelled")
        detail = {key: value for key, value in attention.items()
                  if key not in {"code", "message", "terminal_state",
                                 "pending_children"}}
        return self._request_cancellation(
            run_id, str(attention.get("code") or "USER_REQUEST"),
            terminal_state=terminal_state, detail=detail)

    def _advance(self, run_id: str) -> None:
        try:
            current = self._get(run_id)
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT specification FROM app.rbfe_run_set WHERE id=%s",
                            (run_id,))
                definition = dict(cur.fetchone()[0])
            principal = self._principal(definition.get("run_actor"))
            _, edge_spec = self._read_json(definition["edge_spec_ref"], "rbfe.edge_spec")
            if current["state"] == "cancel_requested":
                if not self._settle_requested_cancellation(run_id, current):
                    time.sleep(2)
                return
            if current["state"] in {"blocked", "cancelled", "completed"}:
                return
            try:
                self._assert_campaign_current(definition, edge_spec)
            except failures.DiracFailure:
                self._request_cancellation(run_id, "CAMPAIGN_STALE")
                return
            _, complex_transformation = self._read_json(
                definition["complex_transformation_ref"],
                "rbfe.openfe.complex_transformation")
            _, solvent_transformation = self._read_json(
                definition["solvent_transformation_ref"],
                "rbfe.openfe.solvent_transformation")
            jobs = dict(current["jobs"])
            for row in edge_spec["execution_matrix"]:
                leg, repeat = str(row["leg"]), int(row["repeat_index"])
                key = f"{leg}:{repeat}"
                if key in jobs:
                    continue
                transformation = (complex_transformation if leg == "complex"
                                  else solvent_transformation)
                jobs, state = self._submit_leg_if_active(
                    run_id, edge_spec, row, transformation)
                if state == "cancel_requested":
                    current = self._get(run_id)
                    self._settle_requested_cancellation(run_id, current)
                    return
                if state not in {"pending", "running"}:
                    return

            while True:
                current = self._get(run_id)
                if current["state"] == "cancel_requested":
                    if not self._settle_requested_cancellation(run_id, current):
                        time.sleep(2)
                    return
                if current["state"] in {"blocked", "cancelled", "completed"}:
                    return
                try:
                    self._assert_campaign_current(definition, edge_spec)
                except failures.DiracFailure:
                    self._request_cancellation(run_id, "CAMPAIGN_STALE")
                    return
                jobs = dict(current["jobs"])
                failures_seen = []
                for key, item in jobs.items():
                    job = self.service.get_job(
                        item["job_id"], actor=principal)
                    item["state"] = str(job.get("state"))
                    item["error"] = job.get("error_detail")
                    if item["state"] in {"failed", "cancelled"}:
                        failures_seen.append(key)
                if not self._save_jobs(run_id, jobs, "running"):
                    continue
                if failures_seen:
                    if not self._request_cancellation(
                            run_id, "OPENFE_LEG_FAILED", terminal_state="blocked",
                            detail={"jobs": failures_seen}):
                        time.sleep(2)
                    return
                if all(item["state"] == "done" for item in jobs.values()):
                    break
                time.sleep(2)

            current = self._get(run_id)
            if current["state"] == "cancel_requested":
                self._settle_requested_cancellation(run_id, current)
                return
            if current["state"] not in {"running", "aggregating"}:
                return
            runs = []
            jobs = dict(current["jobs"])
            for item in jobs.values():
                job = self.service.get_job(
                    item["job_id"], actor=principal)
                by_role = {artifact["role"]: artifact for artifact in job["artifacts"]}
                runs.append({
                    "result_ref": self._reference(by_role["rbfe.openfe.result"]),
                    "run_report_ref": self._reference(
                        by_role["rbfe.openfe.run_report"]),
                })
            aggregate_id, state = self._submit_aggregate_if_active(
                run_id, edge_spec, runs)
            if state == "cancel_requested":
                self._settle_requested_cancellation(run_id, self._get(run_id))
                return
            if state != "aggregating" or aggregate_id is None:
                return
            aggregate = self.service.wait_job(
                aggregate_id, actor=principal, timeout=86400, poll=2)
            current = self._get(run_id)
            if current["state"] == "cancel_requested":
                self._settle_requested_cancellation(run_id, current)
                return
            if current["state"] != "aggregating":
                return
            if aggregate.get("state") != "done":
                self._block(run_id, "RBFE_AGGREGATION_FAILED", {
                    "job_id": aggregate_id, "state": aggregate.get("state"),
                    "error": aggregate.get("error_detail")})
                return
            self._complete_aggregate_if_current(
                run_id, aggregate_id, aggregate, edge_spec)
        except _CampaignGateClosed:
            self._request_cancellation(run_id, "CAMPAIGN_STALE")
            return
        except Exception as error:  # noqa: BLE001
            self._block(run_id, "RBFE_CONTROLLER_ERROR", {
                "type": type(error).__name__, "message": str(error)})

    @staticmethod
    def _reference(artifact: dict) -> dict[str, str]:
        sha = str(artifact["sha256"])
        return {"kind": "artifact", "id": str(artifact["id"]),
                "sha256": sha if sha.startswith("sha256:") else "sha256:" + sha}

    def _save_jobs(self, run_id: str, jobs: dict, state: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE app.rbfe_run_set SET leg_jobs=%s,state=%s,updated_at=now() "
                "WHERE id=%s AND state IN ('pending','running') RETURNING id",
                (json.dumps(jobs), state, run_id))
            return cur.fetchone() is not None

    def _block(self, run_id: str, code: str, detail: dict) -> None:
        try:
            current = self._get(run_id)
            if current["state"] in {"completed", "cancelled"}:
                return
            self._request_cancellation(
                run_id, code, terminal_state="blocked", detail=detail)
            self.wake()
        except failures.DiracNotFound:
            return


__all__ = ["RbfeRunSetController"]
