#!/usr/bin/env python3
"""Destructive-to-its-own-fixtures acceptance test for the Motif Kueue stack.

Every Kubernetes object created here uses the ``dirac-9...`` attempt range and
is deleted in ``finally``. The harness never deletes pre-existing objects.
Run the harness itself through ``gpu-run --wait`` so local CUDA work cannot race
the Kubernetes GPU tests.
"""
from __future__ import annotations

import copy
import json
import subprocess
import time
from dataclasses import asdict
from typing import Any, Callable

from executors.kubernetes_kueue import KubernetesKueueAdapter


BUSYBOX = (
    "docker.io/library/busybox@sha256:"
    "9db7b59979c38555a39def84a31fb98b5296952f9e3afd4f6f11f05b07adfab0"
)
GPU_OPERATOR = (
    "nvcr.io/nvidia/gpu-operator@sha256:"
    "6584c36f153d18cfce284f7e5bc477887ce3c1ac566dc795bd80c9af6c6488f7"
)
TERMINAL = {"succeeded", "failed", "cancelled", "unknown"}
BASE_REQUEST = {
    "schema_version": "1.0",
    "execution_id": "90000000-0000-4000-8000-000000000900",
    "job_id": "90000000-0000-4000-8000-000000000901",
    "step_id": "90000000-0000-4000-8000-000000000902",
    "attempt": 1,
    "fencing_token": 1,
    "method_id": "ml.motif.evaluate",
    "entrypoint": ["ignored-by-fixed-worker-policy"],
    "input_manifest_artifact_id": "90000000-0000-4000-8000-000000000903",
    "output_contract_digest": "sha256:" + "a" * 64,
    "retry_policy": {
        "max_attempts": 1,
        "retryable_codes": [],
        "backoff": {"kind": "fixed", "initial_seconds": 1, "max_seconds": 1},
        "preserve_seed": True,
        "resume_from_checkpoint": False,
    },
    "checkpoint_policy": {
        "enabled": False,
        "upload_mode": "sync",
        "retain_last": 0,
        "checkpoint_timeout_seconds": 60,
    },
    "security_context": {
        "actor": {"kind": "service", "id": "adversarial-verifier"},
        "project_scope": "program:adversarial-verification",
        "artifact_read_ids": ["90000000-0000-4000-8000-000000000903"],
        "artifact_write_session": "adversarial-verification",
        "credential_expires_at": "2030-01-01T00:00:00Z",
    },
    "determinism": {"class": "bitwise", "root_seed": 1729, "numeric_mode": "fp64"},
}


def kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("kubectl", *args), text=True, capture_output=True, check=check
    )


def kube_json(*args: str) -> dict[str, Any]:
    return json.loads(kubectl(*args, "-o", "json").stdout)


def request(number: int, *, image: str, cpu: float = 1, memory: int = 256 << 20,
            gpus: int = 0, walltime: int = 60) -> dict[str, Any]:
    value = copy.deepcopy(BASE_REQUEST)
    attempt = f"90000000-0000-4000-8000-{number:012d}"
    value.update({
        "attempt_id": attempt,
        "container_image": image,
        "execution_digest": "sha256:" + f"{number % 16:x}" * 64,
        "created_at": "2026-08-13T00:00:00Z",
    })
    value["placement"] = {
        "backend": "kubernetes",
        "topology": "single_process",
        "workload_priority_class": "motif-standard",
    }
    value["resource_request"] = {
        "cpu_cores": cpu,
        "memory_bytes": memory,
        "gpus": gpus,
        "scratch_bytes": 1 << 20,
        "walltime_seconds": walltime,
    }
    if gpus:
        value["resource_request"].update({
            "gpu_arch": ["blackwell"], "gpu_memory_bytes_min": 1 << 30,
        })
    return value


def adapter(command: list[str], image: str) -> KubernetesKueueAdapter:
    return KubernetesKueueAdapter(
        worker_command=command, allowed_images=[image], policy_init_image=BUSYBOX)


def wait_status(instance: KubernetesKueueAdapter, allocation_id: str, *,
                wanted: set[str] = TERMINAL, timeout: float = 90) -> Any:
    deadline = time.monotonic() + timeout
    status = instance.inspect(allocation_id)
    while status.state not in wanted and time.monotonic() < deadline:
        time.sleep(.25)
        status = instance.inspect(allocation_id)
    if status.state not in wanted:
        raise AssertionError(
            f"{allocation_id} did not reach {sorted(wanted)}; last={asdict(status)}"
        )
    return status


def workload_for(job_name: str) -> dict[str, Any]:
    values = kube_json("get", "workloads.kueue.x-k8s.io", "-n", "dirac-motif")
    for item in values.get("items", []):
        owners = item.get("metadata", {}).get("ownerReferences", [])
        if any(owner.get("kind") == "Job" and owner.get("name") == job_name
               for owner in owners):
            return item
    raise AssertionError(f"no Kueue Workload owns Job {job_name}")


def condition(workload: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((item for item in workload.get("status", {}).get("conditions", [])
                 if item.get("type") == kind), None)


class Verification:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []
        self.allocations: list[tuple[KubernetesKueueAdapter, str]] = []

    def record(self, name: str, verdict: str, **evidence: Any) -> None:
        self.results.append({"name": name, "verdict": verdict, "evidence": evidence})
        print(json.dumps(self.results[-1], sort_keys=True), flush=True)

    def submit(self, instance: KubernetesKueueAdapter, value: dict[str, Any]):
        status = instance.submit(value)
        self.allocations.append((instance, status.allocation_id))
        return status

    def cleanup(self) -> None:
        for instance, allocation_id in reversed(self.allocations):
            try:
                instance.request_cancel(allocation_id, grace_seconds=0)
            except Exception:
                pass
        names = [allocation_id.split("/", 1)[1] for _, allocation_id in self.allocations]
        deadline = time.monotonic() + 30
        while names and time.monotonic() < deadline:
            names = [name for name in names if kubectl(
                "get", "job", name, "-n", "dirac-motif", check=False
            ).returncode == 0]
            if names:
                time.sleep(.25)

    def run_case(self, name: str, body: Callable[[], dict[str, Any]]) -> None:
        started = time.monotonic()
        try:
            evidence = body()
        except Exception as error:
            details = {"error": repr(error)}
            if isinstance(error, subprocess.CalledProcessError):
                details["stderr_tail"] = (error.stderr or "")[-1000:]
            self.record(name, "overturned", **details,
                        elapsed_seconds=round(time.monotonic() - started, 3))
        else:
            self.record(name, "confirmed", **evidence,
                        elapsed_seconds=round(time.monotonic() - started, 3))


def main() -> int:
    verify = Verification()
    try:
        verify.run_case("baseline_health", lambda: baseline_health())
        verify.run_case("fail_closed_request_boundaries", lambda: request_boundaries())
        verify.run_case("pod_security_and_network_boundary", lambda: security_boundary(verify))
        verify.run_case("hard_walltime", lambda: hard_walltime(verify))
        verify.run_case("real_cancellation_and_gc", lambda: cancellation(verify))
        verify.run_case("duplicate_attempt_identity", lambda: duplicate_attempt(verify))
        verify.run_case("cpu_quota_vs_physical_capacity", lambda: cpu_overcommit(verify))
        for round_number in range(3):
            verify.run_case(
                f"gpu_exclusive_round_{round_number + 1}",
                lambda round_number=round_number: gpu_exclusive(verify, round_number),
            )
        verify.run_case("event_cursor_is_monotonic", lambda: event_cursor())
    finally:
        verify.cleanup()
    overturned = [item for item in verify.results if item["verdict"] == "overturned"]
    print(json.dumps({
        "summary": {
            "confirmed": len(verify.results) - len(overturned),
            "overturned": len(overturned),
            "total": len(verify.results),
        },
        "results": verify.results,
    }, sort_keys=True), flush=True)
    return 1 if overturned else 0


def baseline_health() -> dict[str, Any]:
    policy = kube_json("get", "clusterpolicy", "cluster-policy")
    node = kube_json("get", "node", "icu")
    queue = kube_json("get", "clusterqueue", "motif")
    assert policy["status"]["state"] == "ready"
    assert node["status"]["allocatable"]["nvidia.com/gpu"] == "1"
    active = condition(queue, "Active")
    assert active and active["status"] == "True"
    return {
        "operator": "ready",
        "node_cpu": node["status"]["allocatable"]["cpu"],
        "node_gpu": node["status"]["allocatable"]["nvidia.com/gpu"],
        "queue_active": True,
    }


def request_boundaries() -> dict[str, Any]:
    instance = adapter(["/bin/true"], BUSYBOX)
    base = request(1, image=BUSYBOX)
    rejected = []
    mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        ("missing_attempt", lambda value: value.pop("attempt_id")),
        ("zero_cpu", lambda value: value["resource_request"].update(cpu_cores=0)),
        ("zero_walltime", lambda value: value["resource_request"].update(walltime_seconds=0)),
        ("negative_gpu", lambda value: value["resource_request"].update(gpus=-1)),
        ("unknown_field", lambda value: value.update(escape=True)),
        ("bad_attempt_uuid", lambda value: value.update(attempt_id="../../escape")),
        ("bad_environment_type", lambda value: value.update(environment={"OK": 3})),
        ("unknown_priority", lambda value: value["placement"].update(
            workload_priority_class="motif-unknown")),
    ]
    for name, mutate in mutations:
        candidate = copy.deepcopy(base)
        mutate(candidate)
        try:
            instance.admit(candidate)
        except Exception:
            rejected.append(name)
    assert rejected == [name for name, _ in mutations]

    sensitive = copy.deepcopy(base)
    sensitive["environment"] = {"DB_PASSWORD": "plaintext"}
    reserved = copy.deepcopy(base)
    reserved["environment"] = {"DIRAC_ALLOCATION_ID": "forged"}
    wrong_backend = copy.deepcopy(base)
    wrong_backend["placement"]["backend"] = "local_gpu"
    topology = copy.deepcopy(base)
    topology["placement"]["topology"] = "multi_node"
    decisions = {
        "sensitive": instance.admit(sensitive).code,
        "reserved": instance.admit(reserved).code,
        "backend": instance.admit(wrong_backend).code,
        "topology": instance.admit(topology).code,
    }
    assert decisions == {
        "sensitive": "SENSITIVE_ENVIRONMENT",
        "reserved": "RESERVED_ENVIRONMENT",
        "backend": "PLACEMENT_MISMATCH",
        "topology": "TOPOLOGY_UNSUPPORTED",
    }
    return {"schema_mutations_rejected": len(rejected), "decisions": decisions}


def security_boundary(verify: Verification) -> dict[str, Any]:
    command = ["/bin/sh", "-c", """
set -eu
test "$(id -u)" = "65532"
test -r /var/run/dirac/execution-request.json
test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token
if touch /dirac-root-write-probe 2>/dev/null; then exit 41; fi
if wget -T 2 -qO- https://example.com >/dev/null 2>&1; then exit 42; fi
echo DIRAC_SECURITY_BOUNDARY_OK
"""]
    instance = adapter(command, BUSYBOX)
    status = verify.submit(instance, request(10, image=BUSYBOX, walltime=20))
    final = wait_status(instance, status.allocation_id)
    logs = instance.logs(status.allocation_id)
    assert final.state == "succeeded" and "DIRAC_SECURITY_BOUNDARY_OK" in logs
    job = kube_json("get", "job", status.allocation_id.split("/")[1], "-n", "dirac-motif")
    pod_spec = job["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    assert pod_spec["automountServiceAccountToken"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    return {"state": final.state, "logs": logs.strip(),
            "service_account_token": "absent", "egress": "denied"}


def hard_walltime(verify: Verification) -> dict[str, Any]:
    instance = adapter(["/bin/sh", "-c", "echo WALLTIME_STARTED; sleep 30"], BUSYBOX)
    status = verify.submit(instance, request(20, image=BUSYBOX, walltime=7))
    # The walltime starts only after Kueue resumes the Pod.  Keep the execution
    # deadline at seven seconds while allowing independent queue/start/terminal
    # observation latency in the acceptance harness.
    final = wait_status(instance, status.allocation_id, timeout=60)
    assert final.state == "failed"
    reasons = [item.get("reason") for item in final.scheduler_summary["conditions"]]
    reasons.extend(item.get("reason")
                   for item in final.scheduler_summary["pod_status"])
    reasons.extend(final.scheduler_summary["termination_reasons"])
    assert "DeadlineExceeded" in reasons
    return {"state": final.state, "condition_reasons": reasons,
            "logs": instance.logs(status.allocation_id).strip()}


def cancellation(verify: Verification) -> dict[str, Any]:
    instance = adapter(["/bin/sh", "-c", "echo CANCEL_STARTED; sleep 60"], BUSYBOX)
    status = verify.submit(instance, request(30, image=BUSYBOX, walltime=120))
    running = wait_status(instance, status.allocation_id, wanted={"running"}, timeout=30)
    assert running.state == "running"
    job_name = status.allocation_id.split("/")[1]
    job_uid = kube_json("get", "job", job_name, "-n", "dirac-motif")["metadata"]["uid"]
    instance.request_cancel(status.allocation_id, grace_seconds=0)
    cancelled_at = time.monotonic()
    deadline = time.monotonic() + 120
    while kubectl("get", "job", job_name, "-n", "dirac-motif", check=False).returncode == 0:
        if time.monotonic() >= deadline:
            raise AssertionError("cancelled Job still exists")
        time.sleep(.25)
    while (kubectl("get", "configmap", f"{job_name}-request", "-n", "dirac-motif",
                   check=False).returncode == 0 or kube_json(
                       "get", "pods", "-n", "dirac-motif",
                       "-l", f"job-name={job_name}").get("items", [])):
        if time.monotonic() >= deadline:
            raise AssertionError("owned request ConfigMap was not garbage collected")
        time.sleep(.25)
    pods = kube_json("get", "pods", "-n", "dirac-motif",
                     "-l", f"job-name={job_name}").get("items", [])
    workloads = kube_json(
        "get", "workloads.kueue.x-k8s.io", "-n", "dirac-motif",
        "-l", f"kueue.x-k8s.io/job-uid={job_uid}").get("items", [])
    assert not pods
    assert not workloads
    return {"job_deleted": True, "pod_count": 0, "workload_count": 0,
            "request_configmap_deleted": True,
            "converged_seconds": round(time.monotonic() - cancelled_at, 3)}


def duplicate_attempt(verify: Verification) -> dict[str, Any]:
    instance = adapter(["/bin/sh", "-c", "sleep 4; echo DUPLICATE_OK"], BUSYBOX)
    value = request(40, image=BUSYBOX, walltime=30)
    first = verify.submit(instance, value)
    job_name = first.allocation_id.split("/")[1]
    first_uid = kube_json("get", "job", job_name, "-n", "dirac-motif")["metadata"]["uid"]
    try:
        second = instance.submit(value)
    except subprocess.CalledProcessError as error:
        raise AssertionError(
            "identical attempt resubmission is not idempotent: "
            + (error.stderr or "")[-1000:]
        ) from error
    second_uid = kube_json("get", "job", job_name, "-n", "dirac-motif")["metadata"]["uid"]
    assert first.allocation_id == second.allocation_id and first_uid == second_uid
    count = len(kube_json("get", "jobs", "-n", "dirac-motif",
                         "-l", f"dirac.io/attempt-id={value['attempt_id']}")["items"])
    assert count == 1
    collision = copy.deepcopy(value)
    collision["execution_digest"] = "sha256:" + "f" * 64
    try:
        instance.submit(collision)
    except RuntimeError as error:
        conflict = str(error)[-500:]
    else:
        raise AssertionError("same attempt_id accepted a different execution digest")
    final = wait_status(instance, first.allocation_id)
    assert final.state == "succeeded"
    return {"same_uid": first_uid, "job_count": count,
            "collision_rejected": True, "conflict_tail": conflict}


def cpu_overcommit(verify: Verification) -> dict[str, Any]:
    instances, statuses = [], []
    for offset in range(3):
        instance = adapter(["/bin/sh", "-c", "echo CPU_STARTED; sleep 30"], BUSYBOX)
        instances.append(instance)
        statuses.append(verify.submit(
            instance, request(50 + offset, image=BUSYBOX, cpu=12, memory=256 << 20,
                              walltime=90)))
    max_reserved_cpu = 0
    max_running = 0
    saw_waiting = False
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        queue = kube_json("get", "clusterqueue", "motif")
        cpu_values = [resource.get("total", "0")
                      for flavor in queue.get("status", {}).get("flavorsReservation", [])
                      for resource in flavor.get("resources", [])
                      if resource.get("name") == "cpu"]
        max_reserved_cpu = max(max_reserved_cpu, int(cpu_values[0]) if cpu_values else 0)
        jobs = [instance.inspect(status.allocation_id).state
                for instance, status in zip(instances, statuses)]
        max_running = max(max_running, jobs.count("running"))
        saw_waiting = saw_waiting or "suspended" in jobs
        time.sleep(.25)
    events = kube_json("get", "events", "-n", "dirac-motif")
    unschedulable = [item.get("note") or item.get("message")
                     for item in events.get("items", [])
                     if item.get("reason") == "FailedScheduling" and
                     item.get("involvedObject", {}).get("name", "").startswith("dirac-900")]
    if max_reserved_cpu > 20:
        raise AssertionError(
            f"Kueue reserved {max_reserved_cpu} CPU above its 20-core quota; "
            f"max_running={max_running}, states={jobs}, waiting={saw_waiting}, "
            f"failed_scheduling={bool(unschedulable)}"
        )
    assert max_running < 3
    assert saw_waiting or unschedulable
    return {"max_reserved_cpu": max_reserved_cpu, "max_running": max_running,
            "saw_waiting": saw_waiting, "job_states": jobs,
            "node_allocatable_cpu": kube_json("get", "node", "icu")["status"]
            ["allocatable"]["cpu"], "declared_queue_cpu": "20",
            "failed_scheduling_tail": unschedulable[-1][-500:] if unschedulable else None}


def gpu_exclusive(verify: Verification, round_number: int) -> dict[str, Any]:
    base = 100 + round_number * 10
    command = [
        "/usr/bin/nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader",
        "--loop=1",
    ]
    instances = [adapter(command, GPU_OPERATOR), adapter(command, GPU_OPERATOR)]
    gpu = instances[0].health()["gpu"]

    def bound_gpu_request(number: int, *, walltime: int) -> dict[str, Any]:
        value = request(number, image=GPU_OPERATOR, gpus=1, walltime=walltime)
        value["resource_request"]["gpu_arch"] = [gpu["arch"]]
        value["determinism"]["numeric_mode"] = gpu["numeric_mode"]
        value["placement"]["node_constraints"] = gpu["node_selector"]
        return value

    statuses = [verify.submit(instances[index], bound_gpu_request(
        base + index, walltime=6
    )) for index in range(2)]
    max_reserved = 0
    saw_second_waiting = False
    live_logs = ["", ""]
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        queue = kube_json("get", "clusterqueue", "motif")
        reservations = queue.get("status", {}).get("flavorsReservation", [])
        gpu_values = [resource.get("total", "0") for flavor in reservations
                      for resource in flavor.get("resources", [])
                      if resource.get("name") == "nvidia.com/gpu"]
        reserved = int(gpu_values[0]) if gpu_values else 0
        max_reserved = max(max_reserved, reserved)
        current = [instance.inspect(status.allocation_id).state
                   for instance, status in zip(instances, statuses)]
        for index, state in enumerate(current):
            if state == "running":
                result = kubectl(
                    "logs", "job/" + statuses[index].allocation_id.split("/")[1],
                    "--namespace", "dirac-motif", "--tail", "20", check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    live_logs[index] = result.stdout.strip()
        if current[0] in {"running", "pending"} and current[1] == "suspended":
            saw_second_waiting = True
        if all(state in TERMINAL for state in current):
            break
        time.sleep(.2)
    finals = [wait_status(instance, status.allocation_id, timeout=10)
              for instance, status in zip(instances, statuses)]
    assert max_reserved == 1
    assert saw_second_waiting
    assert all(final.state == "failed" for final in finals)
    assert all("RTX 5080" in output and "595.84" in output for output in live_logs)

    postmortem_logs = [kubectl(
        "logs", "job/" + status.allocation_id.split("/")[1],
        "--namespace", "dirac-motif", "--tail", "20", check=False,
    ).returncode == 0 for status in statuses]
    assert all(postmortem_logs)

    smoke = adapter(command[:-1], GPU_OPERATOR)
    smoke_status = verify.submit(smoke, bound_gpu_request(base + 2, walltime=30))
    smoke_final = wait_status(smoke, smoke_status.allocation_id, timeout=30)
    smoke_logs = smoke.logs(smoke_status.allocation_id).strip()
    assert smoke_final.state == "succeeded" and "RTX 5080" in smoke_logs
    return {"max_reserved_gpu": max_reserved, "second_waited": saw_second_waiting,
            "timeout_states": [item.state for item in finals],
            "live_log_lines": [len(output.splitlines()) for output in live_logs],
            "postmortem_logs_available": postmortem_logs,
            "post_contention_smoke": smoke_logs}


def event_cursor() -> dict[str, Any]:
    instance = adapter(["/bin/true"], BUSYBOX)
    first = instance.collect_events(None)
    second = instance.collect_events(first.cursor)
    assert first.cursor is not None
    assert not second.events and second.cursor == first.cursor
    return {"first_event_count": len(first.events), "cursor": first.cursor,
            "replay_event_count": len(second.events)}


if __name__ == "__main__":
    raise SystemExit(main())
