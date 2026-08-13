from __future__ import annotations

import hashlib
import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

from programs.repository import PostgresProgramRepository
from failures import DiracInvalidParameters, DiracNotFound


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

    def test_cross_program_references_and_request_id_relabeling_fail_without_side_effects(self):
        seeded = self.seed_scientific_objects()
        first = self.repo.create({
            "code": f"SCOPE-A-{uuid.uuid4().hex[:8]}", "name": "Scope owner A",
            "target_ref": {"kind": "target", "id": seeded["target"]},
        }, self.actor)["program"]
        second = self.repo.create({
            "code": f"SCOPE-B-{uuid.uuid4().hex[:8]}", "name": "Scope attacker B",
            "target_ref": {"kind": "target", "id": seeded["target"]},
        }, self.actor)["program"]

        first_version = first["version"]

        def first_record(kind: str, value: dict, request_id: str | None = None) -> dict:
            nonlocal first_version
            result = self.repo.record_reference_job(
                first["ref"], first_version, kind, value, self.actor,
                request_id=request_id or str(uuid.uuid4()))
            first_version = result["program_version"]
            return result["record"]

        disease = first_record("target_disease", {
            "disease_key": f"scope-{uuid.uuid4().hex[:8]}", "name": "Scoped disease",
            "target_ref": {"kind": "target", "id": seeded["target"]},
            "rationale": "Owned by Program A",
        })
        sample = first_record("sample", {
            "sample_code": f"SCOPE-{uuid.uuid4().hex[:8]}",
            "batch_ref": {"kind": "batch", "id": seeded["batch"]},
            "amount_value": 1, "amount_unit": "mg", "location": "freezer-a",
        })
        work_a = self.repo.record_work_package(first["ref"], first_version, {
            "key": f"work-a-{uuid.uuid4().hex[:8]}", "title": "Work A",
            "description": "Program A work", "lane": "test_learn",
        }, self.actor)
        first_version = work_a["program_version"]
        protocol = first_record("protocol_version", {
            "protocol_key": f"scope-protocol-{uuid.uuid4().hex[:8]}", "title": "Scoped protocol",
            "assay_ref": {"kind": "assay", "id": seeded["assay"]}, "specification": {"replicates": 3},
        })
        experiment = first_record("experiment", {
            "experiment_key": f"scope-experiment-{uuid.uuid4().hex[:8]}", "title": "Experiment A",
            "work_item_ref": work_a["work_item"]["ref"], "protocol_version_ref": protocol["ref"],
            "status": "completed", "started_at": "2026-08-13T10:00:00Z",
            "completed_at": "2026-08-13T11:00:00Z",
            "samples": [{"sample_ref": sample["ref"], "role": "test"}],
        })
        dataset = first_record("dataset_version", {
            "dataset_key": f"scope-dataset-{uuid.uuid4().hex[:8]}",
            "manifest_artifact_ref": {"kind": "artifact", "id": seeded["artifact"]},
            "manifest": {"rows": 1}, "schema_version": "1", "experiment_ref": experiment["ref"],
        })
        observation = first_record("structure_observation", {
            "observation_key": f"scope-observation-{uuid.uuid4().hex[:8]}",
            "structure_ref": {"kind": "protein_structure", "id": seeded["structure"]},
            "experiment_ref": experiment["ref"], "dataset_version_ref": dataset["ref"],
        })
        release = first_record("evidence_release", {
            "source_name": "Scope fixture", "release_name": f"release-{uuid.uuid4().hex[:8]}",
            "retrieved_at": "2026-08-13T12:00:00Z",
            "payload_artifact_ref": {"kind": "artifact", "id": seeded["artifact"]},
        })

        work_b = self.repo.record_work_package(second["ref"], second["version"], {
            "key": f"work-b-{uuid.uuid4().hex[:8]}", "title": "Work B",
            "description": "Program B work", "lane": "test_learn",
        }, self.actor)
        second_version = work_b["program_version"]
        attacks = [
            ("sample_transfer", {"sample_ref": sample["ref"], "to_location": "attacker-lab",
                                 "reason": "cross-program custody attack"}),
            ("experiment", {"experiment_key": f"attack-exp-{uuid.uuid4().hex[:8]}", "title": "Attack",
                            "work_item_ref": work_b["work_item"]["ref"], "protocol_version_ref": protocol["ref"],
                            "status": "running", "started_at": "2026-08-13T12:00:00Z",
                            "samples": [{"sample_ref": sample["ref"], "role": "test"}]}),
            ("dataset_version", {"dataset_key": f"attack-ds-{uuid.uuid4().hex[:8]}",
                                 "manifest_artifact_ref": {"kind": "artifact", "id": seeded["artifact"]},
                                 "manifest": {"rows": 1}, "schema_version": "1",
                                 "experiment_ref": experiment["ref"]}),
            ("structure_observation", {"observation_key": f"attack-obs-{uuid.uuid4().hex[:8]}",
                                       "structure_ref": {"kind": "protein_structure", "id": seeded["structure"]},
                                       "dataset_version_ref": dataset["ref"]}),
            ("annotation", {"subject_ref": observation["ref"], "annotation_kind": "note",
                            "label": "cross-scope", "value": {}}),
            ("review", {"subject_ref": observation["ref"], "status": "accepted",
                        "comment": "cross-scope review"}),
            ("analysis_snapshot", {"work_item_ref": work_b["work_item"]["ref"], "title": "Attack snapshot",
                                   "snapshot_mode": "preserved", "dataset_version_refs": [dataset["ref"]],
                                   "state": {}}),
            ("external_evidence", {"release_ref": release["ref"], "source_record_id": str(uuid.uuid4()),
                                   "target_ref": {"kind": "target", "id": seeded["target"]},
                                   "disease_ref": disease["ref"], "data_type": "genetic_association",
                                   "evidence_source": "attack", "payload": {}}),
        ]
        for kind, value in attacks:
            with self.subTest(kind=kind), self.assertRaises((DiracNotFound, DiracInvalidParameters)):
                self.repo.record_reference_job(
                    second["ref"], second_version, kind, value, self.actor, request_id=str(uuid.uuid4()))
            self.assertEqual(self.repo.get(second["ref"])["program"]["version"], second_version)

        collision_id = str(uuid.uuid4())
        collision_record = {
            "sample_code": f"REQUEST-{uuid.uuid4().hex[:8]}",
            "batch_ref": {"kind": "batch", "id": seeded["batch"]},
            "amount_value": 1, "amount_unit": "mg",
        }
        accepted = self.repo.record_reference_job(
            second["ref"], second_version, "sample", collision_record, self.actor, collision_id)
        with self.assertRaises(DiracInvalidParameters):
            self.repo.record_reference_job(second["ref"], accepted["program_version"], "sample",
                {**collision_record, "sample_code": f"RELABEL-{uuid.uuid4().hex[:8]}"},
                self.actor, collision_id)
        cross_command_id = str(uuid.uuid4())
        updated = self.repo.update(second["ref"], accepted["program_version"],
                                   {"summary": "ordinary update"}, self.actor, cross_command_id)
        current_version = updated["program"]["version"]
        with self.assertRaises(DiracInvalidParameters):
            self.repo.record_reference_job(second["ref"], current_version, "sample", {
                **collision_record, "sample_code": f"CROSS-COMMAND-{uuid.uuid4().hex[:8]}"},
            self.actor, cross_command_id)
        self.assertEqual(self.repo.get(second["ref"])["program"]["version"], current_version)

    def test_optimistic_lock_survives_repeated_concurrent_writers(self):
        seeded = self.seed_scientific_objects()
        for round_number in range(5):
            program = self.repo.create({
                "code": f"RACE-{round_number}-{uuid.uuid4().hex[:6]}",
                "name": f"Race round {round_number}",
            }, self.actor)["program"]
            workers = 12
            barrier = threading.Barrier(workers)

            def attack(index: int) -> str:
                barrier.wait(timeout=10)
                try:
                    self.repo.record_reference_job(program["ref"], 1, "sample", {
                        "sample_code": f"RACE-{round_number}-{index}-{uuid.uuid4().hex[:6]}",
                        "batch_ref": {"kind": "batch", "id": seeded["batch"]},
                        "amount_value": 1, "amount_unit": "mg",
                    }, self.actor, request_id=str(uuid.uuid4()))
                    return "accepted"
                except DiracInvalidParameters:
                    return "conflict"

            with ThreadPoolExecutor(max_workers=workers) as pool:
                verdicts = list(pool.map(attack, range(workers)))
            self.assertEqual(verdicts.count("accepted"), 1, verdicts)
            self.assertEqual(verdicts.count("conflict"), workers - 1, verdicts)
            overview = self.repo.get(program["ref"])["program"]
            self.assertEqual(overview["version"], 2)
            self.assertEqual(overview["counts"]["reference_jobs"], 1)

    def test_database_itself_rejects_cross_program_rows_when_repository_is_bypassed(self):
        seeded = self.seed_scientific_objects()
        program_a = self.repo.create({
            "code": f"SQL-A-{uuid.uuid4().hex[:8]}", "name": "SQL owner A",
        }, self.actor)["program"]
        program_b = self.repo.create({
            "code": f"SQL-B-{uuid.uuid4().hex[:8]}", "name": "SQL attacker B",
        }, self.actor)["program"]
        work_a = self.repo.record_work_package(program_a["ref"], 1, {
            "key": f"sql-work-a-{uuid.uuid4().hex[:8]}", "title": "SQL Work A",
            "description": "Owned by A",
        }, self.actor)
        work_b = self.repo.record_work_package(program_b["ref"], 1, {
            "key": f"sql-work-b-{uuid.uuid4().hex[:8]}", "title": "SQL Work B",
            "description": "Owned by B",
        }, self.actor)
        protocol = self.repo.record_reference_job(program_a["ref"], work_a["program_version"],
            "protocol_version", {
                "protocol_key": f"sql-protocol-{uuid.uuid4().hex[:8]}", "title": "SQL protocol",
                "specification": {"temperature_k": 298.15},
            }, self.actor, request_id=str(uuid.uuid4()))
        experiment = self.repo.record_reference_job(program_a["ref"], protocol["program_version"],
            "experiment", {
                "experiment_key": f"sql-experiment-{uuid.uuid4().hex[:8]}", "title": "SQL experiment",
                "work_item_ref": work_a["work_item"]["ref"],
                "protocol_version_ref": protocol["record"]["ref"], "status": "planned", "samples": [],
            }, self.actor, request_id=str(uuid.uuid4()))
        dataset = self.repo.record_reference_job(program_a["ref"], experiment["program_version"],
            "dataset_version", {
                "dataset_key": f"sql-dataset-{uuid.uuid4().hex[:8]}",
                "manifest_artifact_ref": {"kind": "artifact", "id": seeded["artifact"]},
                "manifest": {"rows": 0}, "schema_version": "1",
                "experiment_ref": experiment["record"]["ref"],
            }, self.actor, request_id=str(uuid.uuid4()))

        attacks = [
            ("experiment/work-item", """
                INSERT INTO bio.experiment(
                    experiment_key,program_id,work_item_id,protocol_version_id,title,
                    status,recorded_by_kind,recorded_by_id)
                VALUES (%s,%s,%s,%s,'attack','planned','human','sql-attacker')
            """, (f"raw-exp-{uuid.uuid4().hex[:8]}", program_b["ref"]["id"],
                    work_a["work_item"]["ref"]["id"], protocol["record"]["ref"]["id"])),
            ("dataset/experiment", """
                INSERT INTO app.dataset_version(
                    dataset_key,version,program_id,experiment_id,manifest_artifact_id,
                    manifest,schema_version,digest,committed_by_kind,committed_by_id)
                VALUES (%s,1,%s,%s,%s,'{}','1',%s,'human','sql-attacker')
            """, (f"raw-dataset-{uuid.uuid4().hex[:8]}", program_b["ref"]["id"],
                    experiment["record"]["ref"]["id"], seeded["artifact"], os.urandom(32))),
            ("observation/dataset", """
                INSERT INTO bio.structure_observation(
                    observation_key,program_id,structure_id,source_dataset_version_id,
                    created_by_kind,created_by_id)
                VALUES (%s,%s,%s,%s,'human','sql-attacker')
            """, (f"raw-observation-{uuid.uuid4().hex[:8]}", program_b["ref"]["id"],
                    seeded["structure"], dataset["record"]["ref"]["id"])),
            ("snapshot/work-and-dataset", """
                INSERT INTO design.analysis_snapshot(
                    program_id,work_item_id,title,snapshot_mode,dataset_version_ids,state,digest,
                    created_by_kind,created_by_id)
                VALUES (%s,%s,'attack','preserved',%s,'{}',%s,'human','sql-attacker')
            """, (program_b["ref"]["id"], work_a["work_item"]["ref"]["id"],
                    [dataset["record"]["ref"]["id"]], os.urandom(32))),
        ]
        for name, statement, parameters in attacks:
            with self.subTest(name=name), self.assertRaises(psycopg.Error):
                with self.connect() as conn:
                    conn.execute(statement, parameters)
        for nonfinite in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(nonfinite=nonfinite), self.assertRaises(psycopg.Error):
                with self.connect() as conn:
                    conn.execute("""
                        INSERT INTO chem.sample(
                            batch_id,sample_code,amount_value,amount_unit,created_by_kind,created_by_id)
                        VALUES (%s,%s,%s::numeric,'mg','human','sql-attacker')
                    """, (seeded["batch"], f"NONFINITE-{uuid.uuid4().hex[:8]}", nonfinite))
        self.assertEqual(self.repo.get(program_b["ref"])["program"]["version"],
                         work_b["program_version"])

    def test_global_scientific_identity_is_reused_but_natural_key_collisions_are_rejected(self):
        seeded = self.seed_scientific_objects()
        programs = [self.repo.create({
            "code": f"IDENTITY-{index}-{uuid.uuid4().hex[:7]}", "name": f"Identity {index}",
            "target_ref": {"kind": "target", "id": seeded["target"]},
        }, self.actor)["program"] for index in range(2)]
        versions = [program["version"] for program in programs]

        def record(index: int, kind: str, value: dict) -> dict:
            result = self.repo.record_reference_job(
                programs[index]["ref"], versions[index], kind, value, self.actor,
                request_id=str(uuid.uuid4()))
            versions[index] = result["program_version"]
            return result["record"]

        disease_value = {
            "disease_key": f"GLOBAL-{uuid.uuid4().hex[:8]}", "name": "Canonical disease",
            "target_ref": {"kind": "target", "id": seeded["target"]},
            "rationale": "Shared target-disease definition",
        }
        disease_a = record(0, "target_disease", disease_value)
        disease_b = record(1, "target_disease", disease_value)
        self.assertEqual(disease_a["ref"], disease_b["ref"])
        with self.assertRaises(DiracInvalidParameters):
            record(1, "target_disease", {**disease_value, "disease_key": disease_value["disease_key"],
                                          "name": "Conflicting relabel"})

        protocol_value = {
            "protocol_key": f"GLOBAL-PROTOCOL-{uuid.uuid4().hex[:8]}", "title": "Canonical protocol",
            "assay_ref": {"kind": "assay", "id": seeded["assay"]},
            "specification": {"temperature_k": 298.15, "replicates": 3},
        }
        protocol_a = record(0, "protocol_version", protocol_value)
        protocol_b = record(1, "protocol_version", protocol_value)
        self.assertEqual(protocol_a["ref"], protocol_b["ref"])
        with self.assertRaises(DiracInvalidParameters):
            record(1, "protocol_version", {**protocol_value, "title": "Conflicting title"})

        release_value = {
            "source_name": "Canonical source", "release_name": f"R-{uuid.uuid4().hex[:8]}",
            "retrieved_at": "2026-08-13T12:00:00Z",
            "payload_artifact_ref": {"kind": "artifact", "id": seeded["artifact"]},
        }
        release_a = record(0, "evidence_release", release_value)
        release_b = record(1, "evidence_release", release_value)
        self.assertEqual(release_a["ref"], release_b["ref"])
        with self.assertRaises(DiracInvalidParameters):
            record(1, "evidence_release", {**release_value, "retrieved_at": "2026-08-14T12:00:00Z"})

        evidence_value = {
            "release_ref": release_a["ref"], "source_record_id": f"record-{uuid.uuid4().hex[:8]}",
            "target_ref": {"kind": "target", "id": seeded["target"]}, "disease_ref": disease_a["ref"],
            "data_type": "genetic_association", "evidence_source": "canonical", "score": 0.75,
            "is_direct": True, "payload": {"direction": "supports"},
        }
        evidence_a = record(0, "external_evidence", evidence_value)
        evidence_b = record(1, "external_evidence", evidence_value)
        self.assertEqual(evidence_a["ref"], evidence_b["ref"])
        with self.assertRaises(DiracInvalidParameters):
            record(1, "external_evidence", {**evidence_value, "payload": {"direction": "contradicts"}})

        sample_value = {
            "sample_code": f"PHYSICAL-{uuid.uuid4().hex[:8]}",
            "batch_ref": {"kind": "batch", "id": seeded["batch"]},
            "amount_value": 1, "amount_unit": "mg",
        }
        record(0, "sample", sample_value)
        with self.assertRaises(DiracInvalidParameters):
            record(1, "sample", sample_value)

    def test_global_natural_keys_survive_parallel_cross_program_claims(self):
        seeded = self.seed_scientific_objects()
        workers = 8
        programs = [self.repo.create({
            "code": f"GLOBAL-RACE-{index}-{uuid.uuid4().hex[:6]}", "name": f"Global race {index}",
            "target_ref": {"kind": "target", "id": seeded["target"]},
        }, self.actor)["program"] for index in range(workers)]
        versions = [1] * workers

        def parallel_record(kind: str, value: dict) -> list[dict]:
            barrier = threading.Barrier(workers)

            def write(index: int) -> dict:
                barrier.wait(timeout=10)
                return self.repo.record_reference_job(
                    programs[index]["ref"], versions[index], kind, value, self.actor,
                    request_id=str(uuid.uuid4()))

            with ThreadPoolExecutor(max_workers=workers) as pool:
                return list(pool.map(write, range(workers)))

        disease_value = {
            "disease_key": f"RACE-DISEASE-{uuid.uuid4().hex[:8]}", "name": "Race disease",
            "target_ref": {"kind": "target", "id": seeded["target"]}, "rationale": "Shared scope",
        }
        disease_results = parallel_record("target_disease", disease_value)
        self.assertEqual(len({item["record"]["ref"]["id"] for item in disease_results}), 1)
        versions = [item["program_version"] for item in disease_results]

        protocol_value = {
            "protocol_key": f"RACE-PROTOCOL-{uuid.uuid4().hex[:8]}", "title": "Race protocol",
            "assay_ref": {"kind": "assay", "id": seeded["assay"]},
            "specification": {"replicates": 3},
        }
        protocol_results = parallel_record("protocol_version", protocol_value)
        self.assertEqual(len({item["record"]["ref"]["id"] for item in protocol_results}), 1)
        versions = [item["program_version"] for item in protocol_results]

        release_value = {
            "source_name": "Race source", "release_name": f"RACE-{uuid.uuid4().hex[:8]}",
            "retrieved_at": "2026-08-13T12:00:00Z",
            "payload_artifact_ref": {"kind": "artifact", "id": seeded["artifact"]},
        }
        release_results = parallel_record("evidence_release", release_value)
        self.assertEqual(len({item["record"]["ref"]["id"] for item in release_results}), 1)
        versions = [item["program_version"] for item in release_results]

        evidence_value = {
            "release_ref": release_results[0]["record"]["ref"],
            "source_record_id": f"race-record-{uuid.uuid4().hex[:8]}",
            "target_ref": {"kind": "target", "id": seeded["target"]},
            "disease_ref": disease_results[0]["record"]["ref"],
            "data_type": "genetic_association", "evidence_source": "race",
            "score": 0.8, "payload": {"direction": "supports"},
        }
        evidence_results = parallel_record("external_evidence", evidence_value)
        self.assertEqual(len({item["record"]["ref"]["id"] for item in evidence_results}), 1)
        versions = [item["program_version"] for item in evidence_results]

        sample_value = {
            "sample_code": f"RACE-PHYSICAL-{uuid.uuid4().hex[:8]}",
            "batch_ref": {"kind": "batch", "id": seeded["batch"]},
            "amount_value": 1, "amount_unit": "mg",
        }
        barrier = threading.Barrier(workers)

        def claim_sample(index: int) -> str:
            barrier.wait(timeout=10)
            try:
                self.repo.record_reference_job(
                    programs[index]["ref"], versions[index], "sample", sample_value, self.actor,
                    request_id=str(uuid.uuid4()))
                return "accepted"
            except DiracInvalidParameters:
                return "rejected"

        with ThreadPoolExecutor(max_workers=workers) as pool:
            sample_verdicts = list(pool.map(claim_sample, range(workers)))
        self.assertEqual(sample_verdicts.count("accepted"), 1, sample_verdicts)
        self.assertEqual(sample_verdicts.count("rejected"), workers - 1, sample_verdicts)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
