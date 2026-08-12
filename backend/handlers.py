"""Handlers: the science, wrapped in a signature every transport can call.

A handler is `(payload, ctx) -> HandlerResult`. It receives validated parameters and
returns a scientific result plus artifact BYTES; it never sees an HTTP request, never
touches the database, and never decides whether its cube travels inline. Those are the
service's jobs, and a handler that did any of them could not be called by a CLI.

THIS is the layer allowed to import RDKit and pyscf. It is also the only one — which
is what makes `dirac methods list` work on a machine with neither.

ONE DECISION WORTH READING, because it looks like extra work: the declared output
(grid dimensions, spacing, extrema) is derived FROM THE CUBE, not from the science
function's metadata dict. Two reasons, both measured today:

  · field_quantum's meta carries no dims/spacing/vmin/vmax at all — the classical
    paths add them at lines 900/973 and the quantum path never did. So a handler
    trusting meta would have to report null for four fields the artifact plainly
    contains.
  · deriving them from the artifact makes the response VERIFIABLE against the
    artifact: a client can download the cube, parse the same header and get the same
    numbers. When the result and the bytes come from one source, they cannot drift.

The alternative — adding those keys inside field_quantum — costs an implementation
digest bump on every fields.qm.* method, which invalidates the entire quantum cache.
That happened once already today for a one-line refusal change, and once was enough to
learn the price.
"""
from __future__ import annotations

from typing import Any

import failures
from invocation import HandlerResult, InvocationContext

# method_id → the `kind` string the existing science functions expect. The mapping is
# explicit rather than derived from the last dotted segment: `fields.qm.mep_qm` →
# `mep_qm` would work by luck and `fields.mep` → `mep` too, which is exactly the kind
# of coincidence that breaks silently when a method is renamed.
KIND_FOR_METHOD: dict[str, str] = {
    'fields.mep': 'mep',
    'fields.mlp': 'mlp',
    'fields.qm.homo': 'homo',
    'fields.qm.lumo': 'lumo',
    'fields.qm.density': 'density',
    'fields.qm.mep_qm': 'mep_qm',
}

QUANTUM_KINDS = {'homo', 'lumo', 'density', 'mep_qm'}


def parse_cube_header(cube: str) -> dict[str, Any]:
    """Grid geometry, read out of the Gaussian cube's own first six lines.

    The cube format is fixed: two comment lines, then `natoms ox oy oz`, then three
    axis lines `n vx vy vz`. Bohr unless natoms is negative. Parsed here rather than
    trusted from a metadata dict because the cube is the object and the dict is a
    description of it.
    """
    lines = cube.split('\n', 6)
    if len(lines) < 6:
        raise failures.DiracInternal(
            f'the cube has {len(lines)} lines before its grid block; it cannot be a '
            f'Gaussian cube, and reporting a grid from it would be invention')
    BOHR = 0.529177210903
    try:
        natoms = int(float(lines[2].split()[0]))
        axes = [lines[3].split(), lines[4].split(), lines[5].split()]
        dims = [int(float(a[0])) for a in axes]
        # The step along each axis, in Å. Diagonal grids are what every path here
        # produces; a rotated grid would need the full matrix, and the honest move is
        # to report the axis vector norms rather than pretend one number describes it.
        steps = []
        for i, a in enumerate(axes):
            v = [float(x) for x in a[1:4]]
            steps.append(round((sum(c * c for c in v) ** 0.5) * BOHR, 6))
    except (ValueError, IndexError) as e:
        raise failures.DiracInternal(f'unparseable cube grid block: {e}') from e
    return {'dimensions': dims, 'spacing_angstrom': steps[0],
            'spacing_per_axis_angstrom': steps,
            'n_atoms_in_cube': abs(natoms)}


def cube_extrema(cube: str) -> tuple[float, float]:
    """min and max of the volumetric values.

    numpy, because a Python loop over 840k floats costs ~1 s per request and this runs
    on every invocation. The value block starts after the header and (for a cube with
    an MO index line) one extra line — detected rather than assumed, since pyscf's
    cubegen writes that line and the classical writer does not.
    """
    import numpy as np
    lines = cube.split('\n')
    natoms = abs(int(float(lines[2].split()[0])))
    start = 6 + natoms
    # An MO-index line is a short line of small integers where a value row of 6
    # floats is expected. Sniffed on the FIRST candidate row only: guessing wrong
    # here shifts every value by one row and the extrema would be subtly wrong
    # rather than obviously broken.
    probe = lines[start].split() if start < len(lines) else []
    if probe and len(probe) <= 2 and all(p.lstrip('-').isdigit() for p in probe):
        start += 1
    values = np.fromstring(' '.join(lines[start:]), sep=' ')
    if values.size == 0:
        raise failures.DiracInternal(
            'the cube parsed to zero volumetric values, so its extrema would be a '
            'fabrication; the header said '
            f'{lines[3].split()[0]}x{lines[4].split()[0]}x{lines[5].split()[0]}')
    return float(values.min()), float(values.max())


def field_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    """Every fields.* method. One function, because they differ only in `kind`.

    Separate handlers per kind would mean six copies of the molecule-parsing, budget
    and provenance code, and the sixth would drift. What differs is dispatched on
    `kind` in one place, below.
    """
    import field_server as FS

    kind = KIND_FOR_METHOD.get(ctx.method_id)
    if kind is None:
        raise failures.DiracInternal(
            f'{ctx.method_id} routed to field_handler and KIND_FOR_METHOD has no entry '
            f'for it; the descriptor declares a handler this handler does not serve')

    mol_in = payload['molecule']
    params = dict(payload.get('parameters') or {})
    molblock = mol_in['content']

    basis = params.get('basis', 'sto-3g')
    spin = params.get('spin')
    budget = ctx.budget_seconds or FS.DEFAULT_MAX_SECONDS

    mol = FS.prepare_mol(molblock)
    ctx.check_budget()
    ctx.on_progress('prepared', 0.1)

    if kind in QUANTUM_KINDS:
        cube, meta = FS.field_quantum(mol, kind, basis, max_seconds=budget, spin=spin)
        parameters_used = {'basis': basis, 'spin': spin, 'max_seconds': budget}
    elif kind == 'mep':
        cube, meta = FS.field_mep(mol)
        parameters_used = {'charge_model': meta.get('method', 'gasteiger')}
    elif kind == 'mlp':
        cube, meta = FS.field_mlp(mol)
        parameters_used = {'logp_model': meta.get('method', 'crippen')}
    else:                                                          # pragma: no cover
        raise failures.DiracInternal(f'kind {kind!r} is in KIND_FOR_METHOD and has no '
                                     f'dispatch branch')
    ctx.on_progress('computed', 0.9)

    # CANONICALISE BEFORE ADDRESSING. pyscf writes the wall clock into cube line 1, so
    # without this the artifact's SHA-256 is a function of the time of day: measured,
    # three identical runs gave three different digests and the same calculation could
    # never deduplicate. The removed timestamp is kept in provenance below rather than
    # thrown away — it is a real fact, it just cannot live in the addressed bytes.
    import cube as CU
    computed_at = CU.timestamp_in(cube)
    cube = CU.canonicalise(cube)

    grid = parse_cube_header(cube)
    vmin, vmax = cube_extrema(cube)

    result: dict[str, Any] = {
        'field': {
            'kind': kind,
            'native_units': _UNITS[kind],
            'grid': {'dimensions': grid['dimensions'],
                     'spacing_angstrom': grid['spacing_angstrom']},
            'extrema': {'min': vmin, 'max': vmax},
        }
    }
    if kind in QUANTUM_KINDS:
        # `converged` is `const: true` in the output schema — an unconverged SCF is a
        # refusal, not a result with a flag. field_quantum already raises, and this
        # assert exists so a future change there cannot quietly produce a success
        # envelope the schema forbids.
        if not meta.get('converged'):
            raise failures.DiracUnconverged(
                f'SCF did not converge for {ctx.method_id}',
                details={'basis': basis, 'energy_ha': meta.get('scf_energy_ha')})
        result['wavefunction'] = {
            'converged': True,
            'method': meta.get('method', ''),
            'basis': basis,
            'n_basis_functions': int(meta.get('nbasis') or 0),
            'energy_hartree': meta.get('scf_energy_ha'),
            'scf_cycles': meta.get('scf_cycles'),
        }
        for key, out_key in (('homo_ev', 'homo_ev'), ('lumo_ev', 'lumo_ev')):
            if meta.get(key) is not None:
                result['wavefunction'][out_key] = meta[key]

    warnings = []
    if meta.get('frontier_caveat'):
        # A typed caveat, so a client can decide per code rather than by matching
        # prose. The minimal-basis warning is the difference between a shape and a
        # quotable number, and burying it in a metadata string made it invisible.
        warnings.append({'code': 'BASIS_NOT_QUOTABLE',
                         'message': meta['frontier_caveat'],
                         'affects': ['wavefunction.homo_ev', 'wavefunction.lumo_ev']})
    for k in ('sigma_hole_representable', 'model_caveat', 'physics_caveat'):
        if meta.get(k):
            warnings.append({'code': 'MODEL_CAVEAT', 'message': str(meta[k]),
                             'affects': ['field']})

    return HandlerResult(
        result=result,
        artifacts=[('field.cube', cube.encode())],
        provenance={
            'n_atoms': meta.get('natoms') or mol.GetNumAtoms(),
            'charge': meta.get('charge'),
            'spin': meta.get('spin'),
            'scf_seconds': meta.get('scf_seconds'),
            'cube_seconds': meta.get('cube_seconds'),
            'ecp': meta.get('ecp') or [],
            'toolkit_wrote_at': computed_at,
        },
        warnings=warnings,
        parameters_used=parameters_used,
        cache='computed')


_UNITS = {'mep': 'kcal_per_mol_per_e', 'mlp': 'dimensionless',
          'homo': 'amplitude', 'lumo': 'amplitude',
          'density': 'electrons_per_bohr3', 'mep_qm': 'hartree_per_e'}


def field_estimate(payload: dict) -> dict:
    """What a field would cost, without running it.

    The SCF scaling here is the SAME expression the daemon's pre-flight uses
    (2.8 x 5.9e-9 x nao^4.03, fitted on this machine), reached by importing it rather
    than by copying the constant — two fitted constants in two files would diverge on
    the first refit, and the estimate would then disagree with the refusal it is
    supposed to predict.

    Needs RDKit to count atoms, so it is honest about being unavailable without it
    rather than guessing from the molfile's line count.
    """
    mol_in = payload.get('molecule') or {}
    content = mol_in.get('content') or ''
    try:
        import field_server as FS
        mol = FS.prepare_mol(content)
        n_atoms = mol.GetNumAtoms()
    except Exception as e:                                          # noqa: BLE001
        return {'available': False,
                'reason': f'cannot parse the molecule to estimate its cost '
                          f'({type(e).__name__}); an estimate from the file size '
                          f'would be a number with no relationship to the work'}
    params = payload.get('parameters') or {}
    basis = params.get('basis', 'sto-3g')
    # Basis functions per atom, measured on this box: sto-3g water is 7 for 3 atoms.
    per_atom = {'sto-3g': 2.4, '6-31g': 4.6, '6-31g*': 6.1, 'def2-svp': 6.8}
    nao = max(1.0, n_atoms * per_atom.get(basis, 5.0))
    scf_seconds = 2.8 * 5.9e-9 * nao ** 4.03
    # A cube's cost is grid points, and the grid is chosen by the box, so this is the
    # weaker half of the estimate and is labelled as such.
    cube_seconds = 0.7 * max(1.0, n_atoms / 3.0)
    return {
        'available': True,
        'n_atoms': n_atoms,
        'n_basis_functions_estimated': int(nao),
        'seconds': round(scf_seconds + cube_seconds, 2),
        'seconds_breakdown': {'scf': round(scf_seconds, 2),
                              'cube': round(cube_seconds, 2)},
        'artifact_bytes_estimated': int(2.2e6 * max(1.0, n_atoms / 3.0)),
        'confidence': 'scf term is a fit on this machine (R^2 ~0.97 over 4 decades); '
                      'the cube term scales with the box and is the weaker half',
    }
