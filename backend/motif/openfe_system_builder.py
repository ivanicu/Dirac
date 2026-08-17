"""Fixed OpenFE entrypoint that compiles attested coordinates into two RFE legs.

The caller resolves a prepared receptor and two receptor-aligned pose artifacts.
GUFE serialization is deliberately produced here, inside the pinned OpenFE
runtime; neither source bytes nor GUFE JSON are browser-owned inputs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile

import openfe
from openfe.protocols.openmm_rfe import RelativeHybridTopologyProtocol
from openff.units import unit

try:
    from motif.rbfe_binding import validate_campaign_binding
    from motif.rbfe_chemistry_evidence import (
        CHANGED, CONFIRMED, UNVERIFIED,
        canonical_isomeric_identity,
        input_pose_identity_witness,
        mapping_depiction_contract,
        mapping_direction_audit,
        pose_geometry_evidence,
        require_resolved_stereochemistry,
    )
except ModuleNotFoundError:  # fixed-entrypoint execution adds this directory only
    from rbfe_binding import validate_campaign_binding
    from rbfe_chemistry_evidence import (
        CHANGED, CONFIRMED, UNVERIFIED,
        canonical_isomeric_identity,
        input_pose_identity_witness,
        mapping_depiction_contract,
        mapping_direction_audit,
        pose_geometry_evidence,
        require_resolved_stereochemistry,
    )


_MAX_PDB_BYTES = 8 << 20
_MAX_SDF_BYTES = 2 << 20
_MIN_EXECUTION_MAPPING_SCORE = 0.8


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _validated_campaign_contract(value) -> dict:
    return validate_campaign_binding(value)


def _component(path: Path, name: str):
    component = openfe.SmallMoleculeComponent.from_sdf_file(path)
    if component is None:
        raise ValueError(f"{name} SDF contains no molecule")
    return component


def _formal_charge(component) -> int:
    molecule = component.to_rdkit()
    return int(sum(atom.GetFormalCharge() for atom in molecule.GetAtoms()))


def _canonical_smiles(component) -> str:
    return canonical_isomeric_identity(component.to_rdkit())[
        "canonical_isomeric_smiles"]


def _pose_geometry(protein, ligand, name: str) -> dict:
    """Prove that a pose is in the receptor frame and not catastrophically clashing.

    This is intentionally a coarse coordinate-frame gate, not a docking-quality
    claim.  It catches the two dangerous false positives: a ligand tens of
    Angstroms outside the protein and a ligand laid through protein atoms.
    """
    evidence = pose_geometry_evidence(
        protein.to_rdkit(), ligand.to_rdkit(), name,
        hard_clash_floor_angstrom=1.5, contact_ceiling_angstrom=6.0)
    witness = evidence["nearest_pair_witness"]
    residue = " ".join(str(value) for value in (
        witness.get("residue_name"), witness.get("chain_id"),
        witness.get("residue_number")) if value not in (None, "")) or "unknown residue"
    if evidence["verdict"] == CHANGED:
        raise ValueError(
            f"{name} pose hard-clashes with the prepared receptor: ligand atom "
            f"{witness['ligand_atom_index']} {witness['ligand_element']} -> protein atom "
            f"{witness['protein_atom_index']} {witness['protein_element']} "
            f"({residue}) at {witness['distance_angstrom']:.3f} A; require >= "
            f"{witness['hard_clash_floor_angstrom']:.3f} A")
    if evidence["verdict"] == UNVERIFIED:
        raise ValueError(
            f"{name} pose is outside the prepared receptor coordinate frame "
            f"(nearest atom pair {witness['distance_angstrom']:.3f} A; "
            "no contacts within 6.0 A)")
    return evidence


def build(document: dict) -> dict:
    if document.get("protocol_preset") != "openfe-rfe-standard-v1":
        raise ValueError("unsupported RBFE protocol preset")
    receptor_pdb = str(document.get("receptor_pdb") or "")
    parent_sdf = str(document.get("parent_sdf") or "")
    proposal_sdf = str(document.get("proposal_sdf") or "")
    if not receptor_pdb.strip() or not parent_sdf.strip() or not proposal_sdf.strip():
        raise ValueError("receptor PDB and both posed ligand SDFs are required")
    if len(receptor_pdb.encode()) > _MAX_PDB_BYTES:
        raise ValueError("receptor PDB exceeds 8 MiB")
    if len(parent_sdf.encode()) > _MAX_SDF_BYTES or len(proposal_sdf.encode()) > _MAX_SDF_BYTES:
        raise ValueError("posed ligand SDF exceeds 2 MiB")
    campaign_contract = _validated_campaign_contract(
        document.get("campaign_contract"))
    expected_receptor_sha256 = str(document.get("expected_receptor_sha256") or "")
    observed_receptor_sha256 = _sha(receptor_pdb)
    if not expected_receptor_sha256:
        raise ValueError("prepared receptor artifact digest attestation is required")
    if observed_receptor_sha256 != expected_receptor_sha256:
        raise ValueError(
            "prepared receptor bytes do not match the registered artifact digest: "
            f"received {observed_receptor_sha256!r}, expected "
            f"{expected_receptor_sha256!r}")

    with tempfile.TemporaryDirectory(prefix="dirac-openfe-system-") as temporary:
        root = Path(temporary)
        receptor_path, parent_path, proposal_path = (
            root / "receptor.pdb", root / "parent.sdf", root / "proposal.sdf")
        receptor_path.write_text(receptor_pdb)
        parent_path.write_text(parent_sdf)
        proposal_path.write_text(proposal_sdf)
        protein = openfe.ProteinComponent.from_pdb_file(receptor_path, name="receptor")
        parent = _component(parent_path, "parent")
        proposal = _component(proposal_path, "proposal")

    parent_geometry = _pose_geometry(protein, parent, "parent")
    proposal_geometry = _pose_geometry(protein, proposal, "proposal")

    parent_rdkit = parent.to_rdkit()
    proposal_rdkit = proposal.to_rdkit()
    parent_charge, proposal_charge = _formal_charge(parent), _formal_charge(proposal)
    parent_smiles, proposal_smiles = (
        _canonical_smiles(parent), _canonical_smiles(proposal))
    expected_parent = str(document.get("expected_parent_smiles") or "")
    expected_proposal = str(document.get("expected_proposal_smiles") or "")
    parent_label = str(document.get("parent_id") or "parent")
    proposal_label = str(document.get("proposal_id") or "proposal")
    expected_parent_identity = require_resolved_stereochemistry(
        expected_parent, parent_label)
    expected_proposal_identity = require_resolved_stereochemistry(
        expected_proposal, proposal_label)
    parent_pose_identity = input_pose_identity_witness(
        expected_parent, parent_rdkit, parent_label)
    proposal_pose_identity = input_pose_identity_witness(
        expected_proposal, proposal_rdkit, proposal_label)
    if (parent_pose_identity["verdict"] != CONFIRMED
            or proposal_pose_identity["verdict"] != CONFIRMED):
        raise ValueError(
            "posed SDF endpoints do not match the selected network edge: "
            f"received {parent_smiles!r} -> {proposal_smiles!r}, expected "
            f"{expected_parent_identity['canonical_isomeric_smiles']!r} -> "
            f"{expected_proposal_identity['canonical_isomeric_smiles']!r}; "
            f"identity verdicts {parent_pose_identity['verdict']} / "
            f"{proposal_pose_identity['verdict']}")
    if parent_charge != proposal_charge:
        raise ValueError(
            "charge-changing RBFE is not enabled: endpoint formal charges differ "
            f"({parent_charge} versus {proposal_charge})")

    mapper = openfe.LomapAtomMapper(time=20, threed=False)
    proposals = list(mapper.suggest_mappings(parent, proposal))
    if not proposals:
        raise ValueError(
            f"OpenFE LoMap could not construct a common endpoint atom mapping for "
            f"{parent_label!r} -> {proposal_label!r}; culprit endpoints: "
            f"{parent_smiles!r} -> {proposal_smiles!r}")
    mapping = max(proposals, key=openfe.lomap_scorers.default_lomap_score)
    mapping_score = float(openfe.lomap_scorers.default_lomap_score(mapping))
    if mapping_score <= 0:
        raise ValueError("OpenFE LoMap rejected the endpoint transformation")
    execution_reasons = ([] if mapping_score >= _MIN_EXECUTION_MAPPING_SCORE else [{
        "code": "MAPPING_SCORE_BELOW_EXECUTION_FLOOR",
        "message": (
            f"OpenFE mapping score {mapping_score:.3f} is below the execution "
            f"floor {_MIN_EXECUTION_MAPPING_SCORE:.3f}"),
    }])

    solvent = openfe.SolventComponent(
        positive_ion="Na", negative_ion="Cl",
        ion_concentration=0.15 * unit.molar)
    settings = RelativeHybridTopologyProtocol.default_settings()
    protocol = RelativeHybridTopologyProtocol(settings=settings)
    complex_a = openfe.ChemicalSystem(
        {"protein": protein, "solvent": solvent, "ligand": parent},
        name="parent-complex")
    complex_b = openfe.ChemicalSystem(
        {"protein": protein, "solvent": solvent, "ligand": proposal},
        name="proposal-complex")
    solvent_a = openfe.ChemicalSystem(
        {"solvent": solvent, "ligand": parent}, name="parent-solvent")
    solvent_b = openfe.ChemicalSystem(
        {"solvent": solvent, "ligand": proposal}, name="proposal-solvent")
    complex_transformation = openfe.Transformation(
        complex_a, complex_b, protocol=protocol, mapping=mapping,
        name="dirac-complex-leg")
    solvent_transformation = openfe.Transformation(
        solvent_a, solvent_b, protocol=protocol, mapping=mapping,
        name="dirac-solvent-leg")
    pairs = sorted([int(left), int(right)]
                   for left, right in mapping.componentA_to_componentB.items())
    heavy_pairs = [pair for pair in pairs
                   if parent_rdkit.GetAtomWithIdx(pair[0]).GetAtomicNum() > 1
                   and proposal_rdkit.GetAtomWithIdx(pair[1]).GetAtomicNum() > 1]
    if not heavy_pairs:
        raise ValueError(
            f"OpenFE LoMap produced a zero-heavy-atom map for {parent_label!r} -> "
            f"{proposal_label!r}; this edge is rejected and cannot enter an executable network")
    reverse_proposals = list(mapper.suggest_mappings(proposal, parent))
    if not reverse_proposals:
        raise ValueError(
            f"OpenFE LoMap returned {parent_label!r} -> {proposal_label!r} but no "
            "reverse mapping; A<->B direction audit is UNVERIFIED")
    reverse_mapping = max(
        reverse_proposals, key=openfe.lomap_scorers.default_lomap_score)
    reverse_pairs = sorted([int(left), int(right)]
                           for left, right in
                           reverse_mapping.componentA_to_componentB.items())
    direction_audit = mapping_direction_audit(
        parent_rdkit, proposal_rdkit, heavy_pairs, reverse_pairs)
    if direction_audit["verdict"] != CONFIRMED:
        raise ValueError(
            f"OpenFE LoMap A<->B direction audit is {direction_audit['verdict']} "
            f"for {parent_label!r} -> {proposal_label!r}")
    depiction_contract = mapping_depiction_contract(
        parent_rdkit, proposal_rdkit, heavy_pairs,
        microstate_contract_attached=True)
    chemistry_evidence = depiction_contract["chemistry_evidence"]
    state = {
        "parent_sdf_sha256": _sha(parent_sdf),
        "proposal_sdf_sha256": _sha(proposal_sdf),
        "parent_canonical_smiles": parent_smiles,
        "proposal_canonical_smiles": proposal_smiles,
        "parent_formal_charge": parent_charge,
        "proposal_formal_charge": proposal_charge,
        "atom_mapping": pairs,
        "parent_identity": expected_parent_identity,
        "proposal_identity": expected_proposal_identity,
        "parent_input_pose_identity": parent_pose_identity,
        "proposal_input_pose_identity": proposal_pose_identity,
        "mapping_direction_audit": direction_audit,
        "chemistry_evidence": chemistry_evidence,
        "campaign_contract": campaign_contract,
    }
    return {
        "complex_transformation": json.loads(complex_transformation.to_json()),
        "solvent_transformation": json.loads(solvent_transformation.to_json()),
        "build_report": {
            "engine": "OpenFE", "engine_version": openfe.__version__,
            "protocol_preset": "openfe-rfe-standard-v1",
            "protocol": {
                "sampler": settings.simulation_settings.sampler_method,
                "lambda_windows": settings.lambda_settings.lambda_windows,
                "equilibration": str(settings.simulation_settings.equilibration_length),
                "production": str(settings.simulation_settings.production_length),
                "protein_forcefield": "amber/ff14SB.xml",
                "ligand_forcefield": settings.forcefield_settings.small_molecule_forcefield,
                "solvent": "TIP3P · NaCl 0.15 M",
            },
            "receptor_pdb_sha256": observed_receptor_sha256,
            "receptor_source_pdb_id": str(document.get("source_pdb_id") or "") or None,
            "coordinate_frame_gate": {
                "status": "passed",
                "clash_floor_angstrom": 1.5,
                "contact_ceiling_angstrom": 6.0,
                "parent": parent_geometry,
                "proposal": proposal_geometry,
            },
            "mapping_score": mapping_score,
            "mapped_atom_count": len(pairs),
            "mapped_heavy_atom_count": len(heavy_pairs),
            "selected_atom_mapping": pairs,
            "selected_heavy_atom_mapping": heavy_pairs,
            "mapping_method": "LomapAtomMapper · reviewed receptor-frame poses",
            "mapping_direction_audit": direction_audit,
            "chemistry_evidence": chemistry_evidence,
            "input_pose_identity": {
                "parent": parent_pose_identity,
                "proposal": proposal_pose_identity,
            },
            "depiction_contract": depiction_contract,
            "execution_eligibility": {
                "verdict": CONFIRMED if not execution_reasons else UNVERIFIED,
                "reasons": execution_reasons,
                "minimum_mapping_score": _MIN_EXECUTION_MAPPING_SCORE,
                "requires_nonzero_heavy_map": True,
                "requires_direction_audit": True,
                "requires_resolved_stereochemistry": True,
                "requires_charge_conservation": True,
            },
            "campaign_contract": campaign_contract,
            "ligand_state": state,
            "ligand_state_digest": _sha(json.dumps(
                state, sort_keys=True, separators=(",", ":"))),
        },
    }


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: openfe_system_builder.py INPUT.json OUTPUT.json")
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        if source.stat().st_size > 12 << 20:
            raise ValueError("RBFE system-builder input exceeds 12 MiB")
        document = json.loads(source.read_text())
        result = build(document)
    except (ValueError, json.JSONDecodeError) as error:
        # The parent service must be able to show the scientific source problem
        # without scraping a Python traceback or exposing internal stderr.
        target.write_text(json.dumps({
            "ok": False,
            "error": {
                "code": "INVALID_SCIENTIFIC_SOURCE",
                "message": str(error),
            },
        }, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 2
    target.write_text(json.dumps(
        result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
