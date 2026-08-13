"""Governed Motif endpoint, objective and measurement persistence.

The command layer owns semantics; this repository owns one transactional database
boundary.  Scientific records are validated before this module is called, encoded as
canonical JSON, registered as immutable Artifacts, written to their domain table and
announced through the outbox in the same PostgreSQL transaction.
"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import uuid
from typing import Any

import failures
from contracts.validation import violations

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "domain" / "motif"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def semantic_digest(value: dict[str, Any]) -> str:
    unsigned = copy.deepcopy(value)
    unsigned.pop("digest", None)
    return "sha256:" + hashlib.sha256(canonical_bytes(unsigned)).hexdigest()


def with_semantic_digest(value: dict[str, Any]) -> dict[str, Any]:
    frozen = copy.deepcopy(value)
    frozen["digest"] = semantic_digest(frozen)
    return frozen


def validate_document(schema_name: str, value: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
    errors = violations(schema, value)
    if errors:
        first = errors[0]
        raise failures.DiracInvalidParameters(
            f"{schema_name}: {first.message}",
            details={"schema": schema_name, "pointer": first.pointer},
        )


def _require_actor(document: dict[str, Any], actor: dict[str, str]) -> None:
    declared = document.get("created_by")
    if declared is not None and declared != actor:
        raise failures.DiracInvalidParameters(
            "created_by must match the authenticated Command actor",
            details={"declared": declared, "authenticated": actor},
        )


def _require_recorded_actor(document: dict[str, Any], actor: dict[str, str]) -> None:
    declared = document.get("recorded_by")
    if declared is not None and declared != actor:
        raise failures.DiracInvalidParameters(
            "recorded_by must match the authenticated Command actor",
            details={"declared": declared, "authenticated": actor},
        )


def _require_digest(document: dict[str, Any]) -> str:
    expected = semantic_digest(document)
    if document.get("digest") != expected:
        raise failures.DiracInvalidParameters(
            "document digest does not match its canonical semantic content",
            details={"expected": expected, "received": document.get("digest")},
        )
    return expected


def _artifact_ref(artifact_id: str, digest: str, role: str, size: int) -> dict[str, Any]:
    return {
        "kind": "artifact",
        "id": artifact_id,
        "sha256": digest,
        "role": role,
        "media_type": "application/json",
        "size_bytes": size,
    }


class MemoryMotifGovernanceStore:
    """Semantically faithful process-local store used by focused tests."""

    kind = "memory"
    durability = "process"

    def __init__(self) -> None:
        self.endpoints: dict[tuple[str, str], dict[str, Any]] = {}
        self.policies: dict[str, dict[str, Any]] = {}
        self.objectives: dict[str, dict[str, Any]] = {}
        self.measurements: dict[str, dict[str, Any]] = {}
        self.datasets: dict[str, dict[str, Any]] = {}
        self.models: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _artifact(document: dict[str, Any], role: str) -> dict[str, Any]:
        payload = canonical_bytes(document)
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dirac:{role}:{digest}"))
        return _artifact_ref(identifier, digest, role, len(payload))

    def register_endpoint(self, document: dict[str, Any], actor: dict[str, str]) -> dict:
        validate_document("endpoint-definition.schema.json", document)
        _require_actor(document, actor)
        _require_digest(document)
        key = (document["endpoint_key"], document["version"])
        existing = self.endpoints.get(key)
        if existing is not None and existing["document"] != document:
            raise failures.DiracInvalidParameters(
                "endpoint key/version already exists with different content")
        created = existing is None
        if created:
            row_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dirac:endpoint:{key}"))
            existing = {"id": row_id, "document": copy.deepcopy(document),
                        "artifact": self._artifact(document, "design.endpoint.definition")}
            self.endpoints[key] = existing
        return self._endpoint_result(existing, created)

    @staticmethod
    def _endpoint_result(row: dict[str, Any], created: bool) -> dict:
        doc = row["document"]
        return {
            "endpoint": {"id": doc["endpoint_key"], "version": doc["version"]},
            "endpoint_definition_id": row["id"], "digest": doc["digest"],
            "artifact": row["artifact"], "created": created,
        }

    def save_objective(self, document: dict[str, Any], actor: dict[str, str]) -> dict:
        validate_document("design-brief.schema.json", document)
        _require_actor(document, actor)
        _require_digest(document)
        for objective in document["objectives"]:
            endpoint = objective["endpoint"]
            if (endpoint["id"], endpoint["version"]) not in self.endpoints:
                raise failures.DiracInvalidParameters(
                    "objective references an unregistered endpoint definition",
                    details={"endpoint": endpoint},
                )
        expected_policies = dict(document["policy_releases"])
        expected_policies["identity_gate"] = document["chemistry_constraints"][
            "identity_policy_release_id"]
        for kind, identifier in expected_policies.items():
            policy = self.policies.get(identifier)
            if policy is None or policy["document"]["policy_kind"] != kind:
                raise failures.DiracInvalidParameters(
                    "objective references a missing or wrong-kind policy release",
                    details={"policy_kind": kind, "policy_release_id": identifier},
                )
        identifier = document["objective_spec_id"]
        existing = self.objectives.get(identifier)
        if existing is not None and existing["document"] != document:
            raise failures.DiracInvalidParameters(
                "objective_spec_id already exists with different content")
        created = existing is None
        if created:
            existing = {"document": copy.deepcopy(document),
                        "artifact": self._artifact(document, "design.objective.spec")}
            self.objectives[identifier] = existing
        return {
            "objective_spec_id": identifier,
            "objective": {"campaign": document["campaign"], "target": document["target"]},
            "digest": document["digest"], "artifact": existing["artifact"],
            "created": created,
        }

    def register_policy(self, document: dict[str, Any], actor: dict[str, str]) -> dict:
        validate_document("policy-release.schema.json", document)
        _require_actor(document, actor)
        _require_digest(document)
        identifier = document["policy_release_id"]
        existing = self.policies.get(identifier)
        if existing is not None and existing["document"] != document:
            raise failures.DiracInvalidParameters(
                "policy_release_id already exists with different content")
        created = existing is None
        if created:
            existing = {"document": copy.deepcopy(document),
                        "artifact": self._artifact(document, "policy.spec")}
            self.policies[identifier] = existing
        return {"policy_release_id": identifier, "policy_kind": document["policy_kind"],
                "name": document["name"], "version": document["version"],
                "lifecycle": document["lifecycle"], "digest": document["digest"],
                "artifact": existing["artifact"], "created": created}

    def ingest_measurements(self, documents: list[dict[str, Any]],
                            actor: dict[str, str]) -> dict:
        results = []
        created_count = 0
        for document in documents:
            validate_document("measurement-v2.schema.json", document)
            _require_recorded_actor(document, actor)
            digest = "sha256:" + hashlib.sha256(canonical_bytes(document)).hexdigest()
            key = document["measurement_id"]
            endpoint_ref = document["endpoint"]
            endpoint = self.endpoints.get((endpoint_ref["id"], endpoint_ref["version"]))
            if endpoint is None:
                raise failures.DiracInvalidParameters(
                    "measurement references an unregistered endpoint definition",
                    details={"endpoint": endpoint_ref},
                )
            endpoint_document = endpoint["document"]
            if (document["assay"] != endpoint_document["assay"]
                    or document["protocol"] != endpoint_document["protocol"]):
                raise failures.DiracInvalidParameters(
                    "measurement assay/protocol does not match its endpoint definition")
            existing = self.measurements.get(key)
            if existing is not None and existing["digest"] != digest:
                raise failures.DiracInvalidParameters(
                    "measurement_id already exists with different content",
                    details={"measurement_id": key},
                )
            created = existing is None
            if created:
                created_count += 1
                existing = {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"dirac:measurement:{key}")),
                    "digest": digest,
                    "artifact": self._artifact(document, "bio.measurement.v2"),
                }
                self.measurements[key] = existing
            results.append({
                "measurement_ref": {"kind": "measurement", "id": existing["id"]},
                "measurement_id": key, "digest": digest,
                "artifact": existing["artifact"], "created": created,
            })
        return {"measurements": results, "created_count": created_count,
                "deduplicated_count": len(results) - created_count}

    def project_completion(self, *, method_id: str, payload: dict,
                           result: dict, artifacts: list[dict],
                           envelope_meta: dict, actor: dict[str, str],
                           job_id: str | None) -> dict:
        if method_id == "data.motif.snapshot":
            manifest = result["manifest"]
            registration = payload["registration"]
            policy = self.policies.get(registration["identity_policy_release_id"])
            if policy is None or policy["document"]["policy_kind"] != "identity_gate":
                raise failures.DiracInvalidParameters(
                    "dataset identity policy is missing or has the wrong kind")
            for endpoint in payload["endpoint_definitions"]:
                registered = self.endpoints.get(
                    (endpoint["endpoint_key"], endpoint["version"]))
                if registered is None:
                    raise failures.DiracInvalidParameters(
                        "dataset endpoint is not registered")
            digest = manifest["manifest_digest"]
            existing = self.datasets.get(digest)
            created = existing is None
            if created:
                identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dirac:dataset:{digest}"))
                existing = {"id": identifier, "status": (
                    "valid" if manifest["leakage"]["valid"] else "invalid"),
                    "data_digest": manifest["data_digest"],
                    "endpoint_keys": set(manifest["endpoint_keys"])}
                self.datasets[digest] = existing
            return {"dataset_snapshot": {
                "ref": {"kind": "dataset", "id": existing["id"]},
                "digest": digest, "status": existing["status"], "created": created}}
        if method_id == "ml.motif.train":
            registration = payload["registration"]
            snapshot_id = registration["dataset_snapshot_ref"]["id"]
            snapshot = next((item for item in self.datasets.values()
                             if item["id"] == snapshot_id), None)
            if snapshot is None or snapshot["status"] != "valid":
                raise failures.DiracInvalidParameters(
                    "model release requires a valid Dataset Snapshot")
            rows_digest = "sha256:" + hashlib.sha256(
                canonical_bytes(payload["rows"])).hexdigest()
            if rows_digest != snapshot["data_digest"]:
                raise failures.DiracInvalidParameters(
                    "training rows do not match the Dataset Snapshot data Artifact")
            if payload["endpoint_key"] not in snapshot["endpoint_keys"]:
                raise failures.DiracInvalidParameters(
                    "training endpoint is not part of the Dataset Snapshot")
            identifier = registration["model_object_id"]
            key = f"{identifier}:{registration['release_name']}"
            existing = self.models.get(key)
            created = existing is None
            if created:
                existing = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"dirac:model:{key}"))}
                self.models[key] = existing
            return {"model_release": {
                "ref": {"kind": "model", "id": identifier},
                "model_release_id": existing["id"], "lifecycle": "candidate",
                "created": created}}
        return {}


class PostgresMotifGovernanceStore:
    kind = "postgres"
    durability = "durable"

    def __init__(self, connect) -> None:
        self._connect = connect

    @staticmethod
    def _put_json(cur, document: dict[str, Any], role: str) -> dict[str, Any]:
        from psycopg.types.json import Jsonb

        payload = canonical_bytes(document)
        digest_hex = hashlib.sha256(payload).hexdigest()
        cur.execute(
            "INSERT INTO app.blob (sha256, media_type, byte_len, bytes) "
            "VALUES (decode(%s,'hex'),'application/json',%s,%s) "
            "ON CONFLICT (sha256) DO NOTHING",
            (digest_hex, len(payload), payload),
        )
        cur.execute(
            "INSERT INTO app.artifact "
            "(blob_sha256,media_type,role,size_bytes,metadata) "
            "VALUES (decode(%s,'hex'),'application/json',%s,%s,%s) "
            "ON CONFLICT (blob_sha256,role,encoding) DO NOTHING RETURNING id",
            (digest_hex, role, len(payload), Jsonb({"schema_version": document.get("schema_version")})),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "SELECT id FROM app.artifact WHERE blob_sha256=decode(%s,'hex') "
                "AND role=%s AND encoding='identity'", (digest_hex, role),
            )
            row = cur.fetchone()
        if row is None:  # pragma: no cover - protected by the unique key
            raise failures.DiracInternal("artifact upsert did not resolve an id")
        return _artifact_ref(str(row[0]), "sha256:" + digest_hex, role, len(payload))

    @staticmethod
    def _event(cur, *, key: str, kind: str, identifier: str,
               event_type: str, payload: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb
        cur.execute(
            "INSERT INTO app.outbox_event "
            "(event_key,aggregate_kind,aggregate_id,event_type,payload) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (event_key) DO NOTHING",
            (key, kind, identifier, event_type, Jsonb(payload)),
        )

    def register_endpoint(self, document: dict[str, Any], actor: dict[str, str]) -> dict:
        from psycopg.types.json import Jsonb
        validate_document("endpoint-definition.schema.json", document)
        _require_actor(document, actor)
        digest = _require_digest(document)
        raw_digest = bytes.fromhex(digest.removeprefix("sha256:"))
        with self._connect() as conn, conn.cursor() as cur:
            artifact = self._put_json(cur, document, "design.endpoint.definition")
            cur.execute(
                "INSERT INTO design.endpoint_definition "
                "(endpoint_key,version,assay_id,protocol_ref,target_ref,species,"
                " biological_system,readout,measurement_type,direction,canonical_unit,"
                " quantity_dimension,label_transform,censoring_policy,replicate_policy,"
                " qc_policy,intended_domain,digest,created_at,created_by_kind,created_by_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (endpoint_key,version) DO NOTHING RETURNING id",
                (document["endpoint_key"], document["version"], document["assay"]["id"],
                 Jsonb(document["protocol"]), Jsonb(document.get("target")) if document.get("target") else None,
                 document.get("species"), document["biological_system"], document["readout"],
                 document["measurement_type"], document["direction"], document["canonical_unit"],
                 document["quantity_dimension"], Jsonb(document["label_transform"]),
                 Jsonb(document["censoring_policy"]), Jsonb(document["replicate_policy"]),
                 Jsonb(document["qc_policy"]), Jsonb(document["intended_domain"]), raw_digest,
                 document["created_at"], actor["kind"], actor["id"]),
            )
            row = cur.fetchone()
            created = row is not None
            if row is None:
                cur.execute(
                    "SELECT id,digest FROM design.endpoint_definition "
                    "WHERE endpoint_key=%s AND version=%s",
                    (document["endpoint_key"], document["version"]),
                )
                row = cur.fetchone()
                if row is None or bytes(row[1]) != raw_digest:
                    raise failures.DiracInvalidParameters(
                        "endpoint key/version already exists with different content")
            endpoint_id = str(row[0])
            self._event(cur, key=f"motif.endpoint.registered:{endpoint_id}:{digest}",
                        kind="endpoint", identifier=endpoint_id,
                        event_type="motif.endpoint.registered",
                        payload={"endpoint_key": document["endpoint_key"],
                                 "version": document["version"], "digest": digest,
                                 "artifact_id": artifact["id"]})
        return {"endpoint": {"id": document["endpoint_key"], "version": document["version"]},
                "endpoint_definition_id": endpoint_id, "digest": digest,
                "artifact": artifact, "created": created}

    def save_objective(self, document: dict[str, Any], actor: dict[str, str]) -> dict:
        from psycopg.types.json import Jsonb
        validate_document("design-brief.schema.json", document)
        _require_actor(document, actor)
        digest = _require_digest(document)
        raw_digest = bytes.fromhex(digest.removeprefix("sha256:"))
        identifier = document["objective_spec_id"]
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT c.program_id,p.target_id FROM design.campaign c "
                "JOIN design.project p ON p.id=c.program_id WHERE c.id=%s",
                (document["campaign"]["id"],),
            )
            campaign = cur.fetchone()
            if (campaign is None or str(campaign[0]) != document["program"]["id"]
                    or str(campaign[1]) != document["target"]["id"]):
                raise failures.DiracInvalidParameters(
                    "objective program/campaign/target references are not coherent")
            for objective in document["objectives"]:
                endpoint = objective["endpoint"]
                cur.execute(
                    "SELECT direction FROM design.endpoint_definition "
                    "WHERE endpoint_key=%s AND version=%s",
                    (endpoint["id"], endpoint["version"]),
                )
                endpoint_row = cur.fetchone()
                if endpoint_row is None:
                    raise failures.DiracInvalidParameters(
                        "objective references an unregistered endpoint definition",
                        details={"endpoint": endpoint},
                    )
                if endpoint_row[0] != objective["direction"]:
                    raise failures.DiracInvalidParameters(
                        "objective direction conflicts with its endpoint definition",
                        details={"endpoint": endpoint, "endpoint_direction": endpoint_row[0],
                                 "objective_direction": objective["direction"]},
                    )
            expected_policies = dict(document["policy_releases"])
            expected_policies["identity_gate"] = document["chemistry_constraints"][
                "identity_policy_release_id"]
            for kind, policy_id in expected_policies.items():
                cur.execute("SELECT policy_kind::text FROM meta.policy_release WHERE id=%s",
                            (policy_id,))
                policy = cur.fetchone()
                if policy is None or policy[0] != kind:
                    raise failures.DiracInvalidParameters(
                        "objective references a missing or wrong-kind policy release",
                        details={"policy_kind": kind, "policy_release_id": policy_id},
                    )
            artifact = self._put_json(cur, document, "design.objective.spec")
            cur.execute(
                "INSERT INTO design.objective_spec "
                "(id,schema_version,program_id,campaign_id,target_ref,spec_artifact_id,"
                " digest,supersedes_id,created_at,created_by_kind,created_by_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (id) DO NOTHING RETURNING id",
                (identifier, document["schema_version"], document["program"]["id"],
                 document["campaign"]["id"], Jsonb(document["target"]), artifact["id"],
                 raw_digest, document.get("supersedes_objective_spec_id"), document["created_at"],
                 actor["kind"], actor["id"]),
            )
            row = cur.fetchone()
            created = row is not None
            if row is None:
                cur.execute("SELECT id,digest FROM design.objective_spec WHERE id=%s", (identifier,))
                row = cur.fetchone()
                if row is None or bytes(row[1]) != raw_digest:
                    raise failures.DiracInvalidParameters(
                        "objective_spec_id already exists with different content")
            self._event(cur, key=f"motif.objective.saved:{identifier}:{digest}",
                        kind="objective_spec", identifier=identifier,
                        event_type="motif.objective.saved",
                        payload={"program": document["program"], "campaign": document["campaign"],
                                 "target": document["target"], "digest": digest,
                                 "artifact_id": artifact["id"]})
        return {"objective_spec_id": identifier,
                "objective": {"campaign": document["campaign"], "target": document["target"]},
                "digest": digest, "artifact": artifact, "created": created}

    def register_policy(self, document: dict[str, Any], actor: dict[str, str]) -> dict:
        validate_document("policy-release.schema.json", document)
        _require_actor(document, actor)
        digest = _require_digest(document)
        raw_digest = bytes.fromhex(digest.removeprefix("sha256:"))
        identifier = document["policy_release_id"]
        with self._connect() as conn, conn.cursor() as cur:
            artifact = self._put_json(cur, document, "policy.spec")
            cur.execute(
                "INSERT INTO meta.policy_release "
                "(id,policy_kind,name,version,lifecycle,spec_artifact_id,digest,created_at,"
                " created_by_kind,created_by_id) "
                "VALUES (%s,%s,%s,%s,'candidate',%s,%s,%s,%s,%s) "
                "ON CONFLICT (id) DO NOTHING RETURNING id",
                (identifier, document["policy_kind"], document["name"], document["version"],
                 artifact["id"], raw_digest, document["created_at"],
                 actor["kind"], actor["id"]),
            )
            row = cur.fetchone()
            created = row is not None
            if row is None:
                cur.execute("SELECT id,digest FROM meta.policy_release WHERE id=%s", (identifier,))
                row = cur.fetchone()
                if row is None or bytes(row[1]) != raw_digest:
                    raise failures.DiracInvalidParameters(
                        "policy_release_id already exists with different content")
            self._event(cur, key=f"motif.policy.registered:{identifier}:{digest}",
                        kind="policy_release", identifier=identifier,
                        event_type="motif.policy.registered",
                        payload={"policy_kind": document["policy_kind"],
                                 "name": document["name"], "version": document["version"],
                                 "digest": digest, "artifact_id": artifact["id"]})
        return {"policy_release_id": identifier, "policy_kind": document["policy_kind"],
                "name": document["name"], "version": document["version"],
                "lifecycle": "candidate", "digest": digest,
                "artifact": artifact, "created": created}

    def ingest_measurements(self, documents: list[dict[str, Any]],
                            actor: dict[str, str]) -> dict:
        from psycopg.types.json import Jsonb
        for document in documents:
            validate_document("measurement-v2.schema.json", document)
            _require_recorded_actor(document, actor)
        results: list[dict[str, Any]] = []
        created_count = 0
        with self._connect() as conn, conn.cursor() as cur:
            for document in documents:
                payload_digest = "sha256:" + hashlib.sha256(canonical_bytes(document)).hexdigest()
                raw_digest = bytes.fromhex(payload_digest.removeprefix("sha256:"))
                artifact = self._put_json(cur, document, "bio.measurement.v2")
                cur.execute(
                    "SELECT id,assay_id,protocol_ref FROM design.endpoint_definition "
                    "WHERE endpoint_key=%s AND version=%s",
                    (document["endpoint"]["id"], document["endpoint"]["version"]),
                )
                endpoint = cur.fetchone()
                if endpoint is None:
                    raise failures.DiracInvalidParameters(
                        "measurement references an unregistered endpoint definition",
                        details={"endpoint": document["endpoint"]},
                    )
                if (str(endpoint[1]) != document["assay"]["id"]
                        or endpoint[2] != document["protocol"]):
                    raise failures.DiracInvalidParameters(
                        "measurement assay/protocol does not match its endpoint definition")
                quantity = document["quantity"]
                cur.execute(
                    "INSERT INTO bio.measurement_v2 "
                    "(measurement_key,endpoint_definition_id,sample_ref,batch_id,compound_id,"
                    " assay_id,protocol_ref,qualifier,value_num,lower_num,upper_num,unit,"
                    " quantity_dimension,qc_status,qc_reason_codes,missing_reason,value_status,"
                    " measured_at,source_artifact_id,payload_artifact_id,payload,digest,"
                    " recorded_at,recorded_by_kind,recorded_by_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    " coalesce(%s,now()),%s,%s) "
                    "ON CONFLICT (measurement_key) DO NOTHING RETURNING id",
                    (document["measurement_id"], endpoint[0], Jsonb(document["sample"]),
                     (document.get("batch") or {}).get("id"),
                     (document.get("compound") or {}).get("id"), document["assay"]["id"],
                     Jsonb(document["protocol"]), document["qualifier"], quantity.get("value"),
                     quantity.get("lower"), quantity.get("upper"), quantity["unit"],
                     quantity.get("dimension"), document["qc"]["status"],
                     document["qc"]["reason_codes"], document.get("missing_reason"),
                     document.get("value_status", "raw"), document["measured_at"],
                     document["source"]["artifact_id"], artifact["id"], Jsonb(document),
                     raw_digest, document.get("recorded_at"), actor["kind"], actor["id"]),
                )
                row = cur.fetchone()
                created = row is not None
                if row is None:
                    cur.execute(
                        "SELECT id,digest,payload_artifact_id FROM bio.measurement_v2 "
                        "WHERE measurement_key=%s", (document["measurement_id"],),
                    )
                    row = cur.fetchone()
                    if row is None or bytes(row[1]) != raw_digest:
                        raise failures.DiracInvalidParameters(
                            "measurement_id already exists with different content",
                            details={"measurement_id": document["measurement_id"]},
                        )
                else:
                    created_count += 1
                identifier = str(row[0])
                self._event(cur,
                            key=f"motif.measurement.ingested:{identifier}:{payload_digest}",
                            kind="measurement", identifier=identifier,
                            event_type="motif.measurement.ingested",
                            payload={"measurement_id": document["measurement_id"],
                                     "digest": payload_digest, "artifact_id": artifact["id"],
                                     "qualifier": document["qualifier"],
                                     "qc_status": document["qc"]["status"]})
                results.append({
                    "measurement_ref": {"kind": "measurement", "id": identifier},
                    "measurement_id": document["measurement_id"], "digest": payload_digest,
                    "artifact": artifact, "created": created,
                })
        return {"measurements": results, "created_count": created_count,
                "deduplicated_count": len(results) - created_count}

    @staticmethod
    def _artifact_roles(artifacts: list[dict]) -> dict[str, dict]:
        roles = {item["role"]: item for item in artifacts}
        if any(not item.get("id") for item in roles.values()):
            raise failures.DiracInternal(
                "governed completion requires durable Artifact IDs")
        return roles

    def project_completion(self, *, method_id: str, payload: dict,
                           result: dict, artifacts: list[dict],
                           envelope_meta: dict, actor: dict[str, str],
                           job_id: str | None) -> dict:
        if method_id == "data.motif.snapshot":
            return self._register_dataset_completion(
                payload, result, artifacts, actor=actor, job_id=job_id)
        if method_id == "ml.motif.train":
            return self._register_model_completion(
                payload, result, artifacts, envelope_meta=envelope_meta,
                actor=actor, job_id=job_id)
        return {}

    def _register_dataset_completion(self, payload: dict, result: dict,
                                     artifacts: list[dict], *,
                                     actor: dict[str, str], job_id: str | None) -> dict:
        registration = payload["registration"]
        manifest = result["manifest"]
        digest = manifest["manifest_digest"]
        raw_digest = bytes.fromhex(digest.removeprefix("sha256:"))
        roles = self._artifact_roles(artifacts)
        required_roles = {"dataset.rows", "dataset.manifest", "dataset.split_manifest",
                          "dataset.leakage_report"}
        if missing := required_roles - set(roles):
            raise failures.DiracInternal(
                f"dataset registration is missing Artifacts {sorted(missing)}")
        status = "valid" if manifest["leakage"]["valid"] else "invalid"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT c.program_id FROM design.campaign c WHERE c.id=%s",
                (registration["campaign_ref"]["id"],),
            )
            campaign = cur.fetchone()
            if campaign is None or str(campaign[0]) != registration["program_ref"]["id"]:
                raise failures.DiracInvalidParameters(
                    "dataset Program/Campaign references are not coherent")
            cur.execute("SELECT policy_kind::text FROM meta.policy_release WHERE id=%s",
                        (registration["identity_policy_release_id"],))
            policy = cur.fetchone()
            if policy is None or policy[0] != "identity_gate":
                raise failures.DiracInvalidParameters(
                    "dataset identity policy is missing or has the wrong kind")
            endpoint_rows = []
            for endpoint in payload["endpoint_definitions"]:
                cur.execute(
                    "SELECT id,canonical_unit,measurement_type FROM design.endpoint_definition "
                    "WHERE endpoint_key=%s AND version=%s",
                    (endpoint["endpoint_key"], endpoint["version"]),
                )
                row = cur.fetchone()
                if (row is None or row[1] != endpoint["canonical_unit"]
                        or row[2] != endpoint["measurement_type"]):
                    raise failures.DiracInvalidParameters(
                        "dataset endpoint is missing or conflicts with its registered definition",
                        details={"endpoint_key": endpoint["endpoint_key"],
                                 "version": endpoint["version"]},
                    )
                endpoint_rows.append((row[0], endpoint["endpoint_key"]))
            cur.execute(
                "INSERT INTO app.dataset_snapshot "
                "(program_id,campaign_id,schema_version,selection_query,selection_query_digest,"
                " identity_policy_release_id,manifest_artifact_id,data_artifact_id,"
                " split_manifest_artifact_id,leakage_report_artifact_id,row_count,status,"
                " data_classification,digest,created_by_kind,created_by_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (digest) DO NOTHING RETURNING id",
                (registration["program_ref"]["id"], registration["campaign_ref"]["id"],
                 manifest["schema_version"], manifest["selection_query"],
                 bytes.fromhex(manifest["selection_query_digest"].removeprefix("sha256:")),
                 registration["identity_policy_release_id"], roles["dataset.manifest"]["id"],
                 roles["dataset.rows"]["id"], roles["dataset.split_manifest"]["id"],
                 roles["dataset.leakage_report"]["id"], manifest["row_count"], status,
                 registration["data_classification"], raw_digest,
                 actor["kind"], actor["id"]),
            )
            row = cur.fetchone()
            created = row is not None
            if row is None:
                cur.execute(
                    "SELECT id,status,program_id,campaign_id,manifest_artifact_id,data_artifact_id "
                    "FROM app.dataset_snapshot WHERE digest=%s", (raw_digest,))
                row = cur.fetchone()
                if (row is None or str(row[2]) != registration["program_ref"]["id"]
                        or str(row[3]) != registration["campaign_ref"]["id"]
                        or str(row[4]) != roles["dataset.manifest"]["id"]
                        or str(row[5]) != roles["dataset.rows"]["id"]):
                    raise failures.DiracInvalidParameters(
                        "dataset digest already exists with different registration context")
                status = row[1]
            snapshot_id = str(row[0])
            counts = manifest.get("endpoint_counts") or {}
            for endpoint_id, endpoint_key in endpoint_rows:
                cur.execute(
                    "INSERT INTO app.dataset_snapshot_endpoint "
                    "(dataset_snapshot_id,endpoint_definition_id,row_count,summary) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (snapshot_id, endpoint_id, int(counts.get(endpoint_key, 0)),
                     json.dumps({"endpoint_key": endpoint_key})),
                )
            if job_id:
                cur.execute(
                    "INSERT INTO app.object_relation "
                    "(source_kind,source_id,relation,target_kind,target_id,actor_kind,actor_id) "
                    "VALUES ('dataset',%s,'generated_by','job',%s,%s,%s) "
                    "ON CONFLICT DO NOTHING", (snapshot_id, job_id, actor["kind"], actor["id"]),
                )
            self._event(cur, key=f"motif.dataset.registered:{snapshot_id}:{digest}",
                        kind="dataset", identifier=snapshot_id,
                        event_type="motif.dataset.registered",
                        payload={"digest": digest, "status": status,
                                 "row_count": manifest["row_count"],
                                 "job_id": job_id, "artifact_ids": {
                                     role: roles[role]["id"] for role in sorted(required_roles)}})
        return {"dataset_snapshot": {
            "ref": {"kind": "dataset", "id": snapshot_id}, "digest": digest,
            "status": status, "created": created}}

    def _register_model_completion(self, payload: dict, result: dict,
                                   artifacts: list[dict], *, envelope_meta: dict,
                                   actor: dict[str, str], job_id: str | None) -> dict:
        from psycopg.types.json import Jsonb

        registration = payload["registration"]
        snapshot_id = registration["dataset_snapshot_ref"]["id"]
        roles = self._artifact_roles(artifacts)
        required_roles = {"model.checkpoint", "model.validation", "model.runtime_lock"}
        if missing := required_roles - set(roles):
            raise failures.DiracInternal(
                f"model registration is missing Artifacts {sorted(missing)}")
        execution_digest = envelope_meta.get("execution_digest")
        if not isinstance(execution_digest, str) or not execution_digest.startswith("sha256:"):
            raise failures.DiracInternal("model completion has no execution digest")
        runtime_digest = roles["model.runtime_lock"]["sha256"]
        if not runtime_digest.startswith("sha256:"):
            runtime_digest = "sha256:" + runtime_digest
        if runtime_digest != result["runtime_lock_digest"]:
            raise failures.DiracInternal(
                "runtime lock result digest does not match its stored Artifact")
        row_data_digest = hashlib.sha256(canonical_bytes(payload["rows"])).digest()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ds.status,a.blob_sha256 FROM app.dataset_snapshot ds "
                "JOIN app.artifact a ON a.id=ds.data_artifact_id WHERE ds.id=%s",
                (snapshot_id,),
            )
            snapshot = cur.fetchone()
            if snapshot is None:
                raise failures.DiracInvalidParameters(
                    "model release references an unknown Dataset Snapshot")
            if snapshot[0] != "valid":
                raise failures.DiracInvalidParameters(
                    "model release requires a Dataset Snapshot with status valid",
                    details={"dataset_snapshot_id": snapshot_id, "status": snapshot[0]},
                )
            if bytes(snapshot[1]) != row_data_digest:
                raise failures.DiracInvalidParameters(
                    "training rows do not match the Dataset Snapshot data Artifact",
                    details={"dataset_snapshot_id": snapshot_id,
                             "training_rows_digest": "sha256:" + row_data_digest.hex(),
                             "snapshot_data_digest": "sha256:" + bytes(snapshot[1]).hex()},
                )
            cur.execute(
                "SELECT e.id FROM app.dataset_snapshot_endpoint dse "
                "JOIN design.endpoint_definition e ON e.id=dse.endpoint_definition_id "
                "WHERE dse.dataset_snapshot_id=%s AND e.endpoint_key=%s",
                (snapshot_id, payload["endpoint_key"]),
            )
            if cur.fetchone() is None:
                raise failures.DiracInvalidParameters(
                    "training endpoint is not part of the Dataset Snapshot")
            cur.execute(
                "SELECT id FROM meta.method WHERE method_id='ml.motif.train' AND version=%s",
                (envelope_meta.get("version"),),
            )
            method = cur.fetchone()
            if method is None:
                raise failures.DiracInternal(
                    "the executing ml.motif.train Method version is not registered")
            raw_execution = bytes.fromhex(execution_digest.removeprefix("sha256:"))
            cur.execute(
                "SELECT id,model_object_id,release_name FROM meta.model_release "
                "WHERE execution_digest=%s", (raw_execution,),
            )
            existing_execution = cur.fetchone()
            if existing_execution is not None:
                if (existing_execution[1] != registration["model_object_id"]
                        or existing_execution[2] != registration["release_name"]):
                    raise failures.DiracInvalidParameters(
                        "execution digest is already assigned to a different model release")
                release_id = str(existing_execution[0])
                created = False
            else:
                cur.execute(
                    "INSERT INTO meta.model_release "
                    "(model_object_id,release_name,lifecycle,method_row_id,source_commit,"
                    " container_image_digest,runtime_kind,runtime_lock_artifact_id,"
                    " lockfile_digest,featurizer_digest,checkpoint_artifact_id,"
                    " validation_artifact_id,execution_digest,intended_use,prohibited_use,"
                    " known_limitations,created_by_kind,created_by_id) "
                    "VALUES (%s,%s,'candidate',%s,%s,NULL,'local_env',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (model_object_id,release_name) DO NOTHING RETURNING id",
                    (registration["model_object_id"], registration["release_name"], method[0],
                     registration["source_commit"], roles["model.runtime_lock"]["id"],
                     bytes.fromhex(runtime_digest.removeprefix("sha256:")),
                     bytes.fromhex(result["featurizer_digest"].removeprefix("sha256:")),
                     roles["model.checkpoint"]["id"], roles["model.validation"]["id"],
                     raw_execution, Jsonb(registration["intended_use"]),
                     Jsonb(registration["prohibited_use"]),
                     Jsonb(registration["known_limitations"]), actor["kind"], actor["id"]),
                )
                row = cur.fetchone()
                created = row is not None
                if row is None:
                    cur.execute(
                        "SELECT id,execution_digest FROM meta.model_release "
                        "WHERE model_object_id=%s AND release_name=%s",
                        (registration["model_object_id"], registration["release_name"]),
                    )
                    row = cur.fetchone()
                    if row is None or bytes(row[1]) != raw_execution:
                        raise failures.DiracInvalidParameters(
                            "model object/release name already exists with different execution")
                release_id = str(row[0])
            cur.execute(
                "INSERT INTO meta.model_release_dataset "
                "(model_release_id,dataset_snapshot_id,role) VALUES (%s,%s,'train') "
                "ON CONFLICT DO NOTHING", (release_id, snapshot_id),
            )
            if job_id:
                cur.execute(
                    "INSERT INTO app.object_relation "
                    "(source_kind,source_id,relation,target_kind,target_id,actor_kind,actor_id) "
                    "VALUES ('model',%s,'generated_by','job',%s,%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    (registration["model_object_id"], job_id, actor["kind"], actor["id"]),
                )
            cur.execute(
                "INSERT INTO app.object_relation "
                "(source_kind,source_id,relation,target_kind,target_id,actor_kind,actor_id) "
                "VALUES ('model',%s,'used','dataset',%s,%s,%s) ON CONFLICT DO NOTHING",
                (registration["model_object_id"], snapshot_id, actor["kind"], actor["id"]),
            )
            self._event(cur, key=f"motif.model.registered:{release_id}:{execution_digest}",
                        kind="model_release", identifier=release_id,
                        event_type="motif.model.registered",
                        payload={"model_object_id": registration["model_object_id"],
                                 "release_name": registration["release_name"],
                                 "lifecycle": "candidate", "dataset_snapshot_id": snapshot_id,
                                 "execution_digest": execution_digest, "job_id": job_id,
                                 "artifact_ids": {role: roles[role]["id"]
                                                  for role in sorted(required_roles)}})
        return {"model_release": {
            "ref": {"kind": "model", "id": registration["model_object_id"]},
            "model_release_id": release_id, "lifecycle": "candidate",
            "created": created}}


__all__ = [
    "MemoryMotifGovernanceStore", "PostgresMotifGovernanceStore", "canonical_bytes",
    "semantic_digest", "validate_document", "with_semantic_digest",
]
