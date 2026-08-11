#!/usr/bin/env python3
"""Method registration — the socket every computation plugs into (migration 007).

WHY THIS IS A SEPARATE FILE, not a block inside field_server.py:

  A producer used to be one row per SERVICE, versioned by the sha256 of an
  849-line file. Measured on 2026-08-10: eight generations in fifty-three
  minutes, and 29% of the cache dark — because editing an HTTP comment
  invalidated every SCF ever computed. Identity has to be the hash of THE CODE
  THAT CAN CHANGE THE NUMBER and nothing else.

  So the unit of identity is a COMPUTE UNIT: `fields.mep` is the source of
  field_mep + write_cube + prepare_mol, and nothing to do with CORS headers or
  request parsing. Registration is a pure function of those sources, computed
  at startup, never typed by a human — which is why a forgotten version bump
  becomes impossible rather than merely loud.

  The same row is the socket the terminal state needs: an ML model is a method
  whose version is its checkpoint hash; docking, MD and FEP register the same
  way. Nothing in this file knows that, and that is the point — it takes a
  method_id, a set of functions, and a schema.

Usage from the service (one line, so the hot file stays cold):

    from method_registry import register_all
    METHOD_IDS = register_all(conn_factory=_db, module=sys.modules[__name__])

Standalone (also the smoke test — prints what it would register):

    backend/env/bin/python backend/method_registry.py [--apply]
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
from typing import Callable, Iterable

# ── the compute units ───────────────────────────────────────────────────────
#
# Each entry names the functions whose source can change the returned numbers.
# A function listed here is part of the version; a function NOT listed must be
# unable to change the answer — that is the invariant to check when editing
# this table, and it is the whole reason the table is explicit rather than
# "hash the module".
#
# `exec_class` is a claim about latency, not importance: 'interactive' means it
# is served inside the request; 'job' means it may outlive the request and
# therefore MUST have an app.job row (007 seam B).
UNITS: dict[str, dict] = {
    'fields.mep': {
        'fns': ['field_mep', 'write_cube', 'prepare_mol'],
        # The numbers that DECIDE THE GEOMETRY of the cube. They are read by
        # field_mep and contained in none of the functions above, so before they
        # were listed here a change to the box-growing rule left the version
        # untouched and the servable view kept serving old-geometry cubes.
        'consts': ['PAD_MAX', 'PAD_STEP', 'FIXED_ISO', 'GRID_MAX_DIM',
                   'ISO_ENCLOSED_FRACTION', 'ISO_SLIDER_FLOOR'],
        'exec_class': 'interactive',
        'in_schema': {'type': 'object', 'required': ['molfile'],
                      'properties': {'molfile': {'type': 'string'},
                                     'spacing': {'type': 'number', 'unit': 'angstrom'},
                                     'pad': {'type': 'number', 'unit': 'angstrom'}}},
        'out_schema': {'type': 'object', 'required': ['cube', 'units'],
                       'properties': {'cube': {'type': 'string', 'format': 'gaussian-cube'},
                                      'units': {'const': 'kcal/mol'}}},
        'capabilities': {
            'charges': 'gasteiger',
            # The PF6- incident, as data: Gasteiger returns NaN for hypervalent
            # P and several metals, and a zero field is silence rather than a
            # measurement. The refusal is a capability, not an error path.
            'refuses': ['gasteiger_nonfinite', 'all_zero_charges'],
            'refusal_points_at': 'fields.qm.mep_qm',
        },
    },
    'fields.mlp': {
        'fns': ['field_mlp', 'write_cube', 'prepare_mol'],
        'consts': ['PAD_MAX', 'PAD_STEP', 'FIXED_ISO', 'GRID_MAX_DIM',
                   'ISO_ENCLOSED_FRACTION', 'ISO_SLIDER_FLOOR'],
        'exec_class': 'interactive',
        'in_schema': {'type': 'object', 'required': ['molfile'],
                      'properties': {'molfile': {'type': 'string'}}},
        'out_schema': {'type': 'object', 'required': ['cube', 'units'],
                       'properties': {'cube': {'type': 'string', 'format': 'gaussian-cube'},
                                      'units': {'const': 'MLP (Crippen/Fauchere)'}}},
        'capabilities': {'charges': 'crippen', 'kernel': 'fauchere_exp_d_over_2',
                         'refuses': ['crippen_undefined'],
                         # Not in app.field_kind, so it is never DB-cached;
                         # 0.03 s means recompute is cheaper than the roundtrip.
                         'db_cacheable': False},
    },
    # the four quantum units are added below, from one template
}

# The four quantum units share one implementation and differ only in which
# quantity is read off the converged wavefunction — so they share a source set
# and therefore a version. Written as a loop because four hand-copied dicts is
# four places for a default to drift (the iodine bug was exactly that).
_QM_FNS = ['field_quantum', 'run_scf', 'ecp_for', 'write_cube', 'prepare_mol']
_QM_CAPS = {
    'reference': ['RHF', 'UHF'],
    'converger': ['diis', 'soscf'],
    # The iodine incident as data: def2 bases replace the core from Rb up with
    # an ECP, pyscf does not attach it automatically, and without it the answer
    # converges, balances charge, decays correctly at infinity, and is wrong by
    # 58 kcal/mol with the sign flipped. Any consumer choosing a basis for a
    # heavy element must be able to read this.
    'requires_ecp_from_z': 37,
    'basis_with_ecp': ['def2-svp'],
    'basis_without_ecp': ['sto-3g', '6-31g', '6-31g*'],
    'refuses': ['unconverged', 'budget_exceeded', 'open_shell_metal_without_spin'],
    'uncertainty_pct': 25,   # absolute values; ORDERINGS are stable (measured)
}


def _qm_unit(kind: str, quantity: str, units: str) -> dict:
    return {
        'fns': _QM_FNS,
        # A quantum cube's grid comes from pyscf's cubegen at these fixed
        # resolutions, and the cost model decides whether it runs at all. Both
        # are numbers, both change the output or the refusal, and neither lives
        # inside a listed function.
        'consts': ['CUBE_GRID_MEP', 'CUBE_GRID_ORB', 'CUBE_MEP_FIXED',
                   'CUBE_MEP_MARGINAL', 'CUBE_ORB_FIXED', 'CUBE_ORB_MARGINAL'],
        'exec_class': 'interactive',
        'in_schema': {'type': 'object', 'required': ['molfile'],
                      'properties': {'molfile': {'type': 'string'},
                                     'basis': {'enum': ['sto-3g', '6-31g', '6-31g*', 'def2-svp']},
                                     'spin': {'type': ['integer', 'null']},
                                     'max_seconds': {'type': 'number', 'unit': 's'}}},
        'out_schema': {'type': 'object', 'required': ['cube', 'units', 'method', 'converged'],
                       'properties': {'cube': {'type': 'string', 'format': 'gaussian-cube'},
                                      'units': {'const': units},
                                      'quantity': {'const': quantity}}},
        'capabilities': dict(_QM_CAPS, quantity=quantity),
    }


UNITS.update({
    'fields.qm.homo': _qm_unit('homo', 'homo_amplitude', 'amplitude'),
    'fields.qm.lumo': _qm_unit('lumo', 'lumo_amplitude', 'amplitude'),
    'fields.qm.density': _qm_unit('density', 'electron_density', 'e/Bohr^3'),
    'fields.qm.mep_qm': _qm_unit('mep_qm', 'electrostatic_potential', 'Ha/e'),
})


# ── version = hash of the compute unit, machine-derived ─────────────────────

def unit_version(module, fns: Iterable[str],
                 consts: Iterable[str] = ()) -> tuple[str, bytes]:
    """(short hex version, full sha256) over the SOURCE of the named functions
    AND THE VALUES of the named module-level constants.

    Sorted, so declaration order in UNITS cannot change a version. Source only
    for functions: a docstring edit does change the version, which is the
    conservative direction — the dangerous direction is a behaviour change that
    does NOT change it.

    ⚠ CONSTANTS WERE THE HOLE, and it cost 22 cached rows that had to be deleted
    by hand. The geometry of an MEP cube is decided by module-level numbers —
    PAD_MAX, PAD_STEP, FIXED_ISO, GRID_MAX_DIM — that the listed functions READ
    but do not CONTAIN. So the box-growing rule changed, every cube's geometry
    changed with it, and the method version did not move: reads go through
    app.v_field_cube_servable, which keys on method currency, and it happily
    served cubes with the OLD geometry under the NEW rule. The failure is silent
    by construction, because a stale cube is a perfectly valid cube.

    Hashing source-but-not-constants means the version misses exactly the edit
    most likely to happen. Tuning a number IS the common change; rewriting a
    function is the rare one. `repr` is used deliberately: 12.0 and 12 are
    different values here and must produce different versions.
    """
    h = hashlib.sha256()
    for name in sorted(fns):
        fn = getattr(module, name, None)
        if fn is None:
            raise LookupError(
                f'compute unit names {name!r}, which does not exist in '
                f'{module.__name__} — the UNITS table has drifted from the code')
        h.update(name.encode())
        h.update(inspect.getsource(fn).encode())
    for name in sorted(consts):
        if not hasattr(module, name):
            raise LookupError(
                f'compute unit names the constant {name!r}, which does not exist '
                f'in {module.__name__} — a constant that vanished silently stops '
                f'being part of the version, which is how this hole was made')
        h.update(name.encode())
        h.update(repr(getattr(module, name)).encode())
    digest = h.digest()
    return digest.hex()[:12], digest


def plan(module) -> list[dict]:
    """What register_all would write. Pure; no DB. This is the smoke test."""
    out = []
    for method_id, spec in sorted(UNITS.items()):
        version, digest = unit_version(module, spec['fns'],
                                       spec.get('consts', ()))
        out.append({'method_id': method_id, 'version': version, 'sha256': digest,
                    'exec_class': spec['exec_class'], 'fns': sorted(spec['fns']),
                    'in_schema': spec['in_schema'], 'out_schema': spec['out_schema'],
                    'capabilities': spec['capabilities']})
    return out


def register_all(conn_factory: Callable, module, toolkit_id: str | None = None) -> dict[str, str]:
    """Register every compute unit; return {method_id: method_row_id}.

    Idempotent by construction: meta.register_method returns the existing row
    when (method_id, version) is unchanged, and supersedes the previous
    generation when the source moved. Failure to reach the DB is NOT fatal
    here — a cache is recomputable and an outage of it must never become an
    outage of the compute path. The caller decides what to do with {}.
    """
    ids: dict[str, str] = {}
    rows = plan(module)
    with conn_factory() as conn, conn.cursor() as cur:
        for r in rows:
            cur.execute(
                'SELECT meta.register_method(%s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (r['method_id'], r['version'], r['sha256'],
                 json.dumps(r['in_schema']), json.dumps(r['out_schema']),
                 r['exec_class'], json.dumps(r['capabilities']), toolkit_id,
                 'auto-registered from source hash of ' + ', '.join(r['fns'])))
            ids[r['method_id']] = cur.fetchone()[0]
    return ids


# Which method produced a given field kind — the join the cache needs when it
# writes field_cube.method_row_id. One table, so a new kind cannot be cached
# under a method that did not compute it.
KIND_TO_METHOD = {
    'mep': 'fields.mep',
    'mlp': 'fields.mlp',
    'homo': 'fields.qm.homo',
    'lumo': 'fields.qm.lumo',
    'density': 'fields.qm.density',
    'mep_qm': 'fields.qm.mep_qm',
}


def main() -> int:
    sys.path.insert(0, __file__.rsplit('/', 1)[0])
    import field_server as fs

    rows = plan(fs)
    print(f'{len(rows)} compute units:')
    for r in rows:
        caps = r['capabilities']
        print(f"  {r['method_id']:22s} v{r['version']}  {r['exec_class']:11s} "
              f"refuses={len(caps.get('refuses', []))}  fns={len(r['fns'])}")

    # The invariant that keeps this table honest: every kind the service can
    # serve must map to a registered method, and every registered method must
    # be reachable from some kind.
    ids = {r['method_id'] for r in rows}
    missing = {k: m for k, m in KIND_TO_METHOD.items() if m not in ids}
    orphan = ids - set(KIND_TO_METHOD.values())
    if missing:
        print(f'FAIL: kinds mapped to unregistered methods: {missing}')
        return 1
    if orphan:
        print(f'FAIL: registered methods no kind can reach: {orphan}')
        return 1
    print('OK: kind↔method mapping is total and has no orphans')

    if '--apply' in sys.argv:
        ids_written = register_all(fs._db, fs)
        print(f'registered {len(ids_written)} methods:')
        for mid, row in sorted(ids_written.items()):
            print(f'  {mid:22s} -> {row}')
    else:
        print('(dry run — pass --apply to write to the dirac database)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
