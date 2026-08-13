"""AutoDock Vina pose baseline with explicit receptor/grid provenance."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _ligand_pdbqt(smiles: str, *, seed: int) -> tuple[str, str]:
    from meeko import MoleculePreparation, PDBQTWriterLegacy
    from rdkit import Chem
    from rdkit.Chem import AllChem

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"cannot parse ligand SMILES {smiles!r}")
    molecule = Chem.AddHs(molecule)
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = int(seed)
    if AllChem.EmbedMolecule(molecule, parameters) != 0:
        raise ValueError("ligand ETKDG embedding failed")
    if AllChem.MMFFHasAllMoleculeParams(molecule):
        AllChem.MMFFOptimizeMolecule(molecule, mmffVariant="MMFF94s")
    else:
        AllChem.UFFOptimizeMolecule(molecule)
    setup = MoleculePreparation().prepare(molecule)[0]
    pdbqt, ok, message = PDBQTWriterLegacy.write_string(
        setup, add_index_map=True, remove_smiles=False, bad_charge_ok=False)
    if not ok:
        raise ValueError(f"Meeko ligand preparation failed: {message}")
    canonical = Chem.MolToSmiles(Chem.RemoveHs(molecule), canonical=True,
                                 isomericSmiles=True)
    return pdbqt, canonical


def dock_vina(receptor_pdbqt: str, ligands: Iterable[dict[str, str]], *,
              center: Iterable[float], box_size: Iterable[float], seed: int = 0,
              exhaustiveness: int = 16, n_poses: int = 9,
              energy_range: float = 3.0, cpu: int = 1) -> tuple[dict[str, Any], bytes]:
    """Run real Vina docking; receptor preparation is an explicit upstream artifact."""
    import numpy as np
    from vina import Vina

    center_values, box_values = list(center), list(box_size)
    if len(center_values) != 3 or len(box_values) != 3 or any(value <= 0 for value in box_values):
        raise ValueError("Vina center and positive box_size must each have length three")
    receptor_digest = "sha256:" + hashlib.sha256(receptor_pdbqt.encode()).hexdigest()
    results, pose_blocks = [], []
    for index, ligand in enumerate(ligands):
        ligand_pdbqt, canonical = _ligand_pdbqt(ligand["smiles"], seed=seed + index)
        engine = Vina(sf_name="vina", cpu=cpu, seed=seed + index, verbosity=0)
        # The Vina Python API accepts ligand strings but receptor input only as a path.
        # Use its private validated parser through a task-local temporary file, never a
        # caller-selected filesystem path.
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".pdbqt") as receptor_file:
            receptor_file.write(receptor_pdbqt)
            receptor_file.flush()
            engine.set_receptor(receptor_file.name)
            engine.set_ligand_from_string(ligand_pdbqt)
            engine.compute_vina_maps(center=center_values, box_size=box_values)
            engine.dock(exhaustiveness=exhaustiveness, n_poses=n_poses)
            energies = np.asarray(engine.energies(
                n_poses=n_poses, energy_range=energy_range), dtype=float)
            poses = engine.poses(n_poses=n_poses, energy_range=energy_range)
        # energies() returns energy components, not RMSD. RMSD bounds live in
        # each pose's REMARK VINA RESULT line. Keeping those two interfaces
        # distinct avoids the particularly dangerous error of labelling an
        # intermolecular energy as a geometric RMSD.
        pose_results = [tuple(float(value) for value in match)
                        for match in re.findall(
                            r"REMARK VINA RESULT:\s+(-?\d+(?:\.\d+)?)\s+"
                            r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", poses)]
        scores = []
        for rank, row in enumerate(energies):
            result = pose_results[rank] if rank < len(pose_results) else None
            scores.append({
                "pose_rank": rank, "affinity_kcal_mol": float(row[0]),
                "intermolecular_energy_kcal_mol": float(row[1]),
                "intramolecular_energy_kcal_mol": float(row[2]),
                "torsional_energy_kcal_mol": float(row[3]),
                "unbound_or_best_pose_intra_kcal_mol": float(row[4]),
                "rmsd_lower_bound_angstrom": result[1] if result else None,
                "rmsd_upper_bound_angstrom": result[2] if result else None,
            })
        pose_blocks.append(f"MODEL_GROUP {ligand['id']}\n{poses}\nEND_MODEL_GROUP\n")
        results.append({"ligand_id": ligand["id"], "canonical_smiles": canonical,
                        "best_affinity_kcal_mol": scores[0]["affinity_kcal_mol"],
                        "poses": scores})
    poses_bytes = "".join(pose_blocks).encode()
    report = {
        "schema_version": "1.0", "kind": "autodock_vina_pose_ensemble",
        "vina_version": __import__("vina").__version__,
        "receptor_pdbqt_sha256": receptor_digest,
        "grid": {"center_angstrom": center_values, "box_size_angstrom": box_values},
        "seed": seed, "exhaustiveness": exhaustiveness, "n_poses": n_poses,
        "energy_range_kcal_mol": energy_range, "results": results,
        "poses_sha256": "sha256:" + hashlib.sha256(poses_bytes).hexdigest(),
        "claim_boundary": "docking score and pose hypothesis; not binding free energy",
    }
    report["digest"] = _digest(report)
    return report, poses_bytes


__all__ = ["dock_vina"]
