"""Bridge InvocationService's callable boundary to Kubernetes/Kueue.

The scheduler adapter intentionally knows nothing about Python callables.  This
executor turns the already-validated invocation into an immutable ExecutionRequest,
waits for the fixed Motif worker, verifies its fenced result, and reconstructs the
HandlerResult that InvocationService already knows how to validate, persist, cache,
govern, and expose.  No scientific completion logic is duplicated here.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable
from uuid import uuid4

import failures
from execution_control.identity import sha256_digest
from invocation import HandlerResult, InvocationContext


_TERMINAL = {"succeeded", "failed", "cancelled", "unknown"}


class KubernetesInvocationExecutor:
    """Run GPU-class handlers in a fixed Kubernetes worker.

    ``submit`` only hosts the small control loop in a bounded thread.  The handler
    itself runs in the Kueue-admitted Pod.  CPU handlers remain local so switching
    the API to this executor does not turn cheap validation and governance commands
    into cluster jobs.
    """

    kind = "remote"
    adapter_kind = "kubernetes"
    supports_submission = True
    cancellation_capability = "cooperative+remote-hard"

    def __init__(self, *, adapter: Any, exchange_root: Path,
                 container_image: str, max_controllers: int = 4,
                 poll_seconds: float = .25) -> None:
        self.adapter = adapter
        self.exchange_root = exchange_root.resolve()
        self.container_image = container_image
        self.poll_seconds = max(.05, float(poll_seconds))
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, int(max_controllers)),
            thread_name_prefix="dirac-k8s-controller")
        for directory in (self.exchange_root / "inputs", self.exchange_root / "outputs"):
            directory.mkdir(parents=True, exist_ok=True)

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> Future:
        return self._pool.submit(fn, *args, **kwargs)

    def execute(self, handler: Callable[..., HandlerResult], payload: dict,
                ctx: InvocationContext) -> HandlerResult:
        if (ctx.spec is None
                or ctx.spec.execution.get("resource_class") != "gpu"):
            return handler(payload, ctx)
        return self._execute_remote(handler, payload, ctx)

    @staticmethod
    def cancel(future: Future) -> bool:
        return future.cancel()

    def shutdown(self, *, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)

    def _execute_remote(self, handler: Callable[..., HandlerResult], payload: dict,
                        ctx: InvocationContext) -> HandlerResult:
        if not ctx.job_id:
            raise failures.DiracInternal(
                "Kubernetes execution requires a durable public Job identity")
        if not ctx.execution_digest:
            raise failures.DiracInternal(
                "Kubernetes execution requires the Invocation execution digest")

        attempt_id = str(uuid4())
        input_id = str(uuid4())
        now = datetime.now(timezone.utc)
        budget = max(1, int(ctx.budget_seconds or 3600))
        input_document = {
            "schema_version": "1.0",
            "method_id": ctx.method_id,
            "method_version": ctx.version,
            "execution_digest": ctx.execution_digest,
            "job_id": ctx.job_id,
            "attempt_id": attempt_id,
            "payload": payload,
            "budget_seconds": budget,
        }
        input_bytes = json.dumps(
            input_document, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode("utf-8")
        input_sha = hashlib.sha256(input_bytes).hexdigest()
        input_path = self.exchange_root / "inputs" / f"{input_id}.json"
        self._atomic_write(input_path, input_bytes)

        request = self._request(
            ctx, payload, attempt_id=attempt_id, input_id=input_id,
            input_sha=input_sha, budget=budget, now=now)
        allocation_id: str | None = None
        try:
            status = self.adapter.submit(request)
            allocation_id = status.allocation_id
            while status.state not in _TERMINAL:
                if ctx.cancellation_token.requested:
                    self.adapter.request_cancel(allocation_id, grace_seconds=5)
                    raise failures.DiracCancelled(
                        "Kubernetes Motif execution was cancelled",
                        details={"allocation_id": allocation_id,
                                 "reason": ctx.cancellation_token.reason})
                if ctx.deadline is not None and time.time() > ctx.deadline:
                    self.adapter.request_cancel(allocation_id, grace_seconds=5)
                    raise failures.DiracBudgetExceeded(
                        f"{ctx.method_id} exceeded its {ctx.budget_seconds}s budget",
                        details={"allocation_id": allocation_id,
                                 "budget_seconds": ctx.budget_seconds})
                time.sleep(self.poll_seconds)
                status = self.adapter.inspect(allocation_id)

            result_path = self.exchange_root / "outputs" / attempt_id / "worker-result.json"
            worker_result = self._load_result(result_path) if result_path.exists() else None
            if worker_result is not None:
                self._verify_result(worker_result, request)
                if not worker_result.get("ok"):
                    error = worker_result.get("error") or {}
                    raise failures.DiracFailure(
                        str(error.get("code") or "INTERNAL"),
                        str(error.get("message") or "Motif worker failed"),
                        details=dict(error.get("details") or {}),
                        hint=error.get("hint"))
            if status.state != "succeeded":
                logs = self._safe_logs(allocation_id)
                raise failures.DiracInternal(
                    f"Kubernetes allocation {allocation_id} ended as {status.state}; "
                    f"scheduler={status.scheduler_summary}; logs_tail={logs}")
            if worker_result is None:
                raise failures.DiracInternal(
                    f"Kubernetes allocation {allocation_id} succeeded without a fenced result")
            return self._handler_result(worker_result, result_path.parent)
        finally:
            # InvocationService persists public artifacts only after this method
            # returns, so outputs must remain until a separate retention/GC policy
            # can prove persistence. Inputs are request-local and safe to remove.
            input_path.unlink(missing_ok=True)

    def _request(self, ctx: InvocationContext, payload: dict, *, attempt_id: str,
                 input_id: str, input_sha: str, budget: int,
                 now: datetime) -> dict[str, Any]:
        spec = ctx.spec
        checkpointable = bool(spec.execution.get("checkpointable"))
        determinism = str(spec.execution.get("determinism") or "numeric_tolerant")
        if determinism not in {"bitwise", "numeric_tolerant", "statistical",
                               "non_deterministic"}:
            determinism = "numeric_tolerant"
        seed = int(payload.get("seed", 0))
        output_digest = sha256_digest(json.dumps(
            spec.output_schema, sort_keys=True, separators=(",", ":")))
        return {
            "schema_version": "1.0",
            "execution_id": str(uuid4()),
            "job_id": ctx.job_id,
            "step_id": str(uuid4()),
            "attempt_id": attempt_id,
            "attempt": 1,
            "fencing_token": 1,
            "method_id": ctx.method_id,
            "execution_digest": ctx.execution_digest,
            "container_image": self.container_image,
            "entrypoint": ["motif-worker"],
            "input_manifest_artifact_id": input_id,
            "output_contract_digest": output_digest,
            "resource_request": {
                "cpu_cores": 4,
                "memory_bytes": 8 << 30,
                "gpus": 1,
                "gpu_arch": ["blackwell"],
                "gpu_memory_bytes_min": 1 << 30,
                "scratch_bytes": 8 << 30,
                "walltime_seconds": budget,
                "network": "none",
                "exclusive_gpu": True,
            },
            "placement": {
                "backend": "kubernetes", "site": "dirac-local-k3s",
                "queue": "motif", "topology": "single_node",
                "node_constraints": {}, "data_residency": ["local-node"],
            },
            "retry_policy": {
                "max_attempts": 1, "retryable_codes": [],
                "backoff": {"kind": "none", "initial_seconds": 0,
                            "max_seconds": 0},
                "preserve_seed": True, "resume_from_checkpoint": False,
            },
            "checkpoint_policy": {
                "enabled": checkpointable, "interval_steps": 100 if checkpointable else None,
                "upload_mode": "sync", "retain_last": 1 if checkpointable else 0,
                "checkpoint_timeout_seconds": 120,
            },
            "security_context": {
                "actor": dict(ctx.actor or {"kind": "service", "id": "dirac-api"}),
                "project_scope": "dirac:motif",
                "artifact_read_ids": [input_id],
                "artifact_write_session": attempt_id,
                "credential_expires_at": (now + timedelta(seconds=budget + 300)).isoformat(),
                "network_policy": "deny_all",
            },
            "determinism": {
                "class": determinism, "root_seed": seed,
                "seed_scope_digest": sha256_digest(f"{ctx.execution_digest}:{seed}"),
                "numeric_mode": "fp32",
            },
            "environment": {
                "MOTIF_INPUT_SHA256": input_sha,
                "HOME": "/tmp", "XDG_CACHE_HOME": "/tmp/cache",
                "MPLCONFIGDIR": "/tmp/matplotlib", "PYTHONUNBUFFERED": "1",
                # Distroless-style base images intentionally have no passwd row
                # for the fixed non-root UID. PyTorch/getpass use these before
                # falling back to pwd.getpwuid(), so make that identity explicit.
                "USER": "dirac-worker", "LOGNAME": "dirac-worker",
                "TORCHINDUCTOR_CACHE_DIR": "/tmp/torchinductor",
                # The small immutable NVIDIA base intentionally omits desktop X11
                # libraries that the RDKit wheel links even in headless mode. The
                # runtime snapshot carries only that audited compatibility closure.
                "LD_LIBRARY_PATH": "/home/ivan/dirac/runtime-libs",
            },
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=budget + 300)).isoformat(),
        }

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)

    @staticmethod
    def _load_result(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise failures.DiracInternal("Motif worker result is not a JSON object")
        return value

    @staticmethod
    def _verify_result(result: dict[str, Any], request: dict[str, Any]) -> None:
        expected = {
            "job_id": request["job_id"], "attempt_id": request["attempt_id"],
            "fencing_token": request["fencing_token"],
            "execution_digest": request["execution_digest"],
            "method_id": request["method_id"],
        }
        actual = {key: result.get(key) for key in expected}
        if actual != expected:
            raise failures.DiracInternal(
                f"stale or foreign Motif worker result: expected={expected}, actual={actual}")

    @staticmethod
    def _handler_result(result: dict[str, Any], directory: Path) -> HandlerResult:
        document = result.get("handler_result") or {}
        artifacts: list[tuple[str, bytes]] = []
        for item in result.get("artifacts") or []:
            filename = str(item.get("filename") or "")
            path = (directory / filename).resolve()
            if directory.resolve() not in path.parents:
                raise failures.DiracInternal("worker artifact escaped its attempt directory")
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != item.get("sha256") or len(data) != item.get("size_bytes"):
                raise failures.DiracInternal(
                    f"worker artifact integrity failure for {item.get('role')}")
            artifacts.append((str(item["role"]), data))
        return HandlerResult(
            result=dict(document.get("result") or {}), artifacts=artifacts,
            provenance=dict(document.get("provenance") or {}),
            warnings=list(document.get("warnings") or []),
            parameters_used=dict(document.get("parameters_used") or {}),
            cache=str(document.get("cache") or "computed"),
            cache_record=document.get("cache_record"),
        )

    def _safe_logs(self, allocation_id: str) -> str:
        try:
            return self.adapter.logs(allocation_id, tail=80)[-4000:]
        except Exception as error:  # noqa: BLE001
            return f"logs unavailable: {type(error).__name__}: {error}"


__all__ = ["KubernetesInvocationExecutor"]
