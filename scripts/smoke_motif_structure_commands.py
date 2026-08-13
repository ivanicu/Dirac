#!/usr/bin/env python3
"""Exercise Motif structure/physics Methods through public durable Commands."""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import kernel  # noqa: E402
from dirac_app.dispatcher import CommandDispatcher  # noqa: E402


def main() -> None:
    service = kernel.build(dsn="dbname=dirac user=ivan")
    dispatcher = CommandDispatcher(service)
    actor = {"kind": "service", "id": "codex-motif-structure-smoke"}
    calls = [
        ("structure.conformers", {"smiles": "CCO", "count": 4, "seed": 19}),
        ("structure.vina", {
            "receptor_pdbqt": (
                "ATOM      1  C   ILE A  39       3.060  12.040  22.770  "
                "1.00  0.00     0.243 C \n"),
            "ligands": [{"id": "ethanol", "smiles": "CCO"}],
            "center": [3, 12, 23], "box_size": [10, 10, 10], "seed": 1,
            "exhaustiveness": 1, "n_poses": 1, "cpu": 1,
        }),
        ("physics.rbfe-network", {"compounds": [
            {"id": "a", "smiles": "CCO"}, {"id": "b", "smiles": "CCN"},
            {"id": "c", "smiles": "CCC"}], "extra_edge_fraction": 1}),
    ]
    output = {}
    for command, payload in calls:
        envelope = dispatcher.execute(command, payload, actor=actor)
        if not envelope.get("ok"):
            raise RuntimeError(json.dumps(envelope, sort_keys=True))
        job = service.wait_job(envelope["data"]["job"]["id"], timeout=120)
        if job["state"] != "done":
            raise RuntimeError(json.dumps(job, sort_keys=True))
        output[command] = {"job_id": job["id"],
                           "artifact_roles": [item["role"] for item in job["artifacts"]],
                           "result": job["result_summary"]["data"]}
    print(json.dumps({"ok": True, "fixture": "synthetic-control-plane-only",
                      "commands": output}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
