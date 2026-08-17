from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest

import failures
from artifacts import MemoryArtifactStore
from motif.rbfe_runset import RbfeRunSetController, _digest


def test_runset_schema_scopes_request_keys_and_seals_state_timestamps():
    migrations = Path(__file__).parents[1] / "db" / "migrations"
    job_tenant = (migrations / "042_job_tenant_isolation.sql").read_text()
    tenant_key = (migrations / "043_rbfe_runset_tenant_request_key.sql").read_text()
    state_gate = (migrations / "044_rbfe_runset_state_integrity.sql").read_text()
    command_key = (migrations / "046_job_command_request_key.sql").read_text()
    assert ("ON app.job (actor_kind, actor_id, method_row_id, request_digest)"
            in job_tenant)
    assert "WHERE state IN ('queued', 'running')" in job_tenant
    assert "UNIQUE (actor_kind, actor_id, request_key)" in tenant_key
    assert "DROP CONSTRAINT rbfe_run_set_request_key_key" in tenant_key
    assert "cancellation_requested_at IS NULL" in state_gate
    assert "cancellation_requested_at IS NOT NULL" in state_gate
    assert "state IN ('blocked','completed','cancelled')" in state_gate
    assert "finished_at IS NOT NULL" in state_gate
    assert "finished_at IS NULL" in state_gate
    assert "finished_at >= cancellation_requested_at" in state_gate
    assert "job_command_request_key_once" in command_key
    assert "(actor_kind, actor_id, command_id, request_key)" in command_key
    assert "WHERE request_key IS NOT NULL" in command_key
    assert "request_key IS NULL AND state IN ('queued', 'running')" in command_key
    assert "[^[:space:]" in command_key
    assert "U&'\\00A0\\2007\\202F\\FEFF'" in command_key
    assert "COMMENT ON INDEX app.job_one_inflight" in command_key


def test_runset_read_model_carries_immutable_campaign_scientific_ref():
    campaign_id = "b9523286-30cf-4ef9-b298-990a602ecea8"
    scientific_digest = "sha256:" + "c" * 64
    execution_refs = {
        "edge_spec_ref": {
            "kind": "artifact", "id": "00000000-0000-4000-8000-000000000011",
            "sha256": "sha256:" + "1" * 64,
        },
        "edge_network_ref": {
            "kind": "artifact", "id": "00000000-0000-4000-8000-000000000012",
            "sha256": "sha256:" + "2" * 64,
        },
        "complex_transformation_ref": {
            "kind": "artifact", "id": "00000000-0000-4000-8000-000000000013",
            "sha256": "sha256:" + "3" * 64,
        },
        "solvent_transformation_ref": {
            "kind": "artifact", "id": "00000000-0000-4000-8000-000000000014",
            "sha256": "sha256:" + "4" * 64,
        },
    }
    now = datetime.now(timezone.utc)
    row = (
        "ee575314-973c-4982-ae14-00aec64e01eb", "request-1",
        {"edge_id": "edge-1", "campaign_id": campaign_id,
         "campaign_scientific_generation": 3,
         "campaign_scientific_digest": scientific_digest,
         **execution_refs},
        "running", {}, None, {}, {}, "human", "chemist-1", now, now,
        None, "d" * 64, None,
    )

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def execute(_query, _params):
            return None

        @staticmethod
        def fetchone():
            return row

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def cursor():
            return Cursor()

    controller = object.__new__(RbfeRunSetController)
    controller._connect = lambda: Connection()
    result = controller._get(str(row[0]))
    assert result["campaign_scientific_ref"] == {
        "kind": "rbfe_campaign", "id": campaign_id, "version": 3,
        "sha256": scientific_digest,
    }
    assert {field: result[field] for field in execution_refs} == execution_refs


def test_runset_read_model_rejects_incomplete_execution_provenance():
    campaign_id = "b9523286-30cf-4ef9-b298-990a602ecea8"
    now = datetime.now(timezone.utc)
    specification = {
        "edge_id": "edge-1", "campaign_id": campaign_id,
        "campaign_scientific_generation": 3,
        "campaign_scientific_digest": "sha256:" + "c" * 64,
        "edge_spec_ref": {
            "kind": "artifact", "id": "00000000-0000-4000-8000-000000000011",
        },
        "edge_network_ref": {
            "kind": "artifact", "id": "00000000-0000-4000-8000-000000000012",
            "sha256": "sha256:" + "2" * 64,
        },
        "complex_transformation_ref": {
            "kind": "artifact", "id": "00000000-0000-4000-8000-000000000013",
            "sha256": "sha256:" + "3" * 64,
        },
        "solvent_transformation_ref": {
            "kind": "artifact", "id": "00000000-0000-4000-8000-000000000014",
            "sha256": "sha256:" + "4" * 64,
        },
    }
    row = (
        "ee575314-973c-4982-ae14-00aec64e01eb", "request-1", specification,
        "running", {}, None, {}, {}, "human", "chemist-1", now, now,
        None, "d" * 64, None,
    )

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def execute(_query, _params):
            return None

        @staticmethod
        def fetchone():
            return row

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def cursor():
            return Cursor()

    controller = object.__new__(RbfeRunSetController)
    controller._connect = lambda: Connection()
    with pytest.raises(failures.DiracInternal, match="edge_spec_ref"):
        controller._get(str(row[0]))


class _FinishedService:
    def __init__(self, aggregate_job_id: str) -> None:
        self.store = MemoryArtifactStore()
        self.jobs: dict[str, dict] = {}
        self.aggregate_job_id = aggregate_job_id
        self.rbfe_reference_resolver = _CampaignResolver()
        self.submissions: list[tuple[str, str | None]] = []
        self.resolver_calls_at_wait: int | None = None

    def submit(self, method_id, payload, **kwargs):
        self.submissions.append((method_id, kwargs.get("request_id")))
        job_id = (self.aggregate_job_id
                  if method_id == "physics.motif.rbfe_aggregate" else str(uuid4()))
        artifacts = []
        if method_id == "physics.motif.openfe_edge":
            for role in ("rbfe.openfe.result", "rbfe.openfe.run_report"):
                artifacts.append({
                    "id": str(uuid4()), "role": role,
                    "sha256": "a" * 64})
        self.jobs[job_id] = {"id": job_id, "state": "done", "artifacts": artifacts,
                             "result_summary": {"data": {"status": "computed_unattested",
                                                           "passed_leg_count": 6}}}
        return {"ok": True, "meta": {"job_id": job_id}}

    @staticmethod
    def capabilities():
        return {"executor": {"adapter": "local_gpu", "gpu_execution": True}}

    def get_job(self, job_id, *, actor=None):
        assert actor == {"kind": "human", "id": "chemist-1"}
        return self.jobs[job_id]

    def wait_job(self, job_id, **_kwargs):
        self.resolver_calls_at_wait = self.rbfe_reference_resolver.calls
        return self.jobs[job_id]

    def cancel_job(self, job_id, *, actor=None):
        assert actor == {"kind": "human", "id": "chemist-1"}
        self.jobs[job_id]["state"] = "cancelled"
        return self.jobs[job_id]


class _CampaignResolver:
    def __init__(self) -> None:
        self.calls = 0

    def assert_campaign_generation(self, campaign_id, scientific_generation,
                                   scientific_digest, actor):
        self.calls += 1
        assert actor == {"kind": "human", "id": "chemist-1"}
        assert campaign_id
        assert scientific_generation == 1
        assert scientific_digest == "sha256:" + "c" * 64
        return {"campaign_ref": {"kind": "campaign", "id": campaign_id},
                "scientific_generation": scientific_generation,
                "scientific_digest": scientific_digest}


def _ref(artifact):
    return {"kind": "artifact", "id": artifact.id,
            "sha256": "sha256:" + artifact.sha256}


class _StateDb:
    def __init__(self, *, state="running", jobs=None, aggregate_job_id=None):
        self.run_id = "run-1"
        self.state = state
        self.jobs = jobs or {}
        self.aggregate_job_id = aggregate_job_id
        self.attention = {}
        self.specification = {
            "retry_count": 0,
            "run_actor": {"kind": "human", "id": "chemist-1"},
        }
        self.cancellation_requested_at = None
        self.request_jobs = {}


class _FakeContext:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args):
        return False


class _StateCursor:
    def __init__(self, db: _StateDb):
        self.db = db
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        statement = " ".join(sql.split())
        if statement.startswith(
                "SELECT state,leg_jobs,aggregate_job_id,attention,specification"):
            self.result = (self.db.state, self.db.jobs,
                           self.db.aggregate_job_id, self.db.attention,
                           self.db.specification)
        elif statement.startswith(
                "SELECT state,leg_jobs,aggregate_job_id,specification FROM"):
            self.result = (self.db.state, self.db.jobs,
                           self.db.aggregate_job_id, self.db.specification)
        elif statement.startswith(
                "SELECT state,aggregate_job_id,specification FROM"):
            self.result = (self.db.state, self.db.aggregate_job_id,
                           self.db.specification)
        elif statement.startswith("SELECT id FROM app.job WHERE request_id="):
            job_id = self.db.request_jobs.get(params[0])
            self.result = (job_id,) if job_id else None
        elif "SET state=%s,leg_jobs=%s,attention=%s" in statement:
            state, jobs, attention, aggregate_job_id, _terminal, _run_id = params
            if self.db.state in {"completed", "cancelled"}:
                self.result = None
            else:
                self.db.state = state
                self.db.jobs = json.loads(jobs)
                self.db.attention = json.loads(attention)
                self.db.aggregate_job_id = (
                    self.db.aggregate_job_id or aggregate_job_id)
                self.db.cancellation_requested_at = "now"
                self.result = (self.db.run_id,)
        elif "SET state='pending',leg_jobs=%s" in statement:
            jobs, _run_id = params
            if self.db.state != "blocked":
                self.result = None
            else:
                self.db.state = "pending"
                self.db.jobs = json.loads(jobs)
                self.db.aggregate_job_id = None
                self.db.attention = {}
                self.db.cancellation_requested_at = None
                self.db.specification["retry_count"] += 1
                self.result = (self.db.run_id,)
        elif "SET leg_jobs=%s,state=%s" in statement:
            jobs, state, _run_id = params
            if self.db.state not in {"pending", "running"}:
                self.result = None
            else:
                self.db.jobs = json.loads(jobs)
                self.db.state = state
                self.result = (self.db.run_id,)
        elif "SET state='completed',aggregate_output=%s" in statement:
            _output, _run_id, aggregate_job_id = params
            if (self.db.state != "aggregating"
                    or self.db.aggregate_job_id != aggregate_job_id):
                self.result = None
            else:
                self.db.state = "completed"
                self.result = (self.db.run_id,)
        else:  # pragma: no cover - fails loudly if production SQL changes shape
            raise AssertionError(f"unexpected SQL in RunSet unit double: {statement}")

    def fetchone(self):
        return self.result


class _StateConnection:
    def __init__(self, db: _StateDb):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def transaction(self):
        return _FakeContext(self)

    def cursor(self):
        return _StateCursor(self.db)


class _ControllableService:
    def __init__(self, jobs):
        self.jobs = jobs
        self.cancelled = []

    def get_job(self, job_id, *, actor=None):
        assert actor == {"kind": "human", "id": "chemist-1"}
        return dict(self.jobs[job_id])

    def cancel_job(self, job_id, *, actor=None):
        assert actor == {"kind": "human", "id": "chemist-1"}
        self.cancelled.append(job_id)
        row = dict(self.jobs[job_id])
        row["cancel_requested_at"] = "now"
        return row


def _state_controller(db: _StateDb, service: _ControllableService):
    controller = object.__new__(RbfeRunSetController)
    controller.service = service
    controller._connect = lambda: _StateConnection(db)
    controller.wake = lambda: None
    controller._get = lambda _run_id: {
        "state": db.state, "jobs": db.jobs, "attention": db.attention,
        "aggregate_job_ref": ({"kind": "job", "id": db.aggregate_job_id}
                              if db.aggregate_job_id else None),
        "actor": {"kind": "human", "id": "chemist-1"},
    }
    return controller


def test_runset_uuid_does_not_authorize_a_different_actor():
    db = _StateDb()
    controller = _state_controller(db, _ControllableService({}))
    with pytest.raises(failures.DiracNotFound):
        controller.get(db.run_id, {"kind": "human", "id": "intruder"})


class _QueuedPool:
    def __init__(self):
        self.callbacks = []

    def submit(self, callback):
        self.callbacks.append(callback)


class _ExitHookLock:
    def __init__(self, hook):
        self._lock = threading.Lock()
        self.hook = hook

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, *_args):
        self._lock.release()
        hook, self.hook = self.hook, None
        if hook:
            hook()
        return False


class _CapabilityCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql):
        self.connection.sql = sql

    def fetchone(self):
        return ("app.rbfe_run_set", self.connection.capability,
                self.connection.job_tenant_capability,
                self.connection.capability, self.connection.capability,
                self.connection.command_key_capability,
                self.connection.dispatch_capability)


class _CapabilityConnection:
    def __init__(self, capability, *, job_tenant_capability=None,
                 command_key_capability=None, dispatch_capability=None):
        self.capability = capability
        self.job_tenant_capability = (
            capability if job_tenant_capability is None
            else job_tenant_capability)
        self.command_key_capability = (
            capability if command_key_capability is None
            else command_key_capability)
        self.dispatch_capability = (
            capability if dispatch_capability is None
            else dispatch_capability)
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _CapabilityCursor(self)


def test_controller_refuses_partial_041_schema_capability():
    connection = _CapabilityConnection(False)
    with pytest.raises(failures.DiracFailure) as error:
        RbfeRunSetController(object(), lambda: connection)
    assert error.value.code == "DB_UNAVAILABLE"
    assert "041_rbfe_runset_cancellation.sql" in str(error.value)
    assert "cancellation_requested_at" in connection.sql
    assert "rbfe_run_set_state_check" in connection.sql


def test_controller_refuses_missing_actor_scoped_job_identity():
    connection = _CapabilityConnection(
        True, job_tenant_capability=False)
    with pytest.raises(failures.DiracFailure) as error:
        RbfeRunSetController(object(), lambda: connection)
    assert error.value.code == "DB_UNAVAILABLE"
    assert "042_job_tenant_isolation.sql" in str(error.value)
    assert "pg_get_indexdef(indexrelid)" in connection.sql
    assert "(actor_kind, actor_id, method_row_id, request_digest)" in connection.sql
    assert "pg_get_expr(indpred, indrelid)" in connection.sql
    assert "((request_key IS NULL) AND (state = ANY " in connection.sql
    assert "'queued''::app.job_state, ''running''::app.job_state" in connection.sql


def test_controller_refuses_missing_command_request_key_identity():
    connection = _CapabilityConnection(
        True, command_key_capability=False)
    with pytest.raises(failures.DiracFailure) as error:
        RbfeRunSetController(object(), lambda: connection)
    assert error.value.code == "DB_UNAVAILABLE"
    assert "046_job_command_request_key.sql" in str(error.value)
    assert "job_command_request_key_once" in connection.sql
    assert "job_request_key_nonempty" in connection.sql
    assert "job_request_key_length" in connection.sql
    assert "job_request_key_has_command" in connection.sql
    assert "pg_get_constraintdef(oid) = format(" in connection.sql
    assert "[^[:space:]" in connection.sql
    assert "CHECK (((request_key IS NULL) OR (length(request_key) <= 256)))" in (
        connection.sql)
    assert "CHECK (((request_key IS NULL) OR (command_id IS NOT NULL)))" in (
        connection.sql)
    assert "LIKE '%%request_key ~%%'" not in connection.sql


def test_real_catalog_accepts_actor_scoped_job_identity(monkeypatch):
    """The capability probe must accept PostgreSQL's real enum-typed predicate."""
    dsn = os.environ.get("DIRAC_TEST_DSN")
    if not dsn:
        pytest.skip("requires isolated PostgreSQL DIRAC_TEST_DSN")
    psycopg = pytest.importorskip("psycopg")

    def connect():
        return psycopg.connect(dsn, autocommit=True)

    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('app.rbfe_run_set'), "
                       "(SELECT count(*) FROM app.rbfe_run_set)")
        table, rows = cursor.fetchone()
    if table is None:
        pytest.skip("requires migrations 039-046")
    assert rows == 0, "catalog positive control requires an empty isolated RunSet table"

    controller = RbfeRunSetController(object(), connect)
    controller._pool.shutdown(wait=True)

    import field_server

    monkeypatch.setattr(field_server, "_db", connect)
    monkeypatch.setattr(field_server, "psycopg", psycopg)

    class Service:
        rbfe_reference_resolver = object()
        rbfe_runset_controller = controller

        @staticmethod
        def capabilities():
            return {"executor": {
                "adapter": "kubernetes", "gpu_execution": True}}

    readiness = field_server._rbfe_readiness(Service())
    assert readiness["rbfe_campaign_store"]["ready"] is True
    assert readiness["rbfe_runset"]["ready"] is True


def test_wake_arriving_during_empty_drain_is_not_lost():
    controller = object.__new__(RbfeRunSetController)
    controller._lock = threading.Lock()
    controller._running = False
    controller._wake_requested = False
    controller._pool = _QueuedPool()
    seen = []
    calls = 0

    def next_active():
        nonlocal calls
        calls += 1
        if calls == 1:
            controller.wake()
            return None
        if calls == 2:
            return "committed-after-first-scan"
        return None

    controller._next_active = next_active
    controller._advance = seen.append
    controller.wake()
    assert len(controller._pool.callbacks) == 1
    controller._pool.callbacks.pop()()
    assert seen == ["committed-after-first-scan"]
    assert controller._running is False


def test_new_drain_started_during_old_clean_exit_is_not_clobbered():
    controller = object.__new__(RbfeRunSetController)
    controller._running = True
    controller._wake_requested = False
    controller._pool = _QueuedPool()
    controller._next_active = lambda: None
    controller._advance = lambda _run_id: None
    controller._lock = _ExitHookLock(controller.wake)
    controller._drain()
    assert controller._running is True
    assert len(controller._pool.callbacks) == 1


def test_failed_leg_waits_for_running_sibling_before_blocked_and_retry():
    jobs = {
        "complex:1": {"job_id": "failed", "state": "failed"},
        "solvent:1": {"job_id": "sibling", "state": "running"},
    }
    db = _StateDb(jobs=jobs)
    service = _ControllableService({
        "failed": {"id": "failed", "state": "failed"},
        "sibling": {"id": "sibling", "state": "running"},
    })
    controller = _state_controller(db, service)

    settled = controller._request_cancellation(
        db.run_id, "OPENFE_LEG_FAILED", terminal_state="blocked",
        detail={"jobs": ["complex:1"]})
    assert settled is False
    assert db.state == "cancel_requested"
    assert db.attention["terminal_state"] == "blocked"
    assert service.cancelled == ["sibling"]

    with pytest.raises(failures.DiracInvalidParameters) as blocked_retry:
        controller.retry(db.run_id, {"kind": "human", "id": "chemist-1"})
    assert "only a blocked" in str(blocked_retry.value)

    service.jobs["sibling"]["state"] = "cancelled"
    settled = controller._request_cancellation(
        db.run_id, "OPENFE_LEG_FAILED", terminal_state="blocked",
        detail={"jobs": ["complex:1"]})
    assert settled is True
    assert db.state == "blocked"

    retried = controller.retry(
        db.run_id, {"kind": "human", "id": "chemist-1"})
    assert retried["state"] == "pending"
    assert db.jobs == {}
    assert db.specification["retry_count"] == 1
    assert db.cancellation_requested_at is None


def test_retry_refuses_legacy_blocked_row_with_active_child():
    jobs = {"complex:1": {"job_id": "still-running", "state": "running"}}
    db = _StateDb(state="blocked", jobs=jobs)
    service = _ControllableService({
        "still-running": {"id": "still-running", "state": "running"}})
    controller = _state_controller(db, service)
    with pytest.raises(failures.DiracInvalidParameters) as error:
        controller.retry(db.run_id, {"kind": "human", "id": "chemist-1"})
    assert "every previous-attempt child Job is terminal" in str(error.value)
    assert db.state == "blocked"


def test_cancellation_recovers_job_committed_before_runset_registration():
    db = _StateDb(jobs={})
    request_id = "rbfe-runset:run-1:complex:1:attempt:0"
    db.request_jobs[request_id] = "orphaned-leg"
    service = _ControllableService({
        "orphaned-leg": {"id": "orphaned-leg", "state": "running"}})
    controller = _state_controller(db, service)

    assert controller._request_cancellation(db.run_id, "USER_REQUEST") is False
    assert db.state == "cancel_requested"
    assert db.jobs["complex:1"]["job_id"] == "orphaned-leg"
    assert service.cancelled == ["orphaned-leg"]

    service.jobs["orphaned-leg"]["state"] = "cancelled"
    assert controller._request_cancellation(db.run_id, "USER_REQUEST") is True
    assert db.state == "cancelled"


def test_failure_cannot_downgrade_prior_user_cancellation_to_blocked():
    db = _StateDb(
        state="cancel_requested",
        jobs={"complex:1": {"job_id": "running", "state": "running"}})
    db.attention = {"code": "USER_REQUEST", "terminal_state": "cancelled"}
    service = _ControllableService({
        "running": {"id": "running", "state": "running"}})
    controller = _state_controller(db, service)
    assert controller._request_cancellation(
        db.run_id, "OPENFE_LEG_FAILED", terminal_state="blocked") is False
    assert db.attention["terminal_state"] == "cancelled"
    assert db.attention["code"] == "USER_REQUEST"

    service.jobs["running"]["state"] = "cancelled"
    assert controller._settle_requested_cancellation(
        db.run_id, controller._get(db.run_id)) is True
    assert db.state == "cancelled"


def test_progress_save_is_compare_and_swap_against_cancellation():
    db = _StateDb(state="cancel_requested")
    controller = _state_controller(db, _ControllableService({}))
    assert controller._save_jobs(
        db.run_id, {"complex:1": {"state": "done"}}, "running") is False
    assert db.state == "cancel_requested"


def test_aggregate_request_identity_changes_on_retry():
    first = RbfeRunSetController._aggregate_request_id("run", 0)
    retried = RbfeRunSetController._aggregate_request_id("run", 1)
    assert first != retried
    assert first.endswith("attempt:0") and retried.endswith("attempt:1")


def test_aggregate_completion_rechecks_generation_inside_completion_cas():
    db = _StateDb(state="aggregating", aggregate_job_id="aggregate-1")
    controller = _state_controller(db, _ControllableService({}))
    gates = []
    controller._require_campaign_current = (
        lambda definition, edge_spec: gates.append((definition, edge_spec)))
    completed = controller._complete_aggregate_if_current(
        db.run_id, "aggregate-1",
        {"state": "done", "result_summary": {"ok": True}, "artifacts": []},
        {"edge_id": "e1"})
    assert completed is True
    assert db.state == "completed"
    assert gates == [(db.specification, {"edge_id": "e1"})]


def test_edge_network_must_summarize_its_own_content():
    controller = object.__new__(RbfeRunSetController)
    network = {"edges": [{"edge_id": "e1"}], "digest": "sha256:" + "0" * 64}
    spec = {"edge_id": "e1", "edge_network_digest": network["digest"]}
    spec["digest"] = _digest(spec)
    documents = {
        "rbfe.edge_spec": spec,
        "rbfe.edge_network": network,
        "rbfe.openfe.complex_transformation": {},
        "rbfe.openfe.solvent_transformation": {},
    }
    controller._read_json = lambda _ref, role: (None, documents[role])
    controller._connect = lambda: (_ for _ in ()).throw(
        AssertionError("invalid network must be rejected before persistence"))
    with pytest.raises(failures.DiracInvalidParameters) as error:
        controller.start({
            "edge_spec_ref": {}, "edge_network_ref": {},
            "complex_transformation_ref": {}, "solvent_transformation_ref": {},
        }, {"kind": "agent", "id": "test"})
    assert "artifacts frozen by one RBFE preflight" in str(error.value)


def test_runset_survives_browser_boundary_and_aggregates_server_side():
    psycopg = pytest.importorskip("psycopg")
    connect = lambda: psycopg.connect("dbname=dirac", autocommit=True)
    try:
        connection = connect()
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL socket is unavailable in this sandbox")
    with connection as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('app.rbfe_run_set')")
        if cur.fetchone()[0] is None:
            pytest.skip("migration 039 is not applied")
        cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='app' AND table_name='rbfe_run_set' "
                    "AND column_name='cancellation_requested_at')")
        if not cur.fetchone()[0]:
            pytest.skip("migration 041 is not applied")
        cur.execute("SELECT id FROM app.job ORDER BY created_at DESC LIMIT 1")
        existing_job = cur.fetchone()
        if existing_job is None:
            pytest.skip("an existing durable Job is required for the FK-backed fake aggregate")
    service = _FinishedService(str(existing_job[0]))
    complex_tx = {"kind": "complex", "component": "ProteinComponent"}
    solvent_tx = {"kind": "solvent", "component": "SolventComponent"}
    campaign_id = str(uuid4())
    campaign_digest = "sha256:" + "c" * 64
    binding = {
        "schema_version": "rbfe-campaign-binding.v2",
        "campaign_id": campaign_id,
        "campaign_scientific_generation": 1,
        "campaign_scientific_digest": campaign_digest,
        "prepared_system_id": str(uuid4()),
        "network_digest": "sha256:" + "d" * 64,
        "verdict": "CONFIRMED",
    }
    binding["digest"] = _digest(binding)
    network = {"schema_version": "1.0", "kind": "rbfe_edge_network",
               "edges": [{"edge_id": "e1"}], "campaign_binding": binding}
    network["digest"] = _digest(network)
    matrix = [{"leg": leg, "repeat_index": repeat,
               "orchestration_seed": repeat * 10 + (1 if leg == "complex" else 2)}
              for repeat in range(1, 4) for leg in ("complex", "solvent")]
    spec = {
        "schema_version": "1.0", "kind": "rbfe_edge_execution_spec",
        "edge_id": "e1", "edge_network_digest": network["digest"],
        "complex_transformation_digest": _digest(complex_tx),
        "solvent_transformation_digest": _digest(solvent_tx),
        "target_ref": {"kind": "target", "id": str(uuid4())},
        "protein_structure_ref": {"kind": "protein_structure", "id": str(uuid4())},
        "thermodynamic_cycle_id": str(uuid4()),
        "ligand_charge_digest": "sha256:" + "b" * 64,
        "execution_matrix": matrix, "campaign_binding": binding,
    }
    spec["digest"] = _digest(spec)
    artifacts = {
        "edge_spec_ref": service.store.put(
            json.dumps(spec).encode(), role="rbfe.edge_spec"),
        "edge_network_ref": service.store.put(
            json.dumps(network).encode(), role="rbfe.edge_network"),
        "complex_transformation_ref": service.store.put(
            json.dumps(complex_tx).encode(),
            role="rbfe.openfe.complex_transformation"),
        "solvent_transformation_ref": service.store.put(
            json.dumps(solvent_tx).encode(),
            role="rbfe.openfe.solvent_transformation"),
    }
    request_key = f"architecture-smoke:{uuid4()}"
    controller = RbfeRunSetController(service, connect)
    run = controller.start({"request_key": request_key,
                            "campaign_id": campaign_id,
                            "campaign_scientific_generation": 1,
                            "campaign_scientific_digest": campaign_digest,
                            **{key: _ref(value) for key, value in artifacts.items()}},
                           {"kind": "agent", "id": "architecture-test"})
    run_id = run["ref"]["id"]
    deadline = time.time() + 5
    while time.time() < deadline:
        run = controller._get(run_id)
        if run["state"] == "completed":
            break
        time.sleep(.05)
    try:
        assert run["state"] == "completed", run["attention"]
        assert len(run["jobs"]) == 6
        assert all(item["state"] == "done" for item in run["jobs"].values())
        assert run["aggregate_job_ref"] is not None
        aggregate_requests = [request_id for method, request_id in service.submissions
                              if method == "physics.motif.rbfe_aggregate"]
        assert aggregate_requests == [
            RbfeRunSetController._aggregate_request_id(run_id, 0)]
        assert service.resolver_calls_at_wait is not None
        assert service.rbfe_reference_resolver.calls > service.resolver_calls_at_wait
    finally:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM app.rbfe_run_set WHERE id=%s", (run_id,))
