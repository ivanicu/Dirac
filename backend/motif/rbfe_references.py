"""DB resolver for attested prepared receptors and aligned endpoint poses."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from uuid import NAMESPACE_URL, uuid5

import failures
from motif import rbfe_campaign_state as campaign_state
from motif.rbfe_binding import (
    campaign_scientific_ref as build_campaign_scientific_ref,
)


_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME = _ROOT / "openfe-runtime-v2/bin/python"
_CAMPAIGN_BUILDER = Path(__file__).with_name("rbfe_campaign_builder.py")
_PREPARE_TRANSPORT_FIELDS = frozenset({
    "campaign_id", "expected_version", "actor", "request_key",
})


def _prepare_scientific_payload(payload: dict) -> dict:
    """Strip command-transport identity before scientific normalization.

    ``request_key`` identifies delivery of one command. It must not alter the
    scientific bundle/request digest, reach the builder, or be persisted in the
    campaign's scientific inputs.
    """
    return {
        key: value for key, value in payload.items()
        if key not in _PREPARE_TRANSPORT_FIELDS
    }


def _canonical(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def _sha(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _artifact_ref(artifact) -> dict[str, str]:
    return {"kind": "artifact", "id": str(artifact.id),
            "sha256": "sha256:" + artifact.sha256}


def _preserve_server_owned_bundle(previous: dict | None,
                                  candidate: dict) -> dict:
    """Recompute browser-owned inputs without erasing server stage evidence.

    Pose review, protocol, and network are promoted only by server commands.
    A later draft/metadata save necessarily lacks those payloads; treating that
    absence as a scientific change would invalidate accepted poses on every
    harmless rename. Preserve their current digests/verdicts while comparing
    the independently canonicalized client-owned inputs.
    """
    if not isinstance(previous, dict):
        return candidate
    merged = dict(candidate)
    verdicts = dict(candidate.get("domain_verdicts") or {})
    previous_verdicts = dict(previous.get("domain_verdicts") or {})
    for domain in ("pose_review", "protocol", "network"):
        key = f"{domain}_digest"
        if previous.get(key):
            merged[key] = previous[key]
            verdicts[domain] = previous_verdicts.get(domain, "UNVERIFIED")
    merged["domain_verdicts"] = verdicts
    complete = {
        "schema_version": campaign_state.SCHEMA_VERSION,
        **{
            key: value for key, value in merged.items()
            if key.endswith("_digest") and key != "bundle_digest"
        },
    }
    merged["bundle_digest"] = campaign_state.sha256_digest(complete)
    return merged


def _target_ref(target_id: str, name: str) -> dict:
    return campaign_state.full_ref(
        "target", target_id,
        campaign_state.sha256_digest({"id": target_id, "name": name}))


def _protein_structure_ref(structure_id: str, *, target_id: str,
                           pdb_id: str | None, method: str,
                           resolution_angstrom: float | None) -> dict:
    return campaign_state.full_ref(
        "protein_structure", structure_id,
        campaign_state.sha256_digest({
            "id": structure_id, "target_id": target_id, "pdb_id": pdb_id,
            "method": method, "resolution_angstrom": resolution_angstrom,
        }))


def _hex_digest(value: str) -> str:
    return campaign_state.require_digest(value)[7:]


def _reseal_state(document: dict) -> dict:
    state = dict(document)
    state.pop("state_digest", None)
    return {**state, "state_digest": campaign_state.sha256_digest(state)}


def _scientific_pair(previous: dict | None, transition: dict) -> dict:
    """Seal one scientific transition independently of UI/audit revisions.

    ``version``/``state_digest`` identify every persisted aggregate revision and
    therefore advance for harmless metadata edits.  Scientific consumers need a
    different clock: one that advances only when inputs, a server-owned stage,
    an import, or invalidation changes what may be computed.  The transition
    record is deliberately digest-only so it never recursively contains the
    content-addressed objects whose documents bind to the resulting pair.
    """
    if previous is None:
        prior_generation = 0
        prior_digest = None
    else:
        prior_generation = previous.get("scientific_generation")
        if type(prior_generation) is not int:
            raise ValueError("prior scientific_generation must be an integer")
        prior_digest = campaign_state.require_digest(
            previous["scientific_digest"], "prior_scientific_digest")
        _validate_scientific_state(previous, prior_generation, prior_digest)
    evidence = dict(transition)
    action = str(evidence.pop("action", "")).strip()
    if not action:
        raise ValueError("scientific transition requires an action")
    record = {
        "action": action,
        "prior_scientific_generation": prior_generation,
        "prior_scientific_digest": prior_digest,
        "evidence_digest": campaign_state.sha256_digest(evidence),
    }
    generation = prior_generation + 1
    digest = campaign_state.sha256_digest({
        "scientific_generation": generation,
        "scientific_transition": record,
    })
    return {
        "scientific_generation": generation,
        "scientific_digest": digest,
        "scientific_transition": record,
    }


def _advance_scientific_state(document: dict, previous: dict | None,
                              transition: dict) -> dict:
    state = dict(document)
    state.update(_scientific_pair(previous, transition))
    return _reseal_state(state)


def _validate_scientific_state(state: dict, generation: int,
                               digest: str) -> None:
    """Verify the row/state scientific pair and its transition-chain seal."""
    try:
        declared_generation = state.get("scientific_generation")
        if type(declared_generation) is not int:
            raise ValueError("scientific_generation must be an integer")
        if type(generation) is not int:
            raise ValueError("row scientific_generation must be an integer")
        declared_digest = campaign_state.require_digest(
            state.get("scientific_digest"), "scientific_digest")
        row_digest = campaign_state.require_digest(digest, "row scientific_digest")
        transition = state.get("scientific_transition")
        if not isinstance(transition, dict):
            raise ValueError("scientific_transition must be an object")
        action = str(transition.get("action") or "").strip()
        prior_generation = transition.get("prior_scientific_generation")
        if type(prior_generation) is not int:
            raise ValueError("prior_scientific_generation must be an integer")
        prior_digest = transition.get("prior_scientific_digest")
        campaign_state.require_digest(
            transition.get("evidence_digest"), "scientific evidence digest")
        if not action or prior_generation != declared_generation - 1:
            raise ValueError("scientific transition generation is inconsistent")
        if declared_generation == 1:
            if prior_generation != 0 or prior_digest is not None:
                raise ValueError("initial scientific transition has a prior pair")
        else:
            campaign_state.require_digest(
                prior_digest, "prior_scientific_digest")
        actual = campaign_state.sha256_digest({
            "scientific_generation": declared_generation,
            "scientific_transition": transition,
        })
    except (TypeError, ValueError) as error:
        raise failures.DiracInternal(
            "RBFE campaign scientific generation is malformed") from error
    if (declared_generation != generation
            or declared_digest != row_digest or actual != row_digest):
        raise failures.DiracInternal(
            "RBFE campaign row and sealed state disagree on scientific generation")


def _campaign_scientific_ref(row: dict) -> dict:
    return build_campaign_scientific_ref(
        campaign_id=row["id"], generation=row["scientific_generation"],
        digest=row["scientific_digest"])


def _document_campaign_scientific_ref(document: dict) -> dict | None:
    """Read the dedicated science ref, with one legacy-document fallback."""
    value = document.get("campaign_scientific_ref")
    if value is None:
        value = document.get("campaign_ref")
    if value is None:
        return None
    try:
        if not isinstance(value, dict) or value.get("kind") != "rbfe_campaign":
            raise ValueError("campaign scientific ref has the wrong shape or kind")
        exact = build_campaign_scientific_ref(
            campaign_id=value.get("id"), generation=value.get("version"),
            digest=value.get("sha256"))
        if value != exact:
            raise ValueError("campaign scientific ref has unexpected fields")
        return exact
    except (TypeError, ValueError) as error:
        raise failures.DiracInternal(
            "persisted scientific object has a malformed campaign scientific ref"
        ) from error


def _import_receipt_is_current(receipt: dict, campaign: dict) -> bool:
    """Imports follow the scientific clock, never the editable revision clock."""
    return receipt.get("campaign_scientific_ref") == _campaign_scientific_ref(
        campaign)


def _campaign_failure(stage: str, message: str, *, expected_version=None,
                      actual_version=None, required_actions=()):
    details = campaign_state.stage_payload(
        stage, "OVERTURNED",
        error={
            "code": "CAMPAIGN_STATE_CONFLICT", "message": message,
            "expected_version": expected_version, "actual_version": actual_version,
        },
        recovery={
            "retryable": actual_version is not None,
            "resume_from_stage": "campaign",
            "required_actions": list(required_actions),
        })
    return failures.DiracInvalidParameters(message, details=details,
                                           user_message=message)


def _scientific_failure(stage: str, message: str, *,
                        expected_scientific_generation=None,
                        actual_scientific_generation=None,
                        expected_scientific_digest=None,
                        actual_scientific_digest=None,
                        required_actions=()):
    details = campaign_state.stage_payload(
        stage, "OVERTURNED",
        error={
            "code": "CAMPAIGN_SCIENTIFIC_CONFLICT", "message": message,
            "expected_scientific_generation": expected_scientific_generation,
            "actual_scientific_generation": actual_scientific_generation,
            "expected_scientific_digest": expected_scientific_digest,
            "actual_scientific_digest": actual_scientific_digest,
        },
        recovery={
            "retryable": actual_scientific_generation is not None,
            "resume_from_stage": "campaign",
            "required_actions": list(required_actions),
        })
    return failures.DiracInvalidParameters(message, details=details,
                                           user_message=message)


def _campaign_input_failure(stage: str, error: Exception,
                            *required_actions: str):
    message = str(error)
    details = campaign_state.stage_payload(
        stage, "OVERTURNED",
        error={"code": "INVALID_PARAMETERS", "message": message},
        recovery={
            "retryable": True, "resume_from_stage": stage,
            "required_actions": list(required_actions or ("correct_campaign_input",)),
        })
    return failures.DiracInvalidParameters(
        message, details=details, user_message=message)


class PostgresRbfeReferenceResolver:
    def __init__(self, connect) -> None:
        self._connect = connect

    def resolve(self, target_id: str, structure_id: str) -> dict:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT t.id::text, t.name::text, s.id::text, trim(s.pdb_id), "
                "s.method::text, s.resolution_a::float8 "
                "FROM bio.target t JOIN bio.structure s ON s.target_id=t.id "
                "WHERE t.id=%s AND s.id=%s", (target_id, structure_id))
            row = cursor.fetchone()
        if row is None:
            raise failures.DiracInvalidParameters(
                "target_ref and protein_structure_ref do not resolve to one registered target/pose pair",
                details={"target_id": target_id, "protein_structure_id": structure_id})
        target_ref = _target_ref(row[0], row[1])
        structure_ref = _protein_structure_ref(
            row[2], target_id=row[0], pdb_id=row[3], method=row[4],
            resolution_angstrom=row[5])
        return {"target_id": row[0], "target_name": row[1],
                "protein_structure_id": row[2], "pdb_id": row[3],
                "experimental_method": row[4], "resolution_angstrom": row[5],
                "target_ref": target_ref,
                "protein_structure_ref": structure_ref}

    @staticmethod
    def _campaign_row(cursor, campaign_id: str, *, lock: bool = False):
        suffix = " FOR UPDATE" if lock else ""
        cursor.execute(
            "SELECT id::text,version,status,state,encode(state_digest,'hex'),"
            "scientific_generation,encode(scientific_digest,'hex'),"
            "invalidated_at,invalidation_reason,created_by_kind::text,created_by_id,"
            "created_at,updated_at FROM app.rbfe_campaign WHERE id=%s" + suffix,
            (campaign_state.require_campaign_id(campaign_id),))
        row = cursor.fetchone()
        if row is None:
            return None
        state = row[3] if isinstance(row[3], dict) else json.loads(row[3])
        declared = "sha256:" + row[4]
        actual = campaign_state.sha256_digest({
            key: value for key, value in state.items() if key != "state_digest"
        })
        if state.get("state_digest") != declared or actual != declared:
            raise failures.DiracInternal(
                "RBFE campaign state digest is inconsistent with its canonical document")
        scientific_digest = "sha256:" + row[6]
        _validate_scientific_state(state, int(row[5]), scientific_digest)
        if (state.get("campaign_id") != row[0]
                or int(state.get("version", -1)) != int(row[1])
                or state.get("status") != row[2]):
            raise failures.DiracInternal(
                "RBFE campaign row and sealed state disagree on identity or generation")
        return {
            "id": row[0], "version": int(row[1]), "status": row[2],
            "state": state, "state_digest": declared,
            "scientific_generation": int(row[5]),
            "scientific_digest": scientific_digest,
            "invalidated_at": row[7], "invalidation_reason": row[8],
            "created_by": {"kind": row[9], "id": row[10]},
            "created_at": row[11], "updated_at": row[12],
        }

    @staticmethod
    def _campaign_response(row: dict, *, idempotent_replay: bool = False) -> dict:
        return {
            "campaign_ref": campaign_state.full_ref(
                "rbfe_campaign", row["id"], row["state_digest"],
                version=row["version"]),
            "campaign_id": row["id"], "version": row["version"],
            "status": row["status"], "state_digest": row["state_digest"],
            "campaign_scientific_ref": _campaign_scientific_ref(row),
            "campaign_scientific_generation": row["scientific_generation"],
            "campaign_scientific_digest": row["scientific_digest"],
            "state": row["state"], "invalidated_at": row["invalidated_at"],
            "invalidation_reason": row["invalidation_reason"],
            "created_by": row["created_by"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "idempotent_replay": idempotent_replay,
            "verdict": ("OVERTURNED" if row["status"] in {"stale", "archived"}
                        else "UNVERIFIED"),
        }

    @staticmethod
    def _authorize_campaign(row: dict, actor: dict[str, str], action: str) -> dict:
        """Owner-only campaign authorization until an explicit ACL exists.

        Campaign UUIDs, versions and digests are integrity capabilities, not
        authorization capabilities.  Treating possession of them as permission
        would let any authenticated principal mutate another user's campaign.
        """
        principal = campaign_state.require_actor(actor)
        if row.get("created_by") != principal:
            # Do not confirm that an inaccessible campaign exists.
            raise failures.DiracNotFound(
                "RBFE campaign does not exist or is not accessible",
                details={"action": str(action)})
        return principal

    @staticmethod
    def _insert_revision(cursor, row: dict, *, changed_domains: list[str],
                         reason: str, actor: dict[str, str]) -> None:
        cursor.execute(
            "INSERT INTO app.rbfe_campaign_revision "
            "(campaign_id,version,status,state,state_digest,scientific_generation,"
            " scientific_digest,changed_domains,reason,actor_kind,actor_id) "
            "VALUES (%s,%s,%s,%s,decode(%s,'hex'),%s,decode(%s,'hex'),%s,%s,%s,%s)",
            (row["id"], row["version"], row["status"], json.dumps(row["state"]),
             _hex_digest(row["state_digest"]), row["scientific_generation"],
             _hex_digest(row["scientific_digest"]), changed_domains, reason,
             actor["kind"], actor["id"]))

    @staticmethod
    def _invalidate_owned_objects(cursor, state: dict, *, reason: str,
                                  stale_stages: list[str] | None = None) -> list[dict]:
        """Fail closed every object owned by a superseded campaign revision.

        The recursive dependency walk catches registered downstream consumers;
        seeding it with every owned object also covers independent branches such
        as the ligand-state ensemble.
        """
        object_stage = {
            "protein_structure_source": "source",
            "chemical_state_ensemble": "microstates",
            "chemical_microstate": "microstates",
            "prepared_receptor_state": "prepared_receptor",
            "pose_hypothesis": "poses",
            "pose_ensemble": "poses",
        }
        stale = set(stale_stages) if stale_stages is not None else None
        root_refs = [
            dict(ref) for ref in state.get("owned_object_refs") or []
            if ref.get("kind") in {
                "protein_structure_source", "prepared_receptor_state",
                "pose_hypothesis", "pose_ensemble", "chemical_state_ensemble",
                "chemical_microstate",
            }
            and (stale is None or object_stage.get(ref.get("kind")) in stale)
        ]
        roots = sorted({str(ref["id"]) for ref in root_refs})
        if not roots:
            return []
        cursor.execute(
            "WITH RECURSIVE affected(id) AS ("
            " SELECT unnest(%s::uuid[]) UNION "
            " SELECT d.object_id FROM design.motif_scientific_dependency d "
            " JOIN affected a ON d.dependency_id=a.id) "
            "UPDATE design.motif_scientific_object o SET invalidated_at=now(),"
            "invalidation_code='campaign_stale',scientific_state='not_assessed',"
            "disposition='pending',claim_eligibility='ineligible_stale',"
            "reason_codes=ARRAY['campaign_stale',%s] "
            "FROM affected a WHERE o.id=a.id AND o.invalidated_at IS NULL",
            (roots, str(reason)))
        return root_refs

    @staticmethod
    def _scientific_inputs(client_state: dict) -> dict | None:
        required = {
            "receptor_pdb", "compounds", "parent_id", "reference_ligand",
            "receptor_policy", "ligand_policy",
        }
        for candidate in (
                client_state.get("scientific_inputs"),
                client_state.get("inputs"), client_state):
            if isinstance(candidate, dict) and required.issubset(candidate):
                return candidate
        return None

    def _save_generic_campaign(self, payload: dict,
                               principal: dict[str, str]) -> dict:
        """Persist the public command's generic state inside a server-owned seal."""
        submitted = payload.get("state")
        if not isinstance(submitted, dict):
            raise failures.DiracInvalidParameters("campaign state must be an object")
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            raise failures.DiracInvalidParameters(
                "campaign save requires a non-empty reason")
        expected = int(payload.get("expected_version", -1))
        identity = {"campaign_id": payload.get("campaign_id")}
        if not identity["campaign_id"]:
            identity["campaign_key"] = str(
                submitted.get("campaign_key")
                or submitted.get("campaign_name")
                or submitted.get("project_id")
                or campaign_state.sha256_digest(submitted))
        campaign_id = campaign_state.stable_campaign_id(identity, principal)
        label = str(
            submitted.get("campaign_name") or submitted.get("label")
            or "RBFE campaign").strip()
        public_domains = list(payload.get("changed_domains") or [])
        try:
            dag_roots = (campaign_state.normalize_changed_domains(public_domains)
                         if public_domains else [])
        except ValueError as error:
            raise failures.DiracInvalidParameters(str(error)) from error
        scientific_inputs = self._scientific_inputs(submitted)
        try:
            bundle = (campaign_state.canonical_digest_bundle(scientific_inputs)
                      if scientific_inputs is not None
                      else campaign_state.empty_digest_bundle())
        except (TypeError, ValueError) as error:
            raise _campaign_input_failure(
                "save_campaign", error, "correct_campaign_state") from error
        with self._connect() as connection, connection.cursor() as cursor:
            current = self._campaign_row(cursor, campaign_id, lock=True)
            if current is None:
                if expected != 0:
                    raise _campaign_failure(
                        "save_campaign", "new campaign expected_version must be zero",
                        expected_version=expected, actual_version=0,
                        required_actions=["retry_with_expected_version_zero"])
                if payload.get("status") != "draft":
                    raise failures.DiracInvalidParameters(
                        "a new campaign must begin in draft status")
                version = 1
                document = campaign_state.campaign_document(
                    campaign_id=campaign_id, version=version, label=label,
                    actor=principal, digest_bundle=bundle,
                    artifact_dag=campaign_state.dependency_dag(bundle),
                    status="draft", inputs=scientific_inputs,
                    stages={"campaign": campaign_state.stage_payload(
                        "campaign", "CONFIRMED",
                        digests={"bundle_digest": bundle["bundle_digest"]})})
                document = _advance_scientific_state(
                    {**document, "client_state": submitted}, None, {
                        "action": "campaign_created",
                        "bundle_digest": bundle["bundle_digest"],
                        "has_scientific_inputs": scientific_inputs is not None,
                        "status": "draft",
                    })
                cursor.execute(
                    "INSERT INTO app.rbfe_campaign "
                    "(id,version,status,state,state_digest,scientific_generation,"
                    "scientific_digest,created_by_kind,created_by_id) "
                    "VALUES (%s,1,'draft',%s,decode(%s,'hex'),%s,decode(%s,'hex'),%s,%s)",
                    (campaign_id, json.dumps(document),
                     _hex_digest(document["state_digest"]),
                     document["scientific_generation"],
                     _hex_digest(document["scientific_digest"]),
                     principal["kind"], principal["id"]))
                current = {
                    "id": campaign_id, "version": version, "status": "draft",
                    "state": document, "state_digest": document["state_digest"],
                    "scientific_generation": document["scientific_generation"],
                    "scientific_digest": document["scientific_digest"],
                    "invalidated_at": None, "invalidation_reason": None,
                    "created_by": principal, "created_at": None, "updated_at": None,
                }
                creation_changed = (
                    campaign_state.changed_domains(
                        campaign_state.empty_digest_bundle(), bundle)
                    if scientific_inputs is not None else [])
                self._insert_revision(
                    cursor, current,
                    changed_domains=(creation_changed or ["campaign_metadata"]),
                    reason=reason, actor=principal)
            else:
                self._authorize_campaign(current, principal, "save_campaign")
                if current["invalidated_at"] is not None:
                    raise _campaign_failure(
                        "save_campaign", "an invalidated campaign cannot be revived",
                        actual_version=current["version"],
                        required_actions=["create_new_campaign"])
                if (current["state"].get("client_state") == submitted
                        and payload.get("status") == current["status"]
                        and expected in {0, current["version"], current["version"] - 1}):
                    return self._campaign_response(current, idempotent_replay=True)
                if expected != current["version"]:
                    raise _campaign_failure(
                        "save_campaign", "campaign version does not match",
                        expected_version=expected, actual_version=current["version"],
                        required_actions=["reload_campaign", "reapply_change"])
                prior_inputs = current["state"].get("inputs")
                prior_owned = current["state"].get("owned_object_refs") or []
                if (scientific_inputs is None
                        and (isinstance(prior_inputs, dict) or prior_owned
                             or current["status"] != "draft")):
                    raise failures.DiracInvalidParameters(
                        "a campaign with scientific state must be saved with its complete "
                        "scientific_inputs; deleting or partially omitting them is not a "
                        "metadata-only change")
                bundle = _preserve_server_owned_bundle(
                    current["state"].get("digest_bundle"), bundle)
                # `changed_domains` is client intent, never scientific truth.
                # Recompute the actual domain delta from canonical server
                # digests so an empty or mislabelled declaration cannot retain
                # prepared poses/network artifacts after receptor, ligand or
                # policy inputs changed.
                actual_changed = (
                    campaign_state.changed_domains(
                        current["state"].get("digest_bundle"), bundle)
                    if scientific_inputs is not None else []
                )
                # Declared domains are provenance about the caller's intent,
                # not authority to destroy scientific state.  Only the
                # canonical server-side digest delta invalidates the DAG;
                # callers that intentionally invalidate an unchanged input use
                # the explicit campaign.invalidate command.
                effective_roots = sorted(set(actual_changed))
                underreported = sorted(set(actual_changed).difference(dag_roots))
                science_changed = bool(effective_roots)
                if (not science_changed
                        and payload.get("status") != current["status"]):
                    raise failures.DiracInvalidParameters(
                        "prepared/planned campaign status is server-owned; use its stage command")
                version = current["version"] + 1
                if science_changed:
                    stale = campaign_state.recursively_stale(
                        current["state"].get("artifact_dag"),
                        effective_roots, reason)
                    invalidation = {
                        **stale["invalidation"],
                        "declared_changed_domains": public_domains,
                        "server_detected_changed_domains": actual_changed,
                        "underreported_changed_domains": underreported,
                    }
                    stale_stages = stale["invalidation"]["stale_stages"]
                    self._invalidate_owned_objects(
                        cursor, current["state"], reason=reason,
                        stale_stages=stale_stages)
                    next_bundle = (bundle if scientific_inputs is not None else
                                   current["state"].get("digest_bundle"))
                    next_dag = (
                        campaign_state.recursively_stale(
                            campaign_state.dependency_dag(next_bundle),
                            effective_roots, reason)
                        if scientific_inputs is not None else stale)
                    ref_stage = {
                        "protein_structure_source": "source",
                        "chemical_state_ensemble": "microstates",
                        "chemical_microstate": "microstates",
                        "prepared_receptor_state": "prepared_receptor",
                        "pose_hypothesis": "poses", "pose_ensemble": "poses",
                    }
                    document = dict(current["state"])
                    document.update({
                        "version": version, "label": label, "actor": principal,
                        "status": "draft", "verdict": "UNVERIFIED",
                        "digest_bundle": next_bundle, "artifact_dag": next_dag,
                        "prior_invalidation": invalidation,
                        "pending_changed_domains": effective_roots,
                        "owned_object_refs": [
                            ref for ref in current["state"].get("owned_object_refs") or []
                            if ref_stage.get(ref.get("kind")) not in stale_stages
                        ],
                    })
                    if scientific_inputs is not None:
                        document["inputs"] = scientific_inputs
                    document.pop("prepare_receipt", None)
                    if "prepared_receptor" in stale_stages:
                        document.pop("prepared_scientific_ref", None)
                    if "pose_review" in stale_stages:
                        document.pop("pose_review_receipt", None)
                        document.pop("review_attestation", None)
                    status = "draft"
                else:
                    document = dict(current["state"])
                    document.update({
                        "version": version, "label": label, "actor": principal,
                    })
                    status = current["status"]
                document["client_state"] = submitted
                if science_changed:
                    document = _advance_scientific_state(
                        document, current["state"], {
                            "action": "scientific_inputs_changed",
                            "bundle_digest": next_bundle["bundle_digest"],
                            "changed_domains": effective_roots,
                            "status": status,
                        })
                else:
                    document = _reseal_state(document)
                cursor.execute(
                    "UPDATE app.rbfe_campaign SET version=%s,status=%s,state=%s,"
                    "state_digest=decode(%s,'hex'),scientific_generation=%s,"
                    "scientific_digest=decode(%s,'hex'),updated_at=now() "
                    "WHERE id=%s AND version=%s AND invalidated_at IS NULL RETURNING id",
                    (version, status, json.dumps(document),
                     _hex_digest(document["state_digest"]),
                     document["scientific_generation"],
                     _hex_digest(document["scientific_digest"]), campaign_id,
                     current["version"]))
                if cursor.fetchone() is None:
                    raise _campaign_failure(
                        "save_campaign", "campaign changed concurrently",
                        expected_version=current["version"],
                        required_actions=["reload_campaign", "reapply_change"])
                current.update({
                    "version": version, "status": status, "state": document,
                    "state_digest": document["state_digest"], "updated_at": None,
                    "scientific_generation": document["scientific_generation"],
                    "scientific_digest": document["scientific_digest"],
                })
                self._insert_revision(
                    cursor, current,
                    changed_domains=(effective_roots if science_changed
                                     else ["campaign_metadata"]),
                    reason=reason, actor=principal)
        return self._campaign_response(current)

    def save_campaign(self, payload: dict, actor: dict[str, str]) -> dict:
        """Create or compare-and-swap one durable campaign aggregate."""
        try:
            principal = campaign_state.require_actor(actor)
        except (TypeError, ValueError) as error:
            raise _campaign_input_failure(
                "save_campaign", error, "authenticate_actor") from error
        if {"state", "status", "changed_domains", "reason"}.issubset(payload):
            return self._save_generic_campaign(payload, principal)
        try:
            campaign_id = campaign_state.stable_campaign_id(payload, principal)
        except (TypeError, ValueError) as error:
            raise _campaign_input_failure(
                "save_campaign", error, "correct_campaign_identity") from error
        inputs = {key: value for key, value in payload.items()
                  if key not in {"campaign_id", "campaign_key", "expected_version"}}
        try:
            bundle = campaign_state.canonical_digest_bundle(
                inputs, pose_review=inputs.get("pose_review"),
                protocol=inputs.get("protocol"), network=inputs.get("network"))
        except (TypeError, ValueError) as error:
            raise _campaign_input_failure(
                "save_campaign", error, "correct_scientific_inputs") from error
        fresh_dag = campaign_state.dependency_dag(bundle)
        label = str(payload.get("campaign_name") or "RBFE campaign").strip()
        with self._connect() as connection, connection.cursor() as cursor:
            current = self._campaign_row(cursor, campaign_id, lock=True)
            if current is None:
                version, changed = 1, sorted(campaign_state.INPUT_DOMAINS)
                document = campaign_state.campaign_document(
                    campaign_id=campaign_id, version=version, label=label,
                    actor=principal, digest_bundle=bundle, artifact_dag=fresh_dag,
                    status="draft", inputs=inputs,
                    stages={"inputs": campaign_state.stage_payload(
                        "inputs", "CONFIRMED",
                        digests={"bundle_digest": bundle["bundle_digest"]})})
                document = _advance_scientific_state(
                    document, None, {
                        "action": "campaign_created",
                        "bundle_digest": bundle["bundle_digest"],
                        "has_scientific_inputs": True,
                        "status": "draft",
                    })
                cursor.execute(
                    "INSERT INTO app.rbfe_campaign "
                    "(id,version,status,state,state_digest,scientific_generation,"
                    "scientific_digest,created_by_kind,created_by_id) "
                    "VALUES (%s,%s,'draft',%s,decode(%s,'hex'),%s,decode(%s,'hex'),%s,%s)",
                    (campaign_id, version, json.dumps(document),
                     _hex_digest(document["state_digest"]),
                     document["scientific_generation"],
                     _hex_digest(document["scientific_digest"]),
                     principal["kind"], principal["id"]))
                current = {
                    "id": campaign_id, "version": version, "status": "draft",
                    "state": document, "state_digest": document["state_digest"],
                    "scientific_generation": document["scientific_generation"],
                    "scientific_digest": document["scientific_digest"],
                    "invalidated_at": None, "invalidation_reason": None,
                    "created_by": principal, "created_at": None, "updated_at": None,
                }
                self._insert_revision(
                    cursor, current, changed_domains=changed,
                    reason="campaign_created", actor=principal)
            else:
                self._authorize_campaign(current, principal, "save_campaign")
                if current["invalidated_at"] is not None:
                    raise _campaign_failure(
                        "save_campaign", "an invalidated campaign cannot be revived",
                        actual_version=current["version"],
                        required_actions=["create_new_campaign"])
                expected = payload.get("expected_version")
                if expected is None or int(expected) != current["version"]:
                    raise _campaign_failure(
                        "save_campaign", "campaign version does not match",
                        expected_version=expected, actual_version=current["version"],
                        required_actions=["reload_campaign", "reapply_change"])
                previous_bundle = current["state"].get("digest_bundle")
                changed = campaign_state.changed_domains(previous_bundle, bundle)
                if (not changed and current["state"].get("label") == label
                        and current["state"].get("inputs") == inputs):
                    return self._campaign_response(current, idempotent_replay=True)
                if not changed:
                    # Labels and other non-scientific metadata advance the
                    # aggregate without destroying still-valid prepared objects.
                    version = current["version"] + 1
                    document = dict(current["state"])
                    document.update({
                        "version": version, "label": label, "inputs": inputs,
                        "actor": principal,
                    })
                    document = _reseal_state(document)
                    cursor.execute(
                        "UPDATE app.rbfe_campaign SET version=%s,state=%s,"
                        "state_digest=decode(%s,'hex'),scientific_generation=%s,"
                        "scientific_digest=decode(%s,'hex'),updated_at=now() "
                        "WHERE id=%s AND version=%s AND invalidated_at IS NULL "
                        "RETURNING id",
                        (version, json.dumps(document),
                         _hex_digest(document["state_digest"]),
                         document["scientific_generation"],
                         _hex_digest(document["scientific_digest"]), campaign_id,
                         current["version"]))
                    if cursor.fetchone() is None:
                        raise _campaign_failure(
                            "save_campaign", "campaign changed concurrently",
                            expected_version=current["version"],
                            required_actions=["reload_campaign", "reapply_change"])
                    current.update({
                        "version": version, "state": document,
                        "state_digest": document["state_digest"],
                        "scientific_generation": document["scientific_generation"],
                        "scientific_digest": document["scientific_digest"],
                        "updated_at": None,
                    })
                    self._insert_revision(
                        cursor, current, changed_domains=["campaign_metadata"],
                        reason="campaign_metadata_changed", actor=principal)
                    return self._campaign_response(current)
                prior = campaign_state.recursively_stale(
                    current["state"].get("artifact_dag") or
                    campaign_state.dependency_dag(previous_bundle),
                    changed, "campaign inputs changed")
                self._invalidate_owned_objects(
                    cursor, current["state"], reason="campaign inputs changed",
                    stale_stages=prior["invalidation"]["stale_stages"])
                version = current["version"] + 1
                document = campaign_state.campaign_document(
                    campaign_id=campaign_id, version=version, label=label,
                    actor=principal, digest_bundle=bundle, artifact_dag=fresh_dag,
                    status="draft", inputs=inputs,
                    stages={"inputs": campaign_state.stage_payload(
                        "inputs", "CONFIRMED",
                        digests={"bundle_digest": bundle["bundle_digest"]})},
                    prior_invalidation=prior.get("invalidation"))
                document = _advance_scientific_state(
                    document, current["state"], {
                        "action": "scientific_inputs_changed",
                        "bundle_digest": bundle["bundle_digest"],
                        "changed_domains": changed,
                        "status": "draft",
                    })
                cursor.execute(
                    "UPDATE app.rbfe_campaign SET version=%s,status='draft',state=%s,"
                    "state_digest=decode(%s,'hex'),scientific_generation=%s,"
                    "scientific_digest=decode(%s,'hex'),updated_at=now() "
                    "WHERE id=%s AND version=%s AND invalidated_at IS NULL RETURNING id",
                    (version, json.dumps(document), _hex_digest(document["state_digest"]),
                     document["scientific_generation"],
                     _hex_digest(document["scientific_digest"]), campaign_id,
                     current["version"]))
                if cursor.fetchone() is None:
                    raise _campaign_failure(
                        "save_campaign", "campaign changed concurrently",
                        expected_version=current["version"],
                        required_actions=["reload_campaign", "reapply_change"])
                current.update({
                    "version": version, "status": "draft", "state": document,
                    "state_digest": document["state_digest"], "updated_at": None,
                    "scientific_generation": document["scientific_generation"],
                    "scientific_digest": document["scientific_digest"],
                })
                self._insert_revision(
                    cursor, current, changed_domains=changed or ["campaign_metadata"],
                    reason="campaign_inputs_changed", actor=principal)
        return self._campaign_response(current)

    def get_campaign(self, campaign_id: str, actor: dict[str, str]) -> dict:
        with self._connect() as connection, connection.cursor() as cursor:
            row = self._campaign_row(cursor, campaign_id)
        if row is None:
            raise failures.DiracNotFound(
                "RBFE campaign does not exist",
                details={"campaign_id": str(campaign_id)})
        self._authorize_campaign(row, actor, "get_campaign")
        return self._campaign_response(row)

    def assert_campaign_generation(self, campaign_id: str,
                                   scientific_generation: int,
                                   scientific_digest: str,
                                   actor: dict[str, str]) -> dict:
        """Return the current live campaign or reject a stale science pair."""
        campaign = self.get_campaign(campaign_id, actor)
        try:
            claimed_digest = campaign_state.require_digest(
                scientific_digest, "scientific_digest")
            claimed_generation = int(scientific_generation)
        except (TypeError, ValueError) as error:
            raise failures.DiracInvalidParameters(str(error)) from error
        system_build = ((campaign["state"].get("artifact_dag") or {})
                        .get("nodes", {}).get("system_build", {}))
        current_generation = campaign["campaign_scientific_generation"]
        current_digest = campaign["campaign_scientific_digest"]
        if (current_generation != claimed_generation
                or current_digest != claimed_digest
                or campaign["invalidated_at"] is not None
                or campaign["status"] not in {"poses_reviewed", "planned"}
                or bool(system_build.get("stale"))):
            raise _scientific_failure(
                "assert_campaign_generation",
                "campaign generation is stale, mismatched, or not execution-ready",
                expected_scientific_generation=claimed_generation,
                actual_scientific_generation=current_generation,
                expected_scientific_digest=claimed_digest,
                actual_scientific_digest=current_digest,
                required_actions=["reload_campaign", "complete_campaign_reviews"])
        return {**campaign, "verdict": "CONFIRMED"}

    def list_campaigns(self, actor: dict[str, str]) -> list[dict]:
        principal = campaign_state.require_actor(actor)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT id::text FROM app.rbfe_campaign "
                "WHERE created_by_kind=%s AND created_by_id=%s "
                "ORDER BY updated_at DESC",
                (principal["kind"], principal["id"]))
            identifiers = [row[0] for row in cursor.fetchall()]
            rows = [self._campaign_row(cursor, identifier) for identifier in identifiers]
        return [self._campaign_response(row) for row in rows if row is not None]

    def invalidate_campaign(self, campaign_id: str, expected_version: int,
                            reason: str, changed_domains: list[str],
                            actor: dict[str, str]) -> dict:
        """Invalidate the campaign aggregate and every owned transitive artifact."""
        if not str(reason or "").strip():
            raise failures.DiracInvalidParameters(
                "campaign invalidation requires a non-empty reason")
        principal = campaign_state.require_actor(actor)
        with self._connect() as connection, connection.cursor() as cursor:
            current = self._campaign_row(cursor, campaign_id, lock=True)
            if current is None:
                raise failures.DiracNotFound("RBFE campaign does not exist")
            self._authorize_campaign(current, principal, "invalidate_campaign")
            if int(expected_version) != current["version"]:
                raise _campaign_failure(
                    "invalidate_campaign", "campaign version does not match",
                    expected_version=expected_version, actual_version=current["version"],
                    required_actions=["reload_campaign"])
            public_domains = list(changed_domains or [])
            try:
                domains = campaign_state.normalize_changed_domains(public_domains)
            except ValueError as error:
                raise failures.DiracInvalidParameters(str(error)) from error
            stale_dag = campaign_state.recursively_stale(
                current["state"].get("artifact_dag"), domains, reason)
            invalidation = {
                **stale_dag["invalidation"],
                "declared_changed_domains": public_domains,
            }
            state = dict(current["state"])
            state.update({
                "version": current["version"] + 1, "status": "stale",
                "verdict": "OVERTURNED", "artifact_dag": stale_dag,
                "actor": principal,
                "invalidation": invalidation,
            })
            state = _advance_scientific_state(
                state, current["state"], {
                    "action": "campaign_invalidated",
                    "changed_domains": domains,
                    "reason_digest": campaign_state.sha256_digest({
                        "reason": str(reason).strip(),
                    }),
                    "status": "stale",
                })
            invalidated_refs = self._invalidate_owned_objects(
                cursor, state, reason=str(reason))
            cursor.execute(
                "UPDATE app.rbfe_campaign SET version=%s,status='stale',state=%s,"
                "state_digest=decode(%s,'hex'),scientific_generation=%s,"
                "scientific_digest=decode(%s,'hex'),invalidated_at=now(),"
                "invalidation_reason=%s,updated_at=now() "
                "WHERE id=%s AND version=%s RETURNING id",
                (state["version"], json.dumps(state), _hex_digest(state["state_digest"]),
                 state["scientific_generation"],
                 _hex_digest(state["scientific_digest"]), str(reason),
                 current["id"], current["version"]))
            if cursor.fetchone() is None:
                raise _campaign_failure(
                    "invalidate_campaign", "campaign changed concurrently",
                    expected_version=current["version"],
                    required_actions=["reload_campaign"])
            current.update({
                "version": state["version"], "status": "stale", "state": state,
                "state_digest": state["state_digest"], "invalidated_at": True,
                "scientific_generation": state["scientific_generation"],
                "scientific_digest": state["scientific_digest"],
                "invalidation_reason": str(reason), "updated_at": None,
            })
            self._insert_revision(
                cursor, current, changed_domains=domains,
                reason=str(reason), actor=principal)
        return {
            **self._campaign_response(current),
            "invalidated_artifacts": invalidated_refs,
        }

    def list_systems(self, actor: dict[str, str],
                     campaign_id: str | None = None,
                     include_importable: bool = False) -> list[dict]:
        """Return campaign-owned/imported systems; foreign systems are opt-in only."""
        principal = campaign_state.require_actor(actor)
        normalized_campaign_id = (
            campaign_state.require_campaign_id(campaign_id) if campaign_id else None)
        if normalized_campaign_id is None and not include_importable:
            return []
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT id::text FROM app.rbfe_campaign "
                "WHERE created_by_kind=%s AND created_by_id=%s",
                (principal["kind"], principal["id"]))
            accessible_campaign_ids = {row[0] for row in cursor.fetchall()}
            imported_ids: set[str] = set()
            stale_import_ids: set[str] = set()
            campaign = None
            if normalized_campaign_id is not None:
                campaign = self._campaign_row(cursor, normalized_campaign_id)
                if campaign is None or campaign["invalidated_at"] is not None:
                    raise failures.DiracInvalidParameters(
                        "system listing requires a live campaign")
                self._authorize_campaign(campaign, principal, "list_systems")
                cursor.execute(
                    "SELECT prepared_receptor_state_id::text,receipt,"
                    "encode(receipt_digest,'hex') "
                    "FROM app.rbfe_campaign_system_import WHERE campaign_id=%s",
                    (normalized_campaign_id,))
                for receptor_id, encoded_receipt, receipt_digest in cursor.fetchall():
                    receipt = (encoded_receipt if isinstance(encoded_receipt, dict)
                               else json.loads(encoded_receipt))
                    if campaign_state.sha256_digest(receipt) != "sha256:" + receipt_digest:
                        raise failures.DiracInternal(
                            "RBFE campaign import receipt digest is inconsistent")
                    if _import_receipt_is_current(receipt, campaign):
                        imported_ids.add(receptor_id)
                    else:
                        stale_import_ids.add(receptor_id)
            cursor.execute(
                "SELECT o.id::text, b.bytes,encode(o.semantic_digest,'hex') "
                "FROM design.motif_scientific_object o "
                "JOIN app.artifact a ON a.id=o.document_artifact_id "
                "JOIN app.blob b ON b.sha256=a.blob_sha256 "
                "WHERE o.object_kind='prepared_receptor_state' "
                "AND o.invalidated_at IS NULL ORDER BY o.created_at")
            receptor_rows = cursor.fetchall()
            systems = []
            for receptor_id, raw, semantic_digest in receptor_rows:
                document = json.loads(bytes(raw))
                source_campaign_scientific_ref = (
                    _document_campaign_scientific_ref(document) or {})
                source_campaign_id = source_campaign_scientific_ref.get("id")
                if source_campaign_id not in accessible_campaign_ids:
                    continue
                owned = source_campaign_id == normalized_campaign_id
                imported = receptor_id in imported_ids
                stale_import = receptor_id in stale_import_ids
                if (not owned and not imported and not stale_import
                        and not include_importable):
                    continue
                scope = ("owned" if owned else "imported" if imported
                         else "import_stale" if stale_import else "import_required")
                cursor.execute(
                    "SELECT p.id::text, pb.bytes, p.scientific_state::text,"
                    "encode(p.semantic_digest,'hex') "
                    "FROM design.motif_scientific_dependency d "
                    "JOIN design.motif_scientific_object p ON p.id=d.object_id "
                    "JOIN app.artifact pa ON pa.id=p.document_artifact_id "
                    "JOIN app.blob pb ON pb.sha256=pa.blob_sha256 "
                    "WHERE d.dependency_id=%s AND d.dependency_role='aligned_to' "
                    "AND p.object_kind='pose_hypothesis' AND p.invalidated_at IS NULL "
                    "ORDER BY p.created_at", (receptor_id,))
                poses = []
                for pose_id, pose_raw, pose_state, pose_digest in cursor.fetchall():
                    pose = json.loads(bytes(pose_raw))
                    poses.append({
                        "pose_ref": campaign_state.full_ref(
                            "pose_hypothesis", pose_id, "sha256:" + pose_digest),
                        "label": pose["label"],
                        "canonical_smiles": pose["canonical_smiles"],
                        "core_rmsd_angstrom": pose.get("pose_report", {}).get("core_rmsd_angstrom"),
                        "core_coverage": pose.get("pose_report", {}).get("minimum_bidirectional_coverage"),
                        "minimum_heavy_atom_distance_angstrom": pose.get("pose_report", {}).get("minimum_heavy_atom_distance_angstrom"),
                        "protein_contacts_within_6_angstrom": pose.get("pose_report", {}).get("protein_contacts_within_6_angstrom"),
                        "coordinate_artifact_ref": pose.get("coordinate_artifact_ref"),
                        "review_state": pose_state,
                    })
                cursor.execute(
                    "SELECT t.name::text,trim(s.pdb_id),s.method::text,"
                    "s.resolution_a::float8 "
                    "FROM bio.target t JOIN bio.structure s ON s.target_id=t.id "
                    "WHERE t.id=%s AND s.id=%s",
                    (document["target_ref"]["id"],
                     document["protein_structure_ref"]["id"]))
                metadata = cursor.fetchone()
                if metadata is None:
                    continue
                actual_target_ref = _target_ref(
                    document["target_ref"]["id"], metadata[0])
                actual_structure_ref = _protein_structure_ref(
                    document["protein_structure_ref"]["id"],
                    target_id=document["target_ref"]["id"], pdb_id=metadata[1],
                    method=metadata[2], resolution_angstrom=metadata[3])
                if (document["target_ref"] != actual_target_ref
                        or document["protein_structure_ref"] != actual_structure_ref):
                    continue
                systems.append({
                    "prepared_receptor_state_ref": campaign_state.full_ref(
                        "prepared_receptor_state", receptor_id,
                        "sha256:" + semantic_digest),
                    "campaign_scope": scope,
                    "source_campaign_id": source_campaign_id,
                    "source_campaign_scientific_ref": (
                        source_campaign_scientific_ref),
                    "import_required": scope in {"import_required", "import_stale"},
                    "execution_eligible": (
                        scope in {"owned", "imported"}
                        and campaign is not None
                        and campaign["status"] in {"poses_reviewed", "planned"}
                        and bool(poses)
                        and all(pose["review_state"] == "accepted" for pose in poses)),
                    "label": document["label"],
                    "target_name": metadata[0],
                    "target_ref": document["target_ref"],
                    "protein_structure_ref": document["protein_structure_ref"],
                    "pdb_id": document["source_pdb_id"],
                    "experimental_method": metadata[2],
                    "resolution_angstrom": metadata[3],
                    "preparation_state": (
                        "server-attested" if scope in {"owned", "imported"}
                        else "import-required"),
                    "claim_boundary": document["claim_boundary"],
                    "poses": poses,
                })
        return systems

    def import_system(self, campaign_id: str, prepared_receptor_state_ref: dict,
                      actor: dict[str, str], *, expected_version: int,
                      reason: str) -> dict:
        """Create an immutable receipt before a foreign system becomes executable."""
        principal = campaign_state.require_actor(actor)
        target_campaign_id = campaign_state.require_campaign_id(campaign_id)
        if not str(reason or "").strip():
            raise failures.DiracInvalidParameters(
                "cross-campaign import requires a non-empty reason")
        try:
            declared_receptor_ref = campaign_state.full_ref(
                "prepared_receptor_state",
                prepared_receptor_state_ref.get("id"),
                prepared_receptor_state_ref.get("sha256"))
            if (prepared_receptor_state_ref.get("kind")
                    != "prepared_receptor_state"):
                raise ValueError("prepared receptor ref kind is incorrect")
        except (AttributeError, TypeError, ValueError) as error:
            raise failures.DiracInvalidParameters(
                "system import requires a complete content-addressed prepared "
                "receptor ref") from error
        receptor_id = declared_receptor_ref["id"]
        with self._connect() as connection, connection.cursor() as cursor:
            current = self._campaign_row(cursor, target_campaign_id, lock=True)
            if current is None or current["invalidated_at"] is not None:
                raise failures.DiracInvalidParameters(
                    "system import requires a live target campaign")
            self._authorize_campaign(current, principal, "import_system")
            record = self._object_record(
                cursor, receptor_id, "prepared_receptor_state")
            source_campaign_scientific_ref = (
                _document_campaign_scientific_ref(record["document"]))
            source_campaign_id = (
                source_campaign_scientific_ref or {}).get("id")
            try:
                if (not isinstance(source_campaign_scientific_ref, dict)
                        or source_campaign_scientific_ref.get("kind")
                        != "rbfe_campaign"):
                    raise ValueError("missing campaign ref")
                campaign_state.full_ref(
                    "rbfe_campaign", source_campaign_scientific_ref.get("id"),
                    source_campaign_scientific_ref.get("sha256"),
                    version=int(source_campaign_scientific_ref.get("version")))
            except (TypeError, ValueError) as error:
                raise failures.DiracInvalidParameters(
                    "prepared system lacks a complete source campaign generation ref") from error
            source_campaign = self._campaign_row(cursor, source_campaign_id)
            if (source_campaign is None
                    or source_campaign["invalidated_at"] is not None):
                raise failures.DiracInvalidParameters(
                    "prepared system source campaign is absent or stale")
            self._authorize_campaign(
                source_campaign, principal, "import_system_source")
            if (source_campaign_scientific_ref
                    != source_campaign["state"].get("prepared_scientific_ref")):
                raise failures.DiracInvalidParameters(
                    "prepared system source scientific generation is stale")
            if source_campaign_id == target_campaign_id:
                raise failures.DiracInvalidParameters(
                    "campaign already owns this prepared system; import is not applicable")
            cursor.execute(
                "SELECT count(*) FROM design.motif_scientific_dependency d "
                "JOIN design.motif_scientific_object p ON p.id=d.object_id "
                "WHERE d.dependency_id=%s AND d.dependency_role='aligned_to' "
                "AND p.object_kind='pose_hypothesis' "
                "AND p.scientific_state='accepted' AND p.invalidated_at IS NULL",
                (receptor_id,))
            accepted_pose_count = int(cursor.fetchone()[0])
            if accepted_pose_count < 2:
                raise failures.DiracInvalidParameters(
                    "only a prepared system with at least two accepted endpoint poses "
                    "can be imported")
            if declared_receptor_ref != record["ref"]:
                raise failures.DiracInvalidParameters(
                    "prepared system ref digest does not match the registered object")
            cursor.execute(
                "SELECT receipt,encode(receipt_digest,'hex') "
                "FROM app.rbfe_campaign_system_import "
                "WHERE campaign_id=%s AND prepared_receptor_state_id=%s",
                (target_campaign_id, receptor_id))
            prior = cursor.fetchone()
            if prior is not None:
                receipt = prior[0] if isinstance(prior[0], dict) else json.loads(prior[0])
                if campaign_state.sha256_digest(receipt) != "sha256:" + prior[1]:
                    raise failures.DiracInternal(
                        "existing campaign import receipt failed digest verification")
                if receipt.get("prepared_receptor_state_ref") != record["ref"]:
                    raise failures.DiracInternal(
                        "existing campaign import receipt does not match its object")
                receipt_is_current = _import_receipt_is_current(receipt, current)
                original_retry = (
                    receipt.get("campaign_input_version") == int(expected_version))
                if receipt_is_current and (
                        original_retry or int(expected_version) == current["version"]):
                    return {
                        **self._campaign_response(current, idempotent_replay=True),
                        "import_receipt": receipt,
                        "receipt_digest": "sha256:" + prior[1],
                        "prepared_receptor_state_ref": record["ref"],
                        "verdict": "CONFIRMED",
                    }
            if int(expected_version) != current["version"]:
                raise _campaign_failure(
                    "import_system", "campaign version does not match",
                    expected_version=expected_version, actual_version=current["version"],
                    required_actions=["reload_campaign", "repeat_import"])
            scientific_transition = {
                "action": "system_imported",
                "prepared_receptor_digest": record["ref"]["sha256"],
                "source_scientific_digest": source_campaign["scientific_digest"],
                "reason_digest": campaign_state.sha256_digest({
                    "reason": str(reason).strip(),
                }),
                "status": "poses_reviewed",
            }
            output_science = _scientific_pair(
                current["state"], scientific_transition)
            output_scientific_ref = build_campaign_scientific_ref(
                campaign_id=target_campaign_id,
                generation=output_science["scientific_generation"],
                digest=output_science["scientific_digest"])
            receipt = {
                "schema_version": "rbfe-campaign-system-import.v1",
                "campaign_id": target_campaign_id,
                "campaign_input_version": current["version"],
                "campaign_version": current["version"] + 1,
                "campaign_scientific_ref": output_scientific_ref,
                "source_campaign_id": source_campaign_id,
                "source_campaign_scientific_ref": (
                    source_campaign_scientific_ref),
                "prepared_receptor_state_ref": record["ref"],
                "reason": str(reason).strip(), "actor": principal,
                "verdict": "CONFIRMED",
            }
            receipt_digest = campaign_state.sha256_digest(receipt)
            if prior is None:
                cursor.execute(
                    "INSERT INTO app.rbfe_campaign_system_import "
                    "(campaign_id,prepared_receptor_state_id,source_campaign_id,receipt,"
                    " receipt_digest,actor_kind,actor_id) "
                    "VALUES (%s,%s,%s,%s,decode(%s,'hex'),%s,%s)",
                    (target_campaign_id, receptor_id, source_campaign_id,
                     json.dumps(receipt), _hex_digest(receipt_digest),
                     principal["kind"], principal["id"]))
            else:
                # The campaign revision ledger retains the old receipt.  Refresh
                # this projection only after an explicit, reasoned re-import.
                cursor.execute(
                    "UPDATE app.rbfe_campaign_system_import SET source_campaign_id=%s,"
                    "receipt=%s,receipt_digest=decode(%s,'hex'),actor_kind=%s,"
                    "actor_id=%s,created_at=now() WHERE campaign_id=%s "
                    "AND prepared_receptor_state_id=%s",
                    (source_campaign_id, json.dumps(receipt),
                     _hex_digest(receipt_digest), principal["kind"], principal["id"],
                     target_campaign_id, receptor_id))
            state = dict(current["state"])
            imports = list(state.get("imports") or [])
            imports.append({**receipt, "receipt_digest": receipt_digest})
            state.update({
                "version": current["version"] + 1, "status": "poses_reviewed",
                "imports": imports, "actor": principal,
            })
            state = _advance_scientific_state(
                state, current["state"], scientific_transition)
            cursor.execute(
                "UPDATE app.rbfe_campaign SET version=%s,status='poses_reviewed',state=%s,"
                "state_digest=decode(%s,'hex'),scientific_generation=%s,"
                "scientific_digest=decode(%s,'hex'),updated_at=now() "
                "WHERE id=%s AND version=%s AND invalidated_at IS NULL RETURNING id",
                (state["version"], json.dumps(state), _hex_digest(state["state_digest"]),
                 state["scientific_generation"],
                 _hex_digest(state["scientific_digest"]), target_campaign_id,
                 current["version"]))
            if cursor.fetchone() is None:
                raise _campaign_failure(
                    "import_system", "campaign changed concurrently",
                    expected_version=current["version"],
                    required_actions=["reload_campaign", "repeat_import"])
            current.update({
                "version": state["version"], "status": "poses_reviewed", "state": state,
                "state_digest": state["state_digest"], "updated_at": None,
                "scientific_generation": state["scientific_generation"],
                "scientific_digest": state["scientific_digest"],
            })
            self._insert_revision(
                cursor, current, changed_domains=["system_import"],
                reason=str(reason), actor=principal)
        return {
            **self._campaign_response(current),
            "import_receipt": receipt, "receipt_digest": receipt_digest,
            "prepared_receptor_state_ref": record["ref"],
            "verdict": "CONFIRMED",
        }

    def _object_record(self, cursor, object_id: str, kind: str) -> dict:
        cursor.execute(
            "SELECT b.bytes,encode(o.semantic_digest,'hex') "
            "FROM design.motif_scientific_object o "
            "JOIN app.artifact a ON a.id=o.document_artifact_id "
            "JOIN app.blob b ON b.sha256=a.blob_sha256 "
            "WHERE o.id=%s AND o.object_kind=%s AND o.invalidated_at IS NULL",
            (object_id, kind))
        row = cursor.fetchone()
        if row is None:
            raise failures.DiracInvalidParameters(
                f"{kind} reference is not a live registered scientific object")
        raw = bytes(row[0])
        if hashlib.sha256(raw).hexdigest() != row[1]:
            raise failures.DiracInternal(
                f"{kind} scientific document digest is inconsistent")
        return {
            "document": json.loads(raw),
            "ref": campaign_state.full_ref(kind, str(object_id), "sha256:" + row[1]),
        }

    def _object_document(self, cursor, object_id: str, kind: str) -> dict:
        return self._object_record(cursor, object_id, kind)["document"]

    def _artifact_bytes(self, cursor, reference: dict, role: str) -> bytes:
        cursor.execute(
            "SELECT a.role, encode(a.blob_sha256,'hex'), b.bytes "
            "FROM app.artifact a JOIN app.blob b ON b.sha256=a.blob_sha256 "
            "WHERE a.id=%s", (reference["id"],))
        row = cursor.fetchone()
        if row is None or row[0] != role:
            raise failures.DiracInvalidParameters(
                f"registered coordinate artifact is absent or is not {role}")
        raw = bytes(row[2])
        actual = hashlib.sha256(raw).hexdigest()
        if actual != row[1] or reference.get("sha256") != "sha256:" + actual:
            raise failures.DiracInternal(
                f"registered {role} bytes failed content-address verification")
        return raw

    def resolve_prepared_system(self, prepared_receptor_state_ref: dict,
                                parent_pose_ref: dict,
                                proposal_pose_ref: dict, *,
                                campaign_id: str | None = None,
                                scientific_generation: int | None = None,
                                scientific_digest: str | None = None,
                                actor: dict[str, str] | None = None) -> dict:
        if (campaign_id is None or scientific_generation is None
                or scientific_digest is None):
            raise failures.DiracInvalidParameters(
                "campaign_id and the scientific generation/digest pair are required "
                "to resolve a prepared RBFE system")
        normalized_campaign_id = campaign_state.require_campaign_id(campaign_id)
        try:
            receptor_ref = campaign_state.full_ref(
                "prepared_receptor_state",
                prepared_receptor_state_ref.get("id"),
                prepared_receptor_state_ref.get("sha256"))
            parent_ref = campaign_state.full_ref(
                "pose_hypothesis", parent_pose_ref.get("id"),
                parent_pose_ref.get("sha256"))
            proposal_ref = campaign_state.full_ref(
                "pose_hypothesis", proposal_pose_ref.get("id"),
                proposal_pose_ref.get("sha256"))
            if (prepared_receptor_state_ref.get("kind")
                    != "prepared_receptor_state"
                    or parent_pose_ref.get("kind") != "pose_hypothesis"
                    or proposal_pose_ref.get("kind") != "pose_hypothesis"):
                raise ValueError("scientific object ref kind is incorrect")
        except (AttributeError, TypeError, ValueError) as error:
            raise failures.DiracInvalidParameters(
                "prepared-system resolution requires complete content-addressed "
                "receptor and endpoint pose refs") from error
        receptor_id = receptor_ref["id"]
        parent_pose_id = parent_ref["id"]
        proposal_pose_id = proposal_ref["id"]
        with self._connect() as connection, connection.cursor() as cursor:
            campaign = self._campaign_row(cursor, normalized_campaign_id)
            if campaign is None or campaign["invalidated_at"] is not None:
                raise failures.DiracInvalidParameters(
                    "prepared-system resolution requires a live campaign")
            principal = self._authorize_campaign(
                campaign, actor, "resolve_prepared_system")
            if (campaign["scientific_generation"] != int(scientific_generation)
                    or campaign["scientific_digest"] != scientific_digest):
                raise _scientific_failure(
                    "resolve_prepared_system",
                    "campaign scientific generation or digest does not match",
                    expected_scientific_generation=int(scientific_generation),
                    actual_scientific_generation=campaign[
                        "scientific_generation"],
                    expected_scientific_digest=str(scientific_digest),
                    actual_scientific_digest=campaign["scientific_digest"],
                    required_actions=["reload_campaign", "reselect_system"])
            system_build = ((campaign["state"].get("artifact_dag") or {})
                            .get("nodes", {}).get("system_build", {}))
            if (campaign["status"] not in {"poses_reviewed", "planned"}
                    or bool(system_build.get("stale"))):
                raise failures.DiracInvalidParameters(
                    "campaign is not ready to resolve a physical RBFE system")
            receptor_record = self._object_record(
                cursor, receptor_id, "prepared_receptor_state")
            if receptor_record["ref"] != receptor_ref:
                raise failures.DiracInvalidParameters(
                    "prepared receptor ref digest does not match the registered object")
            receptor = receptor_record["document"]
            receptor_campaign_scientific_ref = (
                _document_campaign_scientific_ref(receptor) or {})
            owner = receptor_campaign_scientific_ref.get("id")
            cursor.execute(
                "SELECT receipt,encode(receipt_digest,'hex') "
                "FROM app.rbfe_campaign_system_import "
                "WHERE campaign_id=%s AND prepared_receptor_state_id=%s",
                (normalized_campaign_id, receptor_id))
            imported_row = cursor.fetchone()
            imported = imported_row is not None
            if owner != normalized_campaign_id and not imported:
                raise failures.DiracInvalidParameters(
                    "prepared system is foreign to this campaign; explicit import is required")
            if (owner == normalized_campaign_id
                    and receptor_campaign_scientific_ref
                    != campaign["state"].get("prepared_scientific_ref")):
                raise failures.DiracInvalidParameters(
                    "prepared system belongs to a stale scientific generation")
            if imported:
                receipt = (imported_row[0] if isinstance(imported_row[0], dict)
                           else json.loads(imported_row[0]))
                if (campaign_state.sha256_digest(receipt)
                        != "sha256:" + imported_row[1]):
                    raise failures.DiracInternal(
                        "prepared-system import receipt failed digest verification")
                if (receipt.get("campaign_id") != normalized_campaign_id
                        or receipt.get("campaign_scientific_ref")
                        != _campaign_scientific_ref(campaign)
                        or receipt.get("prepared_receptor_state_ref")
                        != receptor_record["ref"]):
                    raise failures.DiracInvalidParameters(
                        "system import receipt is stale for this campaign scientific generation")
                source_campaign_id = receptor_campaign_scientific_ref.get("id")
                source_campaign = self._campaign_row(cursor, source_campaign_id)
                if source_campaign is None:
                    raise failures.DiracInvalidParameters(
                        "imported system source campaign is absent")
                self._authorize_campaign(
                    source_campaign, principal, "resolve_imported_system")
                if (receptor_campaign_scientific_ref
                        != source_campaign["state"].get(
                            "prepared_scientific_ref")):
                    raise failures.DiracInvalidParameters(
                        "imported system source scientific generation is stale")
            poses = []
            for pose_id, declared_pose_ref in (
                    (parent_pose_id, parent_ref),
                    (proposal_pose_id, proposal_ref)):
                pose_record = self._object_record(cursor, pose_id, "pose_hypothesis")
                if pose_record["ref"] != declared_pose_ref:
                    raise failures.DiracInvalidParameters(
                        "endpoint pose ref digest does not match the registered object")
                pose = pose_record["document"]
                cursor.execute(
                    "SELECT scientific_state::text FROM design.motif_scientific_object "
                    "WHERE id=%s AND invalidated_at IS NULL", (pose_id,))
                state = cursor.fetchone()
                if state is None or state[0] != "accepted":
                    raise failures.DiracInvalidParameters(
                        "selected endpoint pose has not passed human pose review")
                receptor_ref = pose.get("prepared_receptor_state_ref", {})
                if receptor_ref.get("id") != receptor_id:
                    raise failures.DiracInvalidParameters(
                        "selected endpoint pose is not aligned to the prepared receptor")
                cursor.execute(
                    "SELECT 1 FROM design.motif_scientific_dependency "
                    "WHERE object_id=%s AND dependency_id=%s "
                    "AND dependency_role='aligned_to'", (pose_id, receptor_id))
                if cursor.fetchone() is None:
                    raise failures.DiracInvalidParameters(
                        "selected endpoint pose lacks an aligned_to dependency")
                poses.append((pose_record, self._artifact_bytes(
                    cursor, pose["coordinate_artifact_ref"], "rbfe.pose.sdf")))
            receptor_bytes = self._artifact_bytes(
                cursor, receptor["coordinate_artifact_ref"], "rbfe.receptor.pdb")
        target = receptor["target_ref"]
        structure = receptor["protein_structure_ref"]
        identity = self.resolve(target["id"], structure["id"])
        if (target != identity["target_ref"]
                or structure != identity["protein_structure_ref"]):
            raise failures.DiracInvalidParameters(
                "prepared receptor target/structure provenance is stale")
        return {
            **identity,
            "target_ref": target,
            "protein_structure_ref": structure,
            "campaign_ref": campaign_state.full_ref(
                "rbfe_campaign", normalized_campaign_id,
                campaign["state_digest"],
                version=campaign["version"]),
            "campaign_scientific_ref": _campaign_scientific_ref(campaign),
            "campaign_scientific_generation": campaign["scientific_generation"],
            "campaign_scientific_digest": campaign["scientific_digest"],
            "prepared_receptor_state_ref": receptor_record["ref"],
            "receptor_pdb": receptor_bytes.decode(),
            "expected_receptor_sha256": receptor["coordinate_frame_digest"],
            "source_pdb_id": receptor["source_pdb_id"],
            "parent_sdf": poses[0][1].decode(),
            "proposal_sdf": poses[1][1].decode(),
            "parent_canonical_smiles": poses[0][0]["document"]["canonical_smiles"],
            "proposal_canonical_smiles": poses[1][0]["document"]["canonical_smiles"],
            "parent_pose_ref": poses[0][0]["ref"],
            "proposal_pose_ref": poses[1][0]["ref"],
            "campaign_scope": "owned" if owner == normalized_campaign_id else "imported",
        }

    def _build_campaign(self, payload: dict) -> dict:
        if not _RUNTIME.is_file():
            raise failures.DiracUnsupported("pinned OpenFE preparation runtime is unavailable")
        with tempfile.TemporaryDirectory(prefix="dirac-rbfe-campaign-") as temporary:
            source = Path(temporary) / "input.json"
            target = Path(temporary) / "output.json"
            source.write_bytes(_canonical(payload))
            completed = subprocess.run(
                [str(_RUNTIME), str(_CAMPAIGN_BUILDER), str(source), str(target)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                check=False, timeout=300)
            output = None
            if target.is_file():
                try:
                    output = json.loads(target.read_text())
                except json.JSONDecodeError:
                    output = None
            if completed.returncode != 0:
                error = output.get("error") if isinstance(output, dict) else None
                message = error.get("message") if isinstance(error, dict) else None
                detail = campaign_state.stage_payload(
                    "prepare", "OVERTURNED",
                    error={
                        "code": (error.get("code") if isinstance(error, dict)
                                 else "CAMPAIGN_PREPARATION_FAILED"),
                        "message": str(message or "receptor/pose preparation failed"),
                    },
                    recovery={
                        "retryable": True, "resume_from_stage": "inputs",
                        "required_actions": [
                            "inspect_structured_error", "correct_named_ligand_or_policy",
                        ],
                    })
                raise failures.DiracInvalidParameters(
                    str(message or "receptor/pose preparation failed"),
                    details=detail,
                    user_message=str(message or "The receptor or reference pose could not be prepared."))
            if not isinstance(output, dict):
                raise failures.DiracInternal("campaign preparation returned no readable result")
            return output

    @staticmethod
    def _put_object(cursor, store, *, kind: str, document: dict,
                    dependencies: list[tuple[str, str]] = (),
                    artifact_sink: list[tuple[object, str]] | None = None) -> str:
        body = _canonical({"schema_version": "rbfe-prepared-system.v2",
                           "object_kind": kind, **document})
        semantic_digest = hashlib.sha256(body).hexdigest()
        object_id = str(uuid5(NAMESPACE_URL, f"dirac:rbfe:{kind}:{semantic_digest}"))
        artifact = store.put(
            body, role=f"rbfe.{kind}", media_type="application/json",
            metadata={"scientific_scope": "campaign_preparation",
                      "object_kind": kind}, cursor=cursor)
        if artifact_sink is not None:
            artifact_sink.append((artifact, f"rbfe.{kind}"))
        cursor.execute(
            "INSERT INTO design.motif_scientific_object "
            "(id,object_kind,semantic_digest,document_artifact_id,applicability,"
            " scientific_state,disposition,claim_eligibility,reason_codes) "
            "VALUES (%s,%s,decode(%s,'hex'),%s,'applicable','provisional','selected',"
            " 'ineligible_provisional_quality',ARRAY['human_pose_review_required']) "
            "ON CONFLICT (object_kind,semantic_digest) DO UPDATE SET "
            "document_artifact_id=EXCLUDED.document_artifact_id RETURNING id::text",
            (object_id, kind, semantic_digest, artifact.id))
        oid = cursor.fetchone()[0]
        for dependency_id, role in dependencies:
            cursor.execute(
                "INSERT INTO design.motif_scientific_dependency "
                "(object_id,dependency_id,dependency_role) VALUES (%s,%s,%s) "
                "ON CONFLICT DO NOTHING", (oid, dependency_id, role))
        return oid

    @staticmethod
    def _object_ref(kind: str, object_id: str, document: dict) -> dict:
        body = _canonical({"schema_version": "rbfe-prepared-system.v2",
                           "object_kind": kind, **document})
        return campaign_state.full_ref(
            kind, object_id, "sha256:" + hashlib.sha256(body).hexdigest())

    def prepare_campaign(self, payload: dict, store,
                         actor: dict[str, str], *, job_id: str | None = None,
                         dispatch_fence=None) -> dict:
        """Prepare and durably register one raw structure plus aligned analogue poses.

        This is the browser-facing bridge.  It accepts ordinary inputs and returns
        only versioned references; callers never construct OpenFE/ GUFE JSON.
        """
        if not hasattr(store, "put") or store.__class__.__name__ != "PostgresArtifactStore":
            raise failures.DiracFailure(
                "DB_UNAVAILABLE",
                "campaign preparation requires the durable PostgreSQL artifact store")
        if not job_id:
            raise failures.DiracInternal(
                "campaign preparation requires the durable Job id that owns its artifacts")
        try:
            campaign_id = campaign_state.require_campaign_id(
                payload.get("campaign_id"))
        except (TypeError, ValueError) as error:
            raise _campaign_input_failure(
                "prepare_campaign", error, "correct_campaign_identity") from error
        if payload.get("expected_version") is None:
            raise failures.DiracInvalidParameters(
                "campaign preparation requires expected_version")
        expected_version = int(payload["expected_version"])
        scientific_payload = _prepare_scientific_payload(payload)
        try:
            scientific_payload, stereo_enumeration = (
                campaign_state.normalize_ligand_series(scientific_payload))
            input_bundle = campaign_state.canonical_digest_bundle(scientific_payload)
        except (TypeError, ValueError) as error:
            raise _campaign_input_failure(
                "prepare_campaign", error, "correct_scientific_inputs") from error
        request_digest = campaign_state.idempotency_key(
            campaign_id, expected_version, input_bundle["bundle_digest"], "prepare")
        with self._connect() as connection, connection.cursor() as cursor:
            current = self._campaign_row(cursor, campaign_id)
        if current is None or current["invalidated_at"] is not None:
            raise failures.DiracInvalidParameters(
                "campaign preparation requires a live saved campaign")
        principal = self._authorize_campaign(
            current, actor, "prepare_campaign")
        prior_receipt = current["state"].get("prepare_receipt") or {}
        if (prior_receipt.get("request_digest") == request_digest
                and prior_receipt.get("input_version") == expected_version):
            # A retry is a new durable Job even when its scientific output is a
            # cache hit.  Reattach the already-published campaign artifacts so the
            # retry Job remains a complete provenance witness without rewriting any
            # bytes or advancing the campaign generation.
            with self._connect() as connection, connection.cursor() as cursor:
                if dispatch_fence is not None:
                    dispatch_fence(cursor)
                cursor.execute(
                    "INSERT INTO app.job_artifact (job_id,artifact_id,role,ordinal) "
                    "SELECT j.id,ca.artifact_id,ca.role,ca.ordinal "
                    "FROM app.job j JOIN app.rbfe_campaign c ON c.id=%s "
                    "JOIN app.rbfe_campaign_artifact ca ON ca.campaign_id=c.id "
                    "WHERE j.id=%s AND j.actor_kind=%s AND j.actor_id=%s "
                    "AND c.created_by_kind=%s AND c.created_by_id=%s "
                    "ON CONFLICT DO NOTHING",
                    (campaign_id, job_id, principal["kind"], principal["id"],
                     principal["kind"], principal["id"]))
            return {
                **prior_receipt["response"],
                "campaign_ref": campaign_state.full_ref(
                    "rbfe_campaign", current["id"], current["state_digest"],
                    version=current["version"]),
                "campaign_scientific_ref": _campaign_scientific_ref(current),
                "campaign_version": current["version"],
                "campaign_state_digest": current["state_digest"],
                "campaign_scientific_generation": current[
                    "scientific_generation"],
                "campaign_scientific_digest": current["scientific_digest"],
                "idempotent_replay": True,
            }
        if current["version"] != expected_version:
            raise _campaign_failure(
                "prepare_campaign", "campaign version does not match",
                expected_version=expected_version, actual_version=current["version"],
                required_actions=["reload_campaign"])
        changed = campaign_state.changed_domains(
            current["state"].get("digest_bundle"), input_bundle)
        saved_verdicts = (current["state"].get("digest_bundle") or {}).get(
            "domain_verdicts", {})
        inputs_unbound = (
            not current["state"].get("owned_object_refs")
            and saved_verdicts
            and all(value == "UNVERIFIED" for value in saved_verdicts.values()))
        inputs_unbound = (inputs_unbound
                          or bool(current["state"].get("pending_changed_domains")))
        if changed and not inputs_unbound:
            raise _campaign_failure(
                "prepare_campaign",
                "campaign inputs differ from the saved campaign revision",
                expected_version=expected_version, actual_version=current["version"],
                required_actions=["save_campaign_changes", "review_inputs_again"])
        principal = campaign_state.require_actor(actor)
        prepared = self._build_campaign(scientific_payload)
        if (prepared.get("digest_bundle", {}).get("source_digest")
                != input_bundle["source_digest"]):
            raise failures.DiracInternal(
                "campaign builder source digest disagrees with the saved campaign")
        raw_pdb = str(scientific_payload["receptor_pdb"])
        prepared_pdb = str(prepared["prepared_receptor_pdb"])
        output_version = expected_version + 1
        scientific_transition = {
            "action": "campaign_prepared",
            "request_digest": request_digest,
            "bundle_digest": prepared["digest_bundle"]["bundle_digest"],
            "status": "prepared",
        }
        output_science = _scientific_pair(
            current["state"], scientific_transition)
        prepared_scientific_ref = build_campaign_scientific_ref(
            campaign_id=campaign_id,
            generation=output_science["scientific_generation"],
            digest=output_science["scientific_digest"])
        source_pdb_id = str(scientific_payload.get("source_pdb_id") or "").upper().strip()
        pdb_id = source_pdb_id if re.fullmatch(r"[0-9][A-Z0-9]{3}", source_pdb_id) else None
        target_name = str(scientific_payload.get("target_name") or source_pdb_id or
                          scientific_payload.get("campaign_name") or
                          "Imported RBFE target").strip()
        method = str(scientific_payload.get("structure_method") or "xray")
        if method not in {"xray", "cryoem", "nmr", "predicted", "model"}:
            raise failures.DiracInvalidParameters("unsupported protein structure method")
        resolution = scientific_payload.get("resolution_angstrom")
        if method not in {"xray", "cryoem"}:
            resolution = None
        with self._connect() as connection, connection.cursor() as cursor:
            locked = self._campaign_row(cursor, campaign_id, lock=True)
            if locked is None or locked["version"] != expected_version:
                raise _campaign_failure(
                    "prepare_campaign", "campaign changed while preparation was running",
                    expected_version=expected_version,
                    actual_version=locked["version"] if locked else None,
                    required_actions=["reload_campaign", "retry_prepare"])
            cursor.execute(
                "SELECT 1 FROM app.job WHERE id=%s AND actor_kind=%s AND actor_id=%s "
                "FOR UPDATE",
                (job_id, principal["kind"], principal["id"]))
            if cursor.fetchone() is None:
                raise failures.DiracInternal(
                    "campaign preparation Job ownership does not match its actor")
            if dispatch_fence is not None:
                dispatch_fence(cursor)

            # Every byte and every ownership edge uses this same cursor.  A later
            # failed campaign CAS therefore rolls the artifact rows back instead of
            # leaving content-addressed but unreachable receptor/pose objects.
            raw_artifact = store.put(
                raw_pdb.encode(), role="rbfe.receptor.source.pdb",
                media_type="chemical/x-pdb",
                metadata={"source_pdb_id": scientific_payload.get("source_pdb_id"),
                          "campaign_id": campaign_id,
                          "campaign_version": output_version,
                          "scientific_generation": output_science[
                              "scientific_generation"],
                          "scientific_scope": "campaign_preparation"},
                cursor=cursor)
            receptor_artifact = store.put(
                prepared_pdb.encode(), role="rbfe.receptor.pdb",
                media_type="chemical/x-pdb",
                metadata={"source_pdb_id": scientific_payload.get("source_pdb_id"),
                          "preparation": prepared["receptor_report"],
                          "campaign_id": campaign_id,
                          "campaign_version": output_version,
                          "scientific_generation": output_science[
                              "scientific_generation"],
                          "scientific_scope": "campaign_preparation"},
                cursor=cursor)
            pose_artifacts = []
            for pose in prepared["poses"]:
                pose_artifacts.append((pose, store.put(
                    pose["sdf"].encode(), role="rbfe.pose.sdf",
                    media_type="chemical/x-mdl-sdfile",
                    metadata={"ligand_id": pose["id"],
                              "canonical_smiles": pose["report"]["canonical_smiles"],
                              "campaign_id": campaign_id,
                              "campaign_version": output_version,
                              "scientific_generation": output_science[
                                  "scientific_generation"],
                              "scientific_scope": "campaign_preparation"},
                    cursor=cursor)))
            produced_artifacts = [
                (raw_artifact, "rbfe.receptor.source.pdb"),
                (receptor_artifact, "rbfe.receptor.pdb"),
                *[(artifact, "rbfe.pose.sdf")
                  for _pose, artifact in pose_artifacts],
            ]
            existing = None
            if pdb_id:
                cursor.execute(
                    "SELECT s.id::text,s.target_id::text,t.name::text,"
                    "trim(s.pdb_id),s.method::text,s.resolution_a::float8 "
                    "FROM bio.structure s LEFT JOIN bio.target t ON t.id=s.target_id "
                    "WHERE trim(s.pdb_id)=%s", (pdb_id,))
                existing = cursor.fetchone()
            if existing and existing[1]:
                (structure_id, target_id, target_name, pdb_id,
                 method, resolution) = existing
            else:
                cursor.execute(
                    "INSERT INTO bio.target (name,kind,note) "
                    "VALUES (%s,'protein','Created by the FEP campaign preparation UI; provisional.') "
                    "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id::text",
                    (target_name,))
                target_id = cursor.fetchone()[0]
                if existing:
                    structure_id = existing[0]
                    pdb_id, method, resolution = existing[3:]
                    cursor.execute("UPDATE bio.structure SET target_id=%s WHERE id=%s",
                                   (target_id, structure_id))
                else:
                    cursor.execute(
                        "INSERT INTO bio.structure (pdb_id,target_id,method,resolution_a) "
                        "VALUES (%s,%s,%s,%s) RETURNING id::text",
                        (pdb_id, target_id, method, resolution))
                    structure_id = cursor.fetchone()[0]

            normalized_resolution = (
                float(resolution) if resolution is not None else None)
            target_ref = _target_ref(target_id, target_name)
            structure_ref = _protein_structure_ref(
                structure_id, target_id=target_id, pdb_id=pdb_id,
                method=method, resolution_angstrom=normalized_resolution)

            ligand_state_document = {
                "label": f"{target_name} · canonical ligand state ensemble",
                "campaign_scientific_ref": prepared_scientific_ref,
                "canonical_ligands": input_bundle["canonical_ligands"],
                "microstates": input_bundle["microstates"],
                "ligand_policy": input_bundle["ligand_policy"],
                "canonical_ligands_digest": input_bundle["canonical_ligands_digest"],
                "microstates_digest": input_bundle["microstates_digest"],
                "ligand_policy_digest": input_bundle["ligand_policy_digest"],
                "verdict": "CONFIRMED",
            }
            ligand_state_id = self._put_object(
                cursor, store, kind="chemical_state_ensemble",
                document=ligand_state_document,
                artifact_sink=produced_artifacts)
            ligand_state_ref = self._object_ref(
                "chemical_state_ensemble", ligand_state_id, ligand_state_document)
            source_document = {
                    "label": f"{source_pdb_id or 'UPLOAD'} · raw structure",
                    "campaign_scientific_ref": prepared_scientific_ref,
                    "target_ref": target_ref,
                    "protein_structure_ref": structure_ref,
                    "source_pdb_id": source_pdb_id or None,
                    "coordinate_artifact_ref": _artifact_ref(raw_artifact),
                    "coordinate_digest": _sha(raw_pdb),
                    "source_digest": input_bundle["source_digest"],
                    "verdict": "CONFIRMED",
                }
            source_id = self._put_object(
                cursor, store, kind="protein_structure_source",
                document=source_document,
                artifact_sink=produced_artifacts)
            source_ref = self._object_ref(
                "protein_structure_source", source_id, source_document)
            receptor_document = {
                    "label": (f"{target_name} · {source_pdb_id or 'uploaded structure'} · "
                              "PDBFixer prepared"),
                    "campaign_scientific_ref": prepared_scientific_ref,
                    "target_ref": target_ref,
                    "protein_structure_ref": structure_ref,
                    "source_pdb_id": source_pdb_id or None,
                    "source_object_ref": source_ref,
                    "ligand_state_ensemble_ref": ligand_state_ref,
                    "coordinate_artifact_ref": _artifact_ref(receptor_artifact),
                    "coordinate_frame_digest": _sha(prepared_pdb),
                    "preparation": prepared["receptor_report"],
                    "reference_ligand": prepared["reference_ligand"],
                    "reference_canonical_smiles": prepared["reference_canonical_smiles"],
                    "digest_bundle": prepared["digest_bundle"],
                    "receptor_policy": input_bundle["receptor_policy"],
                    "claim_boundary": prepared["claim_boundary"],
                    "verdict": "UNVERIFIED",
                }
            receptor_id = self._put_object(
                cursor, store, kind="prepared_receptor_state",
                dependencies=[(source_id, "prepared_from"),
                              (ligand_state_id, "reference_state")],
                document=receptor_document,
                artifact_sink=produced_artifacts)
            receptor_ref = self._object_ref(
                "prepared_receptor_state", receptor_id, receptor_document)
            pose_rows = []
            pose_object_refs = []
            for pose, artifact in pose_artifacts:
                pose_document = {
                        "label": str(pose["id"]),
                        "campaign_scientific_ref": prepared_scientific_ref,
                        "canonical_smiles": pose["report"]["canonical_smiles"],
                        "prepared_receptor_state_ref": receptor_ref,
                        "ligand_state_ensemble_ref": ligand_state_ref,
                        "coordinate_artifact_ref": _artifact_ref(artifact),
                        "coordinate_frame_digest": _sha(prepared_pdb),
                        "pose_source": "ETKDG + MMFF/UFF + crystallographic MCS alignment",
                        "pose_report": pose["report"],
                        "claim_boundary": (
                            "Reference-constrained pose hypothesis; human review required."),
                        "review_state": "pending", "verdict": "UNVERIFIED",
                    }
                pose_id = self._put_object(
                    cursor, store, kind="pose_hypothesis",
                    dependencies=[(receptor_id, "aligned_to"),
                                  (ligand_state_id, "state_from")],
                    document=pose_document,
                    artifact_sink=produced_artifacts)
                pose_ref = self._object_ref(
                    "pose_hypothesis", pose_id, pose_document)
                pose_object_refs.append(pose_ref)
                pose_rows.append({
                    "pose_ref": pose_ref,
                    "label": str(pose["id"]),
                    "canonical_smiles": pose["report"]["canonical_smiles"],
                    "core_rmsd_angstrom": pose["report"]["core_rmsd_angstrom"],
                    "core_coverage": pose["report"]["minimum_bidirectional_coverage"],
                    "minimum_heavy_atom_distance_angstrom": pose["report"]["minimum_heavy_atom_distance_angstrom"],
                    "protein_contacts_within_6_angstrom": pose["report"]["protein_contacts_within_6_angstrom"],
                    "coordinate_artifact_ref": _artifact_ref(artifact),
                    "review_state": "pending",
                })
            pose_ensemble_document = {
                "label": f"{target_name} · receptor-aligned pose ensemble",
                "campaign_scientific_ref": prepared_scientific_ref,
                "prepared_receptor_state_ref": receptor_ref,
                "pose_refs": pose_object_refs,
                "poses_digest": prepared["digest_bundle"]["poses_digest"],
                "review_state": "pending", "verdict": "UNVERIFIED",
            }
            pose_ensemble_id = self._put_object(
                cursor, store, kind="pose_ensemble",
                dependencies=[(pose_ref["id"], "contains")
                              for pose_ref in pose_object_refs],
                document=pose_ensemble_document,
                artifact_sink=produced_artifacts)
            pose_ensemble_ref = self._object_ref(
                "pose_ensemble", pose_ensemble_id, pose_ensemble_document)
            output_bundle = prepared["digest_bundle"]
            artifact_refs = {
                "source": source_ref, "canonical_ligands": ligand_state_ref,
                "microstates": ligand_state_ref,
                "prepared_receptor": receptor_ref, "poses": pose_ensemble_ref,
            }
            dag = campaign_state.dependency_dag(output_bundle, artifact_refs)
            stages = dict(locked["state"].get("stages") or {})
            stages.update(prepared.get("stages") or {})
            stages["registration"] = campaign_state.stage_payload(
                "registration", "CONFIRMED",
                refs=[source_ref, ligand_state_ref, receptor_ref,
                      pose_ensemble_ref, *pose_object_refs],
                digests={"bundle_digest": output_bundle["bundle_digest"]})
            response = {
            "prepared_receptor_state_ref": receptor_ref,
            "source_ref": source_ref,
            "ligand_state_ensemble_ref": ligand_state_ref,
            "pose_ensemble_ref": pose_ensemble_ref,
            "target_ref": target_ref,
            "protein_structure_ref": structure_ref,
            "label": f"{target_name} · {source_pdb_id or 'upload'}",
            "target_name": target_name, "pdb_id": source_pdb_id or None,
            "experimental_method": method,
            "resolution_angstrom": normalized_resolution,
            "preparation_state": "server-attested-human-review-pending",
            "coordinate_artifact_ref": _artifact_ref(receptor_artifact),
            "receptor_report": prepared["receptor_report"],
            "stereo_enumeration": stereo_enumeration,
            "reference_ligand": prepared["reference_ligand"],
            "poses": pose_rows,
            "digest_bundle": output_bundle,
            "artifact_dag": dag, "stages": stages,
            "verdict": "UNVERIFIED",
            "claim_boundary": prepared["claim_boundary"],
            }
            owned_refs = [source_ref, ligand_state_ref, receptor_ref,
                          pose_ensemble_ref, *pose_object_refs]
            state = dict(locked["state"])
            state.update({
                "version": output_version, "status": "prepared",
                "verdict": "UNVERIFIED", "actor": principal,
                "inputs": scientific_payload,
                "digest_bundle": output_bundle, "artifact_dag": dag,
                "stages": stages, "owned_object_refs": owned_refs,
                "prepared_scientific_ref": prepared_scientific_ref,
            })
            state.pop("pending_changed_domains", None)
            receipt_response = dict(response)
            receipt = {
                "request_digest": request_digest,
                "input_version": expected_version,
                "output_version": output_version,
                "response": receipt_response,
                "verdict": "CONFIRMED",
            }
            state["prepare_receipt"] = receipt
            state = _advance_scientific_state(
                state, locked["state"], scientific_transition)
            response["campaign_ref"] = campaign_state.full_ref(
                "rbfe_campaign", campaign_id, state["state_digest"],
                version=output_version)
            response["campaign_scientific_ref"] = prepared_scientific_ref
            response["campaign_version"] = output_version
            response["campaign_state_digest"] = state["state_digest"]
            response["campaign_scientific_generation"] = state[
                "scientific_generation"]
            response["campaign_scientific_digest"] = state[
                "scientific_digest"]
            cursor.execute(
                "UPDATE app.rbfe_campaign SET version=%s,status='prepared',state=%s,"
                "state_digest=decode(%s,'hex'),scientific_generation=%s,"
                "scientific_digest=decode(%s,'hex'),updated_at=now() "
                "WHERE id=%s AND version=%s AND invalidated_at IS NULL RETURNING id",
                (output_version, json.dumps(state), _hex_digest(state["state_digest"]),
                 state["scientific_generation"],
                 _hex_digest(state["scientific_digest"]), campaign_id,
                 expected_version))
            if cursor.fetchone() is None:
                raise _campaign_failure(
                    "prepare_campaign", "campaign changed while artifacts were registered",
                    expected_version=expected_version,
                    required_actions=["reload_campaign", "retry_prepare"])
            role_ordinals: dict[str, int] = {}
            for artifact, role in produced_artifacts:
                ordinal = role_ordinals.get(role, 0)
                role_ordinals[role] = ordinal + 1
                store.link_to_job(
                    job_id, artifact.id, role, ordinal, cursor=cursor)
                store.link_to_campaign(
                    campaign_id, artifact.id, role, ordinal, cursor=cursor)
            revision_row = {
                **locked, "version": output_version, "status": "prepared",
                "state": state, "state_digest": state["state_digest"],
                "scientific_generation": state["scientific_generation"],
                "scientific_digest": state["scientific_digest"],
            }
            self._insert_revision(
                cursor, revision_row,
                changed_domains=["prepared_receptor", "poses"],
                reason="campaign_prepared", actor=principal)
        return response

    def accept_poses(self, payload: dict, actor: dict[str, str]) -> dict:
        """Persist an attributable, digest-bound review as a campaign revision."""
        try:
            campaign_id = campaign_state.require_campaign_id(
                payload.get("campaign_id"))
            authenticated_actor = campaign_state.require_actor(actor)
        except (TypeError, ValueError) as error:
            raise _campaign_input_failure(
                "accept_poses", error, "correct_review_attestation") from error
        if payload.get("expected_version") is None:
            raise failures.DiracInvalidParameters(
                "pose review requires expected_version")
        expected_version = int(payload["expected_version"])
        if authenticated_actor["kind"] != "human":
            raise failures.DiracInvalidParameters(
                "pose acceptance that unlocks RBFE execution requires a human actor")
        # A scientific attestation may only name the transport-authenticated
        # principal.  The browser cannot nominate a different reviewer.
        reviewer = authenticated_actor
        try:
            receptor_ref = campaign_state.full_ref(
                "prepared_receptor_state",
                payload["prepared_receptor_state_ref"].get("id"),
                payload["prepared_receptor_state_ref"].get("sha256"))
            if payload["prepared_receptor_state_ref"].get("kind") \
                    != "prepared_receptor_state":
                raise ValueError("prepared receptor ref kind is incorrect")
            declared_pose_refs = [
                campaign_state.full_ref(
                    "pose_hypothesis", row.get("id"), row.get("sha256"))
                for row in payload["pose_refs"]
            ]
            if any(row.get("kind") != "pose_hypothesis"
                   for row in payload["pose_refs"]):
                raise ValueError("pose ref kind is incorrect")
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise failures.DiracInvalidParameters(
                "pose review requires complete content-addressed receptor and "
                "pose references") from error
        receptor_id = receptor_ref["id"]
        pose_ids = [row["id"] for row in declared_pose_refs]
        if len(pose_ids) < 2 or len(set(pose_ids)) != len(pose_ids):
            raise failures.DiracInvalidParameters(
                "pose review requires at least two distinct endpoint poses")
        request_digest = campaign_state.sha256_digest({
            key: value for key, value in payload.items() if key != "expected_version"
        })
        with self._connect() as connection, connection.cursor() as cursor:
            current = self._campaign_row(cursor, campaign_id, lock=True)
            if current is None or current["invalidated_at"] is not None:
                raise failures.DiracInvalidParameters(
                    "pose review requires a live campaign")
            self._authorize_campaign(
                current, authenticated_actor, "accept_poses")
            prior = current["state"].get("pose_review_receipt") or {}
            if (prior.get("request_digest") == request_digest
                    and prior.get("input_version") == expected_version):
                return {
                    **prior["response"],
                    "campaign_ref": campaign_state.full_ref(
                        "rbfe_campaign", current["id"], current["state_digest"],
                        version=current["version"]),
                    "campaign_scientific_ref": _campaign_scientific_ref(current),
                    "campaign_version": current["version"],
                    "campaign_state_digest": current["state_digest"],
                    "campaign_scientific_generation": current[
                        "scientific_generation"],
                    "campaign_scientific_digest": current[
                        "scientific_digest"],
                    "idempotent_replay": True,
                }
            if current["version"] != expected_version:
                raise _campaign_failure(
                    "accept_poses", "campaign version does not match",
                    expected_version=expected_version, actual_version=current["version"],
                    required_actions=["reload_campaign", "repeat_pose_review"])
            receptor_record = self._object_record(
                cursor, receptor_id, "prepared_receptor_state")
            if (_document_campaign_scientific_ref(
                    receptor_record["document"])
                    != current["state"].get("prepared_scientific_ref")):
                raise failures.DiracInvalidParameters(
                    "prepared receptor does not belong to this campaign scientific generation")
            if receptor_ref != receptor_record["ref"]:
                raise failures.DiracInvalidParameters(
                    "prepared receptor ref digest does not match the registered object")
            pose_records = []
            for pose_id in pose_ids:
                record = self._object_record(cursor, pose_id, "pose_hypothesis")
                pose = record["document"]
                if (_document_campaign_scientific_ref(pose)
                        != current["state"].get("prepared_scientific_ref")
                        or pose.get("prepared_receptor_state_ref", {}).get("id") != receptor_id):
                    raise failures.DiracInvalidParameters(
                        "reviewed pose is not owned by and aligned to this campaign receptor")
                declared = next(
                    row for row in declared_pose_refs if row["id"] == pose_id)
                if declared != record["ref"]:
                    raise failures.DiracInvalidParameters(
                        "reviewed pose ref digest does not match the registered object")
                report = pose.get("pose_report") or {}
                if (report.get("geometry_gate") != "passed"
                        or float(report.get("core_rmsd_angstrom", 999)) > 1.0
                        or float(report.get("minimum_bidirectional_coverage", 0)) < .5
                        or float(report.get(
                            "minimum_heavy_atom_distance_angstrom", 0)) < 1.5):
                    raise failures.DiracInvalidParameters(
                        "pose cannot be accepted because a server-measured geometry gate failed")
                pose_records.append(record)
            full_pose_refs = [record["ref"] for record in pose_records]
            viewed_digests = payload.get("viewed_pose_digests")
            if viewed_digests is not None:
                if set(viewed_digests) != {
                        ref["sha256"] for ref in full_pose_refs}:
                    raise failures.DiracInvalidParameters(
                        "viewed_pose_digests must exactly cover the reviewed pose set")
            else:
                viewed = payload.get("viewed_pose_refs") or payload["pose_refs"]
                if {str(ref.get("id")) for ref in viewed} != set(pose_ids):
                    raise failures.DiracInvalidParameters(
                        "viewed_pose_refs must exactly cover the reviewed pose set")
            review_reason = payload.get("review_reason", payload.get("reason"))
            try:
                attestation = campaign_state.review_attestation(
                    campaign_id=campaign_id, campaign_version=expected_version,
                    reviewer=reviewer, reviewed_at=payload.get("reviewed_at"),
                    reason=review_reason, viewed_pose_refs=full_pose_refs,
                    review_checks=payload.get("review_checks") or [])
            except (TypeError, ValueError) as error:
                raise _campaign_input_failure(
                    "accept_poses", error, "repeat_pose_review") from error
            attestation_ref = campaign_state.full_ref(
                "rbfe_pose_review_attestation",
                str(uuid5(NAMESPACE_URL, attestation["attestation_digest"])),
                attestation["attestation_digest"])
            reviewed = []
            for record in pose_records:
                cursor.execute(
                    "UPDATE design.motif_scientific_object SET "
                    "scientific_state='accepted',disposition='selected',"
                    "claim_eligibility='ineligible_unvalidated_method',"
                    "reason_codes=ARRAY['human_pose_reviewed','reference_constrained_pose'] "
                    "WHERE id=%s AND object_kind='pose_hypothesis' "
                    "AND invalidated_at IS NULL RETURNING id::text",
                    (record["ref"]["id"],))
                row = cursor.fetchone()
                if row is None:
                    raise failures.DiracInvalidParameters(
                        "pose review target is unavailable")
                reviewed.append(record["ref"])
            owned = current["state"].get("owned_object_refs") or []
            pose_ensemble_ids = [ref["id"] for ref in owned
                                 if ref.get("kind") == "pose_ensemble"]
            if pose_ensemble_ids:
                cursor.execute(
                    "UPDATE design.motif_scientific_object SET scientific_state='accepted',"
                    "disposition='selected',claim_eligibility='ineligible_unvalidated_method',"
                    "reason_codes=ARRAY['human_pose_reviewed'] "
                    "WHERE id=ANY(%s::uuid[]) AND invalidated_at IS NULL",
                    (pose_ensemble_ids,))
            inputs = current["state"].get("inputs") or {}
            bundle = campaign_state.canonical_digest_bundle(
                inputs, pose_review=attestation)
            for key, value in current["state"].get("digest_bundle", {}).items():
                if key in {"prepared_receptor_digest", "poses_digest"}:
                    bundle[key] = value
            bundle["bundle_digest"] = campaign_state.sha256_digest({
                key: value for key, value in bundle.items()
                if key.endswith("_digest") and key != "bundle_digest"
            })
            artifact_refs = {
                name: node.get("artifact_ref")
                for name, node in current["state"]["artifact_dag"]["nodes"].items()
                if node.get("artifact_ref") is not None
            }
            artifact_refs["pose_review"] = attestation_ref
            dag = campaign_state.dependency_dag(bundle, artifact_refs)
            stages = dict(current["state"].get("stages") or {})
            stages["pose_review"] = campaign_state.stage_payload(
                "pose_review", "CONFIRMED",
                refs=[attestation_ref, *reviewed],
                digests={"pose_review_digest": bundle["pose_review_digest"],
                         "attestation_digest": attestation["attestation_digest"]})
            output_version = expected_version + 1
            response = {
                "prepared_receptor_state_ref": receptor_record["ref"],
                "poses": reviewed, "review_state": "accepted",
                "review_attestation": attestation,
                "review_attestation_ref": attestation_ref,
                "digest_bundle": bundle, "artifact_dag": dag,
                "stages": stages, "verdict": "UNVERIFIED",
                "claim_boundary": (
                    "Human-reviewed reference-constrained poses. This acceptance does not "
                    "constitute an FEP result or validate the pose-generation method."),
            }
            state = dict(current["state"])
            state.update({
                "version": output_version, "status": "poses_reviewed",
                "verdict": "UNVERIFIED", "actor": reviewer,
                "digest_bundle": bundle, "artifact_dag": dag, "stages": stages,
                "review_attestation": attestation,
                "pose_review_receipt": {
                    "request_digest": request_digest,
                    "input_version": expected_version,
                    "output_version": output_version,
                    "response": response, "verdict": "CONFIRMED",
                },
            })
            state = _advance_scientific_state(
                state, current["state"], {
                    "action": "poses_reviewed",
                    "attestation_digest": attestation["attestation_digest"],
                    "bundle_digest": bundle["bundle_digest"],
                    "status": "poses_reviewed",
                })
            response["campaign_ref"] = campaign_state.full_ref(
                "rbfe_campaign", campaign_id, state["state_digest"],
                version=output_version)
            response["campaign_scientific_ref"] = build_campaign_scientific_ref(
                campaign_id=campaign_id,
                generation=state["scientific_generation"],
                digest=state["scientific_digest"])
            response["campaign_version"] = output_version
            response["campaign_state_digest"] = state["state_digest"]
            response["campaign_scientific_generation"] = state[
                "scientific_generation"]
            response["campaign_scientific_digest"] = state[
                "scientific_digest"]
            cursor.execute(
                "UPDATE app.rbfe_campaign SET version=%s,status='poses_reviewed',state=%s,"
                "state_digest=decode(%s,'hex'),scientific_generation=%s,"
                "scientific_digest=decode(%s,'hex'),updated_at=now() "
                "WHERE id=%s AND version=%s AND invalidated_at IS NULL RETURNING id",
                (output_version, json.dumps(state), _hex_digest(state["state_digest"]),
                 state["scientific_generation"],
                 _hex_digest(state["scientific_digest"]), campaign_id,
                 expected_version))
            if cursor.fetchone() is None:
                raise _campaign_failure(
                    "accept_poses", "campaign changed during pose review",
                    expected_version=expected_version,
                    required_actions=["reload_campaign", "repeat_pose_review"])
            revision_row = {
                **current, "version": output_version, "status": "poses_reviewed",
                "state": state, "state_digest": state["state_digest"],
                "scientific_generation": state["scientific_generation"],
                "scientific_digest": state["scientific_digest"],
            }
            self._insert_revision(
                cursor, revision_row, changed_domains=["pose_review"],
                reason=str(review_reason), actor=reviewer)
        return response


__all__ = ["PostgresRbfeReferenceResolver"]
