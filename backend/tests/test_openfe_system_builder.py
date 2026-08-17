from __future__ import annotations

import json
import gzip
import hashlib
from pathlib import Path
import subprocess

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Geometry import Point3D


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "openfe-runtime-v2/bin/python"
BUILDER = ROOT / "backend/motif/openfe_system_builder.py"
DATA = ROOT / "openfe-runtime-v2/lib/python3.12/site-packages/openfe/tests/data"
CAMPAIGN_ID = "99db9c06-23e9-4552-b3ab-43c1a840e15d"
PREPARED_SYSTEM_ID = "15bcb5b7-26c4-4d5e-a7e9-f0a6a63f7d31"
CAMPAIGN_DIGEST = "sha256:" + "1" * 64


def _campaign_contract() -> dict:
    contract = {
        "schema_version": "rbfe-campaign-binding.v2",
        "campaign_id": CAMPAIGN_ID,
        "campaign_scientific_generation": 7,
        "campaign_scientific_digest": CAMPAIGN_DIGEST,
        "prepared_system_id": PREPARED_SYSTEM_ID,
        "network_digest": "sha256:" + "2" * 64,
        "verdict": "CONFIRMED",
    }
    contract["digest"] = "sha256:" + hashlib.sha256(json.dumps(
        contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return contract


def _network_document(*, campaign_context=None) -> dict:
    network = {
        "schema_version": "1.0",
        "kind": "rbfe_network_plan",
        "compounds": [
            {"id": "parent", "canonical_smiles": "Fc1ccccc1"},
            {"id": "proposal", "canonical_smiles": "Clc1ccccc1"},
        ],
        "edges": [{
            "edge_id": "edge-1", "left_id": "parent", "right_id": "proposal",
            "status": "planned", "mapping_score": 0.9,
            "selected_heavy_atom_mapping": [[index, index]
                                             for index in range(7)],
        }],
        "campaign_context": campaign_context,
    }
    network["digest"] = "sha256:" + hashlib.sha256(json.dumps(
        network, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return network


def _document() -> dict:
    ligands = DATA / "htf/t4_lysozyme_data"
    receptor = gzip.decompress(
        (ligands / "t4_lysozyme_solvated.pdb.gz").read_bytes()).decode()
    return {
        "protocol_preset": "openfe-rfe-standard-v1",
        "campaign_contract": _campaign_contract(),
        "receptor_pdb": receptor,
        "parent_sdf": (ligands / "fluorobenzene.sdf").read_text(),
        "proposal_sdf": (ligands / "chlorobenzene.sdf").read_text(),
        "expected_parent_smiles": "Fc1ccccc1",
        "expected_proposal_smiles": "Clc1ccccc1",
        "source_pdb_id": "181L",
        "expected_receptor_sha256": "sha256:" + hashlib.sha256(
            receptor.encode()).hexdigest(),
    }


def _run(tmp_path: Path, document: dict) -> subprocess.CompletedProcess[str]:
    source, target = tmp_path / "input.json", tmp_path / "output.json"
    source.write_text(json.dumps(document))
    return subprocess.run(
        [str(RUNTIME), str(BUILDER), str(source), str(target)],
        text=True, capture_output=True, check=False, timeout=30)


def _reordered_chiral_pose_pair(tmp_path: Path) -> tuple[str, str]:
    ligands = DATA / "htf/t4_lysozyme_data"
    anchor = next(molecule for molecule in Chem.SDMolSupplier(
        str(ligands / "fluorobenzene.sdf"), removeHs=False)
        if molecule is not None)
    anchor_conf = anchor.GetConformer()
    anchor_center = [
        sum(anchor_conf.GetAtomPosition(index)[axis]
            for index in range(anchor.GetNumAtoms())) / anchor.GetNumAtoms()
        for axis in range(3)
    ]

    parent = Chem.AddHs(Chem.MolFromSmiles("C[C@H](O)F"))
    assert AllChem.EmbedMolecule(parent, randomSeed=101) == 0
    AllChem.UFFOptimizeMolecule(parent)
    parent_conf = parent.GetConformer()
    parent_center = [
        sum(parent_conf.GetAtomPosition(index)[axis]
            for index in range(parent.GetNumAtoms())) / parent.GetNumAtoms()
        for axis in range(3)
    ]
    for index in range(parent.GetNumAtoms()):
        point = parent_conf.GetAtomPosition(index)
        parent_conf.SetAtomPosition(index, Point3D(
            point.x - parent_center[0] + anchor_center[0],
            point.y - parent_center[1] + anchor_center[1],
            point.z - parent_center[2] + anchor_center[2],
        ))

    heavy = [atom.GetIdx() for atom in parent.GetAtoms()
             if atom.GetAtomicNum() > 1]
    hydrogens = [atom.GetIdx() for atom in parent.GetAtoms()
                 if atom.GetAtomicNum() == 1]
    proposal = Chem.RenumberAtoms(
        parent, [hydrogens[0], *heavy, *hydrogens[1:]])
    paths = (tmp_path / "chiral-parent.sdf", tmp_path / "chiral-proposal.sdf")
    for path, molecule in zip(paths, (parent, proposal), strict=True):
        writer = Chem.SDWriter(str(path))
        writer.write(molecule)
        writer.close()
    return paths[0].read_text(), paths[1].read_text()


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_builder_compiles_both_internal_legs(tmp_path: Path) -> None:
    from motif import rbfe_pipeline

    completed = _run(tmp_path, _document())
    assert completed.returncode == 0, completed.stderr
    output = json.loads((tmp_path / "output.json").read_text())
    assert output["complex_transformation"]
    assert output["solvent_transformation"]
    report = output["build_report"]
    assert report["engine_version"] == "1.11.1"
    assert report["protocol"]["lambda_windows"] == 11
    assert report["mapped_atom_count"] == 12
    assert report["mapped_heavy_atom_count"] == 7
    assert report["selected_atom_mapping"] == report["ligand_state"]["atom_mapping"]
    assert report["mapping_method"].startswith("LomapAtomMapper")
    depiction = report["depiction_contract"]
    assert depiction["schema_version"] == "rbfe-depiction-index.v2"
    assert len(depiction["selected_heavy_atom_mapping"]) == 7
    assert Chem.MolFromSmiles(depiction["parent_smiles"]).GetNumHeavyAtoms() == 7
    assert Chem.MolFromSmiles(depiction["proposal_smiles"]).GetNumHeavyAtoms() == 7
    parent_depiction = Chem.MolFromSmiles(depiction["parent_smiles"])
    proposal_depiction = Chem.MolFromSmiles(depiction["proposal_smiles"])
    depiction_pairs = depiction["selected_heavy_atom_mapping"]
    element_pairs = [
        (parent_depiction.GetAtomWithIdx(left).GetSymbol(),
         proposal_depiction.GetAtomWithIdx(right).GetSymbol())
        for left, right in depiction_pairs
    ]
    assert sorted(pair for pair in element_pairs if pair[0] != pair[1]) == [("F", "Cl")]
    translation = dict(depiction_pairs)
    for bond in parent_depiction.GetBonds():
        left, right = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if left not in translation or right not in translation:
            continue
        matched = proposal_depiction.GetBondBetweenAtoms(
            translation[left], translation[right])
        assert matched is not None
        assert matched.GetBondType() == bond.GetBondType()
    assert report["ligand_state"]["parent_canonical_smiles"] == "Fc1ccccc1"
    assert report["mapping_direction_audit"]["verdict"] == "CONFIRMED"
    assert report["input_pose_identity"]["parent"]["verdict"] == "CONFIRMED"
    assert report["input_pose_identity"]["proposal"]["verdict"] == "CONFIRMED"
    ledger = {row["dimension"]: row for row in report["chemistry_evidence"]["ledger"]}
    assert set(ledger) == {
        "SCOPE", "ELEMENT", "CONNECTIVITY", "BOND_ORDER", "FORMAL_CHARGE",
        "STEREO", "RING_CYCLE_RANK", "UNMAPPED", "PROTONATION_TAUTOMER",
    }
    assert ledger["ELEMENT"]["verdict"] == "CHANGED"
    assert ledger["STEREO"]["summary"] == "none in either structure"
    assert report["execution_eligibility"]["verdict"] == "CONFIRMED"
    assert report["mapping_score"] >= report["execution_eligibility"][
        "minimum_mapping_score"]
    assert report["coordinate_frame_gate"]["status"] == "passed"
    assert report["coordinate_frame_gate"]["parent"][
        "minimum_heavy_atom_distance_angstrom"] >= 1.5
    witness = report["coordinate_frame_gate"]["parent"]["nearest_pair_witness"]
    assert witness["ligand_atom_index"] >= 0
    assert witness["protein_atom_index"] >= 0
    assert witness["residue_name"]

    # Positive control: the exact system-builder report receives the private
    # in-process seal and survives full structural recomputation.
    attestation = rbfe_pipeline._seal_mapping_attestation(report)
    rbfe_pipeline._validate_mapping_attestation(attestation)

    # A caller can reproduce all public fields and canonical digests, but a
    # JSON payload cannot reproduce the module-owned seal.
    inline = {key: value for key, value in attestation.items()
              if key != "_server_seal"}
    with pytest.raises(Exception, match="not server-sealed"):
        rbfe_pipeline._validate_mapping_attestation(inline)

    # Even recomputing the visible digest after inserting a fake witness does
    # not update the server seal issued over the original builder report.
    tampered = dict(attestation)
    tampered_chemistry = json.loads(json.dumps(
        attestation["chemistry_evidence"]))
    next(row for row in tampered_chemistry["ledger"]
         if row["dimension"] == "STEREO")["witnesses"].append({
             "forged": "caller-authored stereo witness"})
    tampered["chemistry_evidence"] = tampered_chemistry
    tampered["attestation_digest"] = (
        rbfe_pipeline._mapping_attestation_digest(tampered))
    with pytest.raises(Exception, match="changed after the system builder sealed"):
        rbfe_pipeline._validate_mapping_attestation(tampered)


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_builder_round_trip_keeps_reordered_explicit_h_stereo_in_source_index_space(
        tmp_path: Path) -> None:
    document = _document()
    parent_sdf, proposal_sdf = _reordered_chiral_pose_pair(tmp_path)
    document.update({
        "parent_sdf": parent_sdf,
        "proposal_sdf": proposal_sdf,
        "expected_parent_smiles": "C[C@H](O)F",
        "expected_proposal_smiles": "C[C@H](O)F",
        "parent_id": "R-parent",
        "proposal_id": "R-parent-reordered",
    })

    completed = _run(tmp_path, document)
    assert completed.returncode == 0, completed.stderr
    report = json.loads((tmp_path / "output.json").read_text())["build_report"]
    assert report["mapping_score"] == 1.0
    assert report["mapping_direction_audit"]["verdict"] == "CONFIRMED"
    assert report["input_pose_identity"]["parent"]["verdict"] == "CONFIRMED"
    assert report["input_pose_identity"]["proposal"]["verdict"] == "CONFIRMED"
    ledger = {row["dimension"]: row
              for row in report["chemistry_evidence"]["ledger"]}
    assert ledger["STEREO"] == {
        "dimension": "STEREO",
        "verdict": "CONFIRMED",
        "summary": "mapped CIP/E/Z witnesses agree",
        "witnesses": [],
    }
    assert report["chemistry_evidence"]["verdict"] == "CONFIRMED"
    assert report["execution_eligibility"]["verdict"] == "CONFIRMED"


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_builder_preserves_verified_campaign_binding(tmp_path: Path) -> None:
    document = _document()
    document["campaign_contract"] = _campaign_contract()
    completed = _run(tmp_path, document)
    assert completed.returncode == 0, completed.stderr
    output = json.loads((tmp_path / "output.json").read_text())
    report = output["build_report"]
    assert report["campaign_contract"] == document["campaign_contract"]
    assert report["ligand_state"]["campaign_contract"] == document[
        "campaign_contract"]


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_builder_refuses_missing_campaign_binding(tmp_path: Path) -> None:
    document = _document()
    del document["campaign_contract"]
    completed = _run(tmp_path, document)
    assert completed.returncode != 0
    output = json.loads((tmp_path / "output.json").read_text())
    assert "campaign binding must be a JSON object" in output["error"]["message"]


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_builder_refuses_tampered_campaign_binding(tmp_path: Path) -> None:
    document = _document()
    document["campaign_contract"] = _campaign_contract()
    document["campaign_contract"]["campaign_scientific_generation"] += 1
    completed = _run(tmp_path, document)
    assert completed.returncode != 0
    output = json.loads((tmp_path / "output.json").read_text())
    assert "does not match its digest" in output["error"]["message"]


def test_physical_preflight_refuses_legacy_unbound_network(monkeypatch) -> None:
    from motif import rbfe_pipeline

    network = _network_document(campaign_context=None)
    monkeypatch.setattr(
        rbfe_pipeline, "_read_json_ref",
        lambda _ctx, _reference, _role: (object(), network))

    class Context:
        @staticmethod
        def check_budget() -> None:
            return None

    with pytest.raises(Exception, match="refuses legacy unbound networks"):
        rbfe_pipeline.preflight_rbfe_edge(
            {"network_ref": {}, "edge_id": "edge-1"}, Context())


def test_campaign_binding_is_network_digest_bound_and_fail_closed() -> None:
    from motif import rbfe_pipeline

    context = {
        "campaign_id": CAMPAIGN_ID,
        "campaign_scientific_generation": 7,
        "campaign_scientific_digest": CAMPAIGN_DIGEST,
        "prepared_system_id": PREPARED_SYSTEM_ID,
    }
    network = _network_document(campaign_context=context)
    binding = rbfe_pipeline._campaign_binding(
        network, campaign_id=context["campaign_id"],
        campaign_scientific_generation=context[
            "campaign_scientific_generation"],
        campaign_scientific_digest=context["campaign_scientific_digest"],
        prepared_system_id=context["prepared_system_id"])
    assert binding["network_digest"] == network["digest"]
    assert binding["verdict"] == "CONFIRMED"
    assert binding["digest"].startswith("sha256:")

    audit_context = {**context, "state_digest": "sha256:" + "f" * 64}
    audit_network = _network_document(campaign_context=audit_context)
    with pytest.raises(Exception, match="exact Campaign scientific context"):
        rbfe_pipeline._campaign_binding(
            audit_network, campaign_id=context["campaign_id"],
            campaign_scientific_generation=context[
                "campaign_scientific_generation"],
            campaign_scientific_digest=context[
                "campaign_scientific_digest"],
            prepared_system_id=context["prepared_system_id"])

    network["compounds"][0]["canonical_smiles"] = "c1ccccc1"
    with pytest.raises(Exception, match="does not match its immutable network digest"):
        rbfe_pipeline._campaign_binding(
            network, campaign_id=context["campaign_id"],
            campaign_scientific_generation=context[
                "campaign_scientific_generation"],
            campaign_scientific_digest=context[
                "campaign_scientific_digest"],
            prepared_system_id=context["prepared_system_id"])


def test_preflight_refuses_raw_transformation_without_builder_attestation(
        monkeypatch) -> None:
    from motif import rbfe_pipeline

    context = {
        "campaign_id": "9e06f5a5-e31d-481c-a35d-eaa1c85f3c92",
        "campaign_scientific_generation": 3,
        "campaign_scientific_digest": "sha256:" + "3" * 64,
        "prepared_system_id": "027aacfa-b069-4e54-9211-a153ed04497d",
    }
    network = _network_document(campaign_context=context)
    binding = rbfe_pipeline._campaign_binding(
        network, campaign_id=context["campaign_id"],
        campaign_scientific_generation=context[
            "campaign_scientific_generation"],
        campaign_scientific_digest=context["campaign_scientific_digest"],
        prepared_system_id=context["prepared_system_id"])
    monkeypatch.setattr(
        rbfe_pipeline, "_read_json_ref",
        lambda _ctx, _reference, _role: (object(), network))

    class Context:
        @staticmethod
        def check_budget() -> None:
            return None

    with pytest.raises(Exception, match="server-built pose/mapping attestation"):
        rbfe_pipeline.preflight_rbfe_edge({
            "network_ref": {}, "edge_id": "edge-1",
            "campaign_binding": binding,
            "complex_transformation": {"raw": True},
            "solvent_transformation": {"raw": True},
        }, Context())

    with pytest.raises(Exception, match="not server-sealed"):
        rbfe_pipeline.preflight_rbfe_edge({
            "network_ref": {}, "edge_id": "edge-1",
            "campaign_binding": binding,
            "complex_transformation": {"raw": True},
            "solvent_transformation": {"raw": True},
            "mapping_attestation": {
                "mapping_score": 1.0,
                "mapping_direction_audit": {"verdict": "CONFIRMED"},
                "chemistry_evidence": {
                    "verdict": "CONFIRMED",
                    "ledger": [{
                        "dimension": "STEREO", "verdict": "CONFIRMED",
                        "summary": "caller says stereo is fine",
                        "witnesses": [{"forged": True}],
                    }],
                },
                "execution_eligibility": {"verdict": "CONFIRMED"},
            },
        }, Context())


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_server_refuses_system_builder_source_drift(monkeypatch) -> None:
    from motif import rbfe_pipeline

    monkeypatch.setattr(
        rbfe_pipeline, "_SYSTEM_BUILDER_SHA256", "sha256:" + "0" * 64)
    with pytest.raises(Exception, match="changed after the RBFE method version"):
        rbfe_pipeline._build_system({})


def test_aggregate_binding_rechecks_live_campaign_generation() -> None:
    from motif import rbfe_pipeline

    context = {
        "campaign_id": "7e33a279-7377-422e-87e5-08c1aceecf6b",
        "campaign_scientific_generation": 8,
        "campaign_scientific_digest": "sha256:" + "4" * 64,
        "prepared_system_id": "31da4895-d88c-4112-8c80-e2a9a1d4a7e1",
    }
    parent = _network_document(campaign_context=context)
    binding = rbfe_pipeline._campaign_binding(
        parent, campaign_id=context["campaign_id"],
        campaign_scientific_generation=context[
            "campaign_scientific_generation"],
        campaign_scientific_digest=context["campaign_scientific_digest"],
        prepared_system_id=context["prepared_system_id"])
    edge_network = {
        "parent_network_digest": parent["digest"],
        "campaign_binding": binding,
    }

    class Resolver:
        observed = None

        def assert_campaign_generation(self, campaign_id, version, digest, actor):
            self.observed = (campaign_id, version, digest, actor)
            return {"verdict": "CONFIRMED"}

    class Context:
        rbfe_reference_resolver = Resolver()
        actor = {"kind": "human", "id": "chemist-1"}

    assert rbfe_pipeline._assert_current_campaign_binding(
        {"campaign_binding": binding}, edge_network, Context()) == binding
    assert Context.rbfe_reference_resolver.observed == (
        context["campaign_id"], context["campaign_scientific_generation"],
        context["campaign_scientific_digest"], Context.actor)

    stale = dict(binding)
    stale["campaign_scientific_generation"] += 1
    with pytest.raises(Exception, match="campaign binding is invalid"):
        rbfe_pipeline._assert_current_campaign_binding(
            {"campaign_binding": stale}, edge_network, Context())


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_builder_refuses_pose_from_wrong_edge(tmp_path: Path) -> None:
    document = _document()
    document["expected_proposal_smiles"] = "Cc1ccccc1"
    completed = _run(tmp_path, document)
    assert completed.returncode != 0
    output = json.loads((tmp_path / "output.json").read_text())
    assert output["error"]["code"] == "INVALID_SCIENTIFIC_SOURCE"
    assert "do not match the selected network edge" in output["error"]["message"]


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
def test_builder_refuses_receptor_with_wrong_artifact_digest(tmp_path: Path) -> None:
    document = _document()
    document["expected_receptor_sha256"] = "sha256:" + "0" * 64
    completed = _run(tmp_path, document)
    assert completed.returncode != 0
    output = json.loads((tmp_path / "output.json").read_text())
    assert output["error"]["code"] == "INVALID_SCIENTIFIC_SOURCE"
    assert "do not match the registered artifact digest" in output["error"]["message"]


@pytest.mark.skipif(not RUNTIME.is_file(), reason="pinned OpenFE runtime unavailable")
@pytest.mark.parametrize("receptor_name, expected_fragment", [
    ("cdk8/cdk8_protein.pdb", "hard-clashes"),
    ("181l_only.pdb", "outside the prepared receptor coordinate frame"),
])
def test_builder_refuses_mismatched_coordinate_frame(
        tmp_path: Path, receptor_name: str, expected_fragment: str) -> None:
    document = _document()
    receptor = (DATA / receptor_name).read_text()
    document["receptor_pdb"] = receptor
    document["expected_receptor_sha256"] = "sha256:" + hashlib.sha256(
        receptor.encode()).hexdigest()
    completed = _run(tmp_path, document)
    assert completed.returncode != 0
    output = json.loads((tmp_path / "output.json").read_text())
    assert expected_fragment in output["error"]["message"]
