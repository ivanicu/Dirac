"""Reliable completion barrier for worker output manifests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import failures
from contracts.validation import violations

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_SCHEMA = json.loads(
    (ROOT / "contracts/execution/output-manifest.schema.json").read_text(encoding="utf-8")
)


def validate_output_manifest(
    manifest: dict[str, Any], *, expected_execution_digest: str,
    expected_fencing_token: int, required_roles: Iterable[str],
    artifact_reader: Any,
) -> dict[str, Any]:
    errors = violations(OUTPUT_SCHEMA, manifest)
    if errors:
        first = errors[0]
        raise failures.DiracInternal(
            f"output manifest {first.pointer or '(root)'} {first.message}"
        )
    if manifest["execution_digest"] != expected_execution_digest:
        raise failures.DiracInternal("output manifest execution digest does not match the Attempt")
    if manifest["fencing_token"] != expected_fencing_token:
        raise failures.DiracInternal("STALE_ATTEMPT_RESULT: output fencing token is not current")
    artifacts = manifest["artifacts"]
    roles = {artifact["role"] for artifact in artifacts}
    missing = sorted(set(required_roles) - roles)
    if missing:
        raise failures.DiracInternal(f"required output artifacts are missing: {missing}")
    for artifact in artifacts:
        verified = artifact_reader.verify(artifact["sha256"])
        if verified.size_bytes != artifact["size_bytes"]:
            raise failures.DiracInternal(
                f"artifact {artifact['role']} size does not match its output manifest"
            )
    return manifest
