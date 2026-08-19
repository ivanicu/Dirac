from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import failures
from catalog import MethodCatalog
from artifacts import MemoryArtifactStore
from execution_control.identity import ExecutionIdentity, sha256_digest
from execution_control.protocol import AllocationStatus
from executors.kubernetes_invocation import KubernetesInvocationExecutor
from invocation import InvocationContext
from invocation import InvocationService
from jobs import MemoryJobStore
import kernel
import motif_worker


IMAGE = "registry/worker@sha256:" + "a" * 64


def gpu_identity(method_id: str) -> ExecutionIdentity:
    return ExecutionIdentity.build(
        method_id=method_id,
        method_descriptor_digest=sha256_digest("descriptor"),
        handler_source_digest=sha256_digest("handler"),
        container_image=IMAGE,
        executor_adapter="kubernetes",
        hardware_compatibility_profile=f"kubernetes:gpu:blackwell:{16 << 30}",
        numeric_mode="fp32")


class FakeAdapter:
    def __init__(self, root: Path, *, result: dict | None = None) -> None:
        self.root = root
        self.result = result
        self.request = None
        self.inspections = 0
        self.allowed_images = {IMAGE}

    def submit(self, request):
        self.request = request
        if self.result is not None:
            directory = self.root / "outputs" / request["attempt_id"]
            directory.mkdir(parents=True)
            value = dict(self.result)
            value.update({key: request[key] for key in (
                "job_id", "attempt_id", "fencing_token", "execution_digest", "method_id")})
            value.setdefault("worker_attestation", {"gpu": {
                "available": True, "architecture": "blackwell",
                "numeric_mode": "fp32", "compute_capability": "12.0",
                "memory_bytes": 16 << 30,
            }})
            (directory / "worker-result.json").write_text(json.dumps(value))
        return AllocationStatus("dirac-motif/job", "suspended", {})

    def inspect(self, allocation_id):
        self.inspections += 1
        return AllocationStatus(allocation_id, "succeeded", {"verified": True})

    def logs(self, allocation_id, tail=80):
        return "fake logs"

    def request_cancel(self, allocation_id, grace_seconds=5):
        return None

    def health(self):
        return {"ready": True, "gpu": {
            "verified": True, "arch": "blackwell", "numeric_mode": "fp32",
            "memory_bytes": 16 << 30,
            "node_selector": {"dirac.io/gpu-arch": "blackwell",
                              "dirac.io/gpu-numeric-mode": "fp32",
                              "dirac.io/gpu-memory-bytes": str(16 << 30)},
        }}


class KubernetesInvocationExecutorTests(unittest.TestCase):
    def test_remote_worker_receives_only_explicit_digest_verified_artifact_capabilities(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryArtifactStore()
            artifact = store.put(b'{"governed":true}', role="rbfe.edge_spec",
                                 media_type="application/json")
            executor = KubernetesInvocationExecutor(
                adapter=mock.Mock(), exchange_root=Path(temporary),
                container_image=IMAGE,
                artifact_reader=store)
            reference = {"kind": "artifact", "id": artifact.id,
                         "sha256": "sha256:" + artifact.sha256}
            grants, staged = executor._stage_artifact_references(
                {"edge_spec_ref": reference}, attempt_id="attempt-1")
            self.assertEqual([row["role"] for row in grants], ["rbfe.edge_spec"])
            self.assertEqual((Path(temporary) / grants[0]["path"]).read_bytes(),
                             b'{"governed":true}')
            self.assertEqual(staged.name, "attempt-1")
            with self.assertRaises(failures.DiracInvalidParameters):
                executor._stage_artifact_references(
                    {"edge_spec_ref": {**reference, "sha256": "sha256:" + "0" * 64}},
                    attempt_id="attempt-2")

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
                container_image=IMAGE,
                poll_seconds=.01)
            spec = MethodCatalog.load().get("ml.motif.mesh.predict")
            ctx = InvocationContext(
                method_id=spec.method_id, version="sha256:" + "b" * 64,
                execution_digest="sha256:" + "c" * 64,
                execution_adapter="kubernetes",
                execution_identity=gpu_identity(spec.method_id),
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
                container_image=IMAGE)
            spec = MethodCatalog.load().get("physics.motif.openfe_edge")
            request = executor._request(
                InvocationContext(
                    method_id=spec.method_id, version="sha256:" + "b" * 64,
                    execution_digest="sha256:" + "c" * 64,
                    execution_adapter="kubernetes",
                    execution_identity=gpu_identity(spec.method_id),
                    budget_seconds=30,
                    job_id="00000000-0000-4000-8000-000000000001", spec=spec),
                {}, attempt_id="00000000-0000-4000-8000-000000000002",
                input_id="sha256:" + "d" * 64, input_sha="sha256:" + "e" * 64,
                budget=30, now=__import__('datetime').datetime.now(
                    __import__('datetime').timezone.utc))
            self.assertEqual(request["resource_request"]["cpu_cores"], 20)

    def test_openfe_remote_manifest_receives_api_owned_generation_attestation(self):
        with tempfile.TemporaryDirectory() as temporary:
            executor = KubernetesInvocationExecutor(
                adapter=mock.Mock(), exchange_root=Path(temporary),
                container_image=IMAGE)
            spec = MethodCatalog.load().get("physics.motif.openfe_edge")
            context = InvocationContext(
                method_id=spec.method_id, spec=spec,
                execution_adapter="kubernetes",
                server_attestations={}, actor={"kind": "human", "id": "chemist-1"})
            context.server_attestations["rbfe_campaign_generation"] = {
                "verdict": "CONFIRMED", "sealed": True}
            sentinel = object()
            with mock.patch.object(executor, "_execute_remote",
                                   return_value=sentinel) as remote:
                self.assertIs(executor.execute(mock.Mock(), {"edge": "payload"},
                                               context), sentinel)
            self.assertEqual(context.server_attestations, {
                "rbfe_campaign_generation": {
                    "verdict": "CONFIRMED", "sealed": True}})
            remote.assert_called_once()

    def test_cpu_handler_stays_inside_controller_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            adapter = mock.Mock()
            executor = KubernetesInvocationExecutor(
                adapter=adapter, exchange_root=Path(temporary),
                container_image=IMAGE)
            spec = MethodCatalog.load().get("design.motif.acquire")
            ctx = InvocationContext(
                method_id=spec.method_id, spec=spec,
                execution_adapter="local_cpu")
            sentinel = object()
            handler = mock.Mock(return_value=sentinel)
            self.assertIs(executor.execute(handler, {"x": 1}, ctx), sentinel)
            adapter.submit.assert_not_called()
            self.assertEqual(executor.execution_adapter_for(spec), "local_cpu")

    def test_cpu_qm_route_reserves_the_scf_admission_slot(self):
        with tempfile.TemporaryDirectory() as temporary:
            broker = mock.Mock()
            broker.acquire.return_value = SimpleNamespace(
                lease_id="lease", fencing_token=1)
            executor = KubernetesInvocationExecutor(
                adapter=mock.Mock(), exchange_root=Path(temporary),
                container_image=IMAGE, resource_broker=broker)
            spec = MethodCatalog.load().get("fields.qm.homo")
            ctx = InvocationContext(
                method_id=spec.method_id, spec=spec, job_id="job",
                execution_adapter="local_cpu", budget_seconds=30)
            sentinel = object()

            self.assertIs(
                executor.execute(mock.Mock(return_value=sentinel), {}, ctx),
                sentinel)

            resources = broker.acquire.call_args.args[2]
            self.assertEqual(resources["scf_slots"], 1)
            broker.release.assert_called_once_with("lease", 1)
            executor.shutdown()

    def test_gpu_and_cpu_contracts_see_their_actual_hybrid_adapter(self):
        with tempfile.TemporaryDirectory() as temporary:
            executor = KubernetesInvocationExecutor(
                adapter=mock.Mock(), exchange_root=Path(temporary),
                container_image=IMAGE)
            service = InvocationService(
                MethodCatalog.load(), ledger=MemoryJobStore(), executor=executor)
            cpu = service.catalog.get("physics.motif.rbfe_campaign_prepare")
            gpu = service.catalog.get("physics.motif.openfe_edge")

            self.assertEqual(service._executor_adapter(cpu), "local_cpu")
            self.assertEqual(service._executor_adapter(gpu), "kubernetes")
            service._require_executor_compatibility(cpu)
            service._require_executor_compatibility(gpu)

    def test_service_reports_remote_cancel_as_accepted_not_not_interruptible(self):
        ledger = MemoryJobStore()
        executor = KubernetesInvocationExecutor(
            adapter=object(), exchange_root=Path(tempfile.mkdtemp()),
            container_image=IMAGE)
        service = InvocationService(MethodCatalog.load(), ledger=ledger,
                                    executor=executor)
        actor = {"kind": "human", "id": "chemist-1"}
        job_id, _ = ledger.open(method_row_id="physics.motif.openfe_edge",
                                input_sha256=b"x" * 32,
                                params={}, queued=True,
                                actor_kind=actor["kind"], actor_id=actor["id"])
        token = __import__('execution_control.protocol', fromlist=['CancellationToken']).CancellationToken()
        service._cancellation_tokens[job_id] = token
        service._job_execution_adapters[job_id] = "kubernetes"
        row = service.cancel_job(job_id, actor=actor)
        self.assertTrue(row["cancel"]["accepted"])
        self.assertEqual(row["cancel"]["capability"], "cooperative+remote-hard")
        self.assertTrue(token.requested)
        executor.shutdown()

    def test_cpu_queued_only_job_does_not_inherit_remote_hard_cancel_claim(self):
        ledger = MemoryJobStore()
        executor = KubernetesInvocationExecutor(
            adapter=object(), exchange_root=Path(tempfile.mkdtemp()),
            container_image=IMAGE)
        service = InvocationService(MethodCatalog.load(), ledger=ledger,
                                    executor=executor)
        actor = {"kind": "human", "id": "chemist-1"}
        job_id, _ = ledger.open(
            method_row_id="physics.motif.rbfe_campaign_prepare",
            input_sha256=b"z" * 32, params={}, queued=True,
            actor_kind=actor["kind"], actor_id=actor["id"])
        token = __import__(
            'execution_control.protocol',
            fromlist=['CancellationToken']).CancellationToken()
        service._cancellation_tokens[job_id] = token
        service._job_execution_adapters[job_id] = "local_cpu"
        row = service.cancel_job(job_id, actor=actor)
        self.assertNotEqual(
            row["cancel"]["capability"], "cooperative+remote-hard")
        self.assertFalse(token.requested)
        executor.shutdown()

    def test_cpu_cooperative_job_reports_only_cooperative_cancel(self):
        ledger = MemoryJobStore()
        executor = KubernetesInvocationExecutor(
            adapter=object(), exchange_root=Path(tempfile.mkdtemp()),
            container_image=IMAGE)
        service = InvocationService(MethodCatalog.load(), ledger=ledger,
                                    executor=executor)
        actor = {"kind": "human", "id": "chemist-1"}
        job_id, _ = ledger.open(
            method_row_id="data.motif.snapshot",
            input_sha256=b"c" * 32, params={}, queued=True,
            actor_kind=actor["kind"], actor_id=actor["id"])
        token = __import__(
            'execution_control.protocol',
            fromlist=['CancellationToken']).CancellationToken()
        service._cancellation_tokens[job_id] = token
        service._job_execution_adapters[job_id] = "local_cpu"

        row = service.cancel_job(job_id, actor=actor)

        self.assertEqual(row["cancel"]["capability"], "cooperative")
        self.assertNotEqual(
            row["cancel"]["capability"], "cooperative+remote-hard")
        self.assertTrue(token.requested)
        executor.shutdown()

    def test_capabilities_refuse_adapter_name_without_protocol_and_health(self):
        with tempfile.TemporaryDirectory() as temporary:
            executor = KubernetesInvocationExecutor(
                adapter=object(), exchange_root=Path(temporary),
                container_image=IMAGE)
            capability = executor.capabilities()
            self.assertFalse(capability["protocol_valid"])
            self.assertFalse(capability["gpu_execution"])
            service = InvocationService(MethodCatalog.load(), executor=executor)
            self.assertFalse(service.capabilities()["executor"]["gpu_execution"])
            executor.shutdown()

    def test_capabilities_positive_control_requires_verified_health_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            executor = KubernetesInvocationExecutor(
                adapter=FakeAdapter(Path(temporary)),
                exchange_root=Path(temporary), container_image=IMAGE)
            capability = executor.capabilities()
            self.assertTrue(capability["protocol_valid"])
            self.assertTrue(capability["scheduler_healthy"])
            self.assertTrue(capability["gpu_execution"])
            self.assertEqual(capability["gpu"]["arch"], "blackwell")
            executor.adapter.allowed_images = {
                "registry/other@sha256:" + "f" * 64}
            self.assertFalse(executor.capabilities()["gpu_execution"])
            executor.shutdown()

    def test_capabilities_reject_profile_with_non_enforcing_node_selector(self):
        with tempfile.TemporaryDirectory() as temporary:
            adapter = FakeAdapter(Path(temporary))
            adapter.health = lambda: {"ready": True, "gpu": {
                "verified": True,
                "arch": "blackwell",
                "numeric_mode": "fp32",
                "memory_bytes": 16 << 30,
                "node_selector": {"attacker": "unconstrained"},
            }}
            executor = KubernetesInvocationExecutor(
                adapter=adapter, exchange_root=Path(temporary),
                container_image=IMAGE)

            capability = executor.capabilities()

            self.assertTrue(capability["protocol_valid"])
            self.assertTrue(capability["scheduler_healthy"])
            self.assertFalse(capability["gpu_execution"])
            self.assertFalse(capability["gpu"]["verified"])
            executor.shutdown()

    def test_capabilities_reject_truthy_strings_and_boolean_capacity(self):
        with tempfile.TemporaryDirectory() as temporary:
            adapter = FakeAdapter(Path(temporary))
            adapter.health = lambda: {"ready": "yes", "gpu": {
                "verified": "yes",
                "arch": "blackwell",
                "numeric_mode": "fp32",
                "memory_bytes": True,
                "node_selector": {
                    "dirac.io/gpu-arch": "blackwell",
                    "dirac.io/gpu-numeric-mode": "fp32",
                    "dirac.io/gpu-memory-bytes": "True",
                },
            }}
            executor = KubernetesInvocationExecutor(
                adapter=adapter, exchange_root=Path(temporary),
                container_image=IMAGE)

            capability = executor.capabilities()

            self.assertFalse(capability["scheduler_healthy"])
            self.assertFalse(capability["gpu_execution"])
            executor.shutdown()

    def test_capabilities_reject_non_cuda_arch_for_fixed_nvidia_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            adapter = FakeAdapter(Path(temporary))
            adapter.health = lambda: {"ready": True, "gpu": {
                "verified": True,
                "arch": "rocm",
                "numeric_mode": "fp32",
                "memory_bytes": 16 << 30,
                "node_selector": {
                    "dirac.io/gpu-arch": "rocm",
                    "dirac.io/gpu-numeric-mode": "fp32",
                    "dirac.io/gpu-memory-bytes": str(16 << 30),
                },
            }}
            executor = KubernetesInvocationExecutor(
                adapter=adapter, exchange_root=Path(temporary),
                container_image=IMAGE)

            self.assertFalse(executor.capabilities()["gpu_execution"])
            executor.shutdown()

    def test_success_without_matching_worker_gpu_attestation_is_rejected(self):
        request = {
            "job_id": "job", "attempt_id": "attempt", "fencing_token": 1,
            "execution_digest": "sha256:" + "1" * 64,
            "method_id": "physics.motif.openfe_edge",
            "resource_request": {"gpus": 1, "gpu_arch": ["blackwell"]},
            "determinism": {"numeric_mode": "fp32"},
        }
        result = {
            "schema_version": "1.0", "ok": True,
            **{key: request[key] for key in (
                "job_id", "attempt_id", "fencing_token", "execution_digest",
                "method_id")},
            "worker_attestation": {"gpu": {
                "available": True, "architecture": "hopper",
                "numeric_mode": "fp32", "compute_capability": "9.0",
                "memory_bytes": 16 << 30,
            }},
        }
        with self.assertRaisesRegex(failures.DiracInternal, "attestation"):
            KubernetesInvocationExecutor._verify_result(result, request)

    def test_success_with_capability_architecture_contradiction_is_rejected(self):
        request = {
            "job_id": "job", "attempt_id": "attempt", "fencing_token": 1,
            "execution_digest": "sha256:" + "1" * 64,
            "method_id": "physics.motif.openfe_edge",
            "resource_request": {"gpus": 1, "gpu_arch": ["blackwell"],
                                 "gpu_memory_bytes_min": 8 << 30},
            "determinism": {"numeric_mode": "fp32"},
        }
        result = {
            "schema_version": "1.0", "ok": True,
            **{key: request[key] for key in (
                "job_id", "attempt_id", "fencing_token", "execution_digest",
                "method_id")},
            "worker_attestation": {"gpu": {
                "available": True,
                "architecture": "blackwell",
                # 9.0 is Hopper; a self-contradictory attestation is not proof.
                "compute_capability": "9.0",
                "numeric_mode": "fp32",
                "memory_bytes": 16 << 30,
            }},
        }

        with self.assertRaisesRegex(failures.DiracInternal, "attestation"):
            KubernetesInvocationExecutor._verify_result(result, request)

        result["worker_attestation"]["gpu"].update(
            architecture="blackwell", compute_capability="12.0",
            available="yes", memory_bytes=str(16 << 30))
        with self.assertRaisesRegex(failures.DiracInternal, "attestation"):
            KubernetesInvocationExecutor._verify_result(result, request)

    def test_worker_result_requires_exact_schema_and_boolean_verdict(self):
        request = {
            "job_id": "job", "attempt_id": "attempt", "fencing_token": 1,
            "execution_digest": "sha256:" + "1" * 64,
            "method_id": "design.motif.acquire",
            "resource_request": {"gpus": 0},
            "determinism": {"numeric_mode": "fp32"},
        }
        identity = {key: request[key] for key in (
            "job_id", "attempt_id", "fencing_token", "execution_digest",
            "method_id")}

        with self.assertRaisesRegex(failures.DiracInternal, "schema version"):
            KubernetesInvocationExecutor._verify_result(
                {"schema_version": "garbage", "ok": False, **identity}, request)
        with self.assertRaisesRegex(failures.DiracInternal, "JSON boolean"):
            KubernetesInvocationExecutor._verify_result(
                {"schema_version": "1.0", "ok": "false", **identity}, request)

    def test_worker_observes_compute_capability_and_rejects_architecture_mismatch(self):
        cuda = SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 1,
            get_device_capability=lambda _index: (12, 0),
            get_device_name=lambda _index: "fake device name is not provenance",
            get_device_properties=lambda _index: SimpleNamespace(
                total_memory=16 << 30),
        )
        torch = SimpleNamespace(
            __version__="test", version=SimpleNamespace(cuda="test"), cuda=cuda,
            get_default_dtype=lambda: "torch.float32",
            backends=SimpleNamespace(
                cuda=SimpleNamespace(
                    matmul=SimpleNamespace(allow_tf32=False)),
                cudnn=SimpleNamespace(allow_tf32=False)))
        request = {
            "resource_request": {"gpus": 1, "gpu_arch": ["blackwell"],
                                 "gpu_memory_bytes_min": 8 << 30},
            "determinism": {"numeric_mode": "fp32"},
        }
        with mock.patch.dict("sys.modules", {"torch": torch}):
            evidence = motif_worker._gpu_evidence(request)
            self.assertEqual(evidence["architecture"], "blackwell")
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                motif_worker._gpu_evidence({
                    **request,
                    "resource_request": {"gpus": 1, "gpu_arch": ["hopper"],
                                         "gpu_memory_bytes_min": 8 << 30},
                })

    def test_worker_configures_tf32_paths_to_match_the_admitted_numeric_mode(self):
        cuda = SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 1,
            get_device_capability=lambda _index: (12, 0),
            get_device_name=lambda _index: "fake",
            get_device_properties=lambda _index: SimpleNamespace(
                total_memory=16 << 30),
        )
        torch = SimpleNamespace(
            __version__="test", version=SimpleNamespace(cuda="test"), cuda=cuda,
            get_default_dtype=lambda: "torch.float32",
            backends=SimpleNamespace(
                cuda=SimpleNamespace(
                    matmul=SimpleNamespace(allow_tf32=False)),
                cudnn=SimpleNamespace(allow_tf32=False)))
        request = {
            "resource_request": {"gpus": 1, "gpu_arch": ["blackwell"],
                                 "gpu_memory_bytes_min": 8 << 30},
            "determinism": {"numeric_mode": "tf32"},
        }

        with mock.patch.dict("sys.modules", {"torch": torch}):
            evidence = motif_worker._gpu_evidence(request)
        self.assertTrue(evidence["matmul_allow_tf32"])
        self.assertTrue(evidence["cudnn_allow_tf32"])

    def test_unauthorized_cancel_cannot_touch_owner_cancellation_token(self):
        ledger = MemoryJobStore()
        executor = mock.Mock(
            kind="remote", adapter_kind="kubernetes", supports_submission=True,
            cancellation_capability="cooperative+remote-hard")
        service = InvocationService(MethodCatalog.load(), ledger=ledger,
                                    executor=executor)
        owner = {"kind": "human", "id": "chemist-owner"}
        attacker = {"kind": "human", "id": "chemist-other"}
        job_id, _ = ledger.open(
            method_row_id="method", input_sha256=b"y" * 32, params={},
            queued=True, actor_kind=owner["kind"], actor_id=owner["id"])
        token = __import__(
            'execution_control.protocol',
            fromlist=['CancellationToken']).CancellationToken()
        service._cancellation_tokens[job_id] = token

        with self.assertRaises(failures.DiracNotFound):
            service.cancel_job(job_id, actor=attacker)

        self.assertFalse(token.requested)
        self.assertEqual(
            service.get_job(job_id, actor=owner)["state"], "queued")


if __name__ == "__main__":
    unittest.main()
