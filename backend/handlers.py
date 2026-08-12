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

import json
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

    return field_result(
        ctx, kind, basis, cube, meta,
        mol_n_atoms=mol.GetNumAtoms(), toolkit_wrote_at=computed_at,
        parameters_used=parameters_used,
        cache_record={'meta': dict(meta)})


def field_result(ctx: InvocationContext, kind: str, basis: str, cube: str, meta: dict,
                 *, mol_n_atoms: int | None = None,
                 toolkit_wrote_at: str | None = None,
                 parameters_used: dict[str, Any] | None = None,
                 cache_record: dict[str, Any] | None = None,
                 cache: str = 'computed') -> HandlerResult:
    """Project producer-native field data into the one canonical result shape.

    Fresh calculations and durable cache hits both pass through this function. Units,
    warnings, optional model facts and wavefunction names therefore have one home instead
    of two implementations that merely hope to keep agreeing.
    """
    grid = parse_cube_header(cube)
    vmin, vmax = cube_extrema(cube)

    # EVERY DECLARED KEY, populated from what the science already computed. Written the
    # other way round first — the handler returned four facts and the frontend read
    # twenty-one out of an unschema'd `meta` dict — which meant a renderer depended on
    # facts the contract never promised. The output schema now declares them and
    # catalog.validate_output enforces it, so this block cannot silently shrink.
    box = {k: v for k, v in (
        ('iso_fixed', meta.get('iso_fixed')),
        ('iso_sized_for', meta.get('iso_sized_for')),
        ('contour_closes_in_box', meta.get('contour_closes_in_box')),
        ('pad_angstrom', meta.get('pad_used_angstrom')),
        ('capped', meta.get('grid_capped')),
        ('wall_seconds', meta.get('wall_max')),
    ) if v is not None}
    model = {k: v for k, v in (
        ('charge_model', meta.get('charges') if kind == 'mep' else None),
        ('logp_model', meta.get('method') if kind == 'mlp' else None),
        ('net_charge', meta.get('net_charge')),
        ('total_logp', meta.get('total_logp')),
        ('sigma_hole_representable', meta.get('sigma_hole_representable')),
    ) if v is not None}
    result: dict[str, Any] = {
        'field': {
            'kind': kind,
            'native_units': declared_units(ctx, kind),
            'grid': {'dimensions': grid['dimensions'],
                     'spacing_angstrom': grid['spacing_angstrom']},
            'extrema': {'min': vmin, 'max': vmax},
            # Derived from the extrema rather than read from meta: the classical paths
            # report it and the quantum ones never did, and it is a one-line consequence
            # of two numbers this function already holds. A fact computed where its
            # inputs are is a fact that cannot be missing on one path.
            'single_signed': bool(vmin >= 0 or vmax <= 0),
            **({'box': box} if box else {}),
        },
        **({'model': model} if model else {}),
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
        # The KEY NAMES are the descriptor's, not mine. `energy_hartree` was rejected by
        # output validation because the contract calls it `scf_energy_hartree` — and the
        # contract is the name every client was told to read.
        wf: dict[str, Any] = {
            'converged': True,
            'method': meta.get('method', ''),
            'basis': basis,
            'n_basis_functions': int(meta.get('nbasis') or 0),
        }
        for src, dst in (('scf_energy_ha', 'scf_energy_hartree'),
                         ('scf_cycles', 'scf_cycles'),
                         ('homo_ev', 'homo_ev'), ('lumo_ev', 'lumo_ev')):
            if meta.get(src) is not None:
                wf[dst] = meta[src]
        if meta.get('ecp'):
            wf['ecp_elements'] = list(meta['ecp'])
        result['wavefunction'] = wf

    warnings = []
    if meta.get('frontier_caveat'):
        # A typed caveat, so a client can decide per code rather than by matching
        # prose. The minimal-basis warning is the difference between a shape and a
        # quotable number, and burying it in a metadata string made it invisible.
        warnings.append({'code': 'BASIS_NOT_QUOTABLE',
                         'message': meta['frontier_caveat'],
                         'affects': ['wavefunction.homo_ev', 'wavefunction.lumo_ev']})
    if meta.get('sigma_hole_representable') is False:
        warnings.append({
            'code': 'SIGMA_HOLE_NOT_REPRESENTABLE',
            'message': 'A spherical point-charge model cannot represent sigma-hole '
                       'anisotropy; this field cannot answer a halogen-bonding question.',
            'affects': ['field'],
        })
    for k in ('model_caveat', 'physics_caveat'):
        if meta.get(k):
            warnings.append({'code': 'MODEL_CAVEAT', 'message': str(meta[k]),
                             'affects': ['field']})

    return HandlerResult(
        result=result,
        artifacts=[('field.cube', cube.encode())],
        provenance={
            'n_atoms': meta.get('natoms') or mol_n_atoms,
            'charge': meta.get('charge'),
            'spin': meta.get('spin'),
            'scf_seconds': meta.get('scf_seconds'),
            'cube_seconds': meta.get('cube_seconds'),
            'ecp': meta.get('ecp') or [],
            'computed_at': meta.get('computed_at'),
            'toolkit_wrote_at': toolkit_wrote_at or meta.get('toolkit_wrote_at'),
        },
        warnings=warnings,
        parameters_used=parameters_used or {},
        cache=cache,
        cache_record=cache_record)


def declared_units(ctx: InvocationContext, kind: str) -> str:
    """The unit string THE DESCRIPTOR declares. One home for it.

    Replaces a hand-written `_UNITS` dict that disagreed with the contract in four of six
    methods ('kcal_per_mol_per_e' vs the declared 'kcal/mol', 'dimensionless' vs
    'MLP (Crippen/Fauchere)', and so on). Six transports read that dict and therefore
    agreed with each other perfectly while all six were wrong — which is why parity across
    transports is necessary and not sufficient, and why output validation is the check that
    actually binds.

    Falls back to the kind name only if the descriptor declares no const, and that
    fallback is a stated last resort rather than a guess dressed as a default.
    """
    try:
        units = (ctx.spec.output_schema['properties']['field']
                 ['properties']['native_units'])
        for key in ('const', 'default'):
            if key in units:
                return str(units[key])
        if units.get('enum'):
            return str(units['enum'][0])
    except (AttributeError, KeyError, TypeError, IndexError):
        pass
    raise failures.DiracInternal(
        f'{ctx.method_id} declares no native_units const in its output schema, so this '
        f'handler has no authority to state the units of the field it just computed. '
        f'Inventing a string here is how the previous _UNITS dict came to disagree with '
        f'four descriptors.')


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


def embed_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    """molecule.embed — SMILES or a 2D molfile to real 3D coordinates.

    WHY THIS METHOD HAD TO BECOME EXECUTABLE, and it was found by dogfooding rather than
    by reading: the frontend was sending a 2D molfile (`RDKit 2D` in the header) to a field
    method. v1 accepted it, because prepare_mol quietly embeds when there are no 3D
    coordinates — so ONE invocation was doing TWO methods' work, and the response reported
    one method version for a result that had passed through two pieces of science. The
    input contract already forbade it, in its own words: "A 2D structure must not reach a
    3D physics method — the coordinate space is part of the type, and molecule.embed is the
    explicit step that produces 3D."

    So the fix is not to relax the contract. It is to make the step the contract names
    actually callable, and let a client compose the two — two invocations, two versions,
    two provenance records, and a conformer whose origin is stated rather than implied.

    The output is EXACTLY the `molecule` object a field method takes as input. That
    composition is designed into the descriptors, and it is why this handler returns a
    molecule rather than a molfile string.
    """
    import field_server as FS

    mol_in = payload.get('molecule') or {}
    smiles = payload.get('smiles')
    molblock = payload.get('molfile') or (mol_in.get('content') if mol_in else None)
    if not smiles and not molblock:
        raise failures.DiracParseFailure(
            'molecule.embed needs either `smiles` or `molfile`; neither was given',
            details={'got_keys': sorted(payload)})
    params = dict(payload.get('parameters') or {})
    seed = int(params.get('seed', 42))

    # embed_molecule returns (molblock_text, meta) — READ from the function rather than
    # guessed from its signature, which is how the first version of this handler came to
    # pass a tuple to MolToMolBlock.
    content, emeta = FS.embed_molecule(smiles, molblock, seed=seed)
    # A 3D claim that is CHECKED rather than asserted: an embed that silently produced a
    # flat conformer would hand a field method exactly the input this method exists to
    # prevent, and `dimensionality: 3` in the output schema would then be a lie the
    # contract itself cannot catch. Parsed back from the molblock, so the check is on the
    # BYTES that will travel, not on an object that produced them.
    # THE SAME BUG LIVED HERE and was found in the browser, in the TypeScript twin: reading
    # "until the lines stop looking like atoms" walks straight into the BOND block, whose
    # lines have four fields and whose third column is the bond order. That read 2 bonds as
    # atoms and took bond orders for z. The counts line says how many atom lines there are;
    # anything else is a heuristic wearing a parser's clothes.
    lines = content.split('\n')
    try:
        n_atoms = int(lines[3].split()[0])
    except (IndexError, ValueError):
        raise failures.DiracInternal(
            'the embedded molfile has no parseable counts line, so the number of atom rows '
            'is unknown and a flatness check on it would be reading arbitrary text')
    zs = []
    for line in lines[4:4 + n_atoms]:
        parts = line.split()
        if len(parts) < 4:
            break
        try:
            zs.append(float(parts[2]))
        except ValueError:
            break
    if zs and max(zs) - min(zs) < 1e-6 and n_atoms > 2:
        raise failures.DiracInternal(
            f'the embedding produced a FLAT conformer (z range '
            f'{max(zs) - min(zs):.2e} Å over {n_atoms} heavy atoms), so declaring it 3D '
            f'would pass a 2D structure into a 3D physics method under a true-looking type')

    return HandlerResult(
        result={'molecule': {'kind': 'molfile', 'content': content,
                             'format': 'mdl-v2000', 'dimensionality': 3,
                             'coordinate_units': 'angstrom'}},
        artifacts=[('molecule.molfile', content.encode())],
        provenance={'n_atoms': emeta.get('natoms'), 'n_atoms_heavy': n_atoms,
                    'seed': seed, 'source': 'smiles' if smiles else 'molfile',
                    'inchikey': emeta.get('inchikey'),
                    'smiles_canonical': emeta.get('smiles_canonical'),
                    'z_range_angstrom': round(max(zs) - min(zs), 4) if zs else None},
        parameters_used={'seed': seed, 'optimize': params.get('optimize', True)},
        cache='computed')


def embed_estimate(payload: dict) -> dict:
    """Embedding is milliseconds and its cost is not worth predicting badly."""
    return {'available': True, 'seconds': 0.2,
            'confidence': 'ETKDG + MMFF on a drug-sized ligand is 0.05-0.5 s on this '
                          'machine; the number is a ceiling, not a fit'}


REGION_KIND_FOR_METHOD = {'fields.region.mep': 'mep', 'fields.region.mlp': 'mlp'}


def region_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    """fields.region.* — the classical field of an ARBITRARY atom set in a caller's box.

    THE LAST ROUTE OUTSIDE THE KERNEL until now. /field/region answered in the flat v1 shape
    and the frontend lifted it into the canonical view through a pocket named
    `regionExtras` — one fact, two homes, and the pocket existed only because this method had
    no contract. It has one now, so the pocket goes.

    It also carried the line PR-03 deleted everywhere else:

        reason = 'unsupported' if isinstance(e, (ValueError, KeyError)) else 'internal'

    A KeyError from a missing `frame` and a ValueError from "quantum region fields are not
    attempted" were being reported to a client as the same word. Through the kernel they are
    INVALID_PARAMETERS and UNSUPPORTED respectively — different remedies, which is the whole
    point of the vocabulary.

    NOT CACHEABLE, declared in the descriptor: the cache is keyed on a molfile hash, and this
    method has no molfile. A source set plus a box would need a different key, and inventing
    one that collides would serve a pocket field for the wrong pocket — the one failure mode
    worse than recomputing.
    """
    import field_server as FS

    kind = REGION_KIND_FOR_METHOD.get(ctx.method_id)
    if kind is None:
        raise failures.DiracInternal(
            f'{ctx.method_id} routed to region_handler and REGION_KIND_FOR_METHOD has no '
            f'entry for it')
    sources = payload['sources']
    frame = payload['frame']
    params = dict(payload.get('parameters') or {})
    spacing = float(frame.get('spacing', 0.5))
    dielectric = params.get('dielectric') or 'r-dependent'

    # THE CONTRACT DECLARES `xyz: [x, y, z]`; field_region reads a['x'], a['y'], a['z'].
    # The contract keeps the better shape and the handler adapts, which is this layer's job:
    # a coordinate triple is ONE fact, and three separate keys can be two-thirds present
    # while an array of exactly 3 cannot. Adapting here costs a dict comprehension; declaring
    # the weaker shape would cost every future client the ability to be sure a position is
    # complete.
    adapted = []
    for a in sources:
        x, y, z = a['xyz']
        adapted.append({**{k: v for k, v in a.items() if k != 'xyz'},
                        'x': x, 'y': y, 'z': z})
    cube, meta = FS.field_region(adapted, frame['lo'], frame['hi'], spacing, kind,
                                 req_dielectric=dielectric)
    ctx.on_progress('computed', 0.9)

    import cube as CU
    wrote_at = CU.timestamp_in(cube)
    cube = CU.canonicalise(cube)
    grid = parse_cube_header(cube)
    vmin, vmax = cube_extrema(cube)

    box = {k: v for k, v in (
        ('iso_fixed', meta.get('iso_fixed')),
        ('contour_closes_in_box', meta.get('contour_closes_in_box')),
        ('wall_seconds', meta.get('wall_max')),
    ) if v is not None}
    result: dict[str, Any] = {
        'field': {
            'kind': f'{kind}_region',
            'native_units': declared_units(ctx, kind),
            'grid': {'dimensions': grid['dimensions'],
                     'spacing_angstrom': grid['spacing_angstrom']},
            'extrema': {'min': vmin, 'max': vmax},
            'single_signed': bool(vmin >= 0 or vmax <= 0),
            **({'box': box} if box else {}),
        },
        'model': {k: v for k, v in (
            ('charge_model', meta.get('charge_model')),
            ('net_charge', meta.get('net_charge')),
            ('total_logp', meta.get('total_logp')),
        ) if v is not None},
        'region': {
            'n_sources_sent': int(meta.get('n_sources_sent') or 0),
            'n_sources_used': int(meta.get('n_sources_used') or 0),
            'cutoff_angstrom': meta.get('cutoff_angstrom'),
            'waters_excluded': int(meta.get('waters_excluded') or 0),
            # Stated rather than implied: this method cannot grow the box, so a clipped
            # surface is reported instead of repaired.
            'frame_is_callers': True,
            'dielectric': meta.get('dielectric'),
        },
    }
    warnings = []
    for key, code in (('physics_caveat', 'MODEL_CAVEAT'),
                      ('model_caveat', 'MODEL_CAVEAT'),
                      ('waters_note', 'SOURCES_EXCLUDED')):
        if meta.get(key):
            warnings.append({'code': code, 'message': str(meta[key]),
                             'affects': ['field', 'region']})
    return HandlerResult(
        result=result,
        artifacts=[('field.cube', cube.encode())],
        provenance={'n_atoms': meta.get('n_sources_used'),
                    'toolkit_wrote_at': wrote_at},
        warnings=warnings,
        parameters_used={'dielectric': dielectric, 'spacing': spacing},
        cache='computed')


def surface_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    """Both QM surface methods through the canonical invocation kernel."""
    import numpy as np
    from physics import mep_surface as MS

    molecule = payload['molecule']['content']
    params = dict(payload.get('parameters') or {})
    basis = params.get('basis', MS.DEFAULT_BASIS)
    budget = ctx.budget_seconds or params.get('max_seconds') or MS.DEFAULT_MAX_SECONDS
    try:
        if ctx.method_id == 'surface.mep':
            out = MS.compute_surface_mep(
                molecule, basis=basis,
                isovalue=float(params.get('isovalue', MS.DEFAULT_ISOVALUE)),
                points_per_atom=int(params.get('points_per_atom', 120)),
                max_seconds=float(budget), xc=params.get('xc'),
                use_gpu=params.get('use_gpu', 'auto'))
            points = np.ascontiguousarray(out['points'], dtype='<f4')
            values = np.ascontiguousarray(out['values'], dtype='<f4')
            meta = out['meta']
            result = {'summary': {'n_points': int(len(values)),
                                  'extrema': out['extrema'], 'meta': meta}}
            artifacts = [('surface.points', points.tobytes()),
                         ('surface.values', values.tobytes())]
        elif ctx.method_id == 'surface.mep_at':
            points = np.asarray(payload['points'], dtype=float)
            values, meta = MS.mep_at_points(
                molecule, points, basis=basis, max_seconds=float(budget))
            values = np.ascontiguousarray(values, dtype='<f4')
            result = {'summary': {'n_points': int(len(values)),
                                  'min': float(values.min()),
                                  'max': float(values.max()), 'meta': meta}}
            artifacts = [('surface.values', values.tobytes())]
        else:                                                       # pragma: no cover
            raise failures.DiracInternal(f'unknown surface method {ctx.method_id}')
    except Exception as exc:                                       # noqa: BLE001
        _raise_physics_failure(exc)
    return HandlerResult(
        result=result, artifacts=artifacts,
        provenance={'n_atoms': meta.get('n_atoms'),
                    'charge': meta.get('charge'), 'spin': meta.get('spin'),
                    'scf_seconds': meta.get('scf_seconds')},
        parameters_used={'basis': basis, 'max_seconds': float(budget)},
        cache='computed')


def surface_estimate(payload: dict) -> dict:
    from physics import mep_surface as MS
    try:
        basis = (payload.get('parameters') or {}).get('basis', MS.DEFAULT_BASIS)
        nao = MS.nao_for(payload['molecule']['content'], basis)
        seconds = MS.estimated_scf_seconds(nao)
        return {'available': True, 'n_basis_functions_estimated': nao,
                'seconds': round(seconds, 2),
                'confidence': 'same fitted SCF cost model used by the runtime preflight'}
    except Exception as exc:                                       # noqa: BLE001
        return {'available': False, 'reason': f'{type(exc).__name__}: {exc}'}


def torsion_handler(payload: dict, ctx: InvocationContext) -> HandlerResult:
    from physics.torsion import compute_torsion_strain
    params = dict(payload.get('parameters') or {})
    try:
        out = compute_torsion_strain(
            payload['molecule']['content'], steps=int(params.get('steps', 24)),
            relax_hydrogens=bool(params.get('relax_hydrogens', True)),
            max_torsions=int(params.get('max_torsions', 12)),
            variant=params.get('variant', 'MMFF94s'))
    except Exception as exc:                                       # noqa: BLE001
        _raise_physics_failure(exc)
    profile = json.dumps(out, sort_keys=True, separators=(',', ':')).encode()
    meta = out.get('meta') or {}
    return HandlerResult(
        result={'summary': {'total_strain_kcal': out['total_strain_kcal'],
                            'total_verdict': out['total_verdict'],
                            'n_scanned': meta.get('n_scanned'), 'meta': meta}},
        artifacts=[('torsion.profile', profile)],
        provenance={'n_atoms': None},
        parameters_used={'steps': int(params.get('steps', 24)),
                         'relax_hydrogens': bool(params.get('relax_hydrogens', True)),
                         'max_torsions': int(params.get('max_torsions', 12)),
                         'variant': params.get('variant', 'MMFF94s')},
        cache='computed')


def torsion_estimate(payload: dict) -> dict:
    return {'available': False,
            'reason': 'cost depends on the number of rotatable bonds and MMFF convergence'}


def _raise_physics_failure(exc: Exception) -> None:
    """Translate producer-native refusals once, at the science boundary."""
    from physics.mep_surface import PhysicsBudgetExceeded
    if isinstance(exc, failures.DiracFailure):
        raise exc
    message = str(exc)
    lower = message.lower()
    if isinstance(exc, PhysicsBudgetExceeded) or 'budget' in lower or 'predicted' in lower:
        raise failures.DiracBudgetExceeded(message) from exc
    if 'did not converge' in lower:
        raise failures.DiracUnconverged(message) from exc
    if 'parse' in lower or 'molfile' in lower and 'no 3d' not in lower:
        raise failures.DiracParseFailure(message) from exc
    if 'exceeds' in lower and 'atoms' in lower:
        raise failures.DiracTooLarge(message) from exc
    if 'type' in lower or 'force field' in lower:
        raise failures.DiracUnparameterized(message) from exc
    raise failures.DiracUnsupported(message) from exc
