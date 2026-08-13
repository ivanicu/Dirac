from __future__ import annotations

import copy
import json
import math
import pathlib
import unittest
from types import SimpleNamespace

import failures
from dirac_app.dispatcher import CommandDispatcher
from programs import domain as D
from programs.repository import MemoryProgramRepository


ACTOR = {"kind": "human", "id": "adversarial:program-loop"}
ROOT = pathlib.Path(__file__).resolve().parents[2]


CASES: dict[str, tuple[str, dict]] = {
    "program.target_disease.link": ("target_disease", {
        "disease_key": "MONDO:0004992", "name": "cancer",
        "ontology": {"namespace": "MONDO", "id": "0004992"},
        "target_ref": {"kind": "target", "id": "target-1"},
        "role": "primary", "rationale": "Declared scope",
    }),
    "identity.substance_registration.record": ("substance_registration", {
        "compound_ref": {"kind": "compound", "id": "compound-1"},
        "status": "approved", "definition": {"parent": "C"},
        "validation": {"identity": "matched"}, "decision": "Approved by review",
    }),
    "sample.create": ("sample", {
        "sample_code": "SAMPLE-1", "batch_ref": {"kind": "batch", "id": "batch-1"},
        "amount_value": 1.0, "amount_unit": "mg", "location": "freezer-a",
    }),
    "sample.transfer": ("sample_transfer", {
        "sample_ref": {"kind": "sample", "id": "sample-1"},
        "to_location": "assay-lab", "reason": "Allocated to assay",
    }),
    "program.work_comment.record": ("work_comment", {
        "work_item_ref": {"kind": "work_item", "id": "work-1"}, "body": "Review note",
    }),
    "program.work_attachment.record": ("work_attachment", {
        "work_item_ref": {"kind": "work_item", "id": "work-1"},
        "artifact_ref": {"kind": "artifact", "id": "artifact-1"}, "role": "source",
    }),
    "program.gate_criterion.assess": ("gate_criterion", {
        "stage_gate_ref": {"kind": "stage_gate", "id": "gate-1"},
        "criterion_key": "identity", "status": "met",
        "evidence_ref": {"kind": "artifact", "id": "artifact-1"},
        "explanation": "Identity evidence resolves",
    }),
    "protocol.version.record": ("protocol_version", {
        "protocol_key": "binding-v1", "title": "Binding protocol",
        "assay_ref": {"kind": "assay", "id": "assay-1"},
        "specification": {"temperature_k": 298.15},
    }),
    "experiment.record": ("experiment", {
        "experiment_key": "experiment-1", "work_item_ref": {"kind": "work_item", "id": "work-1"},
        "protocol_version_ref": {"kind": "protocol_version", "id": "protocol-1"},
        "title": "Binding experiment", "status": "completed",
        "started_at": "2026-08-13T10:00:00Z", "completed_at": "2026-08-13T11:00:00Z",
        "samples": [{"sample_ref": {"kind": "sample", "id": "sample-1"}, "role": "test"}],
    }),
    "dataset.version.commit": ("dataset_version", {
        "dataset_key": "binding-results", "manifest_artifact_ref": {"kind": "artifact", "id": "artifact-1"},
        "manifest": {"rows": 1}, "schema_version": "1", "access_scope": "program",
        "experiment_ref": {"kind": "experiment", "id": "experiment-1"},
    }),
    "structure.observation.register": ("structure_observation", {
        "observation_key": "obs-1", "structure_ref": {"kind": "protein_structure", "id": "structure-1"},
        "compound_ref": {"kind": "compound", "id": "compound-1"},
        "experiment_ref": {"kind": "experiment", "id": "experiment-1"},
        "dataset_version_ref": {"kind": "dataset_version", "id": "dataset-1"},
    }),
    "structure.annotation.record": ("annotation", {
        "subject_ref": {"kind": "structure_observation", "id": "obs-1"},
        "annotation_kind": "site", "label": "binding site", "value": {"confidence": "observed"},
    }),
    "structure.review.record": ("review", {
        "subject_ref": {"kind": "structure_observation", "id": "obs-1"},
        "review_role": "peer", "status": "accepted", "comment": "Map inspected",
    }),
    "structure.analysis_snapshot.create": ("analysis_snapshot", {
        "work_item_ref": {"kind": "work_item", "id": "work-1"}, "title": "Decision view",
        "snapshot_mode": "preserved", "dataset_version_refs": [{"kind": "dataset_version", "id": "dataset-1"}],
        "state": {"selection": "obs-1"},
    }),
    "evidence.release.import": ("evidence_release", {
        "source_name": "Open Targets", "release_name": "2026-08",
        "retrieved_at": "2026-08-13T12:00:00Z",
        "payload_artifact_ref": {"kind": "artifact", "id": "artifact-1"},
    }),
    "evidence.external.record": ("external_evidence", {
        "release_ref": {"kind": "external_evidence_release", "id": "release-1"},
        "source_record_id": "record-1", "target_ref": {"kind": "target", "id": "target-1"},
        "disease_ref": {"kind": "disease", "id": "disease-1"},
        "data_type": "genetic_association", "evidence_source": "fixture",
        "score": 0.5, "is_direct": False, "payload": {"direction": "supports"},
    }),
}


class ReferenceJobAdversarialDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = MemoryProgramRepository()
        self.program = self.repo.create({"code": "BRUTAL", "name": "Brutal loop"}, ACTOR)["program"]
        self.program_ref = self.program["ref"]

    def test_every_public_record_schema_fails_closed_under_required_field_and_extra_field_mutations(self):
        registry = json.loads((ROOT / "contracts/commands/registry.json").read_text())
        schemas = {item["id"]: item["input_schema"] for item in registry["commands"]}
        dispatcher = CommandDispatcher(SimpleNamespace(program_repository=self.repo, command_traces=None))
        attacks = 0

        def reject(command_id: str, payload: dict, label: str) -> None:
            nonlocal attacks
            with self.assertRaises(failures.DiracInvalidParameters, msg=(command_id, label)):
                dispatcher.execute(command_id, payload, actor=ACTOR,
                                   request_id=f"attack:{attacks}:{command_id}")
            attacks += 1

        for command_id, (_kind, record) in CASES.items():
            schema = schemas[command_id]
            required = schema["properties"]["record"].get("required", [])
            for missing in required:
                candidate = copy.deepcopy(record)
                candidate.pop(missing, None)
                reject(command_id, {"program_ref": self.program_ref, "expected_version": 1,
                                    "record": candidate}, f"missing:{missing}")
            candidate = copy.deepcopy(record)
            candidate["__prototype_pollution_probe__"] = True
            reject(command_id, {"program_ref": self.program_ref, "expected_version": 1,
                                "record": candidate}, "extra-record-field")
            base = {"program_ref": self.program_ref, "expected_version": 1,
                    "record": copy.deepcopy(record)}
            reject(command_id, {**base, "unexpected": True}, "extra-input-field")
            for field in ("program_ref", "expected_version", "record"):
                missing_input = copy.deepcopy(base); missing_input.pop(field)
                reject(command_id, missing_input, f"missing-input:{field}")
            for version in (0, -1, 1.5, True, "1", None):
                reject(command_id, {**base, "expected_version": version}, f"version:{version!r}")

            properties = schema["properties"]["record"]["properties"]
            for field, field_schema in properties.items():
                if field not in record:
                    continue
                if "$ref" in field_schema:
                    invalid_values = [{}, {"kind": "not-a-kind", "id": ""}, "not-an-object"]
                elif "enum" in field_schema:
                    invalid_values = ["__NOT_IN_ENUM__", None, {}]
                else:
                    invalid_values = {
                        "string": [0, [], {}], "number": ["1", None, {}],
                        "integer": [1.5, "1", True], "object": ["object", [], None],
                        "array": [{}, "array", None], "boolean": ["false", 0, None],
                    }.get(field_schema.get("type"), [None])
                for invalid in invalid_values:
                    candidate = copy.deepcopy(record); candidate[field] = invalid
                    reject(command_id, {"program_ref": self.program_ref, "expected_version": 1,
                                        "record": candidate}, f"type:{field}:{invalid!r}")
        self.assertGreaterEqual(attacks, 300)
        self.assertEqual(self.repo.get(self.program_ref)["program"]["version"], 1)

    def test_nonfinite_quantities_and_scores_are_rejected(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(amount=value), self.assertRaises(failures.DiracInvalidParameters):
                D.reference_job("sample", {**CASES["sample.create"][1], "amount_value": value})
            with self.subTest(score=value), self.assertRaises(failures.DiracInvalidParameters):
                D.reference_job("external_evidence", {**CASES["evidence.external.record"][1], "score": value})

    def test_domain_layer_rejects_unknown_fields_even_without_http_schema(self):
        for command_id, (kind, record) in CASES.items():
            with self.subTest(command=command_id), self.assertRaises(failures.DiracInvalidParameters):
                D.reference_job(kind, {**record, "silent_internal_bypass": "attack"})
        with self.assertRaises(failures.DiracInvalidParameters):
            D.reference_job("target_disease", {
                **CASES["program.target_disease.link"][1],
                "ontology": {"namespace": "MONDO", "id": "0004992", "injected": True},
            })
        with self.assertRaises(failures.DiracInvalidParameters):
            D.reference_job("experiment", {
                **CASES["experiment.record"][1],
                "samples": [{"sample_ref": {"kind": "sample", "id": "sample-1"},
                             "role": "test", "amount_override": "infinite"}],
            })
        for kind, record, field in (
            ("protocol_version", CASES["protocol.version.record"][1], "specification"),
            ("dataset_version", CASES["dataset.version.commit"][1], "manifest"),
            ("annotation", CASES["structure.annotation.record"][1], "value"),
            ("analysis_snapshot", CASES["structure.analysis_snapshot.create"][1], "state"),
            ("external_evidence", CASES["evidence.external.record"][1], "payload"),
        ):
            with self.subTest(kind=kind, field=field), self.assertRaises(failures.DiracInvalidParameters):
                D.reference_job(kind, {**record, field: {"nonfinite": math.nan}})

    def test_boolean_fields_are_not_truthiness_coerced(self):
        for value in ("false", "true", 0, 1, [], {}):
            with self.subTest(value=value), self.assertRaises(failures.DiracInvalidParameters):
                D.reference_job("external_evidence", {
                    **CASES["evidence.external.record"][1], "is_direct": value,
                })

    def test_experiment_timeline_is_causally_ordered(self):
        base = CASES["experiment.record"][1]
        invalid = [
            {**base, "status": "running", "started_at": None, "completed_at": None},
            {**base, "status": "completed", "started_at": None},
            {**base, "status": "completed", "started_at": "2026-08-13T12:00:00Z",
             "completed_at": "2026-08-13T11:00:00Z"},
            {**base, "status": "planned", "started_at": "2026-08-13T10:00:00Z",
             "completed_at": "2026-08-13T11:00:00Z"},
        ]
        for index, record in enumerate(invalid):
            with self.subTest(index=index), self.assertRaises(failures.DiracInvalidParameters):
                D.reference_job("experiment", record)

    def test_request_id_collision_cannot_relabel_a_different_mutation(self):
        first = self.repo.record_reference_job(
            self.program_ref, 1, "sample", CASES["sample.create"][1], ACTOR, "same-request")
        with self.assertRaises(failures.DiracInvalidParameters):
            self.repo.record_reference_job(
                self.program_ref, first["program_version"], "sample",
                {**CASES["sample.create"][1], "sample_code": "SAMPLE-2"}, ACTOR, "same-request")
        self.assertEqual(self.repo.get(self.program_ref)["program"]["counts"]["reference_jobs"], 1)

    def test_request_id_from_another_command_cannot_be_replayed_as_a_reference_job(self):
        self.repo.update(self.program_ref, 1, {"summary": "ordinary update"}, ACTOR, "cross-command-id")
        with self.assertRaises(failures.DiracInvalidParameters):
            self.repo.record_reference_job(
                self.program_ref, 2, "sample", CASES["sample.create"][1], ACTOR, "cross-command-id")
        self.assertEqual(self.repo.get(self.program_ref)["program"]["version"], 2)

    def test_sample_custody_cannot_be_hijacked_by_another_program(self):
        sample = self.repo.record_reference_job(
            self.program_ref, 1, "sample", CASES["sample.create"][1], ACTOR, "sample-a")["record"]
        other = self.repo.create({"code": "OTHER", "name": "Other Program"}, ACTOR)["program"]
        with self.assertRaises((failures.DiracNotFound, failures.DiracInvalidParameters)):
            self.repo.record_reference_job(other["ref"], 1, "sample_transfer", {
                "sample_ref": sample["ref"], "to_location": "unknown", "reason": "cross-program attack",
            }, ACTOR, "hijack")
        self.assertEqual(self.repo.get(other["ref"])["program"]["version"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
