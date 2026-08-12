#!/usr/bin/env python3
"""Freeze today's v1 responses BEFORE the routes change, then diff against them.

ADR-005 makes v2 the authority and turns v1 into `to_v1(v2_response)`. That
migration is only safe if v1's observable shape is pinned first — and pinned from
the RUNNING service, not from a reading of the handler, because the handler is
exactly what is about to change.

WHAT IS COMPARED, and what is deliberately not: the SHAPE and the scientifically
meaningful VALUES. Timings, request ids, `computed_at` and the cube's bytes are
volatile by design; a golden that included them would fail on every run and be
turned off within a day. So the cube is reduced to its SHA-256 and its length —
which is a stronger check than comparing text, because it catches a one-voxel change
that a truncated diff would hide.

Usage:
    python3 scripts/capture_v1_golden.py --write     # freeze current behaviour
    python3 scripts/capture_v1_golden.py             # compare; exit 1 on drift
Exit: 0 match · 1 drift · 2 could not run (no daemon, no RDKit)
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
GOLDEN = ROOT / 'contracts' / 'golden' / 'v1_responses.json'
BASE = 'http://127.0.0.1:8901'

# Volatile by nature: they differ between two correct runs. Dropped rather than
# compared, and LISTED here so the exclusion is a decision on the record instead of
# a silently forgiving diff.
VOLATILE = {
    'total_seconds', 'scf_seconds', 'cube_seconds', 'cube_predicted_seconds',
    'computed_at', 'request_id', 'cache', 'stored', 'seconds',
}


FIXTURES = ROOT / 'contracts' / 'golden' / 'fixtures'


def molfile(smiles: str, seed: int) -> str:
    """A FIXED molfile from disk, embedded once and committed.

    Embedding per run made the golden useless in a way that took one run to show:
    a fresh conformer means a fresh cube, so `cube_sha256` could never be compared
    across runs — the strongest check in the file would have been the one thing
    guaranteed to differ. The geometry is now frozen in the repo.
    """
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / f'{smiles_slug(smiles)}_{seed}.mol'
    if path.exists():
        return path.read_text()
    from rdkit import Chem
    from rdkit.Chem import AllChem
    m = Chem.AddHs(Chem.MolFromSmiles(smiles))
    AllChem.EmbedMolecule(m, randomSeed=seed)
    text = Chem.MolToMolBlock(m)
    path.write_text(text)
    return text


def smiles_slug(smiles: str) -> str:
    import re as _re
    return _re.sub(r'[^A-Za-z0-9]+', '_', smiles).strip('_').lower()[:40]


def post(path: str, payload: dict, timeout: float = 400) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return {'http': fh.status, 'body': json.load(fh)}
    except urllib.error.HTTPError as e:
        return {'http': e.code, 'body': json.loads(e.read().decode() or '{}')}


def reduce_body(body: dict) -> dict:
    """Keep shape and meaning; replace bulk with a digest."""
    out: dict = {}
    for k, v in sorted(body.items()):
        if k == 'cube' and isinstance(v, str):
            out['cube_sha256'] = hashlib.sha256(v.encode()).hexdigest()
            out['cube_len'] = len(v)
        elif k == 'molfile' and isinstance(v, str):
            out['molfile_sha256'] = hashlib.sha256(v.encode()).hexdigest()
        elif k == 'meta' and isinstance(v, dict):
            out['meta_keys'] = sorted(v)
            out['meta_values'] = {mk: mv for mk, mv in sorted(v.items())
                                  if mk not in VOLATILE
                                  and not isinstance(mv, (list, dict))}
        elif k == 'error':
            # The MESSAGE is part of the contract for a refusal — it is what a
            # chemist reads — but it carries measured numbers that legitimately
            # move. Keep the first clause, which is the classification.
            out['error_head'] = str(v).split('—')[0].strip()[:80]
        else:
            out[k] = v
    return out


CASES: list[tuple[str, str, dict]] = [
    ('mep_ethanol', '/field',
     {'molecule': 'CCO', 'seed': 20260811, 'kind': 'mep'}),
    ('mep_pf6_refusal', '/field',
     {'molecule': 'F[P-](F)(F)(F)(F)F', 'seed': 11, 'kind': 'mep'}),
    ('homo_water', '/field',
     {'molecule': 'O', 'seed': 3, 'kind': 'homo', 'basis': 'sto-3g'}),
    ('homo_zero_budget_refusal', '/field',
     {'molecule': 'c1ccccc1O', 'seed': 12, 'kind': 'homo', 'basis': 'sto-3g',
      'max_seconds': 0}),
    ('homo_bad_basis_refusal', '/field',
     {'molecule': 'Ic1ccccc1', 'seed': 5, 'kind': 'homo', 'basis': '6-31g'}),
    ('embed_aspirin', '/embed',
     {'smiles': 'CC(=O)Oc1ccccc1C(=O)O'}),
]


def parity_probe() -> dict:
    """A request guaranteed to be COLD, so the cache-parity claim has a witness that
    this run created rather than inherited.

    MEASURED FIRST, because the trick only works if it is true: the Gaussian cube
    bytes are byte-identical regardless of the molfile's TITLE line (verified — the
    same geometry with three different titles produced cube sha 96e0aca35c5f every
    time, while the cache key changed and the request went from db to computed). So a
    nonce in the title buys a cold key without changing the science.

    Water, because the probe leaves a cache row behind on every run and water's cube
    is tens of KB rather than the 2.5 MB an ethanol probe would accumulate.
    """
    import os
    base = molfile('O', 3).split('\n')
    base[0] = 'dirac-parity-probe-' + os.urandom(6).hex()
    mf = '\n'.join(base)
    first = post('/field', {'molfile': mf, 'kind': 'mep'})
    import time as _t
    _t.sleep(4.0)
    second = post('/field', {'molfile': mf, 'kind': 'mep'})
    return {
        'parity_probe': {'path': '/field', 'http': first['http'],
                         'body': reduce_body(first['body']),
                         'cache': (first['body'].get('meta') or {}).get('cache')},
        'parity_probe__second_request': {
            'path': '/field', 'http': second['http'],
            'body': reduce_body(second['body']),
            'cache': (second['body'].get('meta') or {}).get('cache')},
    }


def run_cases() -> dict:
    """Each field case is captured TWICE, and both states are kept.

    The first capture run showed why: the same request returns materially different
    META VALUES on a cache hit — 12 of them None, because app.field_cube does not
    store the grid metadata — and `method` goes the other way, None on compute and
    'gasteiger' on the hit. Averaging that into one golden would have produced a
    file that flakes on the second run; recording both states turns the gap into
    evidence with a name.
    """
    out: dict = {}
    import time as _t
    for name, path, spec in CASES:
        payload = dict(spec)
        smiles = payload.pop('molecule', None)
        seed = payload.pop('seed', 0)
        if smiles is not None:
            payload['molfile'] = molfile(smiles, seed)
        first = post(path, payload)
        out[name] = {'path': path, 'http': first['http'],
                     'body': reduce_body(first['body']),
                     'cache': (first['body'].get('meta') or {}).get('cache')}
        if path == '/field' and first['body'].get('ok'):
            _t.sleep(2.0)          # the persist thread is asynchronous by design
            second = post(path, payload)
            out[name + '__second_request'] = {
                'path': path, 'http': second['http'],
                'body': reduce_body(second['body']),
                'cache': (second['body'].get('meta') or {}).get('cache')}
    return out


def main() -> int:
    write = '--write' in sys.argv
    try:
        with urllib.request.urlopen(BASE + '/health', timeout=5):
            pass
    except Exception as e:                                            # noqa: BLE001
        print(f'capture_v1_golden: no daemon on 8901 ({e}). The goldens describe a '
              f'RUNNING service; a file written without one would be a description '
              f'of nothing.', file=sys.stderr)
        return 2
    try:
        import rdkit                                                 # noqa: F401
    except ImportError:
        print('capture_v1_golden: RDKit is required to build the fixtures; run with '
              'backend/env/bin/python', file=sys.stderr)
        return 2

    current = run_cases()
    # The probe's molfile carries a fresh nonce each run, so its molfile_sha256 is
    # not comparable across runs and is dropped from the golden; what it exists to
    # prove is the computed→db transition and the value parity across it.
    probe = parity_probe()
    for rec in probe.values():
        rec['body'].pop('molfile_sha256', None)
    current.update(probe)
    if write:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(current, indent=2, sort_keys=True) + '\n')
        print(f'wrote {GOLDEN.relative_to(ROOT)} · {len(current)} case(s)')
        return 0

    if not GOLDEN.exists():
        print('capture_v1_golden: no golden file. Run with --write first.',
              file=sys.stderr)
        return 2
    expected = json.loads(GOLDEN.read_text())
    drift = []
    # `cache` is recorded for information and EXCLUDED from the diff: whether the
    # first request of a pair computes or hits depends on whether the fixture is
    # already in the cache, which is a property of the machine's history and not of
    # the API. Comparing it made the file report a breaking change on its own second
    # run — my golden, not the service.
    COMPARE_SKIP = {'cache'}
    for name in sorted(set(expected) | set(current)):
        e, c = expected.get(name), current.get(name)
        if e is None:
            drift.append(f'{name}: NEW case not in the golden file')
        elif c is None:
            drift.append(f'{name}: case MISSING from this run')
        else:
            for key in sorted((set(e) | set(c)) - COMPARE_SKIP):
                if e.get(key) != c.get(key):
                    drift.append(f'{name}.{key}: golden {e.get(key)!r} → now '
                                 f'{c.get(key)!r}')

    # And the parity claim needs a WITNESS, or excluding `cache` would turn this file
    # into one that cannot fail on the thing it was written for: at least one pair
    # must actually demonstrate computed → db, and their non-volatile values must be
    # identical. Without this, a cache that never hits would pass silently — which is
    # exactly what happened for one commit when a view stopped carrying a column.
    witnessed = False
    for name, rec in current.items():
        if not name.endswith('__second_request'):
            continue
        first = current.get(name[: -len('__second_request')])
        if not first:
            continue
        if first.get('cache') == 'computed' and rec.get('cache') == 'db':
            witnessed = True
            if first['body'].get('meta_values') != rec['body'].get('meta_values'):
                drift.append(f'{name}: a cache hit returned DIFFERENT values from the '
                             f'compute that produced it')
    if not witnessed:
        drift.append('no computed→db pair in this run: the cache-parity claim is '
                     'UNVERIFIED. Either every fixture was already warm (re-run '
                     'against a cold cache) or the cache is not being read at all — '
                     'the second is what a dead read path looks like from here.')
    if drift:
        print(f'V1 DRIFT — {len(drift)} difference(s):', file=sys.stderr)
        for d in drift[:20]:
            print(f'  {d}', file=sys.stderr)
        print('\nv1 is a compatibility surface. If this change is intended, it is a '
              'BREAKING change to every existing client and the golden file must be '
              'updated deliberately, in the same commit, with the reason.',
              file=sys.stderr)
        return 1
    print(f'{len(current)} v1 case(s) unchanged')
    return 0


if __name__ == '__main__':
    sys.exit(main())
