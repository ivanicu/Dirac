from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import os
import unittest
import uuid

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


@unittest.skipUnless(os.environ.get("DIRAC_TEST_DSN") and psycopg,
                     "requires PostgreSQL DIRAC_TEST_DSN")
class MotifV3PostgresTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(os.environ["DIRAC_TEST_DSN"])
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def artifact(self, role: str) -> str:
        payload = uuid.uuid4().bytes
        digest = hashlib.sha256(payload).digest()
        self.cur.execute(
            "INSERT INTO app.blob(sha256,media_type,byte_len,bytes) "
            "VALUES (%s,'application/json',%s,%s) ON CONFLICT DO NOTHING",
            (digest, len(payload), payload))
        self.cur.execute(
            "INSERT INTO app.artifact(blob_sha256,media_type,role,size_bytes) "
            "VALUES (%s,'application/json',%s,%s) RETURNING id",
            (digest, role, len(payload)))
        return str(self.cur.fetchone()[0])

    def method_manifest(self) -> str:
        artifact = self.artifact("motif.method_manifest")
        runtime = self.artifact("motif.runtime")
        self.cur.execute("SELECT id FROM meta.method ORDER BY method_id LIMIT 1")
        method = self.cur.fetchone()[0]
        digest = hashlib.sha256(uuid.uuid4().bytes).digest()
        self.cur.execute(
            "INSERT INTO meta.method_manifest "
            "(method_row_id,release_name,schema_version,manifest_artifact_id,manifest_digest,"
            "input_schema_digest,output_schema_digest,parameter_schema_digest,runtime_artifact_id,"
            "numeric_contract,determinism_contract,checkpoint_contract,capability_contract,lifecycle) "
            "VALUES (%s,%s,'3.0',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'technical_smoke') RETURNING id",
            (method, f"test-{uuid.uuid4()}", artifact, digest, b"i" * 32, b"o" * 32,
             b"p" * 32, runtime, '{"comparison":"tolerance"}',
             '{"class":"numeric_tolerance"}', '{"supported":false}', '{}'))
        return str(self.cur.fetchone()[0])

    def test_object_graph_rejects_refused_without_reason_and_unsupported_eligible(self):
        artifact = self.artifact("motif.scientific_object")
        manifest = self.method_manifest()
        for applicability, disposition, eligibility in (
                ("applicable", "refused", "ineligible_unvalidated_method"),
                ("unsupported", "pending", "eligible")):
            self.cur.execute("SAVEPOINT bad_state")
            with self.assertRaises(psycopg.errors.CheckViolation):
                self.cur.execute(
                    "INSERT INTO design.motif_scientific_object "
                    "(object_kind,semantic_digest,document_artifact_id,method_manifest_id,"
                    "applicability,disposition,claim_eligibility) "
                    "VALUES ('chemical_entity',%s,%s,%s,%s,%s,%s)",
                    (hashlib.sha256(uuid.uuid4().bytes).digest(), artifact, manifest,
                     applicability, disposition, eligibility))
            self.cur.execute("ROLLBACK TO SAVEPOINT bad_state")

    def test_one_active_atomic_resource_lease_per_owner(self):
        owner = uuid.uuid4()
        self.cur.execute(
            "INSERT INTO app.resource_lease "
            "(owner_kind,owner_id,backend,request,fencing_token,lease_owner,expires_at,heartbeat_at) "
            "VALUES ('job',%s,'local_cpu','{\"cpu_cores\":4}',1,'test',now()+interval '1 hour',now())",
            (owner,))
        self.cur.execute("SAVEPOINT duplicate_lease")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.cur.execute(
                "INSERT INTO app.resource_lease "
                "(owner_kind,owner_id,backend,request,fencing_token,lease_owner,expires_at,heartbeat_at) "
                "VALUES ('job',%s,'local_cpu','{\"cpu_cores\":1}',2,'other',now()+interval '1 hour',now())",
                (owner,))
        self.cur.execute("ROLLBACK TO SAVEPOINT duplicate_lease")

    def test_migration_is_registered_and_core_tables_exist(self):
        self.cur.execute(
            "SELECT filename FROM meta.migration WHERE filename='037_motif_scientific_semantics.sql'")
        self.assertEqual(self.cur.fetchone()[0], "037_motif_scientific_semantics.sql")
        self.cur.execute(
            "SELECT to_regclass('design.motif_evidence_item'),"
            "to_regclass('design.motif_routing_action'),to_regclass('app.artifact_commit')")
        self.assertEqual(tuple(map(str, self.cur.fetchone())),
                         ("design.motif_evidence_item", "design.motif_routing_action",
                          "app.artifact_commit"))

    def test_postgres_resource_broker_serializes_cross_process_capacity(self):
        from motif.resource_broker import InsufficientCapacity, PostgresResourceBroker
        connect = lambda: psycopg.connect(os.environ["DIRAC_TEST_DSN"])
        broker = PostgresResourceBroker(connect, {
            "cpu_cores": 10, "ram_bytes": 100, "gpus": 1,
            "gpu_vram_bytes": 100, "scratch_bytes": 100,
            "persistent_growth_bytes": 100, "process_slots": 10,
            "scf_slots": 2, "campaign_credits": 100,
        })
        owners = [str(uuid.uuid4()), str(uuid.uuid4())]
        first = broker.acquire(owners[0], None, {"cpu_cores": 6},
                               ttl_seconds=30, backend="local_cpu")
        try:
            with self.assertRaises(InsufficientCapacity):
                broker.acquire(owners[1], None, {"cpu_cores": 5},
                               ttl_seconds=30, backend="local_cpu")
            broker.release(first.lease_id, first.fencing_token)
            second = broker.acquire(owners[1], None, {"cpu_cores": 5},
                                    ttl_seconds=30, backend="local_cpu")
            broker.release(second.lease_id, second.fencing_token)
        finally:
            with connect() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM app.resource_lease WHERE owner_id=ANY(%s)",
                            (owners,))

    def test_fenced_attempt_commits_terminal_manifest_exactly_once(self):
        from artifacts_pg import PostgresArtifactStore
        from execution_control.attempt_store import PostgresAttemptStore
        connect = lambda: psycopg.connect(os.environ["DIRAC_TEST_DSN"])
        store = PostgresArtifactStore(connect)
        attempt_store = PostgresAttemptStore(connect)
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM meta.method ORDER BY method_id LIMIT 1")
            method_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO app.job(method_row_id,input_sha256,request_digest,params) "
                "VALUES (%s,%s,%s,'{}') RETURNING id",
                (method_id, b"j" * 32, b"r" * 32))
            job_id = str(cur.fetchone()[0])
        result_artifact = store.put(b"result", role="motif.result",
                                    media_type="application/json")
        manifest_artifact = store.put(b"manifest", role="output.manifest",
                                      media_type="application/json")
        claim = attempt_store.claim(
            job_id=job_id, execution_digest=b"e" * 32,
            owner="test-controller", lease_seconds=60)
        now = datetime.now(timezone.utc).isoformat()
        manifest = {
            "schema_version": "1.0", "job_id": job_id,
            "attempt_id": claim.attempt_id, "fencing_token": claim.fencing_token,
            "execution_digest": "sha256:" + (b"e" * 32).hex(),
            "artifacts": [{"role": "motif.result",
                           "sha256": "sha256:" + result_artifact.sha256,
                           "size_bytes": result_artifact.size_bytes,
                           "media_type": "application/json", "required": True}],
            "result_summary": {}, "warnings": [],
            "started_at": now, "finished_at": now,
        }
        try:
            self.assertTrue(attempt_store.commit_success(
                claim, manifest=manifest, manifest_artifact_id=manifest_artifact.id,
                required_roles=["motif.result"], artifact_reader=store,
                event_key=f"test:{claim.attempt_id}:success"))
            self.assertFalse(attempt_store.commit_success(
                claim, manifest=manifest, manifest_artifact_id=manifest_artifact.id,
                required_roles=["motif.result"], artifact_reader=store,
                event_key=f"test:{claim.attempt_id}:success"))
            with connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT state FROM app.job_attempt WHERE id=%s",
                            (claim.attempt_id,))
                self.assertEqual(cur.fetchone()[0], "succeeded")
                cur.execute("SELECT count(*) FROM app.artifact_commit WHERE logical_job_id=%s",
                            (job_id,))
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            with connect() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM design.motif_method_outcome WHERE attempt_id=%s",
                            (claim.attempt_id,))
                cur.execute("DELETE FROM app.artifact_commit WHERE logical_job_id=%s", (job_id,))
                cur.execute("DELETE FROM app.outbox_event WHERE aggregate_id=%s", (claim.attempt_id,))
                cur.execute("DELETE FROM app.job WHERE id=%s", (job_id,))
                cur.execute("DELETE FROM app.artifact WHERE id=ANY(%s)",
                            ([result_artifact.id, manifest_artifact.id],))


if __name__ == "__main__":
    unittest.main()
