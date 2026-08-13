#!/usr/bin/env python3
"""Run the public Motif governance Commands against an explicit smoke fixture."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import jobs  # noqa: E402
import kernel  # noqa: E402
from dirac_app.dispatcher import CommandDispatcher  # noqa: E402
from motif.governance import with_semantic_digest  # noqa: E402


def stable_uuid(prefix: str, role: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"dirac:motif-smoke:{prefix}:{role}"))


def require_ok(envelope: dict, command: str) -> dict:
    if not envelope.get("ok"):
        raise RuntimeError(f"{command} failed: {json.dumps(envelope, sort_keys=True)}")
    return envelope["data"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default="dbname=dirac user=ivan")
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--program-id", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--assay-id", required=True)
    parser.add_argument("--source-artifact-id", required=True)
    args = parser.parse_args()

    actor = {"kind": "service", "id": "codex-motif-smoke"}
    endpoint_key = f"smoke.{args.prefix}.potency.ic50".lower()
    endpoint = with_semantic_digest({
        "schema_version": "2.0", "endpoint_key": endpoint_key, "version": "1",
        "assay": {"kind": "assay", "id": args.assay_id},
        "protocol": {"kind": "protocol", "id": f"{args.prefix}-protocol-v1"},
        "target": {"kind": "target", "id": args.target_id},
        "species": "synthetic", "biological_system": "control-plane smoke fixture",
        "readout": "IC50", "measurement_type": "concentration",
        "direction": "minimize", "canonical_unit": "nM",
        "quantity_dimension": "concentration", "label_transform": {},
        "censoring_policy": {"retain_bounds": True},
        "replicate_policy": {"aggregation": "none"},
        "qc_policy": {"fixture_only": True}, "intended_domain": {"fixture": True},
        "created_by": actor, "created_at": args.created_at,
    })
    policy_ids = {
        role: stable_uuid(args.prefix, f"{role}-policy")
        for role in ("generation", "fidelity", "acquisition", "diversity", "explanation")
    }
    identity_policy_id = stable_uuid(args.prefix, "identity-policy")
    policy_documents = [with_semantic_digest({
        "schema_version": "1.0", "policy_release_id": identifier,
        "policy_kind": kind, "name": f"smoke-{args.prefix}-{kind}", "version": "1",
        "lifecycle": "candidate", "spec": {"fixture": True, "policy_kind": kind},
        "created_by": actor, "created_at": args.created_at,
    }) for kind, identifier in {**policy_ids, "identity_gate": identity_policy_id}.items()]
    objective = with_semantic_digest({
        "schema_version": "2.0",
        "objective_spec_id": stable_uuid(args.prefix, "objective"),
        "program": {"kind": "program", "id": args.program_id},
        "campaign": {"kind": "campaign", "id": args.campaign_id},
        "target": {"kind": "target", "id": args.target_id},
        "target_state": {"state_id": "fixture", "description": "synthetic smoke state",
                         "confidence": 0.0},
        "objectives": [{"endpoint": {"id": endpoint_key, "version": "1"},
                        "direction": "minimize", "role": "soft_objective",
                        "priority": 1, "missing_value_policy": "require_measurement"}],
        "chemistry_constraints": {
            "protected": [], "forbidden": [],
            "identity_policy_release_id": identity_policy_id,
            "max_heavy_atoms": 80, "charge_range": {"lower": -2, "upper": 2},
        },
        "synthesis_resources": {
            "reaction_template_release_ids": [], "building_block_snapshot_ids": [],
            "max_route_depth": 4,
            "budget": {"currency": "USD", "amount": 0, "max_lead_days": 0},
        },
        "compute_budget": {
            "cpu_core_hours": 1, "gpu_hours": 0, "artifact_bytes": 1000000,
            "walltime_seconds": 300,
            "external_cost": {"currency": "USD", "amount": 0},
        },
        "experimental_capacity": {"max_selected": 0, "max_reserve": 0,
                                    "assay_slots": {}},
        "risk_policy": {"exploration_fraction": 0, "minimum_diversity": 0,
                        "out_of_domain": "refuse", "missing_evidence": "refuse"},
        "policy_releases": policy_ids,
        "created_by": actor, "created_at": args.created_at,
    })
    measurement = {
        "schema_version": "2.0", "measurement_id": f"{args.prefix}-not-tested-1",
        "sample": {"kind": "sample", "id": f"{args.prefix}-sample-1"},
        "assay": {"kind": "assay", "id": args.assay_id},
        "protocol": {"kind": "protocol", "id": f"{args.prefix}-protocol-v1"},
        "endpoint": {"id": endpoint_key, "version": "1"},
        "qualifier": "not_tested", "quantity": {"unit": "nM"},
        "qc": {"status": "not_assessed", "reason_codes": []},
        "missing_reason": "not_tested", "value_status": "raw",
        "source": {"artifact_id": args.source_artifact_id,
                   "record_locator": f"motif-smoke/{args.prefix}/1"},
        "measured_at": args.created_at, "recorded_at": args.created_at,
        "recorded_by": actor,
    }

    service = kernel.build(dsn=args.dsn, with_versions=False, with_cache=False,
                           job_store=jobs.MemoryJobStore())
    dispatcher = CommandDispatcher(service)
    calls = [("endpoint.register", {"definition": endpoint})]
    calls.extend(("policy.release.register", {"release": release})
                 for release in policy_documents)
    calls.extend([
        ("objective.save", {"objective": objective}),
        ("result.ingest", {"measurements": [measurement]}),
    ])
    result = {}
    for command, payload in calls:
        first = require_ok(dispatcher.execute(command, payload, actor=actor), command)
        second = require_ok(dispatcher.execute(command, payload, actor=actor), command)
        if not first.get("created", first.get("created_count") == 1):
            raise RuntimeError(f"{command}: first call was not created")
        if second.get("created", second.get("created_count") != 0):
            raise RuntimeError(f"{command}: second call was not deduplicated")
        result.setdefault(command, []).append({"first": first, "second": second})
    print(json.dumps({"ok": True, "fixture": "synthetic-control-plane-only",
                      "commands": result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
