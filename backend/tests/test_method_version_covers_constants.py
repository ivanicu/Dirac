#!/usr/bin/env python3
"""A method version that hashes code but not the numbers the code READS is a
version that misses the edit most likely to happen.

THE INCIDENT, 2026-08-11: the MEP box-growing rule changed (the box now grows
until the contour closes across the whole slider range). Every cube's GEOMETRY
changed with it. The method version did not move, because the geometry is decided
by module-level constants — PAD_MAX, PAD_STEP, FIXED_ISO, GRID_MAX_DIM — which
the hashed functions read and do not contain. Reads go through
app.v_field_cube_servable, which keys on METHOD currency, so it kept serving
old-geometry cubes under the new rule and 22 rows had to be deleted by hand.

The failure is silent by construction: a stale cube is a perfectly valid cube.
Nothing crashes, nothing looks wrong, and the picture is of a box that today's
code would not have drawn.

Tuning a number IS the common change; rewriting a function is the rare one. So
these tests assert the version moves for BOTH, and — the part that matters —
that it moves for the right SCOPE: a classical geometry constant must not
invalidate the quantum units, or every tuning pass throws away real SCF work.

Run: backend/env/bin/python backend/tests/test_method_version_covers_constants.py
"""
from __future__ import annotations

import os
import sys
import importlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import field_server as fs                                            # noqa: E402
import method_registry as mr                                         # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f'PASS    {name}')
    except AssertionError as e:
        FAIL.append(name)
        print(f'FAIL    {name}\n        {e}')


def versions() -> dict:
    return {u['method_id']: u['version'] for u in mr.plan(fs)}


def moved_when(attr: str, delta):
    """Which method versions change when a module constant changes."""
    before = versions()
    original = getattr(fs, attr)
    setattr(fs, attr, delta(original))
    try:
        after = versions()
    finally:
        setattr(fs, attr, original)
    assert versions() == before, (
        f'restoring {attr} did not restore the versions — this test mutates '
        f'module state and must leave it exactly as found')
    return {k for k in before if before[k] != after[k]}


def test_a_geometry_constant_moves_the_classical_units():
    moved = moved_when('PAD_MAX', lambda v: v + 1.0)
    assert moved == {'fields.mep', 'fields.mlp'}, (
        f'PAD_MAX moved {sorted(moved)}; expected exactly the two classical grid '
        f'units. This is the exact hole that served 22 stale-geometry cubes: if '
        f'this set is EMPTY the version does not cover constants at all.')


def test_a_grid_constant_moves_the_quantum_units_and_only_those():
    moved = moved_when('CUBE_GRID_ORB', lambda v: v + 1)
    assert moved == {'fields.qm.homo', 'fields.qm.lumo', 'fields.qm.density',
                     'fields.qm.mep_qm'}, (
        f'CUBE_GRID_ORB moved {sorted(moved)}; expected exactly the four quantum '
        f'units. SCOPE is the point: a classical tuning pass that invalidated '
        f'converged SCF results would make everyone stop tuning.')


def test_a_classical_constant_does_not_invalidate_quantum_work():
    moved = moved_when('PAD_STEP', lambda v: v + 0.5)
    quantum = {m for m in moved if m.startswith('fields.qm.')}
    assert not quantum, (
        f'a classical padding step invalidated {sorted(quantum)} — an Fe-heme SCF '
        f'is minutes of CPU and must not be thrown away by a change that cannot '
        f'affect it')


def test_the_registry_refuses_a_constant_that_does_not_exist():
    """A named constant that vanishes must RAISE, not silently stop counting.

    Silent removal is how the original hole would come back: the name disappears
    from the module, the hash quietly covers one thing less, and nothing says so.
    """
    try:
        mr.unit_version(fs, ['field_mep'], ['NO_SUCH_CONSTANT_XYZ'])
    except LookupError as e:
        assert 'NO_SUCH_CONSTANT_XYZ' in str(e), (
            f'the refusal does not name the missing constant: {e}')
        return
    raise AssertionError(
        'unit_version accepted a constant that does not exist — a name that '
        'vanishes would silently leave the version, which is the failure mode '
        'this whole file is about')


def test_every_declared_constant_exists_today():
    """The declarations must be true right now, not aspirational."""
    for method_id, spec in mr.UNITS.items():
        module = importlib.import_module(spec['module']) if spec.get('module') else fs
        for const in spec.get('consts', ()):
            assert hasattr(module, const), (
                f'{method_id} declares the constant {const!r}, which is not in '
                f'{module.__name__} today')


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            check(name, fn)

    print('─' * 100)
    print(f'{len(PASS)} passed · {len(FAIL)} failed')
    sys.exit(1 if FAIL else 0)
