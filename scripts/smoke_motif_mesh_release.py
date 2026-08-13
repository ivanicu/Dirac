#!/usr/bin/env python3
"""Create a governed synthetic Dataset Snapshot and predictor-mesh Model Release."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import kernel  # noqa: E402
from dirac_app.dispatcher import CommandDispatcher  # noqa: E402


def _done(service, command: str, payload: dict, actor: dict) -> dict:
    envelope = CommandDispatcher(service).execute(command, payload, actor=actor)
    if not envelope.get("ok"):
        raise RuntimeError(json.dumps(envelope, sort_keys=True))
    job = service.wait_job(envelope["data"]["job"]["id"], timeout=180)
    if job["state"] != "done":
        raise RuntimeError(json.dumps(job, sort_keys=True))
    return job


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default="dbname=dirac user=ivan")
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--program-id", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--endpoint-key", required=True)
    parser.add_argument("--identity-policy-release-id", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--include-chemprop", action="store_true")
    args = parser.parse_args()
    actor = {"kind": "service", "id": "codex-motif-mesh-smoke"}
    chemistry = [
        ("CCO", 1.0), ("CCN", 1.2), ("CCC", 1.4), ("CCCl", 1.7),
        ("CCBr", 1.8), ("CCCO", 1.3), ("CCCN", 1.5), ("CCCC", 1.6),
        ("c1ccccc1", 2.2), ("c1ccncc1", 2.0), ("CC(=O)O", 1.1),
        ("CCOC", 1.45),
    ]
    splits = ["train"] * 8 + ["validation"] * 2 + ["calibration"] * 2
    rows = [{
        "measurement_id": f"{args.prefix}-m-{index}",
        "compound_id": f"{args.prefix}-c-{index}", "smiles": smiles,
        "endpoint_key": args.endpoint_key,
        "protocol_id": f"{args.prefix}-protocol-{index}", "unit": "nM",
        "measurement_type": "concentration", "value": value,
        "qualifier": "equal", "split": splits[index],
    } for index, (smiles, value) in enumerate(chemistry)]
    service = kernel.build(dsn=args.dsn)
    dataset_job = _done(service, "dataset.snapshot.create", {
        "selection_query": f"synthetic-mesh-control:{args.prefix}",
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
    snapshot = dataset_job["result_summary"]["data"]["dataset_snapshot"]
    model_job = _done(service, "model.mesh.train", {
        "endpoint_key": args.endpoint_key, "rows": rows, "n_bits": 128,
        "include_chemprop": args.include_chemprop,
        "chemprop_ensemble_size": 2, "chemprop_epochs": 1,
        "accelerator": "cpu", "bootstrap_samples": 20, "seed": 73,
        "registration": {
            "dataset_snapshot_ref": snapshot["ref"],
            "model_object_id": f"motif-mesh-{args.prefix}",
            "release_name": "candidate-cpu-control-plane",
            "source_commit": args.source_commit,
            "intended_use": {"purpose": "synthetic control-plane verification"},
            "prohibited_use": {"scientific_claims": True, "clinical_decisions": True},
            "known_limitations": {"synthetic_fixture": True,
                                  "chemprop_in_release": args.include_chemprop,
                                  "gpu_verified_separately": True},
        },
    }, actor)
    release = model_job["result_summary"]["data"]["model_release"]
    print(json.dumps({
        "ok": True, "fixture": "synthetic-control-plane-only",
        "dataset_job_id": dataset_job["id"], "dataset_snapshot": snapshot,
        "model_job_id": model_job["id"], "model_release": release,
        "validation": model_job["result_summary"]["data"]["validation"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
