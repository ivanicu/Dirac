#!/usr/bin/env python3
"""At most MAX_CONCURRENT_QM quantum computations may run at once.

Dedup (test_job_join.py) removed duplicate work. It did not remove LOAD: five
DISTINCT SCFs still all started, each planning around pyscf's 4000 MB default
max_memory and each taking as many BLAS threads as it liked on a 24-thread box —
so the fifth request did not merely wait, it made the first four slower and pushed
RSS toward the ceiling that killed this daemon once already.

WHERE THE PROPERTY IS MEASURED, and this is the part that took two wrong readings:
  · attempt 1 counted overlapping [started_at, finished_at] intervals in the job
    ledger with a SELF-JOIN. It reported 4 for a bound of 2, because a row overlaps
    itself and the window swept up stale rows.
  · attempt 2 fixed the sweep and still read 3 — because the window included
    CLASSICAL rows, which are deliberately ungated at ~0.1 s. The check was right;
    its POPULATION was not.
  · attempt 3 restricted to quantum rows and STILL read 3 — because a ledger
    interval includes work done after the slot is released, so three intervals
    genuinely overlap while only two SCFs ever ran. The ledger is a proxy with a
    known upward bias.
So the property is counted AT THE SEMAPHORE (`qm_waiting.running_peak`), which is
the thing itself. The ledger stays useful as corroboration, and its bias is
written down rather than discovered again.

MEASURED with this instrument, six distinct quantum requests fired together:
  wall 2.0 s · all ok · per-request 0.56 0.69 1.18 1.42 1.90 1.98 (three waves)
  qm: peak 6 at the gate · running_peak 2 · refused 0

Run: backend/env/bin/python backend/tests/test_concurrency_bound.py
     (needs the daemon on :8901 — skipped, loudly, otherwise)
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

PASS, FAIL, SKIP = [], [], []
HEALTH = 'http://127.0.0.1:8901/health'
FIELD = 'http://127.0.0.1:8901/field'


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f'PASS    {name}')
    except AssertionError as e:
        FAIL.append(name)
        print(f'FAIL    {name}\n        {e}')
    except _Skip as e:
        SKIP.append(name)
        print(f'SKIP    {name} — {e}')


class _Skip(Exception):
    pass


def health() -> dict:
    try:
        with urllib.request.urlopen(HEALTH, timeout=5) as fh:
            return json.load(fh)
    except Exception as e:                                            # noqa: BLE001
        raise _Skip(f'no daemon on 8901 ({e}); the bound is UNVERIFIED by this '
                    f'run, not confirmed')


def molfile(smiles: str, seed: int) -> str:
    from rdkit import Chem
    from rdkit.Chem import AllChem
    m = Chem.AddHs(Chem.MolFromSmiles(smiles))
    AllChem.EmbedMolecule(m, randomSeed=seed)
    return Chem.MolToMolBlock(m)


def post(payload: dict, timeout: float = 600) -> dict:
    req = urllib.request.Request(
        FIELD, data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def test_more_requests_than_slots_never_exceeds_the_bound():
    h0 = health()
    slots = h0['qm_slots']
    n = slots * 3
    seed = int.from_bytes(os.urandom(2), 'big')
    smis = ['c1ccccc1O', 'c1ccccc1N', 'c1ccccc1C#N', 'c1ccccc1F',
            'c1ccccc1Cl', 'c1ccccc1C', 'c1ccccc1Br', 'c1ccncc1'][:n]
    bodies = [{'molfile': molfile(s, seed + i), 'kind': 'homo',
               'basis': 'sto-3g', 'max_seconds': 120}
              for i, s in enumerate(smis)]
    out: dict[int, tuple] = {}

    def fire(i: int) -> None:
        t = time.time()
        d = post(bodies[i])
        out[i] = (round(time.time() - t, 2), d.get('ok'), d.get('error'))

    threads = [threading.Thread(target=fire, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    h1 = health()
    qm = h1['qm_waiting']
    assert qm['running_peak'] <= slots, (
        f"running_peak {qm['running_peak']} exceeded the bound of {slots} — the "
        f'semaphore is not holding, and {n} simultaneous SCFs each planning around '
        f'4000 MB is how this daemon died once')
    assert qm['peak'] >= n, (
        f"only {qm['peak']} requests reached the gate out of {n} — the test did "
        f'not actually create contention, so the bound was never exercised')
    assert all(v[1] for v in out.values()), (
        f'a request FAILED while merely waiting: {out}. Bounding load must not '
        f'turn a queued request into an error; that would trade a slow app for a '
        f'broken one')


def test_a_short_budget_is_REFUSED_with_the_depth_rather_than_held():
    """The refusal path, exercised in seconds instead of ninety.

    A caller who says `max_seconds: 1` has declared that it will not wait, so when
    both slots are busy it must be refused AT ONCE and told how deep the queue is —
    "too busy" without a number tells nobody whether to retry in ten seconds or
    ten minutes. Holding the thread instead would make the queue behind it longer
    on behalf of a client that has already given up.
    """
    h0 = health()
    slots = h0['qm_slots']
    seed = int.from_bytes(os.urandom(2), 'big')
    # Occupy every slot with work that takes seconds, not milliseconds.
    hogs = [{'molfile': molfile('c1ccc2ccccc2c1', seed + 100 + i), 'kind': 'mep_qm',
             'basis': '6-31g', 'max_seconds': 120} for i in range(slots)]
    results: list = []

    def hog(i: int) -> None:
        try:
            results.append(post(hogs[i]).get('ok'))
        except Exception as e:                                        # noqa: BLE001
            results.append(f'error {e}')

    threads = [threading.Thread(target=hog, args=(i,), daemon=True)
               for i in range(slots)]
    for t in threads:
        t.start()
    time.sleep(2.5)                     # let them take the slots

    t0 = time.time()
    d = post({'molfile': molfile('c1ccccc1O', seed + 200), 'kind': 'homo',
              'basis': 'sto-3g', 'max_seconds': 1}, timeout=120)
    waited = time.time() - t0
    for t in threads:
        t.join(timeout=600)

    if d.get('ok'):
        raise _Skip(
            f'the slots were free again within {waited:.1f}s, so the refusal path '
            f'was not reached — the hog molecules finished too fast on this box. '
            f'UNVERIFIED, not passed')
    assert d.get('reason') == 'budget', (
        f"a queue refusal came back as reason={d.get('reason')!r}; it must be "
        f"'budget' so the panel can offer 'retry' instead of showing a chemist a "
        f'red chemistry failure for a calculation that was merely queued')
    assert 'ahead of this one' in str(d.get('error')), (
        f"the refusal does not state the queue depth: {d.get('error')!r}")
    assert waited < 30, (
        f'the refusal took {waited:.1f}s for a 1 s budget — the wait is not bounded '
        f'by the caller\'s own tolerance')


def test_classical_fields_are_NOT_gated():
    """Scope, asserted. A 0.1 s MEP behind a queue of SCFs would make the
    interactive path hostage to the expensive one, and a semaphore costs more than
    the work it would guard here."""
    h0 = health()
    seed = int.from_bytes(os.urandom(2), 'big')
    before = h0['qm_waiting']['peak']
    t0 = time.time()
    d = post({'molfile': molfile('c1ccccc1O', seed + 300), 'kind': 'mep'})
    assert d.get('ok'), f'classical mep failed: {d.get("error")}'
    assert time.time() - t0 < 20, 'a classical field took longer than any queue wait'
    after = health()['qm_waiting']['peak']
    assert after == before, (
        f'a classical request passed through the quantum gate (peak {before} → '
        f'{after}) — the bound has been applied to the interactive path')


if __name__ == '__main__' and os.environ.get('DIRAC_IMPORT_PROBE') != '1':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            check(name, fn)

    print('─' * 100)
    print(f'{len(PASS)} passed · {len(FAIL)} failed · {len(SKIP)} skipped')
    sys.exit(1 if FAIL else 0)
