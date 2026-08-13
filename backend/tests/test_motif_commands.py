from __future__ import annotations

import unittest

from dirac_app.dispatcher import CommandDispatcher


class _Kernel:
    command_traces = None


class MotifCommandTests(unittest.TestCase):
    def setUp(self):
        self.dispatcher = CommandDispatcher(_Kernel())
        self.actor = {"kind": "service", "id": "test"}

    def test_plan_validate_explain_share_command_surface(self):
        action = {
            "action_kind": "compute", "subject_ref": {"kind": "compound", "id": "c1"},
            "scientific_question": "which compound maximizes utility?",
            "required_input_refs": [],
            "resource_estimate": {"gpu_hours": .1},
            "outcome_scenarios": [
                {"probability": .5, "posterior_utilities": {"A": 1, "B": 3}},
                {"probability": .5, "posterior_utilities": {"A": 1, "B": 0}},
            ],
        }
        planned = self.dispatcher.execute("motif.plan", {
            "evidence_snapshot_ref": {"kind": "evidence_snapshot", "id": "es1"},
            "current_utilities": {"A": 1, "B": .5}, "candidate_actions": [action],
            "remaining_budget": {"gpu_hours": 1}, "iteration": 0,
            "policy": {
                "policy_release_id": "p1", "utility_contract_id": "u1",
                "outcome_model_release_id": "o1", "cost_model_release_id": "c1",
                "resource_prices": {"gpu_hours": .1}, "max_iterations": 3,
                "max_actions_per_subject_question": 1,
            },
        }, actor=self.actor)
        self.assertTrue(planned["ok"], planned)
        self.assertEqual(planned["data"]["decision"], "act")
        explained = self.dispatcher.execute(
            "motif.explain", {"plan": planned["data"]}, actor=self.actor)
        self.assertTrue(explained["ok"], explained)
        self.assertTrue(explained["data"]["p_decision_change_is_diagnostic_only"])

        validated = self.dispatcher.execute("motif.validate", {
            "schema": "orthogonal-state", "document": {
                "execution": "succeeded", "applicability": "applicable",
                "scientific": "provisional", "disposition": "pending",
                "claim_eligibility": "ineligible_provisional_quality",
                "reason_codes": [],
            }}, actor=self.actor)
        self.assertTrue(validated["ok"], validated)
        self.assertTrue(validated["data"]["valid"])


if __name__ == "__main__":
    unittest.main()
