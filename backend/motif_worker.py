"""Fixed, network-isolated Kubernetes entrypoint for one Motif ExecutionRequest."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback
from typing import Any

import failures
from artifacts import Artifact, verify_bytes
from catalog import MethodCatalog
from execution_control.protocol import validate_execution_request
from execution_control.protocol import CancellationToken
from execution_control.identity import cuda_architecture_for_capability
from invocation import HandlerResult, InvocationContext


class _GrantedArtifactReader:
    """Read-only, request-scoped artifact capability for an isolated worker."""

    def __init__(self, exchange_root: Path, grants: list[dict[str, Any]]) -> None:
        self.exchange_root = exchange_root
        self.grants = {str(item["id"]): dict(item) for item in grants}

    def read(self, address: str) -> tuple[Artifact, bytes]:
        grant = self.grants.get(str(address))
        if grant is None:
            raise failures.DiracNotFound(
                "artifact is outside this worker's request-scoped capability",
                details={"artifact_id": str(address)})
        path = (self.exchange_root / grant["path"]).resolve()
        if self.exchange_root not in path.parents:
            raise failures.DiracInternal("granted artifact path escaped the exchange root")
        data = path.read_bytes()
        digest = str(grant["sha256"]).removeprefix("sha256:")
        verify_bytes(data, digest)
        if len(data) != int(grant["size_bytes"]):
            raise failures.DiracInternal("granted artifact size does not match its manifest")
        artifact = Artifact(
            id=str(grant["id"]), sha256=digest, role=str(grant["role"]),
            media_type=str(grant["media_type"]), size_bytes=len(data),
            method_version=grant.get("method_version"))
        return artifact, data

    def head(self, address: str) -> Artifact:
        artifact, _ = self.read(address)
        return artifact


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
    major, minor = (int(value) for value in torch.cuda.get_device_capability(0))
    try:
        architecture = cuda_architecture_for_capability(major, minor)
    except ValueError as error:
        raise RuntimeError(f"{error}; refusing to guess its architecture") from error
    requested_arch = list(request["resource_request"].get("gpu_arch") or [])
    if requested_arch != [architecture]:
        raise RuntimeError(
            f"worker GPU architecture {architecture} does not match admitted "
            f"constraint {requested_arch}")
    numeric_mode = str(request["determinism"].get("numeric_mode") or "")
    dtype_by_mode = {
        "fp64": getattr(torch, "float64", None),
        "fp32": getattr(torch, "float32", None),
        "tf32": getattr(torch, "float32", None),
        "bf16": getattr(torch, "bfloat16", None),
        "fp16": getattr(torch, "float16", None),
    }
    target_dtype = dtype_by_mode.get(numeric_mode)
    set_default_dtype = getattr(torch, "set_default_dtype", None)
    if target_dtype is not None and callable(set_default_dtype):
        set_default_dtype(target_dtype)
    backends = getattr(torch, "backends", None)
    cuda_backend = getattr(backends, "cuda", None)
    matmul_backend = getattr(cuda_backend, "matmul", None)
    cudnn_backend = getattr(backends, "cudnn", None)
    if numeric_mode in {"fp32", "tf32"}:
        expected_tf32 = numeric_mode == "tf32"
        if matmul_backend is not None:
            matmul_backend.allow_tf32 = expected_tf32
        if cudnn_backend is not None:
            cudnn_backend.allow_tf32 = expected_tf32
    memory_bytes = int(torch.cuda.get_device_properties(0).total_memory)
    admitted_memory_bytes = int(
        request["resource_request"].get("gpu_memory_bytes_min") or 0)
    if memory_bytes < admitted_memory_bytes:
        raise RuntimeError(
            "worker GPU memory is below the admitted minimum capacity")
    actual_dtype = str(torch.get_default_dtype())
    expected_dtype = {
        "fp64": "torch.float64",
        "fp32": "torch.float32",
        "tf32": "torch.float32",
        "bf16": "torch.bfloat16",
        "fp16": "torch.float16",
    }.get(numeric_mode)
    if expected_dtype is None or actual_dtype != expected_dtype:
        raise RuntimeError(
            f"worker numeric mode {numeric_mode!r} is not attested by default "
            f"dtype {actual_dtype!r}")
    matmul_tf32 = getattr(matmul_backend, "allow_tf32", None)
    cudnn_tf32 = getattr(cudnn_backend, "allow_tf32", None)
    if numeric_mode in {"fp32", "tf32"}:
        expected_tf32 = numeric_mode == "tf32"
        if (not isinstance(matmul_tf32, bool)
                or not isinstance(cudnn_tf32, bool)
                or matmul_tf32 is not expected_tf32
                or cudnn_tf32 is not expected_tf32):
            raise RuntimeError(
                f"worker numeric mode {numeric_mode!r} conflicts with CUDA "
                f"TF32 flags matmul={matmul_tf32!r}, cudnn={cudnn_tf32!r}")
    return {
        "requested": True, "available": True,
        "torch": torch.__version__, "cuda": torch.version.cuda,
        "device_count": torch.cuda.device_count(),
        "device_name": torch.cuda.get_device_name(0),
        "compute_capability": f"{major}.{minor}",
        "memory_bytes": memory_bytes,
        "architecture": architecture,
        "numeric_mode": numeric_mode,
        "default_dtype": actual_dtype,
        "matmul_allow_tf32": matmul_tf32,
        "cudnn_allow_tf32": cudnn_tf32,
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
        os.environ["DIRAC_MOTIF_ATTEMPT_DIR"] = str(output_dir.resolve())
        os.environ.setdefault(
            "DIRAC_OPENFE_EXECUTABLE",
            "/home/ivan/dirac/openfe-runtime-v2/bin/openfe")
        cancellation_token = CancellationToken()
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(
            signal.SIGTERM,
            lambda _signum, _frame: cancellation_token.request(
                "Kubernetes termination requested; checkpoint before grace expires"))
        context = InvocationContext(
            method_id=spec.method_id,
            version=document.get("method_version"),
            execution_digest=request["execution_digest"],
            actor=dict(request["security_context"]["actor"]),
            budget_seconds=document.get("budget_seconds"),
            job_id=request["job_id"], spec=spec, deadline=deadline,
            cancellation_token=cancellation_token,
            artifact_reader=_GrantedArtifactReader(
                exchange_root, list(document.get("artifact_grants") or [])),
            server_attestations=dict(document.get("server_attestations") or {}))
        try:
            output = handler(payload, context)
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
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
            "worker_attestation": {"gpu": gpu},
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
