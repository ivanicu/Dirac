from __future__ import annotations

import json

import pytest

import failures
from artifacts import MemoryArtifactStore
from catalog import MethodCatalog
from execution_control.identity import ExecutionIdentity, sha256_digest
from invocation import HandlerResult, InvocationService
from jobs import MemoryJobStore


class _DurableTenantJobStore(MemoryJobStore):
    """In-memory test double with the production durability contract."""

    kind = "test-durable"
    durability = "durable"


class _TenantArtifactStore(MemoryArtifactStore):
    """Exercise the same Job -> Artifact ownership relation as PostgreSQL."""

    def __init__(self, ledger: _DurableTenantJobStore) -> None:
        super().__init__()
        self.ledger = ledger
        self.links: set[tuple[str, str, str]] = set()

    def link_to_job(self, job_id: str, artifact_id: str, role: str) -> None:
        self.links.add((job_id, artifact_id, role))

    def read_authorized(self, address: str, actor: dict[str, str]):
        artifact, data = self.read(address)
        for job_id, artifact_id, _role in self.links:
            if artifact_id != artifact.id:
                continue
            if self.ledger.get(
                    job_id, actor_kind=actor["kind"], actor_id=actor["id"]):
                return artifact, data
        raise failures.DiracNotFound(f"no artifact at {address!r}")


class _PinnedArtifactCache:
    def __init__(self, result: HandlerResult) -> None:
        self.result = result
        self.lookups = 0

    def lookup(self, _method_id, _payload, *, execution_digest):
        assert execution_digest.startswith("sha256:")
        self.lookups += 1
        return self.result


def _cacheable_embed_catalog() -> MethodCatalog:
    loaded = MethodCatalog.load().get("molecule.embed")
    descriptor = json.loads(json.dumps(loaded.descriptor))
    descriptor["execution"]["cacheable"] = True
    return MethodCatalog({loaded.method_id: type(loaded)(
        method_id=loaded.method_id, summary=loaded.summary,
        descriptor=descriptor, handler_ref=loaded.handler_ref,
        estimate_ref=loaded.estimate_ref, artifacts=loaded.artifacts,
        version="sha256:" + "a" * 64)})


def _production_identity(spec, payload, *, execution_adapter):
    return ExecutionIdentity.build(
        method_id=spec.method_id,
        method_descriptor_digest=sha256_digest(json.dumps(
            spec.descriptor, sort_keys=True, separators=(",", ":"))),
        handler_source_digest=sha256_digest("cache-acl-test-handler"),
        repository_commit="b" * 40,
        dependency_lock_digest=sha256_digest("cache-acl-test-lock"),
        runtime_lock_digest=sha256_digest("cache-acl-test-runtime"),
        executor_adapter=execution_adapter,
        parameter_digest=sha256_digest(json.dumps(
            payload, sort_keys=True, separators=(",", ":"))),
        hardware_compatibility_profile=(
            f"{execution_adapter}:controller-cpu:x86_64:cpu"),
        numeric_mode="native",
        production=True,
    )


def test_two_actors_get_distinct_cache_follower_jobs_and_shared_owned_bytes():
    ledger = _DurableTenantJobStore()
    store = _TenantArtifactStore(ledger)
    artifact_bytes = b"private cached molfile\n" * 4096
    cache = _PinnedArtifactCache(HandlerResult(
        result={
            "molecule": {
                "kind": "molfile",
                "content": "cache identity test molfile".ljust(40, " "),
                "dimensionality": 3,
            },
        },
        artifacts=[("molecule.molfile", artifact_bytes)],
        cache="db",
    ))
    service = InvocationService(
        _cacheable_embed_catalog(), store=store, ledger=ledger, cache=cache,
        production_execution=True,
        execution_identity_resolver=_production_identity,
    )
    payload = {"smiles": "CCO"}
    actor_a = {"kind": "human", "id": "chemist-a"}
    actor_b = {"kind": "human", "id": "chemist-b"}

    response_a = service.invoke("molecule.embed", payload, actor=actor_a)
    response_b = service.invoke("molecule.embed", payload, actor=actor_b)

    assert response_a["ok"] and response_b["ok"]
    job_a = response_a["meta"]["job_id"]
    job_b = response_b["meta"]["job_id"]
    assert job_a and job_b and job_a != job_b
    owned_job_a = service.get_job(job_a, actor=actor_a)
    owned_job_b = service.get_job(job_b, actor=actor_b)
    assert owned_job_a["actor_id"] == "chemist-a"
    assert owned_job_b["actor_id"] == "chemist-b"
    assert owned_job_a["state"] == "done"
    assert owned_job_b["state"] == "done"
    assert owned_job_a["started_at"] is not None
    assert owned_job_b["started_at"] is not None
    assert owned_job_a["finished_at"] is not None
    assert owned_job_b["finished_at"] is not None
    with pytest.raises(failures.DiracNotFound):
        service.get_job(job_a, actor=actor_b)

    ref_a = response_a["artifacts"][0]
    ref_b = response_b["artifacts"][0]
    assert ref_a["inline"] is False and ref_b["inline"] is False
    assert ref_a["id"] == ref_b["id"]
    assert ref_a["sha256"] == ref_b["sha256"]
    assert store.read_authorized(ref_a["id"], actor_a)[1] == artifact_bytes
    assert store.read_authorized(ref_b["id"], actor_b)[1] == artifact_bytes
    assert (job_a, ref_a["id"], "molecule.molfile") in store.links
    assert (job_b, ref_b["id"], "molecule.molfile") in store.links
    assert cache.lookups == 2


def test_production_cache_artifact_refuses_without_durable_actor_handle():
    store = _TenantArtifactStore(_DurableTenantJobStore())
    cache = _PinnedArtifactCache(HandlerResult(
        result={"molecule": {
            "kind": "molfile", "content": "x" * 40, "dimensionality": 3}},
        artifacts=[("molecule.molfile", b"private")], cache="db"))
    service = InvocationService(
        _cacheable_embed_catalog(), store=store, cache=cache,
        production_execution=True,
        execution_identity_resolver=_production_identity,
    )

    response = service.invoke(
        "molecule.embed", {"smiles": "CCO"},
        actor={"kind": "human", "id": "chemist-a"})

    assert response["ok"] is False
    assert response["error"]["code"] == "DB_UNAVAILABLE"
    assert response["error"]["details"]["source_job_reused"] is False
    assert "artifacts" not in response
    assert store.links == set()
