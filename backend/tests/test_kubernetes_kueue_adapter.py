from __future__ import annotations

import copy
import json
import subprocess
import unittest

from backend.tests.test_motif_contracts import EXECUTION_REQUEST
from executors.kubernetes_kueue import (
    KubernetesKueueAdapter, StaticHostMount, StaticPvcMount)


IMAGE = "registry.example/dirac-worker@sha256:" + "1" * 64


class FakeKubectl:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.job: dict = {}
        self.config_map: dict = {}
        self.pods: list[dict] = []
        self.job_exists = False

    def __call__(self, command, *, input=None, text, capture_output, check):
        self.calls.append((tuple(command), input))
        args = tuple(command[1:])
        if args[:3] == ("get", "job", "dirac-00000000000040008000000000000004"):
            if not self.job_exists:
                raise subprocess.CalledProcessError(
                    1, command, stderr='Error from server (NotFound): jobs "missing" not found')
            return self._result(json.dumps(self.job))
        if args[:3] == ("get", "configmap", "dirac-00000000000040008000000000000004-request"):
            if not self.config_map:
                raise subprocess.CalledProcessError(
                    1, command, stderr='Error from server (NotFound): configmaps "missing" not found')
            return self._result(json.dumps(self.config_map))
        if args[:2] == ("get", "pods"):
            return self._result(json.dumps({"items": self.pods}))
        if args[:2] == ("get", "events"):
            return self._result('{"items": []}')
        if args[:2] == ("logs", "job/dirac-00000000000040008000000000000004"):
            return self._result("worker output\n")
        if args[:3] == ("apply", "--server-side", "-f"):
            manifest = json.loads(input)
            if manifest["kind"] == "ConfigMap":
                self.config_map = manifest
            elif manifest["kind"] == "Job":
                self.job = manifest
                self.job["metadata"]["uid"] = "internal-kubernetes-uid"
                self.job["status"] = {}
                self.job_exists = True
            return self._result("{}")
        if args[:3] == ("patch", "configmap", "dirac-00000000000040008000000000000004-request"):
            patch = json.loads(args[7])
            self.config_map["metadata"].update(patch["metadata"])
        return self._result("{}")

    @staticmethod
    def _result(stdout: str):
        return subprocess.CompletedProcess(("kubectl",), 0, stdout=stdout, stderr="")


class KubernetesKueueAdapterTests(unittest.TestCase):
    def setUp(self):
        self.runner = FakeKubectl()
        self.adapter = KubernetesKueueAdapter(
            worker_command=["python", "-m", "dirac_worker"],
            allowed_images=[IMAGE],
            policy_init_image=IMAGE,
            runner=self.runner,
        )
        self.request = copy.deepcopy(EXECUTION_REQUEST)
        self.request["placement"]["backend"] = "kubernetes"
        self.request["container_image"] = IMAGE
        self.request["attempt_id"] = "00000000-0000-4000-8000-000000000004"

    def test_submit_uses_fixed_worker_and_kueue_suspension(self):
        self.request["entrypoint"] = ["malicious", "--escape"]
        status = self.adapter.submit(self.request)
        self.assertEqual(status.state, "suspended")
        manifests = [json.loads(body) for command, body in self.runner.calls
                     if command[1:3] == ("apply", "--server-side")]
        self.assertEqual(len(manifests), 2)
        job = manifests[1]
        container = job["spec"]["template"]["spec"]["containers"][0]
        init = job["spec"]["template"]["spec"]["initContainers"][0]
        self.assertEqual(container["command"], ["python", "-m", "dirac_worker"])
        self.assertEqual(container["image"], IMAGE)
        self.assertEqual(init["command"], ["/bin/sh", "-c", "sleep 3"])
        self.assertEqual(init["image"], IMAGE)
        self.assertEqual(job["spec"]["suspend"], True)
        self.assertNotIn("activeDeadlineSeconds", job["spec"])
        self.assertEqual(job["spec"]["template"]["spec"]["activeDeadlineSeconds"], 3600)
        self.assertEqual(job["spec"]["template"]["spec"]["terminationGracePeriodSeconds"],
                         5)
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertEqual(
            job["metadata"]["labels"]["kueue.x-k8s.io/queue-name"], "motif")
        self.assertNotIn("internal-kubernetes-uid", json.dumps(status.scheduler_summary))

    def test_image_allowlist_and_sensitive_env_fail_closed(self):
        unknown = copy.deepcopy(self.request)
        unknown["container_image"] = "registry.example/other@sha256:" + "2" * 64
        self.assertEqual(self.adapter.admit(unknown).code, "IMAGE_NOT_ALLOWED")
        sensitive = copy.deepcopy(self.request)
        sensitive["environment"] = {"API_TOKEN": "do-not-pass"}
        self.assertEqual(self.adapter.admit(sensitive).code, "SENSITIVE_ENVIRONMENT")
        reserved = copy.deepcopy(self.request)
        reserved["environment"] = {"DIRAC_EXECUTION_REQUEST": "/tmp/escape"}
        self.assertEqual(self.adapter.admit(reserved).code, "RESERVED_ENVIRONMENT")

    def test_gpu_job_uses_nvidia_runtime_class_and_extended_resource(self):
        request = copy.deepcopy(self.request)
        request["resource_request"].update({
            "gpus": 1,
            "gpu_arch": ["blackwell"],
            "gpu_memory_bytes_min": 1,
        })
        self.adapter.submit(request)
        manifests = [json.loads(body) for command, body in self.runner.calls
                     if command[1:3] == ("apply", "--server-side")]
        pod_spec = manifests[1]["spec"]["template"]["spec"]
        self.assertEqual(pod_spec["runtimeClassName"], "nvidia")
        resources = pod_spec["containers"][0]["resources"]
        self.assertEqual(resources["requests"]["nvidia.com/gpu"], "1")
        self.assertEqual(resources["limits"]["nvidia.com/gpu"], "1")

    def test_deployment_owned_host_mounts_are_fixed_and_not_request_controlled(self):
        adapter = KubernetesKueueAdapter(
            worker_command=["worker"], allowed_images=[IMAGE],
            policy_init_image=IMAGE, runner=self.runner,
            static_host_mounts=[
                StaticHostMount("runtime", "/srv/dirac", "/srv/dirac", True),
                StaticHostMount("exchange", "/var/lib/dirac", "/exchange", False),
            ])
        adapter.submit(self.request)
        manifests = [json.loads(body) for command, body in self.runner.calls
                     if command[1:3] == ("apply", "--server-side")]
        pod = manifests[-1]["spec"]["template"]["spec"]
        mounts = {item["name"]: item for item in pod["containers"][0]["volumeMounts"]}
        volumes = {item["name"]: item for item in pod["volumes"]}
        self.assertEqual(mounts["runtime"]["readOnly"], True)
        self.assertEqual(mounts["exchange"]["readOnly"], False)
        self.assertEqual(volumes["runtime"]["hostPath"]["path"], "/srv/dirac")
        self.assertNotIn("host_path", json.dumps(self.request))

        with self.assertRaisesRegex(ValueError, "absolute normalized"):
            StaticHostMount("escape", "../etc", "/etc")

    def test_restricted_pss_compatible_pvc_mounts_are_fixed(self):
        adapter = KubernetesKueueAdapter(
            worker_command=["worker"], allowed_images=[IMAGE],
            policy_init_image=IMAGE, runner=self.runner,
            static_pvc_mounts=[
                StaticPvcMount("runtime", "dirac-runtime", "/srv/dirac", True),
                StaticPvcMount("shell", "dirac-runtime", "/bin/sh", True,
                               sub_path="runtime-bin/dash"),
                StaticPvcMount("exchange", "dirac-exchange", "/exchange", False),
            ])
        adapter.submit(self.request)
        manifests = [json.loads(body) for command, body in self.runner.calls
                     if command[1:3] == ("apply", "--server-side")]
        pod = manifests[-1]["spec"]["template"]["spec"]
        self.assertFalse(any("hostPath" in volume for volume in pod["volumes"]))
        claims = {volume["name"]: volume["persistentVolumeClaim"]
                  for volume in pod["volumes"] if "persistentVolumeClaim" in volume}
        self.assertEqual(claims["runtime"], {
            "claimName": "dirac-runtime", "readOnly": True})
        self.assertEqual(claims["exchange"], {
            "claimName": "dirac-exchange", "readOnly": False})
        mounts = {item["name"]: item for item in pod["containers"][0]["volumeMounts"]}
        self.assertEqual(mounts["shell"]["subPath"], "runtime-bin/dash")
        with self.assertRaisesRegex(ValueError, "relative normalized"):
            StaticPvcMount("bad", "dirac-runtime", "/bin/sh", True,
                           sub_path="../dash")

    def test_state_mapping_cancel_and_events(self):
        self.adapter.submit(self.request)
        allocation = "dirac-motif/dirac-00000000000040008000000000000004"
        self.runner.job["spec"]["suspend"] = False
        self.runner.job["status"] = {"active": 1}
        self.runner.pods = [{"status": {"phase": "Pending", "conditions": [{
            "type": "PodScheduled", "status": "False", "reason": "Unschedulable",
            "message": "Insufficient cpu",
        }]}}]
        pending = self.adapter.inspect(allocation)
        self.assertEqual(pending.state, "pending")
        self.assertEqual(pending.scheduler_summary["unschedulable"][0]["reason"],
                         "Unschedulable")
        self.runner.pods[0]["status"]["phase"] = "Running"
        self.assertEqual(self.adapter.inspect(allocation).state, "running")
        self.runner.pods[0]["status"].update({
            "phase": "Failed", "reason": "DeadlineExceeded",
        })
        self.assertEqual(self.adapter.inspect(allocation).state, "failed")
        self.runner.job["status"] = {"succeeded": 1, "conditions": [
            {"type": "Complete", "status": "True", "reason": "Completed"}]}
        self.assertEqual(self.adapter.reconcile(allocation).state, "succeeded")
        self.adapter.request_cancel(allocation, grace_seconds=30)
        self.assertTrue(any(call[0][1:4] == ("delete", "job",
                                             "dirac-00000000000040008000000000000004")
                            for call in self.runner.calls))
        job_delete = next(call[0] for call in self.runner.calls
                          if call[0][1:3] == ("delete", "job"))
        self.assertIn("--cascade=orphan", job_delete)
        self.assertTrue(any(call[0][1:3] == ("delete", "pods")
                            for call in self.runner.calls))
        self.assertTrue(any(call[0][1:4] == ("delete", "configmap",
                                             "dirac-00000000000040008000000000000004-request")
                            for call in self.runner.calls))
        workload_delete = next(call[0] for call in self.runner.calls
                               if call[0][1:3] == (
                                   "delete", "workloads.kueue.x-k8s.io"))
        self.assertIn("kueue.x-k8s.io/job-uid=internal-kubernetes-uid",
                      workload_delete)
        self.assertEqual(self.adapter.collect_events(None).cursor, "0")

    def test_exact_resubmission_is_idempotent_but_collision_is_rejected(self):
        first = self.adapter.submit(self.request)
        apply_count = sum(call[0][1:3] == ("apply", "--server-side")
                          for call in self.runner.calls)
        self.runner.job["spec"]["suspend"] = False  # Kueue owns this mutation.
        second = self.adapter.submit(copy.deepcopy(self.request))
        self.assertEqual(first.allocation_id, second.allocation_id)
        self.assertEqual(
            apply_count,
            sum(call[0][1:3] == ("apply", "--server-side")
                for call in self.runner.calls),
        )
        collision = copy.deepcopy(self.request)
        collision["execution_digest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(RuntimeError, "ATTEMPT_IDENTITY_COLLISION"):
            self.adapter.submit(collision)

    def test_configmap_first_race_is_replayed_without_overwrite(self):
        request_json = json.dumps(
            self.request, sort_keys=True, separators=(",", ":"))
        self.runner.config_map = self.adapter._config_map_manifest(
            "dirac-00000000000040008000000000000004-request",
            "dirac-00000000000040008000000000000004", request_json)
        self.adapter.submit(copy.deepcopy(self.request))
        applied = [json.loads(body) for command, body in self.runner.calls
                   if command[1:3] == ("apply", "--server-side")]
        self.assertEqual([item["kind"] for item in applied], ["Job"])

        self.runner.job_exists = False
        collision = copy.deepcopy(self.request)
        collision["execution_digest"] = "sha256:" + "e" * 64
        with self.assertRaisesRegex(RuntimeError, "ATTEMPT_IDENTITY_COLLISION"):
            self.adapter.submit(collision)

    def test_mutable_images_are_rejected_at_configuration(self):
        with self.assertRaisesRegex(ValueError, "mutable"):
            KubernetesKueueAdapter(worker_command=["worker"],
                                   allowed_images=["registry.example/worker:latest"],
                                   policy_init_image=IMAGE)

    def test_policy_barrier_requires_a_pinned_init_image(self):
        with self.assertRaisesRegex(ValueError, "policy_init_image"):
            KubernetesKueueAdapter(worker_command=["worker"], allowed_images=[IMAGE])


if __name__ == "__main__":
    unittest.main()
