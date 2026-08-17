from __future__ import annotations

from pathlib import Path
import tempfile
from unittest import mock

import pytest

import catalog
import execution
from execution_control.identity import sha256_digest
from execution_control.production_identity import build_production_identity_resolver
import failures
import jobs
import kernel
import traces


IMAGE = "registry.local/dirac-worker@sha256:" + "a" * 64
COMMIT = "b" * 40


class ProductionExecutor:
    kind = "remote"
    adapter_kind = "kubernetes"
    container_image = IMAGE
    cpu_numeric_mode = "native"
    supports_submission = True

    @staticmethod
    def verified_gpu_profile():
        return {
            "verified": True, "arch": "blackwell", "numeric_mode": "fp32",
            "memory_bytes": 16 << 30,
            "node_selector": {"dirac.io/gpu-arch": "blackwell",
                              "dirac.io/gpu-numeric-mode": "fp32",
                              "dirac.io/gpu-memory-bytes": str(16 << 30)},
        }

    @staticmethod
    def execution_adapter_for(spec):
        return ("kubernetes" if spec.execution.get("resource_class") == "gpu"
                else "local_cpu")


def _spec(method_id: str = "design.motif.acquire"):
    base = catalog.MethodCatalog.load().get(method_id)
    return catalog.MethodCatalog({method_id: base}).bind_versions(
        {method_id: "source-v1"}).get(method_id)


def _resolver(spec, lock: Path, **changes):
    values = {
        "executor": ProductionExecutor(),
        "method_sources": {
            spec.method_id: {
                "version": "source-v1",
                "digest": sha256_digest("source"),
            },
        },
        "repository": lock.parent,
        "dependency_lock_path": lock,
        "repository_commit": COMMIT,
        "runtime_manifest": {"python": "test-runtime", "packages": []},
    }
    values.update(changes)
    return build_production_identity_resolver(**values)


def test_production_identity_seals_source_descriptor_runtime_executor_and_hardware():
    spec = _spec()
    with tempfile.TemporaryDirectory() as temporary:
        lock = Path(temporary) / "requirements.lock.txt"
        lock.write_text("numpy==2.5.2\n", encoding="utf-8")
        identity = _resolver(spec, lock)(spec, {"candidates": [], "capacity": 1})

    assert identity.schema_version == "3.0"
    assert identity.repository_commit == COMMIT
    assert identity.container_image is None
    assert identity.dependency_lock_digest == sha256_digest("numpy==2.5.2\n")
    assert identity.runtime_lock_digest == sha256_digest(
        b'{"packages":[],"python":"test-runtime"}')
    assert identity.executor_adapter == "local_cpu"
    assert identity.handler_source_digest == sha256_digest("source")
    assert identity.method_descriptor_digest == sha256_digest(
        __import__("json").dumps(
            spec.descriptor, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8"))
    assert identity.hardware_compatibility_profile.startswith(
        "local_cpu:controller-cpu:")
    assert identity.numeric_mode == "native"


def test_hybrid_production_identity_uses_remote_image_only_for_gpu_methods():
    spec = _spec("physics.motif.openfe_edge")
    with tempfile.TemporaryDirectory() as temporary:
        lock = Path(temporary) / "requirements.lock.txt"
        lock.write_text("numpy==2.5.2\n", encoding="utf-8")
        identity = _resolver(spec, lock)(spec, {
            "edge_spec_ref": {"kind": "rbfe.edge-spec", "id": "x",
                              "sha256": "sha256:" + "1" * 64},
        })

    assert identity.executor_adapter == "kubernetes"
    assert identity.container_image == IMAGE
    assert identity.hardware_compatibility_profile == (
        f"kubernetes:gpu:blackwell:{16 << 30}")


def test_production_resolver_refuses_mutable_or_local_executor_and_source_drift():
    spec = _spec()
    with tempfile.TemporaryDirectory() as temporary:
        lock = Path(temporary) / "requirements.lock.txt"
        lock.write_text("numpy==2.5.2\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="remote/isolated"):
            _resolver(spec, lock, executor=execution.ThreadExecutor())
        mutable = ProductionExecutor()
        mutable.container_image = "registry.local/dirac-worker:latest"
        with pytest.raises(RuntimeError, match="immutable OCI"):
            _resolver(spec, lock, executor=mutable)
        resolver = _resolver(spec, lock)
        drifted = catalog.MethodCatalog({spec.method_id: spec}).bind_versions(
            {spec.method_id: "source-v2"}).get(spec.method_id)
        with pytest.raises(failures.DiracInternal, match="version drift"):
            resolver(drifted, {"candidates": [], "capacity": 1})


def test_gpu_identity_refuses_unverified_inventory_even_with_plausible_attributes():
    spec = _spec("physics.motif.openfe_edge")

    class AttributeOnlyExecutor(ProductionExecutor):
        gpu_arch = "blackwell"
        gpu_numeric_mode = "fp32"
        verified_gpu_profile = None

    with tempfile.TemporaryDirectory() as temporary:
        lock = Path(temporary) / "requirements.lock.txt"
        lock.write_text("numpy==2.5.2\n", encoding="utf-8")
        resolver = _resolver(spec, lock, executor=AttributeOnlyExecutor())
        with pytest.raises(failures.DiracUnsupported, match="inventory protocol"):
            resolver(spec, {
                "edge_spec_ref": {"kind": "rbfe.edge-spec", "id": "x",
                                  "sha256": "sha256:" + "1" * 64},
            }, execution_adapter="kubernetes")


def test_gpu_identity_refuses_non_enforcing_selector_and_unattestable_mode():
    spec = _spec("physics.motif.openfe_edge")

    class BadSelectorExecutor(ProductionExecutor):
        @staticmethod
        def verified_gpu_profile():
            return {
                "verified": True, "arch": "blackwell", "numeric_mode": "fp32",
                "memory_bytes": 16 << 30,
                "node_selector": {"attacker": "unconstrained"},
            }

    class MixedModeExecutor(ProductionExecutor):
        @staticmethod
        def verified_gpu_profile():
            return {
                "verified": True, "arch": "blackwell", "numeric_mode": "mixed",
                "memory_bytes": 16 << 30,
                "node_selector": {
                    "dirac.io/gpu-arch": "blackwell",
                    "dirac.io/gpu-numeric-mode": "mixed",
                    "dirac.io/gpu-memory-bytes": str(16 << 30),
                },
            }

    class TruthyProfileExecutor(ProductionExecutor):
        @staticmethod
        def verified_gpu_profile():
            return {
                "verified": "yes", "arch": "blackwell", "numeric_mode": "fp32",
                "memory_bytes": True,
                "node_selector": {
                    "dirac.io/gpu-arch": "blackwell",
                    "dirac.io/gpu-numeric-mode": "fp32",
                    "dirac.io/gpu-memory-bytes": "1",
                },
            }

    class NonCudaExecutor(ProductionExecutor):
        @staticmethod
        def verified_gpu_profile():
            return {
                "verified": True, "arch": "rocm", "numeric_mode": "fp32",
                "memory_bytes": 16 << 30,
                "node_selector": {
                    "dirac.io/gpu-arch": "rocm",
                    "dirac.io/gpu-numeric-mode": "fp32",
                    "dirac.io/gpu-memory-bytes": str(16 << 30),
                },
            }

    with tempfile.TemporaryDirectory() as temporary:
        lock = Path(temporary) / "requirements.lock.txt"
        lock.write_text("numpy==2.5.2\n", encoding="utf-8")
        for executor, message in (
                (BadSelectorExecutor(), "node selector"),
                (MixedModeExecutor(), "cannot attest"),
                (TruthyProfileExecutor(), "no verified"),
                (NonCudaExecutor(), "NVIDIA worker")):
            resolver = _resolver(spec, lock, executor=executor)
            with pytest.raises(failures.DiracUnsupported, match=message):
                resolver(spec, {
                    "edge_spec_ref": {"kind": "rbfe.edge-spec", "id": "x",
                                      "sha256": "sha256:" + "1" * 64},
                }, execution_adapter="kubernetes")


def test_every_used_cpu_deployment_boundary_invalidates_the_execution_digest():
    spec = _spec()
    payload = {"candidates": [], "capacity": 1}
    with tempfile.TemporaryDirectory() as temporary:
        lock = Path(temporary) / "requirements.lock.txt"
        lock.write_text("numpy==2.5.2\n", encoding="utf-8")
        baseline = _resolver(spec, lock)(spec, payload).digest

        source_changed = _resolver(spec, lock, method_sources={spec.method_id: {
            "version": "source-v1", "digest": sha256_digest("source-v2")}})(
                spec, payload).digest
        runtime_changed = _resolver(
            spec, lock,
            runtime_manifest={"python": "other-runtime", "packages": []})(
                spec, payload).digest
        other_executor = ProductionExecutor()
        other_executor.container_image = (
            "registry.local/dirac-worker@sha256:" + "c" * 64)
        image_changed = _resolver(spec, lock, executor=other_executor)(spec, payload).digest
        lock.write_text("numpy==2.5.3\n", encoding="utf-8")
        dependency_changed = _resolver(spec, lock)(spec, payload).digest

    # The remote worker image is not part of a controller-local CPU execution.
    # Including it would invalidate valid CPU cache entries when an unused GPU
    # worker is upgraded, while still falsely claiming that image ran the Method.
    assert image_changed == baseline
    assert len({baseline, source_changed, runtime_changed,
                dependency_changed}) == 4


def test_remote_gpu_worker_image_change_invalidates_gpu_execution_identity():
    spec = _spec("physics.motif.openfe_edge")
    payload = {"edge_spec_ref": {"kind": "rbfe.edge-spec", "id": "x",
                                  "sha256": "sha256:" + "1" * 64}}
    with tempfile.TemporaryDirectory() as temporary:
        lock = Path(temporary) / "requirements.lock.txt"
        lock.write_text("numpy==2.5.2\n", encoding="utf-8")
        baseline = _resolver(spec, lock)(spec, payload).digest
        other_executor = ProductionExecutor()
        other_executor.container_image = (
            "registry.local/dirac-worker@sha256:" + "c" * 64)
        changed = _resolver(spec, lock, executor=other_executor)(spec, payload).digest

    assert changed != baseline


def test_kernel_defaults_to_production_and_dev_requires_explicit_opt_out():
    spec = _spec()
    unbound = catalog.MethodCatalog({spec.method_id: spec})
    source_rows = {
        spec.method_id: {"version": "source-v1", "digest": sha256_digest("source")}}
    store = __import__("artifacts").MemoryArtifactStore()

    with mock.patch.object(kernel.catalog.MethodCatalog, "load", return_value=unbound), \
            mock.patch.object(kernel, "source_identities", return_value=source_rows), \
            mock.patch.object(kernel, "default_rbfe_reference_resolver", return_value=None), \
            mock.patch("psycopg.connect", side_effect=RuntimeError("test isolation")):
        service = kernel.build(
            store=store, with_cache=False,
            job_store=jobs.MemoryJobStore(), executor=ProductionExecutor(),
            trace_store=traces.MemoryCommandTraceStore(),
            motif_governance=object(), program_repository=object())
    assert service.production_execution is True
    assert service.execution_identity_resolver is not None
    assert service.execution_identity_mode == "production"

    with mock.patch.object(kernel.catalog.MethodCatalog, "load", return_value=unbound), \
            mock.patch.object(kernel, "default_rbfe_reference_resolver", return_value=None), \
            mock.patch("psycopg.connect", side_effect=RuntimeError("test isolation")):
        service = kernel.build(
            with_versions=False, production_execution=False,
            store=store, with_cache=False, job_store=jobs.MemoryJobStore(),
            executor=execution.ThreadExecutor(),
            trace_store=traces.MemoryCommandTraceStore(),
            motif_governance=object(), program_repository=object())
    assert service.production_execution is False
    assert service.execution_identity_resolver is None
    assert service.execution_identity_mode == "development"

    with mock.patch.object(kernel.catalog.MethodCatalog, "load", return_value=unbound):
        with pytest.raises(RuntimeError, match="production_execution=False explicitly"):
            kernel.build(with_versions=False)
