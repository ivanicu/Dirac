from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import failures
from research.loop_controller import ResearchLoopController
from research.loop_repository import LoopClaim, ResearchLoopRepository
from research.loop_summary import LoopSummaryBuilder


class RecordingRepository:
    def __init__(self):
        self.transitions = []
        self.linked_artifacts = []

    def link_artifact(self, **kwargs):
        self.linked_artifacts.append(kwargs)

    def transition(self, claim, **kwargs):
        self.transitions.append((claim, kwargs))
        return kwargs


def claim(state):
    return LoopClaim(
        run_id="00000000-0000-0000-0000-000000000001",
        version=int(state["version"]), lease_owner="controller",
        lease_expires_at=datetime.now(timezone.utc), state=state,
    )


class ResearchLoopControllerStateMachineTests(unittest.TestCase):
    def controller(self):
        controller = ResearchLoopController.__new__(ResearchLoopController)
        controller.repository = RecordingRepository()
        controller.instance_id = "controller-test"
        return controller

    def test_reason_job_completion_persists_proposal_pointer_before_advancing(self):
        proposal = {
            "context_digest": "sha256:" + "1" * 64,
            "candidate_actions": [], "preferred_action_id": None,
        }
        controller = self.controller()
        controller.service = SimpleNamespace(get_job=lambda *_args, **_kwargs: {
            "state": "done",
            "artifacts": [{
                "id": "00000000-0000-0000-0000-000000000099",
                "role": "research.proposal", "sha256": "2" * 64,
            }],
        })
        controller.store = SimpleNamespace(read=lambda _identifier: (
            SimpleNamespace(role="research.proposal"),
            json.dumps(proposal).encode("utf-8"),
        ))
        state = {
            "version": 7, "actor_kind": "human", "actor_id": "chemist",
            "data_classification": "internal", "outputs": {},
            "stage_jobs": {"active": {
                "kind": "reason",
                "job_id": "00000000-0000-0000-0000-000000000010",
            }},
        }

        controller._stage_wait_job(claim(state))

        transition = controller.repository.transitions[-1][1]
        self.assertEqual(transition["stage"], "validate_proposal")
        self.assertEqual(
            transition["updates"]["proposal_artifact_id"],
            "00000000-0000-0000-0000-000000000099")
        self.assertEqual(
            transition["updates"]["proposal_context_digest"], bytes.fromhex("1" * 64))
        self.assertEqual(
            transition["updates"]["outputs"]["proposal_artifact_sha256"],
            "sha256:" + "2" * 64)

    def test_failed_reason_job_blocks_with_reason_retry_stage(self):
        controller = self.controller()
        state = {
            "version": 9, "stage": "wait_job",
            "stage_jobs": {"active": {"kind": "reason", "job_id": "job-1"}},
        }
        controller._block_claim(
            claim(state), failures.DiracFailure("PROVIDER_UNAVAILABLE", "offline"))
        transition = controller.repository.transitions[-1][1]
        self.assertEqual(transition["state"], "blocked")
        self.assertEqual(transition["stage"], "wait_job")
        self.assertEqual(transition["updates"]["attention"]["retry_stage"], "reason")

    def test_failed_command_job_blocks_with_dispatch_retry_stage(self):
        controller = self.controller()
        state = {
            "version": 10, "stage": "wait_job",
            "stage_jobs": {"active": {"kind": "command_job", "job_id": "job-2"}},
        }
        controller._block_claim(claim(state), RuntimeError("worker vanished"))
        attention = controller.repository.transitions[-1][1]["updates"]["attention"]
        self.assertEqual(attention["retry_stage"], "dispatch")

    def test_retry_target_uses_persisted_origin_stage(self):
        state, stage, event = ResearchLoopRepository._control_target({
            "state": "blocked", "stage": "wait_job",
            "attention": {"retry_stage": "reason"},
        }, "retry")
        self.assertEqual((state, stage, event),
                         ("active", "reason", "loop_retried"))

    def test_prepare_prerequisite_resumes_run_without_second_reasoner_call(self):
        controller = self.controller()
        subject = {"kind": "free_energy_transformation", "id": "edge-a-b"}
        proposal = {
            "preferred_action_id": "candidate-1",
            "scientific_questions": [{
                "question_id": "question-1", "question": "Will this edge change ranking?",
            }],
            "candidate_actions": [{
                "proposal_action_id": "candidate-1",
                "scientific_question_id": "question-1",
                "template_id": "fep.run_selected_edge.v1",
                "subject_ref": subject,
            }],
        }
        context = {"available_actions": [{
            "template_id": "fep.prepare_selected_edge.v1",
            "subject_refs": [subject],
        }]}
        controller._read_state_artifact = lambda _state, which: (
            context if which == "context" else proposal)
        select_state = {"version": 2, "outputs": {}}
        controller._stage_select_action(claim(select_state))
        selected_outputs = controller.repository.transitions[-1][1]["updates"]["outputs"]
        self.assertEqual(selected_outputs["selected_candidate"]["template_id"],
                         "fep.prepare_selected_edge.v1")
        self.assertEqual(
            selected_outputs["deferred_candidate_after_prerequisite"]["template_id"],
            "fep.run_selected_edge.v1")

        observe_state = {
            "version": 3, "stage_jobs": {"active": {
                "kind": "command_job", "job_id": "prepare-job",
                "template_id": "fep.prepare_selected_edge.v1",
            }},
            "outputs": selected_outputs,
            "budget_remaining": {"iterations": 3}, "budget_spent": {},
        }
        controller._stage_observe(claim(observe_state))
        observed_outputs = controller.repository.transitions[-1][1]["updates"]["outputs"]
        self.assertTrue(observed_outputs["resume_candidate_after_snapshot"])
        self.assertEqual(observed_outputs["selected_candidate"]["template_id"],
                         "fep.run_selected_edge.v1")

        built = SimpleNamespace(
            canonical_bytes=b"{}", size_bytes=2,
            digest="sha256:" + "3" * 64)
        controller.context_builder = SimpleNamespace(build=lambda *_args: built)
        controller.fep = SimpleNamespace(snapshot=lambda _state: {})
        controller._store_artifact = lambda *_args: {
            "id": "00000000-0000-0000-0000-000000000077",
            "sha256": "sha256:" + "4" * 64,
        }
        snapshot_state = {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "version": 4, "outputs": observed_outputs,
        }
        controller._stage_snapshot_context(claim(snapshot_state))
        snapshot_transition = controller.repository.transitions[-1][1]
        self.assertEqual(snapshot_transition["stage"], "prepare_action")
        self.assertNotIn("resume_candidate_after_snapshot",
                         snapshot_transition["updates"]["outputs"])

    def test_campaign_binding_change_invalidates_proposal_before_selection(self):
        controller = self.controller()
        context = {
            "digest": "sha256:" + "1" * 64,
            "campaign_binding": {
                "campaign_scientific_generation": 2,
                "campaign_scientific_digest": "sha256:" + "2" * 64,
                "campaign_status": "planned",
                "state_digest": "sha256:" + "3" * 64,
            },
        }
        proposal = {"context_digest": context["digest"]}
        controller._read_state_artifact = lambda _state, which: (
            context if which == "context" else proposal)
        controller.fep = SimpleNamespace(snapshot=lambda _state: {
            "campaign_binding": {
                **context["campaign_binding"],
                "campaign_scientific_generation": 3,
            },
        })
        state = {"version": 6}

        controller._stage_validate_proposal(claim(state))

        transition = controller.repository.transitions[-1][1]
        self.assertEqual(transition["stage"], "snapshot_context")
        self.assertEqual(transition["event_type"], "proposal_stale")
        self.assertEqual(transition["payload"]["reason"],
                         "campaign_binding_changed")

    def test_cancel_does_not_cancel_an_already_dispatched_physical_runset(self):
        controller = self.controller()
        state = {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "version": 9, "state": "active", "stage": "wait_job",
            "data_classification": "internal", "policy": {},
            "stage_jobs": {"active": {
                "kind": "runset",
                "runset_id": "00000000-0000-0000-0000-000000000055",
            }},
        }
        calls = []

        class Repository:
            def get(self, *_args, **_kwargs):
                return state

            def control(self, **kwargs):
                calls.append(kwargs)
                return {**state, "state": "cancelled", "stage": "completed",
                        "version": 10}

        controller.repository = Repository()
        controller.fep = SimpleNamespace(runsets=SimpleNamespace(
            cancel=lambda *_args, **_kwargs: self.fail(
                "loop cancellation silently cancelled physical work")))
        controller.wake = lambda: None
        result = controller.control({
            "run_ref": {"kind": "run", "id": state["run_id"]},
            "expected_version": 9,
            "action": "cancel",
            "rationale": "Stop AI iteration but preserve approved compute",
        }, {"kind": "human", "id": "chemist"})

        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(calls[0]["action"], "cancel")

    def test_shutdown_prevents_a_late_timer_from_resubmitting_work(self):
        controller = self.controller()
        controller._lock = __import__("threading").Lock()
        controller._shutdown = True
        controller._running = False
        controller._wake_requested = False
        controller._timer = None
        controller._pool = SimpleNamespace(
            submit=lambda *_args: self.fail("shutdown controller submitted work"))
        controller.wake()

    def test_completion_persists_source_exact_immutable_summary_before_terminal_state(self):
        controller = self.controller()
        controller.summary_builder = LoopSummaryBuilder()
        stored = []

        def put(raw, *, role, media_type):
            stored.append((raw, role, media_type))
            return SimpleNamespace(
                id="00000000-0000-0000-0000-000000000088",
                sha256="8" * 64,
            )

        controller.store = SimpleNamespace(put=put)
        context = {
            "digest": "sha256:" + "3" * 64,
            "facts": [{
                "fact_id": "fact:edge:result",
                "source_class": "method_result",
                "source_ref": {
                    "kind": "artifact",
                    "id": "00000000-0000-0000-0000-000000000099",
                    "sha256": "sha256:" + "9" * 64,
                },
                "freshness": {"stale": False, "source_generation": 4},
                "claim_boundary": {
                    "status": "completed_unvalidated",
                    "eligible_as_scientific_evidence": False,
                    "reason_codes": ["METHOD_RESULT_NOT_EVIDENCE"],
                },
            }],
            "action_history": [{"template_id": "fep.run_selected_edge.v1"}],
        }
        controller._read_state_artifact = lambda _state, which: (
            context if which == "context" else self.fail("unexpected Artifact read"))
        state = {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "program_id": "00000000-0000-0000-0000-000000000002",
            "campaign_id": "00000000-0000-0000-0000-000000000003",
            "version": 12, "iteration": 2,
            "data_classification": "internal",
            "context_artifact_id": "00000000-0000-0000-0000-000000000077",
            "outputs": {"context_artifact_sha256": "sha256:" + "7" * 64},
            "budget_remaining": {"reasoner_calls": 0, "fep_runsets": 0},
            "budget_spent": {"reasoner_calls": 2, "fep_runsets": 1},
        }

        controller._stage_guard(claim(state))

        self.assertEqual(stored[0][1:], ("research.loop_summary", "application/json"))
        summary = json.loads(stored[0][0])
        self.assertEqual(summary["source_classes"], ["method_result"])
        self.assertEqual(summary["claims"][0]["source_ref"]["sha256"],
                         "sha256:" + "9" * 64)
        self.assertEqual(summary["claims"][0]["claim_boundary"], {
            "status": "completed_unvalidated",
            "eligible_as_scientific_evidence": False,
            "reason_codes": ["METHOD_RESULT_NOT_EVIDENCE"],
        })
        transition = controller.repository.transitions[-1][1]
        self.assertEqual((transition["state"], transition["stage"]),
                         ("completed", "completed"))
        self.assertEqual(transition["artifact_id"],
                         "00000000-0000-0000-0000-000000000088")
        self.assertEqual(transition["updates"]["outputs"]["summary_ref"], {
            "kind": "artifact",
            "id": "00000000-0000-0000-0000-000000000088",
            "sha256": "sha256:" + "8" * 64,
        })


if __name__ == "__main__":
    unittest.main()
