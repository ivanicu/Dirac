#!/usr/bin/env python3
"""Non-compute HTTP attacks against the live FEP contract boundary."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8901"
UUID = "00000000-0000-4000-8000-000000000001"
DIGEST = "sha256:" + "a" * 64


def call(path: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def command(command_id: str, payload: dict) -> tuple[int, dict]:
    return call("/v2/execute", {"command": command_id, "input": payload})


def code(response: tuple[int, dict]) -> str:
    return str((response[1].get("error") or {}).get("code") or "")


def artifact_ref() -> dict:
    return {"kind": "artifact", "id": UUID, "sha256": DIGEST}


failures: list[str] = []
observed: dict[str, object] = {}
_, before = call("/health")
observed["jobs_opened_before"] = before.get("jobs", {}).get("opened")

_, meta = call("/v2/meta")
capabilities = meta.get("data", {}).get("capabilities", {})
if capabilities.get("rbfe_campaign_store", {}).get("ready") is not True:
    failures.append("campaign store is not ready after v2 initialization")

attacks = {
    "network_65_compounds": command("physics.rbfe-network", {
        "compounds": [{"id": f"L{i}", "smiles": "C"} for i in range(65)],
    }),
    "network_long_id": command("physics.rbfe-network", {
        "compounds": [{"id": "A" * 129, "smiles": "C"}, {"id": "B", "smiles": "CC"}],
    }),
    "network_long_smiles": command("physics.rbfe-network", {
        "compounds": [{"id": "A", "smiles": "C" * 4097}, {"id": "B", "smiles": "CC"}],
    }),
    "network_partial_campaign": command("physics.rbfe-network", {
        "compounds": [{"id": "A", "smiles": "C"}, {"id": "B", "smiles": "CC"}],
        "campaign_id": UUID,
    }),
    "run_blank_request_key": command("physics.rbfe-run.start", {
        "request_key": " \u00a0\u2007\u202f\ufeff",
        "campaign_id": UUID,
        "campaign_scientific_generation": 1,
        "campaign_scientific_digest": DIGEST,
        "edge_spec_ref": artifact_ref(),
        "edge_network_ref": artifact_ref(),
        "complex_transformation_ref": artifact_ref(),
        "solvent_transformation_ref": artifact_ref(),
    }),
    "run_ref_extra_field": command("physics.rbfe-run.get", {
        "run_ref": {"kind": "run", "id": UUID, "sha256": DIGEST},
    }),
    "job_only_sync_bypass": call("/v2/invoke", {
        "method_id": "physics.motif.rbfe_aggregate",
        "input": {
            "network_ref": artifact_ref(),
            "edge_spec_ref": artifact_ref(),
            "runs": [
                {"result_ref": artifact_ref(), "run_report_ref": artifact_ref()}
                for _ in range(6)
            ],
        },
    }),
}

for name, response in attacks.items():
    observed[name] = {"http": response[0], "code": code(response)}
    expected = "UNSUPPORTED" if name == "job_only_sync_bypass" else "INVALID_PARAMETERS"
    if code(response) != expected:
        failures.append(f"{name} returned {code(response) or 'no typed error'}, expected {expected}")

_, after = call("/health")
observed["jobs_opened_after"] = after.get("jobs", {}).get("opened")
if observed["jobs_opened_after"] != observed["jobs_opened_before"]:
    failures.append("an invalid request opened a durable Job")

print(json.dumps({"ok": not failures, "failures": failures, "observed": observed}, indent=2))
raise SystemExit(1 if failures else 0)
