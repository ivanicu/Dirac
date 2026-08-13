#!/usr/bin/env python3
"""Submit a varied Motif workload through Dirac's public durable Commands.

The fixtures are deliberately synthetic.  This is an execution/control-plane
smoke matrix, not biological or free-energy validation.
"""
from __future__ import annotations

import json
import pathlib
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import kernel  # noqa: E402
from dirac_app.dispatcher import CommandDispatcher  # noqa: E402


UUID = lambda n: f"00000000-0000-4000-8000-{n:012d}"


def _openmm_fixture() -> tuple[str, str]:
    import openmm
    from openmm import XmlSerializer

    system = openmm.System()
    system.addParticle(12.0)
    system.addParticle(12.0)
    bond = openmm.HarmonicBondForce()
    bond.addBond(0, 1, 0.15, 1000.0)
    system.addForce(bond)
    pdb = (
        "ATOM      1  C1  LIG A   1       0.000   0.000   0.000  1.00  0.00           C  \n"
        "ATOM      2  C2  LIG A   1       1.500   0.000   0.000  1.00  0.00           C  \n"
        "CONECT    1    2\nEND\n"
    )
    return XmlSerializer.serialize(system), pdb


def _summary(job: dict[str, Any], *, deduplicated: bool | None = None) -> dict[str, Any]:
    result = (job.get("result_summary") or {}).get("data")
    error = ({"code": job.get("error_code"), "detail": job.get("error_detail")}
             if job.get("error_code") else None)
    out = {
        "job_id": job["id"],
        "state": job["state"],
        "artifact_roles": [item["role"] for item in job.get("artifacts", [])],
        "result": result,
        "error": error,
        "outcome_class": job.get("outcome_class"),
    }
    if deduplicated is not None:
        out["deduplicated"] = deduplicated
    return out


def main() -> None:
    service = kernel.build(dsn="dbname=dirac user=ivan")
    dispatcher = CommandDispatcher(service)
    actor = {"kind": "service", "id": "codex-motif-variety-matrix"}

    bayesian = {
        "train_features": [[0., 0.], [1., 0.], [0., 1.], [1., 1.], [.5, .5]],
        "train_objectives": [[0., 0.], [1., .2], [.2, 1.], [.8, .8], [.6, .7]],
        "reference_point": [-.1, -.1],
        "candidates": [
            {"proposal_id": UUID(101), "features": [.1, .9],
             "objectives": {"potency": .75, "selectivity": .88},
             "constraints": {"route": True}, "components": {"cost": 1.0}},
            {"proposal_id": UUID(102), "features": [.9, .1],
             "objectives": {"potency": .91, "selectivity": .51},
             "constraints": {"route": True}, "components": {"cost": 2.0}},
            {"proposal_id": UUID(103), "features": [.7, .7],
             "objectives": {"potency": .84, "selectivity": .83},
             "constraints": {"route": False}, "components": {"cost": 1.5}},
        ],
        "objectives": [
            {"key": "potency", "direction": "maximize"},
            {"key": "selectivity", "direction": "maximize"},
        ],
        "hard_constraints": [{"key": "route", "equals": True}],
        "capacity": 2,
        "mc_samples": 16,
        "seed": 812,
    }
    local_edit = {
        "strategy": "local_edit",
        "parents": [{"id": "benzene", "smiles": "c1ccccc1"}],
        "transforms": [{
            "transform_id": "aryl_f", "version": "1",
            "reaction_smarts": "[cH:1]>>[c:1]F",
            "description": "synthetic aryl fluorination fixture",
        }],
        "generator_release_id": UUID(201),
        "strategy_release_id": UUID(202),
        "identity_policy_release_id": UUID(203),
        "root_seed": 812,
        "created_at": "2026-08-12T12:00:00Z",
        "constraints": {
            "max_heavy_atoms": 20, "charge_range": [-1, 1],
            "forbidden_smarts": ["[N+](=O)[O-]"],
            "reject_unassigned_stereo": True,
        },
        "max_proposals": 8,
    }
    receptor = (
        "ATOM      1  C   ILE A  39       3.060  12.040  22.770  "
        "1.00  0.00     0.243 C \n"
    )
    system_xml, topology_pdb = _openmm_fixture()
    independent = [
        ("bayesian_rank", "campaign.bayesian-rank", bayesian),
        ("local_edit", "proposal.generate", local_edit),
        ("conformer", "structure.conformers",
         {"smiles": "CCOc1ccc(C(=O)NCC)cc1", "count": 16,
          "seed": 812, "prune_rms_thresh": .35, "minimize": True}),
        ("vina", "structure.vina", {
            "receptor_pdbqt": receptor,
            "ligands": [
                {"id": "ethanol", "smiles": "CCO"},
                {"id": "ethylamine", "smiles": "CCN"},
            ],
            "center": [3, 12, 23], "box_size": [10, 10, 10],
            "seed": 812, "exhaustiveness": 2, "n_poses": 2,
            "energy_range": 2.0, "cpu": 1,
        }),
        ("openmm_reference", "physics.openmm-md", {
            "system_xml": system_xml, "topology_pdb": topology_pdb,
            "steps": 25, "temperature_kelvin": 300.0,
            "friction_per_ps": 1.0, "timestep_fs": 1.0, "seed": 812,
            "report_interval": 5, "minimize": True,
            "platform_name": "Reference", "precision": "double",
        }),
        ("rbfe_network", "physics.rbfe-network", {
            "compounds": [
                {"id": "a", "smiles": "CCO"},
                {"id": "b", "smiles": "CCN"},
                {"id": "c", "smiles": "CCC"},
                {"id": "d", "smiles": "CCF"},
            ],
            "extra_edge_fraction": 1.0, "minimum_similarity": 0.0,
        }),
        ("intentional_bad_smiles", "structure.conformers",
         {"smiles": "this-is-not-smiles", "count": 3, "seed": 812}),
    ]

    submissions: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    inflight_replay = None
    for name, command, payload in independent:
        envelope = dispatcher.execute(command, payload, actor=actor)
        if not envelope.get("ok"):
            raise RuntimeError(json.dumps({"name": name, "envelope": envelope}, sort_keys=True))
        submissions[name] = (envelope, payload)
        if name == "conformer":
            # Submit again before waiting.  The durable ledger should join the
            # already queued/running invocation rather than launch duplicate work.
            inflight_replay = dispatcher.execute(command, payload, actor=actor)

    results: dict[str, dict[str, Any]] = {}
    for name, (envelope, _) in submissions.items():
        job = service.wait_job(envelope["data"]["job"]["id"], timeout=180)
        results[name] = _summary(job, deduplicated=envelope["meta"].get("deduplicated"))

    if not inflight_replay or not inflight_replay.get("ok"):
        raise RuntimeError(json.dumps({"inflight_replay": inflight_replay}, sort_keys=True))
    inflight_job = service.wait_job(inflight_replay["data"]["job"]["id"], timeout=120)
    results["inflight_replay"] = _summary(
        inflight_job, deduplicated=inflight_replay["meta"].get("deduplicated"))
    results["inflight_replay"]["same_job_as_original"] = (
        inflight_job["id"] == results["conformer"]["job_id"])

    network = results["rbfe_network"]["result"]["network"]
    observations = []
    for index, edge in enumerate(network["edges"]):
        observations.append({
            "edge_id": edge["edge_id"], "status": "completed",
            "ddg_kcal_mol": (-1.0 if index % 2 else 1.0) * (index + 1) * .2,
            "uncertainty_kcal_mol": .15 + .05 * index,
        })
    complete = dispatcher.execute(
        "physics.rbfe-aggregate",
        {"network": network, "observations": observations}, actor=actor)
    partial = dispatcher.execute(
        "physics.rbfe-aggregate",
        {"network": network, "observations": [
            {"edge_id": network["edges"][0]["edge_id"], "status": "failed",
             "reason": "synthetic convergence failure"},
        ]}, actor=actor)
    for name, envelope in (("rbfe_complete", complete), ("rbfe_partial", partial)):
        if not envelope.get("ok"):
            raise RuntimeError(json.dumps({"name": name, "envelope": envelope}, sort_keys=True))
        job = service.wait_job(envelope["data"]["job"]["id"], timeout=120)
        results[name] = _summary(job, deduplicated=envelope["meta"].get("deduplicated"))

    replay = dispatcher.execute(
        "campaign.bayesian-rank", bayesian, actor=actor)
    if not replay.get("ok"):
        raise RuntimeError(json.dumps(replay, sort_keys=True))
    replay_job = service.wait_job(replay["data"]["job"]["id"], timeout=120)
    results["completed_replay"] = _summary(
        replay_job, deduplicated=replay["meta"].get("deduplicated"))
    results["completed_replay"]["same_job_as_original"] = (
        replay_job["id"] == results["bayesian_rank"]["job_id"])
    results["completed_replay"]["same_scientific_digest"] = (
        results["completed_replay"]["result"]["acquisition_digest"]
        == results["bayesian_rank"]["result"]["acquisition_digest"])

    expected = {
        "intentional_bad_smiles": "failed",
        **{name: "done" for name in results if name != "intentional_bad_smiles"},
    }
    mismatches = {name: {"expected": state, "actual": results[name]["state"]}
                  for name, state in expected.items() if results[name]["state"] != state}
    bad = results["intentional_bad_smiles"]
    if bad["error"] != {"code": "INVALID_PARAMETERS",
                         "detail": "cannot parse SMILES 'this-is-not-smiles'"}:
        mismatches["intentional_bad_smiles.error"] = {
            "expected": "INVALID_PARAMETERS", "actual": bad["error"]}
    if bad["outcome_class"] != "expected_refusal":
        mismatches["intentional_bad_smiles.outcome_class"] = {
            "expected": "expected_refusal", "actual": bad["outcome_class"]}
    if not (results["inflight_replay"]["deduplicated"]
            and results["inflight_replay"]["same_job_as_original"]):
        mismatches["inflight_replay"] = {
            "expected": "joined original Job", "actual": results["inflight_replay"]}
    if not results["completed_replay"]["same_scientific_digest"]:
        mismatches["completed_replay"] = {
            "expected": "same scientific digest", "actual": results["completed_replay"]}
    print(json.dumps({
        "ok": not mismatches,
        "fixture": "synthetic-control-plane-and-engine-smoke-only",
        "scientific_claim": "none",
        "mismatches": mismatches,
        "jobs": results,
    }, indent=2, sort_keys=True))
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
