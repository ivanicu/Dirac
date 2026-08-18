from __future__ import annotations

import json
import random
from pathlib import Path
import subprocess

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Geometry import Point3D

from motif.rbfe_chemistry_evidence import (
    CHANGED,
    CONFIRMED,
    UNVERIFIED,
    automorphism_mapping_comparison,
    canonical_isomeric_identity,
    input_pose_identity_witness,
    mapping_change_evidence,
    mapping_depiction_contract,
    mapping_direction_audit,
    pose_geometry_evidence,
    require_resolved_stereochemistry,
)


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "openfe-runtime-v2/bin/python"
PLANNER = ROOT / "backend/motif/openfe_network_planner.py"


def _mol(smiles: str) -> Chem.Mol:
    molecule = Chem.MolFromSmiles(smiles)
    assert molecule is not None
    return molecule


def _ledger(evidence: dict) -> dict[str, dict]:
    return {row["dimension"]: row for row in evidence["ledger"]}


def _plan(tmp_path: Path, document: dict) -> dict:
    source, target = tmp_path / "planner-input.json", tmp_path / "planner-output.json"
    source.write_text(json.dumps(document))
    completed = subprocess.run(
        [str(RUNTIME), str(PLANNER), str(source), str(target)],
        text=True, capture_output=True, check=False, timeout=60)
    assert completed.returncode == 0, completed.stderr
    return json.loads(target.read_text())


@pytest.mark.parametrize("smiles, center_kind", [
    ("CC(O)F", "ATOM_TETRAHEDRAL"),
    ("FC=CF", "BOND_DOUBLE"),
])
def test_unspecified_stereo_is_unverified_and_hard_blocked(
        smiles: str, center_kind: str) -> None:
    identity = canonical_isomeric_identity(smiles)
    assert identity["stereo_policy_verdict"] == UNVERIFIED
    assert {row["kind"] for row in identity["unspecified_stereo"]} == {
        center_kind}
    with pytest.raises(ValueError, match="enumerate each stereoisomer"):
        require_resolved_stereochemistry(smiles, "analogue-unknown")


@pytest.mark.parametrize("first, second", [
    ("C[C@H](O)F", "C[C@@H](O)F"),
    ("F/C=C/F", r"F/C=C\F"),
])
def test_canonical_identity_preserves_cip_and_ez(
        first: str, second: str) -> None:
    first_identity = require_resolved_stereochemistry(first, "first")
    second_identity = require_resolved_stereochemistry(second, "second")
    assert first_identity["stereo_policy_verdict"] == CONFIRMED
    assert second_identity["stereo_policy_verdict"] == CONFIRMED
    assert (first_identity["canonical_isomeric_smiles"]
            != second_identity["canonical_isomeric_smiles"])
    assert (first_identity["canonical_connectivity_smiles"]
            == second_identity["canonical_connectivity_smiles"])
    assert first_identity["microstate"]["representation_verdict"] == CONFIRMED
    assert first_identity["microstate"]["solution_population_verdict"] == UNVERIFIED
    assert first_identity["microstate"]["identity_digest"].startswith("sha256:")


def test_input_pose_identity_detects_3d_cip_inversion() -> None:
    smiles = "C[C@H](O)CC"
    posed = Chem.AddHs(_mol(smiles))
    assert AllChem.EmbedMolecule(posed, randomSeed=7) == 0
    assert input_pose_identity_witness(smiles, posed, "analogue")[
        "verdict"] == CONFIRMED
    conformer = posed.GetConformer()
    for index in range(posed.GetNumAtoms()):
        point = conformer.GetAtomPosition(index)
        conformer.SetAtomPosition(index, Point3D(-point.x, point.y, point.z))
    inverted = input_pose_identity_witness(smiles, posed, "analogue")
    assert inverted["verdict"] == CHANGED
    assert "invert or erase" in inverted["reason"]
    assert (inverted["input"]["canonical_isomeric_smiles"]
            != inverted["coordinates"]["canonical_isomeric_smiles"])


def test_mapping_disagreement_is_automorphism_aware_and_directional() -> None:
    parent = _mol("Fc1ccccc1")
    proposal = _mol("Clc1ccccc1")
    index_exact = [(index, index) for index in range(7)]
    symmetry_equivalent = [
        (0, 0), (1, 1), (2, 6), (3, 5), (4, 4), (5, 3), (6, 2),
    ]
    comparison = automorphism_mapping_comparison(
        parent, proposal, index_exact, symmetry_equivalent)
    assert comparison["index_exact_jaccard"] > 0.7
    assert comparison["automorphism_aware_jaccard"] == 0.0
    assert comparison["verdict"] == CONFIRMED
    reverse = [(right, left) for left, right in symmetry_equivalent]
    direction = mapping_direction_audit(parent, proposal, index_exact, reverse)
    assert direction["verdict"] == CONFIRMED


@pytest.mark.parametrize("smiles", [
    "C[C@H](O)F",
    "F/C=C/F",
])
def test_randomized_explicit_h_atom_order_preserves_stereo_mapping_and_direction(
        smiles: str) -> None:
    parent = Chem.AddHs(_mol(smiles))
    randomizer = random.Random(20260817)
    for _ in range(80):
        order = list(range(parent.GetNumAtoms()))
        randomizer.shuffle(order)
        proposal = Chem.RenumberAtoms(parent, order)
        parent_to_proposal = {source: target
                              for target, source in enumerate(order)}
        forward = [
            (atom.GetIdx(), parent_to_proposal[atom.GetIdx()])
            for atom in parent.GetAtoms() if atom.GetAtomicNum() > 1
        ]
        evidence = mapping_change_evidence(
            parent, proposal, forward, microstate_contract_attached=True)
        assert evidence["verdict"] == CONFIRMED
        assert _ledger(evidence)["STEREO"]["verdict"] == CONFIRMED
        reverse = [(proposal_index, parent_index)
                   for parent_index, proposal_index in forward]
        assert mapping_direction_audit(
            parent, proposal, forward, reverse)["verdict"] == CONFIRMED
        reverse_evidence = mapping_change_evidence(
            proposal, parent, reverse, microstate_contract_attached=True)
        assert reverse_evidence["verdict"] == CONFIRMED
        assert _ledger(reverse_evidence)["STEREO"]["verdict"] == CONFIRMED


@pytest.mark.parametrize("smiles", [
    "C[C@H](O)F",
    "F/C=C/F",
])
def test_implicit_and_interleaved_explicit_h_share_exact_stereo_identity(
        smiles: str) -> None:
    implicit = _mol(smiles)
    explicit = Chem.AddHs(implicit)
    heavy = [atom.GetIdx() for atom in explicit.GetAtoms()
             if atom.GetAtomicNum() > 1]
    hydrogens = [atom.GetIdx() for atom in explicit.GetAtoms()
                 if atom.GetAtomicNum() == 1]
    order = [hydrogens[0], *heavy, *hydrogens[1:]]
    interleaved = Chem.RenumberAtoms(explicit, order)
    old_to_new = {source: target for target, source in enumerate(order)}
    mapping = [(index, old_to_new[index]) for index in heavy]

    assert (canonical_isomeric_identity(implicit)["microstate"]["identity_digest"]
            == canonical_isomeric_identity(interleaved)["microstate"][
                "identity_digest"])
    evidence = mapping_change_evidence(
        implicit, interleaved, mapping,
        microstate_contract_attached=True)
    assert evidence["verdict"] == CONFIRMED
    assert _ledger(evidence)["STEREO"] == {
        "dimension": "STEREO",
        "verdict": CONFIRMED,
        "summary": "mapped CIP/E/Z witnesses agree",
        "witnesses": [],
    }


@pytest.mark.parametrize("parent_smiles, proposal_smiles", [
    ("C[C@H](O)F", "C[C@@H](O)F"),
    ("F/C=C/F", r"F/C=C\F"),
])
def test_randomized_explicit_h_order_does_not_hide_real_stereo_change(
        parent_smiles: str, proposal_smiles: str) -> None:
    parent = Chem.AddHs(_mol(parent_smiles))
    proposal_source = Chem.AddHs(_mol(proposal_smiles))
    order = list(reversed(range(proposal_source.GetNumAtoms())))
    proposal = Chem.RenumberAtoms(proposal_source, order)
    old_to_new = {source: target for target, source in enumerate(order)}
    mapping = [
        (atom.GetIdx(), old_to_new[atom.GetIdx()])
        for atom in parent.GetAtoms() if atom.GetAtomicNum() > 1
    ]
    stereo = _ledger(mapping_change_evidence(
        parent, proposal, mapping,
        microstate_contract_attached=True))["STEREO"]
    assert stereo["verdict"] == CHANGED
    assert stereo["witnesses"]


@pytest.mark.parametrize("parent_smiles, proposal_smiles, pairs, dimension", [
    ("Fc1ccccc1", "Clc1ccccc1", [(i, i) for i in range(7)], "ELEMENT"),
    ("C1CCCCC1", "C1=CCCCC1", [(i, i) for i in range(6)], "BOND_ORDER"),
    ("C1CCCCC1", "CCCCCC", [(i, i) for i in range(6)], "RING_CYCLE_RANK"),
    ("CN", "C[NH3+]", [(0, 0), (1, 1)], "FORMAL_CHARGE"),
])
def test_structured_change_ledger_has_specific_witnesses(
        parent_smiles: str, proposal_smiles: str,
        pairs: list[tuple[int, int]], dimension: str) -> None:
    evidence = mapping_change_evidence(
        _mol(parent_smiles), _mol(proposal_smiles), pairs,
        microstate_contract_attached=True)
    row = _ledger(evidence)[dimension]
    assert row["verdict"] == CHANGED
    assert row["witnesses"]
    assert all(item["verdict"] in {CONFIRMED, CHANGED, UNVERIFIED}
               for item in evidence["ledger"])


def test_low_coverage_never_claims_unseen_chemistry_is_conserved() -> None:
    parent = _mol("c1ccccc1")
    proposal = _mol("Cc1ccccc1")
    evidence = mapping_change_evidence(
        parent, proposal, [(index, index + 1) for index in range(6)],
        microstate_contract_attached=False)
    ledger = _ledger(evidence)
    assert evidence["full_heavy_atom_coverage"] is False
    assert ledger["ELEMENT"]["verdict"] == UNVERIFIED
    assert ledger["CONNECTIVITY"]["verdict"] == UNVERIFIED
    assert ledger["BOND_ORDER"]["verdict"] == UNVERIFIED
    assert ledger["FORMAL_CHARGE"]["verdict"] == UNVERIFIED
    assert ledger["RING_CYCLE_RANK"]["verdict"] == UNVERIFIED
    assert ledger["PROTONATION_TAUTOMER"]["verdict"] == UNVERIFIED
    assert ledger["UNMAPPED"]["verdict"] == CHANGED
    assert ledger["STEREO"]["summary"] == "none in either structure"


def test_tautomer_bond_pattern_is_changed_but_analogue_hydrogens_are_not() -> None:
    tautomer = mapping_change_evidence(
        _mol("CC=O"), _mol("C=CO"), [(0, 0), (1, 1), (2, 2)])
    tautomer_row = _ledger(tautomer)["PROTONATION_TAUTOMER"]
    assert tautomer_row["verdict"] == CHANGED
    assert tautomer_row["witnesses"][0]["kind"] == "TAUTOMER_OR_VALENCE_STATE"

    analogue = mapping_change_evidence(
        _mol("c1ccccc1"), _mol("Cc1ccccc1"),
        [(index, index + 1) for index in range(6)])
    analogue_row = _ledger(analogue)["PROTONATION_TAUTOMER"]
    assert analogue_row["verdict"] == UNVERIFIED
    assert analogue_row["witnesses"][0][
        "kind"] == "ENDPOINTS_NOT_MICROSTATE_COMPARABLE"

    attested_analogue = mapping_change_evidence(
        _mol("c1ccccc1"), _mol("Cc1ccccc1"),
        [(index, index + 1) for index in range(6)],
        microstate_contract_attached=True)
    attested_ledger = _ledger(attested_analogue)
    assert attested_analogue["verdict"] == CHANGED
    assert attested_ledger["UNMAPPED"]["verdict"] == CHANGED
    assert attested_ledger["ELEMENT"]["verdict"] == CONFIRMED
    assert attested_ledger["CONNECTIVITY"]["verdict"] == CONFIRMED
    assert attested_ledger["BOND_ORDER"]["verdict"] == CONFIRMED
    assert attested_ledger["FORMAL_CHARGE"]["verdict"] == CONFIRMED
    assert attested_ledger["RING_CYCLE_RANK"]["verdict"] == CONFIRMED
    assert attested_ledger["PROTONATION_TAUTOMER"]["verdict"] == CONFIRMED
    assert attested_ledger["PROTONATION_TAUTOMER"]["witnesses"][0][
        "kind"] == "ATTESTED_ENDPOINTS_WITH_STRUCTURAL_DELTA"


@pytest.mark.parametrize("parent, proposal, change", [
    ("CC(O)C", "C[C@H](O)F", "ADDED_STEREOCENTER"),
    ("C[C@H](O)F", "CC(O)C", "REMOVED_STEREOCENTER"),
    ("FCCF", "F/C=C/F", "ADDED_STEREOCENTER"),
])
def test_added_or_removed_cip_ez_centers_are_explicit_changes(
        parent: str, proposal: str, change: str) -> None:
    evidence = mapping_change_evidence(
        _mol(parent), _mol(proposal), [(index, index) for index in range(4)],
        microstate_contract_attached=True)
    stereo = _ledger(evidence)["STEREO"]
    assert stereo["verdict"] == CHANGED
    assert stereo["witnesses"][0]["change"] == change


def test_depiction_contract_exists_even_for_zero_map() -> None:
    contract = mapping_depiction_contract(
        _mol("c1ccccc1"), _mol("C1CCCCC1"), [])
    assert contract["schema_version"] == "rbfe-depiction-index.v2"
    assert Chem.MolFromSmiles(contract["parent_smiles"]) is not None
    assert Chem.MolFromSmiles(contract["proposal_smiles"]) is not None
    assert contract["selected_heavy_atom_mapping"] == []
    assert contract["chemistry_evidence"]["verdict"] == UNVERIFIED


def _one_atom_molecule(x: float, *, protein: bool) -> Chem.Mol:
    editable = Chem.RWMol()
    editable.AddAtom(Chem.Atom("C"))
    molecule = editable.GetMol()
    conformer = Chem.Conformer(1)
    conformer.SetAtomPosition(0, Point3D(x, 0.0, 0.0))
    conformer.Set3D(True)
    molecule.AddConformer(conformer)
    if protein:
        info = Chem.AtomPDBResidueInfo()
        info.SetName(" CA ")
        info.SetResidueName("ALA")
        info.SetResidueNumber(42)
        info.SetChainId("A")
        molecule.GetAtomWithIdx(0).SetMonomerInfo(info)
    return molecule


def test_hard_clash_evidence_names_atom_residue_and_distance() -> None:
    evidence = pose_geometry_evidence(
        _one_atom_molecule(0.0, protein=True),
        _one_atom_molecule(1.2, protein=False), "proposal",
        hard_clash_floor_angstrom=1.5)
    assert evidence["verdict"] == CHANGED
    witness = evidence["nearest_pair_witness"]
    assert witness["protein_atom_name"] == "CA"
    assert witness["residue_name"] == "ALA"
    assert witness["residue_number"] == 42
    assert witness["chain_id"] == "A"
    assert witness["distance_angstrom"] == 1.2


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_planner_preserves_campaign_contract_and_all_depictions(
        tmp_path: Path) -> None:
    document = {
        "compounds": [
            {"id": "F-BEN", "smiles": "Fc1ccccc1"},
            {"id": "CL-BEN", "smiles": "Clc1ccccc1"},
        ],
        "seed": 1729,
        "mst_num": 1,
        "campaign_id": "c6a51efe-79c5-4d79-ad58-522e5cbebdb6",
        "campaign_scientific_generation": 7,
        "campaign_scientific_digest": "sha256:" + "7" * 64,
        "prepared_system_id": "5402b1d8-a826-427f-b0fe-563ff8c81bd8",
    }
    network = _plan(tmp_path, document)
    assert network["campaign_contract"] == {
        key: document[key] for key in (
            "campaign_id", "campaign_scientific_generation",
            "campaign_scientific_digest",
            "prepared_system_id")
    }
    # A SMILES-only planner has no exact endpoint microstate contract. It may
    # propose this edge to the posed-system builder, but may not call it
    # executable yet.
    assert network["execution_network_gate"]["verdict"] == UNVERIFIED
    edge = network["edges"][0]
    assert edge["status"] == "candidate"
    assert edge["execution_eligibility"]["verdict"] == UNVERIFIED
    assert {row["code"] for row in edge["execution_eligibility"]["reasons"]} >= {
        "CHEMISTRY_EVIDENCE_UNVERIFIED",
    }
    assert network["candidate_edges"] == [edge]
    assert network["planner_diagnostics"]["executable_edge_count"] == 0
    assert network["identity_contract"]["digest"].startswith("sha256:")
    assert network["depiction_contract"]["digest"].startswith("sha256:")
    assert len(network["depiction_contract"]["edges"]) == 1
    assert network["edges"][0]["depiction_contract"][
        "schema_version"] == "rbfe-depiction-index.v2"


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_topology_planning_does_not_trim_a_common_core_by_random_3d_distance(
        tmp_path: Path) -> None:
    network = _plan(tmp_path, {
        "compounds": [
            {"id": "BEN", "smiles": "c1ccccc1"},
            {"id": "TOL", "smiles": "Cc1ccccc1"},
        ],
        "seed": 1729,
        "mst_num": 1,
    })
    assert network["rejected_edges"] == []
    assert len(network["edges"]) == 1
    edge = network["edges"][0]
    assert {edge["left_id"], edge["right_id"]} == {"BEN", "TOL"}
    assert edge["mapped_heavy_atom_count"] == 6
    assert len(edge["selected_heavy_atom_mapping"]) == 6
    assert edge["mapping_score"] >= 0.8


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_planner_direct_entry_refuses_duplicate_compound_ids(
        tmp_path: Path) -> None:
    source, target = tmp_path / "duplicate-input.json", tmp_path / "output.json"
    source.write_text(json.dumps({
        "compounds": [
            {"id": "DUP", "smiles": "c1ccccc1"},
            {"id": "DUP", "smiles": "Fc1ccccc1"},
        ],
        "seed": 1729,
        "mst_num": 1,
    }))
    completed = subprocess.run(
        [str(RUNTIME), str(PLANNER), str(source), str(target)],
        text=True, capture_output=True, check=False, timeout=60)
    assert completed.returncode != 0
    assert "unique non-empty compound ids" in completed.stderr
    assert not target.exists()


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_public_planner_refuses_fixed_entrypoint_source_drift(monkeypatch) -> None:
    from motif import rbfe

    monkeypatch.setattr(
        rbfe, "_OPENFE_NETWORK_PLANNER_SHA256", "sha256:" + "0" * 64)
    with pytest.raises(RuntimeError, match="changed after the RBFE method version"):
        rbfe._plan_with_openfe([
            {"id": "A", "smiles": "c1ccccc1"},
            {"id": "B", "smiles": "Fc1ccccc1"},
        ])


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_preliminary_eligibility_requires_chemistry_stereo_and_direction(
        tmp_path: Path) -> None:
    ledger = [
        {"dimension": dimension, "verdict": CONFIRMED,
         "summary": "positive control", "witnesses": []}
        for dimension in (
            "SCOPE", "ELEMENT", "CONNECTIVITY", "BOND_ORDER",
            "FORMAL_CHARGE", "STEREO", "RING_CYCLE_RANK", "UNMAPPED",
            "PROTONATION_TAUTOMER")
    ]
    chemistry = {
        "schema_version": "rbfe-chemistry-change.v1",
        "verdict": CONFIRMED,
        "ledger": ledger,
    }

    def direct_call(chemistry_evidence: dict, direction_verdict: str) -> tuple:
        payload = {
            "selected_heavy": [[0, 0]],
            "mapping_score": 0.95,
            "left_charge": 0,
            "right_charge": 0,
            "chemistry_evidence": chemistry_evidence,
            "direction_audit": {"verdict": direction_verdict},
        }
        code = (
            "import json,sys;"
            "sys.path.insert(0,sys.argv[1]);"
            "from motif.openfe_network_planner import _execution_eligibility;"
            "p=json.loads(sys.argv[2]);"
            "p['selected_heavy']={tuple(x) for x in p['selected_heavy']};"
            "print(json.dumps(_execution_eligibility(**p)))"
        )
        completed = subprocess.run(
            [str(RUNTIME), "-c", code, str(ROOT / "backend"),
             json.dumps(payload)],
            text=True, capture_output=True, check=False, timeout=60)
        assert completed.returncode == 0, completed.stderr
        return tuple(json.loads(completed.stdout.strip().splitlines()[-1]))

    positive, hard_rejection = direct_call(chemistry, CONFIRMED)
    assert positive == {"verdict": CONFIRMED, "reasons": []}
    assert hard_rejection is False

    stereo_unverified = json.loads(json.dumps(chemistry))
    stereo_unverified["verdict"] = UNVERIFIED
    next(row for row in stereo_unverified["ledger"]
         if row["dimension"] == "STEREO")["verdict"] = UNVERIFIED
    negative, hard_rejection = direct_call(stereo_unverified, CONFIRMED)
    assert negative["verdict"] == UNVERIFIED
    assert {row["code"] for row in negative["reasons"]} >= {
        "CHEMISTRY_EVIDENCE_UNVERIFIED", "STEREOCHEMISTRY_UNVERIFIED"}
    assert hard_rejection is False

    negative, hard_rejection = direct_call(chemistry, UNVERIFIED)
    assert negative["verdict"] == UNVERIFIED
    assert {row["code"] for row in negative["reasons"]} == {
        "MAPPING_DIRECTION_UNVERIFIED"}
    assert hard_rejection is False


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_zero_map_is_rejected_with_named_culprit_and_never_executable(
        tmp_path: Path) -> None:
    network = _plan(tmp_path, {
        "compounds": [
            {"id": "BEN", "smiles": "c1ccccc1"},
            {"id": "NA", "smiles": "[Na+]"},
        ],
        "seed": 1729,
        "mst_num": 1,
    })
    assert network["edges"] == []
    assert len(network["rejected_edges"]) == 1
    rejected = network["rejected_edges"][0]
    assert rejected["culprit_endpoints"] == ["BEN", "NA"]
    assert rejected["mapped_heavy_atom_count"] == 0
    assert rejected["status"] == "rejected"
    assert rejected["execution_eligibility"]["verdict"] == UNVERIFIED
    assert network["execution_network_gate"]["verdict"] == UNVERIFIED
    assert network["execution_network_gate"]["culprit_edges"][0][
        "left_id"] == "BEN"
