"""Compile frozen Motif inputs into an immutable, digest-addressed RunPlan."""
from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import failures


STEP_METHODS = {
    "snapshot.freeze": "data.motif.snapshot",
    "proposal.local_edit": "design.motif.local_edits",
    "proposal.reaction_enumerate": "design.motif.reaction_enumerate",
    "chemistry.identity_gate": "chem.identity.standardize",
    "synthesis.route_gate": "synthesis.motif.assess",
    "prediction.f1": "ml.motif.mesh.predict",
    "structure.conformer": "structure.motif.conformers",
    "structure.pose_f2": "structure.motif.vina",
    "structure.fields_f3": "fields.mep",
    "acquisition.portfolio": "design.motif.acquire",
    "review.human": None,
    "result.ingest": None,
    "model.recalibrate": "ml.motif.calibrate",
    "physics.md_f4": "physics.motif.openmm_md",
    "physics.rbfe_network_f4": "physics.motif.rbfe_network",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def compile_run_plan(spec: dict[str, Any]) -> dict[str, Any]:
    """Build the v1 dependency graph; policy IDs and budgets remain frozen inputs."""
    required = {
        "run_id", "root_seed", "objective_spec_id", "program_snapshot_id",
        "policies", "resource_envelope", "approval_gates",
    }
    missing = required - set(spec)
    if missing:
        raise failures.DiracInvalidParameters(f"RunPlan input misses {sorted(missing)}")
    for key in ("run_id", "objective_spec_id", "program_snapshot_id"):
        try:
            UUID(str(spec[key]))
        except ValueError as exc:
            raise failures.DiracInvalidParameters(f"{key} must be a UUID") from exc

    names = list(STEP_METHODS)
    steps = [{"index": index, "kind": name, "method_id": STEP_METHODS[name]}
             for index, name in enumerate(names)]
    edges = []
    for index in range(len(names) - 1):
        condition = {"type": "on_success"}
        if names[index + 1] == "review.human":
            condition = {"type": "if_approval", "gate": "portfolio_review"}
        edges.append({"from": index, "to": index + 1, "condition": condition})

    plan = {
        "run_id": spec["run_id"], "schema_version": "2.0",
        "root_seed": int(spec["root_seed"]),
        "objective_spec_id": spec["objective_spec_id"],
        "program_snapshot_id": spec["program_snapshot_id"],
        "steps": steps, "edges": edges,
        "policies": spec["policies"],
        "resource_envelope": spec["resource_envelope"],
        "approval_gates": spec["approval_gates"],
    }
    plan["digest"] = "sha256:" + hashlib.sha256(_canonical(plan)).hexdigest()
    return plan


def verify_run_plan(plan: dict[str, Any]) -> bool:
    candidate = dict(plan)
    expected = candidate.pop("digest", None)
    actual = "sha256:" + hashlib.sha256(_canonical(candidate)).hexdigest()
    return expected == actual
