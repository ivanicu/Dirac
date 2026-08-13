from __future__ import annotations

import copy
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


if __name__ == "__main__":
    unittest.main()
