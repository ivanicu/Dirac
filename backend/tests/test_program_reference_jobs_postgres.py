from __future__ import annotations

import hashlib
import os
import unittest
import uuid

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

from programs.repository import PostgresProgramRepository
from failures import DiracInvalidParameters


@unittest.skipUnless(os.environ.get("DIRAC_TEST_DSN") and psycopg,
                     "requires isolated PostgreSQL DIRAC_TEST_DSN")
class PostgresProgramReferenceJobsTests(unittest.TestCase):
    """One durable round trip through every native reference-job family."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dsn = os.environ["DIRAC_TEST_DSN"]
        cls.connect = staticmethod(lambda: psycopg.connect(cls.dsn))
        cls.repo = PostgresProgramRepository(cls.connect)
        cls.actor = {"kind": "human", "id": "reference-job-test"}

    def seed_scientific_objects(self) -> dict[str, str]:
        suffix = uuid.uuid4().hex[:10]
        payload = f"reference jobs {suffix}".encode()
        digest = hashlib.sha256(payload).hexdigest()
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO meta.toolkit(name,version) VALUES (%s,'1') RETURNING id",
                        (f"reference-{suffix}",))
            toolkit_id = cur.fetchone()[0]
            cur.execute("INSERT INTO chem.standardizer(label,toolkit_id,rules) "
                        "VALUES (%s,%s,'[\"sanitize\"]') RETURNING id",
                        (f"reference-{suffix}", toolkit_id))
            standardizer_id = cur.fetchone()[0]
            cur.execute("INSERT INTO bio.target(name,kind,uniprot) "
                        "VALUES (%s,'protein',%s) RETURNING id",
                        (f"Reference target {suffix}", f"P{suffix[:9].upper()}"))
            target_id = cur.fetchone()[0]
            cur.execute("INSERT INTO bio.assay(code,name,kind,target_id) "
                        "VALUES (%s,%s,'binding',%s) RETURNING id",
                        (f"A-{suffix}", f"Reference assay {suffix}", target_id))
            assay_id = cur.fetchone()[0]
            letter_token = "".join(chr(ord("A") + int(char, 16)) for char in suffix)
            inchikey = (letter_token + "A" * 14)[:14] + "-" + "B" * 10 + "-C"
            cur.execute("INSERT INTO chem.compound(inchikey,inchi,smiles,formula,mw_monoisotopic,"
                        "stereo,standardizer_id) VALUES (%s,'InChI=1S/CH4/h1H4','C','CH4',16.0313,"
                        "'no_stereocenters',%s) RETURNING id", (inchikey, standardizer_id))
            compound_id = cur.fetchone()[0]
            full_inchikey = (letter_token + "D" * 14)[:14] + "-" + "E" * 10 + "-F"
            cur.execute("INSERT INTO chem.form(compound_id,form_kind,full_inchikey,components,mw_form,label) "
                        "VALUES (%s,'neutral',%s,'[]',16.0313,%s) RETURNING id",
                        (compound_id, full_inchikey, f"Reference form {suffix}"))
            form_id = cur.fetchone()[0]
            cur.execute("INSERT INTO chem.batch(form_id,batch_code,provenance,purity_pct,purity_method) "
                        "VALUES (%s,%s,'internal_synthesis',99.0,'hplc_uv') RETURNING id",
                        (form_id, f"B-{suffix}"))
            batch_id = cur.fetchone()[0]
            cur.execute("INSERT INTO bio.structure(pdb_id,target_id,method,resolution_a) "
                        "VALUES (NULL,%s,'xray',1.8) RETURNING id", (target_id,))
            structure_id = cur.fetchone()[0]
            cur.execute("INSERT INTO app.blob(sha256,media_type,byte_len,bytes) "
                        "VALUES (decode(%s,'hex'),'application/json',%s,%s)",
                        (digest, len(payload), payload))
            cur.execute("INSERT INTO app.artifact(blob_sha256,media_type,role,size_bytes) "
                        "VALUES (decode(%s,'hex'),'application/json',%s,%s) RETURNING id",
                        (digest, f"reference.manifest.{suffix}", len(payload)))
            artifact_id = cur.fetchone()[0]
        return {name: str(value) for name, value in {
            "target": target_id, "assay": assay_id, "compound": compound_id,
            "batch": batch_id, "structure": structure_id, "artifact": artifact_id,
        }.items()}

    def test_all_reference_jobs_are_durable_and_program_scoped(self):
        seeded = self.seed_scientific_objects()
        program = self.repo.create({
            "code": f"REF-{uuid.uuid4().hex[:10]}", "name": "Reference job integration",
            "target_ref": {"kind": "target", "id": seeded["target"]},
        }, self.actor)["program"]
        program_ref = program["ref"]
        version = program["version"]

        def record(kind: str, value: dict) -> dict:
            nonlocal version
            result = self.repo.record_reference_job(
                program_ref, version, kind, value, self.actor, request_id=str(uuid.uuid4()))
            version = result["program_version"]
            return result["record"]

        disease = record("target_disease", {
            "disease_key": f"disease-{uuid.uuid4().hex[:8]}", "name": "Reference disease",
            "ontology": {"namespace": "EFO", "id": f"EFO:{uuid.uuid4().hex[:8]}"},
            "target_ref": {"kind": "target", "id": seeded["target"]},
            "rationale": "Explicit target-to-disease scope for the Program.",
        })
        record("substance_registration", {
            "compound_ref": {"kind": "compound", "id": seeded["compound"]},
            "status": "approved", "definition": {"parent": "CH4"},
            "validation": {"structure": "matched"}, "decision": "Accepted by identity review.",
        })
        sample_input = {
            "sample_code": f"S-{uuid.uuid4().hex[:8]}",
            "batch_ref": {"kind": "batch", "id": seeded["batch"]},
            "amount_value": 10, "amount_unit": "mg", "location": "freezer-a",
        }
        sample = record("sample", sample_input)
        record("sample_transfer", {"sample_ref": sample["ref"],
                                    "to_location": "assay-lab", "reason": "Assay allocation"})

        work = self.repo.record_work_package(program_ref, version, {
            "key": f"work-{uuid.uuid4().hex[:8]}", "title": "Reference workflow",
            "description": "One governed Work Item carried across all scientific phases.",
            "lane": "understand", "status": "active", "priority": 1,
        }, self.actor)
        version = work["program_version"]
        work_ref = work["work_item"]["ref"]
        record("work_comment", {"work_item_ref": work_ref, "body": "Decision-relevant context."})
        record("work_attachment", {"work_item_ref": work_ref,
                                    "artifact_ref": {"kind": "artifact", "id": seeded["artifact"]},
                                    "role": "source-evidence"})

        gate = self.repo.record_stage_gate(program_ref, version, {
            "key": f"gate-{uuid.uuid4().hex[:8]}", "stage": "discovery",
            "title": "Identity readiness", "criteria": ["identity"], "status": "ready",
        }, self.actor)
        version = gate["program_version"]
        record("gate_criterion", {"stage_gate_ref": gate["stage_gate"]["ref"],
                                   "criterion_key": "identity", "status": "met",
                                   "evidence_ref": {"kind": "artifact", "id": seeded["artifact"]},
                                   "explanation": "The evidence artifact resolves to canonical identity."})

        protocol = record("protocol_version", {
            "protocol_key": f"protocol-{uuid.uuid4().hex[:8]}", "title": "Binding protocol",
            "assay_ref": {"kind": "assay", "id": seeded["assay"]},
            "specification": {"temperature_k": 298.15, "replicates": 3},
        })
        experiment = record("experiment", {
            "experiment_key": f"experiment-{uuid.uuid4().hex[:8]}", "title": "Reference experiment",
            "work_item_ref": work_ref, "protocol_version_ref": protocol["ref"],
            "status": "completed", "started_at": "2026-08-13T10:00:00Z",
            "completed_at": "2026-08-13T11:00:00Z",
            "samples": [{"sample_ref": sample["ref"], "role": "test"}],
        })
        dataset = record("dataset_version", {
            "dataset_key": f"dataset-{uuid.uuid4().hex[:8]}",
            "manifest_artifact_ref": {"kind": "artifact", "id": seeded["artifact"]},
            "manifest": {"rows": 1, "columns": ["sample", "response"]},
            "schema_version": "1", "access_scope": "program", "experiment_ref": experiment["ref"],
        })
        observation = record("structure_observation", {
            "observation_key": f"observation-{uuid.uuid4().hex[:8]}",
            "structure_ref": {"kind": "protein_structure", "id": seeded["structure"]},
            "compound_ref": {"kind": "compound", "id": seeded["compound"]},
            "experiment_ref": experiment["ref"], "dataset_version_ref": dataset["ref"],
            "canonical_site": "orthosteric",
        })
        record("annotation", {"subject_ref": observation["ref"], "annotation_kind": "site",
                               "label": "Primary binding site", "value": {"confidence": "observed"}})
        record("review", {"subject_ref": observation["ref"], "review_role": "main",
                           "status": "accepted", "comment": "Accepted after map inspection."})
        record("analysis_snapshot", {"work_item_ref": work_ref, "title": "Preserved decision view",
                                      "snapshot_mode": "preserved", "dataset_version_refs": [dataset["ref"]],
                                      "state": {"selected_observation": observation["ref"]}})
        release = record("evidence_release", {
            "source_name": "Open Targets", "release_name": f"release-{uuid.uuid4().hex[:8]}",
            "source_url": "https://platform.opentargets.org/", "retrieved_at": "2026-08-13T12:00:00Z",
            "payload_artifact_ref": {"kind": "artifact", "id": seeded["artifact"]},
        })
        record("external_evidence", {
            "release_ref": release["ref"], "source_record_id": f"record-{uuid.uuid4().hex[:8]}",
            "target_ref": {"kind": "target", "id": seeded["target"]}, "disease_ref": disease["ref"],
            "data_type": "genetic_association", "evidence_source": "reference_fixture",
            "score": 0.7, "is_direct": True, "payload": {"direction": "supports"},
        })

        duplicate = self.repo.record_reference_job(
            program_ref, version, "sample", sample_input, self.actor, request_id=str(uuid.uuid4()))
        self.assertEqual(duplicate["record"]["ref"], sample["ref"])
        self.assertEqual(self.repo.get(program_ref)["program"]["version"], version)
        with self.assertRaises(DiracInvalidParameters):
            self.repo.record_reference_job(program_ref, version, "sample",
                {**sample_input, "amount_value": 9}, self.actor, request_id=str(uuid.uuid4()))

        overview = self.repo.get(program_ref)["program"]
        self.assertEqual(version, 19)
        self.assertEqual(overview["counts"]["reference_jobs"], 16)
        self.assertEqual({item["job_kind"] for item in overview["reference_jobs"]}, {
            "target_disease", "substance_registration", "sample", "sample_transfer",
            "work_comment", "work_attachment", "gate_criterion", "protocol_version",
            "experiment", "dataset_version", "structure_observation", "annotation",
            "review", "analysis_snapshot", "evidence_release", "external_evidence",
        })
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT evidence_count,direct_evidence_count,score_mean FROM bio.v_target_disease_association "
                        "WHERE target_id=%s AND disease_id=%s",
                        (seeded["target"], disease["ref"]["id"]))
            association = cur.fetchone()
            self.assertEqual(association[:2], (1, 1))
            self.assertAlmostEqual(float(association[2]), 0.7)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
