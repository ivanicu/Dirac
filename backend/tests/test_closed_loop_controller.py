from __future__ import annotations

import unittest

import failures
from motif.closed_loop import ClosedLoopController, _digest


MEASUREMENTS = [{
    "measurement_id": "m-1", "compound": {"kind": "compound", "id": "c-1"},
    "endpoint": {"id": "activity", "version": "1"},
    "protocol": {"kind": "protocol", "id": "p-1"},
    "qualifier": "equal", "quantity": {"value": 1.0, "unit": "nM"},
    "qc": {"status": "pass", "reason_codes": []},
}]


def specification():
    return {
        "request_key": "loop-1",
        "program_ref": {"kind": "program", "id": "program-1"},
        "campaign_ref": {"kind": "campaign", "id": "campaign-1"},
        "target_ref": {"kind": "target", "id": "target-1"},
        "optimization_hypothesis": "Reduce target potency while preserving domain support.",
        "snapshot": {
            "endpoint_definition": {"endpoint_key": "activity", "version": "1",
                                    "canonical_unit": "nM",
                                    "measurement_type": "concentration",
                                    "target_ref": {"kind": "target", "id": "target-1"},
                                    "direction": "minimize"},
            "identity_policy_release_id": "policy-1", "data_classification": "internal",
            "compound_smiles": {"c-1": "CCO", "c-2": "CCN", "c-3": "CCC"},
            "split_assignments": {"m-1": "train", "m-2": "validation", "m-3": "test"},
        },
        "train": {"registration": {
            "model_object_id": "model-1", "release_name": "cycle-1",
            "source_commit": "0" * 40, "scientific_lifecycle": "technical_smoke",
            "intended_use": {}, "prohibited_use": {},
            "known_limitations": {}}},
        "candidates": [{"proposal_id": "00000000-0000-4000-8000-000000000001",
                        "smiles": "CCN", "objectives": {}, "constraints": {},
                        "components": {}}],
        "acquisition": {"prediction_objective_key": "activity",
                        "objectives": [{"key": "activity", "direction": "minimize"}],
                        "capacity": 1},
    }


class ClosedLoopControllerTests(unittest.TestCase):
    def test_spec_is_explicit_and_rows_derive_from_ingested_measurements(self):
        spec = specification()
        measurements = MEASUREMENTS + [
            {**MEASUREMENTS[0], "measurement_id": "m-2",
             "compound": {"kind": "compound", "id": "c-2"}},
            {**MEASUREMENTS[0], "measurement_id": "m-3",
             "compound": {"kind": "compound", "id": "c-3"}},
        ]
        ClosedLoopController.validate_spec(spec, measurements)
        rows = ClosedLoopController._rows(spec, measurements)
        self.assertEqual(rows[0]["smiles"], "CCO")
        self.assertEqual(rows[0]["protocol_id"], "p-1")
        self.assertEqual(rows[0]["split"], "train")
        self.assertTrue(_digest(spec).startswith("sha256:"))

    def test_missing_compound_identity_mapping_fails_closed(self):
        spec = specification()
        spec["snapshot"]["compound_smiles"] = {}
        with self.assertRaises(failures.DiracInvalidParameters):
            ClosedLoopController.validate_spec(spec, MEASUREMENTS * 3)

    def test_failed_qc_and_missing_values_are_not_training_rows(self):
        spec = specification()
        measurements = [
            {**MEASUREMENTS[0], "measurement_id": "m-1"},
            {**MEASUREMENTS[0], "measurement_id": "m-2",
             "compound": {"kind": "compound", "id": "c-2"},
             "qc": {"status": "fail", "reason_codes": ["ASSAY_FAIL"]}},
            {**MEASUREMENTS[0], "measurement_id": "m-3",
             "compound": {"kind": "compound", "id": "c-3"},
             "qualifier": "missing", "quantity": {"unit": "nM"}},
        ]
        rows = ClosedLoopController._rows(spec, measurements)
        self.assertEqual([row["measurement_id"] for row in rows], ["m-1"])

    def test_endpoint_direction_cannot_silently_flip_optimization(self):
        spec = specification()
        spec["acquisition"]["objectives"][0]["direction"] = "maximize"
        with self.assertRaises(failures.DiracInvalidParameters):
            ClosedLoopController.validate_spec(spec, MEASUREMENTS * 3)


if __name__ == "__main__":
    unittest.main()
