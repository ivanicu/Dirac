"""Server-owned RBFE preflight, OpenFE evidence review, and aggregation bridge."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import failures
from invocation import HandlerResult, InvocationContext
from motif.rbfe import _digest, aggregate_rbfe_results, ingest_openfe_edge_result
from motif.rbfe_binding import (build_campaign_binding,
                                validate_campaign_binding)


_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME = _ROOT / "openfe-runtime-v2/bin/python"
_PROBE = Path(__file__).with_name("openfe_evidence_probe.py")
_PROBE_SHA256 = "sha256:" + hashlib.sha256(_PROBE.read_bytes()).hexdigest()
_SYSTEM_BUILDER = Path(__file__).with_name("openfe_system_builder.py")
_SYSTEM_BUILDER_SHA256 = "sha256:" + hashlib.sha256(
    _SYSTEM_BUILDER.read_bytes()).hexdigest()
_CONVERGENCE_POLICY = {
    "schema_version": "1.0",
    "name": "dirac.openfe-equilibrium-screen",
    "version": "1",
    "minimum_neighbor_overlap": 0.03,
    "maximum_uncertainty_kcal_mol": 1.0,
    "minimum_production_iterations": 1,
    "required_engine": "OpenFE 1.11.1",
}
_CONVERGENCE_POLICY["digest"] = _digest(_CONVERGENCE_POLICY)

_CHEMISTRY_LEDGER_DIMENSIONS = {
    "SCOPE", "ELEMENT", "CONNECTIVITY", "BOND_ORDER", "FORMAL_CHARGE",
    "STEREO", "RING_CYCLE_RANK", "UNMAPPED", "PROTONATION_TAUTOMER",
}
_CHEMISTRY_VERDICTS = {"CONFIRMED", "CHANGED", "UNVERIFIED"}
_MAPPING_ATTESTATION_FIELDS = {
    "schema_version", "source", "builder_sha256", "system_build_report_digest",
    "mapping_score", "mapped_atom_count", "mapped_heavy_atom_count",
    "selected_atom_mapping", "selected_heavy_atom_mapping", "mapping_method",
    "depiction_contract", "mapping_direction_audit", "chemistry_evidence",
    "input_pose_identity", "execution_eligibility", "ligand_state_digest",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def _verified_network_digest(network: dict) -> str:
    digest = str(network.get("digest") or "")
    observed = _digest({key: value for key, value in network.items()
                        if key != "digest"})
    if digest != observed:
        raise failures.DiracInvalidParameters(
            "RBFE network content does not match its immutable network digest")
    return digest


def _campaign_binding(network: dict, *, campaign_id: str,
                      campaign_scientific_generation: int,
                      campaign_scientific_digest: str,
                      prepared_system_id: str) -> dict:
    """Bind one network, campaign generation, and prepared system fail-closed."""
    network_digest = _verified_network_digest(network)
    if any(value is None for value in (
            campaign_id, campaign_scientific_generation,
            campaign_scientific_digest,
            prepared_system_id)):
        raise failures.DiracInvalidParameters(
            "campaign binding requires campaign id, scientific generation, "
            "scientific digest, and prepared system")
    context = network.get("campaign_context")
    if not isinstance(context, dict):
        raise failures.DiracInvalidParameters(
            "system preparation requires a campaign-bound RBFE network")
    expected = {
        "campaign_id": str(campaign_id),
        "campaign_scientific_generation": int(
            campaign_scientific_generation),
        "campaign_scientific_digest": str(campaign_scientific_digest),
        "prepared_system_id": str(prepared_system_id),
    }
    if context != expected:
        raise failures.DiracInvalidParameters(
            "RBFE network does not carry one exact Campaign scientific context",
            details={"network_campaign": context, "requested_campaign": expected})
    try:
        return build_campaign_binding(
            campaign_id=expected["campaign_id"],
            campaign_scientific_generation=expected[
                "campaign_scientific_generation"],
            campaign_scientific_digest=expected[
                "campaign_scientific_digest"],
            prepared_system_id=expected["prepared_system_id"],
            network_digest=network_digest,
        )
    except (TypeError, ValueError) as error:
        raise failures.DiracInvalidParameters(
            f"campaign binding is malformed: {error}") from error


def _mapping_attestation_digest(attestation: dict) -> str:
    return _digest({
        key: value for key, value in attestation.items()
        if key not in {"attestation_digest", "_server_seal"}
    })


def _seal_mapping_attestation(report: dict) -> dict:
    """Create a non-serializable, exact seal over one system-builder report."""
    attestation = {
        "schema_version": "rbfe-system-builder-attestation.v1",
        "source": "openfe_system_builder.reviewed_receptor_frame",
        "builder_sha256": _SYSTEM_BUILDER_SHA256,
        "system_build_report_digest": _digest(report),
        "mapping_score": report["mapping_score"],
        "mapped_atom_count": report["mapped_atom_count"],
        "mapped_heavy_atom_count": report["mapped_heavy_atom_count"],
        "selected_atom_mapping": report["selected_atom_mapping"],
        "selected_heavy_atom_mapping": report["selected_heavy_atom_mapping"],
        "mapping_method": report["mapping_method"],
        "depiction_contract": report["depiction_contract"],
        "mapping_direction_audit": report["mapping_direction_audit"],
        "chemistry_evidence": report["chemistry_evidence"],
        "input_pose_identity": report["input_pose_identity"],
        "execution_eligibility": report["execution_eligibility"],
        "ligand_state_digest": report["ligand_state_digest"],
    }
    digest = _mapping_attestation_digest(attestation)
    attestation["attestation_digest"] = digest
    # JSON/API callers cannot manufacture or replay this module-owned object.
    # The paired digest also prevents mutation after the seal is issued.
    attestation["_server_seal"] = (_validate_mapping_attestation, digest)
    return attestation


def _mapping_pairs(value: Any, label: str) -> list[tuple[int, int]]:
    if not isinstance(value, list) or not value:
        raise failures.DiracInvalidParameters(
            f"posed-system {label} must be a non-empty atom-pair list")
    pairs: list[tuple[int, int]] = []
    for row in value:
        if (not isinstance(row, (list, tuple)) or len(row) != 2
                or any(type(index) is not int or index < 0 for index in row)):
            raise failures.DiracInvalidParameters(
                f"posed-system {label} contains a malformed atom pair")
        pairs.append((row[0], row[1]))
    if (len(set(pairs)) != len(pairs)
            or len({left for left, _ in pairs}) != len(pairs)
            or len({right for _, right in pairs}) != len(pairs)):
        raise failures.DiracInvalidParameters(
            f"posed-system {label} is not one-to-one")
    return pairs


def _validate_mapping_attestation(attestation: dict) -> None:
    if not isinstance(attestation, dict):
        raise failures.DiracInvalidParameters(
            "posed-system mapping attestation must be a server-built object")
    seal = attestation.get("_server_seal")
    if (not isinstance(seal, tuple) or len(seal) != 2
            or seal[0] is not _validate_mapping_attestation):
        raise failures.DiracInvalidParameters(
            "posed-system mapping attestation is not server-sealed; inline caller "
            "evidence is forbidden")
    expected_keys = _MAPPING_ATTESTATION_FIELDS | {
        "attestation_digest", "_server_seal"}
    if set(attestation) != expected_keys:
        raise failures.DiracInvalidParameters(
            "posed-system mapping attestation fields are incomplete or unexpected")
    observed_digest = _mapping_attestation_digest(attestation)
    if (attestation.get("attestation_digest") != observed_digest
            or seal[1] != observed_digest):
        raise failures.DiracInvalidParameters(
            "posed-system mapping attestation changed after the system builder "
            "sealed it")
    if (attestation.get("schema_version")
            != "rbfe-system-builder-attestation.v1"
            or attestation.get("source")
            != "openfe_system_builder.reviewed_receptor_frame"
            or attestation.get("builder_sha256") != _SYSTEM_BUILDER_SHA256
            or not str(attestation.get("system_build_report_digest") or "").startswith(
                "sha256:")):
        raise failures.DiracInvalidParameters(
            "posed-system mapping attestation has no exact system-builder provenance")

    score = float(attestation.get("mapping_score", 0.0))
    if not math.isfinite(score):
        raise failures.DiracInvalidParameters(
            "posed-system mapping score is not finite")
    atom_mapping = _mapping_pairs(
        attestation.get("selected_atom_mapping"), "atom mapping")
    heavy_mapping = _mapping_pairs(
        attestation.get("selected_heavy_atom_mapping"), "heavy-atom mapping")
    if (score < 0.8
            or int(attestation.get("mapped_atom_count", 0)) != len(atom_mapping)
            or int(attestation.get("mapped_heavy_atom_count", 0)) != len(heavy_mapping)
            or not set(heavy_mapping).issubset(atom_mapping)):
        raise failures.DiracInvalidParameters(
            "posed-system mapping attestation did not pass the governed execution gate")
    direction = attestation.get("mapping_direction_audit") or {}
    if direction.get("verdict") != "CONFIRMED":
        raise failures.DiracInvalidParameters(
            "posed-system A-to-B/B-to-A mapping direction is not confirmed")
    depiction = attestation.get("depiction_contract") or {}
    if (depiction.get("schema_version") != "rbfe-depiction-index.v2"
            or not depiction.get("parent_smiles")
            or not depiction.get("proposal_smiles")
            or depiction.get("chemistry_evidence")
            != attestation.get("chemistry_evidence")):
        raise failures.DiracInvalidParameters(
            "posed-system depiction contract is absent or unsupported")
    chemistry = attestation.get("chemistry_evidence") or {}
    ledger = chemistry.get("ledger") or []
    dimensions = {row.get("dimension") for row in ledger
                  if isinstance(row, dict)}
    verdicts = {row.get("verdict") for row in ledger
                if isinstance(row, dict)}
    if (chemistry.get("schema_version") != "rbfe-chemistry-change.v1"
            or dimensions != _CHEMISTRY_LEDGER_DIMENSIONS
            or not verdicts.issubset(_CHEMISTRY_VERDICTS)
            or any(not isinstance(row.get("witnesses"), list)
                   or not isinstance(row.get("summary"), str)
                   for row in ledger if isinstance(row, dict))
            or int(chemistry.get("mapped_heavy_atom_count", -1))
            != len(heavy_mapping)
            or _mapping_pairs(chemistry.get("selected_heavy_atom_mapping"),
                              "chemistry heavy-atom mapping") != heavy_mapping):
        raise failures.DiracInvalidParameters(
            "posed-system chemistry change ledger is incomplete or malformed")
    expected_chemistry_verdict = (
        "UNVERIFIED" if "UNVERIFIED" in verdicts else
        ("CHANGED" if "CHANGED" in verdicts else "CONFIRMED"))
    if (chemistry.get("verdict") != expected_chemistry_verdict
            or expected_chemistry_verdict == "UNVERIFIED"):
        raise failures.DiracInvalidParameters(
            "posed-system chemistry evidence is UNVERIFIED or contradicts its ledger")
    identity = attestation.get("input_pose_identity") or {}
    if any((identity.get(side) or {}).get("verdict") != "CONFIRMED"
           for side in ("parent", "proposal")):
        raise failures.DiracInvalidParameters(
            "input-to-3D chemical identity is not confirmed for both endpoints")
    eligibility = attestation.get("execution_eligibility") or {}
    minimum_score = float(eligibility.get("minimum_mapping_score", 0.8))
    required_flags = (
        "requires_nonzero_heavy_map", "requires_direction_audit",
        "requires_resolved_stereochemistry", "requires_charge_conservation",
    )
    if (eligibility.get("verdict") != "CONFIRMED"
            or eligibility.get("reasons") != []
            or any(eligibility.get(flag) is not True for flag in required_flags)
            or score < minimum_score):
        raise failures.DiracInvalidParameters(
            "posed system is not eligible for executable RBFE")


def _assert_current_campaign_binding(edge_spec: dict, edge_network: dict,
                                     ctx: InvocationContext) -> dict:
    binding = edge_spec.get("campaign_binding")
    try:
        binding = validate_campaign_binding(binding)
    except (TypeError, ValueError) as error:
        raise failures.DiracInvalidParameters(
            f"rbfe.edge_spec campaign binding is invalid: {error}") from error
    if edge_network.get("campaign_binding") != binding:
        raise failures.DiracInvalidParameters(
            "edge network and edge specification do not share one campaign binding")
    if edge_network.get("parent_network_digest") != binding.get("network_digest"):
        raise failures.DiracInvalidParameters(
            "campaign binding does not name the parent network digest")
    resolver = getattr(ctx, "rbfe_reference_resolver", None)
    if resolver is None:
        raise failures.DiracUnsupported(
            "versioned campaign resolution is unavailable")
    resolver.assert_campaign_generation(
        binding.get("campaign_id"),
        binding.get("campaign_scientific_generation"),
        binding.get("campaign_scientific_digest"), ctx.actor)
    return binding


def _replicate_seed(edge_id: str, leg: str, repeat_index: int) -> int:
    """Stable orchestration seed; each physical leg/repeat gets its own identity."""
    raw = hashlib.sha256(f"{edge_id}:{leg}:{repeat_index}".encode()).digest()
    return int.from_bytes(raw[:4], "big") or 1


def _artifact_ref(artifact: Any) -> dict[str, str]:
    return {"kind": "artifact", "id": str(artifact.id),
            "sha256": "sha256:" + artifact.sha256}


def _read_ref(ctx: InvocationContext, reference: dict, role: str) -> tuple[Any, bytes]:
    if ctx.artifact_reader is None:
        raise failures.DiracUnsupported(
            "server-owned network artifact and evidence reference resolution is unavailable")
    artifact, data = ctx.artifact_reader.read(reference["id"])
    declared = reference["sha256"]
    if declared != "sha256:" + artifact.sha256:
        raise failures.DiracInvalidParameters(
            "artifact reference digest does not match the server-owned artifact",
            details={"artifact_id": artifact.id, "expected": "sha256:" + artifact.sha256,
                     "received": declared})
    if artifact.role != role:
        raise failures.DiracInvalidParameters(
            f"artifact {artifact.id} has role {artifact.role!r}, expected {role!r}")
    return artifact, data


def _read_json_ref(ctx: InvocationContext, reference: dict, role: str) -> tuple[Any, dict]:
    artifact, data = _read_ref(ctx, reference, role)
    try:
        document = json.loads(data)
    except json.JSONDecodeError as error:
        raise failures.DiracInvalidParameters(
            f"artifact {artifact.id} is not valid JSON") from error
    if not isinstance(document, dict):
        raise failures.DiracInvalidParameters(f"artifact {artifact.id} is not a JSON object")
    return artifact, document


def _probe(mode: str, data: bytes) -> dict:
    if not _RUNTIME.is_file():
        raise failures.DiracUnsupported("pinned OpenFE 1.11.1 runtime is unavailable")
    with tempfile.TemporaryDirectory(prefix="dirac-rbfe-probe-") as temporary:
        source = Path(temporary) / "input.json"
        target = Path(temporary) / "output.json"
        source.write_bytes(data)
        completed = subprocess.run(
            [str(_RUNTIME), str(_PROBE), mode, str(source), str(target)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False, timeout=120)
        if completed.returncode != 0 or not target.is_file():
            raise failures.DiracInvalidParameters(
                f"OpenFE {mode} probe rejected the supplied evidence",
                details={"stderr_tail": completed.stderr[-3000:]})
        return json.loads(target.read_text())


def _build_system(document: dict) -> dict:
    """Compile user-facing scientific sources into internal GUFE Transformations."""
    if not _RUNTIME.is_file():
        raise failures.DiracUnsupported("pinned OpenFE 1.11.1 runtime is unavailable")
    observed_builder_sha256 = "sha256:" + hashlib.sha256(
        _SYSTEM_BUILDER.read_bytes()).hexdigest()
    if observed_builder_sha256 != _SYSTEM_BUILDER_SHA256:
        raise failures.DiracInvalidParameters(
            "OpenFE system builder changed after the RBFE method version was loaded")
    with tempfile.TemporaryDirectory(prefix="dirac-rbfe-system-") as temporary:
        source = Path(temporary) / "input.json"
        target = Path(temporary) / "output.json"
        source.write_bytes(_canonical(document))
        completed = subprocess.run(
            [str(_RUNTIME), str(_SYSTEM_BUILDER), str(source), str(target)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False, timeout=180)
        output = None
        if target.is_file():
            try:
                output = json.loads(target.read_text())
            except json.JSONDecodeError:
                output = None
        if completed.returncode != 0:
            error = output.get("error") if isinstance(output, dict) else None
            reason = error.get("message") if isinstance(error, dict) else None
            if isinstance(reason, str) and reason.strip():
                raise failures.DiracInvalidParameters(
                    reason.strip(),
                    details={"stage": "openfe_system_builder",
                             "source_problem": reason.strip()},
                    user_message=reason.strip())
            raise failures.DiracInvalidParameters(
                "OpenFE could not prepare the selected protein/pose system",
                details={"stage": "openfe_system_builder"})
        if not isinstance(output, dict):
            raise failures.DiracInvalidParameters(
                "OpenFE system builder returned no readable result",
                details={"stage": "openfe_system_builder"})
        return output


def prepare_rbfe_system(payload: dict, ctx: InvocationContext) -> HandlerResult:
    """Build both legs from server-resolved receptor and pose artifacts."""
    ctx.check_budget()
    _, network = _read_json_ref(ctx, payload["network_ref"], "rbfe.network")
    for field in ("campaign_id", "campaign_scientific_generation",
                  "campaign_scientific_digest"):
        if payload.get(field) is None:
            raise failures.DiracInvalidParameters(
                f"{field} is required for campaign-bound RBFE system preparation")
    prepared_system_id = str(payload["prepared_receptor_state_ref"]["id"])
    binding = _campaign_binding(
        network, campaign_id=str(payload["campaign_id"]),
        campaign_scientific_generation=int(
            payload["campaign_scientific_generation"]),
        campaign_scientific_digest=str(payload["campaign_scientific_digest"]),
        prepared_system_id=prepared_system_id)
    edge = next((row for row in network.get("edges", [])
                 if row.get("edge_id") == payload["edge_id"]), None)
    if edge is None:
        raise failures.DiracInvalidParameters(
            "selected edge is not present in the network artifact")
    compounds = {row["id"]: row for row in network.get("compounds", [])}
    try:
        parent_smiles = compounds[edge["left_id"]]["canonical_smiles"]
        proposal_smiles = compounds[edge["right_id"]]["canonical_smiles"]
    except KeyError as error:
        raise failures.DiracInvalidParameters(
            "selected edge endpoints are absent from the network artifact") from error
    if ctx.rbfe_reference_resolver is None:
        raise failures.DiracUnsupported(
            "registered target/protein-pose resolution is unavailable")
    resolved = ctx.rbfe_reference_resolver.resolve_prepared_system(
        payload["prepared_receptor_state_ref"],
        payload["parent_pose_ref"],
        payload["proposal_pose_ref"],
        campaign_id=binding["campaign_id"],
        scientific_generation=binding["campaign_scientific_generation"],
        scientific_digest=binding["campaign_scientific_digest"],
        actor=ctx.actor)
    campaign_scientific_ref = resolved.get("campaign_scientific_ref") or {}
    if ({"id": campaign_scientific_ref.get("id"),
         "version": campaign_scientific_ref.get("version"),
         "sha256": campaign_scientific_ref.get("sha256")} != {
             "id": binding["campaign_id"],
             "version": binding["campaign_scientific_generation"],
             "sha256": binding["campaign_scientific_digest"],
         }):
        raise failures.DiracInvalidParameters(
            "resolved prepared system is not attested to the requested campaign generation")
    if (resolved["parent_canonical_smiles"] != parent_smiles
            or resolved["proposal_canonical_smiles"] != proposal_smiles):
        raise failures.DiracInvalidParameters(
            "registered endpoint poses do not match the selected network edge",
            details={
                "selected_edge": [parent_smiles, proposal_smiles],
                "registered_poses": [resolved["parent_canonical_smiles"],
                                     resolved["proposal_canonical_smiles"]],
            })
    prepared = _build_system({
        "protocol_preset": payload["protocol_preset"],
        "receptor_pdb": resolved["receptor_pdb"],
        "parent_sdf": resolved["parent_sdf"],
        "proposal_sdf": resolved["proposal_sdf"],
        "expected_parent_smiles": parent_smiles,
        "expected_proposal_smiles": proposal_smiles,
        "expected_receptor_sha256": resolved["expected_receptor_sha256"],
        "source_pdb_id": resolved["source_pdb_id"],
        "parent_id": edge["left_id"],
        "proposal_id": edge["right_id"],
        "campaign_contract": binding,
    })
    report = prepared["build_report"]
    if report.get("campaign_contract") != binding:
        raise failures.DiracInvalidParameters(
            "OpenFE system build did not preserve the campaign binding")
    mapping_score = float(report.get("mapping_score", 0.0))
    if mapping_score < 0.8:
        raise failures.DiracInvalidParameters(
            "the reviewed receptor-frame transformation did not pass the governed "
            f"LoMap qualification gate ({mapping_score:.3f} < 0.800)",
            details={"stage": "posed_system_mapping_gate",
                     "mapping_score": mapping_score,
                     "required_mapping_score": 0.8},
            user_message=(
                "This transformation is not safe to run as an RBFE edge. "
                f"The receptor-frame LoMap score is {mapping_score:.3f}; "
                "Dirac requires at least 0.800."))
    cycle_key = ":".join((
        "dirac-rbfe-cycle-v1", payload["edge_id"],
        resolved["target_ref"]["id"], resolved["protein_structure_ref"]["id"],
        report["receptor_pdb_sha256"], report["ligand_state_digest"],
        payload["protocol_preset"], binding["digest"],
    ))
    result = preflight_rbfe_edge({
        "network_ref": payload["network_ref"],
        "edge_id": payload["edge_id"],
        "complex_transformation": prepared["complex_transformation"],
        "solvent_transformation": prepared["solvent_transformation"],
        "target_ref": resolved["target_ref"],
        "protein_structure_ref": resolved["protein_structure_ref"],
        "thermodynamic_cycle_id": str(uuid5(NAMESPACE_URL, cycle_key)),
        "ligand_charge_digest": report["ligand_state_digest"],
        "mapping_attestation": _seal_mapping_attestation(report),
        "campaign_binding": binding,
    }, ctx)
    result.result["system_build"] = report
    result.artifacts.append(("rbfe.system_build_report", _canonical(report)))
    result.provenance.update({
        "system_builder": "OpenFE ProteinComponent + LoMap + RFE protocol",
        "system_builder_sha256": _SYSTEM_BUILDER_SHA256,
        "server_resolved_sources": {
            "prepared_receptor_state_ref": resolved["prepared_receptor_state_ref"],
            "parent_pose_ref": resolved["parent_pose_ref"],
            "proposal_pose_ref": resolved["proposal_pose_ref"],
        },
    })
    return result


def preflight_rbfe_edge(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    network_artifact, network = _read_json_ref(ctx, payload["network_ref"], "rbfe.network")
    _verified_network_digest(network)
    edge = next((row for row in network.get("edges", [])
                 if row.get("edge_id") == payload["edge_id"]), None)
    if edge is None:
        raise failures.DiracInvalidParameters(
            "selected edge is not present in the network artifact")
    if edge.get("status") == "rejected":
        raise failures.DiracInvalidParameters(
            "selected edge was rejected by network chemistry review")
    if not network.get("campaign_context"):
        raise failures.DiracInvalidParameters(
            "physical RBFE edge preflight refuses legacy unbound networks")
    try:
        campaign_binding = validate_campaign_binding(
            payload.get("campaign_binding"))
    except (TypeError, ValueError) as error:
        raise failures.DiracInvalidParameters(
            f"physical RBFE edge preflight requires one exact campaign binding: "
            f"{error}") from error
    expected_binding = _campaign_binding(
        network,
        campaign_id=campaign_binding.get("campaign_id"),
        campaign_scientific_generation=campaign_binding.get(
            "campaign_scientific_generation"),
        campaign_scientific_digest=campaign_binding.get(
            "campaign_scientific_digest"),
        prepared_system_id=campaign_binding.get("prepared_system_id"))
    if campaign_binding != expected_binding:
        raise failures.DiracInvalidParameters(
            "campaign binding content or digest does not match the network")
    mapping_attestation = payload.get("mapping_attestation")
    if not isinstance(mapping_attestation, dict):
        raise failures.DiracInvalidParameters(
            "physical RBFE preflight requires a server-built pose/mapping attestation; "
            "use physics.rbfe-system.prepare instead of submitting raw Transformations")
    _validate_mapping_attestation(mapping_attestation)
    score = float(mapping_attestation["mapping_score"])
    atom_mapping = mapping_attestation["selected_atom_mapping"]
    heavy_mapping = mapping_attestation["selected_heavy_atom_mapping"]
    edge = {
        **edge,
        "preliminary_mapping_score": edge.get("mapping_score"),
        "mapping_score": score,
        "mapped_atom_count": int(mapping_attestation["mapped_atom_count"]),
        "mapped_heavy_atom_count": int(
            mapping_attestation["mapped_heavy_atom_count"]),
        "selected_atom_mapping": atom_mapping,
        "selected_heavy_atom_mapping": heavy_mapping,
        "mapping_source": mapping_attestation["source"],
        "mapping_method": mapping_attestation["mapping_method"],
        "depiction_contract": mapping_attestation["depiction_contract"],
        "mapping_direction_audit": mapping_attestation[
            "mapping_direction_audit"],
        "chemistry_evidence": mapping_attestation["chemistry_evidence"],
        "input_pose_identity": mapping_attestation["input_pose_identity"],
        "execution_eligibility": mapping_attestation[
            "execution_eligibility"],
        "ligand_state_digest": mapping_attestation["ligand_state_digest"],
        "execution_gate": "passed",
    }
    compound_by_id = {row["id"]: row for row in network.get("compounds", [])}
    if edge["left_id"] not in compound_by_id or edge["right_id"] not in compound_by_id:
        raise failures.DiracInvalidParameters(
            "selected edge endpoints are absent from the network artifact")
    if ctx.rbfe_reference_resolver is None:
        raise failures.DiracUnsupported("registered target/protein-pose resolution is unavailable")
    resolved = ctx.rbfe_reference_resolver.resolve(
        payload["target_ref"]["id"], payload["protein_structure_ref"]["id"])
    transformations = {
        "complex_transformation": payload["complex_transformation"],
        "solvent_transformation": payload["solvent_transformation"],
    }
    validation = _probe("validate", _canonical(transformations))
    complex_types = set(validation["complex"]["component_types"])
    solvent_types = set(validation["solvent"]["component_types"])
    if "ProteinComponent" not in complex_types or "SolventComponent" not in complex_types:
        raise failures.DiracInvalidParameters(
            "complex Transformation must contain ProteinComponent and SolventComponent")
    if "ProteinComponent" in solvent_types or "SolventComponent" not in solvent_types:
        raise failures.DiracInvalidParameters(
            "solvent Transformation must contain SolventComponent and no ProteinComponent")
    complex_digest = _digest(payload["complex_transformation"])
    solvent_digest = _digest(payload["solvent_transformation"])
    subnetwork = {
        "schema_version": "1.0", "kind": "rbfe_edge_network",
        "parent_network_digest": network["digest"],
        "compounds": [compound_by_id[edge["left_id"]], compound_by_id[edge["right_id"]]],
        "edges": [edge], "mode": "pilot",
        "policy": network.get("policy", {}),
        "campaign_binding": campaign_binding,
        "claim_boundary": "One selected edge only; no free energy result is implied.",
    }
    subnetwork["digest"] = _digest(subnetwork)
    spec = {
        "schema_version": "1.0", "kind": "rbfe_edge_execution_spec",
        "network_artifact_ref": _artifact_ref(network_artifact),
        "edge_network_digest": subnetwork["digest"],
        "edge_id": edge["edge_id"], "left_id": edge["left_id"],
        "right_id": edge["right_id"], "target_ref": payload["target_ref"],
        "protein_structure_ref": payload["protein_structure_ref"],
        "resolved_reference": resolved,
        "thermodynamic_cycle_id": payload["thermodynamic_cycle_id"],
        "ligand_charge_digest": payload["ligand_charge_digest"],
        "campaign_binding": campaign_binding,
        "complex_transformation_digest": complex_digest,
        "solvent_transformation_digest": solvent_digest,
        "openfe_validation": validation,
        "execution_matrix": [{"leg": leg, "repeat_index": repeat,
                              "orchestration_seed": _replicate_seed(
                                  edge["edge_id"], leg, repeat)}
                             for repeat in range(1, 4)
                             for leg in ("complex", "solvent")],
        "scientific_status": "server_preflight_passed",
    }
    spec["digest"] = _digest(spec)
    return HandlerResult(
        result={"spec_digest": spec["digest"], "edge_network_digest": subnetwork["digest"],
                "edge_id": edge["edge_id"], "left_id": edge["left_id"],
                "right_id": edge["right_id"],
                "complex_transformation_digest": complex_digest,
                "solvent_transformation_digest": solvent_digest,
                "execution_matrix": spec["execution_matrix"],
                "execution_count": 6, "validation_status": "server_preflight_passed",
                "resolved_target": resolved,
                "campaign_binding": campaign_binding},
        artifacts=[("rbfe.edge_spec", _canonical(spec)),
                   ("rbfe.edge_network", _canonical(subnetwork)),
                   ("rbfe.openfe.complex_transformation",
                    _canonical(payload["complex_transformation"])),
                   ("rbfe.openfe.solvent_transformation",
                    _canonical(payload["solvent_transformation"]))],
        provenance={"validator": "OpenFE Transformation.from_json",
                    "engine": "OpenFE", "engine_version": validation["engine_version"]})


def _convergence(report: dict, diagnostics: dict) -> dict:
    reasons: list[str] = []
    uncertainty = diagnostics.get("uncertainty")
    unit = str(diagnostics.get("unit") or "").replace(" ", "").lower()
    unit = unit.replace("kilocalorie_per_mole", "kilocalorie/mole")
    unit = unit.replace("kilojoule_per_mole", "kilojoule/mole")
    factors = {"kcal/mol": 1.0, "kilocalorie/mole": 1.0,
               "kj/mol": 1.0 / 4.184, "kilojoule/mole": 1.0 / 4.184}
    uncertainty_kcal = (float(uncertainty) * factors[unit]
                        if uncertainty is not None and unit in factors else None)
    overlap = diagnostics.get("minimum_neighbor_overlap")
    iterations = diagnostics.get("production_iterations")
    if uncertainty_kcal is None or not math.isfinite(uncertainty_kcal) or uncertainty_kcal <= 0:
        reasons.append("uncertainty unavailable or non-positive")
    elif uncertainty_kcal > _CONVERGENCE_POLICY["maximum_uncertainty_kcal_mol"]:
        reasons.append("uncertainty exceeds policy")
    if overlap is None:
        reasons.append("MBAR adjacent-window overlap unavailable")
    elif float(overlap) < _CONVERGENCE_POLICY["minimum_neighbor_overlap"]:
        reasons.append("MBAR adjacent-window overlap below policy")
    if iterations is None:
        reasons.append("production iteration count unavailable")
    elif int(iterations) < _CONVERGENCE_POLICY["minimum_production_iterations"]:
        reasons.append("no production iterations")
    verdict = "passed" if not reasons else "unverified"
    document = {
        "schema_version": "1.0", "kind": "rbfe_convergence_verdict",
        "edge_id": report["edge_id"], "leg": report["leg"],
        "repeat_index": report["repeat_index"], "verdict": verdict,
        "reasons": reasons, "policy_digest": _CONVERGENCE_POLICY["digest"],
        "minimum_overlap": overlap,
        "production_iterations": iterations,
        "equilibration_iterations": diagnostics.get("equilibration_iterations"),
        "uncertainty": uncertainty, "unit": diagnostics.get("unit"),
        "uncertainty_kcal_mol": uncertainty_kcal,
    }
    document["diagnostics_digest"] = _digest(document)
    return document


def aggregate_rbfe_evidence(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    _, network = _read_json_ref(ctx, payload["network_ref"], "rbfe.edge_network")
    _, execution_spec = _read_json_ref(ctx, payload["edge_spec_ref"], "rbfe.edge_spec")
    _verified_network_digest(network)
    spec_digest = execution_spec.get("digest")
    if (spec_digest != _digest({key: value for key, value in execution_spec.items()
                                if key != "digest"})
            or execution_spec.get("edge_network_digest") != network.get("digest")):
        raise failures.DiracInvalidParameters(
            "aggregation spec does not bind the supplied edge network")
    campaign_binding = _assert_current_campaign_binding(
        execution_spec, network, ctx)
    expected_matrix = {
        (str(row["leg"]), int(row["repeat_index"])): int(row["orchestration_seed"])
        for row in execution_spec.get("execution_matrix", [])}
    observations, verdicts = [], []
    identities: set[tuple[str, int]] = set()
    work_identities: set[str] = set()
    orchestration_seeds: set[int] = set()
    for run in payload["runs"]:
        _, report = _read_json_ref(ctx, run["run_report_ref"], "rbfe.openfe.run_report")
        result_artifact, result_bytes = _read_ref(ctx, run["result_ref"], "rbfe.openfe.result")
        identity = (str(report.get("leg")), int(report.get("repeat_index", 0)))
        if identity in identities:
            raise failures.DiracInvalidParameters(f"duplicate OpenFE evidence for {identity}")
        identities.add(identity)
        if report.get("edge_id") != network["edges"][0]["edge_id"]:
            raise failures.DiracInvalidParameters("OpenFE result edge does not match edge network")
        if report.get("edge_spec_digest") != spec_digest:
            raise failures.DiracInvalidParameters(
                "OpenFE run report was authorized by a different edge spec")
        expected_seed = expected_matrix.get(identity)
        seed = int(report.get("orchestration_seed", 0))
        if not expected_seed or seed != expected_seed:
            raise failures.DiracInvalidParameters(
                f"OpenFE run report seed is not authorized for {identity}")
        work_identity = str(report.get("work_identity") or "")
        if not work_identity or work_identity in work_identities or seed in orchestration_seeds:
            raise failures.DiracInvalidParameters(
                "OpenFE repeats do not carry unique governed work identities")
        work_identities.add(work_identity)
        orchestration_seeds.add(seed)
        result_sha = "sha256:" + hashlib.sha256(result_bytes).hexdigest()
        if report.get("result_sha256") != result_sha:
            raise failures.DiracInvalidParameters(
                "run report does not bind the supplied result artifact")
        diagnostics = _probe("diagnose", result_bytes)
        verdict = _convergence(report, diagnostics)
        verdict["source_result_artifact_ref"] = _artifact_ref(result_artifact)
        verdicts.append(verdict)
        if verdict["verdict"] != "passed":
            continue
        normalized = ingest_openfe_edge_result({
            "edge_id": report["edge_id"], "leg": report["leg"],
            "repeat_index": report["repeat_index"], "target_ref": report["target_ref"],
            "protein_structure_ref": report.get("protein_structure_ref"),
            "thermodynamic_cycle_id": report["thermodynamic_cycle_id"],
            "ligand_charge_digest": report["ligand_charge_digest"],
            "transformation_digest": report["transformation_digest"],
            "result_digest": result_sha, "engine": "OpenFE",
            "scientific_status": "completed_unvalidated",
            "estimate": diagnostics["estimate"], "uncertainty": diagnostics["uncertainty"],
            "unit": diagnostics["unit"],
        }, verdict)
        observations.append(normalized)
    required = {(leg, repeat) for repeat in range(1, 4)
                for leg in ("complex", "solvent")}
    if identities != required:
        raise failures.DiracInvalidParameters(
            "aggregation requires exactly complex+solvent for repeats 1, 2, and 3",
            details={"received": sorted(f"{leg}:{repeat}" for leg, repeat in identities)})
    if set(expected_matrix) != required:
        raise failures.DiracInvalidParameters(
            "rbfe.edge_spec does not authorize exactly the required six-run matrix")
    result = aggregate_rbfe_results(network, observations)
    result["server_evidence_resolution"] = "artifact_id+sha256+role verified"
    result["campaign_binding"] = campaign_binding
    result["convergence_policy"] = _CONVERGENCE_POLICY
    result["claim_boundary"] = (
        "Server-resolved physical OpenFE runs with an explicit convergence screen. "
        "The result remains unreleased until an external campaign release policy approves it.")
    result["digest"] = _digest({key: value for key, value in result.items() if key != "digest"})
    verdict_bundle = {"schema_version": "1.0", "policy": _CONVERGENCE_POLICY,
                      "verdicts": verdicts, "digest": _digest(verdicts)}
    legs_bundle = {"schema_version": "1.0", "legs": observations,
                   "digest": _digest(observations)}
    return HandlerResult(
        result={"result_digest": result["digest"], "status": result["status"],
                "release_eligible": False, "node_estimates": result["node_estimates"],
                "failed_edges": result["failed_edges"],
                "cycle_closure": result["cycle_closure"],
                "passed_leg_count": len(observations),
                "convergence_verdicts": verdicts},
        artifacts=[("rbfe.result", _canonical(result)),
                   ("rbfe.convergence", _canonical(verdict_bundle)),
                   ("rbfe.validated_legs", _canonical(legs_bundle))],
        provenance={"resolver": "PostgresArtifactStore.read",
                    "convergence_policy_digest": _CONVERGENCE_POLICY["digest"],
                    "campaign_binding_digest": campaign_binding["digest"]},
        warnings=[] if result["status"] == "computed_unattested" else [{
            "code": "RBFE_EVIDENCE_INCOMPLETE",
            "message": "One or more OpenFE legs did not pass the convergence screen."}])


__all__ = ["_PROBE_SHA256", "aggregate_rbfe_evidence", "preflight_rbfe_edge",
           "prepare_rbfe_system"]
