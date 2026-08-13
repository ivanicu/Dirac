"""Durable result-triggered Motif controller built from ordinary Dirac Jobs."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
from typing import Any, Callable

import failures


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _required_object(value: dict, key: str) -> dict:
    child = value.get(key)
    if not isinstance(child, dict):
        raise failures.DiracInvalidParameters(f"closed_loop.{key} must be an object")
    return child


def _eligible(document: dict[str, Any], accepted_qc: set[str]) -> bool:
    """Return whether an ingested observation is admissible for this training cycle."""
    qc = document.get("qc") or {}
    if qc.get("status") not in accepted_qc:
        return False
    qualifier = document.get("qualifier")
    if qualifier in {"missing", "not_tested"}:
        return False
    quantity = document.get("quantity") or {}
    return any(quantity.get(key) is not None for key in ("value", "lower", "upper"))


class ClosedLoopController:
    """Persist and advance one bounded snapshot→train→predict→acquire loop.

    The controller never runs science itself. Every stage is a regular Method/Job,
    which preserves its own execution identity, artifacts, failure and cancellation.
    """
    kind = "postgres"
    durability = "durable"

    def __init__(self, service: Any, connect: Callable[[], Any]) -> None:
        self.service = service
        self._connect = connect
        self._pool = ThreadPoolExecutor(max_workers=1,
                                        thread_name_prefix="motif-loop-controller")
        self._wake_lock = threading.Lock()
        self._running = False
        self.wake()

    @staticmethod
    def validate_spec(spec: dict[str, Any], measurements: list[dict[str, Any]]) -> None:
        if not isinstance(spec, dict):
            raise failures.DiracInvalidParameters("closed_loop must be an object")
        required = {"request_key", "program_ref", "campaign_ref", "target_ref",
                    "optimization_hypothesis", "snapshot", "train", "candidates",
                    "acquisition"}
        missing = sorted(required - set(spec))
        if missing:
            raise failures.DiracInvalidParameters(
                f"closed_loop misses required fields {missing}")
        if not str(spec["request_key"]).strip():
            raise failures.DiracInvalidParameters("closed_loop.request_key is empty")
        for key, kind in (("program_ref", "program"), ("campaign_ref", "campaign")):
            ref = spec.get(key)
            if not isinstance(ref, dict) or ref.get("kind") != kind or not ref.get("id"):
                raise failures.DiracInvalidParameters(
                    f"closed_loop.{key} must be a {kind} ObjectRef")
        target_ref = spec.get("target_ref")
        if (not isinstance(target_ref, dict) or target_ref.get("kind") != "target"
                or not target_ref.get("id")):
            raise failures.DiracInvalidParameters(
                "closed_loop.target_ref must be a target ObjectRef")
        if not str(spec.get("optimization_hypothesis", "")).strip():
            raise failures.DiracInvalidParameters(
                "closed_loop.optimization_hypothesis must state the design intent")
        snapshot = _required_object(spec, "snapshot")
        endpoint = _required_object(snapshot, "endpoint_definition")
        for key in ("endpoint_key", "version", "canonical_unit", "measurement_type",
                    "target_ref", "direction"):
            if not endpoint.get(key):
                raise failures.DiracInvalidParameters(
                    f"closed_loop.snapshot.endpoint_definition.{key} is required")
        if endpoint["target_ref"] != target_ref:
            raise failures.DiracInvalidParameters(
                "closed-loop endpoint target_ref does not match the optimization target")
        if endpoint["direction"] not in {"minimize", "maximize"}:
            raise failures.DiracInvalidParameters(
                "closed-loop endpoint direction must be minimize or maximize")
        for key in ("identity_policy_release_id", "data_classification",
                    "compound_smiles", "split_assignments"):
            if key not in snapshot:
                raise failures.DiracInvalidParameters(
                    f"closed_loop.snapshot.{key} is required")
        train = _required_object(spec, "train")
        registration = _required_object(train, "registration")
        for key in ("model_object_id", "release_name", "source_commit", "intended_use",
                    "prohibited_use", "known_limitations"):
            if key not in registration:
                raise failures.DiracInvalidParameters(
                    f"closed_loop.train.registration.{key} is required")
        acquisition = _required_object(spec, "acquisition")
        for key in ("objectives", "capacity", "prediction_objective_key"):
            if key not in acquisition:
                raise failures.DiracInvalidParameters(
                    f"closed_loop.acquisition.{key} is required")
        allowed_domain = acquisition.get(
            "accepted_applicability_domain", ["in_domain", "borderline"])
        if (not isinstance(allowed_domain, list) or not allowed_domain
                or not set(allowed_domain) <= {"in_domain", "borderline", "out_of_domain"}):
            raise failures.DiracInvalidParameters(
                "closed_loop.acquisition.accepted_applicability_domain is invalid")
        primary = [item for item in acquisition["objectives"]
                   if item.get("key") == acquisition["prediction_objective_key"]]
        if len(primary) != 1 or primary[0].get("direction") != endpoint["direction"]:
            raise failures.DiracInvalidParameters(
                "prediction objective direction must match the registered endpoint")
        candidates = spec.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise failures.DiracInvalidParameters(
                "closed_loop.candidates must be a non-empty array")
        candidate_ids = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise failures.DiracInvalidParameters("each closed-loop candidate must be an object")
            for key in ("proposal_id", "smiles", "objectives", "constraints", "components"):
                if key not in candidate:
                    raise failures.DiracInvalidParameters(
                        f"closed-loop candidate misses {key}")
            if candidate["proposal_id"] in candidate_ids:
                raise failures.DiracInvalidParameters("closed-loop proposal_id values must be unique")
            candidate_ids.add(candidate["proposal_id"])
        smiles = snapshot["compound_smiles"]
        splits = snapshot["split_assignments"]
        accepted_qc = snapshot.get("accepted_qc_statuses", ["pass", "warn"])
        if (not isinstance(accepted_qc, list) or not accepted_qc
                or not set(accepted_qc) <= {"pass", "warn", "fail", "not_assessed"}):
            raise failures.DiracInvalidParameters(
                "closed_loop.snapshot.accepted_qc_statuses is invalid")
        eligible_count = 0
        for document in measurements:
            if document["endpoint"]["id"] != endpoint["endpoint_key"]:
                raise failures.DiracInvalidParameters(
                    "all triggered measurements must match the closed-loop endpoint")
            if not _eligible(document, set(accepted_qc)):
                continue
            eligible_count += 1
            compound = document.get("compound") or {}
            if not compound.get("id"):
                raise failures.DiracInvalidParameters(
                    "closed-loop measurements require canonical compound refs")
            if compound["id"] not in smiles:
                raise failures.DiracInvalidParameters(
                    f"closed_loop.snapshot.compound_smiles misses {compound['id']}")
            if document["measurement_id"] not in splits:
                raise failures.DiracInvalidParameters(
                    f"closed_loop.snapshot.split_assignments misses {document['measurement_id']}")
        if eligible_count < 3:
            raise failures.DiracInvalidParameters(
                "closed loop requires at least 3 QC-admissible measured compounds")

    def enqueue(self, *, spec: dict[str, Any], measurements: list[dict[str, Any]],
                ingest_result: dict[str, Any], actor: dict[str, str]) -> dict[str, Any]:
        self.validate_spec(spec, measurements)
        self.validate_context(spec)
        digest = _digest({
            "specification": spec,
            "measurement_digests": sorted(
                item["digest"] for item in ingest_result["measurements"]),
        })
        measurement_ids = [item["measurement_ref"]["id"]
                           for item in ingest_result["measurements"]]
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO design.motif_closed_loop_run "
                "(request_key,specification_digest,program_id,campaign_id,endpoint_key,"
                " measurement_ids,specification,actor_kind,actor_id) "
                "VALUES (%s,decode(%s,'hex'),%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (request_key) DO NOTHING RETURNING id,state,stage,created_at",
                (spec["request_key"], digest.removeprefix("sha256:"),
                 spec["program_ref"]["id"], spec["campaign_ref"]["id"],
                 spec["snapshot"]["endpoint_definition"]["endpoint_key"],
                 measurement_ids, json.dumps({"policy": spec, "measurements": measurements}),
                 actor["kind"], actor["id"]))
            row = cur.fetchone()
            created = row is not None
            if row is None:
                cur.execute(
                    "SELECT id,state,stage,created_at,encode(specification_digest,'hex') "
                    "FROM design.motif_closed_loop_run WHERE request_key=%s",
                    (spec["request_key"],))
                row = cur.fetchone()
                if row is None or row[4] != digest.removeprefix("sha256:"):
                    raise failures.DiracInvalidParameters(
                        "closed_loop.request_key already exists with different content")
        self.wake()
        return {"ref": {"kind": "run", "id": str(row[0])},
                "state": row[1], "stage": row[2], "created": created,
                "specification_digest": digest}

    def validate_context(self, spec: dict[str, Any]) -> None:
        """Prove Program, Campaign, Target and Endpoint are one coherent context."""
        endpoint = spec["snapshot"]["endpoint_definition"]
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT p.target_id,c.program_id,e.target_ref,e.direction,e.canonical_unit,"
                "e.measurement_type FROM design.program p "
                "JOIN design.campaign c ON c.id=%s "
                "JOIN design.endpoint_definition e ON e.endpoint_key=%s AND e.version=%s "
                "WHERE p.id=%s",
                (spec["campaign_ref"]["id"], endpoint["endpoint_key"],
                 endpoint["version"], spec["program_ref"]["id"]))
            row = cur.fetchone()
        if row is None:
            raise failures.DiracInvalidParameters(
                "closed-loop Program, Campaign or Endpoint is not registered")
        expected_target = spec["target_ref"]["id"]
        registered_target = (row[2] or {}).get("id") if isinstance(row[2], dict) else None
        if str(row[0]) != expected_target or registered_target != expected_target:
            raise failures.DiracInvalidParameters(
                "closed-loop Program and Endpoint must reference the declared target")
        if str(row[1]) != spec["program_ref"]["id"]:
            raise failures.DiracInvalidParameters(
                "closed-loop Campaign does not belong to the declared Program")
        if (row[3] != endpoint["direction"] or row[4] != endpoint["canonical_unit"]
                or row[5] != endpoint["measurement_type"]):
            raise failures.DiracInvalidParameters(
                "closed-loop endpoint semantics conflict with the registered Endpoint")

    def wake(self) -> None:
        with self._wake_lock:
            if self._running:
                return
            self._running = True
            self._pool.submit(self._drain)

    def get(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id,request_key,program_id,campaign_id,endpoint_key,measurement_ids,"
                "state,stage,stage_jobs,stage_attempts,outputs,attention,created_at,updated_at,finished_at,"
                "encode(specification_digest,'hex') "
                "FROM design.motif_closed_loop_run WHERE id=%s", (run_id,))
            row = cur.fetchone()
        if row is None:
            raise failures.DiracNotFound(
                "closed-loop run does not exist", details={"run_id": run_id})
        outputs = dict(row[10])
        outputs.pop("checkpoint", None)
        return {
            "ref": {"kind": "run", "id": str(row[0])},
            "request_key": row[1],
            "program_ref": {"kind": "program", "id": str(row[2])},
            "campaign_ref": {"kind": "campaign", "id": str(row[3])},
            "endpoint_key": row[4],
            "measurement_ids": [str(value) for value in row[5]],
            "state": row[6], "stage": row[7], "stage_jobs": dict(row[8]),
            "stage_attempts": dict(row[9]), "outputs": outputs,
            "attention": dict(row[11]),
            "created_at": row[12].isoformat(), "updated_at": row[13].isoformat(),
            "finished_at": row[14].isoformat() if row[14] is not None else None,
            "specification_digest": "sha256:" + row[15],
        }

    def retry(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT state,stage,stage_jobs,stage_attempts "
                "FROM design.motif_closed_loop_run WHERE id=%s FOR UPDATE", (run_id,))
            row = cur.fetchone()
            if row is None:
                raise failures.DiracNotFound(
                    "closed-loop run does not exist", details={"run_id": run_id})
            if row[0] != "blocked":
                raise failures.DiracInvalidParameters(
                    "only a blocked closed-loop run can be retried",
                    details={"run_id": run_id, "state": row[0]})
            stage = row[1]
            jobs = dict(row[2])
            attempts = dict(row[3])
            jobs.pop(stage, None)
            attempts[stage] = int(attempts.get(stage, 0)) + 1
            cur.execute(
                "UPDATE design.motif_closed_loop_run SET state='pending',stage_jobs=%s,"
                "stage_attempts=%s,attention='{}'::jsonb,updated_at=now(),finished_at=NULL "
                "WHERE id=%s",
                (json.dumps(jobs), json.dumps(attempts), run_id))
        self.wake()
        return {"ref": {"kind": "run", "id": run_id}, "state": "pending",
                "stage": stage, "stage_attempt": attempts[stage]}

    def _drain(self) -> None:
        try:
            while True:
                item = self._claim()
                if item is None:
                    return
                self._advance(item)
        finally:
            with self._wake_lock:
                self._running = False

    def _claim(self) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id,specification,stage,stage_jobs,stage_attempts,outputs,actor_kind,actor_id "
                "FROM design.motif_closed_loop_run "
                "WHERE state IN ('pending','running') ORDER BY created_at "
                "FOR UPDATE SKIP LOCKED LIMIT 1")
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                "UPDATE design.motif_closed_loop_run SET state='running',updated_at=now() "
                "WHERE id=%s", (row[0],))
            return {"id": str(row[0]), "document": dict(row[1]), "stage": row[2],
                    "jobs": dict(row[3]), "attempts": dict(row[4]),
                    "outputs": dict(row[5]),
                    "actor": {"kind": str(row[6]), "id": row[7]}}

    def _advance(self, item: dict[str, Any]) -> None:
        try:
            spec = item["document"]["policy"]
            measurements = item["document"]["measurements"]
            rows = self._rows(spec, measurements)
            if item["stage"] == "snapshot":
                payload = self._snapshot_payload(spec, rows)
                job = self._stage_job(item, "snapshot", "data.motif.snapshot", payload)
                snapshot = (job["result_summary"]["data"].get("dataset_snapshot") or {})
                if snapshot.get("status") != "valid":
                    raise RuntimeError("dataset snapshot did not become valid")
                item["outputs"]["dataset_snapshot"] = snapshot
                self._move(item, "train")
            if item["stage"] == "train":
                payload = copy.deepcopy(spec["train"])
                payload["endpoint_key"] = spec["snapshot"]["endpoint_definition"]["endpoint_key"]
                payload["rows"] = rows
                payload["registration"]["dataset_snapshot_ref"] = (
                    item["outputs"]["dataset_snapshot"]["ref"])
                job = self._stage_job(item, "train", "ml.motif.mesh.train", payload)
                checkpoint_ref = next(
                    artifact for artifact in job["artifacts"]
                    if artifact["role"] == "model.checkpoint")
                _artifact, checkpoint_bytes = self.service.store.read(checkpoint_ref["id"])
                item["outputs"]["model"] = job["result_summary"]["data"]
                item["outputs"]["checkpoint_artifact_id"] = checkpoint_ref["id"]
                item["outputs"]["checkpoint"] = json.loads(checkpoint_bytes)
                self._move(item, "predict")
            if item["stage"] == "predict":
                payload = {"checkpoint": item["outputs"]["checkpoint"],
                           "smiles": [row["smiles"] for row in spec["candidates"]],
                           "accelerator": spec.get("predict_accelerator", "gpu")}
                job = self._stage_job(item, "predict", "ml.motif.mesh.predict", payload)
                item["outputs"]["predictions"] = job["result_summary"]["data"]["predictions"]
                prediction_ref = next(
                    artifact for artifact in job["artifacts"]
                    if artifact["role"] == "model.predictions")
                item["outputs"]["prediction_artifact_id"] = prediction_ref["id"]
                self._move(item, "acquire")
            if item["stage"] == "acquire":
                acquisition = spec["acquisition"]
                objective_key = acquisition["prediction_objective_key"]
                accepted_domain = set(acquisition.get(
                    "accepted_applicability_domain", ["in_domain", "borderline"]))
                candidates = []
                for source, prediction in zip(spec["candidates"],
                                              item["outputs"]["predictions"]):
                    candidate = {key: copy.deepcopy(source[key]) for key in
                                 ("proposal_id", "objectives", "constraints", "components")}
                    candidate["objectives"][objective_key] = prediction["ensemble"]["mean"]
                    uncertainty_key = acquisition.get("uncertainty_objective_key")
                    if uncertainty_key:
                        candidate["objectives"][uncertainty_key] = (
                            prediction["ensemble"]["epistemic_std"])
                    domain_status = prediction["applicability_domain"]["status"]
                    candidate["constraints"]["model_domain_accepted"] = (
                        domain_status in accepted_domain)
                    candidate["constraints"]["model_domain_status"] = domain_status
                    candidate["components"]["missing_evidence"] = (
                        0.0 if domain_status in accepted_domain else 1.0)
                    candidate["evidence_artifact_ids"] = [
                        item["outputs"]["prediction_artifact_id"]]
                    candidates.append(candidate)
                hard_constraints = copy.deepcopy(
                    acquisition.get("hard_constraints", []))
                if not any(rule.get("key") == "model_domain_accepted"
                           for rule in hard_constraints):
                    hard_constraints.append(
                        {"key": "model_domain_accepted", "equals": True})
                payload = {"candidates": candidates,
                           "objectives": acquisition["objectives"],
                           "hard_constraints": hard_constraints,
                           "capacity": acquisition["capacity"]}
                job = self._stage_job(item, "acquire", "design.motif.acquire", payload)
                item["outputs"]["portfolio"] = job["result_summary"]["data"]
                item["outputs"].pop("checkpoint", None)
                self._complete(item)
        except Exception as error:  # noqa: BLE001
            self._block(item, error)

    @staticmethod
    def _rows(spec: dict[str, Any], measurements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        snapshot = spec["snapshot"]
        accepted_qc = set(snapshot.get("accepted_qc_statuses", ["pass", "warn"]))
        rows = []
        for document in measurements:
            if not _eligible(document, accepted_qc):
                continue
            quantity = document["quantity"]
            qualifier = document["qualifier"]
            rows.append({
                "measurement_id": document["measurement_id"],
                "compound_id": document["compound"]["id"],
                "smiles": snapshot["compound_smiles"][document["compound"]["id"]],
                "endpoint_key": document["endpoint"]["id"],
                "protocol_id": document["protocol"]["id"],
                "unit": quantity["unit"],
                "measurement_type": snapshot["endpoint_definition"]["measurement_type"],
                "value": quantity.get("value"), "lower": quantity.get("lower"),
                "upper": quantity.get("upper"),
                "qualifier": qualifier,
                "split": snapshot["split_assignments"][document["measurement_id"]],
            })
        return rows

    @staticmethod
    def _snapshot_payload(spec: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        snapshot = spec["snapshot"]
        return {
            "selection_query": snapshot.get(
                "selection_query", f"closed-loop:{spec['request_key']}"),
            "endpoint_definitions": [snapshot["endpoint_definition"]],
            "rows": rows,
            "registration": {
                "program_ref": spec["program_ref"], "campaign_ref": spec["campaign_ref"],
                "identity_policy_release_id": snapshot["identity_policy_release_id"],
                "data_classification": snapshot["data_classification"],
            },
        }

    def _stage_job(self, item: dict[str, Any], stage: str, method: str,
                   payload: dict[str, Any]) -> dict[str, Any]:
        job_id = item["jobs"].get(stage)
        if not job_id:
            attempt = int(item["attempts"].get(stage, 0))
            envelope = self.service.submit(
                method, payload,
                request_id=f"closed-loop:{item['id']}:{stage}:{attempt}",
                actor=item["actor"], command_id="result.ingest")
            job_id = envelope["data"]["job"]["id"]
            item["jobs"][stage] = job_id
            self._persist(item)
        job = self.service.wait_job(job_id, timeout=86400, poll=.25)
        if job["state"] != "done":
            raise RuntimeError(
                f"closed-loop {stage} Job {job_id} ended {job['state']}: "
                f"{job.get('error_code')} {job.get('error_detail')}")
        return job

    def _move(self, item: dict[str, Any], stage: str) -> None:
        item["stage"] = stage
        self._persist(item)

    def _persist(self, item: dict[str, Any]) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE design.motif_closed_loop_run SET stage=%s,stage_jobs=%s,"
                "stage_attempts=%s,outputs=%s,updated_at=now() WHERE id=%s",
                (item["stage"], json.dumps(item["jobs"]),
                 json.dumps(item["attempts"]), json.dumps(item["outputs"]), item["id"]))

    def _complete(self, item: dict[str, Any]) -> None:
        item["stage"] = "completed"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE design.motif_closed_loop_run SET state='completed',stage='completed',"
                "stage_jobs=%s,outputs=%s,updated_at=now(),finished_at=now() WHERE id=%s",
                (json.dumps(item["jobs"]), json.dumps(item["outputs"]), item["id"]))

    def _block(self, item: dict[str, Any], error: Exception) -> None:
        attention = {"stage": item["stage"], "error_type": type(error).__name__,
                     "message": str(error), "recoverable": True}
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE design.motif_closed_loop_run SET state='blocked',stage_jobs=%s,"
                "outputs=%s,attention=%s,updated_at=now(),finished_at=now() WHERE id=%s",
                (json.dumps(item["jobs"]), json.dumps(item["outputs"]),
                 json.dumps(attention), item["id"]))


__all__ = ["ClosedLoopController", "_digest"]
