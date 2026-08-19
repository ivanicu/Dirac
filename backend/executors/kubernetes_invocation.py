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
import re
import shutil
import time
from typing import Any, Callable
from uuid import uuid4

import failures
from execution_control.identity import (
    CUDA_NUMERIC_MODES,
    CUDA_GPU_ARCHITECTURES,
    ExecutionIdentity,
    cuda_architecture_for_capability,
    sha256_digest,
)
from invocation import HandlerResult, InvocationContext


_TERMINAL = {"succeeded", "failed", "cancelled", "unknown"}
_RESOURCE_CLASSES = frozenset({
    "cpu", "cpu-classical", "cpu-cheminformatics", "cpu-qm", "gpu", "external-api",
})
_ADAPTER_PROTOCOL = ("submit", "inspect", "request_cancel", "logs", "health")
_OCI_DIGEST = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")


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
    cancellation_capability = "route-specific"

    def execution_adapter_for(self, spec: Any) -> str:
        """Name the adapter that will execute this specific Method.

        This executor is deliberately hybrid: GPU Methods cross the Kubernetes
        boundary, while CPU Methods run in the API controller.  Reporting the
        executor's class-level ``adapter_kind`` for every Method used to stamp
        local CPU work as Kubernetes work and let a ``local_cpu``-only contract
        pass without an auditable explanation.  The route is a property of the
        Method, not merely of the executor object.
        """
        resource_class = str(
            getattr(spec, "execution", {}).get("resource_class") or "")
        if resource_class == "gpu":
            return "kubernetes"
        if resource_class in _RESOURCE_CLASSES:
            return "local_cpu"
        return "unconfigured"

    @staticmethod
    def cancellation_capability_for(
            spec: Any, *, execution_adapter: str | None = None) -> str:
        declared = str(getattr(spec, "execution", {}).get("cancellation") or "none")
        if declared != "cooperative":
            return "queued-only"
        if execution_adapter == "kubernetes":
            return "cooperative+remote-hard"
        if execution_adapter == "local_cpu":
            return "cooperative"
        return "queued-only"

    def capabilities(self) -> dict[str, Any]:
        """Prove the deployed adapter protocol and scheduler inventory.

        Merely wrapping an object in this executor is not Kubernetes readiness.
        The positive control is a successful adapter health query that observes an
        active queue and a Ready GPU node carrying explicit Dirac architecture and
        numeric-mode labels.
        """
        missing = [name for name in _ADAPTER_PROTOCOL
                   if not callable(getattr(self.adapter, name, None))]
        image_pinned = bool(_OCI_DIGEST.fullmatch(self.container_image))
        image_allowed = self.container_image in set(
            getattr(self.adapter, "allowed_images", ()) or ())
        health: dict[str, Any] = {}
        health_error: str | None = None
        if not missing:
            try:
                health = dict(self.adapter.health())
            except Exception as error:  # noqa: BLE001 - readiness must fail closed
                health_error = f"{type(error).__name__}: {error}"
        gpu = dict(health.get("gpu") or {})
        expected_selector = {
            "dirac.io/gpu-arch": gpu.get("arch"),
            "dirac.io/gpu-numeric-mode": gpu.get("numeric_mode"),
            "dirac.io/gpu-memory-bytes": str(gpu.get("memory_bytes")),
        }
        verified_gpu = bool(
            not missing and image_pinned and image_allowed
            and health.get("ready") is True
            and gpu.get("verified") is True
            and gpu.get("arch") in CUDA_GPU_ARCHITECTURES
            and gpu.get("numeric_mode") in CUDA_NUMERIC_MODES
            # ``bool`` is an ``int`` subclass in Python; True is not a one-byte
            # capacity witness.
            and type(gpu.get("memory_bytes")) is int
            and gpu.get("memory_bytes") > 0
            # The selector is the enforcement mechanism, not decoration.  A
            # health payload that proves one profile but supplies an unrelated
            # or partial selector would otherwise mint green readiness while a
            # Pod remained schedulable on a different GPU.
            and gpu.get("node_selector") == expected_selector)
        return {
            "adapter": "kubernetes",
            "protocol_valid": not missing,
            "missing_protocol_methods": missing,
            "worker_image_pinned": image_pinned,
            "worker_image_allowed": image_allowed,
            "scheduler_healthy": (health.get("ready") is True
                                  if not missing else False),
            "gpu_execution": verified_gpu,
            "gpu": gpu if verified_gpu else {**gpu, "verified": False},
            "health_error": health_error,
            "cancellation_by_route": {
                "local_cpu": "descriptor-declared",
                "kubernetes": "cooperative+remote-hard",
            },
        }

    def verified_gpu_profile(self) -> dict[str, Any]:
        capability = self.capabilities()
        if not capability["gpu_execution"]:
            raise failures.DiracUnsupported(
                "Kubernetes GPU execution is not ready: adapter protocol, queue "
                "health, pinned allowlisted image, and verified GPU inventory are "
                "all required",
                details={key: value for key, value in capability.items()
                         if key != "cancellation_by_route"})
        return dict(capability["gpu"])

    def __init__(self, *, adapter: Any, exchange_root: Path,
                 container_image: str, max_controllers: int = 4,
                 poll_seconds: float = .25, resource_broker: Any | None = None,
                 attempt_store: Any | None = None,
                 artifact_reader: Any | None = None) -> None:
        self.adapter = adapter
        self.exchange_root = exchange_root.resolve()
        self.container_image = container_image
        self.poll_seconds = max(.05, float(poll_seconds))
        self.resource_broker = resource_broker
        self.attempt_store = attempt_store
        self.artifact_reader = artifact_reader
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, int(max_controllers)),
            thread_name_prefix="dirac-k8s-controller")
        for directory in (self.exchange_root / "inputs", self.exchange_root / "outputs"):
            directory.mkdir(parents=True, exist_ok=True)

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> Future:
        return self._pool.submit(fn, *args, **kwargs)

    def execute(self, handler: Callable[..., HandlerResult], payload: dict,
                ctx: InvocationContext) -> HandlerResult:
        route = str(ctx.execution_adapter or "")
        if route not in {"local_cpu", "kubernetes"}:
            raise failures.DiracInternal(
                "hybrid executor requires the route frozen by Invocation admission")
        if route == "local_cpu":
            if self.resource_broker is None:
                return handler(payload, ctx)
            if not ctx.job_id:
                raise failures.DiracInternal(
                    "resource-governed local execution requires a durable Job identity")
            attempt_claim = None
            if self.attempt_store is not None:
                attempt_claim = self.attempt_store.claim(
                    job_id=ctx.job_id,
                    execution_digest=bytes.fromhex(
                        ctx.execution_digest.removeprefix("sha256:")),
                    owner=f"local-controller:{uuid4()}",
                    lease_seconds=max(600, int(ctx.budget_seconds or 0) + 300))
            profile = (ctx.spec.execution.get("scale_profile")
                       if ctx.spec is not None else {}) or {}
            lease = self.resource_broker.acquire(
                ctx.job_id, None, {
                    "cpu_cores": float(profile.get("cpu_cores", 1)),
                    "ram_bytes": float(profile.get("memory_bytes", 1 << 30)),
                    "scratch_bytes": float(profile.get("scratch_bytes", 1 << 30)),
                    "process_slots": 1,
                    "scf_slots": 1 if (ctx.spec and
                        ctx.spec.execution.get("resource_class") == "cpu-qm") else 0,
                }, ttl_seconds=max(600, int(ctx.budget_seconds or 0) + 300),
                backend="local_cpu")
            try:
                result = handler(payload, ctx)
                if isinstance(result, HandlerResult):
                    result.attempt_claim = attempt_claim
                return result
            except BaseException:
                if attempt_claim is not None:
                    try:
                        self.attempt_store.complete(
                            attempt_claim, state="failed",
                            event_key=(f"attempt:{attempt_claim.attempt_id}:failed:"
                                       f"{attempt_claim.fencing_token}"),
                            payload={"error_code": "LOCAL_EXECUTION_FAILED"})
                    except Exception:
                        pass
                raise
            finally:
                try:
                    self.resource_broker.release(lease.lease_id, lease.fencing_token)
                except RuntimeError:
                    pass
        if route != "kubernetes":
            raise failures.DiracInternal(f"unsupported admitted execution route {route!r}")
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

        budget = max(1, int(ctx.budget_seconds or 3600))
        attempt_claim = None
        if self.attempt_store is not None:
            attempt_claim = self.attempt_store.claim(
                job_id=ctx.job_id,
                execution_digest=bytes.fromhex(ctx.execution_digest.removeprefix("sha256:")),
                owner=f"kubernetes-controller:{uuid4()}", lease_seconds=budget + 600)
        attempt_id = attempt_claim.attempt_id if attempt_claim is not None else str(uuid4())
        input_id = str(uuid4())
        now = datetime.now(timezone.utc)
        artifact_grants, staged_root = self._stage_artifact_references(
            payload, attempt_id=attempt_id)
        input_document = {
            "schema_version": "1.0",
            "method_id": ctx.method_id,
            "method_version": ctx.version,
            "execution_digest": ctx.execution_digest,
            "job_id": ctx.job_id,
            "attempt_id": attempt_id,
            "payload": payload,
            "budget_seconds": budget,
            "artifact_grants": artifact_grants,
            "server_attestations": dict(ctx.server_attestations),
        }
        input_bytes = json.dumps(
            input_document, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode("utf-8")
        input_sha = hashlib.sha256(input_bytes).hexdigest()
        input_path = self.exchange_root / "inputs" / f"{input_id}.json"
        self._atomic_write(input_path, input_bytes)

        request = self._request(
            ctx, payload, attempt_id=attempt_id, input_id=input_id,
            input_sha=input_sha, budget=budget, now=now,
            attempt_no=attempt_claim.attempt if attempt_claim else 1,
            fencing_token=attempt_claim.fencing_token if attempt_claim else 1)
        allocation_id: str | None = None
        resource_lease = None
        next_resource_heartbeat = time.time() + min(30.0, max(1.0, budget / 4))
        try:
            if self.resource_broker is not None:
                resources = request["resource_request"]
                resource_lease = self.resource_broker.acquire(
                    ctx.job_id, None, {
                        "cpu_cores": resources["cpu_cores"],
                        "ram_bytes": resources["memory_bytes"],
                        "gpus": resources["gpus"],
                        "gpu_vram_bytes": resources.get("gpu_memory_bytes_min", 0),
                        "scratch_bytes": resources["scratch_bytes"],
                        "process_slots": 1,
                    }, ttl_seconds=budget + 600, backend="kubernetes")
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
                if resource_lease is not None and time.time() >= next_resource_heartbeat:
                    resource_lease = self.resource_broker.heartbeat(
                        resource_lease.lease_id, resource_lease.fencing_token,
                        ttl_seconds=budget + 600)
                    next_resource_heartbeat = time.time() + min(
                        30.0, max(1.0, budget / 4))
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
            result = self._handler_result(worker_result, result_path.parent)
            result.attempt_claim = attempt_claim
            return result
        finally:
            # InvocationService persists public artifacts only after this method
            # returns, so outputs must remain until a separate retention/GC policy
            # can prove persistence. Inputs are request-local and safe to remove.
            input_path.unlink(missing_ok=True)
            shutil.rmtree(staged_root, ignore_errors=True)
            if resource_lease is not None:
                try:
                    self.resource_broker.release(
                        resource_lease.lease_id, resource_lease.fencing_token)
                except RuntimeError:
                    # A stale lease is already fenced and cannot reserve capacity.
                    pass

    def _gpu_profile_from_identity(self, ctx: InvocationContext) -> dict[str, Any]:
        identity = ctx.execution_identity
        if not isinstance(identity, ExecutionIdentity):
            raise failures.DiracInternal(
                "Kubernetes request requires the admitted ExecutionIdentity")
        if identity.executor_adapter != "kubernetes":
            raise failures.DiracInternal(
                "Kubernetes request identity does not name the Kubernetes adapter")
        if identity.container_image != self.container_image:
            raise failures.DiracInternal(
                "Kubernetes request image differs from the admitted worker image")
        prefix = "kubernetes:gpu:"
        hardware = str(identity.hardware_compatibility_profile or "")
        profile_parts = (hardware.removeprefix(prefix).split(":")
                         if hardware.startswith(prefix) else [])
        arch = profile_parts[0] if len(profile_parts) == 2 else ""
        try:
            memory_bytes = int(profile_parts[1]) if len(profile_parts) == 2 else 0
        except ValueError:
            memory_bytes = 0
        numeric_mode = str(identity.numeric_mode or "")
        if arch not in CUDA_GPU_ARCHITECTURES:
            raise failures.DiracInternal(
                "Kubernetes request identity has no canonical GPU architecture")
        if numeric_mode not in CUDA_NUMERIC_MODES:
            raise failures.DiracInternal(
                "Kubernetes request identity has no canonical GPU numeric mode")
        if memory_bytes < 1:
            raise failures.DiracInternal(
                "Kubernetes request identity has no verified GPU memory capacity")
        return {
            "arch": arch,
            "numeric_mode": numeric_mode,
            "memory_bytes": memory_bytes,
            "node_selector": {
                "dirac.io/gpu-arch": arch,
                "dirac.io/gpu-numeric-mode": numeric_mode,
                "dirac.io/gpu-memory-bytes": str(memory_bytes),
            },
        }

    def _request(self, ctx: InvocationContext, payload: dict, *, attempt_id: str,
                 input_id: str, input_sha: str, budget: int,
                 now: datetime, attempt_no: int = 1,
                 fencing_token: int = 1) -> dict[str, Any]:
        spec = ctx.spec
        checkpointable = bool(spec.execution.get("checkpointable"))
        determinism = str(spec.execution.get("determinism") or "numeric_tolerant")
        if determinism not in {"bitwise", "numeric_tolerant", "statistical",
                               "non_deterministic"}:
            determinism = "numeric_tolerant"
        seed = int(payload.get("seed", 0))
        output_digest = sha256_digest(json.dumps(
            spec.output_schema, sort_keys=True, separators=(",", ":")))
        scale_profile = spec.execution.get("scale_profile") or {}
        cpu_cores = float(scale_profile.get("cpu_cores", 4))
        gpu_profile = self._gpu_profile_from_identity(ctx)
        workload_priority = (
            "motif-long" if ctx.method_id == "physics.motif.openfe_edge" or budget > 3600
            else "motif-interactive" if budget <= 300 else "motif-standard")
        return {
            "schema_version": "1.0",
            "execution_id": str(uuid4()),
            "job_id": ctx.job_id,
            "step_id": str(uuid4()),
            "attempt_id": attempt_id,
            "attempt": attempt_no,
            "fencing_token": fencing_token,
            "method_id": ctx.method_id,
            "execution_digest": ctx.execution_digest,
            "container_image": self.container_image,
            "entrypoint": ["motif-worker"],
            "input_manifest_artifact_id": input_id,
            "output_contract_digest": output_digest,
            "resource_request": {
                "cpu_cores": cpu_cores,
                "memory_bytes": 8 << 30,
                "gpus": 1,
                "gpu_arch": [gpu_profile["arch"]],
                "gpu_memory_bytes_min": gpu_profile["memory_bytes"],
                "scratch_bytes": 8 << 30,
                "walltime_seconds": budget,
                "network": "none",
                "exclusive_gpu": True,
            },
            "placement": {
                "backend": "kubernetes", "site": "dirac-local-k3s",
                "queue": "motif", "topology": "single_node",
                "workload_priority_class": workload_priority,
                "node_constraints": gpu_profile["node_selector"],
                "data_residency": ["local-node"],
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
                "artifact_read_ids": [input_id, *self._artifact_reference_ids(payload)],
                "artifact_write_session": attempt_id,
                "credential_expires_at": (now + timedelta(seconds=budget + 300)).isoformat(),
                "network_policy": "deny_all",
            },
            "determinism": {
                "class": determinism, "root_seed": seed,
                "seed_scope_digest": sha256_digest(f"{ctx.execution_digest}:{seed}"),
                "numeric_mode": gpu_profile["numeric_mode"],
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
    def _artifact_references(value: Any) -> list[dict[str, Any]]:
        """Collect explicit artifact capabilities from a validated payload.

        A worker receives only these bytes, not a database credential or a general
        artifact-store handle.  Duplicate references collapse by id and conflicting
        digests fail before a Pod is submitted.
        """
        found: dict[str, dict[str, Any]] = {}

        def visit(item: Any) -> None:
            if isinstance(item, dict):
                if item.get("kind") == "artifact" and isinstance(item.get("id"), str):
                    artifact_id = item["id"]
                    previous = found.get(artifact_id)
                    if previous is not None and previous.get("sha256") != item.get("sha256"):
                        raise failures.DiracInvalidParameters(
                            "one payload grants conflicting digests for the same artifact",
                            details={"artifact_id": artifact_id})
                    found[artifact_id] = item
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        return [found[key] for key in sorted(found)]

    @classmethod
    def _artifact_reference_ids(cls, payload: dict) -> list[str]:
        return [str(item["id"]) for item in cls._artifact_references(payload)]

    def _stage_artifact_references(
            self, payload: dict, *, attempt_id: str) -> tuple[list[dict[str, Any]], Path]:
        references = self._artifact_references(payload)
        staged_root = self.exchange_root / "inputs" / "artifacts" / attempt_id
        grants: list[dict[str, Any]] = []
        if references and self.artifact_reader is None:
            raise failures.DiracUnsupported(
                "remote execution cannot resolve the payload's artifact capabilities")
        try:
            for index, reference in enumerate(references):
                artifact, data = self.artifact_reader.read(reference["id"])
                actual = "sha256:" + hashlib.sha256(data).hexdigest()
                if (reference.get("sha256") != actual
                        or artifact.sha256 != actual.removeprefix("sha256:")):
                    raise failures.DiracInvalidParameters(
                        "artifact capability digest does not match server-owned bytes",
                        details={"artifact_id": reference["id"]})
                relative = f"inputs/artifacts/{attempt_id}/{index:04d}.bin"
                self._atomic_write(self.exchange_root / relative, data)
                grants.append({
                    "id": str(artifact.id), "sha256": actual,
                    "role": artifact.role, "media_type": artifact.media_type,
                    "size_bytes": len(data), "path": relative,
                    "method_version": artifact.method_version,
                })
        except Exception:
            shutil.rmtree(staged_root, ignore_errors=True)
            raise
        return grants, staged_root

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
        if result.get("schema_version") != "1.0":
            raise failures.DiracInternal(
                "Motif worker result has an unsupported schema version")
        if type(result.get("ok")) is not bool:
            raise failures.DiracInternal(
                "Motif worker result ok verdict must be a JSON boolean")
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
        if result["ok"] and request["resource_request"].get("gpus", 0):
            gpu = dict((result.get("worker_attestation") or {}).get("gpu") or {})
            expected_arch = list(request["resource_request"].get("gpu_arch") or [])
            expected_numeric = request["determinism"].get("numeric_mode")
            capability = str(gpu.get("compute_capability") or "")
            try:
                major_text, minor_text = capability.split(".", 1)
                observed_arch = cuda_architecture_for_capability(
                    int(major_text), int(minor_text))
            except (TypeError, ValueError):
                observed_arch = None
            if (gpu.get("available") is not True
                    or [gpu.get("architecture")] != expected_arch
                    or observed_arch != gpu.get("architecture")
                    or gpu.get("numeric_mode") != expected_numeric
                    or type(gpu.get("memory_bytes")) is not int
                    or gpu.get("memory_bytes", 0) < int(
                        request["resource_request"].get(
                            "gpu_memory_bytes_min") or 0)
                    or observed_arch is None):
                raise failures.DiracInternal(
                    "Motif worker GPU attestation does not match the admitted "
                    "architecture and numeric mode")

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
