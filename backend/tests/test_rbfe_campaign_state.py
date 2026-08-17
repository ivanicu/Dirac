from __future__ import annotations

from copy import deepcopy
import inspect
from pathlib import Path
import subprocess

import pytest


def _payload() -> dict:
    return {
        "campaign_name": "regulated-series-01",
        "target_name": "target",
        "source_pdb_id": "1ABC",
        "structure_method": "xray",
        "resolution_angstrom": 1.8,
        "receptor_pdb": "ATOM      1  CA  ALA A   1       0.000   0.000   0.000\n",
        "compounds": [
            {"id": "parent", "smiles": "CCO"},
            {"id": "analogue", "smiles": "CCN"},
        ],
        "parent_id": "parent",
        "reference_ligand": {
            "resname": "LIG", "chain": "A", "residue_number": "100",
        },
        "pose_strategy": "align_to_reference",
        "minimum_core_coverage": 0.5,
        "seed": 7,
        "receptor_policy": {
            "assembly_id": "1", "chain_ids": ["A"],
            "missing_atoms": "auto_repair_report",
            "missing_residues": "review_each",
            "altloc": "highest_occupancy", "occupancy": "keep_reported",
            "waters": {
                "mode": "review", "site_decisions": [
                    {"chain": "A", "residue_number": "12", "decision": "keep"},
                ],
            },
            "cofactors": "keep_parameter_gate", "metals": "keep_parameter_gate",
            "histidines": "server_assign_review", "termini": "server_assign_review",
            "ph": 7.4,
            "forcefield_contract": {
                "protein": "AMBER ff14SB", "water": "TIP3P",
            },
        },
        "ligand_policy": {
            "formal_charge": "block_changes",
            "tautomer": "strict",
            "protonation": "specified_only",
            "stereochemistry": "preserve_block_unknown",
            "state_population_cutoff": 0.05,
        },
    }


def _openfe_probe(script: str) -> subprocess.CompletedProcess:
    root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [str(root / "openfe-runtime-v2/bin/python"), "-c", script],
        cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False)


def test_coordinate_policy_resolves_altloc_and_water_entities_but_blocks_unparameterized_metal():
    script = r'''
import sys
sys.path.insert(0, "backend/motif")
import rbfe_campaign_builder as builder

def atom(serial, name, resname, residue, x, *, record="ATOM", chain="A",
         altloc="", occupancy=1.0, element="C"):
    return (f"{record:<6}{serial:5d} {name:^4}{altloc:1}{resname:>3} {chain:1}"
            f"{residue:4d}    {x:8.3f}{0.0:8.3f}{0.0:8.3f}"
            f"{occupancy:6.2f}{20.0:6.2f}          {element:>2}")

pdb = "\n".join([
    atom(1, "CA", "ALA", 1, 0.0, altloc="A", occupancy=.30),
    atom(2, "CA", "ALA", 1, .2, altloc="B", occupancy=.70),
    atom(3, "C1", "LIG", 100, 5.0, record="HETATM", chain="B"),
    atom(4, "O", "HOH", 201, 6.0, record="HETATM", chain="C", element="O"),
    atom(5, "ZN", "ZN", 301, 8.0, record="HETATM", chain="D", element="ZN"),
    "END", "",
])
policy = {
    "assembly_id": "deposited_asymmetric_unit", "chain_ids": ["A"],
    "missing_atoms": "auto_repair_report", "missing_residues": "block",
    "altloc": "highest_occupancy", "occupancy": "keep_reported",
    "waters": {"mode": "review_pocket", "site_decisions": []},
    "cofactors": "remove", "metals": "remove",
    "histidines": "server_assign_review", "termini": "server_assign_review",
    "ph": 7.4, "forcefield_contract": dict(builder._SUPPORTED_FORCEFIELD_CONTRACT),
}
selector = {"resname": "LIG", "chain": "B", "residue_number": "100"}
try:
    builder._apply_coordinate_policy(pdb, policy, selector)
except ValueError as error:
    assert "HOH:C:201" in str(error) and "lacks a keep/remove decision" in str(error)
else:
    raise AssertionError("unreviewed pocket water was accepted")

policy["waters"]["site_decisions"] = [
    {"chain": "C", "residue_number": "201", "decision": "keep"}]
policy["metals"] = "keep_parameter_gate"
try:
    builder._apply_coordinate_policy(pdb, policy, selector)
except ValueError as error:
    assert "ZN:D:301" in str(error) and "parameterisation witness" in str(error)
else:
    raise AssertionError("unparameterized metal was accepted")

policy["metals"] = "remove"
filtered, report = builder._apply_coordinate_policy(pdb, policy, selector)
assert " HOH " in filtered and " ZN " not in filtered
assert report["altloc"]["residue_witnesses"][0]["chosen_altloc"] == "B"
assert report["waters"]["pocket_water_witnesses"][0]["decision"] == "keep"
assert report["metals"]["entity_witnesses"][0]["site_id"] == "ZN:D:301"
assert all(axis["verdict"] == "CONFIRMED" for axis in report.values())
'''
    completed = _openfe_probe(script)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_coordinate_policy_rejects_zero_occupancy_and_unsupported_assembly_with_atom_witness():
    script = r'''
import sys
sys.path.insert(0, "backend/motif")
import rbfe_campaign_builder as builder

def atom(serial, name, resname, residue, x, *, record="ATOM", occupancy=1.0,
         element="C"):
    return (f"{record:<6}{serial:5d} {name:^4} {resname:>3} A{residue:4d}    "
            f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}{occupancy:6.2f}{20.0:6.2f}"
            f"          {element:>2}")

pdb = "\n".join([
    atom(1, "CA", "ALA", 1, 0.0, occupancy=0.0),
    atom(2, "C1", "LIG", 100, 4.0, record="HETATM"), "END", ""])
policy = {
    "assembly_id": "deposited_asymmetric_unit", "chain_ids": ["A"],
    "missing_atoms": "block", "missing_residues": "block",
    "altloc": "highest_occupancy", "occupancy": "reject_zero",
    "waters": {"mode": "remove_all", "site_decisions": []},
    "cofactors": "remove", "metals": "remove",
    "histidines": "server_assign_review", "termini": "server_assign_review",
    "ph": 7.4, "forcefield_contract": dict(builder._SUPPORTED_FORCEFIELD_CONTRACT),
}
selector = {"resname": "LIG", "chain": "A", "residue_number": "100"}
try:
    builder._apply_coordinate_policy(pdb, policy, selector)
except ValueError as error:
    message = str(error)
    assert "zero-occupancy" in message and "'serial': 1" in message
else:
    raise AssertionError("zero-occupancy atom was accepted")
policy["occupancy"] = "keep_reported"
policy["assembly_id"] = "1"
try:
    builder._apply_coordinate_policy(pdb, policy, selector)
except ValueError as error:
    assert "biological assembly '1' was not generated" in str(error)
else:
    raise AssertionError("unsupported assembly was accepted")
'''
    completed = _openfe_probe(script)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_pose_geometry_returns_bounded_exact_atom_pair_witnesses_and_names_clash_pair():
    script = r'''
import sys
sys.path.insert(0, "backend/motif")
from rdkit import Chem
import rbfe_campaign_builder as builder

pdb = ("ATOM      7  CA  ALA A  42       0.000   0.000   0.000  1.00 20.00           C  \n"
       "ATOM      8  O   ALA A  42       5.000   0.000   0.000  1.00 20.00           O  \nEND\n")
receptor = builder._heavy_xyz_from_pdb(pdb)
def sdf_at(x):
    editable = Chem.RWMol()
    editable.AddAtom(Chem.Atom("C"))
    molecule = editable.GetMol()
    conformer = Chem.Conformer(1)
    conformer.SetAtomPosition(0, (x, 0.0, 0.0))
    molecule.AddConformer(conformer)
    return Chem.MolToMolBlock(molecule)

report = builder._pose_geometry(receptor, sdf_at(2.0))
nearest = report["nearest_pair_witness"]
assert nearest["protein_serial"] == 7
assert nearest["protein_atom_name"] == "CA"
assert nearest["protein_residue_name"] == "ALA"
assert nearest["protein_chain_id"] == "A"
assert nearest["protein_residue_number"] == "42"
assert nearest["ligand_atom_index"] == 0
assert report["contact_pair_total"] == 2
assert report["hard_clash_pair_total"] == 0
assert report["pair_witness_limit"] == 32
try:
    builder._pose_geometry(receptor, sdf_at(.5))
except ValueError as error:
    message = str(error)
    assert "nearest pair witness" in message and "'protein_serial': 7" in message
else:
    raise AssertionError("hard clash was accepted")
'''
    completed = _openfe_probe(script)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_forcefield_contract_is_exact_and_never_promotes_compatibility_to_parameterization():
    script = r'''
import sys
sys.path.insert(0, "backend/motif")
import rbfe_campaign_builder as builder

report = builder._validate_forcefield_contract(
    dict(builder._SUPPORTED_FORCEFIELD_CONTRACT))
assert report["verdict"] == "CONFIRMED"
assert "actual ForceField.createSystem" in report["parameterization_boundary"]
try:
    builder._validate_forcefield_contract({"protein": "AMBER ff14SB", "water": "TIP3P"})
except ValueError as error:
    assert "missing=" in str(error) and "release" in str(error)
else:
    raise AssertionError("incomplete forcefield contract was accepted")
'''
    completed = _openfe_probe(script)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_real_1cbs_polymer_a_reference_b_simple_remove_path_stays_usable():
    script = r'''
import io, sys
sys.path.insert(0, "backend/motif")
from openmm.app import PDBFile, PDBxFile
import rbfe_campaign_builder as builder

source = PDBxFile("examples/1cbs_updated.cif")
stream = io.StringIO()
PDBFile.writeFile(source.topology, source.positions, stream, keepIds=True)
pdb = stream.getvalue()
selector = builder.inspect_bound_ligands(pdb)[0]
assert selector["resname"] == "REA" and selector["chain"] == "B"
policy = {
    "assembly_id": "deposited_asymmetric_unit", "chain_ids": ["A"],
    "missing_atoms": "auto_repair_report",
    "missing_residues": "auto_repair_report",
    "altloc": "highest_occupancy", "occupancy": "keep_reported",
    "waters": {"mode": "remove_all", "site_decisions": []},
    "cofactors": "remove", "metals": "remove",
    "histidines": "server_assign_review", "termini": "server_assign_review",
    "ph": 7.4, "forcefield_contract": dict(builder._SUPPORTED_FORCEFIELD_CONTRACT),
}
filtered, coordinate_report = builder._apply_coordinate_policy(pdb, policy, selector)
assert coordinate_report["waters"]["water_entity_count"] > 0
assert coordinate_report["waters"]["pocket_candidate_count"] > 0
prepared, preparation_report = builder._prepare_receptor(
    filtered, ph=7.4, keep_waters=False, chain_ids=["A"],
    missing_atoms_policy="auto_repair_report",
    missing_residues_policy="auto_repair_report",
    histidines_policy="server_assign_review",
    termini_policy="server_assign_review",
    forcefield_contract=policy["forcefield_contract"])
assert "ATOM" in prepared
assert all(axis["verdict"] == "CONFIRMED"
           for axis in coordinate_report.values())
assert all(axis["verdict"] == "CONFIRMED"
           for axis in preparation_report["policy_execution"].values())
terminal = preparation_report["policy_execution"]["termini"]["terminal_witnesses"][0]
assert terminal["chain_id"] == "A"
assert terminal["n_terminus"]["atom_name_witnesses"]
assert terminal["c_terminus"]["atom_name_witnesses"]
retinoic_acid = "CC1=C(C(CCC1)(C)C)/C=C/C(=C/C=C/C(=C/C(=O)O)/C)/C"
built = builder.build({
    "campaign_name": "1cbs-positive-control", "target_name": "CRABP2",
    "source_pdb_id": "1CBS", "structure_method": "xray",
    "resolution_angstrom": 1.8, "receptor_pdb": pdb,
    "compounds": [
        {"id": "parent", "smiles": retinoic_acid},
        {"id": "analogue", "smiles": retinoic_acid},
    ],
    "parent_id": "parent",
    "reference_ligand": {**selector, "role": "experimental_ligand"},
    "pose_strategy": "align_to_reference", "minimum_core_coverage": .5,
    "seed": 7, "receptor_policy": policy,
    "ligand_policy": {
        "formal_charge": "block_changes", "tautomer": "strict",
        "protonation": "specified_only",
        "stereochemistry": "preserve_block_unknown",
        "state_population_cutoff": .05,
    },
})
assert len(built["poses"]) == 2
assert built["receptor_report"]["qualification_gate"]["verdict"] == "CONFIRMED"
assert all(pose["report"]["nearest_pair_witness"]["protein_residue_name"]
           for pose in built["poses"])
'''
    completed = _openfe_probe(script)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_server_histidine_and_terminus_assignments_have_exact_atom_witnesses_manual_is_blocked():
    script = r'''
import sys
sys.path.insert(0, "backend/motif")
import rbfe_campaign_builder as builder

pdb = open("backend/env/lib/python3.12/site-packages/prolif/data/implicitHbond/receptor.pdb").read()
contract = dict(builder._SUPPORTED_FORCEFIELD_CONTRACT)
prepared, report = builder._prepare_receptor(
    pdb, ph=7.4, keep_waters=False, chain_ids=[],
    missing_atoms_policy="auto_repair_report",
    missing_residues_policy="auto_repair_report",
    histidines_policy="server_assign_review",
    termini_policy="server_assign_review", forcefield_contract=contract)
histidines = report["policy_execution"]["histidines"]["assignment_witnesses"]
assert histidines
assert {row["assigned_state"] for row in histidines}.issubset({"HID", "HIE", "HIP"})
assert all(row["hydrogen_atom_witnesses"] for row in histidines)
termini = report["policy_execution"]["termini"]["terminal_witnesses"]
assert termini
assert all(row["n_terminus"]["atom_name_witnesses"] for row in termini)
assert all(row["c_terminus"]["atom_name_witnesses"] for row in termini)
try:
    builder._prepare_receptor(
        pdb, ph=7.4, keep_waters=False, chain_ids=[],
        missing_atoms_policy="auto_repair_report",
        missing_residues_policy="auto_repair_report",
        histidines_policy="manual", termini_policy="server_assign_review",
        forcefield_contract=contract)
except ValueError as error:
    assert "histidines=manual is UNVERIFIED" in str(error) and "residue_number" in str(error)
else:
    raise AssertionError("manual histidine policy without site decisions was accepted")
try:
    builder._prepare_receptor(
        pdb, ph=7.4, keep_waters=False, chain_ids=[],
        missing_atoms_policy="auto_repair_report",
        missing_residues_policy="auto_repair_report",
        histidines_policy="server_assign_review", termini_policy="manual",
        forcefield_contract=contract)
except ValueError as error:
    assert "termini=manual is UNVERIFIED" in str(error) and "n_terminus" in str(error)
else:
    raise AssertionError("manual terminus policy without chain decisions was accepted")
'''
    completed = _openfe_probe(script)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_campaign_identity_is_retry_stable_but_actor_scoped():
    from motif.rbfe_campaign_state import stable_campaign_id

    payload = _payload()
    human = {"kind": "human", "id": "reviewer-7"}
    assert stable_campaign_id(payload, human) == stable_campaign_id(payload, human)
    assert stable_campaign_id(payload, human) != stable_campaign_id(
        payload, {"kind": "human", "id": "reviewer-8"})


def test_canonical_bundle_normalizes_smiles_and_digests_every_policy_field():
    from motif.rbfe_campaign_state import canonical_digest_bundle

    baseline = canonical_digest_bundle(_payload())
    equivalent = _payload()
    equivalent["compounds"][0]["smiles"] = "OCC"
    assert (canonical_digest_bundle(equivalent)["canonical_ligands_digest"]
            == baseline["canonical_ligands_digest"])

    receptor_variants = {
        "assembly_id": "2", "chain_ids": ["B"],
        "missing_atoms": "block", "missing_residues": "block", "altloc": "A",
        "occupancy": "reject_zero", "waters": {"mode": "remove", "site_decisions": []},
        "cofactors": "remove", "metals": "remove", "histidines": "manual",
        "termini": "manual", "ph": 6.5,
        "forcefield_contract": {"protein": "other", "water": "other"},
    }
    for field, value in receptor_variants.items():
        changed = _payload()
        changed["receptor_policy"][field] = value
        candidate = canonical_digest_bundle(changed)
        assert candidate["receptor_policy_digest"] != baseline["receptor_policy_digest"], field
        assert candidate["prep_policy_digest"] != baseline["prep_policy_digest"], field

    for field, value in {
        "formal_charge": "allow_governed", "tautomer": "enumerate",
        "protonation": "enumerate_at_ph",
        "stereochemistry": "enumerate_unknown",
        "state_population_cutoff": 0.2,
    }.items():
        changed = _payload()
        changed["ligand_policy"][field] = value
        candidate = canonical_digest_bundle(changed)
        assert candidate["ligand_policy_digest"] != baseline["ligand_policy_digest"], field
        assert candidate["microstates_digest"] != baseline["microstates_digest"], field

    command_shaped = _payload()
    command_shaped["receptor_policy"]["waters"] = "review_pocket"
    command_shaped["receptor_policy"]["water_site_decisions"] = [
        {"chain": "A", "residue_number": "12", "decision": "keep"},
    ]
    normalized = canonical_digest_bundle(command_shaped)["receptor_policy"]
    assert normalized["waters"] == {
        "mode": "review_pocket",
        "site_decisions": [
            {"chain": "A", "residue_number": "12", "decision": "keep"},
        ],
    }
    assert "water_site_decisions" not in normalized


def test_unknown_stereo_is_enumerated_into_explicit_durable_child_identities():
    from rdkit import Chem
    from motif.rbfe_campaign_state import normalize_ligand_series

    payload = _payload()
    payload["compounds"] = [
        {"id": "parent", "smiles": "C[C@H](O)C(=O)O"},
        {"id": "analogue", "smiles": "CC(O)C(=O)O"},
    ]
    payload["ligand_policy"]["stereochemistry"] = "enumerate_unknown"

    normalized, report = normalize_ligand_series(payload)

    assert report["input_count"] == 2
    assert report["output_count"] == 3
    children = [row for row in normalized["compounds"]
                if row["source_compound_id"] == "analogue"]
    assert [row["id"] for row in children] == [
        "analogue__stereo01", "analogue__stereo02",
    ]
    assert all(row["stereo_state"] == "enumerated" for row in children)
    assert {Chem.FindMolChiralCenters(
        Chem.MolFromSmiles(row["smiles"]), includeUnassigned=True)[0][1]
        for row in children} == {"R", "S"}


def test_unknown_stereo_is_fail_closed_when_policy_preserves_only_explicit_states():
    from motif.rbfe_campaign_state import normalize_ligand_series

    payload = _payload()
    payload["compounds"][1]["smiles"] = "CC(O)C(=O)O"
    with pytest.raises(ValueError, match="possible stereoisomers"):
        normalize_ligand_series(payload)


def test_crystallographic_parent_must_never_be_silently_stereo_enumerated():
    from motif.rbfe_campaign_state import normalize_ligand_series

    payload = _payload()
    payload["compounds"][0]["smiles"] = "CC(O)C(=O)O"
    payload["ligand_policy"]["stereochemistry"] = "enumerate_unknown"
    with pytest.raises(ValueError, match="crystallographic parent"):
        normalize_ligand_series(payload)


def test_dependency_dag_recursively_stales_every_consumer_and_only_consumers():
    from motif.rbfe_campaign_state import (canonical_digest_bundle, dependency_dag,
                                            recursively_stale)

    dag = dependency_dag(canonical_digest_bundle(_payload()))
    stale = recursively_stale(dag, ["prep_policy"], "pH or receptor policy changed")
    expected = {
        "prep_policy", "prepared_receptor", "poses", "pose_review",
        "network", "system_build",
    }
    assert set(stale["invalidation"]["stale_stages"]) == expected
    assert all(stale["nodes"][name]["verdict"] == "OVERTURNED" for name in expected)
    assert stale["nodes"]["source"]["verdict"] == "CONFIRMED"
    assert stale["nodes"]["canonical_ligands"]["stale"] is False

    protocol_only = recursively_stale(dag, ["protocol"], "protocol changed")
    assert set(protocol_only["invalidation"]["stale_stages"]) == {
        "protocol", "system_build",
    }


def test_pose_review_attestation_is_attributable_complete_and_digest_bound():
    from motif.rbfe_campaign_state import review_attestation

    digest = "sha256:" + "a" * 64
    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    refs = [
        {"kind": "pose_hypothesis", "id": "pose-a", "sha256": digest},
        {"kind": "pose_hypothesis", "id": "pose-b", "sha256": digest},
    ]
    attestation = review_attestation(
        campaign_id=campaign_id, campaign_version=4,
        reviewer={"kind": "human", "id": "chemist-1"},
        reviewed_at="2026-08-17T09:00:00Z",
        reason="Both endpoints preserve the crystallographic core.",
        viewed_pose_refs=refs,
        review_checks=["shared_coordinate_frame", "core_alignment", "pocket_geometry"])
    assert attestation["verdict"] == "CONFIRMED"
    assert attestation["reviewer"]["id"] == "chemist-1"
    assert attestation["attestation_digest"].startswith("sha256:")

    with pytest.raises(ValueError, match="reason"):
        review_attestation(
            campaign_id=campaign_id, campaign_version=4,
            reviewer={"kind": "human", "id": "chemist-1"},
            reviewed_at="2026-08-17T09:00:00Z", reason="",
            viewed_pose_refs=refs,
            review_checks=["shared_coordinate_frame", "core_alignment", "pocket_geometry"])
    with pytest.raises(ValueError, match="checks"):
        review_attestation(
            campaign_id=campaign_id, campaign_version=4,
            reviewer={"kind": "human", "id": "chemist-1"},
            reviewed_at="2026-08-17T09:00:00Z", reason="reviewed",
            viewed_pose_refs=refs, review_checks=["core_alignment"])


def test_all_stage_verdicts_are_three_valued_and_refs_require_full_digests():
    from motif.rbfe_campaign_state import full_ref, stage_payload

    for verdict in ("CONFIRMED", "OVERTURNED", "UNVERIFIED"):
        assert stage_payload("network", verdict)["verdict"] == verdict
    with pytest.raises(ValueError, match="verdict"):
        stage_payload("network", "PASSED")
    with pytest.raises(ValueError, match="complete sha256"):
        full_ref("artifact", "a1", "sha256:abc")


def test_resolver_public_campaign_api_signatures_remain_explicit():
    from motif.rbfe_references import PostgresRbfeReferenceResolver

    expected = {
        "save_campaign": ["self", "payload", "actor"],
        "get_campaign": ["self", "campaign_id", "actor"],
        "list_campaigns": ["self", "actor"],
        "invalidate_campaign": [
            "self", "campaign_id", "expected_version", "reason",
            "changed_domains", "actor",
        ],
        "list_systems": ["self", "actor", "campaign_id", "include_importable"],
    }
    for method, names in expected.items():
        assert list(inspect.signature(getattr(PostgresRbfeReferenceResolver, method)).parameters) == names
    assert not hasattr(PostgresRbfeReferenceResolver,
                       "validate_campaign_version")
    import_parameters = inspect.signature(
        PostgresRbfeReferenceResolver.import_system).parameters
    assert list(import_parameters) == [
        "self", "campaign_id", "prepared_receptor_state_ref", "actor",
        "expected_version", "reason",
    ]
    assert import_parameters["expected_version"].kind is inspect.Parameter.KEYWORD_ONLY
    assert list(inspect.signature(
        PostgresRbfeReferenceResolver.assert_campaign_generation).parameters) == [
            "self", "campaign_id", "scientific_generation",
            "scientific_digest", "actor",
        ]
    assert list(inspect.signature(
        PostgresRbfeReferenceResolver.prepare_campaign).parameters) == [
            "self", "payload", "store", "actor", "job_id", "dispatch_fence",
        ]
    assert list(inspect.signature(
        PostgresRbfeReferenceResolver.accept_poses).parameters) == [
            "self", "payload", "actor",
        ]


def test_scientific_conflicts_do_not_masquerade_as_revision_conflicts():
    from motif.rbfe_references import _scientific_failure

    error = _scientific_failure(
        "assert_campaign_generation", "science pair is stale",
        expected_scientific_generation=4,
        actual_scientific_generation=5,
        expected_scientific_digest="sha256:" + "a" * 64,
        actual_scientific_digest="sha256:" + "b" * 64,
        required_actions=["reload_campaign"],
    )
    payload = error.details["error"]
    assert payload == {
        "code": "CAMPAIGN_SCIENTIFIC_CONFLICT",
        "message": "science pair is stale",
        "expected_scientific_generation": 4,
        "actual_scientific_generation": 5,
        "expected_scientific_digest": "sha256:" + "a" * 64,
        "actual_scientific_digest": "sha256:" + "b" * 64,
    }
    assert "expected_version" not in payload
    assert "actual_version" not in payload


def test_import_receipt_survives_metadata_revision_but_not_science_change():
    from motif.rbfe_references import (
        _campaign_scientific_ref, _import_receipt_is_current)

    campaign = {
        "id": "00000000-0000-4000-8000-000000000101",
        "version": 7,
        "state_digest": "sha256:" + "a" * 64,
        "scientific_generation": 3,
        "scientific_digest": "sha256:" + "b" * 64,
    }
    receipt = {"campaign_scientific_ref": _campaign_scientific_ref(campaign)}

    metadata_only = {**campaign, "version": 8,
                     "state_digest": "sha256:" + "c" * 64}
    assert _import_receipt_is_current(receipt, metadata_only)

    science_changed = {**metadata_only, "scientific_generation": 4,
                       "scientific_digest": "sha256:" + "d" * 64}
    assert not _import_receipt_is_current(receipt, science_changed)


def test_scientific_object_ref_never_prefers_audit_campaign_ref():
    from motif.rbfe_references import _document_campaign_scientific_ref

    audit_ref = {
        "kind": "rbfe_campaign",
        "id": "00000000-0000-4000-8000-000000000101",
        "version": 8, "sha256": "sha256:" + "a" * 64,
    }
    science_ref = {
        "kind": "rbfe_campaign",
        "id": audit_ref["id"], "version": 3,
        "sha256": "sha256:" + "b" * 64,
    }
    assert _document_campaign_scientific_ref({
        "campaign_ref": audit_ref,
        "campaign_scientific_ref": science_ref,
    }) == science_ref
    # Existing persisted documents remain readable during the field migration.
    assert _document_campaign_scientific_ref({
        "campaign_ref": science_ref,
    }) == science_ref


class _FakeCursor:
    def __init__(self, campaign_row=None):
        self.campaign_row = campaign_row
        self.queries: list[tuple[str, object]] = []
        self._next = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, parameters=None):
        self.queries.append((query, parameters))
        if query.startswith("SELECT id::text,version,status,state"):
            self._next = self.campaign_row
        elif query.startswith("UPDATE app.rbfe_campaign"):
            self._next = (self.campaign_row[0],)
        elif "FROM design.motif_scientific_object o" in query and query.startswith("SELECT"):
            self._next = None
        else:
            self._next = None

    def fetchone(self):
        result, self._next = self._next, None
        return result


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self._cursor


def _campaign_row(document: dict, status: str = "prepared") -> tuple:
    from motif.rbfe_campaign_state import sha256_digest
    from motif.rbfe_references import _advance_scientific_state

    if ("scientific_generation" not in document
            or "scientific_digest" not in document):
        body = {key: value for key, value in document.items()
                if key != "state_digest"}
        body["status"] = status
        sealed = _advance_scientific_state(
            body, None,
            {"action": "fixture_created",
             "fixture_digest": sha256_digest(body), "status": status})
        document.clear()
        document.update(sealed)
    return (
        document["campaign_id"], document["version"], status, document,
        document["state_digest"][7:], document["scientific_generation"],
        document["scientific_digest"][7:], None, None, "human", "chemist-1",
        None, None,
    )


def test_migration_requires_explicit_state_and_rejects_malformed_scientific_pair():
    migration = (Path(__file__).resolve().parents[1]
                 / "db/migrations/040_rbfe_campaign_state.sql").read_text()
    campaign_table = migration.split(
        "CREATE TABLE app.rbfe_campaign_revision", 1)[0]
    # Omitting state must hit NOT NULL; an unusable '{}' default would instead
    # manufacture a row that only fails later scientific-pair checks.
    assert "state jsonb NOT NULL\n" in campaign_table
    assert "state jsonb NOT NULL DEFAULT" not in campaign_table
    # Both mutable campaign rows and immutable revisions must fail closed.
    assert migration.count("state ? 'scientific_generation'") == 2
    assert migration.count(
        "jsonb_typeof(state->'scientific_generation') = 'number'") == 2
    assert migration.count("state ? 'scientific_digest'") == 2
    assert migration.count(
        "jsonb_typeof(state->'scientific_digest') = 'string'") == 2


@pytest.mark.parametrize("mutation", [
    lambda state: state.pop("scientific_generation"),
    lambda state: state.__setitem__("scientific_generation", "1"),
    lambda state: state.pop("scientific_digest"),
    lambda state: state.__setitem__("scientific_digest", 7),
])
def test_campaign_reader_rejects_missing_or_wrong_typed_scientific_pair(mutation):
    from motif.rbfe_campaign_state import sha256_digest
    from motif.rbfe_references import (_advance_scientific_state,
                                       PostgresRbfeReferenceResolver)

    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    state = _advance_scientific_state(
        {"campaign_id": campaign_id, "version": 1, "status": "draft"},
        None, {"action": "campaign_created", "input_digest": "none"})
    row_generation = state["scientific_generation"]
    row_scientific_digest = state["scientific_digest"]
    malformed = deepcopy(state)
    mutation(malformed)
    body = {key: value for key, value in malformed.items()
            if key != "state_digest"}
    malformed = {**body, "state_digest": sha256_digest(body)}
    row = (
        campaign_id, 1, "draft", malformed, malformed["state_digest"][7:],
        row_generation, row_scientific_digest[7:], None, None,
        "human", "chemist-1", None, None,
    )

    with pytest.raises(Exception, match="scientific generation is malformed"):
        PostgresRbfeReferenceResolver(
            lambda: _FakeConnection(_FakeCursor(row))).get_campaign(
                campaign_id, {"kind": "human", "id": "chemist-1"})


def test_scientific_transition_chain_advances_for_stage_and_import_events():
    from motif.rbfe_references import _advance_scientific_state

    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    created = _advance_scientific_state(
        {"campaign_id": campaign_id, "version": 1, "status": "draft"},
        None, {"action": "campaign_created", "input_digest": "fixture"})
    staged = _advance_scientific_state(
        {**created, "version": 2, "status": "prepared"}, created,
        {"action": "campaign_prepared", "prepared_digest": "fixture"})
    imported = _advance_scientific_state(
        {**staged, "version": 3, "status": "prepared"}, staged,
        {"action": "system_imported", "receipt_digest": "fixture"})

    assert staged["scientific_generation"] == (
        created["scientific_generation"] + 1)
    assert imported["scientific_generation"] == (
        staged["scientific_generation"] + 1)
    assert len({created["scientific_digest"], staged["scientific_digest"],
                imported["scientific_digest"]}) == 3
    assert staged["scientific_transition"]["action"] == "campaign_prepared"
    assert imported["scientific_transition"]["action"] == "system_imported"


def test_policy_change_recursively_invalidates_owned_db_objects_before_rebuild():
    from motif.rbfe_campaign_state import (campaign_document,
                                            canonical_digest_bundle,
                                            dependency_dag)
    from motif.rbfe_references import PostgresRbfeReferenceResolver

    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    payload = _payload()
    bundle = canonical_digest_bundle(payload)
    receptor_id = "57484a24-b9c0-4d01-ac32-d3a2ae50b998"
    pose_id = "b62ee0e4-aa16-4e4d-a2b1-61c80fbedc2a"
    document = campaign_document(
        campaign_id=campaign_id, version=2, label=payload["campaign_name"],
        actor={"kind": "human", "id": "chemist-1"}, digest_bundle=bundle,
        artifact_dag=dependency_dag(bundle), status="prepared", inputs=payload,
        owned_object_refs=[
            {"kind": "prepared_receptor_state", "id": receptor_id,
             "sha256": "sha256:" + "a" * 64},
            {"kind": "pose_hypothesis", "id": pose_id,
             "sha256": "sha256:" + "b" * 64},
        ])
    cursor = _FakeCursor(_campaign_row(document))
    resolver = PostgresRbfeReferenceResolver(
        lambda: _FakeConnection(cursor))
    changed = deepcopy(payload)
    changed.update({"campaign_id": campaign_id, "expected_version": 2})
    changed["receptor_policy"]["ph"] = 6.2

    result = resolver.save_campaign(
        changed, {"kind": "human", "id": "chemist-1"})

    recursive_updates = [
        (query, parameters) for query, parameters in cursor.queries
        if "WITH RECURSIVE affected" in query
    ]
    assert len(recursive_updates) == 1
    query, parameters = recursive_updates[0]
    assert "invalidated_at=now()" in query
    assert "invalidation_code='campaign_stale'" in query
    assert "JOIN affected a ON d.dependency_id=a.id" in query
    assert set(parameters[0]) == {receptor_id, pose_id}
    assert result["version"] == 3
    assert result["status"] == "draft"
    assert result["state"]["prior_invalidation"]["verdict"] == "CONFIRMED"


def test_direct_uuid_resolution_rejects_bare_unaddressed_scientific_object_ids():
    from motif.rbfe_campaign_state import sha256_digest
    from motif.rbfe_references import PostgresRbfeReferenceResolver

    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    body = {"campaign_id": campaign_id, "version": 4, "status": "poses_reviewed"}
    document = {**body, "state_digest": sha256_digest(body)}
    cursor = _FakeCursor(_campaign_row(document, "poses_reviewed"))
    resolver = PostgresRbfeReferenceResolver(
        lambda: _FakeConnection(cursor))

    with pytest.raises(Exception, match="complete content-addressed"):
        resolver.resolve_prepared_system(
            "57484a24-b9c0-4d01-ac32-d3a2ae50b998",
            "b62ee0e4-aa16-4e4d-a2b1-61c80fbedc2a",
            "427d74ad-e4aa-48d6-a13c-69936ba5db9c",
            campaign_id=campaign_id,
            scientific_generation=document["scientific_generation"],
            scientific_digest=document["scientific_digest"],
            actor={"kind": "human", "id": "chemist-1"})

    object_queries = [query for query, _ in cursor.queries
                      if "FROM design.motif_scientific_object o" in query]
    assert not object_queries


def test_public_generic_save_creates_stable_server_sealed_draft():
    from motif.rbfe_references import PostgresRbfeReferenceResolver

    state = {"campaign_name": "minimal-qa", "project_id": "p-17"}
    payload = {
        "expected_version": 0, "status": "draft", "state": state,
        "changed_domains": ["project_context"], "reason": "create campaign",
    }
    first_cursor = _FakeCursor()
    resolver = PostgresRbfeReferenceResolver(
        lambda: _FakeConnection(first_cursor))
    created = resolver.save_campaign(
        payload, {"kind": "human", "id": "chemist-1"})
    assert created["version"] == 1
    assert created["status"] == "draft"
    assert created["state"]["client_state"] == state
    assert created["state"]["digest_bundle"]["domain_verdicts"]["source"] == "UNVERIFIED"
    assert created["state_digest"].startswith("sha256:")

    second_cursor = _FakeCursor()
    second = PostgresRbfeReferenceResolver(
        lambda: _FakeConnection(second_cursor)).save_campaign(
            payload, {"kind": "human", "id": "chemist-1"})
    assert second["campaign_id"] == created["campaign_id"]

    retry_cursor = _FakeCursor(_campaign_row(created["state"], "draft"))
    retried = PostgresRbfeReferenceResolver(
        lambda: _FakeConnection(retry_cursor)).save_campaign(
            payload, {"kind": "human", "id": "chemist-1"})
    assert retried["campaign_id"] == created["campaign_id"]
    assert retried["idempotent_replay"] is True
    assert not any(query.startswith("UPDATE app.rbfe_campaign")
                   for query, _ in retry_cursor.queries)


def test_assert_campaign_generation_uses_scientific_pair_not_audit_revision():
    from motif.rbfe_campaign_state import sha256_digest
    from motif.rbfe_references import PostgresRbfeReferenceResolver

    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    body = {"campaign_id": campaign_id, "version": 4, "status": "poses_reviewed"}
    document = {**body, "state_digest": sha256_digest(body)}
    row = _campaign_row(document, "poses_reviewed")

    current = PostgresRbfeReferenceResolver(
        lambda: _FakeConnection(_FakeCursor(row))).assert_campaign_generation(
            campaign_id, document["scientific_generation"],
            document["scientific_digest"],
            {"kind": "human", "id": "chemist-1"})
    assert current["version"] == 4
    assert current["campaign_scientific_generation"] == 1
    assert current["verdict"] == "CONFIRMED"

    with pytest.raises(Exception, match="stale, mismatched, or not execution-ready"):
        PostgresRbfeReferenceResolver(
            lambda: _FakeConnection(_FakeCursor(row))).assert_campaign_generation(
                campaign_id, document["scientific_generation"] - 1,
                document["scientific_digest"],
                {"kind": "human", "id": "chemist-1"})
    with pytest.raises(Exception, match="stale, mismatched, or not execution-ready"):
        PostgresRbfeReferenceResolver(
            lambda: _FakeConnection(_FakeCursor(row))).assert_campaign_generation(
                campaign_id, document["scientific_generation"],
                "sha256:" + "0" * 64,
                {"kind": "human", "id": "chemist-1"})
    stale_body = {**body, "status": "stale"}
    stale_document = {**stale_body, "state_digest": sha256_digest(stale_body)}
    invalidated = list(_campaign_row(stale_document, "stale"))
    invalidated[7] = "2026-08-17T10:00:00Z"
    invalidated[8] = "policy changed"
    with pytest.raises(Exception, match="stale, mismatched, or not execution-ready"):
        PostgresRbfeReferenceResolver(
            lambda: _FakeConnection(_FakeCursor(tuple(invalidated)))).assert_campaign_generation(
                campaign_id, document["scientific_generation"],
                document["scientific_digest"],
                {"kind": "human", "id": "chemist-1"})


def test_declared_network_change_cannot_destroy_state_without_digest_change():
    from motif.rbfe_campaign_state import (campaign_document,
                                            canonical_digest_bundle,
                                            dependency_dag, sha256_digest)
    from motif.rbfe_references import PostgresRbfeReferenceResolver

    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    bundle = canonical_digest_bundle(_payload())
    receptor_ref = {
        "kind": "prepared_receptor_state",
        "id": "57484a24-b9c0-4d01-ac32-d3a2ae50b998",
        "sha256": "sha256:" + "a" * 64,
    }
    initial = campaign_document(
        campaign_id=campaign_id, version=4, label="network campaign",
        actor={"kind": "human", "id": "chemist-1"}, digest_bundle=bundle,
        artifact_dag=dependency_dag(bundle), status="poses_reviewed",
        inputs=_payload(), owned_object_refs=[receptor_ref])
    body = {key: value for key, value in initial.items() if key != "state_digest"}
    body["client_state"] = {"campaign_name": "network campaign", "network": "v1"}
    initial = {**body, "state_digest": sha256_digest(body)}
    cursor = _FakeCursor(_campaign_row(initial, "poses_reviewed"))
    resolver = PostgresRbfeReferenceResolver(lambda: _FakeConnection(cursor))

    changed = resolver.save_campaign({
        "campaign_id": campaign_id, "expected_version": 4,
        "status": "poses_reviewed",
        "state": {"campaign_name": "network campaign", "network": "v2",
                  "scientific_inputs": _payload()},
        "changed_domains": ["network"], "reason": "network replanned",
    }, {"kind": "human", "id": "chemist-1"})

    assert changed["status"] == "poses_reviewed"
    assert changed["state"]["artifact_dag"]["nodes"]["network"]["stale"] is False
    assert changed["state"]["artifact_dag"]["nodes"]["system_build"]["stale"] is False
    assert changed["state"]["owned_object_refs"] == [receptor_ref]
    assert not any("WITH RECURSIVE affected" in query for query, _ in cursor.queries)


def test_public_ligand_policy_change_invalidates_receptor_and_pose_uuid_chain():
    from motif.rbfe_campaign_state import (campaign_document,
                                            canonical_digest_bundle,
                                            dependency_dag, sha256_digest)
    from motif.rbfe_references import PostgresRbfeReferenceResolver

    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    bundle = canonical_digest_bundle(_payload())
    refs = [
        {"kind": "prepared_receptor_state",
         "id": "57484a24-b9c0-4d01-ac32-d3a2ae50b998",
         "sha256": "sha256:" + "a" * 64},
        {"kind": "pose_hypothesis",
         "id": "b62ee0e4-aa16-4e4d-a2b1-61c80fbedc2a",
         "sha256": "sha256:" + "b" * 64},
    ]
    initial = campaign_document(
        campaign_id=campaign_id, version=6, label="policy campaign",
        actor={"kind": "human", "id": "chemist-1"}, digest_bundle=bundle,
        artifact_dag=dependency_dag(bundle), status="poses_reviewed",
        inputs=_payload(), owned_object_refs=refs)
    body = {key: value for key, value in initial.items() if key != "state_digest"}
    body["client_state"] = {"campaign_name": "policy campaign", "policy": "v1"}
    initial = {**body, "state_digest": sha256_digest(body)}
    cursor = _FakeCursor(_campaign_row(initial, "poses_reviewed"))

    changed_inputs = _payload()
    changed_inputs["ligand_policy"] = {
        **changed_inputs["ligand_policy"], "tautomer": "dominant_only",
    }
    result = PostgresRbfeReferenceResolver(
        lambda: _FakeConnection(cursor)).save_campaign({
            "campaign_id": campaign_id, "expected_version": 6, "status": "draft",
            "state": {"campaign_name": "policy campaign", "policy": "v2",
                      "scientific_inputs": changed_inputs},
            "changed_domains": ["ligand_policy"],
            "reason": "microstate policy changed",
        }, {"kind": "human", "id": "chemist-1"})

    recursive = [(query, parameters) for query, parameters in cursor.queries
                 if "WITH RECURSIVE affected" in query]
    assert len(recursive) == 1
    assert set(recursive[0][1][0]) == {ref["id"] for ref in refs}
    assert result["status"] == "draft"
    assert result["state"]["owned_object_refs"] == []
    assert result["state"]["pending_changed_domains"] == ["microstates", "prep_policy"]
    assert result["state"]["artifact_dag"]["nodes"]["poses"]["stale"] is True


def test_generic_save_derives_scientific_change_when_client_reports_no_domains():
    from motif.rbfe_campaign_state import (campaign_document,
                                            canonical_digest_bundle,
                                            dependency_dag, sha256_digest)
    from motif.rbfe_references import PostgresRbfeReferenceResolver

    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    scientific = _payload()
    bundle = canonical_digest_bundle(scientific)
    initial = campaign_document(
        campaign_id=campaign_id, version=3, label="underreport attack",
        actor={"kind": "human", "id": "chemist-1"}, digest_bundle=bundle,
        artifact_dag=dependency_dag(bundle), status="poses_reviewed",
        inputs=scientific)
    body = {key: value for key, value in initial.items() if key != "state_digest"}
    body["client_state"] = {
        "campaign_name": "underreport attack", "scientific_inputs": scientific}
    initial = {**body, "state_digest": sha256_digest(body)}

    changed_science = deepcopy(scientific)
    changed_science["compounds"][1]["smiles"] = "CCCl"
    cursor = _FakeCursor(_campaign_row(initial, "poses_reviewed"))
    old_generation = initial["scientific_generation"]
    old_scientific_digest = initial["scientific_digest"]
    result = PostgresRbfeReferenceResolver(
        lambda: _FakeConnection(cursor)).save_campaign({
            "campaign_id": campaign_id, "expected_version": 3,
            "status": "poses_reviewed",
            "state": {"campaign_name": "underreport attack",
                      "scientific_inputs": changed_science},
            "changed_domains": [], "reason": "analogue identity edited",
        }, {"kind": "human", "id": "chemist-1"})

    assert result["status"] == "draft"
    detected = result["state"]["prior_invalidation"][
        "server_detected_changed_domains"]
    assert "canonical_ligands" in detected
    assert result["state"]["prior_invalidation"][
        "underreported_changed_domains"] == detected
    assert result["campaign_scientific_generation"] == old_generation + 1
    assert result["campaign_scientific_digest"] != old_scientific_digest
    with pytest.raises(Exception, match="stale, mismatched, or not execution-ready"):
        PostgresRbfeReferenceResolver(
            lambda: _FakeConnection(_FakeCursor(
                _campaign_row(result["state"], "draft")))).assert_campaign_generation(
                    campaign_id, old_generation, old_scientific_digest,
                    {"kind": "human", "id": "chemist-1"})


def test_generic_metadata_save_preserves_server_owned_stage_digests():
    from motif.rbfe_campaign_state import (campaign_document,
                                            canonical_digest_bundle,
                                            dependency_dag, sha256_digest)
    from motif.rbfe_references import PostgresRbfeReferenceResolver

    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    scientific = _payload()
    bundle = canonical_digest_bundle(
        scientific, pose_review={"review": "human-confirmed"},
        network={"network": "planned"}, protocol="protocol-v1")
    initial = campaign_document(
        campaign_id=campaign_id, version=7, label="before rename",
        actor={"kind": "human", "id": "chemist-1"}, digest_bundle=bundle,
        artifact_dag=dependency_dag(bundle), status="poses_reviewed",
        inputs=scientific)
    body = {key: value for key, value in initial.items() if key != "state_digest"}
    body["client_state"] = {
        "campaign_name": "before rename", "scientific_inputs": scientific}
    initial = {**body, "state_digest": sha256_digest(body)}
    cursor = _FakeCursor(_campaign_row(initial, "poses_reviewed"))
    old_state_digest = initial["state_digest"]
    old_generation = initial["scientific_generation"]
    old_scientific_digest = initial["scientific_digest"]

    result = PostgresRbfeReferenceResolver(
        lambda: _FakeConnection(cursor)).save_campaign({
            "campaign_id": campaign_id, "expected_version": 7,
            "status": "poses_reviewed",
            "state": {"campaign_name": "after rename",
                      "scientific_inputs": scientific},
            "changed_domains": ["project_context"], "reason": "rename only",
        }, {"kind": "human", "id": "chemist-1"})

    assert result["status"] == "poses_reviewed"
    assert result["version"] == 8
    assert result["state_digest"] != old_state_digest
    assert result["campaign_scientific_generation"] == old_generation
    assert result["campaign_scientific_digest"] == old_scientific_digest
    assert result["campaign_ref"] == {
        "kind": "rbfe_campaign", "id": campaign_id,
        "sha256": result["state_digest"], "version": 8,
    }
    assert result["campaign_scientific_ref"] == {
        "kind": "rbfe_campaign", "id": campaign_id,
        "sha256": old_scientific_digest, "version": old_generation,
    }
    for domain in ("pose_review", "network", "protocol"):
        key = f"{domain}_digest"
        assert result["state"]["digest_bundle"][key] == bundle[key]
    revision_inserts = [parameters for query, parameters in cursor.queries
                        if query.startswith(
                            "INSERT INTO app.rbfe_campaign_revision")]
    assert len(revision_inserts) == 1
    assert revision_inserts[0][5] == old_generation
    assert revision_inserts[0][6] == old_scientific_digest[7:]
    assert revision_inserts[0][7] == ["campaign_metadata"]
    confirmed = PostgresRbfeReferenceResolver(
        lambda: _FakeConnection(_FakeCursor(
            _campaign_row(result["state"], "poses_reviewed")))).assert_campaign_generation(
                campaign_id, old_generation, old_scientific_digest,
                {"kind": "human", "id": "chemist-1"})
    assert confirmed["verdict"] == "CONFIRMED"


def test_generic_save_cannot_delete_existing_scientific_inputs():
    from motif.rbfe_campaign_state import (campaign_document,
                                            canonical_digest_bundle,
                                            dependency_dag, sha256_digest)
    from motif.rbfe_references import PostgresRbfeReferenceResolver

    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    scientific = _payload()
    bundle = canonical_digest_bundle(scientific)
    initial = campaign_document(
        campaign_id=campaign_id, version=2, label="cannot erase",
        actor={"kind": "human", "id": "chemist-1"}, digest_bundle=bundle,
        artifact_dag=dependency_dag(bundle), status="prepared", inputs=scientific)
    body = {key: value for key, value in initial.items() if key != "state_digest"}
    body["client_state"] = {
        "campaign_name": "cannot erase", "scientific_inputs": scientific}
    initial = {**body, "state_digest": sha256_digest(body)}

    with pytest.raises(Exception, match="complete scientific_inputs"):
        PostgresRbfeReferenceResolver(
            lambda: _FakeConnection(_FakeCursor(
                _campaign_row(initial, "prepared")))).save_campaign({
                    "campaign_id": campaign_id, "expected_version": 2,
                    "status": "prepared", "state": {"campaign_name": "erased"},
                    "changed_domains": [], "reason": "malformed client save",
                }, {"kind": "human", "id": "chemist-1"})


def test_campaign_uuid_does_not_authorize_a_different_actor():
    from motif.rbfe_campaign_state import sha256_digest
    from motif.rbfe_references import PostgresRbfeReferenceResolver

    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    body = {"campaign_id": campaign_id, "version": 1, "status": "draft"}
    document = {**body, "state_digest": sha256_digest(body)}
    with pytest.raises(Exception, match="does not exist"):
        PostgresRbfeReferenceResolver(
            lambda: _FakeConnection(_FakeCursor(
                _campaign_row(document, "draft")))).get_campaign(
                    campaign_id, {"kind": "human", "id": "intruder"})


def test_pose_review_unlock_requires_human_reviewer():
    from motif.rbfe_campaign_state import review_attestation

    with pytest.raises(ValueError, match="human reviewer"):
        review_attestation(
            campaign_id="2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34",
            campaign_version=1,
            reviewer={"kind": "service", "id": "auto-reviewer"},
            reviewed_at=None, reason="machine-only review",
            viewed_pose_refs=[
                {"kind": "pose_hypothesis", "id": "p1",
                 "sha256": "sha256:" + "a" * 64},
                {"kind": "pose_hypothesis", "id": "p2",
                 "sha256": "sha256:" + "b" * 64},
            ],
            review_checks=[
                "shared_coordinate_frame", "core_alignment", "pocket_geometry"],
        )


def test_prepare_retry_is_idempotent_after_campaign_version_advances():
    from motif.rbfe_campaign_state import (campaign_document,
                                            canonical_digest_bundle,
                                            dependency_dag, idempotency_key,
                                            sha256_digest)
    from motif.rbfe_references import PostgresRbfeReferenceResolver

    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    scientific = _payload()
    bundle = canonical_digest_bundle(scientific)
    receipt = {
        "request_digest": idempotency_key(
            campaign_id, 1, bundle["bundle_digest"], "prepare"),
        "input_version": 1, "output_version": 2,
        "response": {"prepared_receptor_state_ref": {
            "kind": "prepared_receptor_state",
            "id": "57484a24-b9c0-4d01-ac32-d3a2ae50b998",
            "sha256": "sha256:" + "a" * 64,
        }},
        "verdict": "CONFIRMED",
    }
    initial = campaign_document(
        campaign_id=campaign_id, version=2, label="prepared campaign",
        actor={"kind": "human", "id": "chemist-1"}, digest_bundle=bundle,
        artifact_dag=dependency_dag(bundle), status="prepared", inputs=scientific)
    body = {key: value for key, value in initial.items() if key != "state_digest"}
    body["prepare_receipt"] = receipt
    initial = {**body, "state_digest": sha256_digest(body)}
    cursor = _FakeCursor(_campaign_row(initial, "prepared"))

    class Resolver(PostgresRbfeReferenceResolver):
        def _build_campaign(self, payload):  # pragma: no cover - must not run
            raise AssertionError("idempotent retry rebuilt scientific artifacts")

    PostgresArtifactStore = type(
        "PostgresArtifactStore", (), {"put": lambda self, *args, **kwargs: None})
    result = Resolver(lambda: _FakeConnection(cursor)).prepare_campaign(
        {**scientific, "campaign_id": campaign_id, "expected_version": 1},
        PostgresArtifactStore(), {"kind": "human", "id": "chemist-1"},
        job_id="57484a24-b9c0-4d01-ac32-d3a2ae50b998")

    assert result["idempotent_replay"] is True
    assert result["campaign_version"] == 2
    assert result["campaign_state_digest"] == initial["state_digest"]


def test_prepare_request_key_never_enters_scientific_identity_builder_or_inputs():
    from motif.rbfe_campaign_state import (campaign_document,
                                            canonical_digest_bundle,
                                            dependency_dag, idempotency_key,
                                            normalize_ligand_series)
    from motif.rbfe_references import (_prepare_scientific_payload,
                                       PostgresRbfeReferenceResolver)

    campaign_id = "2d41f92d-b0c9-5cb8-87ea-0eb6bb34ae34"
    first_command = {
        **_payload(), "campaign_id": campaign_id, "expected_version": 1,
        "request_key": "delivery-attempt-a",
    }
    second_command = {**first_command, "request_key": "delivery-attempt-b"}
    first_science = _prepare_scientific_payload(first_command)
    second_science = _prepare_scientific_payload(second_command)

    assert first_science == second_science == _payload()
    assert "request_key" not in first_science
    first_normalized, first_stereo = normalize_ligand_series(first_science)
    second_normalized, second_stereo = normalize_ligand_series(second_science)
    assert first_normalized == second_normalized
    assert first_stereo == second_stereo
    assert "request_key" not in first_normalized
    first_bundle = canonical_digest_bundle(first_normalized)
    second_bundle = canonical_digest_bundle(second_normalized)
    assert first_bundle == second_bundle
    assert idempotency_key(
        campaign_id, 1, first_bundle["bundle_digest"], "prepare") == (
            idempotency_key(
                campaign_id, 1, second_bundle["bundle_digest"], "prepare"))

    document = campaign_document(
        campaign_id=campaign_id, version=1, label="transport identity fixture",
        actor={"kind": "human", "id": "chemist-1"},
        digest_bundle=first_bundle, artifact_dag=dependency_dag(first_bundle),
        status="draft", inputs=first_normalized)
    cursor = _FakeCursor(_campaign_row(document, "draft"))
    observed = {}

    class Resolver(PostgresRbfeReferenceResolver):
        def _build_campaign(self, payload):
            observed["builder_input"] = payload
            raise RuntimeError("stop-after-builder-input-capture")

    PostgresArtifactStore = type(
        "PostgresArtifactStore", (), {"put": lambda self, *args, **kwargs: None})
    with pytest.raises(RuntimeError, match="stop-after-builder-input-capture"):
        Resolver(lambda: _FakeConnection(cursor)).prepare_campaign(
            first_command, PostgresArtifactStore(),
            {"kind": "human", "id": "chemist-1"},
            job_id="57484a24-b9c0-4d01-ac32-d3a2ae50b998")

    assert observed["builder_input"] == first_normalized
    assert "request_key" not in observed["builder_input"]
    # The same stripped object is the persisted campaign input contract; keep
    # this plumbing assertion next to the behavior check so a future second
    # payload construction cannot silently re-introduce transport identity.
    source = inspect.getsource(PostgresRbfeReferenceResolver.prepare_campaign)
    assert "scientific_payload = _prepare_scientific_payload(payload)" in source
    assert "prepared = self._build_campaign(scientific_payload)" in source
    assert '"inputs": scientific_payload' in source


def test_builder_names_the_exact_ligand_when_analogue_preparation_fails():
    root = Path(__file__).resolve().parents[2]
    runtime = root / "openfe-runtime-v2/bin/python"
    script = r'''
import sys
sys.path.insert(0, "backend/motif")
from rdkit import Chem
import rbfe_campaign_builder as builder

builder.inspect_bound_ligands = lambda pdb: [
    {"resname": "LIG", "chain": "A", "residue_number": "1"}]
builder._apply_coordinate_policy = lambda pdb, policy, selector: (pdb, {
    "assembly": {"verdict": "CONFIRMED"},
    "chains": {"verdict": "CONFIRMED"},
    "altloc": {"verdict": "CONFIRMED"},
    "occupancy": {"verdict": "CONFIRMED"},
    "waters": {"verdict": "CONFIRMED", "kept_water_count": 0},
    "cofactors": {"verdict": "CONFIRMED"},
    "metals": {"verdict": "CONFIRMED"},
})
builder._extract_reference = lambda pdb, selector, smiles: Chem.MolFromSmiles("CCO")
builder._prepare_receptor = lambda pdb, **kwargs: (pdb, {})
builder._heavy_xyz_from_pdb = lambda pdb: [(0.0, 0.0, 0.0)]
builder._pose_geometry = lambda points, sdf: {"geometry_gate": "passed"}

def aligned(smiles, reference, **kwargs):
    if smiles == "[Na+]":
        raise ValueError("analogue has no usable common 3D core")
    return "dummy-sdf", {
        "canonical_smiles": "CCO", "core_rmsd_angstrom": 0.0,
        "minimum_bidirectional_coverage": 1.0,
    }
builder._aligned_pose = aligned

try:
    builder.build({
        "receptor_pdb": "ATOM      1  CA  ALA A   1       0.000   0.000   0.000\n",
        "compounds": [
            {"id": "parent", "smiles": "CCO"},
            {"id": "culprit", "smiles": "[Na+]"},
        ],
        "parent_id": "parent", "pose_strategy": "align_to_reference",
        "reference_ligand": {"resname": "LIG", "chain": "A", "residue_number": "1"},
    })
except ValueError as error:
    print(error)
else:
    raise SystemExit("builder unexpectedly succeeded")
'''
    completed = subprocess.run(
        [str(runtime), "-c", script], cwd=root, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert completed.returncode == 0, completed.stderr
    assert "ligand 'culprit' ([Na+])" in completed.stdout
    assert "no usable common 3D core" in completed.stdout
