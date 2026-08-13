"""Kubernetes Job + Kueue scheduler adapter.

Dirac owns attempt identity, retries, fencing and scientific outputs. Kubernetes
and Kueue only own placement and resource admission. ExecutionRequest remains
data: neither its image nor its entrypoint can escape the adapter allowlist.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
import subprocess
from typing import Any, Callable, Sequence

from execution_control.protocol import (
    AdmissionDecision,
    AllocationStatus,
    EventPage,
    validate_execution_request,
)


_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
_SENSITIVE_ENV = re.compile(r"(?:TOKEN|PASSWORD|PASSWD|SECRET|API_KEY|CREDENTIAL)")
_RESERVED_ENV_PREFIX = "DIRAC_"


@dataclass(frozen=True)
class KubernetesKueueConfig:
    namespace: str = "dirac-motif"
    queue_name: str = "motif"
    service_account: str = "dirac-motif-worker"
    termination_grace_seconds: int = 300

    def __post_init__(self) -> None:
        for field in ("namespace", "queue_name", "service_account"):
            value = getattr(self, field)
            if len(value) > 63 or not _DNS_LABEL.fullmatch(value):
                raise ValueError(f"{field} must be a Kubernetes DNS label")
        if self.termination_grace_seconds < 0:
            raise ValueError("termination_grace_seconds must be non-negative")


class KubernetesKueueAdapter:
    """Submit fixed-worker Jobs while Kueue controls admission.

    ``runner`` is injectable for deterministic tests. Production uses kubectl,
    which keeps this adapter dependency-free and works with k3s' bundled client.
    """

    kind = "kubernetes"

    def __init__(
        self,
        *,
        worker_command: Sequence[str],
        allowed_images: Sequence[str],
        config: KubernetesKueueConfig | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        if not worker_command:
            raise ValueError("worker_command must be fixed at adapter construction")
        if not allowed_images:
            raise ValueError("allowed_images must contain immutable OCI references")
        mutable = [image for image in allowed_images if not _is_digest_image(image)]
        if mutable:
            raise ValueError(f"mutable image references are forbidden: {mutable!r}")
        self.worker_command = tuple(worker_command)
        self.allowed_images = frozenset(allowed_images)
        self.config = config or KubernetesKueueConfig()
        self._runner = runner or subprocess.run

    def admit(self, request: dict[str, Any]) -> AdmissionDecision:
        validate_execution_request(request)
        if request["placement"]["backend"] != self.kind:
            return AdmissionDecision(
                False,
                "PLACEMENT_MISMATCH",
                f"request targets {request['placement']['backend']}",
                self._available(),
            )
        if request["container_image"] not in self.allowed_images:
            return AdmissionDecision(
                False,
                "IMAGE_NOT_ALLOWED",
                "container digest is not present in the deployed worker allowlist",
                self._available(),
            )
        if request["placement"]["topology"] not in {"single_process", "single_node"}:
            return AdmissionDecision(
                False,
                "TOPOLOGY_UNSUPPORTED",
                "this adapter version supports one Kubernetes Job pod per attempt",
                self._available(),
            )
        sensitive = sorted(name for name in request.get("environment", {})
                           if _SENSITIVE_ENV.search(name))
        if sensitive:
            return AdmissionDecision(
                False,
                "SENSITIVE_ENVIRONMENT",
                f"secret-like environment names require opaque handles: {sensitive}",
                self._available(),
            )
        reserved = sorted(name for name in request.get("environment", {})
                          if name.startswith(_RESERVED_ENV_PREFIX))
        if reserved:
            return AdmissionDecision(
                False,
                "RESERVED_ENVIRONMENT",
                f"adapter-owned environment names cannot be overridden: {reserved}",
                self._available(),
            )
        return AdmissionDecision(
            True,
            "ADMITTED",
            "request is valid; capacity admission is delegated to Kueue",
            self._available(),
        )

    def submit(self, request: dict[str, Any]) -> AllocationStatus:
        decision = self.admit(request)
        if not decision.admitted:
            raise RuntimeError(f"{decision.code}: {decision.reason}")
        name = self._job_name(request["attempt_id"])
        config_map = f"{name}-request"
        request_json = json.dumps(request, sort_keys=True, separators=(",", ":"))
        self._apply(self._config_map_manifest(config_map, name, request_json))
        try:
            self._apply(self._job_manifest(name, config_map, request))
            job = self._get("job", name)
            uid = job["metadata"]["uid"]
            self._patch(
                "configmap",
                config_map,
                {
                    "metadata": {
                        "ownerReferences": [{
                            "apiVersion": "batch/v1",
                            "kind": "Job",
                            "name": name,
                            "uid": uid,
                            "controller": True,
                            "blockOwnerDeletion": True,
                        }]
                    }
                },
            )
        except Exception:
            self._delete("configmap", config_map, ignore_not_found=True)
            raise
        return self._status(job)

    def inspect(self, allocation_id: str) -> AllocationStatus:
        namespace, name = self._parse_allocation_id(allocation_id)
        try:
            job = self._get("job", name, namespace=namespace)
        except KubernetesObjectNotFound:
            return AllocationStatus(allocation_id, "unknown", {})
        return self._status(job)

    def request_cancel(self, allocation_id: str, *, grace_seconds: int) -> None:
        namespace, name = self._parse_allocation_id(allocation_id)
        self._run(
            "delete", "job", name,
            "--namespace", namespace,
            "--grace-period", str(max(0, grace_seconds)),
            "--wait=false", "--ignore-not-found=true",
        )

    def suspend(self, allocation_id: str) -> None:
        namespace, name = self._parse_allocation_id(allocation_id)
        self._patch("job", name, {"spec": {"suspend": True}}, namespace=namespace)

    def resume(self, allocation_id: str) -> None:
        namespace, name = self._parse_allocation_id(allocation_id)
        self._patch("job", name, {"spec": {"suspend": False}}, namespace=namespace)

    def collect_events(self, cursor: str | None) -> EventPage:
        payload = self._json(
            "get", "events", "--namespace", self.config.namespace,
            "--field-selector", "involvedObject.kind=Job",
        )
        resource_version = int(cursor or 0)
        items = sorted(
            (item for item in payload.get("items", ())
             if int(item.get("metadata", {}).get("resourceVersion", 0)) > resource_version),
            key=lambda item: int(item.get("metadata", {}).get("resourceVersion", 0)),
        )
        events = tuple({
            "allocation_id": self._allocation_id(
                item.get("involvedObject", {}).get("name", "unknown")),
            "state": item.get("reason", "Unknown"),
            "details": {
                "type": item.get("type"),
                "message": item.get("note") or item.get("message"),
                "count": item.get("deprecatedCount") or item.get("count", 1),
                "resource_version": item.get("metadata", {}).get("resourceVersion"),
            },
        } for item in items)
        next_cursor = str(max(
            [resource_version] + [int(item.get("metadata", {}).get("resourceVersion", 0))
                                  for item in items]
        ))
        return EventPage(events, next_cursor)

    def reconcile(self, allocation_id: str) -> AllocationStatus:
        return self.inspect(allocation_id)

    def logs(self, allocation_id: str, *, tail: int = 200) -> str:
        namespace, name = self._parse_allocation_id(allocation_id)
        result = self._run(
            "logs", f"job/{name}", "--namespace", namespace,
            "--tail", str(max(1, tail)),
        )
        return result.stdout

    def _job_manifest(self, name: str, config_map: str,
                      request: dict[str, Any]) -> dict[str, Any]:
        resources = request["resource_request"]
        requests = {
            "cpu": _cpu_quantity(resources["cpu_cores"]),
            "memory": _byte_quantity(resources["memory_bytes"]),
            "ephemeral-storage": _byte_quantity(max(resources["scratch_bytes"], 1 << 20)),
        }
        limits = dict(requests)
        if resources["gpus"]:
            requests["nvidia.com/gpu"] = str(resources["gpus"])
            limits["nvidia.com/gpu"] = str(resources["gpus"])
        environment = [
            {"name": key, "value": value}
            for key, value in sorted(request.get("environment", {}).items())
        ]
        environment.extend((
            {"name": "DIRAC_EXECUTION_REQUEST",
             "value": "/var/run/dirac/execution-request.json"},
            {"name": "DIRAC_ALLOCATION_ID", "value": self._allocation_id(name)},
        ))
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": name,
                "namespace": self.config.namespace,
                "labels": {
                    "app.kubernetes.io/name": "dirac-worker",
                    "app.kubernetes.io/component": "motif-execution",
                    "dirac.io/attempt-id": request["attempt_id"],
                    "dirac.io/job-id": request["job_id"],
                    "kueue.x-k8s.io/queue-name": self.config.queue_name,
                },
                "annotations": {
                    "dirac.io/execution-digest": request["execution_digest"],
                    "dirac.io/fencing-token": str(request["fencing_token"]),
                    "dirac.io/method-id": request["method_id"],
                },
            },
            "spec": {
                "suspend": True,
                "backoffLimit": 0,
                "activeDeadlineSeconds": resources["walltime_seconds"],
                "ttlSecondsAfterFinished": 86400,
                "template": {
                    "metadata": {"labels": {
                        "app.kubernetes.io/name": "dirac-worker",
                        "dirac.io/attempt-id": request["attempt_id"],
                    }},
                    "spec": {
                        **({"runtimeClassName": "nvidia"} if resources["gpus"] else {}),
                        "restartPolicy": "Never",
                        "serviceAccountName": self.config.service_account,
                        "automountServiceAccountToken": False,
                        "terminationGracePeriodSeconds": self.config.termination_grace_seconds,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 65532,
                            "runAsGroup": 65532,
                            "fsGroup": 65532,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [{
                            "name": "worker",
                            "image": request["container_image"],
                            "imagePullPolicy": "IfNotPresent",
                            "command": list(self.worker_command),
                            "env": environment,
                            "resources": {"requests": requests, "limits": limits},
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                                "readOnlyRootFilesystem": True,
                                "runAsNonRoot": True,
                            },
                            "volumeMounts": [
                                {"name": "execution-request", "mountPath": "/var/run/dirac",
                                 "readOnly": True},
                                {"name": "scratch", "mountPath": "/scratch"},
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                        }],
                        "volumes": [
                            {"name": "execution-request", "configMap": {"name": config_map,
                              "items": [{"key": "execution-request.json",
                                         "path": "execution-request.json"}]}},
                            {"name": "scratch", "emptyDir": {
                                "sizeLimit": _byte_quantity(max(resources["scratch_bytes"], 1 << 20))}},
                            {"name": "tmp", "emptyDir": {}},
                        ],
                    },
                },
            },
        }

    def _config_map_manifest(self, name: str, job_name: str,
                             request_json: str) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": name,
                "namespace": self.config.namespace,
                "labels": {"app.kubernetes.io/name": "dirac-worker",
                           "dirac.io/job-name": job_name},
            },
            "immutable": True,
            "data": {"execution-request.json": request_json},
        }

    def _status(self, job: dict[str, Any]) -> AllocationStatus:
        metadata = job.get("metadata", {})
        spec = job.get("spec", {})
        status = job.get("status", {})
        conditions = {
            condition.get("type"): condition
            for condition in status.get("conditions", ())
            if condition.get("status") == "True"
        }
        if metadata.get("deletionTimestamp"):
            state = "cancelled"
        elif "Complete" in conditions or status.get("succeeded", 0) > 0:
            state = "succeeded"
        elif "Failed" in conditions or status.get("failed", 0) > 0:
            state = "failed"
        elif status.get("active", 0) > 0:
            state = "running"
        elif spec.get("suspend"):
            state = "suspended"
        else:
            state = "pending"
        name = metadata["name"]
        summary = {
            "namespace": metadata.get("namespace", self.config.namespace),
            "job_name": name,
            "queue_name": metadata.get("labels", {}).get("kueue.x-k8s.io/queue-name"),
            "active": status.get("active", 0),
            "succeeded": status.get("succeeded", 0),
            "failed": status.get("failed", 0),
            "conditions": [
                {key: condition.get(key) for key in
                 ("type", "status", "reason", "message", "lastTransitionTime")
                 if condition.get(key) is not None}
                for condition in status.get("conditions", ())
            ],
        }
        return AllocationStatus(self._allocation_id(name), state, summary)

    def _available(self) -> dict[str, Any]:
        return {"scheduler": "kubernetes+kueue", "namespace": self.config.namespace,
                "queue": self.config.queue_name, "capacity": "delegated_to_kueue"}

    def _allocation_id(self, name: str) -> str:
        return f"{self.config.namespace}/{name}"

    @staticmethod
    def _job_name(attempt_id: str) -> str:
        return "dirac-" + attempt_id.lower().replace("-", "")

    def _parse_allocation_id(self, allocation_id: str) -> tuple[str, str]:
        parts = allocation_id.split("/", 1)
        if len(parts) != 2 or parts[0] != self.config.namespace or not _DNS_LABEL.fullmatch(parts[1]):
            raise ValueError("allocation_id must be the configured namespace and a Job name")
        return parts[0], parts[1]

    def _apply(self, manifest: dict[str, Any]) -> None:
        self._run("apply", "--server-side", "-f", "-",
                  input=json.dumps(manifest, sort_keys=True))

    def _get(self, kind: str, name: str, *, namespace: str | None = None) -> dict[str, Any]:
        try:
            return self._json("get", kind, name, "--namespace",
                              namespace or self.config.namespace)
        except subprocess.CalledProcessError as error:
            if "NotFound" in (error.stderr or ""):
                raise KubernetesObjectNotFound(name) from error
            raise

    def _patch(self, kind: str, name: str, patch: dict[str, Any],
               *, namespace: str | None = None) -> None:
        self._run("patch", kind, name, "--namespace", namespace or self.config.namespace,
                  "--type=merge", "--patch", json.dumps(patch, sort_keys=True))

    def _delete(self, kind: str, name: str, *, ignore_not_found: bool = False) -> None:
        args = ["delete", kind, name, "--namespace", self.config.namespace]
        if ignore_not_found:
            args.append("--ignore-not-found=true")
        self._run(*args)

    def _json(self, *args: str) -> dict[str, Any]:
        return json.loads(self._run(*args, "-o", "json").stdout)

    def _run(self, *args: str, input: str | None = None) -> subprocess.CompletedProcess[str]:
        return self._runner(
            ("kubectl", *args),
            input=input,
            text=True,
            capture_output=True,
            check=True,
        )


class KubernetesObjectNotFound(LookupError):
    pass


def _is_digest_image(image: str) -> bool:
    return bool(re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image))


def _cpu_quantity(cores: float) -> str:
    millicores = int(math.ceil(float(cores) * 1000))
    return f"{millicores}m"


def _byte_quantity(size: int) -> str:
    mebibytes = max(1, int(math.ceil(int(size) / (1 << 20))))
    return f"{mebibytes}Mi"
