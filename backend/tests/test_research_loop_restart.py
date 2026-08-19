from __future__ import annotations

import os
import threading
import unittest
import uuid

from research.loop_repository import ResearchLoopRepository

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


@unittest.skipUnless(os.environ.get("DIRAC_TEST_DSN") and psycopg,
                     "requires isolated PostgreSQL DIRAC_TEST_DSN with migrations 000-049")
class ResearchLoopRestartMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ["DIRAC_TEST_DSN"]
        cls.connect = staticmethod(lambda: psycopg.connect(cls.dsn))
        with cls.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            database = str(cur.fetchone()[0]).lower()
            if "test" not in database:
                raise RuntimeError("restart matrix refuses a database without 'test' in its name")
            cur.execute(
                "UPDATE app.research_loop_state SET state='cancelled',stage='completed',"
                "lease_owner=NULL,lease_expires_at=NULL,finished_at=now() WHERE state IN "
                "('active','waiting_approval','blocked','paused')")
            code = "RESTART-" + uuid.uuid4().hex[:10]
            cur.execute(
                "INSERT INTO design.project(code,name) VALUES (%s,%s) RETURNING id",
                (code, "Research-loop restart matrix"))
            cls.program_id = str(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO design.campaign "
                "(program_id,name,objective,created_by_kind,created_by_id) "
                "VALUES (%s,%s,%s,'human',%s) RETURNING id",
                (cls.program_id, code, "restart recovery", code))
            cls.campaign_id = str(cur.fetchone()[0])
        cls.actor = {"kind": "human", "id": code}
        cls.repository = ResearchLoopRepository(cls.connect)
        created = cls.repository.create(
            request_key="restart-matrix", program_id=cls.program_id,
            campaign_id=cls.campaign_id, actor=cls.actor,
            intent="Recover every durable controller checkpoint", autonomy_class="A2",
            provider_profile_id="qwen-test", provider_profile_digest=b"p" * 32,
            prompt_release_id="fep-action-proposal-v1", prompt_release_digest=b"r" * 32,
            action_catalog_digest=b"a" * 32, data_classification="internal",
            policy={"cloud_egress_approved": False},
            budget_remaining={"reasoner_calls": 8, "fep_runsets": 3,
                              "gpu_hours": 12, "external_cost": 0, "iterations": 8},
        )
        cls.run_id = created["run_id"]

    def _crash_at(self, stage: str, *, state: str = "active",
                  stage_jobs: str = "{}", pending_action: str | None = None) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE app.research_loop_state SET state=%s,stage=%s,version=version+1,"
                "stage_jobs=%s::jsonb,pending_action=%s::jsonb,next_wake_at=now(),"
                "lease_owner='dead-controller',lease_expires_at=now()-interval '1 second' "
                "WHERE run_id=%s",
                (state, stage, stage_jobs, pending_action, self.run_id))

    def _release(self) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE app.research_loop_state SET lease_owner=NULL,lease_expires_at=NULL "
                "WHERE run_id=%s", (self.run_id,))

    def test_new_repository_claims_every_runnable_stage_after_expired_lease(self):
        stages = (
            "bootstrap", "snapshot_context", "reason", "validate_proposal",
            "select_action", "prepare_action", "dispatch", "wait_job",
            "observe", "refresh", "guard",
        )
        for stage in stages:
            with self.subTest(stage=stage):
                jobs = ('{"active":{"kind":"reason","job_id":"durable-job"}}'
                        if stage == "wait_job" else "{}")
                self._crash_at(stage, stage_jobs=jobs)
                restarted = ResearchLoopRepository(self.connect)
                claim = restarted.claim(owner="controller-after-restart")
                self.assertIsNotNone(claim)
                self.assertEqual((claim.run_id, claim.state["stage"]),
                                 (self.run_id, stage))
                self._release()

    def test_preview_waits_for_human_across_restart_and_is_not_controller_claimed(self):
        pending = ('{"preview":{"template_id":"fep.run_selected_edge.v1",'
                   '"action_fingerprint":"sha256:' + 'f' * 64 + '"}}')
        self._crash_at("await_approval", state="waiting_approval",
                       pending_action=pending)
        restarted = ResearchLoopRepository(self.connect)
        self.assertIsNone(restarted.claim(owner="must-not-claim-human-wait"))
        state = restarted.get(self.run_id, actor=self.actor)
        self.assertEqual(state["state"], "waiting_approval")
        self.assertEqual(state["pending_action"]["preview"]["template_id"],
                         "fep.run_selected_edge.v1")

    def test_two_controllers_compete_with_skip_locked_and_only_one_wins(self):
        self._crash_at("reason")
        barrier = threading.Barrier(2)
        results = []

        def compete(owner: str) -> None:
            barrier.wait()
            results.append(ResearchLoopRepository(self.connect).claim(owner=owner))

        threads = [threading.Thread(target=compete, args=(f"controller-{index}",))
                   for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(sum(result is not None for result in results), 1)
        self._release()


if __name__ == "__main__":
    unittest.main()
