from __future__ import annotations

import json
import os
import pathlib
import tempfile
import unittest
import uuid

import failures
from artifacts_pg import PostgresArtifactStore
from research.loop_repository import ResearchLoopRepository
from research.provider_registry import FileAiProviderRegistry

try:
    import psycopg
except ImportError:  # pragma: no cover - integration environment decides
    psycopg = None


ROOT = pathlib.Path(__file__).resolve().parents[2]
EXAMPLE = json.loads(
    (ROOT / "deploy/ai/providers.example.json").read_text(encoding="utf-8")
)


@unittest.skipUnless(os.environ.get("DIRAC_TEST_DSN") and psycopg,
                     "requires isolated PostgreSQL DIRAC_TEST_DSN with migrations 000-050")
class ResearchLoopSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ["DIRAC_TEST_DSN"]
        cls.connect = staticmethod(lambda: psycopg.connect(cls.dsn))
        with cls.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            if "test" not in str(cur.fetchone()[0]).lower():
                raise RuntimeError("security tests refuse a database without 'test' in its name")
            code = "SEC-" + uuid.uuid4().hex[:10]
            cls.actor = {"kind": "human", "id": code}
            cur.execute(
                "INSERT INTO design.project(code,name) VALUES (%s,%s) RETURNING id",
                (code, "Research-loop security test"),
            )
            cls.program_id = str(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO design.campaign "
                "(program_id,name,objective,created_by_kind,created_by_id) "
                "VALUES (%s,%s,%s,'human',%s) RETURNING id",
                (cls.program_id, code, "verify private loop surfaces", code),
            )
            cls.campaign_id = str(cur.fetchone()[0])
        cls.repository = ResearchLoopRepository(cls.connect)
        cls.loop = cls.repository.create(
            request_key="security-loop", program_id=cls.program_id,
            campaign_id=cls.campaign_id, actor=cls.actor,
            intent="Verify tenant isolation and provider-secret redaction",
            autonomy_class="A2", provider_profile_id="qwen-local-isolated",
            provider_profile_digest=b"p" * 32,
            prompt_release_id="fep-action-proposal-v1",
            prompt_release_digest=b"r" * 32,
            action_catalog_digest=b"a" * 32, data_classification="restricted",
            policy={"cloud_egress_approved": False},
            budget_remaining={"reasoner_calls": 1, "fep_runsets": 0},
        )
        cls.store = PostgresArtifactStore(cls.connect)
        cls.artifact = cls.store.put(
            b'{"claim_boundary":"model_proposal_not_scientific_evidence"}',
            role="research.context_snapshot", media_type="application/json",
            metadata={"visibility": "private"},
        )
        cls.repository.link_artifact(
            run_id=cls.loop["run_id"], artifact_id=cls.artifact.id,
            role="research.context_snapshot", data_classification="restricted",
        )
        with cls.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE app.research_loop_state SET state='paused' WHERE run_id=%s",
                (cls.loop["run_id"],),
            )

    def test_loop_artifact_uuid_and_digest_are_not_authorization_capabilities(self):
        owner = self.store.read_authorized(self.artifact.id, self.actor)
        self.assertEqual(owner[1],
                         b'{"claim_boundary":"model_proposal_not_scientific_evidence"}')
        intruder = {"kind": "human", "id": "intruder-" + uuid.uuid4().hex}
        for address in (self.artifact.id, "sha256:" + self.artifact.sha256):
            with self.subTest(address=address), self.assertRaises(failures.DiracNotFound):
                self.store.read_authorized(address, intruder)

    def test_provider_secret_is_absent_from_every_loop_persisted_surface(self):
        secret = "research-loop-secret-" + uuid.uuid4().hex
        profile = dict(EXAMPLE["profiles"][1])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as source:
            json.dump({"profiles": [profile]}, source)
            source.flush()
            registry = FileAiProviderRegistry(source.name, environ={
                "DIRAC_LOCAL_QWEN_BASE_URL": "http://127.0.0.1:8000/v1",
                "DIRAC_LOCAL_QWEN_API_KEY": secret,
            })
            resolved = registry.resolve("qwen-local-isolated")
            public_surfaces = {
                "repr": repr(resolved),
                "public": resolved.to_public_dict(),
                "attestation": registry.attest(
                    resolved.profile_id, resolved.profile_digest, "restricted"),
            }
        self.assertNotIn(secret, json.dumps(public_surfaces, sort_keys=True))

        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT row_to_json(ls)::text FROM app.research_loop_state ls "
                "WHERE run_id=%s UNION ALL "
                "SELECT row_to_json(le)::text FROM app.research_loop_event le "
                "WHERE run_id=%s UNION ALL "
                "SELECT row_to_json(la)::text FROM app.research_loop_artifact la "
                "WHERE run_id=%s",
                (self.loop["run_id"],) * 3,
            )
            persisted = "\n".join(str(row[0]) for row in cur.fetchall())
            cur.execute(
                "SELECT b.bytes,a.metadata::text FROM app.research_loop_artifact la "
                "JOIN app.artifact a ON a.id=la.artifact_id "
                "JOIN app.blob b ON b.sha256=a.blob_sha256 WHERE la.run_id=%s",
                (self.loop["run_id"],),
            )
            for raw, metadata in cur.fetchall():
                persisted += bytes(raw).decode("utf-8", errors="replace") + str(metadata)
        self.assertNotIn(secret, persisted)


if __name__ == "__main__":
    unittest.main()
