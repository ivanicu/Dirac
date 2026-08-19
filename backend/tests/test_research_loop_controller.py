from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import failures
from research.loop_controller import ResearchLoopController
from research.loop_repository import LoopClaim, ResearchLoopRepository


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


if __name__ == "__main__":
    unittest.main()
