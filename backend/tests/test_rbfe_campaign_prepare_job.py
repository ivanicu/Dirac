from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import threading
from uuid import uuid4

import failures
import jobs
import pytest
from catalog import MethodCatalog
from dirac_app.dispatcher import CommandDispatcher
from execution_control.identity import ExecutionIdentity, sha256_digest
from invocation import HandlerResult, InvocationContext, InvocationService
from motif.rbfe_campaign_prepare_method import prepare_campaign_handler


JOB_ID = "00000000-0000-4000-8000-000000000091"
CAMPAIGN_ID = "00000000-0000-4000-8000-000000000092"
ACTOR = {"kind": "human", "id": "chemist-prepare-test"}


def _payload() -> dict:
    return {
        "request_key": "prepare-jak2-v1",
        "campaign_id": CAMPAIGN_ID,
        "expected_version": 3,
        "campaign_name": "JAK2 analogues",
        "target_name": "JAK2",
        "source_pdb_id": "1CBS",
        "structure_method": "xray",
        "resolution_angstrom": 2.1,
        "receptor_pdb": "ATOM      1  CA  ALA A   1      10.000  10.000  10.000  1.00 20.00           C\n" * 2,
        "compounds": [
            {"id": "parent", "smiles": "CCO"},
            {"id": "proposal-1", "smiles": "CCN"},
        ],
        "parent_id": "parent",
        "pose_strategy": "align_to_reference",
        "reference_ligand": {
            "resname": "LIG",
            "chain": "A",
            "residue_number": "401",
            "role": "experimental_ligand",
            "altloc": None,
            "occupancy": None,
        },
        "receptor_policy": {
            "assembly_id": "deposited",
            "chain_ids": ["A"],
            "missing_atoms": "auto_repair_report",
            "missing_residues": "auto_repair_report",
            "altloc": "highest_occupancy",
            "occupancy": "review_zero",
            "waters": "remove_all",
            "water_site_decisions": [],
            "cofactors": "keep_parameter_gate",
            "metals": "keep_parameter_gate",
            "histidines": "server_assign_review",
            "termini": "server_assign_review",
            "ph": 7.4,
            "forcefield_contract": {"release": "openfe-rfe-standard-v1"},
        },
        "ligand_policy": {
            "formal_charge": "block_changes",
            "tautomer": "strict",
            "protonation": "specified_only",
            "stereochemistry": "preserve_block_unknown",
            "state_population_cutoff": 0.05,
        },
        "minimum_core_coverage": 0.5,
        "seed": 1729,
    }


class _SubmitKernel:
    command_traces = None

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    def submit(self, method_id: str, payload: dict, **kwargs) -> dict:
        self.calls.append((method_id, payload, kwargs))
        return {
            "ok": True,
            "data": {
                "request_key": payload["request_key"],
                "job": {"id": JOB_ID, "state": "queued"},
            },
            "artifacts": [],
            "warnings": [],
            "meta": {"job_id": JOB_ID, "execution_mode": "job"},
        }


def test_prepare_command_returns_durable_job_and_forwards_identity() -> None:
    kernel = _SubmitKernel()
    payload = _payload()
    response = CommandDispatcher(kernel).execute(
        "physics.rbfe-campaign.prepare", payload,
        actor=ACTOR, request_id="request-prepare-1")

    assert response["ok"] is True, response
    assert response["data"]["request_key"] == payload["request_key"]
    assert response["data"]["job"]["id"] == JOB_ID
    assert response["meta"]["job_id"] == JOB_ID
    assert kernel.calls == [(
        "physics.motif.rbfe_campaign_prepare", payload,
        {
            "request_id": "request-prepare-1",
            "actor": ACTOR,
            "command_id": "physics.rbfe-campaign.prepare",
        },
    )]


class _CaptureExecutor:
    kind = "thread"
    adapter_kind = "local_cpu"
    supports_submission = True

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._lock = threading.Lock()

    def submit(self, fn, *args) -> Future:
        # Deliberately do not execute native preparation.  A pending Future is
        # enough to prove how many dispatches the API admitted.
        with self._lock:
            self.calls.append((fn, args))
        return Future()


def _production_identity(spec, payload, *, execution_adapter):
    return ExecutionIdentity.build(
        method_id=spec.method_id,
        method_descriptor_digest=sha256_digest("prepare-test-descriptor"),
        handler_source_digest=sha256_digest("prepare-test-handler"),
        repository_commit="b" * 40,
        dependency_lock_digest=sha256_digest("prepare-test-dependencies"),
        runtime_lock_digest=sha256_digest("prepare-test-runtime"),
        executor_adapter=execution_adapter,
        parameter_digest=sha256_digest(repr(sorted(payload))),
        hardware_compatibility_profile=(
            f"{execution_adapter}:controller-cpu:x86_64:cpu"),
        numeric_mode="native",
        production=True,
    )


def _service() -> tuple[InvocationService, jobs.MemoryJobStore, _CaptureExecutor]:
    ledger = jobs.MemoryJobStore()
    executor = _CaptureExecutor()
    return InvocationService(
        MethodCatalog.load(), ledger=ledger, executor=executor,
    ), ledger, executor


def _submit(service: InvocationService, payload: dict | None = None, *,
            actor: dict | None = None,
            command_id: str = "physics.rbfe-campaign.prepare") -> dict:
    return service.submit(
        "physics.motif.rbfe_campaign_prepare", payload or _payload(),
        actor=actor or ACTOR, command_id=command_id,
        request_id="request-prepare-exactly-once",
    )


def test_prepare_request_key_is_required_by_command_contract() -> None:
    payload = _payload()
    payload.pop("request_key")
    with pytest.raises(failures.DiracInvalidParameters):
        CommandDispatcher(_SubmitKernel()).execute(
            "physics.rbfe-campaign.prepare", payload, actor=ACTOR,
        )


@pytest.mark.parametrize("whitespace_only", [
    "\n\t", "\u00a0", "\u2007", "\u202f", "\ufeff",
])
def test_prepare_request_key_rejects_unicode_whitespace_at_api(
        whitespace_only: str) -> None:
    payload = _payload()
    payload["request_key"] = whitespace_only
    with pytest.raises(failures.DiracInvalidParameters):
        CommandDispatcher(_SubmitKernel()).execute(
            "physics.rbfe-campaign.prepare", payload, actor=ACTOR,
        )


def test_prepare_replays_same_job_across_every_terminal_state() -> None:
    for terminal in ("done", "failed", "cancelled"):
        service, ledger, executor = _service()
        first = _submit(service)
        job_id = first["data"]["job"]["id"]
        if terminal == "cancelled":
            ledger.request_cancel(
                job_id, actor_kind=ACTOR["kind"], actor_id=ACTOR["id"],
            )
        else:
            ledger.start(job_id)
            if terminal == "done":
                ledger.done(job_id, seconds=0.01)
            else:
                ledger.failed(
                    job_id, code="UNSUPPORTED", detail="terminal replay fixture",
                )

        replay = _submit(service)
        assert replay["data"]["request_key"] == _payload()["request_key"]
        assert replay["data"]["job"]["id"] == job_id
        assert replay["data"]["job"]["state"] == terminal
        assert replay["meta"]["deduplicated"] is True
        assert len(executor.calls) == 1, terminal


def test_prepare_same_key_with_different_payload_is_http_409() -> None:
    service, _ledger, executor = _service()
    dispatcher = CommandDispatcher(service)
    first = dispatcher.execute(
        "physics.rbfe-campaign.prepare", _payload(), actor=ACTOR,
    )
    changed = _payload()
    changed["target_name"] = "A different operation"
    conflict = dispatcher.execute(
        "physics.rbfe-campaign.prepare", changed, actor=ACTOR,
    )

    assert first["ok"] is True, first
    assert conflict["ok"] is False, conflict
    assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert failures.DiracIdempotencyConflict("fixture").http_status == 409
    assert len(executor.calls) == 1


def test_prepare_two_concurrent_retries_dispatch_once() -> None:
    service, _ledger, executor = _service()
    barrier = threading.Barrier(3)

    def submit_after_barrier() -> dict:
        barrier.wait(timeout=3)
        return _submit(service)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit_after_barrier) for _ in range(2)]
        barrier.wait(timeout=3)
        responses = [future.result(timeout=5) for future in futures]

    ids = {response["data"]["job"]["id"] for response in responses}
    assert len(ids) == 1
    assert {response["data"]["request_key"] for response in responses} == {
        _payload()["request_key"],
    }
    assert sum(bool(response["meta"]["deduplicated"])
               for response in responses) == 1
    assert len(executor.calls) == 1


def test_prepare_request_key_is_scoped_by_actor_and_command() -> None:
    service, _ledger, executor = _service()
    first = _submit(service)
    other_actor = _submit(
        service, actor={"kind": "human", "id": "chemist-prepare-other"},
    )
    other_command = _submit(
        service, command_id="test.prepare-alternative-command",
    )

    assert len({
        first["data"]["job"]["id"],
        other_actor["data"]["job"]["id"],
        other_command["data"]["job"]["id"],
    }) == 3
    assert len(executor.calls) == 3


def test_production_prepare_refuses_process_only_request_key_ledger() -> None:
    ledger = jobs.MemoryJobStore()
    executor = _CaptureExecutor()
    service = InvocationService(
        MethodCatalog.load(), ledger=ledger, executor=executor,
        production_execution=True,
        execution_identity_resolver=_production_identity,
    )

    with pytest.raises(failures.DiracFailure) as caught:
        _submit(service)

    assert caught.value.code == "DB_UNAVAILABLE"
    assert caught.value.details["job_durability"] == "process"
    assert executor.calls == []


def test_prepare_request_key_migration_is_all_state_and_actor_scoped() -> None:
    source = (Path(__file__).parents[1] / "db" / "migrations" /
              "046_job_command_request_key.sql").read_text(encoding="utf-8")
    index = source.split(
        "CREATE UNIQUE INDEX job_command_request_key_once", 1)[1].split(
            ";", 1)[0]
    assert "(actor_kind, actor_id, command_id, request_key)" in index
    assert "WHERE request_key IS NOT NULL" in index
    assert "state" not in index
    assert "input_sha256" in source
    assert "job_request_key_nonempty" in source
    assert "job_request_key_length" in source
    assert "job_request_key_has_command" in source
    assert "[^[:space:]" in source
    assert "U&'\\00A0\\2007\\202F\\FEFF'" in source
    assert "COMMENT ON INDEX app.job_one_inflight" in source


def test_prepare_conflict_maps_to_http_409_without_starting_a_job() -> None:
    import field_server

    service, _ledger, executor = _service()
    original = field_server._dispatcher_singleton
    field_server._dispatcher_singleton = CommandDispatcher(service)
    try:
        body = {
            "command": "physics.rbfe-campaign.prepare",
            "input": _payload(),
        }
        first_status, first = field_server.handle_v2(
            "POST", "/v2/execute", body, actor=ACTOR,
        )
        changed = deepcopy(body)
        changed["input"]["target_name"] = "Different payload under same key"
        conflict_status, conflict = field_server.handle_v2(
            "POST", "/v2/execute", changed, actor=ACTOR,
        )
    finally:
        field_server._dispatcher_singleton = original

    assert first_status == 202 and first["ok"] is True
    assert conflict_status == 409 and conflict["ok"] is False
    assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(executor.calls) == 1


def test_postgres_prepare_request_key_exactly_once_contract() -> None:
    """Temp-DB acceptance interface; never points at production by default."""
    dsn = os.environ.get("DIRAC_TEST_DSN")
    if not dsn:
        pytest.skip("requires isolated PostgreSQL DIRAC_TEST_DSN with migrations 000-047")
    psycopg = pytest.importorskip("psycopg")
    connect = lambda: psycopg.connect(dsn, autocommit=True)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='app' AND table_name='job' "
            "AND column_name='request_key'),"
            "to_regclass('app.job_command_request_key_once') IS NOT NULL,"
            "(SELECT id::text FROM meta.method ORDER BY declared_at LIMIT 1)")
        column, index, method_row_id = cursor.fetchone()
    assert column and index and method_row_id, "isolated DB is missing migrations 046-047"

    ledger = jobs.JobLedger(connect, "fields/test/prepare-exactly-once")
    actor_id = f"prepare-pg-{uuid4()}"
    base = dict(
        method_row_id=method_row_id,
        input_sha256=hashlib.sha256(b"canonical prepare payload").digest(),
        params={}, actor_kind="human", actor_id=actor_id,
        command_id="physics.rbfe-campaign.prepare",
        request_key="prepare-db-replay", queued=True,
        request_digest=hashlib.sha256(b"execution identity").digest(),
        dispatch_payload={"probe": "exactly-once"},
        execution_adapter="local_cpu",
    )
    first, joined = ledger.open(**base)
    assert first and joined is False
    ledger.start(first)
    ledger.done(first, seconds=0.01)
    replay, joined = ledger.open(**base)
    assert replay == first and joined is True

    changed = dict(base)
    changed["input_sha256"] = hashlib.sha256(b"changed payload").digest()
    changed["request_digest"] = hashlib.sha256(b"changed execution").digest()
    with pytest.raises(failures.DiracIdempotencyConflict):
        ledger.open(**changed)


def test_postgres_prepare_request_key_concurrency_dispatch_claims_once() -> None:
    dsn = os.environ.get("DIRAC_TEST_DSN")
    if not dsn:
        pytest.skip("requires isolated PostgreSQL DIRAC_TEST_DSN with migrations 000-047")
    psycopg = pytest.importorskip("psycopg")
    connect = lambda: psycopg.connect(dsn, autocommit=True)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id::text FROM meta.method ORDER BY declared_at LIMIT 1")
        method_row_id = cursor.fetchone()[0]
    ledger = jobs.JobLedger(connect, "fields/test/prepare-concurrency")
    actor_id = f"prepare-pg-race-{uuid4()}"
    request = dict(
        method_row_id=method_row_id,
        input_sha256=hashlib.sha256(actor_id.encode()).digest(), params={},
        actor_kind="human", actor_id=actor_id,
        command_id="physics.rbfe-campaign.prepare",
        request_key="same-racing-key", queued=True,
        request_digest=hashlib.sha256((actor_id + ":execution").encode()).digest(),
        dispatch_payload={"probe": "concurrent-exactly-once"},
        execution_adapter="local_cpu",
    )
    barrier = threading.Barrier(3)

    def claim() -> tuple[str | None, bool]:
        barrier.wait(timeout=5)
        return ledger.open(**request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        barrier.wait(timeout=5)
        results = [future.result(timeout=10) for future in futures]
    assert len({job_id for job_id, _ in results}) == 1
    assert sorted(joined for _, joined in results) == [False, True]
    ledger.request_cancel(
        results[0][0], actor_kind="human", actor_id=actor_id,
    )


@pytest.mark.parametrize("whitespace_only", [
    "\n\t", "\u00a0", "\u2007", "\u202f", "\ufeff",
])
def test_postgres_prepare_request_key_rejects_unicode_whitespace(
        whitespace_only: str) -> None:
    """Temp-DB negative control for JSON-Schema/PostgreSQL parity."""
    dsn = os.environ.get("DIRAC_TEST_DSN")
    if not dsn:
        pytest.skip("requires isolated PostgreSQL DIRAC_TEST_DSN with migrations 000-047")
    psycopg = pytest.importorskip("psycopg")
    connect = lambda: psycopg.connect(dsn, autocommit=True)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id::text FROM meta.method ORDER BY declared_at LIMIT 1")
        method_row_id = cursor.fetchone()[0]
    ledger = jobs.JobLedger(connect, "fields/test/prepare-whitespace")
    with pytest.raises(failures.DiracFailure) as caught:
        ledger.open(
            method_row_id=method_row_id,
            input_sha256=hashlib.sha256(b"whitespace request key").digest(),
            params={}, actor_kind="human", actor_id=f"whitespace-{uuid4()}",
            command_id="physics.rbfe-campaign.prepare",
            request_key=whitespace_only, queued=True,
            request_digest=hashlib.sha256(b"whitespace execution").digest(),
            dispatch_payload={"probe": "whitespace-negative"},
            execution_adapter="local_cpu",
        )
    assert caught.value.code == "DB_UNAVAILABLE"


def test_prepare_method_rejects_synchronous_invoke_before_science() -> None:
    service = InvocationService(MethodCatalog.load())
    response = service.invoke(
        "physics.motif.rbfe_campaign_prepare", _payload(), actor=ACTOR)

    assert response["ok"] is False
    assert response["error"]["code"] == "UNSUPPORTED"
    assert response["error"]["details"]["supported_modes"] == ["job"]
    assert response["error"]["details"]["required_endpoint"] == "/v2/jobs"


def test_prepare_wrapper_passes_resolver_writer_payload_and_actor() -> None:
    observed: dict = {}

    class Resolver:
        def prepare_campaign(self, payload, writer, actor, *, job_id,
                             dispatch_fence):
            observed.update(
                payload=payload, writer=writer, actor=actor, job_id=job_id,
                dispatch_fence=dispatch_fence)
            return {
                "campaign_ref": {"kind": "campaign", "id": CAMPAIGN_ID},
                "campaign_version": 4,
                "campaign_state_digest": "sha256:" + "a" * 64,
                "campaign_scientific_ref": {
                    "kind": "rbfe_campaign", "id": CAMPAIGN_ID,
                    "version": 1, "sha256": "sha256:" + "b" * 64,
                },
                "campaign_scientific_generation": 1,
                "campaign_scientific_digest": "sha256:" + "b" * 64,
                "prepared_receptor_state_ref": {
                    "kind": "prepared_receptor_state", "id": JOB_ID,
                },
                "target_ref": {"kind": "target", "id": "JAK2"},
                "protein_structure_ref": {
                    "kind": "protein_structure", "id": "1CBS",
                },
                "preparation_state": "server-attested",
                "poses": [],
                "claim_boundary": "preparation only",
            }

    payload = _payload()
    writer = object()
    actor = deepcopy(ACTOR)
    result = prepare_campaign_handler(payload, InvocationContext(
        method_id="physics.motif.rbfe_campaign_prepare",
        actor=actor,
        job_id=JOB_ID,
        artifact_writer=writer,
        rbfe_reference_resolver=Resolver(),
    ))

    assert isinstance(result, HandlerResult)
    assert observed["payload"] is payload
    assert observed["writer"] is writer
    assert observed["actor"] is actor
    assert observed["job_id"] == JOB_ID
    assert callable(observed["dispatch_fence"])
    assert result.result["campaign_version"] == 4
    assert result.result["campaign_scientific_generation"] == 1
    assert result.provenance["actor"] == ACTOR
    assert result.provenance["job_id"] == JOB_ID
    assert result.provenance["artifact_lineage"] == (
        "app.job_artifact+app.rbfe_campaign_artifact")
    assert result.provenance["cancellation_boundary"] == (
        "before_scientific_side_effects")


def test_prepare_wrapper_fails_closed_without_durable_writer() -> None:
    class Resolver:
        def prepare_campaign(self, *_args):  # pragma: no cover - must stay unused
            raise AssertionError("resolver must not run without an artifact writer")

    try:
        prepare_campaign_handler(_payload(), InvocationContext(
            method_id="physics.motif.rbfe_campaign_prepare",
            actor=ACTOR,
            job_id=JOB_ID,
            rbfe_reference_resolver=Resolver(),
        ))
    except Exception as error:  # typed failure asserted without transport shaping
        assert getattr(error, "code", None) == "DB_UNAVAILABLE"
    else:  # pragma: no cover
        raise AssertionError("missing writer must refuse before resolver execution")


def test_prepare_wrapper_fails_closed_without_durable_job_identity() -> None:
    class Resolver:
        def prepare_campaign(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("resolver must not run without a Job id")

    try:
        prepare_campaign_handler(_payload(), InvocationContext(
            method_id="physics.motif.rbfe_campaign_prepare",
            actor=ACTOR,
            artifact_writer=object(),
            rbfe_reference_resolver=Resolver(),
        ))
    except Exception as error:
        assert getattr(error, "code", None) == "INTERNAL"
        assert "job-only" in str(error)
    else:  # pragma: no cover
        raise AssertionError("missing Job identity must refuse before preparation")
