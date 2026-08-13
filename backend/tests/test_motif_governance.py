from __future__ import annotations

import copy
import importlib.util
import unittest

import failures
from catalog import MethodCatalog
from dirac_app.dispatcher import CommandDispatcher
from invocation import InvocationService
from motif.governance import MemoryMotifGovernanceStore, with_semantic_digest
from tests.test_motif_contracts import DESIGN_BRIEF, ENDPOINT_DEFINITION, MEASUREMENT


class _Kernel:
    def __init__(self) -> None:
        self.motif_governance = MemoryMotifGovernanceStore()
        self.command_traces = None


class MotifGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kernel = _Kernel()
        self.dispatcher = CommandDispatcher(self.kernel)
        self.actor = {"kind": "human", "id": "chemist-1"}

    def execute(self, command: str, payload: dict) -> dict:
        return self.dispatcher.execute(command, payload, actor=self.actor)

    def prepare_objective_dependencies(self) -> None:
        self.execute("endpoint.register", {
            "definition": with_semantic_digest(ENDPOINT_DEFINITION)})
        policies = dict(DESIGN_BRIEF["policy_releases"])
        policies["identity_gate"] = DESIGN_BRIEF["chemistry_constraints"][
            "identity_policy_release_id"]
        for kind, identifier in policies.items():
            release = with_semantic_digest({
                "schema_version": "1.0", "policy_release_id": identifier,
                "policy_kind": kind, "name": f"test-{kind}", "version": "1",
                "lifecycle": "candidate", "spec": {"fixture": True},
                "created_by": self.actor, "created_at": "2026-08-12T00:00:00Z",
            })
            result = self.execute("policy.release.register", {"release": release})
            self.assertTrue(result["ok"])

    def test_endpoint_registration_is_content_idempotent(self):
        endpoint = with_semantic_digest(ENDPOINT_DEFINITION)
        first = self.execute("endpoint.register", {"definition": endpoint})
        second = self.execute("endpoint.register", {"definition": endpoint})
        self.assertTrue(first["ok"])
        self.assertTrue(first["data"]["created"])
        self.assertFalse(second["data"]["created"])
        self.assertEqual(first["data"]["endpoint_definition_id"],
                         second["data"]["endpoint_definition_id"])

    def test_objective_rejects_tampered_digest_and_actor(self):
        self.prepare_objective_dependencies()
        objective = with_semantic_digest(DESIGN_BRIEF)
        objective["risk_policy"]["exploration_fraction"] = 0.9
        tampered = self.execute("objective.save", {"objective": objective})
        self.assertFalse(tampered["ok"])
        self.assertEqual(tampered["error"]["code"], "INVALID_PARAMETERS")

        objective = with_semantic_digest(DESIGN_BRIEF)
        wrong_actor = self.dispatcher.execute(
            "objective.save", {"objective": objective},
            actor={"kind": "agent", "id": "someone-else"})
        self.assertFalse(wrong_actor["ok"])
        self.assertEqual(wrong_actor["error"]["code"], "INVALID_PARAMETERS")

    def test_missing_measurement_remains_missing_and_deduplicates(self):
        self.execute("endpoint.register", {
            "definition": with_semantic_digest(ENDPOINT_DEFINITION)})
        first = self.execute("result.ingest", {"measurements": [MEASUREMENT]})
        second = self.execute("result.ingest", {"measurements": [MEASUREMENT]})
        self.assertTrue(first["ok"])
        self.assertEqual(first["data"]["created_count"], 1)
        self.assertEqual(second["data"]["created_count"], 0)
        stored = self.kernel.motif_governance.measurements["m-1"]
        self.assertNotIn("value", MEASUREMENT["quantity"])
        self.assertEqual(stored["digest"], first["data"]["measurements"][0]["digest"])

    def test_measurement_identity_collision_is_rejected(self):
        self.execute("endpoint.register", {
            "definition": with_semantic_digest(ENDPOINT_DEFINITION)})
        self.execute("result.ingest", {"measurements": [MEASUREMENT]})
        changed = copy.deepcopy(MEASUREMENT)
        changed["missing_reason"] = "assay_failed"
        conflict = self.execute("result.ingest", {"measurements": [changed]})
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["error"]["code"], "INVALID_PARAMETERS")

    def test_dataset_completion_registers_release_before_success(self):
        endpoint = with_semantic_digest(ENDPOINT_DEFINITION)
        self.execute("endpoint.register", {"definition": endpoint})
        identity_id = "00000000-0000-4000-8000-000000000003"
        policy = with_semantic_digest({
            "schema_version": "1.0", "policy_release_id": identity_id,
            "policy_kind": "identity_gate", "name": "test-identity", "version": "1",
            "lifecycle": "candidate", "spec": {}, "created_by": self.actor,
            "created_at": "2026-08-12T00:00:00Z",
        })
        self.execute("policy.release.register", {"release": policy})
        service = InvocationService(MethodCatalog.load(),
                                    motif_governance=self.kernel.motif_governance)
        payload = {
            "selection_query": "fixture:v1",
            "endpoint_definitions": [{"endpoint_key": "ic50", "version": "1",
                                      "canonical_unit": "nM",
                                      "measurement_type": "concentration"}],
            "rows": [
                {"measurement_id": "m-1", "compound_id": "c-1", "smiles": "CCO",
                 "endpoint_key": "ic50", "protocol_id": "p-1", "unit": "nM",
                 "measurement_type": "concentration", "value": 1.0,
                 "qualifier": "equal", "split": "train"},
                {"measurement_id": "m-2", "compound_id": "c-2", "smiles": "CCN",
                 "endpoint_key": "ic50", "protocol_id": "p-1", "unit": "nM",
                 "measurement_type": "concentration", "value": 2.0,
                 "qualifier": "equal", "split": "train"},
            ],
            "registration": {
                "program_ref": {"kind": "program", "id": "00000000-0000-4000-8000-000000000010"},
                "campaign_ref": {"kind": "campaign", "id": "00000000-0000-4000-8000-000000000011"},
                "identity_policy_release_id": identity_id, "data_classification": "internal",
            },
        }
        result = service.invoke("data.motif.snapshot", payload, actor=self.actor)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["artifacts"]), 4)
        self.assertEqual(result["data"]["dataset_snapshot"]["status"], "valid")
        again = service.invoke("data.motif.snapshot", payload, actor=self.actor)
        self.assertFalse(again["data"]["dataset_snapshot"]["created"])

        if importlib.util.find_spec("numpy") and importlib.util.find_spec("rdkit"):
            train = service.invoke("ml.motif.train", {
                "endpoint_key": "ic50", "n_bits": 128, "rows": payload["rows"],
                "registration": {
                    "dataset_snapshot_ref": result["data"]["dataset_snapshot"]["ref"],
                    "model_object_id": "motif-test-baseline", "release_name": "candidate-1",
                    "source_commit": "a" * 40,
                    "intended_use": {"fixture": True},
                    "prohibited_use": {"clinical": True},
                    "known_limitations": {"fixture": True},
                },
            }, actor=self.actor)
            self.assertTrue(train["ok"], train)
            self.assertEqual(len(train["artifacts"]), 3)
            self.assertTrue(train["data"]["model_release"]["created"])


if __name__ == "__main__":
    unittest.main()
