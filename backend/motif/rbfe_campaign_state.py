"""Canonical RBFE campaign identity, provenance, and invalidation rules.

This module deliberately has no database or HTTP dependency.  It defines the
document that the PostgreSQL resolver persists and the dependency graph that
every transport must enforce.  A UI badge is never an input to these rules.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable
from uuid import NAMESPACE_URL, UUID, uuid5


SCHEMA_VERSION = "rbfe-campaign-state.v1"
VERDICTS = frozenset({"CONFIRMED", "OVERTURNED", "UNVERIFIED"})
REVIEW_CHECKS = frozenset({
    "shared_coordinate_frame", "core_alignment", "pocket_geometry",
})
RECEPTOR_POLICY_FIELDS = frozenset({
    "assembly_id", "chain_ids", "missing_atoms", "missing_residues", "altloc",
    "occupancy", "waters", "cofactors", "metals", "histidines", "termini",
    "ph", "forcefield_contract",
})
LIGAND_POLICY_FIELDS = frozenset({
    "formal_charge", "tautomer", "protonation", "stereochemistry",
    "state_population_cutoff",
})

# Edges point from an artifact to the artifacts/inputs it consumes.  Inputs are
# nodes too: this makes a changed policy a graph operation instead of a bespoke
# collection of UI resets.
DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "source": (),
    "reference": ("source",),
    "canonical_ligands": (),
    "microstates": ("canonical_ligands",),
    "prep_policy": (),
    "prepared_receptor": ("source", "reference", "microstates", "prep_policy"),
    "poses": ("prepared_receptor", "reference", "microstates"),
    "pose_review": ("poses",),
    "network": ("microstates", "pose_review"),
    "protocol": (),
    "system_build": ("prepared_receptor", "pose_review", "network", "protocol"),
}

INPUT_DOMAINS = frozenset({
    "source", "reference", "canonical_ligands", "microstates", "prep_policy",
    "pose_review", "network", "protocol",
})

PUBLIC_DOMAIN_ROOTS: dict[str, tuple[str, ...]] = {
    "project_context": (),
    "receptor": ("source",),
    "reference": ("reference",),
    "prep_policy": ("prep_policy",),
    "ligand_identity": ("canonical_ligands",),
    "ligand_policy": ("microstates", "prep_policy"),
    "pose_review": ("pose_review",),
    "network": ("network",),
    "protocol": ("protocol",),
    "execution": ("system_build",),
}


def canonical_bytes(value: Any) -> bytes:
    """Return the only byte representation allowed to acquire a digest."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def sha256_digest(value: Any) -> str:
    data = value if isinstance(value, bytes) else canonical_bytes(value)
    return "sha256:" + hashlib.sha256(data).hexdigest()


def require_digest(value: str, field: str = "digest") -> str:
    text = str(value or "")
    if (len(text) != 71 or not text.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in text[7:])):
        raise ValueError(f"{field} must be a complete sha256 digest")
    return text


def require_actor(actor: dict[str, str]) -> dict[str, str]:
    if not isinstance(actor, dict):
        raise ValueError("actor must be an object")
    kind, identifier = str(actor.get("kind") or ""), str(actor.get("id") or "").strip()
    if kind == "user":
        kind = "human"
    if kind not in {"human", "agent", "service"} or not identifier:
        raise ValueError(
            "actor requires kind human/user/agent/service and a non-empty id")
    return {"kind": kind, "id": identifier}


def require_campaign_id(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("campaign_id must be a UUID") from error


def stable_campaign_id(payload: dict, actor: dict[str, str]) -> str:
    """Resolve an explicit UUID or a retry-stable client identity.

    `campaign_key` is intentionally logical, not content-derived: changing a
    receptor must advance one campaign rather than silently create another one.
    The command schema may use campaign_name as the legacy key until clients send
    an explicit UUID on every mutation.
    """
    if payload.get("campaign_id"):
        return require_campaign_id(payload["campaign_id"])
    principal = require_actor(actor)
    key = str(payload.get("campaign_key") or payload.get("campaign_name") or "").strip()
    if not key:
        raise ValueError("a new campaign requires campaign_key or campaign_name")
    return str(uuid5(
        NAMESPACE_URL,
        f"dirac:rbfe:campaign:{principal['kind']}:{principal['id']}:{key}",
    ))


def _canonical_ligands(compounds: Iterable[dict]) -> list[dict]:
    try:
        from rdkit import Chem
    except ImportError as error:  # pragma: no cover - deployment invariant
        raise ValueError("RDKit is required to canonicalize campaign ligands") from error
    rows = []
    seen_ids: set[str] = set()
    for source in compounds:
        identifier = str(source.get("id") or "").strip()
        if not identifier or identifier in seen_ids:
            raise ValueError("compound ids must be non-empty and unique")
        molecule = Chem.MolFromSmiles(str(source.get("smiles") or ""))
        if molecule is None:
            raise ValueError(f"compound {identifier!r} has invalid SMILES")
        seen_ids.add(identifier)
        rows.append({
            "id": identifier,
            "canonical_smiles": Chem.MolToSmiles(
                molecule, canonical=True, isomericSmiles=True),
            "formal_charge": int(sum(
                atom.GetFormalCharge() for atom in molecule.GetAtoms())),
        })
    if not 2 <= len(rows) <= 64:
        raise ValueError("campaign requires 2..64 canonical ligands")
    return sorted(rows, key=lambda row: row["id"])


def normalize_ligand_series(payload: dict) -> tuple[dict, dict]:
    """Resolve or reject unspecified stereochemistry before any 3D artifact exists.

    The returned document is the only document a campaign builder may consume.
    Enumeration therefore creates durable child identities rather than letting an
    embedding seed silently choose one stereoisomer.
    """
    try:
        from rdkit import Chem
        from rdkit.Chem.EnumerateStereoisomers import (
            EnumerateStereoisomers, StereoEnumerationOptions,
        )
    except ImportError as error:  # pragma: no cover - deployment invariant
        raise ValueError("RDKit stereo enumeration is unavailable") from error
    document = deepcopy(payload)
    compounds = list(document.get("compounds") or [])
    policy = document.get("ligand_policy") or {}
    mode = str(policy.get("stereochemistry") or "preserve_block_unknown")
    if mode not in {"preserve_block_unknown", "enumerate_unknown"}:
        raise ValueError("ligand_policy.stereochemistry is unsupported")
    parent_id = str(document.get("parent_id") or "")
    expanded: list[dict] = []
    provenance: list[dict] = []
    for row in compounds:
        identifier = str(row.get("id") or "").strip()
        molecule = Chem.MolFromSmiles(str(row.get("smiles") or ""))
        if molecule is None:
            raise ValueError(f"compound {identifier!r} has invalid SMILES")
        options = StereoEnumerationOptions(
            onlyUnassigned=True, unique=True, maxIsomers=32,
            tryEmbedding=False,
        )
        isomers = list(EnumerateStereoisomers(molecule, options=options))
        identities = sorted({
            Chem.MolToSmiles(item, canonical=True, isomericSmiles=True)
            for item in isomers
        }) or [Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)]
        if len(identities) > 1 and identifier == parent_id:
            raise ValueError(
                f"Parent {identifier!r} has unspecified stereochemistry; the "
                "crystallographic parent must be supplied as one explicit isomer")
        if len(identities) > 1 and mode == "preserve_block_unknown":
            raise ValueError(
                f"compound {identifier!r} has {len(identities)} possible stereoisomers; "
                "select ENUMERATE UNKNOWN or provide an explicit isomeric SMILES")
        if len(identities) > 16:
            raise ValueError(
                f"compound {identifier!r} expands to {len(identities)} stereoisomers; "
                "enumerate a scientifically bounded subset explicitly")
        if len(identities) == 1:
            expanded.append({**row, "id": identifier, "smiles": identities[0],
                             "source_compound_id": identifier,
                             "stereo_state": "specified_or_achiral"})
            provenance.append({"source_id": identifier, "child_ids": [identifier],
                               "count": 1, "verdict": "CONFIRMED"})
            continue
        width = max(2, len(str(len(identities))))
        children = []
        for index, smiles in enumerate(identities, 1):
            child_id = f"{identifier}__stereo{index:0{width}d}"
            children.append(child_id)
            expanded.append({**row, "id": child_id, "smiles": smiles,
                             "source_compound_id": identifier,
                             "stereo_state": "enumerated",
                             "stereo_child_index": index,
                             "stereo_child_count": len(identities)})
        provenance.append({"source_id": identifier, "child_ids": children,
                           "count": len(children), "verdict": "CONFIRMED"})
    if len(expanded) > 64:
        raise ValueError(
            f"stereochemistry enumeration creates {len(expanded)} identities; "
            "campaign maximum is 64")
    document["compounds"] = expanded
    document["stereo_enumeration"] = {
        "schema_version": "rbfe-stereo-enumeration.v1",
        "policy": mode, "sources": provenance,
        "input_count": len(compounds), "output_count": len(expanded),
        "verdict": "CONFIRMED",
    }
    return document, document["stereo_enumeration"]


def _stage_input(value: Any, stage: str) -> dict:
    if value is None:
        return {"stage": stage, "verdict": "UNVERIFIED", "value": None}
    return {"stage": stage, "verdict": "CONFIRMED", "value": value}


def canonical_digest_bundle(payload: dict, *, pose_review: dict | None = None,
                            protocol: dict | str | None = None,
                            network: dict | None = None) -> dict[str, Any]:
    """Digest every scientific domain independently, then seal the bundle."""
    ligands = _canonical_ligands(payload.get("compounds") or [])
    source = {
        "source_pdb_id": str(payload.get("source_pdb_id") or "").upper().strip() or None,
        "structure_method": payload.get("structure_method") or "xray",
        "resolution_angstrom": payload.get("resolution_angstrom"),
        "receptor_pdb_sha256": sha256_digest(
            str(payload.get("receptor_pdb") or "").encode()),
    }
    parent_id = str(payload.get("parent_id") or "")
    parent = next((row for row in ligands if row["id"] == parent_id), None)
    if parent is None:
        raise ValueError("parent_id is absent from the canonical ligand series")
    selector = payload.get("reference_ligand") or {}
    if not isinstance(selector, dict):
        raise ValueError("reference_ligand must be a structured selector")
    reference = {
        "selector": {key: selector.get(key) for key in
                     ("resname", "chain", "residue_number", "role",
                      "altloc", "occupancy")},
        "parent_id": parent_id,
        "reference_canonical_smiles": parent["canonical_smiles"],
    }
    ligand_policy = payload.get("ligand_policy")
    if ligand_policy is None:
        # Compatibility for the v1 builder.  Every old field is expanded into
        # the new structured policy; none is silently discarded.
        ligand_policy = {
            "formal_charge": payload.get("formal_charge", "input_microstate"),
            "tautomer": payload.get("tautomers", "max_2_identity"),
            "protonation": payload.get("protonation", {
                "method": "server_assign", "ph": payload.get("ph", 7.4),
            }),
            "stereochemistry": payload.get("stereo", "preserve_specified"),
            "state_population_cutoff": payload.get("state_population_cutoff", 0.0),
            "charge_edges": payload.get("charge_edges", "block_by_default"),
            "compatibility_mode": "legacy_fields_expanded",
        }
    elif not isinstance(ligand_policy, dict) or not ligand_policy:
        raise ValueError("ligand_policy must be a non-empty structured object")
    missing_ligand_policy = LIGAND_POLICY_FIELDS.difference(ligand_policy)
    if missing_ligand_policy:
        raise ValueError(
            f"ligand_policy is missing required fields: {sorted(missing_ligand_policy)}")
    receptor_policy = deepcopy(payload.get("receptor_policy"))
    if receptor_policy is None:
        receptor_policy = {
            "assembly_id": payload.get("assembly_id", "deposited_asymmetric_unit"),
            "chain_ids": payload.get("chain_ids", []),
            "altloc": payload.get("altloc", "highest_occupancy_report"),
            "occupancy": payload.get("occupancy", "preserve_report"),
            "waters": payload.get("waters", {
                "mode": "keep" if payload.get("keep_waters", True) else "remove",
                "site_decisions": payload.get("water_site_decisions", []),
            }),
            "cofactors": payload.get("cofactors", "keep_parameter_gate"),
            "metals": payload.get("metals", "keep_parameter_gate"),
            "histidines": payload.get("histidines", "server_assign_report"),
            "termini": payload.get("termini", "server_assign_report"),
            "ph": payload.get("ph", 7.4),
            "forcefield_contract": payload.get("forcefield_contract", {
                "protein": "AMBER ff14SB", "ligand": "OpenFF 2.2.1",
                "water": "TIP3P", "ionic_strength_molar": 0.15,
                "release": "openfe-rfe-standard-v1",
            }),
            "missing_atoms": payload.get("missing_atoms", "auto_repair_report"),
            "missing_residues": payload.get("missing_residues", "auto_repair_report"),
            "compatibility_mode": "legacy_fields_expanded",
        }
    elif not isinstance(receptor_policy, dict) or not receptor_policy:
        raise ValueError("receptor_policy must be a non-empty structured object")
    elif not isinstance(receptor_policy.get("waters"), dict):
        # Public v1 keeps water mode and site decisions as siblings.  The
        # canonical policy nests them so the two fields cannot drift apart.
        if "water_site_decisions" not in receptor_policy:
            raise ValueError("receptor_policy requires water_site_decisions")
        receptor_policy["waters"] = {
            "mode": receptor_policy["waters"],
            "site_decisions": receptor_policy.pop("water_site_decisions"),
        }
    if not isinstance(receptor_policy.get("waters"), dict):
        receptor_policy["waters"] = {
            "mode": receptor_policy.get("waters"),
            "site_decisions": payload.get("water_site_decisions", []),
        }
    missing_receptor_policy = RECEPTOR_POLICY_FIELDS.difference(receptor_policy)
    if missing_receptor_policy:
        raise ValueError(
            "receptor_policy is missing required fields: "
            f"{sorted(missing_receptor_policy)}")
    waters = receptor_policy["waters"]
    if (not isinstance(waters, dict)
            or not {"mode", "site_decisions"}.issubset(waters)):
        raise ValueError(
            "receptor_policy.waters requires mode and site_decisions")
    if not isinstance(waters["site_decisions"], list):
        raise ValueError("receptor_policy.waters.site_decisions must be a list")
    if not isinstance(receptor_policy["chain_ids"], list):
        raise ValueError("receptor_policy.chain_ids must be a list")
    if not isinstance(receptor_policy["forcefield_contract"], dict):
        raise ValueError("receptor_policy.forcefield_contract must be an object")
    microstates = {
        "ligands": [{
            **row,
            "microstate_id": next((
                str(source_row.get("microstate_id"))
                for source_row in payload.get("compounds") or []
                if str(source_row.get("id")) == row["id"]
                and source_row.get("microstate_id") is not None
            ), None),
        } for row in ligands],
        "policy": ligand_policy,
    }
    prep_policy = {
        "receptor_policy": receptor_policy,
        "ligand_policy": ligand_policy,
        "pose_strategy": payload.get("pose_strategy"),
        "minimum_core_coverage": payload.get("minimum_core_coverage", .5),
        "seed": payload.get("seed", 20260816),
    }
    values = {
        "source": source,
        "reference": reference,
        "canonical_ligands": ligands,
        "microstates": microstates,
        "prep_policy": prep_policy,
        "pose_review": _stage_input(pose_review, "pose_review"),
        "protocol": _stage_input(protocol, "protocol"),
        "network": _stage_input(network, "network"),
    }
    digests = {f"{name}_digest": sha256_digest(value)
               for name, value in values.items()}
    digests.update({
        "receptor_policy_digest": sha256_digest(receptor_policy),
        "ligand_policy_digest": sha256_digest(ligand_policy),
    })
    complete = {"schema_version": SCHEMA_VERSION, **digests}
    return {
        **digests,
        "bundle_digest": sha256_digest(complete),
        "canonical_ligands": ligands,
        "microstates": microstates,
        "receptor_policy": receptor_policy,
        "ligand_policy": ligand_policy,
        "domain_verdicts": {
            "source": "CONFIRMED", "reference": "CONFIRMED",
            "canonical_ligands": "CONFIRMED", "microstates": "CONFIRMED",
            "prep_policy": "CONFIRMED",
            "pose_review": "CONFIRMED" if pose_review is not None else "UNVERIFIED",
            "protocol": "CONFIRMED" if protocol is not None else "UNVERIFIED",
            "network": "CONFIRMED" if network is not None else "UNVERIFIED",
        },
    }


def empty_digest_bundle() -> dict[str, Any]:
    """Return a fully sealed, explicitly UNVERIFIED pre-input bundle."""
    domains = {
        name: {"stage": name, "verdict": "UNVERIFIED", "value": None}
        for name in sorted(INPUT_DOMAINS)
    }
    digests = {f"{name}_digest": sha256_digest(value)
               for name, value in domains.items()}
    digests.update({
        "receptor_policy_digest": sha256_digest(None),
        "ligand_policy_digest": sha256_digest(None),
    })
    complete = {"schema_version": SCHEMA_VERSION, **digests}
    return {
        **digests, "bundle_digest": sha256_digest(complete),
        "canonical_ligands": [], "microstates": None,
        "receptor_policy": None, "ligand_policy": None,
        "domain_verdicts": {name: "UNVERIFIED" for name in INPUT_DOMAINS},
    }


def changed_domains(previous: dict | None, current: dict) -> list[str]:
    if previous is None:
        return sorted(INPUT_DOMAINS)
    changed = []
    for domain in sorted(INPUT_DOMAINS):
        key = f"{domain}_digest"
        if previous.get(key) != current.get(key):
            changed.append(domain)
    return changed


def dependency_dag(bundle: dict, artifact_refs: dict[str, dict] | None = None) -> dict:
    refs = artifact_refs or {}
    nodes = {}
    for name, dependencies in DEPENDENCIES.items():
        digest = bundle.get(f"{name}_digest")
        reference = refs.get(name)
        if reference is not None:
            digest = reference.get("sha256") or digest
        verdict = bundle.get("domain_verdicts", {}).get(name, "UNVERIFIED")
        if reference is not None:
            verdict = "CONFIRMED"
        nodes[name] = {
            "stage": name,
            "dependencies": list(dependencies),
            "digest": require_digest(digest, f"{name}_digest") if digest else None,
            "artifact_ref": reference,
            "verdict": verdict,
            "stale": False,
            "stale_reasons": [],
        }
    return {"schema_version": "rbfe-artifact-dag.v1", "nodes": nodes,
            "dag_digest": sha256_digest({
                name: {"dependencies": node["dependencies"], "digest": node["digest"]}
                for name, node in nodes.items()
            })}


def recursively_stale(dag: dict, domains: Iterable[str], reason: str) -> dict:
    """Return a copied DAG with every transitive consumer failed closed."""
    if not str(reason or "").strip():
        raise ValueError("stale invalidation requires a reason")
    roots = set(domains)
    unknown = roots.difference(DEPENDENCIES)
    if unknown:
        raise ValueError(f"unknown changed domains: {sorted(unknown)}")
    affected = set(roots)
    while True:
        added = {
            name for name, dependencies in DEPENDENCIES.items()
            if set(dependencies).intersection(affected)
        }.difference(affected)
        if not added:
            break
        affected.update(added)
    result = deepcopy(dag)
    for name in sorted(affected):
        node = result["nodes"][name]
        node.update({"verdict": "OVERTURNED", "stale": True,
                     "stale_reasons": [str(reason)]})
    result["invalidation"] = {
        "changed_domains": sorted(roots), "stale_stages": sorted(affected),
        "reason": str(reason), "verdict": "CONFIRMED",
    }
    result["dag_digest"] = sha256_digest({
        name: {key: node[key] for key in
               ("dependencies", "digest", "verdict", "stale", "stale_reasons")}
        for name, node in result["nodes"].items()
    })
    return result


def normalize_changed_domains(domains: Iterable[str]) -> list[str]:
    """Expand public mutation domains into canonical dependency-DAG roots."""
    roots: set[str] = set()
    unknown: list[str] = []
    seen = False
    for domain in domains:
        seen = True
        name = str(domain)
        if name in DEPENDENCIES:
            roots.add(name)
        elif name in PUBLIC_DOMAIN_ROOTS:
            roots.update(PUBLIC_DOMAIN_ROOTS[name])
        else:
            unknown.append(name)
    if unknown:
        raise ValueError(f"unknown changed domains: {sorted(set(unknown))}")
    if not roots and not seen:
        raise ValueError("changed_domains must not be empty")
    return sorted(roots)


def full_ref(kind: str, identifier: str, digest: str, **fields: Any) -> dict:
    if not str(kind or "").strip() or not str(identifier or "").strip():
        raise ValueError("artifact refs require kind and id")
    return {"kind": str(kind), "id": str(identifier),
            "sha256": require_digest(digest), **fields}


def review_attestation(*, campaign_id: str, campaign_version: int,
                       reviewer: dict[str, str], reviewed_at: str | None,
                       reason: str, viewed_pose_refs: list[dict],
                       review_checks: Iterable[str]) -> dict:
    principal = require_actor(reviewer)
    if principal["kind"] != "human":
        raise ValueError(
            "pose review that unlocks RBFE execution requires a human reviewer")
    if not str(reason or "").strip():
        raise ValueError("pose review requires a non-empty reason")
    if reviewed_at is None:
        timestamp = datetime.now(timezone.utc)
    else:
        try:
            timestamp = datetime.fromisoformat(
                str(reviewed_at).replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("reviewed_at must be an ISO-8601 timestamp") from error
    if timestamp.tzinfo is None:
        raise ValueError("reviewed_at must include a timezone")
    checks = frozenset(review_checks)
    if checks != REVIEW_CHECKS:
        raise ValueError("pose review checks are incomplete")
    if len(viewed_pose_refs) < 2:
        raise ValueError("pose review requires at least two viewed poses")
    refs = [full_ref(str(ref.get("kind")), str(ref.get("id")),
                     str(ref.get("sha256"))) for ref in viewed_pose_refs]
    if len({ref["id"] for ref in refs}) != len(refs):
        raise ValueError("viewed pose refs must be distinct")
    body = {
        "schema_version": "rbfe-pose-review-attestation.v1",
        "campaign_id": require_campaign_id(campaign_id),
        "campaign_version": int(campaign_version),
        "reviewer": principal,
        "reviewed_at": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reason": str(reason).strip(),
        "viewed_pose_refs": refs,
        "review_checks": sorted(checks),
        "verdict": "CONFIRMED",
    }
    return {**body, "attestation_digest": sha256_digest(body)}


def stage_payload(stage: str, verdict: str, *, refs: Iterable[dict] = (),
                  digests: dict[str, str] | None = None,
                  error: dict | None = None,
                  recovery: dict | None = None) -> dict:
    if verdict not in VERDICTS:
        raise ValueError("stage verdict must be CONFIRMED, OVERTURNED, or UNVERIFIED")
    full_digests = {key: require_digest(value, key)
                    for key, value in (digests or {}).items()}
    if verdict == "CONFIRMED" and error is not None:
        raise ValueError("a confirmed stage cannot carry an error")
    return {
        "stage": str(stage), "verdict": verdict,
        "refs": list(refs), "digests": full_digests,
        "error": error,
        "recovery": recovery or {
            "retryable": False, "resume_from_stage": None,
            "required_actions": [],
        },
    }


def campaign_document(*, campaign_id: str, version: int, label: str,
                      actor: dict[str, str], digest_bundle: dict,
                      artifact_dag: dict, status: str = "draft",
                      imports: Iterable[dict] = (), inputs: dict | None = None,
                      stages: dict | None = None,
                      owned_object_refs: Iterable[dict] = (),
                      prior_invalidation: dict | None = None) -> dict:
    principal = require_actor(actor)
    if version < 1:
        raise ValueError("campaign version must be positive")
    if status not in {"draft", "inputs_reviewed", "prepared", "poses_reviewed",
                      "planned", "stale", "archived"}:
        raise ValueError("campaign status is not recognized")
    body = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": require_campaign_id(campaign_id),
        "version": int(version), "label": str(label).strip(),
        "status": status,
        "verdict": "OVERTURNED" if status in {"stale", "archived"}
        else "UNVERIFIED",
        "actor": principal, "digest_bundle": digest_bundle,
        "artifact_dag": artifact_dag, "imports": list(imports),
        "inputs": deepcopy(inputs) if inputs is not None else None,
        "stages": deepcopy(stages) if stages is not None else {},
        "owned_object_refs": list(owned_object_refs),
        "prior_invalidation": deepcopy(prior_invalidation),
    }
    return {**body, "state_digest": sha256_digest(body)}


def idempotency_key(campaign_id: str, version: int, bundle_digest: str,
                    operation: str) -> str:
    return sha256_digest({
        "campaign_id": require_campaign_id(campaign_id),
        "version": int(version), "operation": str(operation),
        "bundle_digest": require_digest(bundle_digest),
    })


__all__ = [
    "DEPENDENCIES", "INPUT_DOMAINS", "PUBLIC_DOMAIN_ROOTS", "REVIEW_CHECKS",
    "SCHEMA_VERSION", "VERDICTS", "campaign_document", "canonical_bytes",
    "canonical_digest_bundle", "changed_domains", "dependency_dag",
    "empty_digest_bundle", "full_ref",
    "idempotency_key", "normalize_changed_domains", "normalize_ligand_series", "recursively_stale", "require_actor",
    "require_campaign_id", "review_attestation", "sha256_digest",
    "stable_campaign_id", "stage_payload",
]
