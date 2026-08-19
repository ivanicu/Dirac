"""Non-blocking durable controller for the attachment-defined FEP research loop."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import failures
from dirac_app.dispatcher import CommandDispatcher
from research.action_catalog import default_action_catalog
from research.action_compiler import ActionCompiler
from research.context_builder import ContextBuilder
from research.loop_repository import LoopClaim, ResearchLoopRepository, stage_request_key
from research.provider_registry import AiProviderConfigurationError
from research.reasoner import _prompt_release


_ACTIVE_JOB_STATES = {"queued", "running"}
_TERMINAL_JOB_STATES = {"done", "failed", "cancelled"}
_ACTIVE_RUNSET_STATES = {"pending", "running", "aggregating", "cancel_requested"}


class ResearchLoopController:
    kind = "postgres"
    durability = "durable"

    def __init__(
        self, *, repository: ResearchLoopRepository, service: Any,
        artifact_store: Any, provider_registry: Any, fep_adapter: Any,
        kernel: Any, instance_id: str = "dirac-research-loop",
    ) -> None:
        self.repository = repository
        self.service = service
        self.store = artifact_store
        self.providers = provider_registry
        self.fep = fep_adapter
        self.kernel = kernel
        self.instance_id = instance_id
        self.context_builder = ContextBuilder()
        self.action_catalog = default_action_catalog()
        self.compiler = ActionCompiler(fep_adapter, action_catalog=self.action_catalog)
        self.dispatcher = CommandDispatcher(kernel)
        self._pool = ThreadPoolExecutor(max_workers=2,
                                        thread_name_prefix="research-loop-controller")
        self._lock = threading.Lock()
        self._running = False
        self._wake_requested = False
        self._timer: threading.Timer | None = None

    def create(self, payload: Mapping[str, Any], actor: Mapping[str, Any]) -> dict[str, Any]:
        if actor.get("kind") != "human" or not str(actor.get("id") or "").strip():
            raise failures.DiracInvalidParameters(
                "research.loop.create requires a named human actor")
        profile_id = str(payload["provider_profile_id"])
        try:
            profile = self.providers.resolve(profile_id)
            witness = self.providers.attest(
                profile_id, profile.profile_digest, payload["data_classification"])
        except AiProviderConfigurationError as error:
            raise failures.DiracUnsupported(
                "AI provider admission was refused",
                details={"reason": error.reason, **error.details}) from None
        requested_policy = dict(payload["policy"])
        if witness["external_egress"] and not requested_policy.get(
                "cloud_egress_approved"):
            raise failures.DiracUnsupported(
                "external provider egress is not authorized by the loop grant")
        manifest, prompt_digest, _ = _prompt_release()
        now = datetime.now(timezone.utc)
        allowed_templates = [
            "fep.prepare_selected_edge.v1", "fep.replan_network.v1",
            "fep.stop.v1", "fep.defer_for_experiment.v1",
            "fep.run_selected_edge.v1",
        ]
        frozen_policy = {
            **requested_policy,
            "session_grant": {
                "authorized_by": dict(actor),
                "automation_actor": {"kind": "service", "id": self.instance_id},
                "allowed_risk_classes": list(requested_policy["auto_risk_classes"]),
                "allowed_template_ids": allowed_templates,
                "cloud_egress_approved": bool(
                    requested_policy.get("cloud_egress_approved")),
                "expires_at": (now + timedelta(hours=24)).isoformat().replace(
                    "+00:00", "Z"),
            },
        }
        budget = payload["budget"]
        state = self.repository.create(
            request_key=str(payload["request_key"]),
            program_id=str(payload["program_ref"]["id"]),
            campaign_id=str(payload["campaign_ref"]["id"]),
            actor=actor, intent=str(payload["intent"]),
            autonomy_class=str(payload["autonomy_class"]),
            provider_profile_id=profile_id,
            provider_profile_digest=bytes.fromhex(
                profile.profile_digest.removeprefix("sha256:")),
            prompt_release_id=manifest["prompt_release_id"],
            prompt_release_digest=bytes.fromhex(prompt_digest.removeprefix("sha256:")),
            action_catalog_digest=bytes.fromhex(
                self.action_catalog.digest.removeprefix("sha256:")),
            data_classification=str(payload["data_classification"]),
            policy=frozen_policy,
            budget_remaining={
                "reasoner_calls": int(budget["max_reasoner_calls"]),
                "fep_runsets": int(budget["max_fep_runsets"]),
                "gpu_hours": budget["max_gpu_hours"],
                "external_cost": budget["max_external_cost"],
                "iterations": int(budget["max_iterations"]),
            },
        )
        self.wake()
        return {
            "mission_ref": {"kind": "mission", "id": state["mission_id"]},
            "run_ref": {"kind": "run", "id": state["run_id"]},
            "state": state["state"], "stage": state["stage"],
            "version": state["version"], "created": bool(state["created"]),
        }

    def get(self, run_id: str, actor: Mapping[str, Any]) -> dict[str, Any]:
        state = self.repository.get(run_id, actor=actor)
        events = self.repository.events(run_id, actor=actor)[-50:]
        return {
            "run_ref": {"kind": "run", "id": run_id},
            "state": state["state"], "stage": state["stage"],
            "version": state["version"], "iteration": state["iteration"],
            "goal": {"intent": state["intent"]},
            "provider": {
                "profile_id": state["provider_profile_id"],
                "profile_digest": state["provider_profile_digest"],
            },
            "budget": {"remaining": state["budget_remaining"],
                       "spent": state["budget_spent"]},
            "context_ref": self._state_artifact_ref(state, "context"),
            "proposal_ref": self._state_artifact_ref(state, "proposal"),
            "pending_action": state["pending_action"],
            "attention": state["attention"], "events": events,
            "deep_links": {
                "fep_workbench": f"/motif/fep?campaign={state['campaign_id']}",
                "jobs": "/workspace/jobs",
            },
            "claim_boundary": "model_proposal_not_scientific_evidence",
        }

    def approve(self, payload: Mapping[str, Any], actor: Mapping[str, Any]) -> dict[str, Any]:
        run_id = str(payload["run_ref"]["id"])
        state = self.repository.get(run_id, actor=actor)
        pending = state.get("pending_action") or {}
        current_sources = self.fep.current_source_versions(state, pending)
        self.compiler.revalidate(
            pending, loop=state, current_context_digest=str(state["context_digest"]),
            current_source_versions=current_sources,
            acknowledgements=list(payload["acknowledgements"]), actor=actor)
        preview = pending["preview"]
        if preview["action_fingerprint"] != payload["action_fingerprint"]:
            raise failures.DiracStalePreview("action fingerprint does not match preview")
        result = self.repository.decide(
            run_id=run_id, expected_version=int(payload["expected_version"]),
            action_fingerprint=bytes.fromhex(
                preview["action_fingerprint"].removeprefix("sha256:")),
            preview_artifact_id=str(pending["preview_artifact_id"]),
            command_input_digest=bytes.fromhex(
                (preview.get("resolved_command") or {
                    "input_digest": "sha256:" + "0" * 64
                })["input_digest"].removeprefix("sha256:")),
            source_versions=preview["source_versions"], decision="approved",
            actor=actor, rationale=str(payload["rationale"]),
            acknowledgements=list(payload["acknowledgements"]),
        )
        self.wake()
        return {"run_ref": {"kind": "run", "id": run_id},
                "state": result["state"], "stage": result["stage"],
                "version": result["version"]}

    def reject(self, payload: Mapping[str, Any], actor: Mapping[str, Any]) -> dict[str, Any]:
        run_id = str(payload["run_ref"]["id"])
        state = self.repository.get(run_id, actor=actor)
        pending = state.get("pending_action") or {}
        preview = pending.get("preview") or {}
        if preview.get("action_fingerprint") != payload["action_fingerprint"]:
            raise failures.DiracStalePreview("action fingerprint does not match preview")
        result = self.repository.decide(
            run_id=run_id, expected_version=int(payload["expected_version"]),
            action_fingerprint=bytes.fromhex(
                preview["action_fingerprint"].removeprefix("sha256:")),
            preview_artifact_id=str(pending["preview_artifact_id"]),
            command_input_digest=bytes.fromhex(
                (preview.get("resolved_command") or {
                    "input_digest": "sha256:" + "0" * 64
                })["input_digest"].removeprefix("sha256:")),
            source_versions=preview["source_versions"], decision="rejected",
            actor=actor, rationale=str(payload["rationale"]),
        )
        self.wake()
        return {"run_ref": {"kind": "run", "id": run_id},
                "state": result["state"], "stage": result["stage"],
                "version": result["version"]}

    def control(self, payload: Mapping[str, Any], actor: Mapping[str, Any]) -> dict[str, Any]:
        action = str(payload["action"])
        provider_id = payload.get("provider_profile_id")
        provider_digest = None
        if action == "change_provider":
            try:
                loop = self.repository.get(payload["run_ref"]["id"], actor=actor)
                profile = self.providers.resolve(str(provider_id))
                witness = self.providers.attest(
                    profile.profile_id, profile.profile_digest,
                    loop["data_classification"])
                if witness["external_egress"] and not bool(
                        (loop.get("policy") or {}).get("cloud_egress_approved")):
                    raise failures.DiracUnsupported(
                        "external provider egress is not authorized by the loop grant")
                provider_digest = bytes.fromhex(
                    profile.profile_digest.removeprefix("sha256:"))
            except AiProviderConfigurationError as error:
                raise failures.DiracUnsupported(
                    "replacement provider admission was refused",
                    details={"reason": error.reason, **error.details}) from None
        state = self.repository.control(
            run_id=str(payload["run_ref"]["id"]),
            expected_version=int(payload["expected_version"]), action=action,
            actor=actor, rationale=str(payload["rationale"]),
            revised_intent=payload.get("revised_intent"),
            provider_profile_id=provider_id,
            provider_profile_digest=provider_digest,
        )
        self.wake()
        return {"run_ref": {"kind": "run", "id": state["run_id"]},
                "state": state["state"], "stage": state["stage"],
                "version": state["version"]}

    def wake(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._running:
                self._wake_requested = True
                return
            self._running = True
        self._pool.submit(self._drain)

    def shutdown(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _drain(self) -> None:
        try:
            while True:
                claim = self.repository.claim(owner=self.instance_id)
                if claim is None:
                    break
                try:
                    self._advance(claim)
                except Exception as error:  # noqa: BLE001
                    self._block_claim(claim, error)
        finally:
            delay = self.repository.next_wake_delay(maximum_seconds=30)
            with self._lock:
                requested = self._wake_requested
                self._wake_requested = False
                self._running = False
                if not requested and delay is not None:
                    self._timer = threading.Timer(max(0.05, delay), self.wake)
                    self._timer.daemon = True
                    self._timer.start()
            if requested:
                self.wake()

    def _advance(self, claim: LoopClaim) -> None:
        stage = claim.state["stage"]
        handler = getattr(self, f"_stage_{stage}", None)
        if not callable(handler):
            raise failures.DiracInternal(f"research loop stage {stage} has no handler")
        handler(claim)

    def _stage_bootstrap(self, claim: LoopClaim) -> None:
        assessment = self.fep.assess_bootstrap(claim.state)
        if not assessment["ready"]:
            self.repository.transition(
                claim, expected_version=claim.version, state="blocked", stage="bootstrap",
                event_type="loop_blocked", actor=self._automation_actor(),
                payload=assessment, updates={"attention": assessment})
            return
        self.repository.transition(
            claim, expected_version=claim.version, stage="snapshot_context",
            event_type="bootstrap_completed", actor=self._automation_actor())

    def _stage_snapshot_context(self, claim: LoopClaim) -> None:
        projected = {**claim.state, "version": claim.version + 1}
        built = self.context_builder.build(projected, self.fep.snapshot(claim.state))
        reference = self._store_artifact(
            claim.state, built.canonical_bytes, "research.context_snapshot")
        outputs = dict(claim.state["outputs"])
        outputs["context_size_bytes"] = built.size_bytes
        outputs["context_artifact_sha256"] = reference["sha256"]
        resume_candidate = bool(outputs.pop("resume_candidate_after_snapshot", False))
        self.repository.transition(
            claim, expected_version=claim.version,
            stage="prepare_action" if resume_candidate else "reason",
            event_type="context_snapshotted", actor=self._automation_actor(),
            artifact_id=reference["id"],
            payload={"context_digest": built.digest, "size_bytes": built.size_bytes},
            updates={"context_artifact_id": reference["id"],
                     "context_digest": bytes.fromhex(built.digest.removeprefix("sha256:")),
                     "outputs": outputs})

    def _stage_reason(self, claim: LoopClaim) -> None:
        remaining = dict(claim.state["budget_remaining"])
        if int(remaining.get("reasoner_calls", 0)) <= 0:
            self.repository.transition(
                claim, expected_version=claim.version, state="blocked", stage="reason",
                event_type="loop_blocked", actor=self._automation_actor(),
                payload={"reason_code": "REASONER_BUDGET_EXHAUSTED"},
                updates={"attention": {"reason_code": "REASONER_BUDGET_EXHAUSTED"}})
            return
        attempt = int(claim.state["stage_attempts"].get("reason", 0))
        request_key = stage_request_key(
            claim.run_id, int(claim.state["iteration"]), "reason", attempt)
        context_ref = self._state_artifact_ref(claim.state, "context")
        manifest, _, _ = _prompt_release()
        payload = {
            "request_key": request_key,
            "run_ref": {"kind": "run", "id": claim.run_id},
            "loop_version": claim.version,
            "iteration": int(claim.state["iteration"]),
            "context_snapshot_ref": context_ref,
            "context_digest": claim.state["context_digest"],
            "context_size_bytes": int(claim.state["outputs"]["context_size_bytes"]),
            "provider_profile_id": claim.state["provider_profile_id"],
            "provider_profile_digest": claim.state["provider_profile_digest"],
            "prompt_release_id": claim.state["prompt_release_id"],
            "prompt_release_digest": claim.state["prompt_release_digest"],
            "output_schema_digest": manifest["proposal_schema_sha256"],
            "action_catalog_digest": claim.state["action_catalog_digest"],
            "data_classification": claim.state["data_classification"],
        }
        actor = self._initiating_actor(claim.state)
        envelope = self.service.submit(
            "ai.research.propose", payload, request_id=request_key, actor=actor,
            command_id="research.loop.create")
        job_id = str(envelope["data"]["job"]["id"])
        self.repository.link_job(run_id=claim.run_id, job_id=job_id,
                                 purpose="bounded research proposal")
        jobs = dict(claim.state["stage_jobs"])
        jobs["active"] = {"kind": "reason", "job_id": job_id,
                          "context_digest": claim.state["context_digest"],
                          "request_key": request_key}
        remaining["reasoner_calls"] = int(remaining["reasoner_calls"]) - 1
        spent = dict(claim.state["budget_spent"])
        spent["reasoner_calls"] = int(spent.get("reasoner_calls", 0)) + 1
        self.repository.transition(
            claim, expected_version=claim.version, stage="wait_job",
            event_type="reason_submitted", actor=self._automation_actor(),
            payload={"job_id": job_id, "request_key": request_key},
            updates={"stage_jobs": jobs, "budget_remaining": remaining,
                     "budget_spent": spent}, next_wake_seconds=1)

    def _stage_wait_job(self, claim: LoopClaim) -> None:
        active = (claim.state["stage_jobs"] or {}).get("active") or {}
        kind = active.get("kind")
        actor = self._initiating_actor(claim.state)
        if kind in {"reason", "command_job"}:
            job = self.service.get_job(str(active["job_id"]), actor=actor)
            if job["state"] in _ACTIVE_JOB_STATES:
                self.repository.transition(
                    claim, expected_version=claim.version, stage="wait_job",
                    event_type="job_polled", actor=self._automation_actor(),
                    payload={"job_id": active["job_id"], "state": job["state"]},
                    next_wake_seconds=2)
                return
            if job["state"] != "done":
                raise failures.DiracFailure(
                    str(job.get("error_code") or "INTERNAL"),
                    "research loop stage Job did not complete",
                    details={"job_id": active["job_id"], "state": job["state"]})
            next_stage = "validate_proposal" if kind == "reason" else "observe"
            updates = {}
            artifact_id = None
            if kind == "reason":
                proposal_ref = self._job_artifact(job, "research.proposal")
                _artifact, raw = self.store.read(proposal_ref["id"])
                proposal = json.loads(raw)
                artifact_id = proposal_ref["id"]
                self.repository.link_artifact(
                    run_id=claim.run_id, artifact_id=artifact_id,
                    role="research.proposal",
                    data_classification=claim.state["data_classification"])
                updates = {
                    "proposal_artifact_id": artifact_id,
                    "proposal_context_digest": bytes.fromhex(
                        proposal["context_digest"].removeprefix("sha256:")),
                }
                outputs = dict(claim.state["outputs"])
                outputs["proposal_artifact_sha256"] = proposal_ref["sha256"]
                updates["outputs"] = outputs
            self.repository.transition(
                claim, expected_version=claim.version, stage=next_stage,
                event_type=("proposal_completed" if kind == "reason" else "action_job_completed"),
                actor=self._automation_actor(), artifact_id=artifact_id,
                payload={"job_id": active["job_id"]}, updates=updates)
            return
        if kind == "runset":
            runset = self.fep.runsets.get(str(active["runset_id"]), actor)
            if runset["state"] in _ACTIVE_RUNSET_STATES:
                self.repository.transition(
                    claim, expected_version=claim.version, stage="wait_job",
                    event_type="runset_polled", actor=self._automation_actor(),
                    payload={"runset_id": active["runset_id"],
                             "state": runset["state"]}, next_wake_seconds=5)
                return
            if runset["state"] != "completed":
                raise failures.DiracInvalidParameters(
                    "RBFE RunSet did not complete",
                    details={"runset_id": active["runset_id"],
                             "state": runset["state"],
                             "attention": runset.get("attention")})
            self.repository.transition(
                claim, expected_version=claim.version, stage="observe",
                event_type="runset_completed", actor=self._automation_actor(),
                payload={"runset_id": active["runset_id"]})
            return
        raise failures.DiracInternal("wait_job stage has no durable active handle")

    def _stage_validate_proposal(self, claim: LoopClaim) -> None:
        context = self._read_state_artifact(claim.state, "context")
        proposal = self._read_state_artifact(claim.state, "proposal")
        if proposal["context_digest"] != context["digest"]:
            raise failures.DiracStalePreview("proposal context digest is stale")
        current = self.fep.snapshot(claim.state)["campaign_binding"]
        if current != context["campaign_binding"]:
            self.repository.transition(
                claim, expected_version=claim.version, stage="snapshot_context",
                event_type="proposal_stale", actor=self._automation_actor(),
                payload={"reason": "campaign_binding_changed"})
            return
        self.repository.transition(
            claim, expected_version=claim.version, stage="select_action",
            event_type="proposal_validated", actor=self._automation_actor())

    def _stage_select_action(self, claim: LoopClaim) -> None:
        context = self._read_state_artifact(claim.state, "context")
        proposal = self._read_state_artifact(claim.state, "proposal")
        preferred = next((row for row in proposal["candidate_actions"]
                          if row["proposal_action_id"] == proposal["preferred_action_id"]), None)
        if preferred is None:
            self.repository.transition(
                claim, expected_version=claim.version, stage="guard",
                event_type="no_action_selected", actor=self._automation_actor())
            return
        selected = dict(preferred)
        deferred_candidate = None
        if selected["template_id"] == "fep.run_selected_edge.v1":
            available = next((row for row in context["available_actions"]
                              if selected["subject_ref"] in row["subject_refs"]), None)
            if available and available["template_id"] == "fep.prepare_selected_edge.v1":
                deferred_candidate = dict(selected)
                selected["template_id"] = "fep.prepare_selected_edge.v1"
        outputs = dict(claim.state["outputs"])
        outputs["selected_candidate"] = selected
        if deferred_candidate is not None:
            outputs["deferred_candidate_after_prerequisite"] = deferred_candidate
        self.repository.transition(
            claim, expected_version=claim.version, stage="prepare_action",
            event_type="action_selected", actor=self._automation_actor(),
            payload={"proposal_action_id": selected["proposal_action_id"],
                     "template_id": selected["template_id"]},
            updates={"outputs": outputs})

    def _stage_prepare_action(self, claim: LoopClaim) -> None:
        context = self._read_state_artifact(claim.state, "context")
        proposal = self._read_state_artifact(claim.state, "proposal")
        selected = claim.state["outputs"].get("selected_candidate")
        if selected is not None:
            proposal = dict(proposal)
            proposal["candidate_actions"] = [
                dict(selected) if row["proposal_action_id"] == selected["proposal_action_id"]
                else row for row in proposal["candidate_actions"]]
        compiled = self.compiler.compile(loop=claim.state, context=context,
                                         proposal=proposal)
        preview_ref = self._store_artifact(
            claim.state, compiled.preview_bytes, "research.action_preview")
        pending = {
            "preview": dict(compiled.preview),
            "preview_artifact_id": preview_ref["id"],
            "preview_artifact_sha256": preview_ref["sha256"],
            "command_input": compiled.command_input,
        }
        approval = compiled.preview["consequence"]["approval"]
        state = "waiting_approval" if approval == "per_action" else "active"
        stage = "await_approval" if approval == "per_action" else "dispatch"
        self.repository.transition(
            claim, expected_version=claim.version, state=state, stage=stage,
            event_type=("approval_requested" if approval == "per_action"
                        else "action_previewed"),
            actor=self._automation_actor(), artifact_id=preview_ref["id"],
            payload={"action_fingerprint": compiled.preview["action_fingerprint"],
                     "risk_class": compiled.preview["consequence"]["risk_class"]},
            updates={"pending_action": pending})

    def _stage_dispatch(self, claim: LoopClaim) -> None:
        pending = claim.state.get("pending_action") or {}
        preview = pending.get("preview") or {}
        sources = self.fep.current_source_versions(claim.state, pending)
        self.compiler.revalidate(
            pending, loop=claim.state,
            current_context_digest=str(claim.state["context_digest"]),
            current_source_versions=sources,
            acknowledgements=list(pending.get("approved_acknowledgements") or []),
            actor=self._initiating_actor(claim.state))
        resolved = preview.get("resolved_command")
        if resolved is None:
            self.repository.transition(
                claim, expected_version=claim.version, state="completed", stage="completed",
                event_type="loop_completed", actor=self._automation_actor(),
                payload={"template_id": preview["template_id"],
                         "reason": "non_executing_action"})
            return
        attempt = int(claim.state["stage_attempts"].get("dispatch", 0))
        request_key = stage_request_key(
            claim.run_id, int(claim.state["iteration"]), "dispatch", attempt)
        envelope = self.dispatcher.execute(
            resolved["command_id"], dict(pending["command_input"]),
            actor=self._initiating_actor(claim.state), request_id=request_key)
        if not envelope["ok"]:
            error = envelope["error"]
            raise failures.DiracFailure(
                error["code"], error["message"], details=error.get("details"))
        data = envelope["data"]
        jobs = dict(claim.state["stage_jobs"])
        if resolved["command_id"] == "physics.rbfe-run.start":
            runset_id = str(data["ref"]["id"])
            jobs["active"] = {"kind": "runset", "runset_id": runset_id,
                              "template_id": preview["template_id"],
                              "action_fingerprint": preview["action_fingerprint"],
                              "subject_ref": preview["subject_ref"]}
            payload = {"runset_id": runset_id}
        else:
            job_id = str(data["job"]["id"])
            self.repository.link_job(run_id=claim.run_id, job_id=job_id,
                                     purpose=preview["template_id"])
            jobs["active"] = {"kind": "command_job", "job_id": job_id,
                              "template_id": preview["template_id"],
                              "action_fingerprint": preview["action_fingerprint"],
                              "subject_ref": preview["subject_ref"]}
            payload = {"job_id": job_id}
        self.repository.transition(
            claim, expected_version=claim.version, stage="wait_job",
            event_type="action_dispatched", actor=self._automation_actor(),
            payload={**payload, "template_id": preview["template_id"],
                     "executed_under": claim.state["actor_id"],
                     "automation_actor": self.instance_id},
            updates={"stage_jobs": jobs}, next_wake_seconds=1)

    def _stage_observe(self, claim: LoopClaim) -> None:
        active = claim.state["stage_jobs"]["active"]
        outputs = dict(claim.state["outputs"])
        outputs.setdefault("action_receipts", []).append(dict(active))
        if (active.get("template_id") == "fep.prepare_selected_edge.v1"
                and outputs.get("deferred_candidate_after_prerequisite")):
            outputs["selected_candidate"] = outputs.pop(
                "deferred_candidate_after_prerequisite")
            outputs["resume_candidate_after_snapshot"] = True
        remaining = dict(claim.state["budget_remaining"])
        spent = dict(claim.state["budget_spent"])
        if active.get("kind") == "runset":
            remaining["fep_runsets"] = max(0, int(remaining.get("fep_runsets", 0)) - 1)
            spent["fep_runsets"] = int(spent.get("fep_runsets", 0)) + 1
        self.repository.transition(
            claim, expected_version=claim.version, stage="refresh",
            event_type="action_completed", actor=self._automation_actor(),
            payload=dict(active),
            updates={"outputs": outputs, "budget_remaining": remaining,
                     "budget_spent": spent, "pending_action": None})

    def _stage_refresh(self, claim: LoopClaim) -> None:
        remaining = dict(claim.state["budget_remaining"])
        if int(remaining.get("iterations", 1)) <= 0:
            self.repository.transition(
                claim, expected_version=claim.version, stage="guard",
                event_type="iteration_budget_exhausted", actor=self._automation_actor())
            return
        remaining["iterations"] = int(remaining.get("iterations", 1)) - 1
        self.repository.transition(
            claim, expected_version=claim.version, stage="snapshot_context",
            event_type="context_refresh_requested", actor=self._automation_actor(),
            updates={"iteration": int(claim.state["iteration"]) + 1,
                     "budget_remaining": remaining})

    def _stage_guard(self, claim: LoopClaim) -> None:
        self.repository.transition(
            claim, expected_version=claim.version, state="completed", stage="completed",
            event_type="loop_completed", actor=self._automation_actor(),
            payload={"reason": "no_valid_information_gaining_action"})

    def _stage_await_approval(self, claim: LoopClaim) -> None:
        raise failures.DiracInternal("waiting_approval loop must not be controller-claimable")

    def _stage_completed(self, claim: LoopClaim) -> None:
        raise failures.DiracInternal("completed loop must not be controller-claimable")

    def _block_claim(self, claim: LoopClaim, error: Exception) -> None:
        if isinstance(error, failures.DiracFailure):
            detail = {"code": error.code, "message": error.message,
                      "details": error.details}
        else:
            detail = {"code": "INTERNAL", "message": f"{type(error).__name__}: {error}"}
        active = (claim.state.get("stage_jobs") or {}).get("active") or {}
        retry_stage = claim.state["stage"]
        if retry_stage == "wait_job":
            retry_stage = "reason" if active.get("kind") == "reason" else "dispatch"
        detail["retry_stage"] = retry_stage
        try:
            self.repository.transition(
                claim, expected_version=claim.version, state="blocked",
                stage=claim.state["stage"], event_type="loop_blocked",
                actor=self._automation_actor(), payload=detail,
                updates={"attention": detail})
        except Exception:
            pass

    def _store_artifact(self, state: Mapping[str, Any], raw: bytes,
                        role: str) -> dict[str, str]:
        art = self.store.put(raw, role=role, media_type="application/json")
        self.repository.link_artifact(
            run_id=str(state["run_id"]), artifact_id=art.id, role=role,
            data_classification=str(state["data_classification"]))
        return {"kind": "artifact", "id": art.id, "sha256": "sha256:" + art.sha256}

    def _read_state_artifact(self, state: Mapping[str, Any], which: str) -> dict[str, Any]:
        identifier = state[f"{which}_artifact_id"]
        if identifier is None:
            raise failures.DiracInternal(f"loop has no {which} Artifact")
        artifact, raw = self.store.read(str(identifier))
        expected_role = f"research.{which if which != 'context' else 'context_snapshot'}"
        if artifact.role != expected_role:
            raise failures.DiracInternal(f"loop {which} Artifact has the wrong role")
        return json.loads(raw)

    @staticmethod
    def _job_artifact(job: Mapping[str, Any], role: str) -> dict[str, str]:
        row = next((item for item in job.get("artifacts") or []
                    if item.get("role") == role), None)
        if row is None:
            raise failures.DiracInternal(f"terminal Job omitted required {role} Artifact")
        digest = str(row["sha256"])
        return {"kind": "artifact", "id": str(row["id"]),
                "sha256": digest if digest.startswith("sha256:") else "sha256:" + digest}

    @staticmethod
    def _state_artifact_ref(state: Mapping[str, Any], which: str) -> dict[str, str] | None:
        identifier = state.get(f"{which}_artifact_id")
        digest = (state.get("outputs") or {}).get(f"{which}_artifact_sha256")
        if identifier is None:
            return None
        return {"kind": "artifact", "id": str(identifier),
                **({"sha256": str(digest)} if digest else {})}

    def _automation_actor(self) -> dict[str, str]:
        return {"kind": "service", "id": self.instance_id}

    @staticmethod
    def _initiating_actor(state: Mapping[str, Any]) -> dict[str, str]:
        return {"kind": str(state["actor_kind"]), "id": str(state["actor_id"])}
