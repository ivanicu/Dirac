from __future__ import annotations

import json
import inspect
from pathlib import Path
from types import SimpleNamespace

import field_server
import kernel
from contracts.validation import validator_for


ROOT = Path(__file__).resolve().parents[2]
DIGEST = "sha256:" + "a" * 64
UUID = "00000000-0000-4000-8000-000000000001"


class _Cursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query):
        self.connection.sql = query

    def fetchone(self):
        return self.connection.row


class _Connection:
    def __init__(self, row):
        self.row = row
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _Cursor(self)


def _readiness(row, service, *, return_connection=False):
    original_db, original_psycopg = field_server._db, field_server.psycopg
    connection = _Connection(row)
    field_server._db = lambda: connection
    field_server.psycopg = object()
    try:
        result = field_server._rbfe_readiness(service)
        return (result, connection) if return_connection else result
    finally:
        field_server._db, field_server.psycopg = original_db, original_psycopg


def _commands() -> dict[str, dict]:
    registry = json.loads(
        (ROOT / "contracts/commands/registry.json").read_text(encoding="utf-8")
    )
    return {row["id"]: row for row in registry["commands"]}


def _method(method_id: str) -> dict:
    path = ROOT / "contracts/methods" / f"{method_id}.method.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _artifact_ref() -> dict[str, str]:
    return {"kind": "artifact", "id": UUID, "sha256": DIGEST}


def test_rbfe_readiness_is_independent_of_process_health_and_names_migrations():
    unavailable = _readiness((False,) * 16, SimpleNamespace())
    assert unavailable["rbfe_campaign_store"] == {
        "ready": False,
        "required_migrations": [
            "040_rbfe_campaign_state.sql",
            "045_rbfe_campaign_artifact_ownership.sql",
            "046_job_command_request_key.sql",
            "047_job_dispatch_fence.sql",
        ],
        "reason": "missing schema capability from "
                  "040_rbfe_campaign_state.sql, "
                  "045_rbfe_campaign_artifact_ownership.sql, "
                  "046_job_command_request_key.sql, "
                  "047_job_dispatch_fence.sql",
        "legacy_unowned_policy": "fail_closed",
        "owner_inference": False,
        "implicit_public": False,
    }
    assert unavailable["rbfe_runset"] == {
        "ready": False,
        "required_migrations": [
            "041_rbfe_runset_cancellation.sql",
            "042_job_tenant_isolation.sql",
            "043_rbfe_runset_tenant_request_key.sql",
            "044_rbfe_runset_state_integrity.sql",
            "046_job_command_request_key.sql",
        ],
        "reason": "missing schema capability from "
                  "041_rbfe_runset_cancellation.sql, "
                  "042_job_tenant_isolation.sql, "
                  "043_rbfe_runset_tenant_request_key.sql, "
                  "044_rbfe_runset_state_integrity.sql, "
                  "046_job_command_request_key.sql",
        "gpu_execution": False,
        "executor_adapter": "unconfigured",
    }


def test_rbfe_readiness_requires_live_components_after_schema_probe():
    uninitialised = _readiness((True,) * 16, None)
    assert uninitialised["rbfe_campaign_store"]["ready"] is False
    assert "not initialized" in uninitialised["rbfe_campaign_store"]["reason"]

    service = SimpleNamespace(
        rbfe_reference_resolver=object(), rbfe_runset_controller=object(),
        capabilities=lambda: {"executor": {
            "adapter": "kubernetes", "gpu_execution": True}})
    ready = _readiness((True,) * 16, service)
    assert ready["rbfe_campaign_store"]["ready"] is True
    assert ready["rbfe_runset"]["ready"] is True
    assert ready["rbfe_campaign_store"]["reason"] == "ready"
    assert ready["rbfe_runset"]["reason"] == "ready"
    assert ready["rbfe_runset"]["executor_adapter"] == "kubernetes"


def test_rbfe_runset_readiness_is_false_without_gpu_admission():
    service = SimpleNamespace(
        rbfe_reference_resolver=object(), rbfe_runset_controller=object(),
        capabilities=lambda: {"executor": {
            "adapter": "local_cpu", "gpu_execution": False}})
    readiness = _readiness((True,) * 16, service)
    assert readiness["rbfe_campaign_store"]["ready"] is True
    assert readiness["rbfe_runset"]["ready"] is False
    assert "GPU execution is unavailable" in readiness["rbfe_runset"]["reason"]


def test_rbfe_readiness_rejects_each_post_base_capability_independently():
    service = SimpleNamespace(
        rbfe_reference_resolver=object(), rbfe_runset_controller=object(),
        capabilities=lambda: {"executor": {
            "adapter": "kubernetes", "gpu_execution": True}})
    missing_045 = _readiness(
        (True, True, True, True, True, False, False,
         True, True, True, True, True, True, True, True, True), service)
    assert missing_045["rbfe_campaign_store"]["ready"] is False
    assert "045_rbfe_campaign_artifact_ownership.sql" in (
        missing_045["rbfe_campaign_store"]["reason"])
    assert missing_045["rbfe_runset"]["ready"] is False

    missing_042, connection = _readiness(
        (True, True, True, True, True, True, True,
         True, True, True, False, True, True, True, True, True), service,
        return_connection=True)
    assert missing_042["rbfe_runset"]["ready"] is False
    assert "042_job_tenant_isolation.sql" in (
        missing_042["rbfe_runset"]["reason"])
    assert "pg_get_indexdef(indexrelid)" in connection.sql
    assert "(actor_kind, actor_id, method_row_id, request_digest)" in connection.sql
    assert "pg_get_expr(indpred, indrelid)" in connection.sql
    assert "((request_key IS NULL) AND (state = ANY " in connection.sql
    assert "'queued''::app.job_state, ''running''::app.job_state" in connection.sql

    missing_043 = _readiness(
        (True, True, True, True, True, True, True,
         True, True, True, True, False, True, True, True, True), service)
    assert missing_043["rbfe_runset"]["ready"] is False
    assert "043_rbfe_runset_tenant_request_key.sql" in (
        missing_043["rbfe_runset"]["reason"])

    missing_044 = _readiness(
        (True, True, True, True, True, True, True,
         True, True, True, True, True, False, True, True, True), service)
    assert missing_044["rbfe_runset"]["ready"] is False
    assert "044_rbfe_runset_state_integrity.sql" in (
        missing_044["rbfe_runset"]["reason"])

    missing_046, command_connection = _readiness(
        (True,) * 13 + (False, False, True), service,
        return_connection=True)
    assert missing_046["rbfe_campaign_store"]["ready"] is False
    assert missing_046["rbfe_runset"]["ready"] is False
    assert "046_job_command_request_key.sql" in (
        missing_046["rbfe_campaign_store"]["reason"])
    assert "job_command_request_key_once" in command_connection.sql
    assert "job_request_key_nonempty" in command_connection.sql
    assert "job_request_key_length" in command_connection.sql
    assert "job_request_key_has_command" in command_connection.sql
    assert "pg_get_constraintdef(oid) = format(" in command_connection.sql
    assert "[^[:space:]" in command_connection.sql
    assert "CHECK (((request_key IS NULL) OR (length(request_key) <= 256)))" in (
        command_connection.sql)
    assert "CHECK (((request_key IS NULL) OR (command_id IS NOT NULL)))" in (
        command_connection.sql)
    assert "LIKE '%%request_key ~%%'" not in command_connection.sql

    # Fake a catalog whose named nonempty CHECK was weakened with `OR TRUE`.
    # The exact-expression aggregate is false even though the column, remaining
    # checks and all-state index still exist, so both readiness surfaces close.
    or_true_046 = _readiness(
        (True,) * 13 + (False, True, True), service)
    assert or_true_046["rbfe_campaign_store"]["ready"] is False
    assert or_true_046["rbfe_runset"]["ready"] is False

    missing_047 = _readiness((True,) * 15 + (False,), service)
    assert missing_047["rbfe_campaign_store"]["ready"] is False
    assert "047_job_dispatch_fence.sql" in (
        missing_047["rbfe_campaign_store"]["reason"])


def test_kernel_resolver_requires_explicit_campaign_artifact_ownership():
    source = inspect.getsource(kernel.default_rbfe_reference_resolver)
    assert "040_rbfe_campaign_state.sql" in source
    assert "045_rbfe_campaign_artifact_ownership.sql" in source
    assert "046_job_command_request_key.sql" in source
    assert "rbfe_campaign_artifact_role_fk" in source
    assert "job_command_request_key_once" in source
    assert "pg_get_constraintdef(oid) = format(" in source
    assert "LIKE '%%request_key ~%%'" not in source


def test_rbfe_network_command_exposes_method_mode_and_planner():
    command = _commands()["physics.rbfe-network"]
    properties = command["input_schema"]["properties"]
    method_properties = _method(
        "physics.motif.rbfe_network")["input"]["schema"]["properties"]
    assert properties["mode"] == method_properties["mode"]
    assert properties["planner"] == method_properties["planner"]


def test_rbfe_network_campaign_context_is_strictly_all_or_none():
    command_schema = _commands()["physics.rbfe-network"]["input_schema"]
    method_schema = _method(
        "physics.motif.rbfe_network")["input"]["schema"]
    base = {"compounds": [
        {"id": "A", "smiles": "CC"}, {"id": "B", "smiles": "CCC"},
    ]}
    complete = {
        **base, "campaign_id": UUID,
        "campaign_scientific_generation": 1,
        "campaign_scientific_digest": DIGEST,
        "prepared_system_id": UUID,
    }
    for schema in (command_schema, method_schema):
        assert list(validator_for(schema).iter_errors(base)) == []
        assert list(validator_for(schema).iter_errors(complete)) == []
        for field in (
                "campaign_id", "campaign_scientific_generation",
                "campaign_scientific_digest", "prepared_system_id"):
            partial = dict(complete)
            partial.pop(field)
            assert list(validator_for(schema).iter_errors(partial)), field


def test_system_list_items_have_one_exact_scientific_scope_contract():
    schema = _commands()["physics.rbfe-system.list"]["output_schema"]
    item = {
        "prepared_receptor_state_ref": {
            "kind": "prepared_receptor_state", "id": UUID, "sha256": DIGEST,
        },
        "campaign_scope": "import_stale",
        "source_campaign_id": UUID,
        "source_campaign_scientific_ref": {
            "kind": "rbfe_campaign", "id": UUID, "version": 2,
            "sha256": DIGEST,
        },
        "import_required": True, "execution_eligible": False,
        "label": "prepared receptor", "target_name": "JAK2",
        "target_ref": {}, "protein_structure_ref": {}, "pdb_id": "1ABC",
        "experimental_method": "xray", "resolution_angstrom": 1.8,
        "preparation_state": "import-required",
        "claim_boundary": "human review required",
        "poses": [{
            "pose_ref": {}, "label": "parent", "canonical_smiles": "CC",
            "core_rmsd_angstrom": None, "core_coverage": None,
            "minimum_heavy_atom_distance_angstrom": None,
            "protein_contacts_within_6_angstrom": None,
            "coordinate_artifact_ref": None, "review_state": "pending",
        }],
    }
    output = {
        "systems": [item],
        "protocol_presets": [{
            "id": "openfe-rfe-standard-v1", "name": "OpenFE RFE Standard",
            "sampler": "replica exchange", "lambda_windows": 11,
            "equilibration": "1 ns", "production": "5 ns",
            "forcefields": "AMBER", "solvent": "NaCl 0.15 M",
        }],
        "required_sources": [
            "prepared_receptor_state_ref", "parent_pose_ref",
            "proposal_pose_ref"],
    }
    assert list(validator_for(schema).iter_errors(output)) == []
    missing_source = json.loads(json.dumps(output))
    del missing_source["systems"][0]["source_campaign_scientific_ref"]
    assert list(validator_for(schema).iter_errors(missing_source))
    audit_leak = json.loads(json.dumps(output))
    audit_leak["systems"][0]["source_campaign_scientific_ref"][
        "state_digest"] = DIGEST
    assert list(validator_for(schema).iter_errors(audit_leak))
    invalid_scope = json.loads(json.dumps(output))
    invalid_scope["systems"][0]["campaign_scope"] = "stale"
    assert list(validator_for(schema).iter_errors(invalid_scope))


def test_import_and_all_runset_outputs_require_scientific_currency_and_exact_execution_refs():
    commands = _commands()
    import_required = set(commands[
        "physics.rbfe-campaign.import-system"]["output_schema"]["required"])
    assert "state_digest" in import_required
    execution_ref_fields = {
        "edge_spec_ref", "edge_network_ref",
        "complex_transformation_ref", "solvent_transformation_ref",
    }
    for command_id in (
            "physics.rbfe-run.start", "physics.rbfe-run.get",
            "physics.rbfe-run.cancel", "physics.rbfe-run.retry"):
        output = commands[command_id]["output_schema"]
        assert {"edge_id", *execution_ref_fields}.issubset(output["required"])
        assert output["properties"]["edge_id"] == {
            "type": "string", "minLength": 1}
        for field in execution_ref_fields:
            reference = output["properties"][field]
            assert reference["additionalProperties"] is False
            assert set(reference["required"]) == {"kind", "id", "sha256"}
            assert reference["properties"]["kind"] == {"const": "artifact"}
            assert reference["properties"]["id"] == {
                "type": "string", "format": "uuid"}
            assert reference["properties"]["sha256"] == {
                "type": "string", "format": "sha256-digest"}
        assert "campaign_scientific_ref" in output["required"]
        science_ref = output["properties"]["campaign_scientific_ref"]
        assert science_ref["additionalProperties"] is False
        assert set(science_ref["required"]) == {
            "kind", "id", "version", "sha256"}
        valid = {
            "ref": {"kind": "run", "id": UUID},
            "state": "running", "jobs": {}, "edge_id": "edge-a-b",
            **{field: _artifact_ref() for field in execution_ref_fields},
            "campaign_scientific_ref": {
                "kind": "rbfe_campaign", "id": UUID, "version": 1,
                "sha256": DIGEST,
            },
        }
        assert list(validator_for(output).iter_errors(valid)) == []
        missing_digest = json.loads(json.dumps(valid))
        del missing_digest["edge_spec_ref"]["sha256"]
        assert list(validator_for(output).iter_errors(missing_digest))
        audit_leak = json.loads(json.dumps(valid))
        audit_leak["edge_network_ref"]["version"] = 7
        assert list(validator_for(output).iter_errors(audit_leak))


def test_system_prepare_command_requires_digest_verified_typed_refs():
    command = _commands()["physics.rbfe-system.prepare"]
    schema = command["input_schema"]
    valid = {
        "campaign_id": UUID,
        "campaign_scientific_generation": 1,
        "campaign_scientific_digest": DIGEST,
        "network_ref": _artifact_ref(),
        "edge_id": "edge-a-b",
        "prepared_receptor_state_ref": {
            "kind": "prepared_receptor_state", "id": UUID, "sha256": DIGEST,
        },
        "parent_pose_ref": {
            "kind": "pose_hypothesis", "id": UUID, "sha256": DIGEST,
        },
        "proposal_pose_ref": {
            "kind": "pose_hypothesis", "id": UUID, "sha256": DIGEST,
        },
        "protocol_preset": "openfe-rfe-standard-v1",
    }
    assert list(validator_for(schema).iter_errors(valid)) == []
    missing_digest = json.loads(json.dumps(valid))
    del missing_digest["parent_pose_ref"]["sha256"]
    assert list(validator_for(schema).iter_errors(missing_digest))
    wrong_kind = json.loads(json.dumps(valid))
    wrong_kind["prepared_receptor_state_ref"]["kind"] = "artifact"
    assert list(validator_for(schema).iter_errors(wrong_kind))

    method_schema = _method(
        "physics.motif.rbfe_system_prepare")["input"]["schema"]
    for name in ("campaign_id", "campaign_scientific_generation",
                 "campaign_scientific_digest",
                 "edge_id", "protocol_preset"):
        assert schema["properties"][name] == method_schema["properties"][name]
    for name in ("network_ref", "prepared_receptor_state_ref",
                 "parent_pose_ref", "proposal_pose_ref"):
        definition = method_schema["properties"][name]["$ref"].rsplit("/", 1)[1]
        assert schema["properties"][name] == method_schema["$defs"][definition]


def test_aggregate_and_runset_commands_refuse_naked_ids():
    commands = _commands()
    aggregate = commands["physics.rbfe-aggregate"]["input_schema"]
    aggregate_method = _method(
        "physics.motif.rbfe_aggregate")["input"]["schema"]
    artifact_ref = aggregate_method["$defs"]["artifact_ref"]
    assert aggregate["properties"]["network_ref"] == artifact_ref
    assert aggregate["properties"]["edge_spec_ref"] == artifact_ref
    aggregate_run_properties = aggregate["properties"]["runs"]["items"]["properties"]
    assert aggregate_run_properties["result_ref"] == artifact_ref
    assert aggregate_run_properties["run_report_ref"] == artifact_ref
    aggregate_input = {
        "network_ref": _artifact_ref(),
        "edge_spec_ref": _artifact_ref(),
        "runs": [
            {"result_ref": _artifact_ref(), "run_report_ref": _artifact_ref()}
            for _ in range(6)
        ],
    }
    assert list(validator_for(aggregate).iter_errors(aggregate_input)) == []
    del aggregate_input["runs"][0]["result_ref"]["sha256"]
    assert list(validator_for(aggregate).iter_errors(aggregate_input))

    run_start = commands["physics.rbfe-run.start"]["input_schema"]
    run_input = {
        "request_key": "request-1",
        "campaign_id": UUID,
        "campaign_scientific_generation": 1,
        "campaign_scientific_digest": DIGEST,
        "edge_spec_ref": _artifact_ref(),
        "edge_network_ref": _artifact_ref(),
        "complex_transformation_ref": _artifact_ref(),
        "solvent_transformation_ref": _artifact_ref(),
    }
    assert list(validator_for(run_start).iter_errors(run_input)) == []
    del run_input["complex_transformation_ref"]["sha256"]
    assert list(validator_for(run_start).iter_errors(run_input))


def test_pose_acceptance_is_a_human_attestation_not_a_service_claim():
    schema = _commands()["physics.rbfe-campaign.accept-poses"]["input_schema"]
    assert "reviewer" not in schema["properties"]
    receptor = schema["properties"]["prepared_receptor_state_ref"]
    pose = schema["properties"]["pose_refs"]["items"]
    assert receptor["required"] == ["kind", "id", "sha256"]
    assert pose["required"] == ["kind", "id", "sha256"]


def test_raw_rbfe_preflight_is_absent_from_every_public_registry():
    assert not (ROOT / "contracts/methods/physics.motif.rbfe_preflight.method.json").exists()
    golden = json.loads(
        (ROOT / "contracts/golden/public-registry-v2.json").read_text()
    )
    assert "physics.motif.rbfe_preflight" not in golden["methods"]
    generated = (ROOT / "contracts/generated/python/methods.py").read_text()
    assert "physics.motif.rbfe_preflight" not in generated
