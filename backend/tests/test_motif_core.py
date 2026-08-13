from __future__ import annotations

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import failures
from catalog import MethodCatalog
from execution_control.admission import ResourceInventory, admit
from execution_control.retry import classify_retry
from execution_control.scratch import AttemptScratch
from executors.local_process import LocalProcessAdapter
from executors.local_gpu import LocalGpuAdapter
from invocation import InvocationService
from motif.acquisition import rank_portfolio
from motif.datasets import create_snapshot
from motif.run_compiler import compile_run_plan, verify_run_plan
from backend.tests.test_motif_contracts import EXECUTION_REQUEST


UUID = lambda n: f"00000000-0000-4000-8000-{n:012d}"


class MotifCoreTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("numpy") and importlib.util.find_spec("rdkit"),
                         "scientific baseline requires numpy and RDKit")
    def test_predictor_baseline_keeps_censored_labels_and_reports_domain(self):
        from motif.models import calibrate, predict, train_baselines
        rows = [
            {"compound_id": "c1", "smiles": "CCO", "value": 1.0,
             "qualifier": "equal", "split": "train"},
            {"compound_id": "c2", "smiles": "CCN", "value": 2.0,
             "qualifier": "equal", "split": "train"},
            {"compound_id": "c3", "smiles": "CCC", "value": 1.5,
             "qualifier": "equal", "split": "test"},
            {"compound_id": "c4", "smiles": "c1ccccc1", "value": 10.0,
             "qualifier": "greater_than", "split": "train"},
        ]
        checkpoint, report = train_baselines(rows, endpoint_key="potency", n_bits=128)
        self.assertEqual(checkpoint["training"]["censored_excluded_count"], 1)
        self.assertIn("nearest_neighbor_metrics", report)
        release = calibrate([1.0, 2.0], [1.2, 1.7], nominal_coverage=.8)
        outputs = predict(checkpoint, ["CCO", "[Na+].[Cl-]"], calibration=release)
        self.assertIn(outputs[0]["applicability_domain"],
                      {"in_domain", "borderline", "out_of_domain"})
        self.assertIn("interval", outputs[0])

    @unittest.skipUnless(importlib.util.find_spec("rdkit"),
                         "proposal generation requires RDKit")
    def test_proposals_have_transform_or_reaction_lineage_and_validate(self):
        import json
        from contracts.validation import violations
        from motif.proposals import local_edits, reaction_enumerate
        common = dict(
            generator_release_id=UUID(20), strategy_release_id=UUID(21),
            identity_policy_release_id=UUID(22), root_seed=1729,
            constraints={"max_heavy_atoms": 50, "charge_range": [-2, 2]},
            created_at="2026-08-12T00:00:00Z")
        edits = local_edits(
            [{"id": "benzene", "smiles": "c1ccccc1"}],
            transforms=[{"transform_id": "aryl_f", "version": "1",
                         "reaction_smarts": "[cH:1]>>[c:1]F",
                         "description": "aryl fluorination"}], **common)
        reactions = reaction_enumerate(
            [{"id": "acid", "smiles": "CC(=O)O"}, {"id": "amine", "smiles": "CN"}],
            templates=[{"template_id": "amide", "version": "1",
                        "reaction_smarts": "[C:1](=[O:2])O.[N:3]>>[C:1](=[O:2])[N:3]"}],
            **common)
        self.assertEqual(len(edits), 1)
        self.assertEqual(len(reactions), 1)
        self.assertIn("edits", edits[0]["generation_trace"])
        self.assertIn("reaction", reactions[0]["generation_trace"])
        schema = json.loads((Path(__file__).resolve().parents[2]
                             / "contracts/domain/motif/proposal.schema.json").read_text())
        for proposal in edits + reactions:
            self.assertEqual(violations(schema, proposal), [])

    def test_snapshot_is_stable_and_detects_cross_split_leakage(self):
        endpoint = {"endpoint_key": "ic50", "canonical_unit": "nM",
                    "measurement_type": "concentration"}
        rows = [
            {"measurement_id": "m2", "compound_id": "c1", "endpoint_key": "ic50",
             "protocol_id": "p1", "unit": "nM", "measurement_type": "concentration",
             "value": 20, "split": "test", "series_id": "s1"},
            {"measurement_id": "m1", "compound_id": "c1", "endpoint_key": "ic50",
             "protocol_id": "p1", "unit": "nM", "measurement_type": "concentration",
             "value": 10, "split": "train", "series_id": "s1"},
        ]
        first, first_bytes = create_snapshot(rows, selection_query="frozen:v1",
                                             endpoint_definitions=[endpoint])
        second, second_bytes = create_snapshot(reversed(rows), selection_query="frozen:v1",
                                               endpoint_definitions=[endpoint])
        self.assertEqual(first, second)
        self.assertEqual(first_bytes, second_bytes)
        self.assertFalse(first["leakage"]["valid"])
        self.assertEqual(first["leakage"]["counts"]["compound_id"], 1)
        with self.assertRaisesRegex(ValueError, "unit"):
            create_snapshot([{**rows[0], "unit": "uM"}], selection_query="q",
                            endpoint_definitions=[endpoint])

    def test_pareto_partition_is_exhaustive_deterministic_and_constraint_exact(self):
        candidates = [
            self._candidate(1, potency=9, clearance=3, route=True, cost=10),
            self._candidate(2, potency=8, clearance=1, route=True, cost=5),
            self._candidate(3, potency=10, clearance=2, route=False, cost=1),
            self._candidate(4, potency=4, clearance=8, route=True, cost=20),
        ]
        kwargs = dict(
            objectives=[{"key": "potency", "direction": "maximize"},
                        {"key": "clearance", "direction": "minimize"}],
            hard_constraints=[{"key": "route", "equals": True}], capacity=1)
        first = rank_portfolio(candidates, **kwargs)
        second = rank_portfolio(reversed(candidates), **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(sum(map(len, first.values())), 4)
        self.assertEqual(first["refused"][0]["proposal_id"], UUID(3))
        self.assertEqual(first["selected"][0]["proposal_id"], UUID(2))

    def test_methods_execute_through_existing_invocation_service(self):
        service = InvocationService(MethodCatalog.load())
        result = service.invoke("design.motif.acquire", {
            "candidates": [self._candidate(1, potency=9, clearance=3,
                                             route=True, cost=10)],
            "objectives": [{"key": "potency", "direction": "maximize"}],
            "hard_constraints": [{"key": "route", "equals": True}],
            "capacity": 1,
        })
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["counts"]["selected"], 1)
        self.assertEqual(result["artifacts"][0]["role"], "portfolio.ranking")
        self.assertRegex(result["meta"]["execution_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(result["meta"]["execution_identity"]["method_id"],
                         "design.motif.acquire")

    def test_gpu_mesh_cannot_escape_to_inline_or_thread_executor(self):
        payload = {
            "endpoint_key": "potency",
            "rows": [
                {"compound_id": "c1", "smiles": "CCO", "endpoint_key": "potency",
                 "value": 1.0, "qualifier": "equal", "split": "train"},
                {"compound_id": "c2", "smiles": "CCN", "endpoint_key": "potency",
                 "value": 2.0, "qualifier": "equal", "split": "train"},
                {"compound_id": "c3", "smiles": "CCC", "endpoint_key": "potency",
                 "value": 1.5, "qualifier": "equal", "split": "test"},
            ],
            "registration": {
                "dataset_snapshot_ref": {"kind": "dataset", "id": UUID(31)},
                "model_object_id": "mesh-test",
                "release_name": "mesh-test-v1",
                "source_commit": "0" * 40,
                "scientific_lifecycle": "technical_smoke",
                "intended_use": {}, "prohibited_use": {}, "known_limitations": {},
            },
        }
        inline = InvocationService(MethodCatalog.load())
        result = inline.invoke("ml.motif.mesh.train", payload)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "UNSUPPORTED")
        self.assertEqual(result["error"]["details"]["executor_adapter"], "inline")
        self.assertFalse(inline.capabilities()["executor"]["gpu_execution"])

        thread = mock.Mock(kind="thread", supports_submission=True)
        service = InvocationService(MethodCatalog.load(), executor=thread)
        with self.assertRaises(failures.DiracUnsupported):
            service.submit("ml.motif.mesh.train", payload)
        thread.submit.assert_not_called()

    def test_production_invocation_refuses_partial_implicit_identity(self):
        service = InvocationService(MethodCatalog.load(), production_execution=True)
        result = service.invoke("design.motif.acquire", {
            "candidates": [self._candidate(1, potency=9, clearance=3,
                                             route=True, cost=10)],
            "objectives": [{"key": "potency", "direction": "maximize"}],
            "capacity": 1,
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INTERNAL")

    def test_run_plan_and_retry_are_replayable(self):
        source = {
            "run_id": UUID(1), "root_seed": 1729, "objective_spec_id": UUID(2),
            "program_snapshot_id": UUID(3), "policies": {"fidelity": UUID(4)},
            "resource_envelope": {"gpu_hours": 1},
            "approval_gates": ["portfolio_review"],
        }
        self.assertEqual(compile_run_plan(source), compile_run_plan(copy.deepcopy(source)))
        self.assertTrue(verify_run_plan(compile_run_plan(source)))
        policy = {"max_attempts": 3, "retryable_codes": ["WORKER_LOST"],
                  "backoff": {"kind": "exponential", "initial_seconds": 2,
                              "max_seconds": 30, "jitter_fraction": .1},
                  "preserve_seed": True, "resume_from_checkpoint": True}
        one = classify_retry(code="WORKER_LOST", attempt=1, policy=policy,
                             execution_digest="sha256:" + "a" * 64)
        two = classify_retry(code="WORKER_LOST", attempt=1, policy=policy,
                             execution_digest="sha256:" + "a" * 64)
        self.assertEqual(one, two)
        self.assertFalse(classify_retry(code="INVALID_PARAMETERS", attempt=1,
                                        policy=policy, execution_digest="x").retry)

    def test_admission_refuses_unhealthy_gpu_and_adapter_ignores_request_entrypoint(self):
        request = copy.deepcopy(EXECUTION_REQUEST)
        request["resource_request"].update({"gpus": 1, "gpu_arch": ["blackwell"],
                                             "gpu_memory_bytes_min": 1})
        request["placement"]["backend"] = "local_gpu"
        inventory = ResourceInventory(24, 32 << 30, 100 << 30, gpus=1,
                                      gpu_arch="blackwell", gpu_memory_bytes_available=16 << 30,
                                      gpu_healthy=False)
        self.assertEqual(admit(request, inventory).code, "GPU_UNHEALTHY")

        cpu_request = copy.deepcopy(EXECUTION_REQUEST)
        cpu_request["entrypoint"] = ["malicious", "callable"]
        inventory = ResourceInventory(24, 32 << 30, 100 << 30)
        with tempfile.TemporaryDirectory() as directory:
            adapter = LocalProcessAdapter(worker_command=["fixed-worker"],
                                          scratch_root=Path(directory), inventory=inventory)
            fake = mock.Mock()
            fake.pid = 1234
            fake.poll.return_value = 0
            with mock.patch("executors.local_process.subprocess.Popen", return_value=fake) as popen:
                status = adapter.submit(cpu_request)
            self.assertEqual(status.state, "succeeded")
            self.assertEqual(popen.call_args.args[0], ("fixed-worker",))

    def test_scratch_quota_and_terminal_gpu_allocation_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            scratch = AttemptScratch.create(Path(directory) / "scratch", "attempt-1", 4)
            (scratch.root / "value").write_bytes(b"1234")
            scratch.check()
            with self.assertRaisesRegex(OSError, "quota"):
                scratch.check(1)
            scratch.cleanup()
            self.assertFalse(scratch.root.exists())

            request = copy.deepcopy(EXECUTION_REQUEST)
            request["placement"]["backend"] = "local_gpu"
            request["resource_request"].update({
                "gpus": 1, "gpu_arch": ["blackwell"], "gpu_memory_bytes_min": 1})
            inventory = ResourceInventory(24, 32 << 30, 100 << 30, gpus=1,
                                          gpu_arch="blackwell",
                                          gpu_memory_bytes_available=16 << 30,
                                          gpu_healthy=True)
            adapter = LocalGpuAdapter(worker_command=["fixed-worker"],
                                      scratch_root=Path(directory) / "gpu",
                                      inventory=inventory)
            fake = mock.Mock(pid=1234)
            fake.poll.return_value = 0
            with mock.patch("executors.local_process.subprocess.Popen", return_value=fake):
                self.assertEqual(adapter.submit(request).state, "succeeded")
                self.assertEqual(adapter.submit(request).state, "succeeded")

    @staticmethod
    def _candidate(index: int, *, potency: float, clearance: float,
                   route: bool, cost: float) -> dict:
        return {
            "proposal_id": UUID(index),
            "objectives": {"potency": potency, "clearance": clearance},
            "constraints": {"route": route},
            "components": {"feasibility": 1.0, "pareto_improvement": None,
                           "information_value": 0.2, "diversity": 0.5,
                           "cost": cost, "failure_risk": 0.1,
                           "missing_evidence": 0.0},
            "evidence_artifact_ids": [],
        }


if __name__ == "__main__":
    unittest.main()
