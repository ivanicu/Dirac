"""Fail-closed gates for externally supplied learned/HPC adapters.

DiffDock and an OpenFE simulation engine are deliberately not smuggled into the
core Python environment: their checkpoints/images and licenses are deployment
artifacts.  These helpers make readiness testable while preventing an installed
look-alike package or mutable image tag from being reported as scientific compute.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


_IMAGE = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


def validate_external_adapter(manifest: dict[str, Any], *, adapter: str) -> dict[str, Any]:
    required = {"adapter", "version", "container_image", "license_artifact_digest",
                "checkpoint_digest", "entrypoint", "validated_fixture_digest"}
    missing = sorted(required - manifest.keys())
    if missing:
        raise ValueError(f"{adapter} manifest misses {missing}")
    if manifest["adapter"] != adapter:
        raise ValueError(f"expected adapter {adapter!r}, got {manifest['adapter']!r}")
    if not _IMAGE.fullmatch(str(manifest["container_image"])):
        raise ValueError("external adapter image must be pinned by sha256 digest")
    for key in ("license_artifact_digest", "checkpoint_digest",
                "validated_fixture_digest"):
        if not _SHA.fullmatch(str(manifest[key])):
            raise ValueError(f"{key} must be a sha256 digest")
    entrypoint = Path(str(manifest["entrypoint"]))
    if not entrypoint.is_absolute() or ".." in entrypoint.parts:
        raise ValueError("adapter entrypoint must be an absolute fixed path")
    normalized = {key: manifest[key] for key in sorted(manifest)}
    normalized["manifest_digest"] = "sha256:" + hashlib.sha256(json.dumps(
        normalized, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode()).hexdigest()
    normalized["ready"] = True
    return normalized


def validate_diffdock_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return validate_external_adapter(manifest, adapter="diffdock")


def validate_openfe_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return validate_external_adapter(manifest, adapter="openfe")


__all__ = ["validate_diffdock_manifest", "validate_external_adapter",
           "validate_openfe_manifest"]
