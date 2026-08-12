#!/usr/bin/env python3
"""Postgres and memory job implementations share one public lifecycle contract."""
from __future__ import annotations

import hashlib
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import execution
import jobs

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f'PASS    {name}')
    except AssertionError as e:
        FAIL.append(name)
        print(f'FAIL    {name}\n        {e}')


def test_memory_store_lifecycle_and_durability_are_explicit():
    store = jobs.MemoryJobStore()
    jid, joined = store.open(
        method_row_id='fields.qm.homo', input_sha256=hashlib.sha256(b'a').digest(),
        params={'basis': 'sto-3g'}, queued=True)
    assert jid and not joined
    assert store.kind == 'memory' and store.durability == 'process'
    assert store.get(jid)['state'] == 'queued'
    store.start(jid)
    assert store.get(jid)['state'] == 'running'
    store.done(jid, seconds=1.2349)
    assert store.get(jid)['state'] == 'done'
    assert store.get(jid)['seconds'] == 1.235
    assert store.list(state='done')[0]['id'] == jid


def test_identical_inflight_open_joins_instead_of_duplicating():
    store = jobs.MemoryJobStore()
    kw = dict(method_row_id='fields.qm.homo',
              input_sha256=hashlib.sha256(b'same').digest(),
              params={'basis': 'sto-3g'}, queued=True)
    first, joined_first = store.open(**kw)
    second, joined_second = store.open(**kw)
    assert not joined_first and joined_second
    assert first == second and len(store.list()) == 1


def test_cancellation_distinguishes_queued_from_running():
    store = jobs.MemoryJobStore()
    queued, _ = store.open(method_row_id='m', input_sha256=b'q' * 32,
                           params={}, queued=True)
    q = store.request_cancel(queued)
    assert q['state'] == 'cancelled' and q['cancel']['accepted'] is True

    running, _ = store.open(method_row_id='m', input_sha256=b'r' * 32,
                            params={}, queued=False)
    r = store.request_cancel(running)
    assert r['state'] == 'running' and r['cancel'] == {
        'requested': True, 'accepted': False, 'capability': 'not_interruptible'}


def test_executors_share_execute_and_thread_submission_is_bounded():
    inline = execution.InlineExecutor()
    assert inline.execute(lambda a, b: a + b, 2, 3) == 5

    gate = threading.Event()
    threaded = execution.ThreadExecutor(max_workers=1)
    first = threaded.submit(lambda: gate.wait(2) or 'first')
    second = threaded.submit(lambda: 'second')
    assert threaded.cancel(second) is True
    gate.set()
    first.result(timeout=3)
    threaded.shutdown()


for name, fn in list(globals().items()):
    if name.startswith('test_') and callable(fn):
        check(name, fn)

print('─' * 100)
print(f'{len(PASS)} passed · {len(FAIL)} failed')
sys.exit(1 if FAIL else 0)
