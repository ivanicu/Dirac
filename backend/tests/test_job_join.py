#!/usr/bin/env python3
"""Two identical requests must produce ONE computation.

`job_one_inflight` has existed since migration 007 and for one day the conflict it
raises was only COUNTED while both requests computed anyway. That was the honest
intermediate state — the counter is what proved the duplicated work was real
(`inflight_conflict` fired in production) rather than a story about concurrency.
`JobLedger.wait_for` acts on it, which is the ledger becoming a coordinator.

MEASURED before these tests were written, two identical HOMO requests 0.4 s apart
on a molecule never seen before:
    A  2.85 s  cache=computed
    B  2.62 s  cache=db        (waited 2.563 s, then read the winner's row)
    counters: opened 1 · inflight_conflict 1 · joined 1 · join_timeout 0
One SCF instead of two. What follows is the part that keeps it true.

WHAT EACH TEST DEFENDS, because "it worked once" is not a property:
  · a waiter must return the winner's OUTCOME, not merely stop waiting;
  · a waiter must TIME OUT and let the caller compute — a waiter that gives up
    must degrade to doing the work, never to an error the molecule did not cause;
  · a waiter must not hang when the row it is waiting on VANISHES (rolled back,
    or reaped);
  · a DETERMINISTIC refusal must be served rather than reproduced, and a
    RETRYABLE one must NOT be — that distinction is the difference between saving
    CPU and inventing a permanent failure out of a transient one.

Run: backend/env/bin/python backend/tests/test_job_join.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import field_server as fs                                            # noqa: E402
import jobs                                                          # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f'PASS    {name}')
    except AssertionError as e:
        FAIL.append(name)
        print(f'FAIL    {name}\n        {e}')


def ledger() -> jobs.JobLedger:
    if not fs._db_ok:
        fs.db_init()
    assert fs._db_ok, ('no database — every test here would pass by doing '
                       'nothing, which is UNVERIFIED, not clean')
    return jobs.JobLedger(fs._db, jobs.make_worker_name(os.getpid(), 'jointest'))


def a_method_row() -> str:
    return next(iter(fs._method_ids.values()))


def fresh_input(tag: str) -> bytes:
    return hashlib.sha256(f'{tag}|{time.time()}|{os.getpid()}'.encode()).digest()


def test_the_index_actually_refuses_a_second_inflight_row():
    """The premise. Everything else is decoration if this does not hold.

    A positive control on the CONSTRAINT rather than on my code: if
    job_one_inflight ever stops covering (method, input, params), the join logic
    silently never triggers and both requests compute again — with every counter
    reading zero, which looks like peace.
    """
    L = ledger()
    sha = fresh_input('premise')
    params = {'kind': 'mep', 'basis': 'none'}
    first, conflict1 = L.open(method_row_id=a_method_row(), input_sha256=sha,
                              params=params)
    assert first and not conflict1, f'the first insert failed: {first} {conflict1}'
    second, conflict2 = L.open(method_row_id=a_method_row(), input_sha256=sha,
                               params=params)
    assert second is None and conflict2, (
        f'a second identical in-flight row was ACCEPTED (id={second}) — '
        f'job_one_inflight is not covering (method, input, params), so nothing '
        f'would ever join and both requests would compute')
    L.failed(first, code='CANCELLED', detail='TEST premise row')


def test_a_waiter_returns_the_winners_outcome():
    L = ledger()
    sha = fresh_input('outcome')
    params = {'kind': 'mep', 'basis': 'none'}
    job, _ = L.open(method_row_id=a_method_row(), input_sha256=sha, params=params)
    assert job

    # the winner finishes while the waiter is waiting
    def finish():
        time.sleep(0.6)
        L.done(job, seconds=0.42)
    threading.Thread(target=finish, daemon=True).start()

    t0 = time.time()
    out = L.wait_for(method_row_id=a_method_row(), input_sha256=sha,
                     params=params, timeout=10.0)
    waited = time.time() - t0
    assert out['state'] == 'done', f'the waiter did not see the completion: {out}'
    assert 0.4 < waited < 4.0, (
        f'waited {waited:.2f}s for a job that finished at 0.6s — either the poll '
        f'interval is wrong or it is not polling at all')


def test_a_waiter_times_out_instead_of_hanging():
    """And the caller then computes. This is the path that must never raise."""
    L = ledger()
    sha = fresh_input('timeout')
    params = {'kind': 'mep', 'basis': 'none'}
    job, _ = L.open(method_row_id=a_method_row(), input_sha256=sha, params=params)
    t0 = time.time()
    out = L.wait_for(method_row_id=a_method_row(), input_sha256=sha,
                     params=params, timeout=1.0)
    waited = time.time() - t0
    assert out['state'] == 'timeout', (
        f"a job still running reported {out['state']!r} — a waiter that believes "
        f'an unfinished job is finished serves an empty cache as a result')
    assert waited < 4.0, f'the timeout took {waited:.2f}s to fire on a 1.0s budget'
    L.failed(job, code='CANCELLED', detail='TEST timeout row')


def test_a_waiter_does_not_hang_when_the_row_vanishes():
    """A conflict can be with a row that is then rolled back or reaped. Waiting
    the full budget for something that no longer exists is a request that hangs
    for two minutes and then computes — the worst of both."""
    L = ledger()
    sha = fresh_input('vanish')
    params = {'kind': 'mep', 'basis': 'none'}
    t0 = time.time()
    out = L.wait_for(method_row_id=a_method_row(), input_sha256=sha,
                     params=params, timeout=8.0)
    waited = time.time() - t0
    assert waited < 2.0, (
        f'waited {waited:.2f}s for a job that never existed — the waiter polls '
        f'until its deadline instead of noticing there is nothing to wait for')
    assert out['state'] == 'timeout', f'unexpected verdict for a missing row: {out}'


def test_the_non_retryable_set_excludes_the_codes_that_can_differ():
    """The judgement call, asserted so it cannot drift into "serve every failure".

    Serving a BUDGET or UNCONVERGED refusal to a waiter would turn a transient
    failure into a permanent one for everyone who asked at the same moment — and
    those two are exactly the codes a second attempt can change (a bigger budget,
    a different starting guess). PARSE / UNSUPPORTED / UNPARAMETERIZED / TOO_LARGE
    cannot come out differently for the same input.
    """
    must = {'PARSE', 'UNSUPPORTED', 'UNPARAMETERIZED', 'TOO_LARGE'}
    must_not = {'BUDGET', 'UNCONVERGED', 'INTERNAL', 'CANCELLED'}
    assert must <= fs.NON_RETRYABLE_JOB_ERRORS, (
        f'missing deterministic codes: {sorted(must - fs.NON_RETRYABLE_JOB_ERRORS)}')
    assert not (must_not & fs.NON_RETRYABLE_JOB_ERRORS), (
        f'{sorted(must_not & fs.NON_RETRYABLE_JOB_ERRORS)} would be served to a '
        f'waiter as final, making a retryable failure permanent for every '
        f'concurrent caller')


def test_two_identical_requests_produce_one_computation():
    """End to end over HTTP, on a molecule the cache has never seen.

    Skipped rather than faked when no daemon is listening: this is the only test
    here that exercises the whole path, and pretending otherwise would leave the
    headline claim unverified.
    """
    import urllib.error
    import urllib.request
    from rdkit import Chem
    from rdkit.Chem import AllChem

    try:
        with urllib.request.urlopen('http://127.0.0.1:8901/health', timeout=4) as fh:
            before = json.load(fh)['jobs']
    except Exception as e:                                            # noqa: BLE001
        print(f'        SKIP (no daemon on 8901: {e}) — the headline claim is '
              f'UNVERIFIED by this run, not confirmed')
        return

    mol = Chem.AddHs(Chem.MolFromSmiles('c1ccc(cc1)C#N'))
    AllChem.EmbedMolecule(mol, randomSeed=int.from_bytes(os.urandom(2), 'big'))
    body = json.dumps({'molfile': Chem.MolToMolBlock(mol), 'kind': 'homo',
                       'basis': 'sto-3g', 'max_seconds': 120}).encode()
    results: dict[str, tuple] = {}

    def fire(tag: str) -> None:
        t = time.time()
        req = urllib.request.Request('http://127.0.0.1:8901/field', data=body,
                                     headers={'Content-Type': 'application/json'})
        d = json.load(urllib.request.urlopen(req, timeout=400))
        results[tag] = (round(time.time() - t, 2), d.get('ok'),
                        (d.get('meta') or {}).get('cache'))

    a = threading.Thread(target=fire, args=('A',))
    b = threading.Thread(target=fire, args=('B',))
    a.start()
    time.sleep(0.4)          # B lands while A is unmistakably still computing
    b.start()
    a.join(); b.join()

    with urllib.request.urlopen('http://127.0.0.1:8901/health', timeout=4) as fh:
        after = json.load(fh)['jobs']

    assert all(v[1] for v in results.values()), f'a request failed: {results}'
    opened = after['opened'] - before['opened']
    joined = after['joined'] - before['joined']
    assert opened == 1, (
        f'{opened} job rows opened for two identical requests — both computed, '
        f'which is the behaviour wait_for exists to remove ({results})')
    assert joined >= 1, f'nothing joined: {results} / {after}'
    caches = {v[2] for v in results.values()}
    assert 'db' in caches, (
        f'no request was served from the winner\'s cache: {results} — the join '
        f'happened but the result did not travel, which is a wait for nothing')


for name, fn in list(globals().items()):
    if name.startswith('test_') and callable(fn):
        check(name, fn)

print('─' * 100)
print(f'{len(PASS)} passed · {len(FAIL)} failed')
sys.exit(1 if FAIL else 0)
