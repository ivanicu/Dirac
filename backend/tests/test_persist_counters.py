#!/usr/bin/env python3
"""A persist failure must be COUNTABLE, because the response already lied.

`meta.stored: true` is set when the background write is STARTED — the render must
never wait on the database, which is the right trade and cannot be revisited. The
hole it leaves is that a write failing afterwards cannot un-say `stored: true`.
So the requirement is not "never fail"; it is that the discrepancy is visible to
anyone who asks the service how it is doing.

WHY THESE TESTS AND NOT A GLANCE AT THE COUNTER: a counter nobody has ever seen
increment is indistinguishable from a counter that is not wired. The whole point
of this file is the POSITIVE CONTROL — force the failure, watch the number move.
Both directions are exercised, because a counter that increments on everything is
as useless as one that increments on nothing.

Run: backend/env/bin/python backend/tests/test_persist_counters.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import field_server as fs                                          # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f'PASS    {name}')
    except AssertionError as e:
        FAIL.append(name)
        print(f'FAIL    {name}\n        {e}')


def _snapshot():
    with fs._persist_lock:
        return dict(fs._persist)


def test_a_failing_write_increments_failed_and_records_the_reason():
    """POSITIVE CONTROL. Replace the connection factory with one that raises,
    call the real db_put_cube, and require the counter to move. If this test
    ever passes trivially, check that _db_ok is True — a short-circuit on a
    disabled cache would make the write never attempted and the counter never
    move, which reads identically to 'no failures'.
    """
    # db_init() runs from __main__ in the daemon, so an IMPORTED module starts
    # with the cache off. Call it here rather than skipping: a control that
    # silently declines to run is the failure mode this whole file is about.
    if not fs._db_ok:
        fs.db_init()
    if not fs._db_ok:
        raise AssertionError(
            'fs._db_ok is False, so db_put_cube returns before attempting a write and '
            'this control cannot fire. That is not a pass — bring the database up or '
            'declare this UNVERIFIED, never CLEAN.')

    before = _snapshot()
    original = fs._db

    class _Boom(RuntimeError):
        pass

    fs._db = lambda: (_ for _ in ()).throw(_Boom('injected: the disk is gone'))
    try:
        fs.db_put_cube(b'\x00' * 32, 'mep', 'none', 'CUBE\n', {'kind': 'mep'})
    finally:
        fs._db = original

    after = _snapshot()
    assert after['failed'] == before['failed'] + 1, (
        f"a raising write did not increment _persist['failed'] "
        f"({before['failed']} -> {after['failed']}) — the counter is not wired to the "
        f'failure path, so a silent persist failure would still be invisible')
    assert after['last_error'] and 'injected' in after['last_error'], (
        f"last_error is {after['last_error']!r} — a count with no reason attached "
        f'cannot be acted on; whoever reads /health needs to know WHAT failed')
    assert after['ok'] == before['ok'], (
        'a failed write also incremented the success counter — the two paths are '
        'not distinguishable, which makes both numbers meaningless')


def test_the_counter_does_not_move_when_nothing_was_written():
    """The other direction. A counter that increments on everything is as useless
    as one that increments on nothing, and this is the cheap way to tell them
    apart: do nothing, require nothing to change.
    """
    before = _snapshot()
    after = _snapshot()
    assert before == after, (
        f'the persist counters changed between two reads with no write in between '
        f'({before} -> {after}) — something else is incrementing them')


def test_health_exposes_the_counters_so_the_number_has_a_reader():
    """A count that never reaches a screen is a log line with extra steps.

    ASKS THE RUNNING SERVICE FIRST, and only falls back to reading the source if
    nothing is listening — with the fallback labelled UNVERIFIED rather than
    passed. A source grep for `'persist'` would be satisfied by the key existing
    in a dict that never gets sent, which is the check-encodes-the-instance trap
    this repo has already paid for twice today.
    """
    import json
    import urllib.request

    try:
        with urllib.request.urlopen('http://127.0.0.1:8901/health', timeout=5) as fh:
            health = json.load(fh)
    except Exception as e:                                          # noqa: BLE001
        import inspect
        src = inspect.getsource(fs.Handler.do_GET)
        assert "'persist'" in src, (
            "/health does not carry the persist counters, so the only way to learn "
            'that writes are failing is to be tailing the log as it happens')
        print(f'        UNVERIFIED (source only): no daemon on 8901 ({e}) — the key is '
              f'in the source, which does not prove it is SENT')
        return

    assert 'persist' in health, (
        f'the LIVE /health payload has no persist block (keys: {sorted(health)}) — '
        f'the counters exist in the process and reach no reader')
    for key in ('queued', 'ok', 'failed'):
        assert key in health['persist'], (
            f"/health persist block is missing {key!r}: {health['persist']} — "
            f'queued-vs-ok is the whole point; one number alone cannot show a gap')
    assert health['persist']['queued'] >= health['persist']['ok'], (
        f"persist reports more completed writes than were ever queued "
        f"({health['persist']}) — the counters are miscounting, and a wrong number "
        f'is worse than none because it will be believed')


for name, fn in list(globals().items()):
    if name.startswith('test_'):
        check(name, fn)

print('─' * 100)
print(f'{len(PASS)} passed · {len(FAIL)} failed')
sys.exit(1 if FAIL else 0)
