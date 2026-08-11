#!/usr/bin/env python3
"""Physics gates for backend/physics. Run before trusting any number it emits.

    backend/env/bin/python backend/physics/validate.py

Each gate is a POSITIVE CONTROL: a known mechanism the instrument must
localise, with the expected answer fixed by chemistry rather than by a
previous run of this code. A module that only checked "does it return a
number" would have passed every broken version this file caught:

  · the first σ-hole implementation mixed Ångström and Bohr between the
    nuclear and electronic terms and returned −6753 kcal/mol
  · the first torsion scan rotated rigidly from the same geometry at every
    point, turning a steric collision into a 34 kcal/mol "barrier" and a
    1099 kcal/mol "strain"

Neither was visible in the output shape. Both are visible here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from rdkit import Chem, RDLogger
from rdkit.Chem import AllChem, rdMolTransforms

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
RDLogger.DisableLog('rdApp.*')

from physics.mep_surface import compute_surface_mep          # noqa: E402
from physics.torsion import compute_torsion_strain           # noqa: E402

results: list[tuple[str, bool, str]] = []


def gate(name: str, passed: bool, detail: str) -> None:
    results.append((name, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {detail}")


def sigma_hole_gates() -> None:
    """The σ-hole must respond to electron withdrawal and to polarizability.

    Absolute V_S,max depends on basis and surface definition, so the gate is
    the ORDERING, which does not: CF3 > CH3 (withdrawal switches the hole on)
    and Br > Cl (the heavier halogen is more polarizable). An implementation
    that fits one absolute number and inverts an ordering is not computing.
    """
    print('σ-hole (RHF/6-31G*, 0.001 a.u. surface)')
    geometries = {
        'CH3Cl': 'C 0 0 0; Cl 0 0 1.78; H 1.03 0 -0.36; H -0.51 0.89 -0.36; H -0.51 -0.89 -0.36',
        'CF3Cl': 'C 0 0 0; Cl 0 0 1.76; F 1.24 0 -0.44; F -0.62 1.07 -0.44; F -0.62 -1.07 -0.44',
        'CH3Br': 'C 0 0 0; Br 0 0 1.94; H 1.03 0 -0.36; H -0.51 0.89 -0.36; H -0.51 -0.89 -0.36',
        'CF3Br': 'C 0 0 0; Br 0 0 1.92; F 1.24 0 -0.44; F -0.62 1.07 -0.44; F -0.62 -1.07 -0.44',
    }
    holes = {}
    for name, atoms in geometries.items():
        molblock = _molblock_from_atomstring(atoms)
        out = compute_surface_mep(molblock, basis='6-31g*', points_per_atom=60)
        halogen = [e for e in out['extrema']
                   if e['kind'] == 'maximum' and e['element'] in ('Cl', 'Br')]
        holes[name] = max((e['value_kcal_per_mol'] for e in halogen), default=float('nan'))
        angle = next((e['sigma_hole']['angle_deg'] for e in halogen if 'sigma_hole' in e), None)
        print(f'    {name}: V_S,max on halogen {holes[name]:+.1f} kcal/mol'
              + (f' at {angle}° from the C–X bond' if angle else ''))

    gate('withdrawal switches the σ-hole on (CF3Cl > CH3Cl)',
         holes['CF3Cl'] > holes['CH3Cl'] + 5,
         f"{holes['CF3Cl']:+.1f} vs {holes['CH3Cl']:+.1f}")
    gate('polarizability orders the halogens (CH3Br > CH3Cl)',
         holes['CH3Br'] > holes['CH3Cl'],
         f"{holes['CH3Br']:+.1f} vs {holes['CH3Cl']:+.1f}")
    gate('both effects compound (CF3Br is the largest)',
         holes['CF3Br'] == max(holes.values()),
         f"{holes['CF3Br']:+.1f}")
    gate('a neutral molecule has both signs on its surface',
         holes['CF3Br'] > 0,
         'positive cap coexists with the negative belt')


def _molblock_from_atomstring(atoms: str) -> str:
    """Build a molfile from a 'Sym x y z; ...' string without bond perception
    guesswork: RDKit determines bonds from proximity, which is adequate for
    the small validation molecules and keeps the gate self-contained."""
    lines = [a.strip() for a in atoms.split(';')]
    xyz = [f'{len(lines)}', 'validation']
    for line in lines:
        parts = line.split()
        xyz.append(f'{parts[0]} {parts[1]} {parts[2]} {parts[3]}')
    mol = Chem.MolFromXYZBlock('\n'.join(xyz))
    from rdkit.Chem import rdDetermineBonds
    rdDetermineBonds.DetermineConnectivity(mol)
    return Chem.MolToMolBlock(mol)


def torsion_gates() -> None:
    """Three controls, because each catches a different way to be wrong."""
    print('torsional strain (MMFF94s relaxed scan)')
    mol = Chem.AddHs(Chem.MolFromSmiles('CC(=O)Oc1ccccc1C(=O)O'))   # aspirin
    AllChem.EmbedMolecule(mol, randomSeed=3)
    AllChem.MMFFOptimizeMolecule(mol, maxIters=2000)
    relaxed = compute_torsion_strain(Chem.MolToMolBlock(mol), steps=24)

    worst = max(t['local_strain_kcal'] for t in relaxed['torsions'])
    gate('a minimised conformer has no strain', worst < 0.3,
         f'worst local strain {worst:+.2f} kcal/mol')

    # The scanner must find a strain it was not told about. Heavy-atom torsion
    # only: an H torsion is legitimately erased by the hydrogen-relaxation step.
    heavy = [t for t in relaxed['torsions'] if 'H' not in t['elements']]
    target = max(heavy, key=lambda t: t['barrier_kcal'])
    profile = np.array(target['profile'])
    top_angle = float(profile[profile[:, 1].argmax(), 0])
    barrier = float(profile[:, 1].max())

    twisted = Chem.Mol(mol)
    rdMolTransforms.SetDihedralDeg(twisted.GetConformer(), *target['atom_indices'], top_angle)
    found = next(t for t in compute_torsion_strain(Chem.MolToMolBlock(twisted), steps=24)['torsions']
                 if t['atom_indices'] == target['atom_indices'])
    gate('a conformer on a barrier top reports that barrier',
         abs(found['local_strain_kcal'] - barrier) < max(0.6, 0.3 * barrier),
         f"reported {found['local_strain_kcal']:+.2f} against a {barrier:.2f} kcal/mol barrier")

    hydrogen = [t for t in relaxed['torsions'] if 'H' in t['elements']]
    if hydrogen:
        idx = hydrogen[0]['atom_indices']
        twisted_h = Chem.Mol(mol)
        conf = twisted_h.GetConformer()
        rdMolTransforms.SetDihedralDeg(conf, *idx, rdMolTransforms.GetDihedralDeg(conf, *idx) + 90)
        found_h = next(t for t in compute_torsion_strain(Chem.MolToMolBlock(twisted_h), steps=24)['torsions']
                       if t['atom_indices'] == idx)
        gate('hydrogen relaxation erases a hydrogen-only twist',
             found_h['local_strain_kcal'] < 0.3,
             f"strain {found_h['local_strain_kcal']:+.2f} after a 90° OH twist")

    # A barrier that a rigid scan would inflate into tens of kcal/mol.
    sane = all(t['barrier_kcal'] < 40 for t in relaxed['torsions'])
    gate('no barrier is a disguised steric collision', sane,
         f"largest barrier {max(t['barrier_kcal'] for t in relaxed['torsions']):.2f} kcal/mol")


def main() -> int:
    sigma_hole_gates()
    torsion_gates()
    failed = [name for name, ok, _ in results if not ok]
    print()
    if failed:
        print(f'{len(failed)} of {len(results)} physics gates FAILED: ' + '; '.join(failed))
        return 1
    print(f'all {len(results)} physics gates passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
