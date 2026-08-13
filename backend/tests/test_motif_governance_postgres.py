from __future__ import annotations

import copy
import hashlib
import os
import unittest

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

from motif.governance import PostgresMotifGovernanceStore, with_semantic_digest
from failures import DiracInvalidParameters
from tests.test_motif_contracts import DESIGN_BRIEF, ENDPOINT_DEFINITION, MEASUREMENT
from artifacts_pg import PostgresArtifactStore
from catalog import MethodCatalog
from invocation import InvocationService


@unittest.skipUnless(os.environ.get("DIRAC_TEST_DSN") and psycopg,
                     "requires isolated PostgreSQL DIRAC_TEST_DSN")
class PostgresMotifGovernanceTests(unittest.TestCase):
    TARGET_ID = "10000000-0000-4000-8000-000000000001"
    ASSAY_ID = "10000000-0000-4000-8000-000000000002"
    PROGRAM_ID = "10000000-0000-4000-8000-000000000003"
    CAMPAIGN_ID = "10000000-0000-4000-8000-000000000004"

    @classmethod
    def setUpClass(cls) -> None:
        cls.dsn = os.environ["DIRAC_TEST_DSN"]
        cls.connect = staticmethod(lambda: psycopg.connect(cls.dsn))
        cls.store = PostgresMotifGovernanceStore(cls.connect)
        cls.actor = {"kind": "human", "id": "chemist-1"}
        source = b"source assay export fixture"
        source_digest = hashlib.sha256(source).hexdigest()
        with cls.connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO bio.target(id,name,kind) VALUES (%s,'Motif test target','protein')",
                        (cls.TARGET_ID,))
            cur.execute("INSERT INTO bio.assay(id,code,name,kind,target_id) "
                        "VALUES (%s,'MOTIF-IC50','Motif IC50','biochemical',%s)",
                        (cls.ASSAY_ID, cls.TARGET_ID))
            cur.execute("INSERT INTO design.project(id,code,name,target_id) "
                        "VALUES (%s,'MOTIF-P','Motif test program',%s)",
                        (cls.PROGRAM_ID, cls.TARGET_ID))
            cur.execute("INSERT INTO design.campaign(id,program_id,name,objective,created_by_kind,created_by_id) "
                        "VALUES (%s,%s,'Motif test campaign','validate governance','human','chemist-1')",
                        (cls.CAMPAIGN_ID, cls.PROGRAM_ID))
            cur.execute("INSERT INTO app.blob(sha256,media_type,byte_len,bytes) "
                        "VALUES (decode(%s,'hex'),'text/plain',%s,%s)",
                        (source_digest, len(source), source))
            cur.execute("INSERT INTO app.artifact(blob_sha256,media_type,role,size_bytes) "
                        "VALUES (decode(%s,'hex'),'text/plain','assay.source',%s) RETURNING id",
                        (source_digest, len(source)))
            cls.source_artifact_id = str(cur.fetchone()[0])

    def endpoint(self) -> dict:
        document = copy.deepcopy(ENDPOINT_DEFINITION)
        document["assay"]["id"] = self.ASSAY_ID
        document["target"]["id"] = self.TARGET_ID
        return with_semantic_digest(document)

    def objective(self) -> dict:
        document = copy.deepcopy(DESIGN_BRIEF)
        document["program"]["id"] = self.PROGRAM_ID
        document["campaign"]["id"] = self.CAMPAIGN_ID
        document["target"]["id"] = self.TARGET_ID
        return with_semantic_digest(document)

    def measurement(self) -> dict:
        document = copy.deepcopy(MEASUREMENT)
        document["assay"]["id"] = self.ASSAY_ID
        document["endpoint"] = {"id": "ic50", "version": "1"}
        document["source"]["artifact_id"] = self.source_artifact_id
        return document

    def register_policies(self) -> None:
        policies = dict(DESIGN_BRIEF["policy_releases"])
        policies["identity_gate"] = DESIGN_BRIEF["chemistry_constraints"][
            "identity_policy_release_id"]
        for kind, identifier in policies.items():
            self.store.register_policy(with_semantic_digest({
                "schema_version": "1.0", "policy_release_id": identifier,
                "policy_kind": kind, "name": f"test-{kind}", "version": "1",
                "lifecycle": "candidate", "spec": {"fixture": True},
                "created_by": self.actor, "created_at": "2026-08-12T00:00:00Z",
            }), self.actor)

    def test_atomic_idempotent_governance_round_trip(self):
        endpoint_first = self.store.register_endpoint(self.endpoint(), self.actor)
        endpoint_second = self.store.register_endpoint(self.endpoint(), self.actor)
        self.assertTrue(endpoint_first["created"])
        self.assertFalse(endpoint_second["created"])

        self.register_policies()

        objective_first = self.store.save_objective(self.objective(), self.actor)
        objective_second = self.store.save_objective(self.objective(), self.actor)
        self.assertTrue(objective_first["created"])
        self.assertFalse(objective_second["created"])

        ingest_first = self.store.ingest_measurements([self.measurement()], self.actor)
        ingest_second = self.store.ingest_measurements([self.measurement()], self.actor)
        self.assertEqual(ingest_first["created_count"], 1)
        self.assertEqual(ingest_second["deduplicated_count"], 1)

        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT qualifier,value_num,lower_num,upper_num,missing_reason "
                        "FROM bio.measurement_v2 WHERE measurement_key='m-1'")
            self.assertEqual(cur.fetchone(), ("not_tested", None, None, None, "not_tested"))
            cur.execute("SELECT count(*) FROM app.outbox_event "
                        "WHERE event_type LIKE 'motif.%'")
            self.assertEqual(cur.fetchone()[0], 9)

    def test_measurement_identity_collision_rolls_back(self):
        self.store.register_endpoint(self.endpoint(), self.actor)
        self.store.ingest_measurements([self.measurement()], self.actor)
        changed = self.measurement()
        changed["missing_reason"] = "assay_failed"
        with self.assertRaises(DiracInvalidParameters):
            self.store.ingest_measurements([changed], self.actor)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT missing_reason,count(*) FROM bio.measurement_v2 "
                        "WHERE measurement_key='m-1' GROUP BY missing_reason")
            self.assertEqual(cur.fetchone(), ("not_tested", 1))

    def test_dataset_completion_registers_snapshot_and_endpoint_link(self):
        self.store.register_endpoint(self.endpoint(), self.actor)
        self.register_policies()
        catalog = MethodCatalog.load().bind_versions({"ml.motif.train": "test-version"})
        train_spec = catalog.get("ml.motif.train")
        from psycopg.types.json import Jsonb
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT meta.register_method(%s,%s,%s,%s,%s,'job',%s)",
                ("ml.motif.train", "test-version", b"t" * 32,
                 Jsonb(train_spec.input_schema), Jsonb(train_spec.output_schema), Jsonb({})),
            )
        service = InvocationService(
            catalog, store=PostgresArtifactStore(self.connect),
            motif_governance=self.store)
        rows = [
            {"measurement_id": "dataset-m-1", "compound_id": "compound-1",
             "smiles": "CCO", "endpoint_key": "ic50", "protocol_id": "protocol-1",
             "unit": "nM", "measurement_type": "concentration", "value": 1.0,
             "qualifier": "equal", "split": "train"},
            {"measurement_id": "dataset-m-2", "compound_id": "compound-2",
             "smiles": "CCN", "endpoint_key": "ic50", "protocol_id": "protocol-1",
             "unit": "nM", "measurement_type": "concentration", "value": 2.0,
             "qualifier": "equal", "split": "train"},
        ]
        payload = {
            "selection_query": "measurement-v2:smoke",
            "endpoint_definitions": [{"endpoint_key": "ic50", "version": "1",
                                      "canonical_unit": "nM",
                                      "measurement_type": "concentration"}],
            "rows": rows,
            "registration": {
                "program_ref": {"kind": "program", "id": self.PROGRAM_ID},
                "campaign_ref": {"kind": "campaign", "id": self.CAMPAIGN_ID},
                "identity_policy_release_id": DESIGN_BRIEF["chemistry_constraints"][
                    "identity_policy_release_id"],
                "data_classification": "internal",
            },
        }
        first = service.invoke("data.motif.snapshot", payload, actor=self.actor)
        second = service.invoke("data.motif.snapshot", payload, actor=self.actor)
        self.assertTrue(first["ok"], first)
        self.assertTrue(first["data"]["dataset_snapshot"]["created"])
        self.assertFalse(second["data"]["dataset_snapshot"]["created"])
        snapshot_id = first["data"]["dataset_snapshot"]["ref"]["id"]
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT status,row_count FROM app.dataset_snapshot WHERE id=%s",
                        (snapshot_id,))
            self.assertEqual(cur.fetchone(), ("valid", 2))
            cur.execute("SELECT count(*) FROM app.dataset_snapshot_endpoint "
                        "WHERE dataset_snapshot_id=%s", (snapshot_id,))
            self.assertEqual(cur.fetchone()[0], 1)

        mismatched_rows = copy.deepcopy(rows)
        mismatched_rows[0]["value"] = 99.0
        refused = service.invoke("ml.motif.train", {
            "endpoint_key": "ic50", "n_bits": 128, "rows": mismatched_rows,
            "registration": {
                "dataset_snapshot_ref": first["data"]["dataset_snapshot"]["ref"],
                "model_object_id": "motif-postgres-mismatch",
                "release_name": "candidate-1", "source_commit": "b" * 40,
                "intended_use": {}, "prohibited_use": {}, "known_limitations": {},
            },
        }, actor=self.actor)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error"]["code"], "INVALID_PARAMETERS")
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM meta.model_release "
                        "WHERE model_object_id='motif-postgres-mismatch'")
            self.assertEqual(cur.fetchone()[0], 0)

        trained = service.invoke("ml.motif.train", {
            "endpoint_key": "ic50", "n_bits": 128, "rows": rows,
            "registration": {
                "dataset_snapshot_ref": first["data"]["dataset_snapshot"]["ref"],
                "model_object_id": "motif-postgres-baseline",
                "release_name": "candidate-1", "source_commit": "b" * 40,
                "intended_use": {"fixture": True},
                "prohibited_use": {"clinical": True},
                "known_limitations": {"fixture": True},
            },
        }, actor=self.actor)
        self.assertTrue(trained["ok"], trained)
        release_id = trained["data"]["model_release"]["model_release_id"]
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT lifecycle::text,runtime_kind,container_image_digest,"
                        "runtime_lock_artifact_id IS NOT NULL FROM meta.model_release WHERE id=%s",
                        (release_id,))
            self.assertEqual(cur.fetchone(), ("candidate", "local_env", None, True))
            cur.execute("SELECT count(*) FROM meta.model_release_dataset "
                        "WHERE model_release_id=%s AND dataset_snapshot_id=%s AND role='train'",
                        (release_id, snapshot_id))
            self.assertEqual(cur.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
