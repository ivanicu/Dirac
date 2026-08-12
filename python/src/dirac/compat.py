"""The v1 shim. Isolated in ONE file so its deletion is a single, verifiable event.

The daemon today speaks `/field`, not `/v2/invoke` (PR-07 is unlanded). The SDK could
have waited for that, and then nothing would exercise the SDK until two more PRs land —
which is how an SDK ships untested. Instead it speaks v1 HERE, and nowhere else.

WHY THIS IS A COMPAT LAYER AND NOT AN ARCHITECTURE: it does the one thing the audit says
a transport must never do — it knows about method-specific route shapes. `fields.qm.homo`
becomes `{kind: 'homo'}` on `/field`, and that mapping is knowledge the kernel already
holds. Keeping it in a file named `compat` with this paragraph at the top is the
difference between a temporary bridge and a permanent second authority.

DELETION CRITERION, so this does not quietly become load-bearing: when `/v2/invoke`
answers on the daemon, HttpTransport.invoke stops calling `_invoke_via_v1`, this file is
deleted, and scripts/acceptance_parity.py is what proves the deletion changed nothing —
the SDK leg must produce the same digest, version and provenance before and after.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

from . import errors

# Every v1 `kind`, derived from the descriptors at call time rather than hard-coded, so a
# method added to the catalog does not need an edit here. Falls back to the last dotted
# segment, which is true for every current fields.* method and is stated as an assumption
# rather than relied on silently.
def _kind_for(method_id: str) -> str:
    return method_id.split('.')[-1]


def parse_cube_header_text(cube: str) -> dict:
    """Grid geometry from a Gaussian cube's first six lines. Pure stdlib.

    Duplicated in spirit with backend/handlers.parse_cube_header and NOT imported from
    it: that module imports numpy and RDKit, and this client must run on a bare
    interpreter. The duplication is a real cost and is bounded by the cube FORMAT being
    fixed for thirty years — the thing this parses cannot drift, which is the only
    condition under which two homes for one rule is acceptable.
    """
    lines = cube.split('\n', 6)
    if len(lines) < 6:
        return {}
    BOHR = 0.529177210903
    try:
        axes = [lines[3].split(), lines[4].split(), lines[5].split()]
        dims = [int(float(a[0])) for a in axes]
        step = (sum(float(x) ** 2 for x in axes[0][1:4]) ** 0.5) * BOHR
    except (ValueError, IndexError):
        return {}
    return {'dimensions': dims, 'spacing_angstrom': round(step, 6)}


def _contracts_dir() -> pathlib.Path | None:
    for parent in pathlib.Path(__file__).resolve().parents:
        d = parent / 'contracts' / 'methods'
        if d.is_dir():
            return d
    return None


def list_methods_from_descriptors() -> list[dict]:
    """The catalog, read locally, when the server has no /v2/methods.

    Reading the LOCAL descriptors to describe a REMOTE server is a real limitation and is
    reported as one: the `source` field says where this came from, so a caller cannot
    mistake it for the server's own answer. Without that field this function would be
    quietly asserting that a remote host runs the same code as this checkout.
    """
    d = _contracts_dir()
    if d is None:
        return []
    out = []
    for f in sorted(d.glob('*.method.json')):
        desc = json.loads(f.read_text())
        out.append({'method_id': desc['method_id'], 'summary': desc.get('summary', ''),
                    'executable': bool(desc.get('invocation')),
                    'source': 'local descriptors (this server has no /v2/methods, so '
                              'this list describes THIS checkout, not the remote host)'})
    return out


def describe_from_descriptors(method_id: str) -> dict:
    d = _contracts_dir()
    if d is None:
        raise errors.DiracError(f'no contracts directory found; cannot describe '
                                f'{method_id}')
    f = d / f'{method_id}.method.json'
    if not f.is_file():
        raise errors.exception_for('NOT_FOUND')(
            f'no descriptor for {method_id!r} in {d}',
            details={'method_id': method_id})
    desc = json.loads(f.read_text())
    return {'method_id': desc['method_id'], 'summary': desc.get('summary', ''),
            'description': desc.get('description', ''),
            'input_schema': (desc.get('input') or {}).get('schema') or {},
            'output_schema': (desc.get('output') or {}).get('schema') or {},
            'refusals': desc.get('refusals') or [],
            'execution': desc.get('execution') or {},
            'source': 'local descriptors'}


def invoke_via_v1(transport, method_id: str, payload: dict, **kw) -> dict:
    """Call `/field` and shape the answer as a v2 envelope.

    THE HARD PART is not the request; it is that v1 returns the cube INLINE as a JSON
    string with no digest, and the v2 contract promises an artifact reference. So the
    digest is computed HERE, over the bytes that actually arrived, and the reference is
    marked `inline: true` with `synthesised_by: 'v1 compat'` — a caller must be able to
    tell a reference the SERVER minted from one this client assembled, because only the
    first one can be fetched again later.
    """
    mol = (payload.get('molecule') or {})
    params = dict(payload.get('parameters') or {})
    body: dict[str, Any] = {'molfile': mol.get('content'), 'kind': _kind_for(method_id)}
    if params.get('basis'):
        body['basis'] = params['basis']
    if params.get('spin') is not None:
        body['spin'] = params['spin']
    if kw.get('budget_seconds') is not None:
        body['max_seconds'] = kw['budget_seconds']

    status, _headers, raw = transport._request('POST', '/field', body)
    v1 = json.loads(raw or b'{}')
    if not v1.get('ok'):
        # v1's refusal carries `reason` (a coarse word) and, since PR-03, no code. The
        # code is NOT reconstructed from the reason string — that would be exactly the
        # guess the typed vocabulary exists to delete. UNSUPPORTED is used only when v1
        # itself said `unsupported`, and anything else becomes INTERNAL with the raw
        # reason preserved in details for whoever reads the ledger.
        reason = str(v1.get('reason') or '')
        code = {'unsupported': 'UNSUPPORTED', 'parse': 'PARSE',
                'budget': 'BUDGET', 'too_large': 'TOO_LARGE'}.get(reason, 'INTERNAL')
        return {'ok': False,
                'error': {'code': code,
                          'message': v1.get('error') or 'v1 refused without a message',
                          'retryable': code in ('BUDGET', 'INTERNAL'),
                          'details': {'v1_reason': reason, 'http_status': status,
                                      'code_is_mapped_from_v1_reason': True}},
                'meta': {'envelope': 2, 'method_id': method_id,
                         'transport': 'http:/field (v1 compat)'}}

    cube = v1.get('cube') or ''
    data = cube.encode()
    meta = v1.get('meta') or {}
    import base64
    ref = {
        'id': None,
        'sha256': hashlib.sha256(data).hexdigest(),
        'role': 'field.cube',
        'media_type': 'application/vnd.dirac.gaussian-cube',
        'size_bytes': len(data),
        'encoding': 'identity',
        'inline': True,
        'inline_base64': base64.b64encode(data).decode('ascii'),
        # THE HONEST FLAG. A server-minted reference can be re-fetched by digest; this
        # one cannot, because v1 never stored an artifact row for it. A client that
        # cached this ref and tried to GET it later would 404, and it deserves to know
        # why from the object rather than from the failure.
        'synthesised_by': 'v1 compat: the daemon returned the bytes inline with no '
                          'artifact row, so this reference is not fetchable by digest',
    }
    grid: dict[str, Any] = {}
    if meta.get('dims'):
        grid['dimensions'] = meta['dims']
        if meta.get('spacing'):
            grid['spacing_angstrom'] = meta['spacing']
    else:
        # v1 reports dims/spacing for the CLASSICAL kinds and NOTHING for the quantum
        # ones, so this used to print `grid None @ None Å`. The cube's own header carries
        # both exactly, in six lines of text — the artifact is the object, and the meta
        # dict is a description of it that happens to be incomplete here.
        grid = parse_cube_header_text(cube)
    result: dict[str, Any] = {
        'field': {'kind': _kind_for(method_id), 'grid': grid},
    }
    if meta.get('vmin') is not None:
        result['field']['extrema'] = {'min': meta['vmin'], 'max': meta['vmax']}
    # NO extrema for the quantum kinds, and absent rather than invented: computing them
    # means a pass over ~840k floats, which a stdlib client must not do on every call,
    # and a zero would be a fabrication. /v2/invoke reports them from the kernel.

    if meta.get('converged') is not None:
        result['wavefunction'] = {
            'converged': meta.get('converged'), 'method': meta.get('method'),
            'basis': meta.get('basis'), 'n_basis_functions': meta.get('nbasis'),
            'energy_hartree': meta.get('scf_energy_ha'),
            'homo_ev': meta.get('homo_ev'), 'lumo_ev': meta.get('lumo_ev'),
            'scf_cycles': meta.get('scf_cycles')}
    return {
        'ok': True, 'data': result, 'artifacts': [ref], 'warnings': [],
        'meta': {'envelope': 2, 'method_id': method_id,
                 'version': meta.get('method_version'),
                 'cache': meta.get('cache'), 'seconds': meta.get('total_seconds'),
                 'transport': 'http:/field (v1 compat)',
                 'toolkit_wrote_at': meta.get('toolkit_wrote_at'),
                 'provenance': {'n_atoms': meta.get('natoms'),
                                'charge': meta.get('charge'),
                                'spin': meta.get('spin'),
                                'scf_seconds': meta.get('scf_seconds'),
                                'cube_seconds': meta.get('cube_seconds'),
                                'ecp': meta.get('ecp') or []},
                 'v1_meta': meta},
    }
