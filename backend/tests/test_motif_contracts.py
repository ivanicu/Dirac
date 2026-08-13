from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from contracts.validation import validator_for

ROOT = Path(__file__).resolve().parents[2]
UUIDS = [f"00000000-0000-4000-8000-{index:012d}" for index in range(1, 40)]
DIGEST = "sha256:" + "a" * 64
NOW = "2026-08-12T00:00:00Z"


def ref(kind: str, index: int) -> dict[str, str]:
    return {"kind": kind, "id": f"{kind}-{index}"}


MEASUREMENT = {
    "schema_version": "2.0", "measurement_id": "m-1",
    "sample": ref("sample", 1), "assay": ref("assay", 1),
    "protocol": ref("protocol", 1), "endpoint": {"id": "ic50", "version": "1"},
    "qualifier": "not_tested", "quantity": {"unit": "nM"},
    "qc": {"status": "not_assessed", "reason_codes": []},
    "missing_reason": "not_tested",
    "source": {"artifact_id": UUIDS[0], "record_locator": "rows/1"},
    "measured_at": NOW,
}

ENDPOINT_DEFINITION = {
    "schema_version": "2.0", "endpoint_key": "ic50", "version": "1",
    "assay": ref("assay", 1), "protocol": ref("protocol", 1),
    "target": ref("target", 1), "species": "Homo sapiens",
    "biological_system": "biochemical binding", "readout": "IC50",
    "measurement_type": "concentration", "direction": "minimize",
    "canonical_unit": "nM", "quantity_dimension": "concentration",
    "label_transform": {"kind": "pIC50"},
    "censoring_policy": {"retain_bounds": True},
    "replicate_policy": {"aggregation": "median"},
    "qc_policy": {"exclude": ["fail"]}, "intended_domain": {},
    "created_by": {"kind": "human", "id": "chemist-1"}, "created_at": NOW,
    "digest": DIGEST,
}

POLICY_RELEASE = {
    "schema_version": "1.0", "policy_release_id": UUIDS[30],
    "policy_kind": "generation", "name": "motif-generation", "version": "1",
    "lifecycle": "candidate", "spec": {"strategy": "local_edit"},
    "created_by": {"kind": "human", "id": "chemist-1"}, "created_at": NOW,
    "digest": DIGEST,
}

DESIGN_BRIEF = {
    "schema_version": "2.0", "objective_spec_id": UUIDS[1],
    "program": ref("program", 1), "campaign": ref("campaign", 1),
    "target": ref("target", 1),
    "target_state": {"state_id": "active", "description": "active state", "confidence": 0.8},
    "objectives": [{
        "endpoint": {"id": "ic50", "version": "1"}, "direction": "minimize",
        "role": "soft_objective", "priority": 1, "missing_value_policy": "penalize",
    }],
    "chemistry_constraints": {
        "protected": [], "forbidden": [], "identity_policy_release_id": UUIDS[2],
        "max_heavy_atoms": 80, "charge_range": {"lower": -2, "upper": 2},
    },
    "synthesis_resources": {
        "reaction_template_release_ids": [], "building_block_snapshot_ids": [],
        "max_route_depth": 4,
        "budget": {"currency": "USD", "amount": 1000, "max_lead_days": 30},
    },
    "compute_budget": {
        "cpu_core_hours": 10, "gpu_hours": 1, "artifact_bytes": 1000000,
        "walltime_seconds": 3600, "external_cost": {"currency": "USD", "amount": 10},
    },
    "experimental_capacity": {"max_selected": 10, "max_reserve": 10, "assay_slots": {}},
    "risk_policy": {
        "exploration_fraction": 0.2, "minimum_diversity": 0.4,
        "out_of_domain": "reserve_only", "missing_evidence": "penalize",
    },
    "policy_releases": {
        "generation": UUIDS[3], "fidelity": UUIDS[4], "acquisition": UUIDS[5],
        "diversity": UUIDS[6], "explanation": UUIDS[7],
    },
    "created_by": {"kind": "human", "id": "chemist-1"}, "created_at": NOW,
    "digest": DIGEST,
}

PROPOSAL = {
    "schema_version": "2.0", "proposal_id": UUIDS[8], "compound": ref("compound", 1),
    "parents": [ref("compound", 0)], "strategy": "local_edit",
    "generator_release_id": UUIDS[9],
    "generation_trace": {
        "root_seed": 1729, "strategy_release_id": UUIDS[10],
        "constraints_applied_during_generation": [],
    },
    "synthesis": {"status": "route_unknown", "route_depth": None,
                  "estimated_cost": None, "estimated_days": None},
    "identity_gate": {"status": "pass", "reason_codes": []}, "created_at": NOW,
}

EVALUATION = {
    "schema_version": "2.0", "proposal_id": UUIDS[8], "objective_spec_id": UUIDS[1],
    "predictions": [], "conflicts": [], "warnings": [], "evaluated_at": NOW,
}

PORTFOLIO = {
    "schema_version": "2.0", "portfolio_id": UUIDS[11], "cycle_id": UUIDS[12],
    "objective_spec_id": UUIDS[1], "program_snapshot_id": UUIDS[13],
    "selected": [], "reserve": [], "rejected": [], "refused": [],
    "capacity_totals": {}, "diversity_summary": {},
    "policy": {"acquisition_release_id": UUIDS[5], "diversity_release_id": UUIDS[6],
               "root_seed": 1729, "solver_settings_digest": DIGEST},
    "human_review": {"status": "not_started", "required_approvals": 1, "approvals": []},
    "created_at": NOW,
}

MOTIF_CYCLE = {
    "schema_version": "2.0", "cycle_id": UUIDS[12], "mission_id": UUIDS[14],
    "run_id": UUIDS[15], "objective_spec_id": UUIDS[1], "program_snapshot_id": UUIDS[13],
    "root_seed": 1729,
    "steps": [{"step_id": UUIDS[16], "step_key": "motif.propose", "kind": "compute",
               "method_id": "design.motif.propose", "required": True,
               "job_policy": "required", "input_bindings": {}, "resource_profile": "local_cpu"}],
    "edges": [], "policy_releases": {"generation": UUIDS[3]},
    "resource_envelope": {}, "approval_gates": [], "digest": DIGEST, "created_at": NOW,
}

EXECUTION_REQUEST = {
    "schema_version": "1.0", "execution_id": UUIDS[17], "job_id": UUIDS[18],
    "step_id": UUIDS[16], "attempt_id": UUIDS[19], "attempt": 1, "fencing_token": 1,
    "method_id": "ml.motif.evaluate", "execution_digest": DIGEST,
    "container_image": "registry.local/motif@sha256:" + "b" * 64,
    "entrypoint": ["python", "-m", "dirac_worker"],
    "input_manifest_artifact_id": UUIDS[20], "output_contract_digest": DIGEST,
    "resource_request": {"cpu_cores": 4, "memory_bytes": 1073741824, "gpus": 0,
                         "scratch_bytes": 1000000, "walltime_seconds": 3600},
    "placement": {"backend": "local_cpu", "topology": "single_process"},
    "retry_policy": {"max_attempts": 2, "retryable_codes": ["WORKER_LOST"],
                     "backoff": {"kind": "fixed", "initial_seconds": 1, "max_seconds": 1},
                     "preserve_seed": True, "resume_from_checkpoint": False},
    "checkpoint_policy": {"enabled": False, "upload_mode": "sync", "retain_last": 0,
                          "checkpoint_timeout_seconds": 60},
    "security_context": {"actor": {"kind": "service", "id": "worker"},
                         "project_scope": "program:demo", "artifact_read_ids": [UUIDS[20]],
                         "artifact_write_session": "session-1", "credential_expires_at": NOW},
    "determinism": {"class": "bitwise", "root_seed": 1729, "numeric_mode": "fp64"},
    "created_at": NOW,
}

OUTPUT_MANIFEST = {
    "schema_version": "1.0", "job_id": UUIDS[18], "attempt_id": UUIDS[19],
    "fencing_token": 1, "execution_digest": DIGEST, "artifacts": [],
    "result_summary": {}, "warnings": [], "started_at": NOW, "finished_at": NOW,
}

FIXTURES = {
    "domain/motif/endpoint-definition.schema.json": ENDPOINT_DEFINITION,
    "domain/motif/policy-release.schema.json": POLICY_RELEASE,
    "domain/motif/measurement-v2.schema.json": MEASUREMENT,
    "domain/motif/design-brief.schema.json": DESIGN_BRIEF,
    "domain/motif/proposal.schema.json": PROPOSAL,
    "domain/motif/evaluation.schema.json": EVALUATION,
    "domain/motif/portfolio.schema.json": PORTFOLIO,
    "domain/motif/motif-cycle.schema.json": MOTIF_CYCLE,
    "execution/execution-request.schema.json": EXECUTION_REQUEST,
    "execution/output-manifest.schema.json": OUTPUT_MANIFEST,
}


class MotifContractTests(unittest.TestCase):
    def test_every_schema_has_positive_and_unknown_field_negative_fixture(self):
        for relative, fixture in FIXTURES.items():
            with self.subTest(schema=relative):
                schema = json.loads((ROOT / "contracts" / relative).read_text(encoding="utf-8"))
                validator = validator_for(schema)
                self.assertEqual(list(validator.iter_errors(fixture)), [])
                invalid = copy.deepcopy(fixture)
                invalid["unknown_contract_field"] = True
                self.assertNotEqual(list(validator.iter_errors(invalid)), [])

    def test_missing_measurement_cannot_carry_a_numeric_value(self):
        schema = json.loads(
            (ROOT / "contracts/domain/motif/measurement-v2.schema.json").read_text()
        )
        invalid = copy.deepcopy(MEASUREMENT)
        invalid["quantity"]["value"] = 12.3
        self.assertNotEqual(list(validator_for(schema).iter_errors(invalid)), [])

    def test_kind_specific_reference_rejects_wrong_kind(self):
        schema = json.loads(
            (ROOT / "contracts/domain/motif/proposal.schema.json").read_text()
        )
        invalid = copy.deepcopy(PROPOSAL)
        invalid["compound"] = ref("model", 1)
        self.assertNotEqual(list(validator_for(schema).iter_errors(invalid)), [])

    def test_invalid_format_and_mutable_image_are_rejected(self):
        schema = json.loads(
            (ROOT / "contracts/execution/execution-request.schema.json").read_text()
        )
        invalid = copy.deepcopy(EXECUTION_REQUEST)
        invalid["attempt_id"] = "not-a-uuid"
        invalid["container_image"] = "registry.local/motif:latest"
        self.assertNotEqual(list(validator_for(schema).iter_errors(invalid)), [])


if __name__ == "__main__":
    unittest.main()
