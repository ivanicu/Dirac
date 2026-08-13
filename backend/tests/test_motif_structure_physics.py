from __future__ import annotations

import base64


def test_etkdg_conformer_ensemble_is_ranked_and_addressed():
    from motif.structure import generate_conformers
    report, sdf = generate_conformers("CCO", count=6, seed=23)
    assert report["generated_count"] >= 1
    assert report["conformers"][0]["relative_energy"] == 0
    assert report["sdf_sha256"].startswith("sha256:")
    assert b"MOTIF_ENERGY" in sdf


def test_vina_executes_pose_search_instead_of_returning_a_placeholder():
    from motif.docking import dock_vina
    receptor = (
        "ATOM      1  C   ILE A  39       3.060  12.040  22.770  "
        "1.00  0.00     0.243 C \n")
    report, poses = dock_vina(
        receptor, [{"id": "ethanol", "smiles": "CCO"}],
        center=[3, 12, 23], box_size=[10, 10, 10], seed=1,
        exhaustiveness=1, n_poses=1, cpu=1)
    score = report["results"][0]["poses"][0]
    assert score["affinity_kcal_mol"] < 0
    assert score["rmsd_lower_bound_angstrom"] == 0
    assert "intermolecular_energy_kcal_mol" in score
    assert b"MODEL" in poses
    assert "not binding free energy" in report["claim_boundary"]


def _openmm_fixture():
    import openmm
    from openmm import XmlSerializer, unit
    system = openmm.System()
    system.addParticle(12 * unit.amu)
    system.addParticle(12 * unit.amu)
    bonds = openmm.HarmonicBondForce()
    bonds.addBond(0, 1, .15 * unit.nanometer,
                  1000 * unit.kilojoule_per_mole / unit.nanometer**2)
    system.addForce(bonds)
    pdb = ("ATOM      1  C1  UNK A   1       0.000   0.000   0.000  1.00  0.00           C  \n"
           "ATOM      2  C2  UNK A   1       1.500   0.000   0.000  1.00  0.00           C  \n"
           "CONECT    1    2\nEND\n")
    return XmlSerializer.serialize(system), pdb


def test_openmm_checkpoint_restarts_on_reference_platform():
    from motif.physics import run_openmm_md
    system_xml, pdb = _openmm_fixture()
    first, artifacts = run_openmm_md(
        system_xml=system_xml, topology_pdb=pdb, steps=5,
        report_interval=2, seed=7, platform_name="Reference")
    assert not first["resumed"]
    assert artifacts["md.trajectory"]
    second, resumed_artifacts = run_openmm_md(
        system_xml=system_xml, topology_pdb=pdb, steps=3,
        report_interval=1, seed=7, platform_name="Reference", minimize=False,
        checkpoint_base64=base64.b64encode(artifacts["md.checkpoint"]).decode())
    assert second["resumed"]
    assert resumed_artifacts["md.checkpoint"] != artifacts["md.checkpoint"]


def test_rbfe_network_and_weighted_aggregation_keep_failures_visible():
    from motif.rbfe import aggregate_rbfe_results, plan_rbfe_network
    network = plan_rbfe_network([
        {"id": "a", "smiles": "CCO"}, {"id": "b", "smiles": "CCN"},
        {"id": "c", "smiles": "CCC"}], extra_edge_fraction=1)
    observations = []
    for index, edge in enumerate(network["edges"]):
        observations.append({"edge_id": edge["edge_id"], "status": "completed",
                             "ddg_kcal_mol": float(index + 1),
                             "uncertainty_kcal_mol": .2})
    result = aggregate_rbfe_results(network, observations)
    assert result["status"] == "complete"
    assert len(result["node_estimates"]) == 3
    partial = aggregate_rbfe_results(network, [
        observations[0], {"edge_id": network["edges"][1]["edge_id"],
                          "status": "failed", "reason": "engine_failed"}])
    assert partial["status"] == "partial"
    assert partial["failed_edges"][0]["reason"] == "engine_failed"


def test_official_openfe_full_network_has_mapper_disagreement_and_24_execution_floor():
    from motif.rbfe import plan_rbfe_network
    network = plan_rbfe_network([
        {"id": "ethanol", "smiles": "CCO"},
        {"id": "ethylamine", "smiles": "CCN"},
        {"id": "propane", "smiles": "CCC"},
        {"id": "chloroethane", "smiles": "CCCl"},
    ], mode="full", planner="openfe")
    assert network["official_openfe_plan"]["engine"] == "OpenFE"
    assert network["execution_matrix"]["production_execution_count"] >= 24
    assert network["execution_matrix"]["pilot_included_in_production_count"] is False
    assert all(len(edge["mapping_methods"]) >= 2 for edge in network["edges"])
    assert all(edge["mapping_disagreement_jaccard"] is not None
               for edge in network["edges"])


def test_chemistry_gate_exposes_properties_stereo_and_reactivity():
    from rdkit import Chem
    from motif.proposals import chemistry_gate
    result = chemistry_gate(Chem.MolFromSmiles("CC(=O)Cl"), {
        "molecular_weight_range": [0, 100], "clogp_range": [-5, 5],
        "reactive_group_filters": ["acyl_halide"], "pains": True,
        "reject_unassigned_stereo": True,
    })
    assert result["status"] == "refuse"
    assert "REACTIVE_GROUP" in result["reason_codes"]
    assert result["details"]["molecular_weight"] > 0


def test_external_learned_and_rbfe_engines_are_fail_closed():
    import pytest
    from motif.optional_adapter_gates import (validate_diffdock_manifest,
                                              validate_openfe_manifest)
    with pytest.raises(ValueError, match="pinned"):
        validate_diffdock_manifest({
            "adapter": "diffdock", "version": "1", "container_image": "diffdock:latest",
            "license_artifact_digest": "sha256:" + "a" * 64,
            "checkpoint_digest": "sha256:" + "b" * 64,
            "entrypoint": "/opt/diffdock/run", "validated_fixture_digest": "sha256:" + "c" * 64})
    manifest = validate_openfe_manifest({
        "adapter": "openfe", "version": "1",
        "container_image": "registry/openfe@sha256:" + "d" * 64,
        "license_artifact_digest": "sha256:" + "a" * 64,
        "checkpoint_digest": "sha256:" + "b" * 64,
        "entrypoint": "/opt/openfe/run", "validated_fixture_digest": "sha256:" + "c" * 64})
    assert manifest["ready"]
