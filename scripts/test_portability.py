#!/usr/bin/env python3
"""How much of the test suite can run WITHOUT the science stack — as a number.

WHY THIS IS A GATE AND NOT A NOTE. An external audit's central claim is that the
scientific semantics are scattered across the HTTP handler, the method registry,
the job ledger and the envelope, and must converge into one transport-neutral
kernel. That claim is usually argued with architecture diagrams. This measures it:

    a suite that cannot be imported without RDKit is a suite whose subject is
    coupled to the science stack, whether or not its subject is science.

`test_admin_queries.py` tests SQL text. `test_job_reap.py` tests a state machine.
`test_envelope.py` tests a codec. None of those is chemistry, and today only ONE
of them imports on a clean interpreter — because they all reach the code they test
through `field_server`, which imports RDKit at module scope. That single fact is
the audit's thesis stated as a cost: it is why CI cannot run them, and it is what
the kernel extraction has to change.

So this script prints a RATCHET number: import-light suites out of total. It fails
when the number goes DOWN. Every step of the consolidation should move it up, and
if a refactor claims to decouple something while this number stays flat, the claim
is decoration.

Usage:  python3 scripts/test_portability.py [--baseline N] [--json]
Exit:   0 at or above baseline · 1 below it · 2 could not run
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import os

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / 'backend' / 'tests'
BASELINE_FILE = ROOT / 'scripts' / '.test_portability_baseline'

# A suite is IMPORT-LIGHT if a bare interpreter can import its module graph. The
# probe deliberately imports rather than runs: running needs a database and a
# daemon, which is a different axis. What is being measured here is COUPLING.
PROBE = (
    'import importlib.util, sys, pathlib\n'
    'sys.path.insert(0, {backend!r})\n'
    'spec = importlib.util.spec_from_file_location("probe", {path!r})\n'
    'mod = importlib.util.module_from_spec(spec)\n'
    'try:\n'
    '    spec.loader.exec_module(mod)\n'
    'except SystemExit:\n'
    '    pass\n'                       # a suite that runs and exits is fine
)


def probe(path: pathlib.Path) -> tuple[bool, str]:
    """Can a clean interpreter import this suite? Returns (ok, first blocker)."""
    code = PROBE.format(backend=str(ROOT / 'backend'), path=str(path))
    # Several historical suites execute their tests at import time. Tell any suite
    # modernised for this probe not to turn an import-coupling measurement into a live
    # daemon integration test (whose result otherwise changes with sandbox/network state).
    env = dict(os.environ, DIRAC_IMPORT_PROBE='1')
    r = subprocess.run([sys.executable, '-c', code], capture_output=True,
                       text=True, timeout=300, env=env)
    if r.returncode == 0:
        return True, ''
    err = (r.stderr or '').strip().splitlines()
    blocker = ''
    for line in reversed(err):
        if 'ModuleNotFoundError' in line or 'ImportError' in line:
            blocker = line.strip()
            break
    return False, blocker or (err[-1].strip() if err else f'exit {r.returncode}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--baseline', type=int, default=None)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    suites = sorted(TESTS.glob('test_*.py'))
    if not suites:
        print('test_portability: no suites found — refusing to report a ratio',
              file=sys.stderr)
        return 2

    rows = []
    for s in suites:
        ok, blocker = probe(s)
        rows.append({'suite': s.name, 'import_light': ok, 'blocker': blocker})

    light = sum(1 for r in rows if r['import_light'])
    baseline = args.baseline
    if baseline is None:
        baseline = int(BASELINE_FILE.read_text().split()[0]) if BASELINE_FILE.exists() else 0

    if args.json:
        print(json.dumps({'import_light': light, 'total': len(rows),
                          'baseline': baseline, 'suites': rows}, indent=2))
    else:
        for r in rows:
            mark = 'LIGHT ' if r['import_light'] else 'COUPLED'
            print(f'  {mark}  {r["suite"]:<46}'
                  + ('' if r['import_light'] else f'  ← {r["blocker"][:60]}'))
        print('─' * 78)
        print(f'{light} of {len(rows)} suites import without the science stack '
              f'(baseline {baseline})')

    if light < baseline:
        print(f'\nPORTABILITY REGRESSED: {light} < {baseline}. A suite that used to '
              f'import on a clean interpreter now needs RDKit — the coupling the '
              f'kernel extraction is removing has grown instead.', file=sys.stderr)
        return 1
    if light > baseline:
        print(f'\nPortability IMPROVED: {light} > {baseline}. Raise the baseline in '
              f'{BASELINE_FILE.name} so it cannot slip back.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
