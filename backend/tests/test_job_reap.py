#!/usr/bin/env python3
"""A reap that reaps nothing is worse than no reap: v_job_live then reports work
nobody is doing, and the ledger reads as a hung system.

THE INCIDENT, measured 2026-08-11: a `fields.qm.homo` row sat in state='running'
with age_seconds = 10809 — three hours — while its worker's pid had been gone for
most of it. Cause: `reap()` called `app.reap_orphaned_jobs(self.worker)`, and the
worker name contains the pid, so after a restart the new process's name never
matches the dead one's. Every restart silently orphaned its predecessor's
in-flight rows, forever. jobs.py's own docstring had predicted exactly this — "a
reap keyed on [a bare pid] can either miss rows or claim another process's" — and
then shipped the version that misses.

These tests are the positive control that was missing. They INSERT rows that must
be reaped and rows that must NOT be, then require the reap to tell them apart.
A reap verified only by "it did not crash" is the state that produced the bug.

Rows written here carry 'TEST' in error_detail and a worker name under
`test/`, so they are identifiable in the ledger afterwards — the ledger is
append-only history and this file deliberately does not delete its own evidence.

Run: backend/env/bin/python backend/tests/test_job_reap.py
"""
from __future__ import annotations

import os
import sys

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


def ensure_db():
    if not fs._db_ok:
        fs.db_init()
    assert fs._db_ok, (
        'no database — every test here would trivially "pass" by doing nothing, '
        'which is UNVERIFIED, not clean')
    assert fs._method_ids, 'no methods registered; a job row needs a method_row_id'


def a_method_row() -> str:
    return next(iter(fs._method_ids.values()))


def insert_running(worker: str, *, age_seconds: int = 0) -> str:
    """A row in flight, optionally backdated. Returns its id."""
    import hashlib
    import json
    sha = hashlib.sha256(f'{worker}|{age_seconds}|test'.encode()).digest()
    with fs._db() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO app.job (method_row_id, state, input_sha256, params, "
            "       worker, started_at, created_at) "
            "VALUES (%s, 'running', %s, %s, %s, "
            "        now() - %s * interval '1 second', "
            "        now() - %s * interval '1 second') RETURNING id",
            (a_method_row(), sha, json.dumps({'test': True, 'age': age_seconds}),
             worker, age_seconds, age_seconds))
        return str(cur.fetchone()[0])


def state_of(job_id: str) -> tuple[str, str | None]:
    with fs._db() as conn, conn.cursor() as cur:
        cur.execute('SELECT state::text, error_detail FROM app.job WHERE id = %s',
                    (job_id,))
        row = cur.fetchone()
    assert row, f'job {job_id} vanished'
    return row[0], row[1]


def test_a_row_from_a_DEAD_worker_is_reaped():
    """The three-hour row, reproduced: a plausible worker name whose pid is gone."""
    ensure_db()
    dead_pid = 999_999_999          # above every /proc/sys/kernel/pid_max in use
    job = insert_running(f'test/{dead_pid}/deadbeef')
    ledger = jobs.JobLedger(fs._db, jobs.make_worker_name(os.getpid(), 'testver'))
    out = ledger.reap()
    state, detail = state_of(job)
    assert state == 'failed', (
        f'a row from a dead worker is still {state!r} — this is the exact bug: '
        f'the reap ran, reported success, and cleaned nothing')
    assert out['dead_worker'] >= 1, f'reap did not count it: {out}'
    assert detail and 'restart' in detail.lower(), (
        f'the row does not say WHY it was failed ({detail!r}); a reaped job that '
        f'cannot be told apart from a genuine failure poisons the error stats')


def test_a_row_from_a_LIVE_worker_is_left_alone():
    """The other direction, which is the one that does damage if wrong.

    A second daemon — or the physics daemon — may legitimately have work in
    flight. Reaping by "not my name" would fail a running job out from under a
    live process, and the row would say INTERNAL about a computation that was
    fine. Uses THIS interpreter's pid, which is provably alive.
    """
    ensure_db()
    job = insert_running(f'test/{os.getpid()}/liveproc')
    ledger = jobs.JobLedger(fs._db, jobs.make_worker_name(os.getpid() + 1, 'other'))
    ledger.reap()
    state, _ = state_of(job)
    assert state == 'running', (
        f'a LIVE worker\'s row was reaped (now {state!r}) — the reap is keyed on '
        f'"not mine" instead of "not alive", which fails other processes\' work')
    # leave the ledger tidy: this row is ours to finish
    ledger.failed(job, code='CANCELLED', detail='TEST row, cancelled by test_job_reap')


def test_an_OVERRAN_row_is_reaped_whatever_pid_it_carries():
    """The second criterion, and the one that survives P2.

    A pid check is meaningless once a worker runs on another host, and it is
    blind to pid REUSE here. So a row older than the hard ceiling is finished no
    matter whose pid is on it — including a pid that is alive.
    """
    ensure_db()
    job = insert_running(f'test/{os.getpid()}/liveproc',
                         age_seconds=jobs.STALE_AFTER_SECONDS + 120)
    ledger = jobs.JobLedger(fs._db, jobs.make_worker_name(os.getpid(), 'testver'))
    out = ledger.reap()
    state, detail = state_of(job)
    assert state == 'failed', (
        f'a row {jobs.STALE_AFTER_SECONDS + 120} s old is still {state!r} — the '
        f'age ceiling is not enforced, so pid reuse and off-host workers both '
        f'leave rows running forever')
    assert out['overran'] >= 1, f'reap did not count the overrun: {out}'
    assert detail and 'ceiling' in detail.lower(), (
        f'the row does not distinguish an overrun from a dead worker ({detail!r})')


def test_a_fresh_row_survives_both_criteria():
    """A control on the controls: the reap must not be a blanket UPDATE.

    Without this, all three tests above would also pass on a reap that simply
    failed every running row — which would destroy live work on every restart.
    """
    ensure_db()
    job = insert_running(f'test/{os.getpid()}/liveproc', age_seconds=5)
    ledger = jobs.JobLedger(fs._db, jobs.make_worker_name(os.getpid(), 'testver'))
    ledger.reap()
    state, _ = state_of(job)
    assert state == 'running', (
        f'a 5-second-old row from a live pid was reaped (now {state!r}) — the '
        f'reap is a blanket UPDATE and would kill real work at every startup')
    ledger.failed(job, code='CANCELLED', detail='TEST row, cancelled by test_job_reap')


def test_an_unparseable_worker_name_is_treated_as_dead():
    """"I cannot tell" must fall to the side that gets CLEANED.

    Treating unknown as alive is precisely how the three-hour row survived. The
    age ceiling bounds the cost of being wrong in this direction; being wrong the
    other way is unbounded.
    """
    ensure_db()
    job = insert_running('legacy-worker-with-no-pid')
    ledger = jobs.JobLedger(fs._db, jobs.make_worker_name(os.getpid(), 'testver'))
    ledger.reap()
    state, _ = state_of(job)
    assert state == 'failed', (
        f'an unparseable worker name was treated as ALIVE (row is {state!r}) — '
        f'unknown must be cleanable, or one bad name orphans a row forever')


for name, fn in list(globals().items()):
    if name.startswith('test_') and callable(fn):
        check(name, fn)

print('─' * 100)
print(f'{len(PASS)} passed · {len(FAIL)} failed')
sys.exit(1 if FAIL else 0)
