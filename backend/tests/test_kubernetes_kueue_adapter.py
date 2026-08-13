from __future__ import annotations

import copy
import json
import subprocess
import unittest

from backend.tests.test_motif_contracts import EXECUTION_REQUEST
from executors.kubernetes_kueue import KubernetesKueueAdapter


IMAGE = "registry.example/dirac-worker@sha256:" + "1" * 64


class FakeKubectl:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.job: dict = {}

    def __call__(self, command, *, input=None, text, capture_output, check):
        self.calls.append((tuple(command), input))
        args = tuple(command[1:])
        if args[:3] == ("get", "job", "dirac-00000000000040008000000000000004"):
            return self._result(json.dumps(self.job))
        if args[:2] == ("get", "events"):
            return self._result('{"items": []}')
        if args[:2] == ("logs", "job/dirac-00000000000040008000000000000004"):
            return self._result("worker output\n")
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
            runner=self.runner,
        )
        self.request = copy.deepcopy(EXECUTION_REQUEST)
        self.request["placement"]["backend"] = "kubernetes"
        self.request["container_image"] = IMAGE
        self.request["attempt_id"] = "00000000-0000-4000-8000-000000000004"
        self.runner.job = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": "dirac-00000000000040008000000000000004",
                "namespace": "dirac-motif",
                "uid": "internal-kubernetes-uid",
                "labels": {"kueue.x-k8s.io/queue-name": "motif"},
            },
            "spec": {"suspend": True},
            "status": {},
        }

    def test_submit_uses_fixed_worker_and_kueue_suspension(self):
        self.request["entrypoint"] = ["malicious", "--escape"]
        status = self.adapter.submit(self.request)
        self.assertEqual(status.state, "suspended")
        manifests = [json.loads(body) for command, body in self.runner.calls
                     if command[1:3] == ("apply", "--server-side")]
        self.assertEqual(len(manifests), 2)
        job = manifests[1]
        container = job["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["command"], ["python", "-m", "dirac_worker"])
        self.assertEqual(job["spec"]["suspend"], True)
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

    def test_state_mapping_cancel_and_events(self):
        allocation = "dirac-motif/dirac-00000000000040008000000000000004"
        self.runner.job["spec"]["suspend"] = False
        self.runner.job["status"] = {"active": 1}
        self.assertEqual(self.adapter.inspect(allocation).state, "running")
        self.runner.job["status"] = {"succeeded": 1, "conditions": [
            {"type": "Complete", "status": "True", "reason": "Completed"}]}
        self.assertEqual(self.adapter.reconcile(allocation).state, "succeeded")
        self.adapter.request_cancel(allocation, grace_seconds=30)
        self.assertTrue(any(call[0][1:4] == ("delete", "job",
                                             "dirac-00000000000040008000000000000004")
                            for call in self.runner.calls))
        self.assertEqual(self.adapter.collect_events(None).cursor, "0")

    def test_mutable_images_are_rejected_at_configuration(self):
        with self.assertRaisesRegex(ValueError, "mutable"):
            KubernetesKueueAdapter(worker_command=["worker"],
                                   allowed_images=["registry.example/worker:latest"])


if __name__ == "__main__":
    unittest.main()
