"""Fixed, network-isolated Kubernetes entrypoint for one Motif ExecutionRequest."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any

import failures
from catalog import MethodCatalog
from execution_control.protocol import validate_execution_request
from invocation import HandlerResult, InvocationContext


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def _gpu_evidence(request: dict[str, Any]) -> dict[str, Any]:
    if request["resource_request"]["gpus"] < 1:
        return {"requested": False}
    import torch
    if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
        raise RuntimeError("GPU was requested but CUDA is not available in the worker")
    return {
        "requested": True, "available": True,
        "torch": torch.__version__, "cuda": torch.version.cuda,
        "device_count": torch.cuda.device_count(),
        "device_name": torch.cuda.get_device_name(0),
    }


def _verify_method_version(method_id: str, expected: str | None) -> None:
    """Refuse API/worker source drift before scientific execution starts."""
    if not expected:
        raise RuntimeError("input manifest is missing method_version")
    import field_server as field_server_module
    import method_registry
    versions = {
        row["method_id"]: row["version"]
        for row in method_registry.plan(field_server_module)
    }
    actual = versions.get(method_id)
    if actual != expected:
        raise RuntimeError(
            f"worker method version mismatch: expected={expected}, actual={actual}")


def run(request_path: Path, exchange_root: Path) -> int:
    started_at = _utcnow()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    validate_execution_request(request)
    output_dir = exchange_root / "outputs" / request["attempt_id"]
    result_path = output_dir / "worker-result.json"
    identity = {key: request[key] for key in (
        "job_id", "attempt_id", "fencing_token", "execution_digest", "method_id")}
    try:
        input_id = request["input_manifest_artifact_id"]
        input_path = exchange_root / "inputs" / f"{input_id}.json"
        input_bytes = input_path.read_bytes()
        expected_sha = request.get("environment", {}).get("MOTIF_INPUT_SHA256")
        actual_sha = hashlib.sha256(input_bytes).hexdigest()
        if not expected_sha or actual_sha != expected_sha:
            raise RuntimeError("input manifest digest mismatch")
        document = json.loads(input_bytes)
        for key in ("method_id", "job_id", "attempt_id", "execution_digest"):
            if document.get(key) != request.get(key):
                raise RuntimeError(f"input manifest {key} does not match ExecutionRequest")

        catalog = MethodCatalog.load()
        spec = catalog.get(request["method_id"])
        _verify_method_version(spec.method_id, document.get("method_version"))
        payload = document["payload"]
        catalog.validate(spec.method_id, payload)
        handler = spec.handler()
        gpu = _gpu_evidence(request)
        deadline = time.time() + request["resource_request"]["walltime_seconds"]
        context = InvocationContext(
            method_id=spec.method_id,
            version=document.get("method_version"),
            execution_digest=request["execution_digest"],
            actor=dict(request["security_context"]["actor"]),
            budget_seconds=document.get("budget_seconds"),
            job_id=request["job_id"], spec=spec, deadline=deadline)
        output = handler(payload, context)
        if not isinstance(output, HandlerResult):
            raise RuntimeError(
                f"handler returned {type(output).__name__}, expected HandlerResult")
        catalog.validate_output(spec.method_id, output.result)

        artifacts = []
        for index, (role, data) in enumerate(output.artifacts):
            filename = f"artifacts/{index:04d}.bin"
            path = output_dir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            artifacts.append({
                "role": role, "filename": filename,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            })
        provenance = dict(output.provenance)
        provenance["remote_execution"] = {
            "backend": "kubernetes", "scheduler": "kueue",
            "allocation_id": os.environ.get("DIRAC_ALLOCATION_ID"),
            "gpu": gpu,
        }
        _atomic_json(result_path, {
            "schema_version": "1.0", **identity, "ok": True,
            "handler_result": {
                "result": output.result, "warnings": output.warnings,
                "provenance": provenance,
                "parameters_used": output.parameters_used,
                "cache": output.cache, "cache_record": output.cache_record,
            },
            "artifacts": artifacts,
            "started_at": started_at, "finished_at": _utcnow(),
        })
        print(json.dumps({**identity, "event": "motif_worker_completed",
                          "artifact_count": len(artifacts)}, sort_keys=True), flush=True)
        return 0
    except failures.DiracFailure as error:
        _atomic_json(result_path, {
            "schema_version": "1.0", **identity, "ok": False,
            "error": error.to_error_payload(), "artifacts": [],
            "started_at": started_at, "finished_at": _utcnow(),
        })
        print(json.dumps({**identity, "event": "motif_worker_refused",
                          "code": error.code}, sort_keys=True), flush=True)
        return 2
    except Exception as error:  # noqa: BLE001
        failure = failures.DiracInternal(error)
        details = failure.to_error_payload()
        details.setdefault("details", {})["traceback_tail"] = traceback.format_exc()[-4000:]
        _atomic_json(result_path, {
            "schema_version": "1.0", **identity, "ok": False,
            "error": details, "artifacts": [],
            "started_at": started_at, "finished_at": _utcnow(),
        })
        print(json.dumps({**identity, "event": "motif_worker_failed",
                          "error": f"{type(error).__name__}: {error}"},
                         sort_keys=True), flush=True)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", default=os.environ.get("DIRAC_EXECUTION_REQUEST"))
    parser.add_argument("--exchange-root", required=True)
    args = parser.parse_args(argv)
    if not args.request:
        parser.error("--request or DIRAC_EXECUTION_REQUEST is required")
    return run(Path(args.request), Path(args.exchange_root).resolve())


if __name__ == "__main__":
    sys.exit(main())
