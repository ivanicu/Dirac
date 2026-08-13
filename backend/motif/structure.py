"""Deterministic conformer ensembles and canonical structure artifacts."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def generate_conformers(smiles: str, *, count: int = 50, seed: int = 0,
                        prune_rms_thresh: float = 0.5, max_attempts: int = 1000,
                        minimize: bool = True) -> tuple[dict[str, Any], bytes]:
    """Generate ETKDGv3 conformers, minimize, energy-rank and Butina-cluster them."""
    from rdkit import Chem
    from rdkit.Chem import AllChem, rdMolAlign
    from rdkit.ML.Cluster import Butina

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"cannot parse SMILES {smiles!r}")
    molecule = Chem.AddHs(molecule)
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = int(seed)
    parameters.pruneRmsThresh = float(prune_rms_thresh)
    # Current RDKit exposes the embedding retry budget as maxIterations on
    # EmbedParameters (older wrapper examples called it maxAttempts).
    parameters.maxIterations = int(max_attempts)
    parameters.useRandomCoords = False
    parameters.enforceChirality = True
    conformer_ids = list(AllChem.EmbedMultipleConfs(molecule, numConfs=count, params=parameters))
    if not conformer_ids:
        raise ValueError("ETKDGv3 generated no conformers")
    force_field = "MMFF94s" if AllChem.MMFFHasAllMoleculeParams(molecule) else "UFF"
    energies: dict[int, float] = {}
    converged: dict[int, bool] = {}
    for conformer_id in conformer_ids:
        if force_field == "MMFF94s":
            properties = AllChem.MMFFGetMoleculeProperties(molecule, mmffVariant="MMFF94s")
            field = AllChem.MMFFGetMoleculeForceField(
                molecule, properties, confId=conformer_id)
        else:
            field = AllChem.UFFGetMoleculeForceField(molecule, confId=conformer_id)
        status = field.Minimize(maxIts=500) if minimize else 0
        energies[conformer_id] = float(field.CalcEnergy())
        converged[conformer_id] = status == 0
    distances = []
    for left in range(1, len(conformer_ids)):
        for right in range(left):
            distances.append(float(rdMolAlign.GetBestRMS(
                molecule, molecule, prbId=conformer_ids[left], refId=conformer_ids[right])))
    clusters = Butina.ClusterData(
        distances, len(conformer_ids), prune_rms_thresh, isDistData=True,
        reordering=True) if len(conformer_ids) > 1 else ((0,),)
    cluster_by_index = {index: cluster for cluster, members in enumerate(clusters)
                        for index in members}
    ordered = sorted(conformer_ids, key=lambda item: (energies[item], item))
    blocks = []
    canonical = Chem.MolToSmiles(Chem.RemoveHs(molecule), canonical=True,
                                 isomericSmiles=True)
    minimum = energies[ordered[0]]
    records = []
    for rank, conformer_id in enumerate(ordered):
        copy = Chem.Mol(molecule)
        copy.RemoveAllConformers()
        copy.AddConformer(molecule.GetConformer(conformer_id), assignId=True)
        copy.SetProp("_Name", f"motif-conformer-{rank}")
        copy.SetProp("MOTIF_CONFORMER_ID", str(conformer_id))
        copy.SetProp("MOTIF_ENERGY", f"{energies[conformer_id]:.8f}")
        copy.SetProp("MOTIF_RELATIVE_ENERGY", f"{energies[conformer_id] - minimum:.8f}")
        copy.SetProp("MOTIF_CLUSTER", str(cluster_by_index[conformer_ids.index(conformer_id)]))
        blocks.append(Chem.MolToMolBlock(copy, confId=0) +
                      f">  <MOTIF_ENERGY>\n{energies[conformer_id]:.8f}\n\n$$$$\n")
        records.append({"rank": rank, "conformer_id": conformer_id,
                        "energy": energies[conformer_id],
                        "relative_energy": energies[conformer_id] - minimum,
                        "cluster": cluster_by_index[conformer_ids.index(conformer_id)],
                        "converged": converged[conformer_id]})
    sdf = "".join(blocks).encode()
    report = {
        "schema_version": "1.0", "kind": "etkdgv3_conformer_ensemble",
        "canonical_smiles": canonical, "requested_count": count,
        "generated_count": len(conformer_ids), "cluster_count": len(clusters),
        "seed": seed, "prune_rms_thresh_angstrom": prune_rms_thresh,
        "force_field": force_field, "minimized": minimize, "conformers": records,
        "sdf_sha256": "sha256:" + hashlib.sha256(sdf).hexdigest(),
    }
    report["digest"] = _digest(report)
    return report, sdf


__all__ = ["generate_conformers"]
