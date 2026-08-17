"""Canonical, composite identity for scientific execution and cache v2.

A Method source digest alone is insufficient for ML or physics.  This value
captures every independently mutable scientific input.  Optional components are
serialized as explicit nulls so two producers cannot disagree about whether a
missing key and a null key mean the same computation.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_OCI_DIGEST = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_HARDWARE_PROFILE = re.compile(
    r"^(inline|local_cpu|local_gpu|slurm|kubernetes|hpc_relay):"
    r"(controller-cpu|worker-cpu|gpu):[a-z0-9][a-z0-9_.+-]*"
    r"(?::[a-z0-9][a-z0-9_.+-]*)?$")

EXECUTOR_ADAPTERS = frozenset({
    "inline", "local_cpu", "local_gpu", "slurm", "kubernetes", "hpc_relay",
})
NUMERIC_MODES = frozenset({
    "native", "fp64", "fp32", "tf32", "bf16", "fp16", "mixed",
})
# Modes the fixed CUDA worker can verify before scientific execution.  ``mixed``
# describes an algorithmic autocast policy rather than a process-wide runtime
# fact, so the current worker must not claim it from inventory labels alone.
CUDA_NUMERIC_MODES = frozenset({"fp64", "fp32", "tf32", "bf16", "fp16"})
GPU_ARCHITECTURES = frozenset({
    "blackwell", "hopper", "ada", "ampere", "rocm", "xpu",
})
CUDA_GPU_ARCHITECTURES = frozenset({"blackwell", "hopper", "ada", "ampere"})


def cuda_architecture_for_capability(major: int, minor: int) -> str:
    """Map an observed CUDA compute capability to the canonical architecture.

    Keep this relation beside the identity vocabulary so the worker that emits
    an attestation and the controller that verifies it cannot silently drift.
    Unsupported capabilities are evidence we do not understand, not a license
    to infer an architecture from a product-name string.
    """
    capability = (int(major), int(minor))
    if capability == (8, 9):
        return "ada"
    if capability[0] == 8:
        return "ampere"
    if capability[0] == 9:
        return "hopper"
    if capability[0] in {10, 12}:
        return "blackwell"
    raise ValueError(
        f"unsupported CUDA compute capability {capability[0]}.{capability[1]}")


def sha256_digest(value: bytes | str) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest_or_none(name: str, value: str | None) -> str | None:
    if value is not None and not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be sha256:<64 lowercase hex> or null")
    return value


@dataclass(frozen=True)
class ExecutionIdentity:
    schema_version: str
    method_id: str
    method_descriptor_digest: str
    handler_source_digest: str
    repository_commit: str | None
    container_image: str | None
    dependency_lock_digest: str | None
    runtime_lock_digest: str | None
    executor_adapter: str | None
    checkpoint_digests: tuple[str, ...]
    featurizer_digest: str | None
    dataset_snapshot_digests: tuple[str, ...]
    calibration_digest: str | None
    policy_digest: str | None
    parameter_digest: str | None
    hardware_compatibility_profile: str | None
    numeric_mode: str | None

    @classmethod
    def build(
        cls,
        *,
        method_id: str,
        method_descriptor_digest: str,
        handler_source_digest: str,
        repository_commit: str | None = None,
        container_image: str | None = None,
        dependency_lock_digest: str | None = None,
        runtime_lock_digest: str | None = None,
        executor_adapter: str | None = None,
        checkpoint_digests: Iterable[str] = (),
        featurizer_digest: str | None = None,
        dataset_snapshot_digests: Iterable[str] = (),
        calibration_digest: str | None = None,
        policy_digest: str | None = None,
        parameter_digest: str | None = None,
        hardware_compatibility_profile: str | None = None,
        numeric_mode: str | None = None,
        production: bool = False,
    ) -> "ExecutionIdentity":
        if not method_id:
            raise ValueError("method_id is required")
        _digest_or_none("method_descriptor_digest", method_descriptor_digest)
        _digest_or_none("handler_source_digest", handler_source_digest)
        for name, value in (
            ("dependency_lock_digest", dependency_lock_digest),
            ("runtime_lock_digest", runtime_lock_digest),
            ("featurizer_digest", featurizer_digest),
            ("calibration_digest", calibration_digest),
            ("policy_digest", policy_digest),
            ("parameter_digest", parameter_digest),
        ):
            _digest_or_none(name, value)
        checkpoints = tuple(sorted(set(checkpoint_digests)))
        datasets = tuple(sorted(set(dataset_snapshot_digests)))
        for index, value in enumerate(checkpoints):
            _digest_or_none(f"checkpoint_digests[{index}]", value)
        for index, value in enumerate(datasets):
            _digest_or_none(f"dataset_snapshot_digests[{index}]", value)
        if container_image is not None and not _OCI_DIGEST.fullmatch(container_image):
            raise ValueError("container_image must be an immutable OCI @sha256 reference")
        if repository_commit is not None and not _GIT_COMMIT.fullmatch(repository_commit):
            raise ValueError("repository_commit must be 40 lowercase hexadecimal characters")
        if executor_adapter is not None and executor_adapter not in EXECUTOR_ADAPTERS:
            raise ValueError(
                f"executor_adapter must be one of {sorted(EXECUTOR_ADAPTERS)}")
        if numeric_mode is not None and numeric_mode not in NUMERIC_MODES:
            raise ValueError(f"numeric_mode must be one of {sorted(NUMERIC_MODES)}")
        if (hardware_compatibility_profile is not None
                and not _HARDWARE_PROFILE.fullmatch(
                    hardware_compatibility_profile)):
            raise ValueError(
                "hardware_compatibility_profile must use the canonical "
                "adapter:kind:profile[:resource] vocabulary")
        if (executor_adapter is not None
                and hardware_compatibility_profile is not None
                and not hardware_compatibility_profile.startswith(
                    executor_adapter + ":")):
            raise ValueError(
                "hardware_compatibility_profile adapter must match executor_adapter")
        hardware_parts = (str(hardware_compatibility_profile).split(":")
                          if hardware_compatibility_profile is not None else [])
        hardware_kind = hardware_parts[1] if len(hardware_parts) >= 2 else None
        if executor_adapter in {"inline", "local_cpu"} and hardware_kind not in {
                None, "controller-cpu"}:
            raise ValueError(
                f"{executor_adapter} execution requires controller-cpu hardware")
        if executor_adapter == "local_gpu" and hardware_kind not in {None, "gpu"}:
            raise ValueError("local_gpu execution requires gpu hardware")
        if (executor_adapter in {"slurm", "kubernetes", "hpc_relay"}
                and hardware_kind == "controller-cpu"):
            raise ValueError(
                f"{executor_adapter} execution cannot claim controller-cpu hardware")
        if hardware_kind in {"controller-cpu", "worker-cpu"} and numeric_mode not in {
                None, "native"}:
            raise ValueError("CPU hardware identity requires numeric_mode='native'")
        if hardware_kind == "gpu" and numeric_mode == "native":
            raise ValueError("GPU hardware identity requires an explicit numeric mode")
        if production:
            required = {
                "repository_commit": repository_commit,
                "dependency_lock_digest": dependency_lock_digest,
                "runtime_lock_digest": runtime_lock_digest,
                "executor_adapter": executor_adapter,
                "hardware_compatibility_profile": hardware_compatibility_profile,
                "numeric_mode": numeric_mode,
            }
            missing = sorted(name for name, value in required.items() if value is None)
            if missing:
                raise ValueError(f"production execution identity misses {missing}")
            if executor_adapter not in {"inline", "local_cpu"} and container_image is None:
                raise ValueError(
                    "production remote execution identity requires container_image")
            if len(hardware_parts) >= 2 and hardware_parts[1] == "gpu":
                if len(hardware_parts) != 4 or hardware_parts[2] not in GPU_ARCHITECTURES:
                    raise ValueError(
                        "production GPU hardware profile requires canonical "
                        "adapter:gpu:architecture:memory_bytes")
                try:
                    memory_bytes = int(hardware_parts[3])
                except ValueError as error:
                    raise ValueError(
                        "production GPU hardware profile memory must be integer bytes") \
                        from error
                if memory_bytes < 1:
                    raise ValueError(
                        "production GPU hardware profile memory must be positive")
        return cls(
            schema_version="3.0",
            method_id=method_id,
            method_descriptor_digest=method_descriptor_digest,
            handler_source_digest=handler_source_digest,
            repository_commit=repository_commit,
            container_image=container_image,
            dependency_lock_digest=dependency_lock_digest,
            runtime_lock_digest=runtime_lock_digest,
            executor_adapter=executor_adapter,
            checkpoint_digests=checkpoints,
            featurizer_digest=featurizer_digest,
            dataset_snapshot_digests=datasets,
            calibration_digest=calibration_digest,
            policy_digest=policy_digest,
            parameter_digest=parameter_digest,
            hardware_compatibility_profile=hardware_compatibility_profile,
            numeric_mode=numeric_mode,
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["checkpoint_digests"] = list(self.checkpoint_digests)
        value["dataset_snapshot_digests"] = list(self.dataset_snapshot_digests)
        return value

    def canonical_json(self) -> bytes:
        return _canonical(self.to_dict())

    @property
    def digest(self) -> str:
        return sha256_digest(self.canonical_json())

    def cache_key(self, request: dict[str, Any], *, seed_scope_digest: str | None) -> str:
        _digest_or_none("seed_scope_digest", seed_scope_digest)
        return sha256_digest(
            _canonical(
                {
                    "schema_version": "3.0",
                    "execution_digest": self.digest,
                    "request": request,
                    "seed_scope_digest": seed_scope_digest,
                }
            )
        )
