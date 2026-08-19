from __future__ import annotations

import hashlib
import os
import pathlib
import unittest
import uuid

import failures
from research.loop_repository import ResearchLoopRepository

try:
    import psycopg2
except ImportError:  # pragma: no cover - integration environment decides
    psycopg2 = None


ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/db/migrations/049_research_loop.sql"


class ResearchLoopMigrationContractTests(unittest.TestCase):
    def test_migration_contains_the_attachment_baseline_and_production_additions(self):
        source = MIGRATION.read_text(encoding="utf-8")
        for table in (
            "app.research_loop_state", "app.research_loop_event",
            "app.research_loop_approval", "app.research_loop_artifact",
        ):
            self.assertIn(f"CREATE TABLE {table}", source)
        self.assertIn("research_loop_one_open_per_campaign", source)
        self.assertIn("research_loop_runnable_idx", source)
        self.assertIn("research_loop_lease_idx", source)
        self.assertIn("UNIQUE (actor_kind, actor_id, request_key)", source)
        self.assertIn("ADD VALUE IF NOT EXISTS 'PROVIDER_UNAVAILABLE'", source)
        self.assertIn("ADD VALUE IF NOT EXISTS 'MODEL_OUTPUT_INVALID'", source)
        self.assertNotIn("ADD VALUE IF NOT EXISTS 'STALE_PREVIEW'", source)
        self.assertIn("'049_research_loop.sql'", source)
        self.assertIn("'content'", source)

    def test_recorded_migration_hash_matches_normalized_file(self):
        source = MIGRATION.read_text(encoding="utf-8")
        marker = "VALUES ('049_research_loop.sql','\\x"
        recorded = source.split(marker, 1)[1][:64]
        normalized = source.replace(recorded, "PENDING")
        self.assertEqual(hashlib.sha256(normalized.encode()).hexdigest(), recorded)


@unittest.skipUnless(os.environ.get("DIRAC_TEST_DSN") and psycopg2,
                     "requires isolated PostgreSQL DIRAC_TEST_DSN with migrations 000-049")
class ResearchLoopRepositoryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ["DIRAC_TEST_DSN"]
        cls.connect = staticmethod(lambda: psycopg2.connect(cls.dsn))
        cls.repository = ResearchLoopRepository(cls.connect)
        cls.actor = {"kind": "human", "id": "chemist-loop-test"}
        with cls.connect() as conn, conn.cursor() as cur:
            code = "RL-" + uuid.uuid4().hex[:12]
            cur.execute(
                "INSERT INTO design.project(code,name) VALUES (%s,%s) RETURNING id",
                (code, "Research loop repository test"),
            )
            cls.program_id = str(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO design.campaign "
                "(program_id,name,objective,created_by_kind,created_by_id) "
                "VALUES (%s,%s,%s,'human',%s) RETURNING id",
                (cls.program_id, "FEP " + code, "close one FEP evidence gap",
                 cls.actor["id"]),
            )
            cls.campaign_id = str(cur.fetchone()[0])

    def _create(self):
        return self.repository.create(
            request_key="repository-integration-create",
            program_id=self.program_id,
            campaign_id=self.campaign_id,
            actor=self.actor,
            intent="Select the next FEP edge that maximally reduces uncertainty",
            autonomy_class="A1",
            provider_profile_id="qwen-test",
            provider_profile_digest=b"p" * 32,
            prompt_release_id="fep-action-proposal-v1",
            prompt_release_digest=b"r" * 32,
            action_catalog_digest=b"a" * 32,
            data_classification="internal",
            policy={"cloud_egress": False, "max_iterations": 3},
            budget_remaining={"fep_edges": 2, "provider_calls": 2},
        )

    def test_durable_create_lease_transition_approval_and_restart_read(self):
        created = self._create()
        replay = self._create()
        self.assertEqual(replay["run_id"], created["run_id"])
        with self.assertRaises(failures.DiracIdempotencyConflict):
            self.repository.create(
                request_key="repository-integration-create",
                program_id=self.program_id, campaign_id=self.campaign_id,
                actor=self.actor, intent="different intent", autonomy_class="A1",
                provider_profile_id="qwen-test", provider_profile_digest=b"p" * 32,
                prompt_release_id="fep-action-proposal-v1",
                prompt_release_digest=b"r" * 32,
                action_catalog_digest=b"a" * 32,
                data_classification="internal", policy={"cloud_egress": False},
                budget_remaining={"fep_edges": 2},
            )
        self.assertEqual(created["stage"], "bootstrap")
        self.assertEqual(created["version"], 1)

        claim = self.repository.claim(owner="loop-controller-test")
        self.assertIsNotNone(claim)
        moved = self.repository.transition(
            claim, expected_version=1, stage="snapshot_context",
            event_type="context_snapshotted", actor={"kind": "service", "id": "controller"},
            payload={"source": "integration-test"}, next_wake_seconds=0,
        )
        self.assertEqual(moved["version"], 2)
        self.assertIsNone(moved["lease_owner"])

        with self.assertRaises(failures.DiracInvalidParameters):
            self.repository.transition(
                claim, expected_version=1, stage="reason", event_type="stale",
                actor={"kind": "service", "id": "controller"},
            )

        second = self.repository.claim(owner="loop-controller-after-restart")
        self.assertEqual(second.run_id, created["run_id"])
        waiting = self.repository.transition(
            second, expected_version=2, stage="await_approval",
            state="waiting_approval", event_type="action_previewed",
            actor={"kind": "service", "id": "controller"},
            updates={"pending_action": {"template_id": "fep.run_selected_edge.v1"}},
        )
        self.assertEqual(waiting["version"], 3)

        preview = b'{"preview":"bounded"}'
        preview_digest = hashlib.sha256(preview).digest()
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.blob(sha256,media_type,byte_len,bytes) "
                "VALUES (%s,'application/json',%s,%s) ON CONFLICT DO NOTHING",
                (preview_digest, len(preview), preview),
            )
            cur.execute(
                "INSERT INTO app.artifact(blob_sha256,media_type,role,size_bytes) "
                "VALUES (%s,'application/json','research.action_preview',%s) "
                "RETURNING id", (preview_digest, len(preview)),
            )
            preview_artifact_id = str(cur.fetchone()[0])
        self.repository.link_artifact(
            run_id=created["run_id"], artifact_id=preview_artifact_id,
            role="research.action_preview", data_classification="internal",
        )
        approved = self.repository.decide(
            run_id=created["run_id"], expected_version=3,
            action_fingerprint=b"f" * 32,
            preview_artifact_id=preview_artifact_id,
            command_input_digest=b"i" * 32,
            source_versions={"campaign_version": 7, "network_digest": "sha256:" + "1" * 64},
            decision="approved", actor=self.actor, rationale="Run this bounded edge",
        )
        self.assertEqual((approved["state"], approved["stage"], approved["version"]),
                         ("active", "dispatch", 4))

        changed_provider = self.repository.control(
            run_id=created["run_id"], expected_version=4,
            action="change_provider", actor=self.actor,
            rationale="Use the approved internal provider",
            provider_profile_id="qwen-internal",
            provider_profile_digest=b"n" * 32,
        )
        self.assertEqual(changed_provider["stage"], "snapshot_context")
        self.assertEqual(changed_provider["provider_profile_id"], "qwen-internal")
        self.assertIsNone(changed_provider["pending_action"])

        events = self.repository.events(created["run_id"], actor=self.actor)
        self.assertEqual([event["sequence"] for event in events], list(range(len(events))))
        self.assertEqual(events[0]["event_type"], "loop_created")
        self.assertEqual(events[-1]["event_type"], "loop_provider_changed")
        progressed_replay = self._create()
        self.assertEqual(progressed_replay["version"], 5)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM app.research_loop_artifact la "
                "JOIN app.research_loop_state ls ON ls.run_id=la.run_id "
                "WHERE la.artifact_id=%s AND ls.actor_kind='human' AND ls.actor_id=%s",
                (preview_artifact_id, self.actor["id"]),
            )
            self.assertEqual(cur.fetchone()[0], 1)

    def test_stage_request_key_is_attempt_scoped(self):
        from research.loop_repository import stage_request_key
        first = stage_request_key(str(uuid.uuid4()), 2, "reason", 0)
        self.assertTrue(first.endswith(":2:reason:0"))
        self.assertNotEqual(first, first[:-1] + "1")
