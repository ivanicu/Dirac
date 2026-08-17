"""Application command handlers. No HTTP and no transport-specific shapes."""
from __future__ import annotations

import hashlib
import importlib.metadata
from typing import Any

import failures


def _ref(kind: str, identifier: Any) -> dict[str, str]:
    return {'kind': kind, 'id': str(identifier)}


def _job_ref(row: dict) -> dict:
    return {**row, 'ref': _ref('job', row['id'])}


def unavailable(_input: dict, _ctx) -> dict:
    raise failures.DiracUnsupported(
        'this command is registered for discovery but its application handler is not '
        'available in this build', details={'availability': 'registered-unavailable'})


def system_health(_input: dict, ctx) -> dict:
    return {'status': 'ok', 'capabilities': ctx.kernel.capabilities()}


def system_capabilities(_input: dict, ctx) -> dict:
    return ctx.kernel.capabilities()


def system_version(_input: dict, _ctx) -> dict:
    try:
        version = importlib.metadata.version('dirac-sdk')
    except importlib.metadata.PackageNotFoundError:
        version = 'workspace'
    return {'api': '2.0.0', 'application': version,
            'command_contract': '1.0.0'}


def method_list(_input: dict, ctx) -> dict:
    return {'methods': ctx.kernel.list_methods()}


def method_describe(input: dict, ctx) -> dict:
    return ctx.kernel.describe(input['method_id'])


def method_estimate(input: dict, ctx) -> dict:
    return ctx.kernel.estimate(input['method_id'], input['input'])


def job_get(input: dict, ctx) -> dict:
    return _job_ref(ctx.kernel.get_job(
        input['job_ref']['id'], actor=ctx.actor))


def job_list(input: dict, ctx) -> dict:
    return {'jobs': [_job_ref(j) for j in ctx.kernel.list_jobs(
        actor=ctx.actor, state=input.get('state'),
        limit=input.get('limit', 100))]}


def attention_list(input: dict, ctx) -> dict:
    return {'items': ctx.kernel.list_attention(
        actor=ctx.actor, limit=input.get('limit', 100))}


def job_wait(input: dict, ctx) -> dict:
    return _job_ref(ctx.kernel.wait_job(
        input['job_ref']['id'], actor=ctx.actor,
        timeout=input.get('timeout', 300)))


def job_cancel(input: dict, ctx) -> dict:
    return _job_ref(ctx.kernel.cancel_job(
        input['job_ref']['id'], actor=ctx.actor))


def _rdkit_molecule(value: dict):
    from rdkit import Chem
    if value.get('kind') == 'molfile' or value.get('content'):
        mol = Chem.MolFromMolBlock(value.get('content', ''), removeHs=False)
    else:
        mol = Chem.MolFromSmiles(value.get('smiles', ''))
    if mol is None:
        raise failures.DiracParseFailure('cannot parse molecule input')
    return mol


def molecule_describe(input: dict, _ctx) -> dict:
    from rdkit import Chem
    from rdkit.Chem import rdMolDescriptors
    mol = _rdkit_molecule(input['molecule'])
    canonical = Chem.MolToSmiles(Chem.RemoveHs(mol), canonical=True)
    identity = hashlib.sha256(canonical.encode()).hexdigest()
    return {'molecule_ref': _ref('molecule', f'mol_{identity[:24]}'),
            'canonical_smiles': canonical,
            'formula': rdMolDescriptors.CalcMolFormula(mol),
            'heavy_atoms': mol.GetNumHeavyAtoms(), 'atoms': mol.GetNumAtoms(),
            'formal_charge': Chem.rdmolops.GetFormalCharge(mol)}


def molecule_properties(input: dict, _ctx) -> dict:
    from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors
    mol = _rdkit_molecule(input['molecule'])
    values = {'molecular_weight': Descriptors.MolWt(mol),
              'clogp': Crippen.MolLogP(mol),
              'tpsa': rdMolDescriptors.CalcTPSA(mol),
              'hbd': Lipinski.NumHDonors(mol),
              'hba': Lipinski.NumHAcceptors(mol),
              'rotatable_bonds': Lipinski.NumRotatableBonds(mol)}
    digest = hashlib.sha256(repr(sorted(values.items())).encode()).hexdigest()
    return {'prediction_ref': _ref('prediction', f'pred_{digest[:24]}'),
            'values': values, 'method': {'name': 'RDKit descriptors'}}


def conformer_generate(input: dict, ctx) -> dict:
    payload = {}
    if input.get('smiles'):
        payload['smiles'] = input['smiles']
    elif input.get('molecule', {}).get('content'):
        payload['molfile'] = input['molecule']['content']
    else:
        raise failures.DiracInvalidParameters(
            'conformer.generate requires smiles or molecule.content')
    if input.get('parameters'):
        payload['parameters'] = input['parameters']
    return ctx.kernel.invoke(
        'molecule.embed', payload, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def dataset_snapshot_create(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'data.motif.snapshot', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def campaign_rank(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'design.motif.acquire', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def model_train(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'ml.motif.train', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def model_mesh_train(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'ml.motif.mesh.train', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def model_mesh_predict(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'ml.motif.mesh.predict', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def campaign_bayesian_rank(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'design.motif.bayesian_acquire', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def structure_conformers(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'structure.motif.conformers', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def structure_vina(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'structure.motif.vina', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def physics_openmm_md(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'physics.motif.openmm_md', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def physics_openfe_edge(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'physics.motif.openfe_edge', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def physics_rbfe_network(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'physics.motif.rbfe_network', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def _rbfe_references(ctx):
    resolver = getattr(ctx.kernel, "rbfe_reference_resolver", None)
    if resolver is None:
        raise failures.DiracFailure(
            "DB_UNAVAILABLE", "registered protein-system catalog is unavailable")
    return resolver


def physics_rbfe_system_list(input: dict, ctx) -> dict:
    resolver = _rbfe_references(ctx)
    return {
        "systems": resolver.list_systems(
            ctx.actor,
            campaign_id=input.get("campaign_id"),
            include_importable=bool(input.get("include_importable", False))),
        "protocol_presets": [{
            "id": "openfe-rfe-standard-v1",
            "name": "OpenFE RFE Standard",
            "sampler": "replica exchange",
            "lambda_windows": 11,
            "equilibration": "1 ns",
            "production": "5 ns",
            "forcefields": "AMBER ff14SB · OpenFF 2.2.1 · TIP3P",
            "solvent": "NaCl 0.15 M",
        }],
        "required_sources": ["prepared_receptor_state_ref", "parent_pose_ref",
                             "proposal_pose_ref"],
    }


def physics_rbfe_campaign_save(input: dict, ctx) -> dict:
    return _rbfe_references(ctx).save_campaign(input, ctx.actor)


def physics_rbfe_campaign_get(input: dict, ctx) -> dict:
    return _rbfe_references(ctx).get_campaign(input["campaign_id"], ctx.actor)


def physics_rbfe_campaign_list(input: dict, ctx) -> dict:
    del input
    return {"campaigns": _rbfe_references(ctx).list_campaigns(ctx.actor)}


def physics_rbfe_campaign_invalidate(input: dict, ctx) -> dict:
    return _rbfe_references(ctx).invalidate_campaign(
        input["campaign_id"], input["expected_version"], input["reason"],
        input["changed_domains"], ctx.actor)


def physics_rbfe_campaign_import_system(input: dict, ctx) -> dict:
    return _rbfe_references(ctx).import_system(
        input["campaign_id"], input["prepared_receptor_state_ref"], ctx.actor,
        expected_version=input["expected_version"], reason=input["reason"])


def physics_rbfe_campaign_prepare(input: dict, ctx) -> dict:
    """Queue server-owned receptor and pose preparation as a durable Job.

    Preparation can invoke native chemistry tools for minutes.  Running that work in
    the command request made a closed browser indistinguishable from a failed
    campaign.  The method owns the resolver/store capabilities; this command only
    mints the reconnectable handle.
    """
    return ctx.kernel.submit(
        'physics.motif.rbfe_campaign_prepare', input,
        request_id=ctx.request_id, actor=ctx.actor,
        command_id=ctx.command_id)


def physics_rbfe_campaign_accept_poses(input: dict, ctx) -> dict:
    resolver = _rbfe_references(ctx)
    return resolver.accept_poses(input, ctx.actor)


def physics_rbfe_system_prepare(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'physics.motif.rbfe_system_prepare', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def physics_rbfe_aggregate(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'physics.motif.rbfe_aggregate', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def _rbfe_runsets(ctx):
    controller = getattr(ctx.kernel, "rbfe_runset_controller", None)
    if controller is None:
        raise failures.DiracFailure(
            "DB_UNAVAILABLE", "durable RBFE RunSet controller is unavailable")
    return controller


def physics_rbfe_run_start(input: dict, ctx) -> dict:
    return _rbfe_runsets(ctx).start(input, ctx.actor)


def physics_rbfe_run_get(input: dict, ctx) -> dict:
    return _rbfe_runsets(ctx).get(input["run_ref"]["id"], ctx.actor)


def physics_rbfe_run_cancel(input: dict, ctx) -> dict:
    return _rbfe_runsets(ctx).cancel(input["run_ref"]["id"], ctx.actor)


def physics_rbfe_run_retry(input: dict, ctx) -> dict:
    return _rbfe_runsets(ctx).retry(input["run_ref"]["id"], ctx.actor)


def proposal_generate(input: dict, ctx) -> dict:
    strategy = input["strategy"]
    method_id = ("design.motif.local_edits" if strategy == "local_edit"
                 else "design.motif.reaction_enumerate")
    payload = dict(input)
    payload.pop("strategy")
    return ctx.kernel.submit(
        method_id, payload, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def _motif_governance(ctx):
    store = getattr(ctx.kernel, "motif_governance", None)
    if store is None:
        raise failures.DiracFailure(
            "DB_UNAVAILABLE",
            "Motif governance Commands require the durable PostgreSQL repository; "
            "this kernel was assembled without it",
        )
    return store


def endpoint_register(input: dict, ctx) -> dict:
    return _motif_governance(ctx).register_endpoint(input["definition"], ctx.actor)


def objective_save(input: dict, ctx) -> dict:
    return _motif_governance(ctx).save_objective(input["objective"], ctx.actor)


def policy_release_register(input: dict, ctx) -> dict:
    return _motif_governance(ctx).register_policy(input["release"], ctx.actor)


def result_ingest(input: dict, ctx) -> dict:
    controller = getattr(ctx.kernel, "closed_loop_controller", None)
    loop_spec = input.get("closed_loop")
    if loop_spec is not None:
        if controller is None:
            raise failures.DiracFailure(
                "DB_UNAVAILABLE", "closed-loop controller is unavailable")
        controller.validate_spec(loop_spec, input["measurements"])
        controller.validate_context(loop_spec)
    result = _motif_governance(ctx).ingest_measurements(
        input["measurements"], ctx.actor)
    if loop_spec is not None:
        result["closed_loop"] = controller.enqueue(
            spec=loop_spec, measurements=input["measurements"],
            ingest_result=result, actor=ctx.actor)
    return result


def campaign_closed_loop_get(input: dict, ctx) -> dict:
    controller = getattr(ctx.kernel, "closed_loop_controller", None)
    if controller is None:
        raise failures.DiracFailure(
            "DB_UNAVAILABLE", "closed-loop controller is unavailable")
    return controller.get(input["run_ref"]["id"])


def campaign_closed_loop_retry(input: dict, ctx) -> dict:
    controller = getattr(ctx.kernel, "closed_loop_controller", None)
    if controller is None:
        raise failures.DiracFailure(
            "DB_UNAVAILABLE", "closed-loop controller is unavailable")
    return controller.retry(input["run_ref"]["id"])


def motif_plan(input: dict, _ctx) -> dict:
    """Plan the next scientific action; fidelity labels are descriptive only."""
    from motif.action_planner import PlannerPolicy, plan_actions

    raw = input["policy"]
    policy = PlannerPolicy(
        policy_release_id=raw["policy_release_id"],
        utility_contract_id=raw["utility_contract_id"],
        outcome_model_release_id=raw["outcome_model_release_id"],
        cost_model_release_id=raw["cost_model_release_id"],
        resource_prices=raw["resource_prices"],
        max_iterations=raw["max_iterations"],
        max_actions_per_subject_question=raw["max_actions_per_subject_question"],
        minimum_net_value=raw.get("minimum_net_value", 0.0),
    )
    return plan_actions(
        evidence_snapshot_ref=input["evidence_snapshot_ref"],
        current_utilities=input["current_utilities"],
        candidates=input["candidate_actions"],
        remaining_budget=input["remaining_budget"], policy=policy,
        iteration=input["iteration"], action_history=input.get("action_history", []),
    )


def motif_validate(input: dict, _ctx) -> dict:
    """Validate one document against an allow-listed Motif machine contract."""
    import json
    from pathlib import Path
    from contracts.validation import violations

    allowed = {
        "scientific-object", "chemical-state-ensemble", "orthogonal-state",
        "method-outcome", "evidence-item", "routing-action", "structured-error",
        "method-manifest", "resource-lease", "model-validation",
    }
    schema_name = input["schema"]
    if schema_name not in allowed:
        raise failures.DiracInvalidParameters(
            "unknown Motif validation schema", details={"schema": schema_name,
                                                        "allowed": sorted(allowed)})
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "contracts/domain/motif" /
                         f"{schema_name}.schema.json").read_text())
    problems = [problem.to_dict() for problem in violations(schema, input["document"])]
    return {"schema": schema_name, "valid": not problems, "violations": problems}


def motif_explain(input: dict, _ctx) -> dict:
    plan = input["plan"]
    selected = plan.get("selected_action")
    return {
        "decision": plan.get("decision"),
        "reason_codes": plan.get("reason_codes", []),
        "selected": None if selected is None else {
            "action_kind": selected["action_kind"],
            "subject_ref": selected["subject_ref"],
            "scientific_question": selected["scientific_question"],
            "expected_utility_delta": selected["expected_utility_delta"],
            "priced_resource_cost": selected["priced_resource_cost"],
            "expected_net_value": selected["expected_net_value"],
        },
        "excluded": plan.get("excluded", []),
        "candidate_count": len(plan.get("ranked_candidates", [])),
        "p_decision_change_is_diagnostic_only": True,
    }


def _programs(ctx):
    repository = getattr(ctx.kernel, "program_repository", None)
    if repository is None:
        raise failures.DiracFailure(
            "DB_UNAVAILABLE",
            "Program Commands require the durable PostgreSQL repository; "
            "this kernel was assembled without it",
        )
    return repository


def program_create(input: dict, ctx) -> dict:
    return _programs(ctx).create(input["program"], ctx.actor, ctx.request_id)


def program_get(input: dict, ctx) -> dict:
    return _programs(ctx).get(input["program_ref"])


def program_list(input: dict, ctx) -> dict:
    return _programs(ctx).list(lifecycle=input.get("lifecycle"), limit=input.get("limit", 100))


def portfolio_create(input: dict, ctx) -> dict:
    return _programs(ctx).create_portfolio(input["portfolio"], ctx.actor, ctx.request_id)


def portfolio_list(input: dict, ctx) -> dict:
    return _programs(ctx).list_portfolios(limit=input.get("limit", 100))


def program_update(input: dict, ctx) -> dict:
    return _programs(ctx).update(input["program_ref"], input["expected_version"],
                                 input["patch"], ctx.actor, ctx.request_id)


def program_objective_record(input: dict, ctx) -> dict:
    return _programs(ctx).record_objective(
        input["program_ref"], input["expected_version"], input["objective"],
        ctx.actor, ctx.request_id)


def program_hypothesis_record(input: dict, ctx) -> dict:
    return _programs(ctx).record_hypothesis(
        input["program_ref"], input["expected_version"], input["hypothesis"],
        ctx.actor, ctx.request_id)


def program_decision_record(input: dict, ctx) -> dict:
    return _programs(ctx).record_decision(
        input["program_ref"], input["expected_version"], input["decision"],
        ctx.actor, ctx.request_id)


def program_milestone_record(input: dict, ctx) -> dict:
    return _programs(ctx).record_milestone(
        input["program_ref"], input["expected_version"], input["milestone"],
        ctx.actor, ctx.request_id)


def program_portfolio_assign(input: dict, ctx) -> dict:
    return _programs(ctx).assign_portfolio(
        input["program_ref"], input["expected_version"], input["portfolio_ref"],
        ctx.actor, ctx.request_id)


def program_member_assign(input: dict, ctx) -> dict:
    return _programs(ctx).assign_member(
        input["program_ref"], input["expected_version"], input["member"],
        ctx.actor, ctx.request_id)


def program_stage_gate_record(input: dict, ctx) -> dict:
    return _programs(ctx).record_stage_gate(
        input["program_ref"], input["expected_version"], input["stage_gate"],
        ctx.actor, ctx.request_id)


def program_work_package_record(input: dict, ctx) -> dict:
    return _programs(ctx).record_work_package(
        input["program_ref"], input["expected_version"], input["work_package"],
        ctx.actor, ctx.request_id)


def program_work_item_transition(input: dict, ctx) -> dict:
    return _programs(ctx).transition_work_item(
        input["program_ref"], input["expected_version"], input["transition"],
        ctx.actor, ctx.request_id)


def program_work_execution_attach(input: dict, ctx) -> dict:
    return _programs(ctx).attach_work_execution(
        input["program_ref"], input["expected_version"], input["execution"],
        ctx.actor, ctx.request_id)


def program_evidence_attach(input: dict, ctx) -> dict:
    return _programs(ctx).attach_evidence(
        input["program_ref"], input["expected_version"], input["binding"],
        ctx.actor, ctx.request_id)


def program_lineage_record(input: dict, ctx) -> dict:
    return _programs(ctx).record_lineage(
        input["program_ref"], input["expected_version"], input["lineage"],
        ctx.actor, ctx.request_id)


def program_health_get(input: dict, ctx) -> dict:
    return _programs(ctx).health(input["program_ref"])


def program_link(input: dict, ctx) -> dict:
    return _programs(ctx).link(
        input["program_ref"], input["expected_version"], input["object_ref"],
        input["role"], input.get("rationale"), ctx.actor, ctx.request_id)


def compound_register(input: dict, ctx) -> dict:
    """Standardize a designed molecule once, then link that canonical Compound."""
    import rdkit
    from rdkit import Chem
    from rdkit.Chem import Descriptors, rdMolDescriptors
    from rdkit.Chem.MolStandardize import rdMolStandardize

    raw = Chem.MolFromSmiles(input["smiles"])
    if raw is None:
        raise failures.DiracParseFailure("cannot parse designed molecule SMILES")
    try:
        parent = rdMolStandardize.Cleanup(raw)
        parent = rdMolStandardize.FragmentParent(parent)
        parent = rdMolStandardize.Uncharger().uncharge(parent)
        parent = rdMolStandardize.TautomerEnumerator().Canonicalize(parent)
        Chem.AssignStereochemistry(parent, cleanIt=True, force=True)
    except Exception as error:  # noqa: BLE001 - convert toolkit failure to contract error
        raise failures.DiracInvalidParameters(
            "molecule standardization failed", details={"reason": str(error)}) from error
    centers = Chem.FindMolChiralCenters(
        parent, includeUnassigned=True, useLegacyImplementation=False)
    unassigned = sum(1 for _, tag in centers if tag == "?")
    stereo = ("no_stereocenters" if not centers else "fully_defined" if not unassigned
              else "undefined" if unassigned == len(centers) else "partially_defined")
    inchikey = Chem.MolToInchiKey(parent)
    inchi = Chem.MolToInchi(parent)
    if not inchikey or not inchi:
        raise failures.DiracParseFailure("standardized molecule has no InChI identity")
    compound = {
        "inchikey": inchikey, "inchi": inchi,
        "smiles": Chem.MolToSmiles(parent, canonical=True),
        "formula": rdMolDescriptors.CalcMolFormula(parent),
        "mw_monoisotopic": Descriptors.ExactMolWt(parent),
        "net_charge": Chem.GetFormalCharge(parent), "stereo": stereo,
        "standardizer": {"label": "dirac-parent-v1", "toolkit": "rdkit",
                         "version": rdkit.__version__,
                         "rules": ["Cleanup", "FragmentParent", "Uncharger",
                                   "TautomerCanonicalize", "AssignStereochemistry"]},
        "is_virtual": True,
    }
    return _programs(ctx).register_compound(
        input["program_ref"], input["expected_version"], compound,
        input.get("role", "design-candidate"), input.get("rationale"),
        ctx.actor, ctx.request_id)


def program_snapshot_create(input: dict, ctx) -> dict:
    return _programs(ctx).create_snapshot(
        input["program_ref"], input["expected_version"], ctx.actor, ctx.request_id)


_REFERENCE_JOB_BY_COMMAND = {
    "program.target_disease.link": "target_disease",
    "identity.substance_registration.record": "substance_registration",
    "material.batch.register": "batch",
    "sample.create": "sample",
    "sample.transfer": "sample_transfer",
    "program.work_comment.record": "work_comment",
    "program.work_attachment.record": "work_attachment",
    "program.gate_criterion.assess": "gate_criterion",
    "protocol.version.record": "protocol_version",
    "dataset.version.commit": "dataset_version",
    "experiment.record": "experiment",
    "structure.observation.register": "structure_observation",
    "structure.annotation.record": "annotation",
    "structure.review.record": "review",
    "structure.analysis_snapshot.create": "analysis_snapshot",
    "evidence.release.import": "evidence_release",
    "evidence.external.record": "external_evidence",
}


def program_reference_job_record(input: dict, ctx) -> dict:
    kind = _REFERENCE_JOB_BY_COMMAND.get(ctx.command_id)
    if kind is None:
        raise failures.DiracInvalidParameters("unknown Program reference-job command")
    return _programs(ctx).record_reference_job(
        input["program_ref"], input["expected_version"], kind, input["record"],
        ctx.actor, ctx.request_id)


def structure_field_compute(input: dict, ctx) -> dict:
    kind = input['field_kind']
    method_id = (f'fields.{kind}' if kind in ('mep', 'mlp')
                 else f'fields.qm.{kind}')
    payload = {'molecule': input['molecule']}
    if input.get('parameters'):
        payload['parameters'] = input['parameters']
    return ctx.kernel.submit(method_id, payload,
                             budget_seconds=input.get('budget_seconds'),
                             request_id=ctx.request_id, actor=ctx.actor,
                             command_id=ctx.command_id)


def structure_surface_compute(input: dict, ctx) -> dict:
    payload = {'molecule': input['molecule']}
    if input.get('parameters'):
        payload['parameters'] = input['parameters']
    return ctx.kernel.submit('surface.mep', payload,
                             budget_seconds=input.get('budget_seconds'),
                             request_id=ctx.request_id, actor=ctx.actor,
                             command_id=ctx.command_id)


def structure_torsion_analyze(input: dict, ctx) -> dict:
    payload = {'molecule': input['molecule']}
    if input.get('parameters'):
        payload['parameters'] = input['parameters']
    return ctx.kernel.submit('torsion.strain', payload,
                             request_id=ctx.request_id, actor=ctx.actor,
                             command_id=ctx.command_id)
