from __future__ import annotations

import copy
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace

import failures
from dirac_app.dispatcher import CommandDispatcher
from programs.repository import MemoryProgramRepository


ACTOR = {"kind": "human", "id": "chemist:ivan"}


class ProgramAggregateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = MemoryProgramRepository()
        created = self.repo.create({
            "code": "mor-pam", "name": "MOR PAM", "summary": "Optimize a selective MOR PAM",
            "lifecycle": "active", "stage": "hit_to_lead",
        }, ACTOR, "create-1")
        self.program_ref = created["program"]["ref"]

    def test_atoms_are_versioned_and_snapshot_is_immutable(self) -> None:
        first = self.repo.record_objective(self.program_ref, 1, {
            "key": "cell-potency", "title": "Cell potency", "rationale": "Primary efficacy gate",
            "category": "efficacy", "metric": "EC50", "direction": "at_most",
            "threshold": {"value": 100, "unit": "nM"}, "hardness": "hard",
        }, ACTOR, "objective-1")
        second = self.repo.record_objective(self.program_ref, 2, {
            "key": "cell-potency", "title": "Cell potency", "rationale": "Tightened after assay review",
            "category": "efficacy", "metric": "EC50", "direction": "at_most",
            "threshold": {"value": 30, "unit": "nM"}, "hardness": "hard",
        }, ACTOR, "objective-2")
        self.assertEqual(first["objective"]["revision"], 1)
        self.assertEqual(second["objective"]["revision"], 2)
        overview = self.repo.get(self.program_ref)["program"]
        self.assertEqual(overview["objectives"][0]["status"], "superseded")

        snapshot = self.repo.create_snapshot(self.program_ref, 3, ACTOR, "snapshot-1")["snapshot"]
        frozen = copy.deepcopy(snapshot["document"])
        self.repo.record_decision(self.program_ref, 4, {
            "key": "advance-series-a", "type": "portfolio", "action": "Advance series A",
            "outcome": "advance", "rationale": "Best balance of potency and selectivity",
            "alternatives": ["hold", "stop"],
        }, ACTOR, "decision-1")
        self.assertEqual(snapshot["document"], frozen)
        self.assertNotEqual(self.repo.get(self.program_ref)["program"]["version"],
                            snapshot["program_version"])

    def test_optimistic_version_blocks_lost_update(self) -> None:
        self.repo.update(self.program_ref, 1, {"summary": "first edit"}, ACTOR, "edit-1")
        with self.assertRaises(failures.DiracInvalidParameters) as caught:
            self.repo.update(self.program_ref, 1, {"summary": "stale edit"}, ACTOR, "edit-2")
        self.assertEqual(caught.exception.details["current_version"], 2)

    def test_request_id_is_idempotent(self) -> None:
        payload = {
            "key": "binding-mode", "title": "Binding mode", "statement": "The allosteric pocket is stable",
            "falsification_criterion": "Replicate structures lose the pocket", "confidence": 0.55,
        }
        first = self.repo.record_hypothesis(self.program_ref, 1, payload, ACTOR, "hypothesis-1")
        again = self.repo.record_hypothesis(self.program_ref, 1, payload, ACTOR, "hypothesis-1")
        self.assertEqual(first, again)
        self.assertEqual(self.repo.get(self.program_ref)["program"]["counts"]["hypotheses"], 1)

    def test_lifecycle_transition_is_explicit(self) -> None:
        self.repo.update(self.program_ref, 1, {"lifecycle": "completed"}, ACTOR, "complete")
        with self.assertRaises(failures.DiracInvalidParameters):
            self.repo.update(self.program_ref, 2, {"lifecycle": "draft"}, ACTOR, "rewind")

    def test_hypothesis_requires_a_falsifier(self) -> None:
        with self.assertRaises(failures.DiracInvalidParameters):
            self.repo.record_hypothesis(self.program_ref, 1, {
                "key": "unfalsifiable", "title": "Vague", "statement": "This should work",
                "confidence": 0.8,
            }, ACTOR, "bad-hypothesis")

    def test_registered_command_surface_dispatches_and_validates(self) -> None:
        repository = MemoryProgramRepository()
        dispatcher = CommandDispatcher(SimpleNamespace(
            program_repository=repository, command_traces=None))
        created = dispatcher.execute("program.create", {"program": {
            "code": "CMD-1", "name": "Command Program", "stage": "discovery",
        }}, actor=ACTOR, request_id="command-create")
        self.assertTrue(created["ok"], created)
        program_ref = created["data"]["program"]["ref"]
        loaded = dispatcher.execute("program.get", {"program_ref": program_ref}, actor=ACTOR)
        self.assertTrue(loaded["ok"], loaded)
        self.assertEqual(loaded["data"]["program"]["code"], "CMD-1")

    def test_kernel_honors_database_boundary_before_assembly(self) -> None:
        env = {**os.environ, "PYTHONPATH": "backend", "DIRAC_DSN": "dbname=isolated-program-test"}
        result = subprocess.run([sys.executable, "-c", "import kernel; print(kernel.DEFAULT_DSN)"],
                                cwd=os.getcwd(), env=env, check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "dbname=isolated-program-test")

    def test_program_operating_system_keeps_governance_in_one_aggregate(self) -> None:
        portfolio = self.repo.create_portfolio({"code": "NEURO", "name": "Neuroscience"}, ACTOR)["portfolio"]
        assigned = self.repo.assign_portfolio(self.program_ref, 1, portfolio["ref"], ACTOR, "portfolio-1")
        self.assertEqual(assigned["program_version"], 2)
        self.repo.assign_member(self.program_ref, 2, {
            "principal": ACTOR, "role": "program_lead", "responsibility": "Own the decision loop",
        }, ACTOR, "member-1")
        self.repo.record_stage_gate(self.program_ref, 3, {
            "key": "hit-to-lead", "stage": "hit_to_lead", "title": "Hit-to-lead readiness",
            "criteria": ["Replicated potency", {"criterion": "Selectivity window", "status": "met"}],
            "status": "ready",
        }, ACTOR, "gate-1")
        work = self.repo.record_work_package(self.program_ref, 4, {
            "key": "confirm-potency", "title": "Confirm potency",
            "description": "Run an orthogonal assay", "status": "active", "priority": 1,
            "owner": ACTOR, "lane": "test_learn",
        }, ACTOR, "work-1")
        overview = self.repo.get(self.program_ref)["program"]
        self.assertEqual(overview["portfolio_ref"], portfolio["ref"])
        self.assertEqual(overview["counts"]["members"], 1)
        self.assertEqual(overview["counts"]["stage_gates"], 1)
        self.assertEqual(work["work_package"]["status"], "active")
        self.assertEqual(work["work_item"]["ref"]["kind"], "work_item")
        self.assertEqual(overview["health"]["basis"], "rule-based-operational-readiness-v1")

    def test_one_work_item_moves_between_workflow_lanes_without_copying(self) -> None:
        created = self.repo.record_work_package(self.program_ref, 1, {
            "key": "select-series", "title": "Select series", "description": "Choose the lead series",
            "lane": "understand", "status": "active",
        }, ACTOR, "work-create")
        work_ref = created["work_item"]["ref"]
        moved = self.repo.transition_work_item(self.program_ref, 2, {
            "work_item_ref": work_ref, "to_lane": "design", "reason": "Target context is ready",
        }, ACTOR, "work-move")
        self.assertEqual(moved["work_item"]["ref"], work_ref)
        self.assertEqual(moved["transition"]["from_lane"], "understand")
        self.assertEqual(moved["transition"]["to_lane"], "design")
        overview = self.repo.get(self.program_ref)["program"]
        self.assertEqual(overview["counts"]["work_items"], 1)
        self.assertEqual(overview["work_items"][0]["lane"], "design")

    def test_work_schedule_and_dependencies_are_real_program_facts(self) -> None:
        first = self.repo.record_work_package(self.program_ref, 1, {
            "key": "map-pocket", "title": "Map pocket", "description": "Confirm the site",
            "lane": "understand", "status": "done", "start_on": "2026-08-14",
            "due_on": "2026-08-18",
        }, ACTOR, "schedule-first")
        second = self.repo.record_work_package(self.program_ref, 2, {
            "key": "design-series", "title": "Design series", "description": "Propose compounds",
            "lane": "design", "status": "active", "start_on": "2026-08-19",
            "due_on": "2026-08-28", "depends_on_refs": [first["work_item"]["ref"]],
        }, ACTOR, "schedule-second")
        overview = self.repo.get(self.program_ref)["program"]
        current = next(item for item in overview["work_items"] if item["key"] == "design-series")
        self.assertEqual(current["start_on"], "2026-08-19")
        self.assertEqual(current["due_on"], "2026-08-28")
        self.assertEqual(current["depends_on_refs"], [first["work_item"]["ref"]])
        with self.assertRaises(failures.DiracInvalidParameters):
            self.repo.record_work_package(self.program_ref, 3, {
                "key": "map-pocket", "title": "Map pocket", "description": "Create a cycle",
                "lane": "understand", "depends_on_refs": [second["work_item"]["ref"]],
            }, ACTOR, "schedule-cycle")

    def test_work_schedule_rejects_reversed_dates(self) -> None:
        with self.assertRaises(failures.DiracInvalidParameters):
            self.repo.record_work_package(self.program_ref, 1, {
                "key": "time-travel", "title": "Time travel", "description": "Impossible plan",
                "start_on": "2026-08-20", "due_on": "2026-08-19",
            }, ACTOR, "bad-schedule")

    def test_runtime_job_can_belong_to_only_one_work_item(self) -> None:
        first = self.repo.record_work_package(self.program_ref, 1, {
            "key": "first", "title": "First", "description": "First job",
        }, ACTOR, "first")
        second = self.repo.record_work_package(self.program_ref, 2, {
            "key": "second", "title": "Second", "description": "Second job",
        }, ACTOR, "second")
        job = {"kind": "job", "id": "job-1"}
        self.repo.attach_work_execution(self.program_ref, 3, {
            "work_item_ref": first["work_item"]["ref"], "job_ref": job,
        }, ACTOR, "attach-first")
        with self.assertRaises(failures.DiracInvalidParameters):
            self.repo.attach_work_execution(self.program_ref, 4, {
                "work_item_ref": second["work_item"]["ref"], "job_ref": job,
            }, ACTOR, "attach-second")

    def test_canonical_lineage_shapes_do_not_collapse_physical_identity(self) -> None:
        edge = self.repo.record_lineage(self.program_ref, 1, {
            "source_ref": {"kind": "compound", "id": "compound-1"},
            "relation": "has_form", "target_ref": {"kind": "compound_form", "id": "form-1"},
        }, ACTOR, "lineage-1")
        self.assertEqual(edge["lineage"]["source_ref"]["id"], "compound-1")
        self.assertEqual(edge["lineage"]["target_ref"]["id"], "form-1")
        with self.assertRaises(failures.DiracInvalidParameters):
            self.repo.record_lineage(self.program_ref, 2, {
                "source_ref": {"kind": "compound", "id": "compound-1"},
                "relation": "sampled_from", "target_ref": {"kind": "sample", "id": "sample-1"},
            }, ACTOR, "bad-lineage")

    def test_reference_jobs_preserve_identity_and_independent_versions(self) -> None:
        disease = self.repo.record_reference_job(self.program_ref, 1, "target_disease", {
            "disease_key": "EFO:0000305", "name": "Breast carcinoma",
            "ontology": {"namespace": "EFO", "id": "0000305"},
            "target_ref": {"kind": "target", "id": "ESR1"},
            "role": "primary", "rationale": "Program indication",
        }, ACTOR, "target-disease")
        sample = self.repo.record_reference_job(self.program_ref, 2, "sample", {
            "sample_code": "MOR-001-A", "batch_ref": {"kind": "batch", "id": "batch-1"},
            "amount_value": 2.5, "amount_unit": "mg", "location": "Freezer A",
        }, ACTOR, "sample-create")
        self.assertEqual(disease["record"]["ref"]["kind"], "disease")
        self.assertEqual(sample["record"]["ref"]["kind"], "sample")
        moved = self.repo.record_reference_job(self.program_ref, 3, "sample_transfer", {
            "sample_ref": sample["record"]["ref"], "to_location": "Assay lab",
            "reason": "Allocated for potency experiment",
        }, ACTOR, "sample-move")
        self.assertEqual(moved["record"]["ref"], sample["record"]["ref"])
        overview = self.repo.get(self.program_ref)["program"]
        self.assertEqual(overview["counts"]["reference_jobs"], 3)

    def test_reference_job_semantics_fail_closed(self) -> None:
        with self.assertRaises(failures.DiracInvalidParameters):
            self.repo.record_reference_job(self.program_ref, 1, "substance_registration", {
                "compound_ref": {"kind": "compound", "id": "compound-1"},
                "status": "approved", "definition": {}, "validation": {},
            }, ACTOR, "bad-approval")
        with self.assertRaises(failures.DiracInvalidParameters):
            self.repo.record_reference_job(self.program_ref, 1, "analysis_snapshot", {
                "title": "Unpinned review", "snapshot_mode": "preserved",
                "dataset_version_refs": [], "state": {},
            }, ACTOR, "bad-snapshot")
        with self.assertRaises(failures.DiracInvalidParameters):
            self.repo.record_reference_job(self.program_ref, 1, "gate_criterion", {
                "stage_gate_ref": {"kind": "stage_gate", "id": "gate-1"},
                "criterion_key": "potency", "status": "met", "explanation": "Looks good",
            }, ACTOR, "bad-gate")

    def test_reference_job_command_is_public_and_dispatchable(self) -> None:
        dispatcher = CommandDispatcher(SimpleNamespace(
            program_repository=self.repo, command_traces=None))
        response = dispatcher.execute("program.target_disease.link", {
            "program_ref": self.program_ref, "expected_version": 1, "record": {
                "disease_key": "MONDO:0004992", "name": "Cancer",
                "ontology": {"namespace": "MONDO", "id": "0004992"},
                "target_ref": {"kind": "target", "id": "T-1"},
                "rationale": "Primary disease context",
            },
        }, actor=ACTOR, request_id="disease-command")
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["data"]["record"]["ref"]["kind"], "disease")


if __name__ == "__main__":
    unittest.main()
