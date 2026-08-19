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
import importlib
import inspect
import json
import pathlib
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
    # molecule.embed became EXECUTABLE on 2026-08-11, and an executable method whose
    # version is null cannot answer "which source produced this conformer" — which is the
    # whole question, because ETKDG with a different seed or a different MMFF pass gives a
    # different geometry and therefore a different field. Found by composing the two
    # methods and seeing `embed version None · field version 8422824f0c93`: half a
    # provenance chain is not a provenance chain.
    'molecule.embed': {
        'fns': ['embed_molecule'],
        # The seed is a PARAMETER, not a constant, so it is not listed. What is listed is
        # nothing — embed_molecule holds its own numbers inline today, and stating that
        # honestly beats listing a constant that does not exist.
        'consts': [],
        'exec_class': 'interactive',
        'in_schema': {'type': 'object',
                      'properties': {'smiles': {'type': 'string'},
                                     'molfile': {'type': 'string'},
                                     'seed': {'type': 'integer'}}},
        'out_schema': {'type': 'object', 'required': ['molfile'],
                       'properties': {'molfile': {'type': 'string',
                                                  'format': 'mdl-molfile-3d'}}},
        'capabilities': {'method': 'ETKDG + MMFF94',
                         'refuses': ['unparseable_smiles', 'embedding_failed'],
                         'deterministic_given_seed': True},
    },
    # The region methods, registered when /field/region joined the kernel. They share ONE
    # science function with two kinds, so they share a version — which is honest: the same
    # source produced both, and pretending otherwise would imply an independence that does
    # not exist. (fields.qm.* have shared a version the same way since they were added.)
    'fields.region.mep': {
        'fns': ['field_region', 'resolve_charges', 'write_cube'],
        'consts': ['MAX_REGION_SOURCES', 'WATER_RESNAMES', 'CHARGE_FORCEFIELD'],
        'exec_class': 'interactive',
        'in_schema': {'type': 'object', 'required': ['sources', 'frame'],
                      'properties': {'sources': {'type': 'array'},
                                     'frame': {'type': 'object'}}},
        'out_schema': {'type': 'object', 'required': ['cube', 'units'],
                       'properties': {'cube': {'type': 'string'},
                                      'units': {'const': 'kcal/mol'}}},
        'capabilities': {'charges': 'residue templates (pdb2pqr)',
                         'refuses': ['quantum_kind', 'no_sources', 'untypable_atom'],
                         'frame_is_callers': True},
    },
    'fields.qm.homo': _qm_unit('homo', 'homo_amplitude', 'amplitude'),
    'fields.qm.lumo': _qm_unit('lumo', 'lumo_amplitude', 'amplitude'),
    'fields.qm.density': _qm_unit('density', 'electron_density', 'e/Bohr^3'),
    'fields.qm.mep_qm': _qm_unit('mep_qm', 'electrostatic_potential', 'Ha/e'),
})

# Motif and governed research-support methods use the same registry and Job
# ledger as the existing physics methods. Their contracts are loaded from the
# canonical descriptors so the DB snapshot cannot drift into a second
# hand-maintained API definition.
_CONTRACTS = pathlib.Path(__file__).resolve().parent.parent / 'contracts' / 'methods'
for _descriptor_path in sorted(_CONTRACTS.glob('*.method.json')):
    _descriptor = json.loads(_descriptor_path.read_text(encoding='utf-8'))
    _method_id = _descriptor['method_id']
    _implementation = _descriptor.get('implementation') or {}
    _declared_module = str(_implementation.get('module') or '')
    # Motif descriptors are the one source of implementation identity.  The
    # old _MOTIF_UNITS table repeated every functions list, so the descriptor
    # could correctly name a new admission/helper function while runtime kept
    # hashing the stale hard-coded subset.  Discover this family by its owned
    # module and copy functions/constants directly from the descriptor.
    if not _declared_module.startswith(('backend.motif.', 'backend.research.')):
        continue
    _module = _declared_module.removeprefix('backend.')
    _functions = list(_implementation.get('functions') or ())
    if not _functions:
        raise ValueError(
            f'{_method_id} declares no implementation.functions; a Motif method '
            'without a source identity cannot be registered')
    UNITS[_method_id] = {
        'module': _module,
        'fns': _functions,
        'consts': list(_implementation.get('constants') or ()),
        'descriptor_path': str(_descriptor_path),
        'exec_class': 'job',
        'in_schema': _descriptor['input']['schema'],
        'out_schema': _descriptor['output']['schema'],
        'capabilities': {
            'resource_class': _descriptor['execution'].get('resource_class'),
            'determinism': _descriptor['execution'].get('determinism'),
            'supported_adapters': _descriptor['execution'].get('supported_adapters', []),
            'artifacts': [item['role'] for item in
                          (_descriptor.get('invocation') or {}).get('artifacts', [])],
            'refuses': [item['code'] for item in _descriptor.get('refusals', [])],
        },
        'toolkit': ('ai-provider' if _declared_module.startswith('backend.research.')
                    else 'rdkit'),
    }
UNITS['fields.region.mlp'] = {
    **UNITS['fields.region.mep'],
    'out_schema': {'type': 'object', 'required': ['cube', 'units'],
                   'properties': {'cube': {'type': 'string'},
                                  'units': {'const': 'MLP (Crippen/Fauchere)'}}},
    'capabilities': {**UNITS['fields.region.mep']['capabilities'],
                     'charges': 'caller-supplied logp contributions'},
}

# Physics used to declare these methods while leaving them absent at runtime. Their source
# lives in separate modules, so each unit names its module explicitly; plan() imports that
# module only for hashing and registration, never for transport routing.
UNITS.update({
    'surface.mep': {
        'module': 'physics.mep_surface',
        'fns': ['compute_surface_mep', '_prepare', '_ecp_for', '_install_watchdog',
                'clamp_budget', 'estimated_scf_seconds', 'nao_for'],
        'consts': ['DEFAULT_BASIS', 'DEFAULT_ISOVALUE', 'MAX_QM_ATOMS',
                   'GPU_CROSSOVER_NAO', 'GPU_SPEEDUP'],
        'exec_class': 'job',
        'in_schema': {'type': 'object', 'required': ['molecule']},
        'out_schema': {'type': 'object', 'required': ['summary']},
        'capabilities': {'refuses': ['unconverged', 'budget_exceeded',
                                     'open_shell_metal_without_spin'],
                         'artifacts': ['surface.points', 'surface.values']},
    },
    'surface.mep_at': {
        'module': 'physics.mep_surface',
        'fns': ['mep_at_points', '_prepare', '_ecp_for', '_install_watchdog',
                'clamp_budget'],
        'consts': ['DEFAULT_BASIS', 'MAX_QM_ATOMS'],
        'exec_class': 'job',
        'in_schema': {'type': 'object', 'required': ['molecule', 'points']},
        'out_schema': {'type': 'object', 'required': ['summary']},
        'capabilities': {'refuses': ['unconverged', 'budget_exceeded'],
                         'artifacts': ['surface.values']},
    },
    'torsion.strain': {
        'module': 'physics.torsion',
        'fns': ['compute_torsion_strain', '_relaxed_scan', '_force_field',
                '_relax_hydrogens', '_dihedral_atoms', '_verdict'],
        'consts': ['FORCE_FIELDS', 'ROTATABLE_SMARTS',
                   'TORSION_FORCE_CONSTANT', 'TORSION_WINDOW_DEG',
                   'MINIMIZE_STEPS', 'VERDICTS'],
        'exec_class': 'job',
        'in_schema': {'type': 'object', 'required': ['molecule']},
        'out_schema': {'type': 'object', 'required': ['summary']},
        'capabilities': {'refuses': ['unparameterized', 'unparseable_molfile'],
                         'artifacts': ['torsion.profile']},
    },
})


# ── version = hash of the compute unit, machine-derived ─────────────────────

def _canonical_repr(value) -> str:
    """A repr that is the same in every process. THE VERSION DEPENDS ON THIS.

    MEASURED DEFECT, 2026-08-11, found by noticing a version that moved between runs when
    the source had not: `repr(set)` iterates in hash order, and Python randomises string
    hashing per process (PYTHONHASHSEED). So a unit listing a set constant got a DIFFERENT
    version in every process —

        a7ecafb6dbd2 · {'WAT', 'TP3', 'SOL', 'HOH', 'TIP', 'H2O', 'DOD'}
        044091195c99 · {'HOH', 'TP3', 'TIP', 'SOL', 'DOD', 'H2O', 'WAT'}
        772a2048d83a · {'TP3', 'WAT', 'TIP', 'H2O', 'HOH', 'DOD', 'SOL'}

    — three digests, one source. That breaks the only thing a version is for: "same version
    means same code". Concretely it would have invalidated the cache on every daemon restart
    and stamped provenance a second run could not reproduce.
    (fields.mep/mlp were NOT affected and it was checked rather than assumed: their
    FIXED_ISO is a dict, and dict repr has followed INSERTION order since 3.7, so a literal
    is deterministic — measured stable across three processes. But relying on that is
    relying on an accident of the language for the foundation of provenance, so unordered
    containers are canonicalised here regardless of which ones happen to be safe today.)

    Recursive, because a tuple of sets is exactly the shape that would sneak past a
    shallow fix.
    """
    if isinstance(value, (set, frozenset)):
        return '{' + ', '.join(sorted(_canonical_repr(v) for v in value)) + '}'
    if isinstance(value, dict):
        return '{' + ', '.join(f'{_canonical_repr(k)}: {_canonical_repr(v)}'
                               for k, v in sorted(value.items(), key=lambda kv: repr(kv[0]))) + '}'
    if isinstance(value, (list, tuple)):
        inner = ', '.join(_canonical_repr(v) for v in value)
        return f'[{inner}]' if isinstance(value, list) else f'({inner})'
    if isinstance(value, pathlib.Path):
        # Source versions must not depend on where the same checkout lives.
        # Preserve the path within this repository (which is material) while
        # discarding the machine-specific absolute clone prefix.
        repository = pathlib.Path(__file__).resolve().parent.parent
        try:
            portable = value.resolve().relative_to(repository)
        except ValueError:
            portable = pathlib.Path(value.name)
        return f'Path({portable.as_posix()!r})'
    return repr(value)


def _resolve_declared_member(default_module, declaration: str):
    """Resolve ``name`` or ``backend.package.module:name`` declarations.

    A compute path can call helpers imported from another source module.  An
    unqualified ``getattr(default_module, name)`` finds the imported function,
    but it cannot name that helper's own transitive dependencies or constants.
    Qualified declarations make those dependencies explicit in the canonical
    descriptor while keeping ordinary single-module methods concise.
    """
    if ':' not in declaration:
        return default_module, declaration
    module_name, member_name = declaration.rsplit(':', 1)
    runtime_name = module_name.removeprefix('backend.')
    if not runtime_name or not member_name:
        raise LookupError(f'invalid qualified implementation member {declaration!r}')
    return importlib.import_module(runtime_name), member_name


def unit_version(module, fns: Iterable[str],
                 consts: Iterable[str] = (), *,
                 descriptor_path: str | None = None) -> tuple[str, bytes]:
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
    for declaration in sorted(fns):
        source_module, name = _resolve_declared_member(module, declaration)
        fn = getattr(source_module, name, None)
        if fn is None:
            raise LookupError(
                f'compute unit names {declaration!r}, which does not exist in '
                f'{source_module.__name__} — the descriptor has drifted from the code')
        h.update(declaration.encode())
        h.update(inspect.getsource(fn).encode())
    for declaration in sorted(consts):
        source_module, name = _resolve_declared_member(module, declaration)
        if not hasattr(source_module, name):
            raise LookupError(
                f'compute unit names the constant {declaration!r}, which does not exist '
                f'in {source_module.__name__} — a constant that vanished silently stops '
                f'being part of the version, which is how this hole was made')
        h.update(declaration.encode())
        h.update(_canonical_repr(getattr(source_module, name)).encode())
    if descriptor_path is not None:
        h.update(b'descriptor\0')
        h.update(pathlib.Path(descriptor_path).read_bytes())
    digest = h.digest()
    return digest.hex()[:12], digest


def plan(module) -> list[dict]:
    """What register_all would write. Pure; no DB. This is the smoke test."""
    out = []
    for method_id, spec in sorted(UNITS.items()):
        source_module = (importlib.import_module(spec['module'])
                         if spec.get('module') else module)
        version, digest = unit_version(
            source_module, spec['fns'], spec.get('consts', ()),
            descriptor_path=spec.get('descriptor_path'))
        out.append({'method_id': method_id, 'version': version, 'sha256': digest,
                    'exec_class': spec['exec_class'], 'fns': sorted(spec['fns']),
                    'in_schema': spec['in_schema'], 'out_schema': spec['out_schema'],
                    'capabilities': spec['capabilities']})
    return out


def register_all(conn_factory: Callable, module,
                 toolkit_ids: dict[str, str] | str | None = None) -> dict[str, str]:
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
            unit = UNITS[r['method_id']]
            toolkit_name = unit.get(
                'toolkit',
                'pyscf' if (r['method_id'].startswith('fields.qm.')
                            or r['method_id'].startswith('surface.')) else 'rdkit')
            toolkit_id = (toolkit_ids.get(toolkit_name)
                          if isinstance(toolkit_ids, dict) else toolkit_ids)
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

    # Field kinds are a legacy v1 transport mapping, not the authority for the
    # application registry. Every legacy kind must resolve, while methods that
    # are reachable through v2/commands are deliberately allowed to have no v1 kind.
    ids = {r['method_id'] for r in rows}
    missing = {k: m for k, m in KIND_TO_METHOD.items() if m not in ids}
    if missing:
        print(f'FAIL: kinds mapped to unregistered methods: {missing}')
        return 1
    print('OK: every legacy field kind resolves; application-only methods are '
          'reachable through the canonical catalog')

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
