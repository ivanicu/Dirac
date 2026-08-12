#!/usr/bin/env python3
"""The kernel field cache must be a round trip, not a read-only shortcut."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import cache_fields
import catalog
import field_server
import invocation


CUBE = '''cache-test\ncomment\n 1 0.000000 0.000000 0.000000\n 2 1.000000 0.000000 0.000000\n 2 0.000000 1.000000 0.000000\n 2 0.000000 0.000000 1.000000\n 1 0.0 0.0 0.0 0.0\n 0.1 0.2 0.3 0.4 0.5 0.6\n 0.7 0.8\n'''

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f'PASS    {name}')
    except AssertionError as e:
        FAIL.append(name)
        print(f'FAIL    {name}\n        {e}')


def test_cache_hit_uses_the_same_canonical_projection():
    def get(_sha, _kind, _basis):
        return CUBE, {'charges': 'gasteiger', 'natoms': 1, 'charge': 0,
                      'spin': 1, 'sigma_hole_representable': True}

    cache = cache_fields.FieldCubeCache(get)
    hit = cache.lookup('fields.mep', {
        'molecule': {'content': 'MOL'}, 'parameters': {}})
    assert hit is not None
    assert hit.result['field']['native_units'] == 'kcal/mol'
    assert hit.result['field']['grid']['dimensions'] == [2, 2, 2]
    assert hit.result['field']['extrema'] == {'min': 0.1, 'max': 0.8}
    catalog.default_catalog().validate_output('fields.mep', hit.result)


def test_computed_result_is_queued_for_the_same_durable_key():
    writes = []

    def put(sha, kind, basis, cube, meta, *, mol, job_id):
        writes.append((sha, kind, basis, cube, meta, mol, job_id))

    cache = cache_fields.FieldCubeCache(lambda *_: None, put_cube=put,
                                        prepare_mol=lambda text: f'parsed:{text}')
    out = invocation.HandlerResult(
        result={'field': {'kind': 'mep', 'native_units': 'kcal/mol',
                          'grid': {'dimensions': [2, 2, 2],
                                   'spacing_angstrom': 0.529177},
                          'extrema': {'min': 0.1, 'max': 0.8},
                          'single_signed': True},
                'model': {'charge_model': 'gasteiger',
                          'sigma_hole_representable': True}},
        artifacts=[('field.cube', CUBE.encode())],
        provenance={'n_atoms': 1, 'charge': 0, 'spin': 1},
        cache_record={'meta': {'charges': 'gasteiger'}})
    cache.store('fields.mep', {'molecule': {'content': 'MOL'}}, out,
                seconds=0.25, job_id='job-1')
    deadline = time.time() + 2
    while not writes and time.time() < deadline:
        time.sleep(0.01)
    assert len(writes) == 1, f'expected one persistence callback, got {len(writes)}'
    sha, kind, basis, cube, meta, mol, job = writes[0]
    assert len(sha) == 32 and kind == 'mep' and basis == 'none'
    assert cube == CUBE and mol == 'parsed:MOL' and job == 'job-1'
    assert meta['dims'] == [2, 2, 2] and meta['total_seconds'] == 0.25
    assert cache.counters['write_queued'] == 1
    assert cache.counters['write_ok'] == 1


def test_explicit_spin_bypasses_both_directions():
    looked, wrote = [], []
    cache = cache_fields.FieldCubeCache(
        lambda *args: looked.append(args), put_cube=lambda *a, **k: wrote.append((a, k)))
    payload = {'molecule': {'content': 'MOL'}, 'parameters': {'spin': 3}}
    assert cache.lookup('fields.qm.homo', payload) is None
    cache.store('fields.qm.homo', payload,
                invocation.HandlerResult(result={}, artifacts=[('field.cube', b'x')]),
                seconds=1.0)
    time.sleep(0.05)
    assert not looked and not wrote
    assert cache.counters['skipped_spin'] == 1


def test_writer_only_helpers_are_not_persisted_in_classical_meta():
    persisted = field_server.cacheable_meta({
        'kind': 'mep', 'charges': 'gasteiger', 'basis': 'none', 'natoms': 7,
        'single_signed': True, 'dims': [2, 2, 2], 'cache': 'computed',
    })
    assert persisted == {
        'kind': 'mep', 'charges': 'gasteiger', 'dims': [2, 2, 2],
    }, persisted


def test_cache_read_prefers_full_precision_json_and_hides_internal_v1_facts():
    row = (
        CUBE.encode(), -74.958608271, True, 3, 7, -10.3962, 15.2807, 1.2,
        'RHF', datetime.now(timezone.utc),
        {'kind': 'homo', 'homo_ev': -10.396226197, 'lumo_ev': 15.280728283,
         'basis': 'sto-3g', 'method': 'RHF', 'converged': True},
    )

    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, *_): pass
        def fetchone(self): return row

    class FakeConnection:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return FakeCursor()

    old_ok, old_db = field_server._db_ok, field_server._db
    field_server._db_ok = True
    field_server._db = lambda: FakeConnection()
    try:
        _, public = field_server.db_get_cube(b'x' * 32, 'homo', 'sto-3g')
        _, internal = field_server.db_get_cube(
            b'x' * 32, 'homo', 'sto-3g', include_internal=True)
    finally:
        field_server._db_ok, field_server._db = old_ok, old_db

    # v1 keeps its historical numeric(10,4) cache codec; only the canonical
    # kernel receives producer-native precision for cross-transport parity.
    assert public['homo_ev'] == -10.3962
    assert public['lumo_ev'] == 15.2807
    assert '_n_atoms' not in public
    assert internal['homo_ev'] == -10.396226197
    assert internal['lumo_ev'] == 15.280728283
    assert internal['_n_atoms'] == 3


for name, fn in list(globals().items()):
    if name.startswith('test_') and callable(fn):
        check(name, fn)

print('─' * 100)
print(f'{len(PASS)} passed · {len(FAIL)} failed')
sys.exit(1 if FAIL else 0)
