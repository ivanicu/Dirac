from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import failures
from research.action_compiler import ActionCompiler


def artifact_ref() -> dict:
    return {"kind": "artifact", "id": str(uuid4()), "sha256": "sha256:" + "a" * 64}


class Resolver:
    def __init__(self) -> None:
        self.campaign_id = str(uuid4())
        self.refs = [artifact_ref() for _ in range(4)]
        self.source_versions = {
            "campaign_version": 8,
            "campaign_scientific_generation": 4,
            "campaign_scientific_digest": "sha256:" + "b" * 64,
            "network_digest": "sha256:" + "c" * 64,
            "edge_spec_digest": "sha256:" + "d" * 64,
        }

    def resolve(self, **_kwargs):
        return {
            "command_input": {
                "request_key": "research-loop:test:0:dispatch:0",
                "campaign_id": self.campaign_id,
                "campaign_scientific_generation": 4,
                "campaign_scientific_digest": "sha256:" + "b" * 64,
                "edge_spec_ref": self.refs[0], "edge_network_ref": self.refs[1],
                "complex_transformation_ref": self.refs[2],
                "solvent_transformation_ref": self.refs[3],
            },
            "source_versions": self.source_versions,
            "estimate": {"available": True, "gpu_hours_upper_bound": 4.0,
                         "external_cost_upper_bound": 0},
            "consequence_summary": "Starts complex and solvent legs across three repeats.",
        }


def loop() -> dict:
    return {
        "run_id": str(uuid4()), "version": 7,
        "budget_remaining": {"fep_runsets": 1, "gpu_hours": 4,
                             "external_cost": 0},
        "policy": {"session_grant": {
            "allowed_risk_classes": ["R0", "R1", "R2"],
            "allowed_template_ids": [
                "fep.prepare_selected_edge.v1", "fep.replan_network.v1",
                "fep.run_selected_edge.v1", "fep.stop.v1",
                "fep.defer_for_experiment.v1",
            ],
        }},
    }


def proposal() -> dict:
    return {
        "scientific_questions": [{"question_id": "q1",
            "question": "Which edge can change the current lead ordering?"}],
        "candidate_actions": [{
            "proposal_action_id": "a1", "template_id": "fep.run_selected_edge.v1",
            "subject_ref": {"kind": "free_energy_transformation", "id": "edge-c2-c7"},
            "scientific_question_id": "q1", "parameter_hints": {"edge_id": "edge-c2-c7"},
        }],
        "preferred_action_id": "a1",
    }


class ResearchActionCompilerTests(unittest.TestCase):
    def test_model_hint_compiles_to_exact_existing_command_and_bounded_preview(self):
        resolver = Resolver()
        compiled = ActionCompiler(resolver).compile(
            loop=loop(), context={"digest": "sha256:" + "e" * 64},
            proposal=proposal(), now=datetime(2026, 8, 18, 1, tzinfo=timezone.utc))
        preview = compiled.preview
        self.assertEqual(preview["resolved_command"]["command_id"],
                         "physics.rbfe-run.start")
        self.assertEqual(preview["loop_version"], 8)
        self.assertEqual(preview["consequence"]["risk_class"], "R3")
        self.assertEqual(preview["required_acknowledgements"], [
            "physical_fep_compute", "completed_unvalidated_claim_boundary"])
        self.assertNotIn("command_input", preview)
        self.assertNotIn("campaign_id", proposal()["candidate_actions"][0])

    def test_stale_source_version_expiry_and_input_tamper_all_fail_closed(self):
        resolver = Resolver()
        compiler = ActionCompiler(resolver)
        current_loop = loop()
        compiled = compiler.compile(
            loop=current_loop, context={"digest": "sha256:" + "e" * 64},
            proposal=proposal(), now=datetime(2026, 8, 18, 1, tzinfo=timezone.utc))
        pending = {"preview": dict(compiled.preview),
                   "preview_artifact_sha256": compiled.preview_digest,
                   "command_input": dict(compiled.command_input)}
        awaiting = {**current_loop, "version": 8}
        compiler.revalidate(
            pending, loop=awaiting, current_context_digest="sha256:" + "e" * 64,
            current_source_versions=resolver.source_versions,
            acknowledgements=list(compiled.preview["required_acknowledgements"]),
            actor={"kind": "human", "id": "chemist"},
            now=datetime(2026, 8, 18, 1, 1, tzinfo=timezone.utc))
        for mutation in ("source", "expiry", "input"):
            changed = deepcopy(pending)
            sources = dict(resolver.source_versions)
            clock = datetime(2026, 8, 18, 1, 1, tzinfo=timezone.utc)
            if mutation == "source":
                sources["campaign_version"] = 9
            elif mutation == "expiry":
                clock = datetime(2026, 8, 18, 2, tzinfo=timezone.utc)
            else:
                changed["command_input"]["analysis_bootstraps"] = 9
            with self.assertRaises(failures.DiracStalePreview, msg=mutation):
                compiler.revalidate(
                    changed, loop=awaiting,
                    current_context_digest="sha256:" + "e" * 64,
                    current_source_versions=sources,
                    acknowledgements=list(compiled.preview["required_acknowledgements"]),
                    actor={"kind": "human", "id": "chemist"}, now=clock)

    def test_persisted_preview_cannot_be_rewritten_into_a_program_mutation(self):
        resolver = Resolver()
        compiler = ActionCompiler(resolver)
        current_loop = loop()
        compiled = compiler.compile(
            loop=current_loop, context={"digest": "sha256:" + "e" * 64},
            proposal=proposal(), now=datetime(2026, 8, 18, 1, tzinfo=timezone.utc))
        awaiting = {**current_loop, "version": 8}
        pending = {
            "preview": deepcopy(dict(compiled.preview)),
            "preview_artifact_sha256": compiled.preview_digest,
            "command_input": dict(compiled.command_input),
        }
        pending["preview"]["resolved_command"]["command_id"] = (
            "program.hypothesis.create")

        with self.assertRaises(failures.DiracStalePreview) as caught:
            compiler.revalidate(
                pending, loop=awaiting,
                current_context_digest="sha256:" + "e" * 64,
                current_source_versions=resolver.source_versions,
                acknowledgements=list(compiled.preview["required_acknowledgements"]),
                actor={"kind": "human", "id": "chemist"},
                now=datetime(2026, 8, 18, 1, 1, tzinfo=timezone.utc))
        witnesses = caught.exception.details["stale_witnesses"]
        self.assertIn("preview_artifact_digest", witnesses)
        self.assertIn("resolved_command", witnesses)
        self.assertIn("command_contract", witnesses)

    def test_budget_reduction_invalidates_a_previously_affordable_preview(self):
        resolver = Resolver()
        compiler = ActionCompiler(resolver)
        current_loop = loop()
        compiled = compiler.compile(
            loop=current_loop, context={"digest": "sha256:" + "e" * 64},
            proposal=proposal(), now=datetime(2026, 8, 18, 1, tzinfo=timezone.utc))
        pending = {
            "preview": dict(compiled.preview),
            "preview_artifact_sha256": compiled.preview_digest,
            "command_input": dict(compiled.command_input),
        }
        awaiting = deepcopy({**current_loop, "version": 8})
        awaiting["budget_remaining"]["fep_runsets"] = 0

        with self.assertRaises(failures.DiracStalePreview) as caught:
            compiler.revalidate(
                pending, loop=awaiting,
                current_context_digest="sha256:" + "e" * 64,
                current_source_versions=resolver.source_versions,
                acknowledgements=list(compiled.preview["required_acknowledgements"]),
                actor={"kind": "human", "id": "chemist"},
                now=datetime(2026, 8, 18, 1, 1, tzinfo=timezone.utc))
        self.assertIn("budget", caught.exception.details["stale_witnesses"])

    def test_template_outside_frozen_grant_cannot_compile(self):
        current = loop()
        current["policy"]["session_grant"]["allowed_template_ids"] = []
        with self.assertRaises(failures.DiracUnsupported):
            ActionCompiler(Resolver()).compile(
                loop=current, context={"digest": "sha256:" + "e" * 64},
                proposal=proposal())

    def test_rejected_fingerprint_and_same_subject_limit_fail_closed(self):
        resolver = Resolver()
        compiler = ActionCompiler(resolver)
        current = loop()
        current["policy"]["max_same_subject_actions"] = 1
        baseline = compiler.compile(
            loop=current,
            context={"digest": "sha256:" + "e" * 64, "action_history": []},
            proposal=proposal())
        rejected = [{
            "action_fingerprint": baseline.preview["action_fingerprint"],
            "subject_ref": baseline.preview["subject_ref"],
            "human_rejected": True,
        }]
        with self.assertRaises(failures.DiracUnsupported):
            compiler.compile(
                loop=current,
                context={"digest": "sha256:" + "e" * 64,
                         "action_history": rejected},
                proposal=proposal())
        completed = [{
            "action_fingerprint": "sha256:" + "f" * 64,
            "subject_ref": baseline.preview["subject_ref"],
            "human_rejected": False,
        }]
        with self.assertRaises(failures.DiracUnsupported):
            compiler.compile(
                loop=current,
                context={"digest": "sha256:" + "e" * 64,
                         "action_history": completed},
                proposal=proposal())
