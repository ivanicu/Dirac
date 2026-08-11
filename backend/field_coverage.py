#!/usr/bin/env python3
"""Brute-force the field system: every field kind against a population chosen
to BREAK it, over HTTP against the running daemon.

    backend/env/bin/python backend/field_coverage.py            # the whole matrix
    backend/env/bin/python backend/field_coverage.py --set edge # boundaries only
    backend/env/bin/python backend/field_coverage.py --budget 45

Why over HTTP and not by import: the daemon is the thing that broke. A click
travels molfile -> HTTP -> prepare_mol -> field_* -> cube -> JSON, and three of
the four defects this harness was written for lived in that pipe rather than in
the physics. Importing the functions would have tested none of them.

WHAT A ROW MEANS. Every outcome is one of four, and the distinction is the
whole point of the file:

  OK        a cube came back
  REFUSED   the backend declined and SAID WHY, in a sentence a chemist can act
            on. This is a PASS. A molecule with an iron in it SHOULD be refused
            a Gasteiger field; the failure mode this harness exists to catch is
            refusing it for the wrong reason, or not at all.
  BROKEN    it failed in a way nobody chose: an internal error, a hang, a
            traceback, or a refusal whose message does not name a cause.
  HARNESS   the harness itself failed (daemon down, socket error, bad request).
            Never folded into BROKEN -- a broken instrument reporting chemistry
            results is how a coverage sweep launders its own bugs into findings.

The expectations below are written from CHEMISTRY, not from a previous run.
`expect` is what the molecule deserves; a row that disagrees with its
expectation is printed whichever direction it disagrees in, because an
UNEXPECTED PASS is as informative as an unexpected failure -- it usually means
a gate stopped firing.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BACKEND = 'http://127.0.0.1:8901'
KINDS = ['mep', 'mlp', 'mep_qm', 'homo', 'lumo', 'density']

# ── the population ──────────────────────────────────────────────────────────
#
# `expect` is per-kind where the kinds genuinely differ, otherwise a single
# verdict for all six. 'ok' = must produce a field. 'refuse' = must decline
# with a reason. 'either' = both outcomes are defensible and only a BROKEN
# result is a defect.

Molecules = [
    # ── ordinary drug-like matter: the floor. Anything here that is not OK is
    #    a regression, full stop.
    ('aspirin',        'CC(=O)Oc1ccccc1C(=O)O',                 'core',  {}),
    ('caffeine',       'Cn1cnc2c1c(=O)n(C)c(=O)n2C',            'core',  {}),
    ('paracetamol',    'CC(=O)Nc1ccc(O)cc1',                    'core',  {}),
    ('ibuprofen',      'CC(C)Cc1ccc(cc1)C(C)C(=O)O',            'core',  {}),
    ('benzene',        'c1ccccc1',                              'core',  {}),

    # ── degenerate geometry: a bounding box that is flat or a line. The grid
    #    builder takes min/max per axis, so a diatomic has ZERO extent on two
    #    of them and a planar ring has zero on one.
    ('N2-diatomic',    'N#N',                                   'edge',  {}),
    ('single-atom-Ne', '[Ne]',                                  'edge',
     {'mep': 'refuse', 'mlp': 'either'}),          # no charges, no bonds
    ('HCN-collinear',  'C#N',                                   'edge',  {}),

    # ── charge: the sign conventions and the net-charge bookkeeping
    ('acetate-anion',  'CC(=O)[O-]',                            'edge',  {}),
    ('choline-cation', 'C[N+](C)(C)CCO',                        'edge',  {}),
    ('glycine-zwitter','C(C(=O)[O-])[NH3+]',                    'edge',  {}),

    # ── odd electron count: forces the UHF branch, which the RHF path never
    #    exercises. A radical must NOT be silently closed-shell.
    ('TEMPO-radical',  'CC1(C)CCCC(C)(C)N1[O]',                 'edge',
     {'mep': 'either'}),                            # Gasteiger on N-oxyl

    # ── the open-shell gate, as a MATCHED PAIR. These two molecules are
    #    identical apart from the metal at the centre, so the gate has nowhere
    #    to hide: Fe(II) is d6 and its ground state is not the singlet that
    #    nelec%2 silently assumes, Zn(II) is d10 and genuinely is one.
    #    Fe must REFUSE and Zn must RUN; a gate that fails either direction is
    #    caught by the other. Zn is the over-refusal control and it matters --
    #    zinc proteases are a large slice of real drug discovery, and a rule
    #    written one period too wide would quietly lock them all out.
    # CONNECTED complexes, and the connectivity is the point. The first version
    # of this pair used "CC(=O)[O-].CC(=O)[O-].[Fe+2]", where salt stripping
    # discarded the iron and ran one acetate: nao=23, converged, green, and
    # testing nothing. A disconnected metal never reaches the gate the pair
    # exists to exercise.
    ('FeCl3-openshell', 'Cl[Fe](Cl)Cl',                         'edge',
     {'mep': 'either', 'mlp': 'either', 'mep_qm': 'refuse', 'homo': 'refuse',
      'lumo': 'refuse', 'density': 'refuse'}),
    ('ZnCl2-closedshell', 'Cl[Zn]Cl',                           'edge',
     {'mep': 'either', 'mlp': 'either', 'mep_qm': 'ok', 'homo': 'ok',
      'lumo': 'ok', 'density': 'ok'}),
    # And the stripping rule itself, both directions: a d-block metal in its
    # own fragment must be refused rather than silently discarded, while an
    # ordinary sodium counter-ion must still be stripped without complaint.
    ('Fe-disconnected', 'CC(=O)[O-].CC(=O)[O-].[Fe+2]',         'edge',
     {k: 'refuse' for k in KINDS}),
    ('Na-carboxylate', 'CC(=O)[O-].[Na+]',                      'edge', {}),
    ('Fe-simple',      '[Fe]',                                  'edge',
     {'mep': 'refuse', 'mlp': 'either', 'mep_qm': 'refuse', 'homo': 'refuse',
      'lumo': 'refuse', 'density': 'refuse'}),

    # Named for what it IS. The first version of this row was called
    # "Fe-porphyrin" and contained no iron -- free-base porphine -- so the
    # sweep reported six green cells for the exact molecule class that started
    # this, having never tested it. It stays in the population on its own
    # merits (large conjugated macrocycle, four nitrogens, the slowest
    # non-refused QM in the set) under an honest name.
    ('porphine-freebase', 'c1cc2cc3ccc(cc4ccc(cc5ccc(cc1n2)[nH]5)n4)[nH]3',
     'edge', {'mep': 'either', 'mlp': 'either'}),

    # ── parameterisation holes: each must refuse by NAME, never zero-fill
    ('PF6-hypervalent','F[P-](F)(F)(F)(F)F',                    'edge',
     {'mep': 'refuse'}),
    ('boronic-acid',   'OB(O)c1ccccc1',                         'edge',  {}),
    ('selenomethionine','C[Se]CCC(C(=O)O)N',                    'edge',  {}),

    # ── heavy halogen: the ECP path. Without an ECP this is wrong with no
    #    error, which is why it is in the sweep at all.
    ('iodobenzene',    'Ic1ccccc1',                             'edge',  {}),
    ('bromobenzene',   'Brc1ccccc1',                            'edge',  {}),

    # ── size: must hit the budget or the atom cap and say which
    ('cyclosporine-ish','CC(C)CC1NC(=O)C(C)NC(=O)C(CC(C)C)NC(=O)C(C)NC(=O)'
                        'C(CC(C)C)NC(=O)C(C)NC(=O)C(CC(C)C)NC1=O',      'edge',
     {'mep': 'ok', 'mlp': 'ok', 'mep_qm': 'either', 'homo': 'either',
      'lumo': 'either', 'density': 'either'}),
]

MALFORMED = [
    ('empty-molfile',   ''),
    ('garbage',         'this is not a molfile\nat all\n'),
    ('truncated-counts','\n\n\n  9  8'),
]


def post(path: str, payload: dict, timeout: float):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(f'{BACKEND}{path}', data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def embed(smiles: str, timeout: float) -> tuple[str | None, str, int]:
    """molfile, the refusal reason if any, and how many fragments were dropped.

    `fragments_stripped` is returned and asserted on because ignoring it is how
    a control gets silently defanged: the Fe/Zn pair spent a whole run testing
    acetate against acetate, green both times, while the metal each row existed
    to exercise had been stripped before the calculation started."""
    out = post('/embed', {'smiles': smiles}, timeout)
    if not out.get('ok'):
        return None, (out.get('error') or 'embed refused'), 0
    return out['molfile'], '', int((out.get('meta') or {}).get('fragments_stripped', 0))


def classify(outcome: dict) -> tuple[str, str]:
    """OK / REFUSED / BROKEN, plus the detail line."""
    if outcome.get('ok'):
        return 'OK', outcome.get('detail', '')
    err = (outcome.get('error') or '').strip()
    reason = outcome.get('reason', '')
    if not err:
        return 'BROKEN', 'declined with an empty message'
    # A refusal earns its PASS only by naming a cause. "failed", "error",
    # "None" and a bare traceback line are not causes.
    if reason == 'internal' or err.lower() in {'none', 'error', 'failed'}:
        return 'BROKEN', err
    if len(err) < 15:
        return 'BROKEN', f'message too thin to act on: {err!r}'
    return 'REFUSED', err


def run_case(name: str, molfile: str, kind: str, budget: float,
             timeout: float) -> dict:
    t0 = time.time()
    try:
        out = post('/field', {'molfile': molfile, 'kind': kind,
                              'basis': 'sto-3g', 'max_seconds': budget},
                   timeout=timeout)
    except urllib.error.URLError as e:
        return {'status': 'HARNESS', 'detail': f'daemon unreachable: {e}',
                'seconds': time.time() - t0}
    except TimeoutError:
        # The budget is enforced INSIDE the SCF loop, so the socket timing out
        # first means the bound did not hold -- that is a defect in the daemon,
        # not in this harness.
        return {'status': 'BROKEN',
                'detail': f'no response in {timeout:.0f}s despite a '
                          f'{budget:.0f}s budget -- the deadline did not fire',
                'seconds': time.time() - t0}
    except Exception as e:                                   # noqa: BLE001
        return {'status': 'HARNESS', 'detail': f'{type(e).__name__}: {e}',
                'seconds': time.time() - t0}

    seconds = time.time() - t0
    if out.get('ok'):
        cube = out.get('cube') or ''
        meta = out.get('meta') or {}
        # A cube that parses is not yet a field: an all-zero grid renders as a
        # perfectly normal empty picture, which is the failure that shipped once.
        if len(cube) < 200:
            return {'status': 'BROKEN', 'detail': 'cube too short to be a grid',
                    'seconds': seconds}
        detail = f"{meta.get('method', 'classical')}"
        if meta.get('nbasis'):
            detail += f" nao={meta['nbasis']}"
        if meta.get('scf_cycles'):
            detail += f" cyc={meta['scf_cycles']}"
        return {'status': 'OK', 'detail': detail, 'seconds': seconds,
                'meta': meta}
    status, detail = classify(out)
    return {'status': status, 'detail': detail, 'seconds': seconds,
            'reason': out.get('reason', '')}



def additivity_control(budget: float, timeout: float) -> tuple[bool, str]:
    """The positive control for /field/region: a classical field must be
    EXACTLY additive over its source set, on a shared frame.

    This is the gate that catches grid registration. If V(A∪B) ever differs
    from V(A)+V(B) by more than the serialisation floor, the three runs did not
    land on the same grid and every difference field built on this route is
    meaningless — which is the whole reason the route exists.

    THE FLOOR IS THE POINT. Gaussian cube values are written `%13.5e`, six
    significant figures, so the round-trip through the file cannot resolve
    better than ~1e-6 relative. In-process the same comparison is 5.2e-16.
    A first version of this check used 1e-9 and "failed" at 2.6e-6 — comparing
    a difference to a threshold below the noise floor of the instrument it was
    measured through, which manufactures a defect out of arithmetic.
    """
    import numpy as np

    def grid(cube: str):
        lines = cube.split('\n')
        nat = abs(int(lines[2].split()[0]))
        d = [int(lines[3 + i].split()[0]) for i in range(3)]
        return np.array(' '.join(lines[6 + nat:]).split(), dtype=float).reshape(d)

    A = [{'element': 'O', 'x': 0, 'y': 0, 'z': 0, 'charge': -0.6},
         {'element': 'H', 'x': 0.96, 'y': 0, 'z': 0, 'charge': 0.3},
         {'element': 'H', 'x': -0.3, 'y': 0.9, 'z': 0, 'charge': 0.3}]
    B = [{'element': 'N', 'x': 8, 'y': 0, 'z': 0, 'charge': -0.9},
         {'element': 'H', 'x': 9, 'y': 0, 'z': 0, 'charge': 0.45},
         {'element': 'H', 'x': 7.6, 'y': 0.9, 'z': 0, 'charge': 0.45}]
    frame = {'lo': [-6, -6, -6], 'hi': [14, 6, 6], 'spacing': 0.5}
    try:
        gA = grid(post('/field/region', {'sources': A, 'frame': frame, 'kind': 'mep'}, timeout)['cube'])
        gB = grid(post('/field/region', {'sources': B, 'frame': frame, 'kind': 'mep'}, timeout)['cube'])
        gAB = grid(post('/field/region', {'sources': A + B, 'frame': frame, 'kind': 'mep'}, timeout)['cube'])
    except Exception as e:                                   # noqa: BLE001
        return False, f'HARNESS: {e}'
    rel = float(np.abs(gAB - (gA + gB)).max() / np.abs(gAB).max())
    CUBE_ASCII_FLOOR = 5e-6      # %13.5e -> 6 significant figures
    ok = rel <= CUBE_ASCII_FLOOR
    return ok, (f'relative deviation {rel:.2e} against a {CUBE_ASCII_FLOOR:.0e} '
                f'cube-ASCII floor — '
                + ('grids register exactly' if ok
                   else 'GRIDS DISAGREE, difference fields are meaningless'))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--set', choices=['all', 'core', 'edge'], default='all')
    ap.add_argument('--budget', type=float, default=45.0,
                    help='per-request SCF budget in seconds')
    ap.add_argument('--kinds', default=','.join(KINDS))
    args = ap.parse_args()

    kinds = [k.strip() for k in args.kinds.split(',') if k.strip()]
    # The socket must outlast the budget, or a timeout here would be
    # indistinguishable from the daemon's own deadline failing to fire.
    timeout = args.budget * 3 + 30

    try:
        with urllib.request.urlopen(f'{BACKEND}/health', timeout=5) as r:
            health = json.loads(r.read())
        print(f"daemon: rdkit {health['rdkit']} · pyscf {health['pyscf']} · "
              f"db cache {health['db_cache']}\n")
    except Exception as e:                                   # noqa: BLE001
        print(f'HARNESS FAULT: no daemon on {BACKEND} ({e})')
        print('start it: backend/env/bin/python backend/field_server.py')
        return 2

    population = [m for m in Molecules
                  if args.set == 'all' or m[2] == args.set]

    tally: dict[str, int] = {}
    surprises: list[str] = []
    slowest: list[tuple[float, str]] = []

    for name, smiles, _group, expect in population:
        try:
            molfile, embed_error, stripped = embed(smiles, timeout=60)
        except Exception as e:                               # noqa: BLE001
            print(f'{name:20s} HARNESS embed failed: {e}')
            tally['HARNESS'] = tally.get('HARNESS', 0) + 1
            continue
        if not molfile:
            # Refusing to embed is a legitimate answer for some of these, and
            # the reason is printed rather than assumed.
            print(f'{name:20s} REFUSED at embed — {embed_error[:90]}')
            tally['REFUSED'] = tally.get('REFUSED', 0) + len(kinds)
            for kind in kinds:
                want = expect.get(kind, 'ok' if _group == 'core' else 'either')
                if want == 'ok':
                    surprises.append(
                        f'{name}/{kind}: expected ok, refused at embed — {embed_error[:120]}')
            continue
        if stripped:
            # Not fatal — it is the documented salt rule — but it changes WHAT
            # was tested, so it is on the record instead of invisible.
            print(f'{name:20s} note: {stripped} fragment(s) stripped before embedding')

        line = f'{name:20s}'
        for kind in kinds:
            r = run_case(name, molfile, kind, args.budget, timeout)
            st = r['status']
            tally[st] = tally.get(st, 0) + 1
            slowest.append((r['seconds'], f'{name}/{kind}'))
            mark = {'OK': '·', 'REFUSED': '○', 'BROKEN': '✗',
                    'HARNESS': '!'}[st]
            line += f' {kind}{mark}'

            want = expect.get(kind, 'ok' if _group == 'core' else 'either')
            got = 'ok' if st == 'OK' else ('refuse' if st == 'REFUSED' else st)
            if want != 'either' and want != got:
                surprises.append(
                    f'{name}/{kind}: expected {want}, got {got} — {r["detail"][:150]}')
            if st in ('BROKEN', 'HARNESS'):
                surprises.append(f'{name}/{kind}: {st} — {r["detail"][:150]}')
        print(line)

    # ── the positive control on this harness's own BROKEN verdict ───────────
    #
    # Without this the sweep can only ever report BROKEN 0, and a zero from an
    # instrument that has never returned non-zero is silence mistaken for an
    # acquittal. Malformed input MUST come back REFUSED with a readable reason:
    # if it comes back OK the daemon invented a field out of garbage, and if it
    # comes back BROKEN the daemon failed without saying why. Either way this
    # block is what proves the three verdicts are distinguishable at all.
    ok, detail = additivity_control(args.budget, timeout)
    print(f'\nregion additivity (positive control): {"PASS" if ok else "FAIL"} — {detail}')
    if not ok:
        surprises.append(f'region additivity: {detail}')

    print('\nmalformed input (positive control — each must be REFUSED, not OK):')
    control_ok = True
    for name, junk in MALFORMED:
        r = run_case(name, junk, 'mep', args.budget, timeout)
        tally[r['status']] = tally.get(r['status'], 0) + 1
        print(f'  {name:20s} {r["status"]:8s} {r["detail"][:80]}')
        if r['status'] != 'REFUSED':
            control_ok = False
            surprises.append(
                f'{name}: malformed input returned {r["status"]} — '
                f'{r["detail"][:120]}')
    if control_ok:
        print('  → the BROKEN/REFUSED/OK verdicts are demonstrably distinguishable')

    print()
    total = sum(tally.values())
    order = ['OK', 'REFUSED', 'BROKEN', 'HARNESS']
    print('  '.join(f'{k} {tally.get(k, 0)}' for k in order) + f'   (of {total})')

    slowest.sort(reverse=True)
    print('\nslowest five:')
    for sec, label in slowest[:5]:
        print(f'  {sec:7.1f}s  {label}')

    if surprises:
        print(f'\n{len(surprises)} rows disagree with chemistry or broke:')
        for s in surprises:
            print(f'  ✗ {s}')
    else:
        print('\nevery row matched its chemical expectation')

    # HARNESS faults are not a chemistry verdict. A sweep whose own instrument
    # failed reports 2, so a green 0 can never be produced by a broken harness.
    if tally.get('HARNESS'):
        return 2
    return 1 if (tally.get('BROKEN') or surprises) else 0


if __name__ == '__main__':
    raise SystemExit(main())
