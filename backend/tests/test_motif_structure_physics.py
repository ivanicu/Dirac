from __future__ import annotations

import base64


def _validated_rbfe_legs(edge_id: str, ddg: float, *, repeats: int = 3,
                         uncertainty: float = .1) -> list[dict]:
    from motif.rbfe import ingest_openfe_edge_result
    rows = []
    for repeat in range(1, repeats + 1):
        for leg, estimate in (("complex", ddg + .5), ("solvent", .5)):
            result = {
                "engine": "OpenFE", "scientific_status": "completed_unvalidated",
                "edge_id": edge_id, "leg": leg, "repeat_index": repeat,
                "target_ref": {"kind": "target", "id": "target-1"},
                "protein_structure_ref": ({"kind": "structure", "id": "pose-1"}
                                          if leg == "complex" else None),
                "thermodynamic_cycle_id": "cycle-1",
                "ligand_charge_digest": "sha256:" + "1" * 64,
                "transformation_digest": "sha256:" + ("2" if leg == "complex" else "3") * 64,
                "result_digest": "sha256:" + f"{repeat}{'4' if leg == 'complex' else '5'}".ljust(64, "0"),
                "estimate": estimate, "uncertainty": uncertainty, "unit": "kcal/mol",
            }
            rows.append(ingest_openfe_edge_result(result, {
                "verdict": "passed", "policy_digest": "sha256:" + "6" * 64,
                "diagnostics_digest": "sha256:" + f"{repeat}7".ljust(64, "0"),
                "effective_samples": 1000, "minimum_overlap": .2,
            }))
    return rows


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
    rejected_network = plan_rbfe_network([
        {"id": "a", "smiles": "CCO"}, {"id": "b", "smiles": "CCN"},
        {"id": "c", "smiles": "CCC"}], extra_edge_fraction=1)
    result = aggregate_rbfe_results(rejected_network, [])
    # The chemistry-aware planner refuses to promote a disconnected executable
    # graph.  Candidate/rejected mappings remain visible, but aggregation cannot
    # manufacture a complete network result from zero executable edges.
    assert rejected_network["execution_network_gate"]["verdict"] == "UNVERIFIED"
    assert result["status"] == "partial_unattested"
    assert result["release_eligible"] is False
    # No executable observations means there is no identifiable node estimate;
    # returning three zero-like rows would be a fabricated scientific result.
    assert result["node_estimates"] == []

    network = plan_rbfe_network([
        {"id": "a", "smiles": "c1ccccc1"},
        {"id": "b", "smiles": "Fc1ccccc1"},
        {"id": "c", "smiles": "Clc1ccccc1"},
    ], extra_edge_fraction=1)
    assert network["execution_network_gate"]["verdict"] == "UNVERIFIED"
    assert network["candidate_edges"]
    assert all(edge["execution_eligibility"]["verdict"] == "UNVERIFIED"
               for edge in network["edges"])
    partial = aggregate_rbfe_results(network, _validated_rbfe_legs(
        network["edges"][0]["edge_id"], 1.0))
    assert partial["status"] == "partial_unattested"
    assert partial["release_eligible"] is False
    assert set(partial["missing_edge_ids"]) == {
        edge["edge_id"] for edge in network["edges"][1:]}


def test_rbfe_network_output_contract_rejects_missing_or_untyped_summary():
    import pytest
    import catalog
    import failures

    method_id = "physics.motif.rbfe_network"
    complete = {
        "network_digest": "sha256:" + "a" * 64,
        "compound_count": 2,
        "edge_count": 0,
        "network": {
            "schema_version": "1.0", "kind": "rbfe_network_plan",
            "digest": "sha256:" + "a" * 64,
            "compounds": [
                {"id": "a", "canonical_smiles": "CC"},
                {"id": "b", "canonical_smiles": "CCC"},
            ],
            "edges": [], "mode": "pilot", "official_openfe_plan": None,
            "policy": {
                "extra_edge_fraction": .35,
                "minimum_similarity": .15,
                "mapping": "RDKit FMCS fallback",
                "planner": "rdkit_fallback",
            },
            "claim_boundary": "mapping plan only",
            "campaign_admission": {
                "schema_version": "rbfe-network-admission.v1",
                "verdict": "UNBOUND", "scope": "smoke_plan",
                "campaign_bound": False,
            },
        },
    }
    registry = catalog.default_catalog()
    registry.validate_output(method_id, complete)
    for malformed in (
        {key: value for key, value in complete.items() if key != "network_digest"},
        {**complete, "edge_count": "0"},
        {**complete, "network": {**complete["network"], "kind": "result"}},
        {**complete, "unexpected": True},
    ):
        with pytest.raises(failures.DiracInternal):
            registry.validate_output(method_id, malformed)


def test_rbfe_rejects_direct_edge_observation_passthrough():
    import pytest
    from motif.rbfe import aggregate_rbfe_results
    network = {"digest": "sha256:" + "a" * 64,
               "compounds": [{"id": "a"}, {"id": "b"}],
               "edges": [{"edge_id": "e1", "left_id": "a", "right_id": "b"}]}
    with pytest.raises(ValueError, match="direct edge observations"):
        aggregate_rbfe_results(network, [{
            "edge_id": "e1", "status": "completed", "ddg_kcal_mol": 1.0,
            "uncertainty_kcal_mol": .2,
        }])


def test_rbfe_rejects_tampered_receipt_and_single_repeat_cannot_complete():
    import pytest
    from motif.rbfe import aggregate_rbfe_results
    network = {"digest": "sha256:" + "a" * 64,
               "compounds": [{"id": "a"}, {"id": "b"}],
               "edges": [{"edge_id": "e1", "left_id": "a", "right_id": "b"}]}
    tampered = _validated_rbfe_legs("e1", 1.0)
    tampered[0]["dg_kcal_mol"] = 999.0
    with pytest.raises(ValueError, match="checksum"):
        aggregate_rbfe_results(network, tampered)
    single = _validated_rbfe_legs("e1", 1.0, repeats=1)
    result = aggregate_rbfe_results(network, single)
    assert result["status"] == "partial_unattested"
    assert result["release_eligible"] is False
    assert result["node_estimates"] == []
    assert result["missing_edge_ids"] == ["e1"]


def test_public_rbfe_aggregate_route_fails_closed_without_server_owned_refs():
    import pytest
    pytest.importorskip("jsonschema")
    import failures
    from invocation import InvocationContext
    from motif.structure_methods import rbfe_aggregate_handler
    with pytest.raises(failures.DiracUnsupported, match="server-owned network artifact"):
        rbfe_aggregate_handler({
            "network_ref": {"kind": "artifact", "id": "n", "sha256": "sha256:" + "1" * 64},
            "validated_leg_refs": [], "convergence_refs": [],
        }, InvocationContext(method_id="physics.motif.rbfe_aggregate"))


def test_official_openfe_full_network_has_mapper_disagreement_and_24_execution_floor():
    import copy
    import catalog
    import failures
    import pytest
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
    assert all(edge["mapped_heavy_atom_count"] > 0 for edge in network["edges"])
    assert all(edge["mapping_disagreement_all_atoms_jaccard"] is not None
               for edge in network["edges"])
    assert any(edge["mapping_disagreement_all_atoms_jaccard"]
               != edge["mapping_disagreement_jaccard"]
               for edge in network["edges"])

    # The public handler always seals one admission verdict into the immutable
    # network. Reuse this real planner object to prove the descriptor accepts the
    # exact producer shape and rejects malformed nested evidence rather than only
    # checking the four summary fields.
    network["campaign_admission"] = {
        "schema_version": "rbfe-network-admission.v1",
        "verdict": "UNBOUND", "scope": "smoke_plan",
        "campaign_bound": False,
    }
    complete = {
        "network_digest": network["digest"],
        "compound_count": len(network["compounds"]),
        "edge_count": len(network["edges"]),
        "network": network,
    }
    registry = catalog.default_catalog()
    registry.validate_output("physics.motif.rbfe_network", complete)

    malformed_outputs = []
    malformed = copy.deepcopy(complete)
    malformed["network"]["edges"][0]["chemistry_evidence"]["verdict"] = "green"
    malformed_outputs.append(malformed)
    malformed = copy.deepcopy(complete)
    malformed["network"]["edges"][0]["chemistry_evidence"]["ledger"][0][
        "witnesses"] = [{"invented_evidence": True}]
    malformed_outputs.append(malformed)
    malformed = copy.deepcopy(complete)
    malformed["network"]["edges"][0]["chemistry_evidence"]["ledger"][0][
        "witnesses"] = [{"parent": None}]
    malformed_outputs.append(malformed)
    malformed = copy.deepcopy(complete)
    malformed["network"]["edges"][0]["chemistry_evidence"]["ledger"][0][
        "witnesses"] = [{"parent_cycle_rank": 0, "proposal_cycle_rank": 0}]
    malformed_outputs.append(malformed)
    malformed = copy.deepcopy(complete)
    ledger = malformed["network"]["edges"][0]["chemistry_evidence"]["ledger"]
    ledger[0], ledger[1] = ledger[1], ledger[0]
    malformed_outputs.append(malformed)
    malformed = copy.deepcopy(complete)
    malformed["network"]["edges"][0]["depiction_contract"]["display_only"] = True
    malformed_outputs.append(malformed)
    malformed = copy.deepcopy(complete)
    malformed["network"]["edges"][0]["execution_eligibility"]["reasons"] = "PASS"
    malformed_outputs.append(malformed)
    malformed = copy.deepcopy(complete)
    malformed["network"]["execution_network_gate"]["verdict"] = True
    malformed_outputs.append(malformed)
    malformed = copy.deepcopy(complete)
    malformed["network"]["edges"][0]["undeclared_field"] = "accepted"
    malformed_outputs.append(malformed)
    for malformed in malformed_outputs:
        with pytest.raises(failures.DiracInternal):
            registry.validate_output("physics.motif.rbfe_network", malformed)


def test_rbfe_cycle_closure_is_derived_from_graph_not_planner_label():
    from motif.rbfe import aggregate_rbfe_results
    network = {
        "digest": "sha256:" + "a" * 64,
        "compounds": [{"id": name} for name in ("a", "b", "c")],
        "edges": [
            {"edge_id": "e1", "left_id": "a", "right_id": "b",
             "purpose": "openfe_redundant_network"},
            {"edge_id": "e2", "left_id": "b", "right_id": "c",
             "purpose": "openfe_redundant_network"},
            {"edge_id": "e3", "left_id": "a", "right_id": "c",
             "purpose": "openfe_redundant_network"},
        ],
    }
    observations = (_validated_rbfe_legs("e1", 1.0)
                    + _validated_rbfe_legs("e2", 1.0)
                    + _validated_rbfe_legs("e3", 2.2))
    result = aggregate_rbfe_results(network, observations)
    assert len(result["cycle_closure"]) == 1
    closure = result["cycle_closure"][0]
    assert closure["edge_ids"] == ["e1", "e2", "e3"]
    assert abs(closure["closure_kcal_mol"] - .2) < 1e-12


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
