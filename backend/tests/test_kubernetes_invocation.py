from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from catalog import MethodCatalog
from execution_control.protocol import AllocationStatus
from executors.kubernetes_invocation import KubernetesInvocationExecutor
from invocation import InvocationContext
from invocation import InvocationService
from jobs import MemoryJobStore
import kernel


class FakeAdapter:
    def __init__(self, root: Path, *, result: dict | None = None) -> None:
        self.root = root
        self.result = result
        self.request = None
        self.inspections = 0

    def submit(self, request):
        self.request = request
        if self.result is not None:
            directory = self.root / "outputs" / request["attempt_id"]
            directory.mkdir(parents=True)
            value = dict(self.result)
            value.update({key: request[key] for key in (
                "job_id", "attempt_id", "fencing_token", "execution_digest", "method_id")})
            (directory / "worker-result.json").write_text(json.dumps(value))
        return AllocationStatus("dirac-motif/job", "suspended", {})

    def inspect(self, allocation_id):
        self.inspections += 1
        return AllocationStatus(allocation_id, "succeeded", {"verified": True})

    def logs(self, allocation_id, tail=80):
        return "fake logs"


class KubernetesInvocationExecutorTests(unittest.TestCase):
    def test_default_worker_uses_one_non_overlapping_runtime_mount(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
                "os.environ", {
                    "DIRAC_EXECUTOR": "kubernetes",
                    "DIRAC_KUBERNETES_EXCHANGE_HOST": temporary,
                }, clear=False):
            executor = kernel.default_executor()
            try:
                mounts = {item.name: item.mount_path
                          for item in executor.adapter.static_pvc_mounts}
                self.assertEqual(mounts["dirac-runtime"], "/home/ivan/dirac")
                self.assertEqual(mounts["dirac-posix-shell"], "/bin/sh")
                self.assertNotIn("openfe-runtime-prefix", mounts)
            finally:
                executor.shutdown()

    def test_gpu_handler_crosses_execution_request_and_fenced_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = b"checkpoint"
            adapter = FakeAdapter(root, result={
                "schema_version": "1.0", "ok": True,
                "handler_result": {
                    "result": {"predictions": [], "count": 0},
                    "provenance": {"remote": True}, "warnings": [],
                    "parameters_used": {}, "cache": "computed",
                },
                "artifacts": [{"role": "model.predictions",
                               "filename": "artifacts/0000.bin",
                               "sha256": __import__('hashlib').sha256(artifact).hexdigest(),
                               "size_bytes": len(artifact)}],
            })
            original_submit = adapter.submit
            def submit(request):
                status = original_submit(request)
                path = root / "outputs" / request["attempt_id"] / "artifacts/0000.bin"
                path.parent.mkdir(parents=True)
                path.write_bytes(artifact)
                return status
            adapter.submit = submit
            executor = KubernetesInvocationExecutor(
                adapter=adapter, exchange_root=root,
                container_image="registry/worker@sha256:" + "a" * 64,
                poll_seconds=.01)
            spec = MethodCatalog.load().get("ml.motif.mesh.predict")
            ctx = InvocationContext(
                method_id=spec.method_id, version="sha256:" + "b" * 64,
                execution_digest="sha256:" + "c" * 64,
                budget_seconds=30,
                job_id="00000000-0000-4000-8000-000000000001", spec=spec)
            local_handler = mock.Mock(side_effect=AssertionError("must be remote"))
            output = executor.execute(local_handler, {
                "checkpoint": {"schema_version": "1", "algorithm": "x",
                               "digest": "sha256:" + "d" * 64,
                               "feature_release": {}, "members": []},
                "smiles": ["CCO"]}, ctx)
            self.assertEqual(output.result["count"], 0)
            self.assertEqual(output.artifacts, [("model.predictions", artifact)])
            self.assertEqual(adapter.request["placement"]["backend"], "kubernetes")
            self.assertEqual(adapter.request["resource_request"]["gpus"], 1)
            self.assertEqual(adapter.request["execution_digest"], ctx.execution_digest)
            local_handler.assert_not_called()

    def test_openfe_declares_analysis_cpu_in_immutable_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            executor = KubernetesInvocationExecutor(
                adapter=mock.Mock(), exchange_root=Path(temporary),
                container_image="registry/worker@sha256:" + "a" * 64)
            spec = MethodCatalog.load().get("physics.motif.openfe_edge")
            request = executor._request(
                InvocationContext(
                    method_id=spec.method_id, version="sha256:" + "b" * 64,
                    execution_digest="sha256:" + "c" * 64,
                    budget_seconds=30,
                    job_id="00000000-0000-4000-8000-000000000001", spec=spec),
                {}, attempt_id="00000000-0000-4000-8000-000000000002",
                input_id="sha256:" + "d" * 64, input_sha="sha256:" + "e" * 64,
                budget=30, now=__import__('datetime').datetime.now(
                    __import__('datetime').timezone.utc))
            self.assertEqual(request["resource_request"]["cpu_cores"], 20)

    def test_cpu_handler_stays_inside_controller_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            adapter = mock.Mock()
            executor = KubernetesInvocationExecutor(
                adapter=adapter, exchange_root=Path(temporary),
                container_image="registry/worker@sha256:" + "a" * 64)
            spec = MethodCatalog.load().get("design.motif.acquire")
            ctx = InvocationContext(method_id=spec.method_id, spec=spec)
            sentinel = object()
            handler = mock.Mock(return_value=sentinel)
            self.assertIs(executor.execute(handler, {"x": 1}, ctx), sentinel)
            adapter.submit.assert_not_called()

    def test_service_reports_remote_cancel_as_accepted_not_not_interruptible(self):
        ledger = MemoryJobStore()
        executor = mock.Mock(
            kind="remote", adapter_kind="kubernetes", supports_submission=True,
            cancellation_capability="cooperative+remote-hard")
        service = InvocationService(MethodCatalog.load(), ledger=ledger,
                                    executor=executor)
        job_id, _ = ledger.open(method_row_id="method", input_sha256=b"x" * 32,
                                params={}, queued=True)
        token = __import__('execution_control.protocol', fromlist=['CancellationToken']).CancellationToken()
        service._cancellation_tokens[job_id] = token
        row = service.cancel_job(job_id)
        self.assertTrue(row["cancel"]["accepted"])
        self.assertEqual(row["cancel"]["capability"], "cooperative+remote-hard")
        self.assertTrue(token.requested)


if __name__ == "__main__":
    unittest.main()
