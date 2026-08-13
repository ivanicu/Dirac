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
        if production:
            required = {
                "repository_commit": repository_commit,
                "container_image": container_image,
                "dependency_lock_digest": dependency_lock_digest,
                "hardware_compatibility_profile": hardware_compatibility_profile,
                "numeric_mode": numeric_mode,
            }
            missing = sorted(name for name, value in required.items() if value is None)
            if missing:
                raise ValueError(f"production execution identity misses {missing}")
        return cls(
            schema_version="2.0",
            method_id=method_id,
            method_descriptor_digest=method_descriptor_digest,
            handler_source_digest=handler_source_digest,
            repository_commit=repository_commit,
            container_image=container_image,
            dependency_lock_digest=dependency_lock_digest,
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
                    "schema_version": "2.0",
                    "execution_digest": self.digest,
                    "request": request,
                    "seed_scope_digest": seed_scope_digest,
                }
            )
        )
