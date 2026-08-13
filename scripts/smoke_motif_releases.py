#!/usr/bin/env python3
"""Exercise governed Dataset Snapshot → Model Release through public Commands."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import kernel  # noqa: E402
from dirac_app.dispatcher import CommandDispatcher  # noqa: E402


def require_ok(envelope: dict, command: str) -> dict:
    if not envelope.get("ok"):
        raise RuntimeError(f"{command} failed: {json.dumps(envelope, sort_keys=True)}")
    return envelope["data"]


def completed_job(service, command: str, payload: dict, actor: dict) -> dict:
    submitted = require_ok(
        CommandDispatcher(service).execute(command, payload, actor=actor), command)
    job = service.wait_job(submitted["job"]["id"], timeout=60)
    if job["state"] != "done":
        raise RuntimeError(f"{command} job did not complete: {json.dumps(job, sort_keys=True)}")
    return job


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default="dbname=dirac user=ivan")
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--program-id", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--endpoint-key", required=True)
    parser.add_argument("--identity-policy-release-id", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()

    actor = {"kind": "service", "id": "codex-motif-release-smoke"}
    rows = [
        {"measurement_id": f"{args.prefix}-m-1", "compound_id": f"{args.prefix}-c-1",
         "smiles": "CCO", "endpoint_key": args.endpoint_key,
         "protocol_id": f"{args.prefix}-protocol", "unit": "nM",
         "measurement_type": "concentration", "value": 1.0,
         "qualifier": "equal", "split": "train"},
        {"measurement_id": f"{args.prefix}-m-2", "compound_id": f"{args.prefix}-c-2",
         "smiles": "CCN", "endpoint_key": args.endpoint_key,
         "protocol_id": f"{args.prefix}-protocol", "unit": "nM",
         "measurement_type": "concentration", "value": 2.0,
         "qualifier": "equal", "split": "train"},
    ]
    service = kernel.build(dsn=args.dsn)
    dataset_job = completed_job(service, "dataset.snapshot.create", {
        "selection_query": f"synthetic-control-plane:{args.prefix}",
        "endpoint_definitions": [{"endpoint_key": args.endpoint_key, "version": "1",
                                  "canonical_unit": "nM",
                                  "measurement_type": "concentration"}],
        "rows": rows,
        "registration": {
            "program_ref": {"kind": "program", "id": args.program_id},
            "campaign_ref": {"kind": "campaign", "id": args.campaign_id},
            "identity_policy_release_id": args.identity_policy_release_id,
            "data_classification": "internal",
        },
    }, actor)
    dataset_result = dataset_job["result_summary"]["data"]["dataset_snapshot"]
    model_job = completed_job(service, "model.train", {
        "endpoint_key": args.endpoint_key, "rows": rows, "n_bits": 128,
        "registration": {
            "dataset_snapshot_ref": dataset_result["ref"],
            "model_object_id": f"motif-smoke-baseline-{args.prefix}",
            "release_name": "candidate-1", "source_commit": args.source_commit,
            "intended_use": {"purpose": "synthetic control-plane verification"},
            "prohibited_use": {"scientific_claims": True, "clinical_decisions": True},
            "known_limitations": {"synthetic_fixture": True,
                                  "held_out_validation": False,
                                  "runtime_wheel_hashes": False},
        },
    }, actor)
    model_result = model_job["result_summary"]["data"]["model_release"]
    print(json.dumps({
        "ok": True, "fixture": "synthetic-control-plane-only",
        "dataset_job_id": dataset_job["id"], "dataset_snapshot": dataset_result,
        "model_job_id": model_job["id"], "model_release": model_result,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
