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
import traces
from dirac_app import CommandDispatcher

PASS, FAIL = [], []


def _add(a, b):
    return a + b


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
    assert store.get(jid)['outcome_class'] == 'success'


def test_outcome_classes_separate_refusals_science_and_operations():
    expected = ('PARSE', 'UNPARAMETERIZED', 'BUDGET', 'UNSUPPORTED', 'TOO_LARGE')
    for code in expected:
        assert jobs.job_outcome_class('failed', code) == 'expected_refusal'
    assert jobs.job_outcome_class('failed', 'UNCONVERGED') == 'scientific_failure'
    assert jobs.job_outcome_class('failed', 'INTERNAL') == 'operational_failure'
    assert jobs.job_outcome_class('cancelled', 'CANCELLED') == 'cancelled'
    assert jobs.job_outcome_class('running') is None


def test_attention_is_derived_from_terminal_jobs_not_written_separately():
    store = jobs.MemoryJobStore()
    expected, _ = store.open(method_row_id='m', input_sha256=b'e' * 32, params={})
    store.failed(expected, code='BUDGET', detail='expected refusal')
    scientific, _ = store.open(method_row_id='m', input_sha256=b's' * 32, params={})
    store.failed(scientific, code='UNCONVERGED', detail='review science')
    operational, _ = store.open(method_row_id='m', input_sha256=b'o' * 32, params={})
    store.failed(operational, code='INTERNAL', detail='repair system')
    items = store.list_attention()
    assert {item['object_id'] for item in items} == {scientific, operational}
    assert {item['priority'] for item in items} == {'review', 'critical'}


def test_memory_store_persists_invocation_identity_and_terminal_meaning():
    store = jobs.MemoryJobStore()
    jid, _ = store.open(
        method_row_id='fields.mep', input_sha256=b'i' * 32, params={},
        actor_kind='agent', actor_id='planner-7',
        command_id='structure.field.compute', request_id='req-identity')
    row = store.get(jid)
    assert (row['actor_kind'], row['actor_id']) == ('agent', 'planner-7')
    assert row['command_id'] == 'structure.field.compute'
    assert row['request_id'] == 'req-identity'
    store.failed(jid, code='BUDGET', detail='cost exceeds caller budget')
    row = store.get(jid)
    assert row['outcome_class'] == 'expected_refusal'


def test_command_dispatcher_propagates_actor_command_and_request_to_kernel():
    class Kernel:
        def __init__(self):
            self.kw = None

        def invoke(self, _method_id, _payload, **kw):
            self.kw = kw
            return {'ok': True, 'data': {}, 'artifacts': [], 'warnings': [],
                    'meta': {}}

    kernel = Kernel()
    sink = traces.MemoryCommandTraceStore()
    result = CommandDispatcher(kernel, trace_sink=sink).execute(
        'conformer.generate', {'smiles': 'CC'},
        actor={'kind': 'agent', 'id': 'planner-7'}, request_id='req-dispatch')
    assert result['ok'] is True
    assert kernel.kw == {
        'request_id': 'req-dispatch',
        'actor': {'kind': 'agent', 'id': 'planner-7'},
        'command_id': 'conformer.generate',
    }
    assert len(sink.rows) == 1
    trace = sink.rows[0]
    assert trace['command_id'] == 'conformer.generate'
    assert trace['actor_kind'] == 'agent' and trace['actor_id'] == 'planner-7'
    assert trace['request_id'] == 'req-dispatch'
    assert trace['dispatch_outcome'] == 'success'


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


def test_process_and_remote_executors_share_the_same_boundary():
    process = execution.ProcessExecutor(max_workers=1)
    assert process.execute(_add, 2, 4) == 6
    assert process.submit(_add, 5, 7).result(timeout=3) == 12
    process.shutdown()

    backing = execution.ThreadExecutor(max_workers=1)
    remote = execution.RemoteExecutor(backing.submit)
    assert remote.execute(_add, 8, 9) == 17
    backing.shutdown()


if __name__ != 'probe':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            check(name, fn)

    print('─' * 100)
    print(f'{len(PASS)} passed · {len(FAIL)} failed')
    sys.exit(1 if FAIL else 0)
