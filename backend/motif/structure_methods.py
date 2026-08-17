"""Dirac Method handlers for Motif structure and physics evidence."""
from __future__ import annotations

import json

import failures
from invocation import HandlerResult, InvocationContext
from motif.docking import dock_vina
from motif.physics import run_openmm_md
from motif.rbfe import _digest, plan_rbfe_network
from motif.rbfe_pipeline import aggregate_rbfe_evidence, prepare_rbfe_system
from motif.structure import generate_conformers


_RBFE_NETWORK_CAMPAIGN_KEYS = (
    "campaign_id", "campaign_scientific_generation",
    "campaign_scientific_digest", "prepared_system_id",
)
_RBFE_NETWORK_ATTESTATION_KEY = "rbfe_network_campaign"


def _json(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def conformer_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        report, sdf = generate_conformers(
            payload["smiles"], count=payload.get("count", 50),
            seed=payload.get("seed", 0),
            prune_rms_thresh=payload.get("prune_rms_thresh", .5),
            max_attempts=payload.get("max_attempts", 1000),
            minimize=payload.get("minimize", True))
    except (ValueError, RuntimeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    return HandlerResult(
        result={"ensemble_digest": report["digest"],
                "generated_count": report["generated_count"],
                "cluster_count": report["cluster_count"],
                "force_field": report["force_field"]},
        artifacts=[("structure.conformers_sdf", sdf),
                   ("structure.conformer_report", _json(report))],
        provenance={"algorithm": "RDKit ETKDGv3 + MMFF94s/UFF + Butina",
                    "seed": payload.get("seed", 0)})


def conformer_estimate(payload: dict) -> dict:
    count = int(payload.get("count", 50))
    return {"available": True, "resource_class": "cpu",
            "estimated_seconds": max(.2, count / 20),
            "estimated_peak_memory_bytes": max(256 << 20, count * (2 << 20))}


def vina_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        report, poses = dock_vina(
            payload["receptor_pdbqt"], payload["ligands"],
            center=payload["center"], box_size=payload["box_size"],
            seed=payload.get("seed", 0),
            exhaustiveness=payload.get("exhaustiveness", 16),
            n_poses=payload.get("n_poses", 9),
            energy_range=payload.get("energy_range", 3.0),
            cpu=payload.get("cpu", 1))
    except (ValueError, RuntimeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    return HandlerResult(
        result={"docking_digest": report["digest"],
                "ligand_count": len(report["results"]),
                "results": report["results"]},
        artifacts=[("structure.poses_pdbqt", poses),
                   ("structure.docking_report", _json(report))],
        provenance={"algorithm": "AutoDock Vina", "receptor_pdbqt_sha256":
                    report["receptor_pdbqt_sha256"]},
        warnings=[{"code": "POSE_HYPOTHESIS_ONLY",
                   "message": report["claim_boundary"]}])


def vina_estimate(payload: dict) -> dict:
    count = len(payload.get("ligands", ()))
    exhaustiveness = int(payload.get("exhaustiveness", 16))
    return {"available": True, "resource_class": "cpu",
            "estimated_seconds": max(1, count * exhaustiveness / 2)}


def openmm_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        report, artifacts = run_openmm_md(**payload)
    except (ValueError, RuntimeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    return HandlerResult(
        result={"run_digest": report["digest"], "platform": report["platform"],
                "resumed": report["resumed"],
                "observables": report["observables"]},
        artifacts=[*(artifacts.items()), ("md.run_report", _json(report))],
        provenance={"engine": "OpenMM", "openmm_version":
                    report["openmm_version"], "system_sha256": report["system_sha256"]},
        warnings=[{"code": "SAMPLING_NOT_VALIDATED",
                   "message": report["claim_boundary"]}])


def openmm_estimate(payload: dict) -> dict:
    steps = int(payload.get("steps", 0))
    return {"available": True, "resource_class": "gpu",
            "estimated_seconds": max(.5, steps / 10000),
            "checkpointable": True}


def attest_rbfe_network_admission(payload: dict,
                                  ctx: InvocationContext) -> dict:
    """Bind a network plan to the owner's current prepared receptor before cache.

    An unbound request is an explicit smoke plan. A bound request is admitted only
    when the server resolver confirms the exact Campaign science pair and the named
    prepared receptor is one of that Campaign's current, non-stale owned objects.
    The resolver never crosses the worker boundary; only this JSON witness does.
    """
    campaign_context = {
        key: payload[key]
        for key in _RBFE_NETWORK_CAMPAIGN_KEYS
        if payload.get(key) is not None
    }
    required = set(_RBFE_NETWORK_CAMPAIGN_KEYS)
    if campaign_context and set(campaign_context) != required:
        missing = sorted(required - set(campaign_context))
        raise failures.DiracInvalidParameters(
            "campaign-bound network planning requires the complete immutable "
            f"campaign reference; missing {missing}")
    actor = dict(ctx.actor or {})
    if not campaign_context:
        return {
            "schema_version": "rbfe-network-admission.v1",
            "verdict": "UNBOUND",
            "scope": "smoke_plan",
            "campaign_bound": False,
            "actor": actor,
        }

    resolver = getattr(ctx, "rbfe_reference_resolver", None)
    if resolver is None:
        raise failures.DiracUnsupported(
            "versioned Campaign admission is unavailable on the API service")
    campaign = resolver.assert_campaign_generation(
        campaign_context["campaign_id"],
        campaign_context["campaign_scientific_generation"],
        campaign_context["campaign_scientific_digest"], actor)
    if not isinstance(campaign, dict):
        raise failures.DiracInternal(
            "Campaign resolver returned a non-object generation witness")
    expected_campaign = (
        campaign.get("campaign_id") == campaign_context["campaign_id"]
        and campaign.get("campaign_scientific_generation")
        == campaign_context["campaign_scientific_generation"]
        and campaign.get("campaign_scientific_digest")
        == campaign_context["campaign_scientific_digest"]
        and campaign.get("verdict") == "CONFIRMED"
    )
    if not expected_campaign:
        raise failures.DiracInternal(
            "Campaign resolver did not attest the requested scientific generation")
    state = campaign.get("state")
    refs = state.get("owned_object_refs") if isinstance(state, dict) else None
    if not isinstance(refs, list):
        raise failures.DiracInternal(
            "Campaign resolver omitted the current owned-object inventory")
    matching = [
        ref for ref in refs
        if isinstance(ref, dict)
        and ref.get("kind") == "prepared_receptor_state"
        and ref.get("id") == campaign_context["prepared_system_id"]
    ]
    if not matching:
        raise failures.DiracInvalidParameters(
            "prepared_system_id is not a current prepared receptor owned by "
            "the admitted Campaign")
    if len(matching) != 1:
        raise failures.DiracInternal(
            "Campaign owns duplicate prepared receptor references")
    prepared_ref = matching[0]
    stale = (
        prepared_ref.get("stale") not in (None, False)
        or prepared_ref.get("invalidated_at") not in (None, "")
        or prepared_ref.get("verdict") in {"OVERTURNED", "STALE"}
        or prepared_ref.get("scientific_state") in {"stale", "invalidated"}
        or prepared_ref.get("claim_eligibility") == "ineligible_stale"
    )
    digest = prepared_ref.get("sha256")
    if stale:
        raise failures.DiracInvalidParameters(
            "prepared_system_id names a stale prepared receptor")
    if (not isinstance(digest, str) or len(digest) != 71
            or not digest.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in digest[7:])):
        raise failures.DiracInternal(
            "Campaign prepared receptor reference has a malformed digest")
    return {
        "schema_version": "rbfe-network-admission.v1",
        "verdict": "CONFIRMED",
        "scope": "campaign_bound_network",
        "campaign_bound": True,
        **campaign_context,
        "prepared_system_ref": {
            "kind": "prepared_receptor_state",
            "id": campaign_context["prepared_system_id"],
            "sha256": digest,
        },
        "actor": actor,
    }


def rbfe_plan_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        campaign_context = {
            key: payload[key]
            for key in _RBFE_NETWORK_CAMPAIGN_KEYS
            if payload.get(key) is not None
        }
        required_campaign_keys = set(_RBFE_NETWORK_CAMPAIGN_KEYS)
        if campaign_context and set(campaign_context) != required_campaign_keys:
            missing = sorted(required_campaign_keys - set(campaign_context))
            raise ValueError(
                "campaign-bound network planning requires the complete immutable "
                f"campaign reference; missing {missing}")
        observed_attestation = (ctx.server_attestations or {}).get(
            _RBFE_NETWORK_ATTESTATION_KEY)
        expected_common = {
            "schema_version": "rbfe-network-admission.v1",
            "actor": dict(ctx.actor or {}),
        }
        if campaign_context:
            prepared_ref = (observed_attestation or {}).get(
                "prepared_system_ref") if isinstance(
                    observed_attestation, dict) else None
            expected_attestation = {
                **expected_common,
                "verdict": "CONFIRMED",
                "scope": "campaign_bound_network",
                "campaign_bound": True,
                **campaign_context,
                "prepared_system_ref": prepared_ref,
            }
            valid_prepared_ref = (
                isinstance(prepared_ref, dict)
                and set(prepared_ref) == {"kind", "id", "sha256"}
                and prepared_ref.get("kind") == "prepared_receptor_state"
                and prepared_ref.get("id") == campaign_context["prepared_system_id"]
                and isinstance(prepared_ref.get("sha256"), str)
                and len(prepared_ref["sha256"]) == 71
                and prepared_ref["sha256"].startswith("sha256:")
                and not any(char not in "0123456789abcdef"
                            for char in prepared_ref["sha256"][7:])
            )
        else:
            expected_attestation = {
                **expected_common,
                "verdict": "UNBOUND",
                "scope": "smoke_plan",
                "campaign_bound": False,
            }
            valid_prepared_ref = True
        if (not valid_prepared_ref
                or observed_attestation != expected_attestation):
            raise failures.DiracUnsupported(
                "RBFE network planning requires the exact server-sealed "
                "Campaign admission witness")
        network = plan_rbfe_network(
            payload["compounds"],
            extra_edge_fraction=payload.get("extra_edge_fraction", .35),
            minimum_similarity=payload.get("minimum_similarity", .15),
            mode=payload.get("mode", "pilot"),
            planner=payload.get("planner", "openfe"),
            campaign_context=campaign_context or None)
        network = dict(network)
        network.pop("digest", None)
        network["campaign_admission"] = {
            key: value for key, value in observed_attestation.items()
            if key != "actor"
        }
        network["digest"] = _digest(network)
    except failures.DiracFailure:
        raise
    except (ValueError, RuntimeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc
    return HandlerResult(
        result={"network_digest": network["digest"],
                "compound_count": len(network["compounds"]),
                "edge_count": len(network["edges"]), "network": network},
        artifacts=[("rbfe.network", _json(network))],
        provenance={
            "algorithm": "OpenFE redundant LigandNetwork + Lomap/Kartograf + FMCS diagnostic",
            "campaign_admission": observed_attestation,
        },
        warnings=[{"code": "NETWORK_PLAN_ONLY", "message": network["claim_boundary"]}])


def rbfe_aggregate_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        return aggregate_rbfe_evidence(payload, ctx)
    except failures.DiracFailure:
        raise
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc


def rbfe_system_prepare_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    ctx.check_budget()
    try:
        return prepare_rbfe_system(payload, ctx)
    except failures.DiracFailure:
        raise
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise failures.DiracInvalidParameters(str(exc)) from exc


def rbfe_estimate(payload: dict) -> dict:
    count = len(payload.get("compounds", payload.get("observations", ())))
    return {"available": True, "resource_class": "cpu",
            "estimated_seconds": max(.1, count / 100)}


__all__ = [name for name in globals() if name.endswith("_handler") or
           name.endswith("_estimate")]
