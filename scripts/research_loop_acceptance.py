#!/usr/bin/env python3
"""Isolated fake-provider/fake-RunSet acceptance for the governed FEP loop.

The command, Method, PostgreSQL checkpoint, Job, Artifact, approval and provider
HTTP paths are real.  Only the expensive OpenFE RunSet is replaced by an
explicitly named deterministic fake, as required by the browser acceptance path.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

from scripts.research_loop_fake_provider import FakeProviderServer, Handler


ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLE = json.loads(
    (ROOT / "deploy/ai/providers.example.json").read_text(encoding="utf-8"))
ACTOR = {"kind": "human", "id": "research-loop-acceptance"}
EDGE = {"kind": "free_energy_transformation", "id": "edge-c2-c7"}
SHA = "sha256:" + "d" * 64


def ref(role: str = "artifact") -> dict[str, object]:
    return {"kind": role, "id": str(uuid.uuid4()), "sha256": SHA}


class FakeRunSets:
    def __init__(self) -> None:
        self.by_key: dict[str, dict] = {}
        self.by_id: dict[str, dict] = {}
        self.starts = 0

    def start(self, payload: dict, actor: dict) -> dict:
        key = f"{actor['kind']}:{actor['id']}:{payload['request_key']}"
        if key in self.by_key:
            return self.by_key[key]
        self.starts += 1
        run_id = str(uuid.uuid4())
        result = {
            "ref": {"kind": "run", "id": run_id}, "state": "completed",
            "jobs": [], "edge_id": EDGE["id"],
            "edge_spec_ref": payload["edge_spec_ref"],
            "edge_network_ref": payload["edge_network_ref"],
            "complex_transformation_ref": payload["complex_transformation_ref"],
            "solvent_transformation_ref": payload["solvent_transformation_ref"],
            "campaign_scientific_ref": {
                "kind": "rbfe_campaign", "id": payload["campaign_id"],
                "version": payload["campaign_scientific_generation"],
                "sha256": payload["campaign_scientific_digest"],
            },
            "aggregate_output": {
                "claim_boundary": "completed_unvalidated",
                "estimate": -1.2, "uncertainty": 0.3, "unit": "kcal/mol",
            },
        }
        self.by_key[key] = result
        self.by_id[run_id] = result
        return result

    def get(self, run_id: str, actor: dict) -> dict:
        if actor != ACTOR or run_id not in self.by_id:
            raise KeyError(run_id)
        return self.by_id[run_id]

    def cancel(self, run_id: str, actor: dict) -> dict:
        return self.get(run_id, actor)

    def retry(self, run_id: str, actor: dict) -> dict:
        return self.get(run_id, actor)


class FakeFep:
    def __init__(self, runsets: FakeRunSets, campaign_id: str) -> None:
        self.runsets = runsets
        self.campaign_id = campaign_id
        self.campaign_binding = {
            "campaign_scientific_generation": 1,
            "campaign_scientific_digest": "sha256:" + (b"b" * 32).hex(),
            "campaign_status": "planned",
            "state_digest": "sha256:" + (b"c" * 32).hex(),
        }
        self.refs = {
            "edge_spec_ref": ref(), "edge_network_ref": ref(),
            "complex_transformation_ref": ref(), "solvent_transformation_ref": ref(),
        }

    def assess_bootstrap(self, _loop: dict) -> dict:
        return {"ready": True, "reason_code": "FAKE_GPU_BOUND_ACCEPTANCE",
                "gpu_execution": True}

    def snapshot(self, loop: dict) -> dict:
        receipts = list((loop.get("outputs") or {}).get("action_receipts") or [])
        completed = bool(receipts)
        facts = []
        if completed:
            facts.append({
                "fact_id": "fact:edge-c2-c7:completed-unvalidated",
                "category": "rbfe_result", "source_class": "method_result",
                "source_ref": ref(), "subject_ref": EDGE, "condition_ref": None,
                "structured_value": {"estimate": -1.2, "uncertainty": 0.3,
                                     "unit": "kcal/mol"},
                "freshness": {"stale": False, "source_generation": 1},
                "claim_boundary": {
                    "status": "completed_unvalidated",
                    "eligible_as_scientific_evidence": False,
                    "reason_codes": ["METHOD_RESULT_NOT_EVIDENCE",
                                     "QUALITY_PROJECTION_REQUIRED"],
                },
            })
        actions = [{
            "template_id": "fep.stop.v1",
            "subject_refs": [{"kind": "campaign", "id": self.campaign_id}],
            "intent": "Stop with an explicit governed receipt.", "risk_class": "R0",
        }]
        if not completed:
            actions.insert(0, {
                "template_id": "fep.run_selected_edge.v1", "subject_refs": [EDGE],
                "intent": "Run the qualified edge.", "risk_class": "R3",
            })
        return {
            "campaign_binding": dict(self.campaign_binding),
            "objects": [{"ref": EDGE, "label": "C2 to C7",
                         "state": {"eligible": True}}],
            "facts": facts, "human_attestations": [],
            "action_history": [
                {"action_fingerprint": row.get("action_fingerprint"),
                 "template_id": row.get("template_id"),
                 "subject_ref": row.get("subject_ref"), "result": "completed",
                 "human_rejected": False}
                for row in receipts
            ],
            "available_actions": actions, "open_attention": [],
            "goal_constraints": ["Model proposals are not Evidence."],
            "success_definition": ["One R3 result changes the next action or stops."],
            "source_clock": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    def resolve(self, *, template_id: str, candidate: dict,
                loop: dict, context: dict) -> dict:
        versions = self.current_source_versions(loop, {})
        if template_id == "fep.stop.v1":
            return {"command_input": None, "source_versions": versions,
                    "estimate": {"available": True, "gpu_hours_upper_bound": 0,
                                 "external_cost_upper_bound": 0}}
        if candidate.get("subject_ref") != EDGE:
            raise ValueError("acceptance candidate did not resolve to the known edge")
        return {
            "command_input": {
                "request_key": (
                    f"research-loop:{loop['run_id']}:{loop['iteration']}:dispatch:0"),
                "campaign_id": self.campaign_id,
                "campaign_scientific_generation": 1,
                "campaign_scientific_digest": self.campaign_binding[
                    "campaign_scientific_digest"],
                **self.refs,
            },
            "source_versions": versions,
            "estimate": {"available": True, "gpu_hours_upper_bound": 1.0,
                         "external_cost_upper_bound": 0},
            "consequence_summary": "Runs one fake six-leg governed acceptance RunSet.",
        }

    def current_source_versions(self, _loop: dict, _pending: dict) -> dict:
        return {"campaign_scientific_generation": 1,
                "campaign_scientific_digest": self.campaign_binding[
                    "campaign_scientific_digest"],
                "state_digest": self.campaign_binding["state_digest"],
                "edge_spec_digest": SHA}


def wait_state(dispatcher, run_id: str, states: set[str], timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        envelope = dispatcher.execute(
            "research.loop.get", {"run_ref": {"kind": "run", "id": run_id}},
            actor=ACTOR)
        if not envelope["ok"]:
            raise RuntimeError(envelope)
        state = envelope["data"]
        if state["state"] in states:
            return state
        time.sleep(0.05)
    raise TimeoutError(f"loop did not reach {sorted(states)}")


def seed(connect) -> tuple[str, str]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        if "test" not in str(cur.fetchone()[0]).lower():
            raise RuntimeError("acceptance refuses a database without 'test' in its name")
        cur.execute(
            "UPDATE app.research_loop_state SET state='cancelled',stage='completed',"
            "finished_at=coalesce(finished_at,now()),lease_owner=NULL,lease_expires_at=NULL "
            "WHERE state IN ('active','waiting_approval','blocked','paused')")
        code = "ACCEPT-" + uuid.uuid4().hex[:10]
        cur.execute("INSERT INTO design.project(code,name) VALUES (%s,%s) RETURNING id",
                    (code, "AI research loop acceptance"))
        program_id = str(cur.fetchone()[0])
        campaign_id = str(uuid.uuid4())
        state = {
            "schema_version": 1, "scientific_generation": 1,
            "scientific_digest": "sha256:" + (b"b" * 32).hex(),
            "client_state": {"schema_version": 2, "name": code},
        }
        cur.execute(
            "INSERT INTO app.rbfe_campaign "
            "(id,status,state,state_digest,scientific_generation,scientific_digest,"
            "created_by_kind,created_by_id) VALUES "
            "(%s,'planned',%s::jsonb,%s,1,%s,'human',%s)",
            (campaign_id, json.dumps(state), b"c" * 32, b"b" * 32, ACTOR["id"]))
    return program_id, campaign_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.environ.get("DIRAC_TEST_DSN", ""))
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("--dsn or DIRAC_TEST_DSN is required")

    provider_server = FakeProviderServer(("127.0.0.1", 0), Handler, mode="action")
    provider_thread = threading.Thread(target=provider_server.serve_forever, daemon=True)
    provider_thread.start()
    profile = copy.deepcopy(EXAMPLE["profiles"][1])
    profile["model"] = "Qwen/Fake-Acceptance"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as config:
        json.dump({"profiles": [profile]}, config); config.flush()
        os.environ.update({
            "DIRAC_DSN": args.dsn, "DIRAC_EXECUTOR": "thread",
            "DIRAC_AI_PROVIDER_CONFIG": config.name,
            "DIRAC_LOCAL_QWEN_BASE_URL": (
                f"http://127.0.0.1:{provider_server.server_port}/v1"),
            "DIRAC_LOCAL_QWEN_API_KEY": "acceptance-secret-never-persist",
        })
        import psycopg
        connect = lambda: psycopg.connect(args.dsn)
        program_id, campaign_id = seed(connect)

        import kernel
        from dirac_app.dispatcher import CommandDispatcher
        from research.loop_controller import ResearchLoopController
        from research.loop_repository import ResearchLoopRepository

        service = kernel.build(dsn=args.dsn, production_execution=False)
        previous = getattr(service, "research_loop_controller", None)
        if previous is not None:
            previous.shutdown()
        runsets = FakeRunSets()
        service.rbfe_runset_controller = runsets
        fep = FakeFep(runsets, campaign_id)
        controller = ResearchLoopController(
            repository=ResearchLoopRepository(connect), service=service,
            artifact_store=service.store, provider_registry=service.ai_provider_registry,
            fep_adapter=fep, kernel=service, instance_id="acceptance-controller")
        service.research_loop_controller = controller
        dispatcher = CommandDispatcher(service)
        try:
            request_key = f"acceptance-loop:{campaign_id}"
            created = dispatcher.execute("research.loop.create", {
                "request_key": request_key,
                "program_ref": {"kind": "program", "id": program_id},
                "campaign_ref": {"kind": "campaign", "id": campaign_id},
                "intent": "Acquire one governed FEP result, then stop explicitly.",
                "autonomy_class": "A2", "provider_profile_id": "qwen-local-isolated",
                "data_classification": "internal",
                "budget": {"max_reasoner_calls": 4, "max_iterations": 4,
                           "max_fep_runsets": 1, "max_gpu_hours": 2,
                           "max_external_cost": 0},
                "policy": {"auto_risk_classes": ["R0", "R1", "R2"],
                           "per_action_risk_classes": ["R3"],
                           "human_only_risk_classes": ["R4"],
                           "stop_on_campaign_stale": True,
                           "stop_on_open_identity_conflict": True,
                           "max_same_subject_actions": 1,
                           "cloud_egress_approved": False},
            }, actor=ACTOR, request_id=request_key)
            if not created["ok"]:
                raise RuntimeError(created)
            run_id = created["data"]["run_ref"]["id"]
            waiting = wait_state(dispatcher, run_id, {"waiting_approval", "blocked"})
            if waiting["state"] != "waiting_approval":
                raise RuntimeError(waiting)
            preview = waiting["pending_action"]["preview"]
            approved = dispatcher.execute("research.loop.approve", {
                "run_ref": {"kind": "run", "id": run_id},
                "expected_version": waiting["version"],
                "action_fingerprint": preview["action_fingerprint"],
                "acknowledgements": preview["required_acknowledgements"],
                "rationale": "The exact edge resolves the acceptance decision gap.",
            }, actor=ACTOR)
            if not approved["ok"]:
                raise RuntimeError(approved)
            terminal = wait_state(dispatcher, run_id, {"completed", "blocked"})
            if terminal["state"] != "completed":
                raise RuntimeError(terminal)
            reloaded = CommandDispatcher(service).execute(
                "research.loop.get", {"run_ref": {"kind": "run", "id": run_id}},
                actor=ACTOR)["data"]
            events = [row["event_type"] for row in reloaded["events"]]
            required = {"loop_created", "reason_submitted", "approval_requested",
                        "action_approved", "action_dispatched", "action_completed",
                        "context_refresh_requested", "loop_completed"}
            missing = sorted(required - set(events))
            if missing or runsets.starts != 1 or provider_server.attempts != 3:
                raise RuntimeError({"missing_events": missing,
                                    "runset_starts": runsets.starts,
                                    "provider_attempts": provider_server.attempts})
            summary_ref = reloaded.get("summary_ref")
            if not summary_ref or not summary_ref.get("sha256"):
                raise RuntimeError("completed loop omitted its immutable summary Artifact")
            summary_artifact, summary_raw = service.store.read(summary_ref["id"])
            summary = json.loads(summary_raw)
            if summary_artifact.role != "research.loop_summary":
                raise RuntimeError("terminal summary Artifact has the wrong role")
            expected_boundary = {
                "status": "completed_unvalidated",
                "eligible_as_scientific_evidence": False,
                "reason_codes": ["METHOD_RESULT_NOT_EVIDENCE",
                                 "QUALITY_PROJECTION_REQUIRED"],
            }
            if (summary["source_classes"] != ["method_result"]
                    or len(summary["claims"]) != 1
                    or summary["claims"][0]["source_class"] != "method_result"
                    or summary["claims"][0]["claim_boundary"] != expected_boundary):
                raise RuntimeError({"invalid_terminal_summary": summary})
            serialized = json.dumps(reloaded, sort_keys=True)
            if "acceptance-secret-never-persist" in serialized:
                raise RuntimeError("provider secret reached the public durable snapshot")
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT row_to_json(ls)::text FROM app.research_loop_state ls "
                    "WHERE run_id=%s UNION ALL "
                    "SELECT row_to_json(le)::text FROM app.research_loop_event le "
                    "WHERE run_id=%s UNION ALL "
                    "SELECT row_to_json(la)::text FROM app.research_loop_artifact la "
                    "WHERE run_id=%s UNION ALL "
                    "SELECT row_to_json(j)::text FROM app.job j "
                    "JOIN app.run_job rj ON rj.job_id=j.id WHERE rj.run_id=%s",
                    (run_id, run_id, run_id, run_id),
                )
                durable_text = "\n".join(str(row[0]) for row in cur.fetchall())
                cur.execute(
                    "SELECT b.bytes,a.metadata::text FROM app.research_loop_artifact la "
                    "JOIN app.artifact a ON a.id=la.artifact_id "
                    "JOIN app.blob b ON b.sha256=a.blob_sha256 WHERE la.run_id=%s",
                    (run_id,),
                )
                for raw, metadata in cur.fetchall():
                    durable_text += bytes(raw).decode("utf-8", errors="replace")
                    durable_text += str(metadata)
            if "acceptance-secret-never-persist" in durable_text:
                raise RuntimeError("provider secret reached a PostgreSQL or Artifact surface")
            print(json.dumps({
                "ok": True, "run_id": run_id, "state": reloaded["state"],
                "stage": reloaded["stage"], "provider_attempts": provider_server.attempts,
                "fake_runset_starts": runsets.starts, "events": events,
                "claim_boundary": reloaded["claim_boundary"],
                "summary_sha256": summary_ref["sha256"],
                "timeline_survived_new_dispatcher": True,
                "physical_execution": "FAKE_RUNSET_ONLY",
            }, sort_keys=True))
        finally:
            controller.shutdown()
            shutdown = getattr(service.executor, "shutdown", None)
            if callable(shutdown):
                shutdown()
    provider_server.shutdown(); provider_server.server_close()
    provider_thread.join(timeout=2)


if __name__ == "__main__":
    main()
