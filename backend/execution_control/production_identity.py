"""Fail-closed execution identity for the deployed Invocation kernel.

The development fallback in :mod:`invocation` is intentionally incomplete: it is
useful for isolated unit tests, but it must never mint a production cache key.  This
module seals the independently mutable deployment facts that can change a scientific
answer even when the request and Method descriptor are unchanged.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any, Callable, Mapping

import failures
from execution_control.identity import (
    CUDA_NUMERIC_MODES,
    CUDA_GPU_ARCHITECTURES,
    EXECUTOR_ADAPTERS,
    GPU_ARCHITECTURES,
    NUMERIC_MODES,
    ExecutionIdentity,
    sha256_digest,
)


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_OCI_DIGEST = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_RESOURCE_CLASSES = frozenset({
    "cpu", "cpu-classical", "cpu-cheminformatics", "cpu-qm", "gpu",
})


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")


def _file_digest(path: Path, *, label: str) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"production identity cannot read {label}: {path}") from exc
    if not data:
        raise RuntimeError(f"production identity refuses an empty {label}: {path}")
    return sha256_digest(data)


def _repository_commit(repository: Path, declared: str | None = None) -> str:
    value = declared or os.environ.get("DIRAC_SOURCE_COMMIT")
    if value is None:
        try:
            result = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                "production identity requires DIRAC_SOURCE_COMMIT or a readable Git HEAD"
            ) from exc
        value = result.stdout.strip()
    if not _COMMIT.fullmatch(value):
        raise RuntimeError(
            "production identity repository commit must be 40 lowercase hex characters")
    return value


def _executor_adapter(executor: Any, spec: Any | None = None) -> str:
    route = getattr(executor, "execution_adapter_for", None)
    if spec is not None and callable(route):
        routed = str(route(spec) or "").strip()
        return routed or "unconfigured"
    explicit = getattr(executor, "adapter_kind", None)
    if explicit:
        return str(explicit)
    return {
        "inline": "inline",
        "thread": "local_cpu",
        "process": "local_cpu",
    }.get(str(getattr(executor, "kind", "")), "unconfigured")


def installed_runtime_manifest(*, dependency_lock_digest: str) -> dict[str, Any]:
    """Canonical identity of the API/controller Python runtime actually loaded."""
    distributions = sorted(
        ({"name": (dist.metadata.get("Name") or "unknown").lower(),
          "version": dist.metadata.get("Version") or "unknown"}
         for dist in importlib.metadata.distributions()),
        key=lambda item: (item["name"], item["version"]))
    return {
        "schema_version": "1.0",
        "kind": "dirac_api_controller_runtime",
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "cache_tag": sys.implementation.cache_tag,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "dependency_lock_digest": dependency_lock_digest,
        "distributions": distributions,
    }


def _declared_digest(value: Any) -> str | None:
    if isinstance(value, dict):
        candidate = value.get("digest") or value.get("sha256")
    else:
        candidate = value
    if isinstance(candidate, str) and _SHA256.fullmatch(candidate):
        return candidate
    return None


def _dataset_digests(payload: Mapping[str, Any]) -> tuple[str, ...]:
    found: set[str] = set()

    def visit(key: str, value: Any) -> None:
        if "dataset" in key.lower() or "snapshot" in key.lower():
            digest = _declared_digest(value)
            if digest:
                found.add(digest)
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(str(child_key), child)
        elif isinstance(value, list):
            for child in value:
                visit(key, child)

    for root_key, root_value in payload.items():
        visit(str(root_key), root_value)
    return tuple(sorted(found))


def build_production_identity_resolver(
        *, executor: Any,
        method_sources: Mapping[str, Mapping[str, str]],
        repository: Path,
        dependency_lock_path: Path,
        repository_commit: str | None = None,
        runtime_manifest: Mapping[str, Any] | None = None,
        ) -> Callable[[Any, dict], ExecutionIdentity]:
    """Build one immutable resolver or refuse kernel startup.

    ``method_sources`` is produced from ``method_registry.plan`` and therefore
    carries the full transitive source/constant digest, not the 12-character
    display version.  Runtime facts are captured once at kernel construction so a
    process cannot mint different identities half way through its lifetime.
    """
    deployment_adapter = _executor_adapter(executor)
    if deployment_adapter in {"inline", "local_cpu", "unconfigured"}:
        raise RuntimeError(
            "production execution requires an explicitly configured remote/isolated "
            f"executor adapter; got {deployment_adapter!r}")
    worker_image = str(getattr(executor, "container_image", "") or "")
    if not _OCI_DIGEST.fullmatch(worker_image):
        raise RuntimeError(
            "production execution requires executor.container_image as an immutable "
            "OCI name@sha256 digest")
    dependency_digest = _file_digest(
        dependency_lock_path, label="dependency lock")
    runtime_document = dict(runtime_manifest or installed_runtime_manifest(
        dependency_lock_digest=dependency_digest))
    runtime_digest = sha256_digest(_canonical(runtime_document))
    commit = _repository_commit(repository, repository_commit)
    source_rows = {str(key): dict(value) for key, value in method_sources.items()}
    if not source_rows:
        raise RuntimeError("production identity has no running Method source manifest")

    def resolve(
            spec: Any, payload: dict, *,
            execution_adapter: str | None = None) -> ExecutionIdentity:
        source = source_rows.get(spec.method_id)
        if source is None:
            raise failures.DiracInternal(
                f"production identity has no source witness for {spec.method_id}")
        source_digest = str(source.get("digest") or "")
        source_version = str(source.get("version") or "")
        if not _SHA256.fullmatch(source_digest):
            raise failures.DiracInternal(
                f"production source witness for {spec.method_id} is not a full SHA-256")
        if not spec.version or spec.version != source_version:
            raise failures.DiracInternal(
                f"production Method version drift for {spec.method_id}: "
                f"catalog={spec.version!r}, source={source_version!r}")

        resource_class = str(spec.execution.get("resource_class") or "")
        if resource_class not in _RESOURCE_CLASSES:
            raise failures.DiracUnsupported(
                f"{spec.method_id} has no recognized execution resource class",
                details={"method_id": spec.method_id,
                         "resource_class": resource_class or None,
                         "known_resource_classes": sorted(_RESOURCE_CLASSES)})
        gpu = resource_class == "gpu"
        adapter = str(execution_adapter or _executor_adapter(executor, spec))
        if adapter not in EXECUTOR_ADAPTERS:
            raise failures.DiracUnsupported(
                f"production identity refuses unknown execution adapter {adapter!r}",
                details={"method_id": spec.method_id,
                         "executor_adapter": adapter})
        supported = tuple(spec.execution.get("supported_adapters") or ())
        if not supported:
            raise failures.DiracUnsupported(
                f"{spec.method_id} does not declare any supported execution adapter",
                details={"method_id": spec.method_id,
                         "executor_adapter": adapter})
        if adapter not in supported:
            raise failures.DiracUnsupported(
                f"production identity route {adapter!r} is not declared for "
                f"{spec.method_id}",
                details={"method_id": spec.method_id,
                         "executor_adapter": adapter,
                         "supported_adapters": list(supported)})
        declared_gpu_arch = ""
        declared_gpu_memory_bytes = 0
        numeric_mode = str(getattr(executor, "cpu_numeric_mode", "native"))
        if gpu:
            inventory = getattr(executor, "verified_gpu_profile", None)
            if not callable(inventory):
                raise failures.DiracUnsupported(
                    f"production GPU identity for {spec.method_id} has no verified "
                    "inventory protocol")
            profile = dict(inventory())
            if profile.get("verified") is not True:
                raise failures.DiracUnsupported(
                    f"production GPU identity for {spec.method_id} has no verified "
                    "healthy GPU inventory")
            declared_gpu_arch = str(profile.get("arch") or "")
            numeric_mode = str(profile.get("numeric_mode") or "")
            try:
                if type(profile.get("memory_bytes")) is not int:
                    raise TypeError("GPU memory capacity must be an integer")
                declared_gpu_memory_bytes = int(profile.get("memory_bytes") or 0)
            except (TypeError, ValueError):
                declared_gpu_memory_bytes = 0
            if declared_gpu_arch not in GPU_ARCHITECTURES:
                raise failures.DiracUnsupported(
                    "verified GPU inventory returned an unknown architecture",
                    details={"gpu_arch": declared_gpu_arch or None})
            if numeric_mode not in NUMERIC_MODES - {"native"}:
                raise failures.DiracUnsupported(
                    "verified GPU inventory returned an unknown numeric mode",
                    details={"numeric_mode": numeric_mode or None})
            if declared_gpu_memory_bytes < 1:
                raise failures.DiracUnsupported(
                    "verified GPU inventory returned no physical memory capacity")
            if adapter == "kubernetes":
                expected_selector = {
                    "dirac.io/gpu-arch": declared_gpu_arch,
                    "dirac.io/gpu-numeric-mode": numeric_mode,
                    "dirac.io/gpu-memory-bytes": str(
                        declared_gpu_memory_bytes),
                }
                if numeric_mode not in CUDA_NUMERIC_MODES:
                    raise failures.DiracUnsupported(
                        "Kubernetes worker cannot attest this GPU numeric mode",
                        details={"numeric_mode": numeric_mode})
                if declared_gpu_arch not in CUDA_GPU_ARCHITECTURES:
                    raise failures.DiracUnsupported(
                        "Kubernetes NVIDIA worker cannot attest this GPU architecture",
                        details={"gpu_arch": declared_gpu_arch})
                if profile.get("node_selector") != expected_selector:
                    raise failures.DiracUnsupported(
                        "verified Kubernetes GPU profile has no exact enforcing "
                        "node selector",
                        details={"expected_node_selector": expected_selector})
        hardware = (
            f"{adapter}:gpu:{declared_gpu_arch}:{declared_gpu_memory_bytes}"
            if gpu else f"{adapter}:controller-cpu:{platform.machine()}:{resource_class}")

        checkpoint = _declared_digest(payload.get("checkpoint"))
        calibration = _declared_digest(payload.get("calibration"))
        featurizer = _declared_digest(payload.get("featurizer"))
        policy = _declared_digest(payload.get("policy"))
        return ExecutionIdentity.build(
            method_id=spec.method_id,
            method_descriptor_digest=sha256_digest(_canonical(spec.descriptor)),
            handler_source_digest=source_digest,
            repository_commit=commit,
            # CPU Methods in the Kubernetes-backed service execute in the API
            # controller and are identified by the captured controller runtime,
            # not by an unused remote worker image.
            container_image=worker_image if adapter != "local_cpu" else None,
            dependency_lock_digest=dependency_digest,
            runtime_lock_digest=runtime_digest,
            executor_adapter=adapter,
            checkpoint_digests=(checkpoint,) if checkpoint else (),
            featurizer_digest=featurizer,
            dataset_snapshot_digests=_dataset_digests(payload),
            calibration_digest=calibration,
            policy_digest=policy,
            parameter_digest=sha256_digest(_canonical(payload)),
            hardware_compatibility_profile=hardware,
            numeric_mode=numeric_mode,
            production=True,
        )

    return resolve
