#!/usr/bin/env python3
"""How much of real chemistry do these components actually handle?

    backend/env/bin/python backend/physics/coverage.py [--limit N] [--budget SEC]

validate.py answers "is it right" on molecules chosen to have a known answer.
This answers the other question, which is not the same one and cannot be
inferred from it: "does it run at all, on molecules nobody picked". A component
that is correct on four validation molecules and fails on a third of a drug
library is not a working component; it is a demo with good manners.

Each molecule runs in its own subprocess with a hard time budget, so a crash,
a hang or an out-of-memory kill costs one row instead of the sweep. Outcomes
are classified by CAUSE — unsupported element, SCF divergence, force-field
typing failure, timeout — because the aggregate number is useless for deciding
what to fix and the cause list is exactly the work queue.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# The shipped screening library is the wrong population for the σ-hole feature
# and measuring against it alone would have produced a flattering number: its
# 68 molecules contain H, C, N, O, S, F and nine chlorines — NO BROMINE AND NO
# IODINE, which are the halogens the feature exists for. A sweep that reports
# "100% of the library" while excluding the elements under test is a check on
# its own population, not on the code.
#
# This set is chosen the other way round: every entry is here because it is
# expected to stress a specific mechanism, including the ones expected to FAIL.
# A coverage suite with no failures in it has not found the boundary.
HARD_CASES = [
    # iodine — the strongest halogen-bond donor, and absent from 6-31G*
    ('iodobenzene',        'Ic1ccccc1',                       'iodine, basis coverage'),
    ('4-iodoaniline',      'Nc1ccc(I)cc1',                    'iodine, weak σ-hole expected'),
    ('1-iodo-4-nitrobenzene', 'O=[N+]([O-])c1ccc(I)cc1',      'iodine + EWG, strong σ-hole'),
    ('5-iodouracil',       'O=c1[nH]cc(I)c(=O)[nH]1',         'iodine on a nucleobase'),
    # bromine
    ('bromobenzene',       'Brc1ccccc1',                      'bromine baseline'),
    ('4-bromophenol',      'Oc1ccc(Br)cc1',                   'bromine, donor + acceptor'),
    ('bromhexine',         'CN(C1CCCCC1)Cc1c(N)c(Br)cc(Br)c1', 'real drug, two bromines'),
    ('halothane',          'FC(F)(F)C(Cl)Br',                 'three halogens on two carbons'),
    # charge — the PF6- class that produced a NaN field in the fields backend
    ('acetate anion',      'CC(=O)[O-]',                      'anion, charge handling'),
    ('choline cation',     'C[N+](C)(C)CCO',                  'cation, quaternary N'),
    ('zwitterion glycine', 'C(C(=O)[O-])[NH3+]',              'net-neutral zwitterion'),
    ('hexafluorophosphate', 'F[P-](F)(F)(F)(F)F',             'the actual PF6- case'),
    # hypervalent and second-row
    ('dimethyl sulfone',   'CS(C)(=O)=O',                     'hypervalent S(VI)'),
    ('trimethyl phosphate', 'COP(=O)(OC)OC',                  'P(V)'),
    ('boronic acid',       'OB(O)c1ccccc1',                   'boron, MMFF coverage'),
    ('TMS ether',          'C[Si](C)(C)OC',                   'silicon'),
    ('selenomethionine',   'C[Se]CC[C@H](N)C(=O)O',           'selenium chalcogen hole'),
    # topology
    ('macrocycle',         'C1CCCCCCCCCCCCCCC1',              'macrocycle, ring torsions'),
    ('biphenyl',           'c1ccc(-c2ccccc2)cc1',             'single hindered torsion'),
    # scale — where the atom cap and the time budget bite
    ('erythromycin',       'CC[C@H]1OC(=O)[C@H](C)[C@@H](O[C@H]2C[C@@](C)(OC)[C@@H](O)[C@H](C)O2)'
                           '[C@H](C)[C@@H](O[C@@H]2O[C@H](C)C[C@@H]([C@H]2O)N(C)C)[C@](C)(O)'
                           'C[C@@H](C)C(=O)[C@H](C)[C@@H](O)[C@]1(C)O', 'large natural product'),
]

ROOT = Path(__file__).resolve().parents[2]
LIBRARY_TS = ROOT / 'src/app.frontend.facets.molstar-rdkit.editable/facets/pharmacophore-designer/library.ts'
ENTRY_RE = re.compile(
    r"\{\s*id:\s*'([^']+)',\s*name:\s*'([^']+)',\s*smiles:\s*'([^']+)',\s*category:\s*'([^']+)'\s*\}"
)
PYTHON = str(ROOT / 'backend/env/bin/python')


def classify(error: str) -> str:
    lowered = error.lower()
    if 'basis set not found' in lowered:
        return 'unsupported element (basis)'
    if 'did not converge' in lowered:
        return 'SCF did not converge'
    if 'exceeds the interactive cap' in lowered:
        return 'over the atom cap'
    if 'mmff cannot type' in lowered or 'force field could not' in lowered:
        return 'MMFF cannot type'
    if 'electron number' in lowered and 'spin' in lowered:
        return 'open shell / charge-spin mismatch'
    if 'timeout' in lowered:
        return 'over the time budget'
    if 'killed' in lowered or 'memory' in lowered:
        return 'out of memory'
    return f'other: {error[:60]}'


def run_one(smiles: str, basis: str, budget: int) -> dict:
    """Worker mode, in a subprocess: embed, then try both components."""
    from rdkit import Chem, RDLogger
    from rdkit.Chem import AllChem
    RDLogger.DisableLog('rdApp.*')
    sys.path.insert(0, str(ROOT / 'backend'))

    out: dict = {}
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    if AllChem.EmbedMolecule(mol, randomSeed=42) != 0:
        return {'embed': 'failed'}
    AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
    molblock = Chem.MolToMolBlock(mol)
    out['n_atoms'] = mol.GetNumAtoms()
    out['elements'] = sorted({a.GetSymbol() for a in mol.GetAtoms()})

    from physics.torsion import compute_torsion_strain
    t0 = time.time()
    try:
        result = compute_torsion_strain(molblock, steps=18)
        out['torsion'] = {'ok': True, 'seconds': round(time.time() - t0, 2),
                          'n_scanned': result['meta']['n_scanned'],
                          'total_strain': result['total_strain_kcal'],
                          'unconverged': result['meta']['unconverged_minimisations']}
    except Exception as exc:                                    # noqa: BLE001
        out['torsion'] = {'ok': False, 'error': str(exc), 'seconds': round(time.time() - t0, 2)}

    from physics.mep_surface import compute_surface_mep
    t0 = time.time()
    try:
        result = compute_surface_mep(molblock, basis=basis, points_per_atom=40)
        out['sigma_hole'] = {'ok': True, 'seconds': round(time.time() - t0, 2),
                             'v_s_max': result['meta']['v_s_max_kcal_per_mol'],
                             'v_s_min': result['meta']['v_s_min_kcal_per_mol'],
                             'holes': result['meta']['sigma_holes_found'],
                             'n_basis': result['meta']['n_basis']}
    except Exception as exc:                                    # noqa: BLE001
        out['sigma_hole'] = {'ok': False, 'error': str(exc), 'seconds': round(time.time() - t0, 2)}
    return out


def drive(entries, basis: str, budget: int, workers: int):
    env = dict(os.environ, OMP_NUM_THREADS='2', MKL_NUM_THREADS='2')

    def one(entry):
        entry_id, name, smiles, category = entry
        cmd = [PYTHON, __file__, '--worker', smiles, '--basis', basis, '--budget', str(budget)]
        started = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=budget, env=env)
            payload = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
            if not payload:
                payload = {'torsion': {'ok': False, 'error': proc.stderr[-200:] or 'no output'},
                           'sigma_hole': {'ok': False, 'error': proc.stderr[-200:] or 'no output'}}
        except subprocess.TimeoutExpired:
            payload = {'torsion': {'ok': False, 'error': 'timeout'},
                       'sigma_hole': {'ok': False, 'error': 'timeout'}}
        payload.update(id=entry_id, name=name, category=category,
                       wall_seconds=round(time.time() - started, 1))
        print('.', end='', flush=True)
        return payload

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, entries))


def report(rows, basis: str):
    print('\n')
    for component in ('torsion', 'sigma_hole'):
        ok = [r for r in rows if r.get(component, {}).get('ok')]
        bad = [r for r in rows if not r.get(component, {}).get('ok')]
        print(f'{component}: {len(ok)}/{len(rows)} molecules '
              f'({100 * len(ok) / max(len(rows), 1):.0f}%)')
        if ok:
            times = sorted(r[component]['seconds'] for r in ok)
            print(f'    seconds  median {times[len(times) // 2]:.1f}  '
                  f'p90 {times[int(0.9 * (len(times) - 1))]:.1f}  max {times[-1]:.1f}')
        causes: dict[str, list[str]] = {}
        for r in bad:
            causes.setdefault(classify(r.get(component, {}).get('error', 'unknown')), []).append(r['name'])
        for cause, names in sorted(causes.items(), key=lambda kv: -len(kv[1])):
            shown = ', '.join(names[:4]) + (f' +{len(names) - 4} more' if len(names) > 4 else '')
            print(f'    {len(names):3d}  {cause}   [{shown}]')
        print()

    holes = [r for r in rows if r.get('sigma_hole', {}).get('ok') and r['sigma_hole']['holes'] > 0]
    print(f'σ-holes found in {len(holes)} molecules: '
          + ', '.join(f"{r['name']} ({r['sigma_hole']['v_s_max']:+.0f})" for r in holes[:8]))
    strained = [r for r in rows if r.get('torsion', {}).get('ok')
                and r['torsion']['total_strain'] > 3]
    print(f'{len(strained)} molecules embed with >3 kcal/mol total strain (ETKDG+MMFF500 input, '
          f'so this measures the INPUT, not the molecule)')
    print(f'\nbasis {basis}')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', metavar='SMILES')
    parser.add_argument('--basis', default='def2-svp')
    parser.add_argument('--budget', type=int, default=180)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--set', choices=('library', 'hard'), default='library')
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()

    if args.worker:
        print(json.dumps(run_one(args.worker, args.basis, args.budget)))
        return 0

    if args.set == 'hard':
        entries = [(name.replace(' ', '-'), name, smiles, why) for name, smiles, why in HARD_CASES]
    else:
        entries = ENTRY_RE.findall(LIBRARY_TS.read_text())
    if args.limit:
        entries = entries[:args.limit]
    print(f'sweeping {len(entries)} library molecules, basis {args.basis}, '
          f'{args.budget}s budget each, {args.workers} workers')
    rows = drive(entries, args.basis, args.budget, args.workers)
    report(rows, args.basis)
    out = Path('/tmp/claude-1000/dirac-physics-coverage.json')
    out.write_text(json.dumps(rows, indent=1))
    print(f'per-molecule detail: {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
