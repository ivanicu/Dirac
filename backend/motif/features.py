"""Versioned, label-free molecular feature releases for Motif predictors."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


DESCRIPTOR_NAMES = (
    "MolWt", "MolLogP", "TPSA", "HBD", "HBA", "RotatableBonds",
    "RingCount", "FractionCSP3", "HeavyAtomCount", "FormalCharge",
)


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _toolkit():
    try:
        from rdkit import Chem
        from rdkit.Chem import Crippen, Descriptors, Lipinski, rdFingerprintGenerator
    except ImportError as exc:  # pragma: no cover - installation boundary
        raise RuntimeError("RDKit is required for Motif molecular features") from exc
    return Chem, Crippen, Descriptors, Lipinski, rdFingerprintGenerator


def canonical_smiles(smiles: str) -> str:
    Chem, *_ = _toolkit()
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"cannot parse SMILES {smiles!r}")
    return Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)


def raw_features(smiles: str, *, radius: int = 2, n_bits: int = 2048,
                 use_chirality: bool = True) -> tuple[list[int], list[float], str]:
    """Return sparse Morgan bits, physicochemical descriptors and canonical identity."""
    Chem, Crippen, Descriptors, Lipinski, generators = _toolkit()
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"cannot parse SMILES {smiles!r}")
    canonical = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
    generator = generators.GetMorganGenerator(
        radius=radius, fpSize=n_bits, includeChirality=use_chirality)
    bits = sorted(int(index) for index in generator.GetFingerprint(molecule).GetOnBits())
    descriptors = [
        float(Descriptors.MolWt(molecule)),
        float(Crippen.MolLogP(molecule)),
        float(Descriptors.TPSA(molecule)),
        float(Lipinski.NumHDonors(molecule)),
        float(Lipinski.NumHAcceptors(molecule)),
        float(Lipinski.NumRotatableBonds(molecule)),
        float(Lipinski.RingCount(molecule)),
        float(Lipinski.FractionCSP3(molecule)),
        float(molecule.GetNumHeavyAtoms()),
        float(Chem.GetFormalCharge(molecule)),
    ]
    return bits, descriptors, canonical


def fit_feature_release(smiles: Iterable[str], *, radius: int = 2,
                        n_bits: int = 2048, use_chirality: bool = True) -> dict[str, Any]:
    """Fit descriptor scaling without accepting labels, preventing label leakage by API."""
    import numpy as np

    rows = [raw_features(value, radius=radius, n_bits=n_bits,
                         use_chirality=use_chirality) for value in smiles]
    if not rows:
        raise ValueError("feature release requires at least one molecule")
    descriptors = np.asarray([row[1] for row in rows], dtype=np.float64)
    mean = descriptors.mean(axis=0)
    scale = descriptors.std(axis=0)
    scale[scale < 1e-12] = 1.0
    release = {
        "schema_version": "1.0",
        "kind": "morgan_rdkit_descriptors",
        "morgan": {"radius": radius, "n_bits": n_bits,
                   "use_chirality": use_chirality},
        "descriptors": {"names": list(DESCRIPTOR_NAMES),
                        "mean": mean.tolist(), "scale": scale.tolist()},
        "feature_count": n_bits + len(DESCRIPTOR_NAMES),
        "fit_count": len(rows),
        "label_access": "forbidden_by_interface",
    }
    release["digest"] = _digest(release)
    return release


def transform(smiles: Iterable[str], release: dict[str, Any]):
    """Transform molecules after verifying the immutable feature release digest."""
    import numpy as np

    material = dict(release)
    expected = material.pop("digest", None)
    if expected != _digest(material):
        raise ValueError("feature release digest mismatch")
    config = release["morgan"]
    rows = [raw_features(value, radius=config["radius"], n_bits=config["n_bits"],
                         use_chirality=config["use_chirality"]) for value in smiles]
    matrix = np.zeros((len(rows), release["feature_count"]), dtype=np.float32)
    mean = np.asarray(release["descriptors"]["mean"], dtype=np.float32)
    scale = np.asarray(release["descriptors"]["scale"], dtype=np.float32)
    for index, (bits, descriptors, _) in enumerate(rows):
        matrix[index, bits] = 1.0
        matrix[index, config["n_bits"]:] = (
            np.asarray(descriptors, dtype=np.float32) - mean) / scale
    return matrix, [row[2] for row in rows]


__all__ = [
    "DESCRIPTOR_NAMES", "canonical_smiles", "fit_feature_release", "raw_features",
    "transform",
]
