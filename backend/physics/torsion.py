"""Ligand torsional strain: how far uphill is the conformation you were given?

A deposited ligand is fitted into density, not minimised. It routinely sits
several kcal/mol above its own relaxed geometry, and that energy is paid out
of the binding free energy — so a docking score or an FEP result computed on a
strained pose is partly fiction. The problem is well documented in PDB ligand
validation and essentially invisible in design software, which shows you the
pose and never what it cost.

This module scans every rotatable bond, one at a time, with the rest of the
molecule allowed to relax, and reports:

    profile        energy vs dihedral, the curve to plot
    observed_deg   where this conformer actually sits on that curve
    local_strain   E(observed) − min(profile), per torsion

Three honesty constraints are built in rather than documented:

  1. The energy is MMFF94s, a force field. It is fast enough to be
     interactive and wrong enough that the number is a triage signal, not a
     thermodynamic quantity. `meta.method` says so on every response.
  2. Hydrogens are relaxed first by default. X-ray coordinates do not contain
     hydrogen positions; whatever RDKit added is arbitrary, and an unrelaxed
     H contact can invent several kcal/mol of "strain" that belongs to the
     model, not the molecule.
  3. Per-torsion strains are NOT summed into a total by default. Torsions are
     coupled; adding them double-counts. The reported `total_strain` is a
     separate, honest quantity: E(as given, H-relaxed) − E(fully minimised).

Thresholds for the verdict follow the ligand-validation literature's rough
consensus: under ~0.5 kcal/mol is noise, ~3 kcal/mol on a single torsion is
the point at which the pose itself should be doubted.
"""
from __future__ import annotations

import time

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, rdMolTransforms

# RDKit's standard rotatable-bond definition: a non-ring single bond between
# two non-terminal heavy atoms, excluding triple bonds.
ROTATABLE_SMARTS = '[!$(*#*)&!D1]-&!@[!$(*#*)&!D1]'

FORCE_FIELDS = ('MMFF94s', 'MMFF94')
# Restraint stiff enough to hold the angle, soft enough that the minimiser can
# still relieve a contact. A ±0.1° window at 1e4 pins the geometry so hard that
# a clash introduced by the rotation survives minimisation and is reported as
# torsional strain.
TORSION_FORCE_CONSTANT = 1.0e3
TORSION_WINDOW_DEG = 0.5
MINIMIZE_STEPS = 2000

VERDICTS = ((0.5, 'negligible'), (1.5, 'mild'), (3.0, 'notable'))


def _verdict(strain: float) -> str:
    for limit, label in VERDICTS:
        if strain < limit:
            return label
    return 'severe'


def _force_field(mol: Chem.Mol, variant: str, conf_id: int = -1):
    props = AllChem.MMFFGetMoleculeProperties(mol, mmffVariant=variant)
    if props is None:
        raise ValueError('MMFF cannot type this molecule (unsupported element?)')
    ff = AllChem.MMFFGetMoleculeForceField(mol, props, confId=conf_id)
    if ff is None:
        raise ValueError('MMFF force field could not be constructed')
    return ff


def _relax_hydrogens(mol: Chem.Mol, variant: str) -> float:
    """Minimise hydrogens with every heavy atom pinned.

    Not cosmetic: crystallographic coordinates carry no hydrogens, so the ones
    being scored were placed by geometry rules. Leaving them unrelaxed reports
    the placement's strain as the ligand's.
    """
    ff = _force_field(mol, variant)
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() > 1:
            ff.MMFFAddPositionConstraint(atom.GetIdx(), 0.0, 1.0e5)
    ff.Minimize(maxIts=MINIMIZE_STEPS)
    return float(ff.CalcEnergy())


def _dihedral_atoms(mol: Chem.Mol, j: int, k: int):
    """Pick i–j–k–l for the rotatable bond j–k, choosing the heaviest
    substituent on each end so the reported angle is reproducible."""
    def pick(centre: int, other: int):
        candidates = [n for n in mol.GetAtomWithIdx(centre).GetNeighbors()
                      if n.GetIdx() != other]
        if not candidates:
            return None
        heavy = [n for n in candidates if n.GetAtomicNum() > 1] or candidates
        return max(heavy, key=lambda a: (a.GetAtomicNum(), -a.GetIdx())).GetIdx()

    i = pick(j, k)
    l = pick(k, j)
    if i is None or l is None:
        return None
    return i, j, k, l


def _relaxed_scan(mol: Chem.Mol, quad, steps: int, variant: str):
    """Walk the dihedral around the circle, carrying the relaxed geometry.

    Setting a dihedral is a RIGID rotation of one half of the molecule. Doing
    that from the same starting geometry at every scan point drives large
    rotations straight through other atoms, and the resulting contact is then
    minimised only as far as a stiff restraint allows — so the profile reports
    a steric collision as a torsional barrier. Measured on aspirin's aryl-ester
    torsion, that mistake produced a 34 kcal/mol barrier and a 1099 kcal/mol
    "strain"; the same scan done incrementally gives a chemically sane curve.

    Stepping instead in 360/steps increments from the previous relaxed point
    keeps every rotation small enough for the minimiser to accommodate.

    A relaxed scan has HYSTERESIS: energy at a given angle depends on which
    way the walk arrived, because the rest of the molecule relaxes as it goes.
    Both directions are therefore walked and the lower envelope is kept. That
    also makes the OBSERVED angle comparable to the rest of the curve — it is
    the first point of both walks, scored by the same protocol, instead of a
    separately-minimised geometry with a different relaxation history. Scoring
    it separately overstated one amide torsion by 10 kcal/mol against its own
    24 kcal/mol barrier, which a smooth curve cannot produce at 15° off the
    minimum.
    """
    i, j, k, l = quad
    increment = 360.0 / steps
    unconverged = 0
    start = rdMolTransforms.GetDihedralDeg(mol.GetConformer(), i, j, k, l)
    energies: dict[float, float] = {}

    for direction in (+1, -1):
        carried = Chem.Mol(mol)
        for step in range(steps):
            angle = start + direction * increment * step
            wrapped = round((angle + 180.0) % 360.0 - 180.0, 4)
            rdMolTransforms.SetDihedralDeg(carried.GetConformer(), i, j, k, l, wrapped)
            ff = _force_field(carried, variant)
            ff.MMFFAddTorsionConstraint(i, j, k, l, False,
                                        wrapped - TORSION_WINDOW_DEG,
                                        wrapped + TORSION_WINDOW_DEG,
                                        TORSION_FORCE_CONSTANT)
            if ff.Minimize(maxIts=MINIMIZE_STEPS) != 0:
                unconverged += 1
            energy = float(ff.CalcEnergy())
            if wrapped not in energies or energy < energies[wrapped]:
                energies[wrapped] = energy

    observed_angle = round((start + 180.0) % 360.0 - 180.0, 4)
    profile = sorted(energies.items())
    return profile, unconverged, observed_angle, energies[observed_angle]


def compute_torsion_strain(molblock: str, steps: int = 24,
                           relax_hydrogens: bool = True,
                           max_torsions: int = 12,
                           variant: str = 'MMFF94s'):
    """Scan every rotatable bond and locate the given conformer on each curve."""
    t0 = time.time()
    if variant not in FORCE_FIELDS:
        raise ValueError(f'unknown force field {variant!r}; expected one of {FORCE_FIELDS}')

    mol = Chem.MolFromMolBlock(molblock, removeHs=False, sanitize=True)
    if mol is None:
        raise ValueError('RDKit cannot parse the molfile')
    mol = Chem.AddHs(mol, addCoords=True)
    if mol.GetNumConformers() == 0:
        raise ValueError('molfile carries no 3D conformer')

    baseline_energy = (_relax_hydrogens(mol, variant) if relax_hydrogens
                       else float(_force_field(mol, variant).CalcEnergy()))

    matches = mol.GetSubstructMatches(Chem.MolFromSmarts(ROTATABLE_SMARTS))
    torsions = []
    for j, k in matches[:max_torsions]:
        quad = _dihedral_atoms(mol, j, k)
        if quad is None:
            continue
        i, j, k, l = quad
        conf = mol.GetConformer()
        observed = rdMolTransforms.GetDihedralDeg(conf, i, j, k, l)

        profile, unconverged, observed, observed_energy = _relaxed_scan(
            mol, (i, j, k, l), steps, variant)

        energies = np.array([e for _, e in profile])
        e_min = float(energies.min())
        best_angle = float(profile[int(energies.argmin())][0])
        strain = observed_energy - e_min

        torsions.append({
            'atom_indices': [int(i), int(j), int(k), int(l)],
            'elements': [mol.GetAtomWithIdx(int(x)).GetSymbol() for x in (i, j, k, l)],
            'observed_deg': round(float(observed), 1),
            'min_energy_deg': round(best_angle, 1),
            'local_strain_kcal': round(float(strain), 2),
            'barrier_kcal': round(float(energies.max() - e_min), 2),
            'verdict': _verdict(strain),
            'scan_unconverged_points': unconverged,
            # Relative to this torsion's own minimum: the curve a chemist reads.
            'profile': [[round(a, 1), round(e - e_min, 3)] for a, e in profile],
        })

    # Global relaxation, for the one total that is defensible.
    relaxed = Chem.Mol(mol)
    ff = _force_field(relaxed, variant)
    ff.Minimize(maxIts=2000)
    global_min = float(ff.CalcEnergy())
    total_strain = baseline_energy - global_min

    torsions.sort(key=lambda t: -t['local_strain_kcal'])

    return {
        'torsions': torsions,
        'total_strain_kcal': round(total_strain, 2),
        'total_verdict': _verdict(total_strain),
        'meta': {
            'method': variant,
            'note': ('Force-field energies. A triage signal, not a thermodynamic '
                     'quantity; per-torsion strains are coupled and are not summed.'),
            'hydrogens_relaxed': relax_hydrogens,
            'scan_steps': steps,
            'n_rotatable_bonds': len(matches),
            'n_scanned': len(torsions),
            'unconverged_minimisations': sum(t['scan_unconverged_points'] for t in torsions),
            'baseline_energy_kcal': round(baseline_energy, 3),
            'global_min_energy_kcal': round(global_min, 3),
            'seconds': round(time.time() - t0, 2),
        },
    }
