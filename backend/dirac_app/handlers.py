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
    return _job_ref(ctx.kernel.get_job(input['job_ref']['id']))


def job_list(input: dict, ctx) -> dict:
    return {'jobs': [_job_ref(j) for j in ctx.kernel.list_jobs(
        state=input.get('state'), limit=input.get('limit', 100))]}


def attention_list(input: dict, ctx) -> dict:
    return {'items': ctx.kernel.list_attention(limit=input.get('limit', 100))}


def job_wait(input: dict, ctx) -> dict:
    return _job_ref(ctx.kernel.wait_job(
        input['job_ref']['id'], timeout=input.get('timeout', 300)))


def job_cancel(input: dict, ctx) -> dict:
    return _job_ref(ctx.kernel.cancel_job(input['job_ref']['id']))


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


def physics_rbfe_network(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'physics.motif.rbfe_network', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


def physics_rbfe_aggregate(input: dict, ctx) -> dict:
    return ctx.kernel.submit(
        'physics.motif.rbfe_aggregate', input, request_id=ctx.request_id,
        actor=ctx.actor, command_id=ctx.command_id)


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
    return _motif_governance(ctx).ingest_measurements(input["measurements"], ctx.actor)


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


def program_snapshot_create(input: dict, ctx) -> dict:
    return _programs(ctx).create_snapshot(
        input["program_ref"], input["expected_version"], ctx.actor, ctx.request_id)


_REFERENCE_JOB_BY_COMMAND = {
    "program.target_disease.link": "target_disease",
    "identity.substance_registration.record": "substance_registration",
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
