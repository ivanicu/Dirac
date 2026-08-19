from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import failures
from research.action_compiler import ActionCompiler
from research.context_builder import ContextBuilder, canonical_digest
from research.fep_adapter import FepAdapter


def artifact(seed: str) -> dict:
    return {"kind": "artifact", "id": str(uuid4()),
            "sha256": "sha256:" + seed * 64}


def fixture() -> dict:
    campaign_id = str(uuid4())
    network_doc = {
        "schema_version": "1.0", "kind": "rbfe_network",
        "compounds": [{"id": "c2", "smiles": "CCO"},
                      {"id": "c7", "smiles": "CCN"}],
        "edges": [{"edge_id": "edge-c2-c7", "left_id": "c2", "right_id": "c7",
                   "mapping_score": 0.91, "mapping_warnings": []}],
        "claim_boundary": "plan only",
    }
    network_doc["digest"] = canonical_digest(network_doc)
    prepared = {
        "job_id": str(uuid4()),
        "result": {"edge_id": "edge-c2-c7", "spec_digest": "sha256:" + "5" * 64,
                   "validation_status": "server_preflight_passed",
                   "system_build": {"mapping_score": 0.91}},
        "edge_spec_ref": artifact("5"), "edge_network_ref": artifact("6"),
        "complex_transformation_ref": artifact("7"),
        "solvent_transformation_ref": artifact("8"),
    }
    now = datetime(2026, 8, 18, 1, tzinfo=timezone.utc)
    return {
        "campaign": {
            "campaign_id": campaign_id, "version": 8, "status": "planned",
            "state_digest": "sha256:" + "1" * 64,
            "campaign_scientific_generation": 4,
            "campaign_scientific_digest": "sha256:" + "2" * 64,
            "state": {"label": "Lead optimization FEP", "inputs": {
                "compounds": [{"id": "c2", "smiles": "CCO"},
                              {"id": "c7", "smiles": "CCN"}]}},
            "created_by": {"kind": "human", "id": "chemist"},
            "updated_at": now,
        },
        "systems": [{
            "prepared_receptor_state_ref": {
                "kind": "prepared_receptor_state", "id": str(uuid4()),
                "sha256": "sha256:" + "3" * 64},
            "campaign_scope": "owned", "execution_eligible": True,
            "label": "Reviewed receptor", "preparation_state": "server-attested",
            "poses": [
                {"pose_ref": {"kind": "pose_hypothesis", "id": str(uuid4()),
                              "sha256": "sha256:" + "a" * 64},
                 "canonical_smiles": "CCO", "review_state": "accepted"},
                {"pose_ref": {"kind": "pose_hypothesis", "id": str(uuid4()),
                              "sha256": "sha256:" + "b" * 64},
                 "canonical_smiles": "CCN", "review_state": "accepted"},
            ],
        }],
        "jobs": [],
        "network": {"ref": artifact("4"), "document": network_doc,
                    "job_id": str(uuid4())},
        "prepared_edges": {"edge-c2-c7": prepared},
        "runsets": [{
            "ref": {"kind": "run", "id": str(uuid4())},
            "edge_id": "edge-c2-c7", "state": "completed", "attention": {},
            "aggregate_output": {"delta_g_kcal_mol": -1.2, "uncertainty": 0.3},
            "edge_spec_ref": prepared["edge_spec_ref"],
            "campaign_scientific_ref": {"kind": "rbfe_campaign",
                "id": campaign_id, "version": 4, "sha256": "sha256:" + "2" * 64},
            "finished_at": now.isoformat(),
        }],
        "action_history": [], "source_clock": "2026-08-18T01:00:00Z",
    }


class FixtureAdapter(FepAdapter):
    def __init__(self, current):
        self.current = current

    def _durable_snapshot(self, _loop):
        return deepcopy(self.current)


def loop(current: dict) -> dict:
    return {
        "run_id": str(uuid4()), "program_id": str(uuid4()),
        "campaign_id": current["campaign"]["campaign_id"],
        "actor_kind": "human", "actor_id": "chemist",
        "state": "active", "stage": "prepare_action", "version": 7,
        "iteration": 1, "intent": "Resolve the lead ordering.",
        "stage_attempts": {},
        "budget_remaining": {"reasoner_calls": 2, "fep_runsets": 1,
                             "gpu_hours": 4, "external_cost": 0},
        "budget_spent": {"reasoner_calls": 1, "fep_runsets": 0,
                         "gpu_hours": 0, "external_cost": 0},
        "policy": {"session_grant": {
            "allowed_risk_classes": ["R0", "R1", "R2"],
            "allowed_template_ids": [
                "fep.prepare_selected_edge.v1", "fep.replan_network.v1",
                "fep.run_selected_edge.v1", "fep.stop.v1",
                "fep.defer_for_experiment.v1"],
        }},
    }


def proposal(template: str) -> dict:
    return {
        "scientific_questions": [{"question_id": "q1",
            "question": "Will edge c2-c7 change the lead ordering?"}],
        "candidate_actions": [{
            "proposal_action_id": "a1", "template_id": template,
            "subject_ref": {"kind": "free_energy_transformation", "id": "edge-c2-c7"},
            "scientific_question_id": "q1",
            "parameter_hints": {"edge_id": "edge-c2-c7"},
        }], "preferred_action_id": "a1",
    }


class ResearchFepAdapterTests(unittest.TestCase):
    def test_snapshot_preserves_completed_unvalidated_boundary_and_validates_schema(self):
        current = fixture()
        adapter = FixtureAdapter(current)
        state = loop(current)
        domain = adapter.snapshot(state)
        built = ContextBuilder().build(state, domain)
        result = next(row for row in built.document["facts"]
                      if row["category"] == "fep_result")
        self.assertEqual(result["source_class"], "method_result")
        self.assertEqual(result["claim_boundary"]["status"], "completed_unvalidated")
        self.assertFalse(result["claim_boundary"]["eligible_as_scientific_evidence"])

    def test_run_selected_edge_resolves_all_server_owned_fields(self):
        current = fixture()
        adapter = FixtureAdapter(current)
        state = loop(current)
        context = ContextBuilder().build(state, adapter.snapshot(state)).document
        compiled = ActionCompiler(adapter).compile(
            loop=state, context=context, proposal=proposal("fep.run_selected_edge.v1"),
            now=datetime(2026, 8, 18, 1, tzinfo=timezone.utc))
        payload = compiled.command_input
        self.assertEqual(payload["campaign_id"], state["campaign_id"])
        self.assertEqual(payload["request_key"],
                         f"research-loop:{state['run_id']}:1:dispatch:0")
        self.assertEqual(payload["campaign_scientific_generation"], 4)
        self.assertEqual(set(payload), {
            "request_key", "campaign_id", "campaign_scientific_generation",
            "campaign_scientific_digest", "edge_spec_ref", "edge_network_ref",
            "complex_transformation_ref", "solvent_transformation_ref"})

    def test_unprepared_edge_is_replaced_by_deterministic_prepare_requirement(self):
        current = fixture()
        current["prepared_edges"] = {}
        adapter = FixtureAdapter(current)
        state = loop(current)
        context = ContextBuilder().build(state, adapter.snapshot(state)).document
        with self.assertRaises(failures.DiracInvalidParameters):
            ActionCompiler(adapter).compile(
                loop=state, context=context,
                proposal=proposal("fep.run_selected_edge.v1"))
        prepared = ActionCompiler(adapter).compile(
            loop=state, context=context,
            proposal=proposal("fep.prepare_selected_edge.v1"))
        self.assertEqual(prepared.command_input["protocol_preset"],
                         "openfe-rfe-standard-v1")
        self.assertNotIn("protocol_preset", proposal(
            "fep.prepare_selected_edge.v1")["candidate_actions"][0]["parameter_hints"])
