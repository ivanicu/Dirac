#!/usr/bin/env python3
"""THE ACCEPTANCE TEST the external audit named, run for as many transports as exist.

    "the same fields.qm.homo invocation via core, HTTP v2, Python SDK and CLI --json
     must yield the same method version, the same scientific result, the same artifact
     SHA-256 and the same typed provenance"

Today two of those four exist (core and the v1 HTTP route), so this file compares the
two that exist and reports the others as ABSENT — never as passing. A parity test that
silently skips the transports it cannot reach would go green the day the CLI lands
broken.

WHAT IS COMPARED, and what is not:
    artifact SHA-256   the whole point. Same bytes or the transports are not the same
                       instrument, whatever their JSON looks like.
    scientific values  energy, HOMO/LUMO, n_basis, grid dimensions, extrema
    method version     which SOURCE ran
Timings are excluded and listed as excluded. Everything else is compared, and a field
present on one side and absent on the other is a DIFFERENCE, not a skip — that
asymmetry is how "the two paths agree" survives one of them returning almost nothing.

Usage:  backend/env/bin/python scripts/acceptance_parity.py [--json]
Exit:   0 the transports that exist agree · 1 a real difference · 2 could not run
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))

FIXTURE = ROOT / 'contracts' / 'golden' / 'fixtures' / 'o_3.mol'
BASE = 'http://127.0.0.1:8901'
METHOD = 'fields.qm.homo'
BASIS = 'sto-3g'

EXCLUDED = {'seconds', 'scf_seconds', 'cube_seconds', 'total_seconds',
            'cube_predicted_seconds', 'request_id', 'job_id', 'computed_at', 'cache',
            'stored'}


def molfile_with_nonce() -> str:
    """A geometry nothing has computed before, so BOTH paths are cold.

    Measured earlier in this project: the cube bytes are invariant to the molfile's
    TITLE line, while the cache key is not. So a nonce in the title buys two genuinely
    cold computations of identical science — which is what makes a byte-identical
    result meaningful rather than a shared cache row.
    """
    lines = FIXTURE.read_text().split('\n')
    lines[0] = 'acceptance-parity-' + os.urandom(6).hex()
    return '\n'.join(lines)


def via_core(molfile: str) -> dict:
    """Leg 1: the InvocationService, in process. No HTTP, no socket."""
    import artifacts
    import catalog
    import invocation
    cat = catalog.MethodCatalog.load()
    # The version comes from the REGISTRY, computed over the running source — the
    # same call the daemon makes, not a re-derivation. My first attempt guessed the
    # API (`u.version` on a dataclass) and got {} silently, which made `version` None
    # on both legs and turned this field into agreement-by-mutual-absence.
    import field_server as FS
    import method_registry as MR
    versions = {mid: MR.unit_version(FS, u['fns'], u['consts'])[0]
                for mid, u in MR.UNITS.items()}
    cat = cat.bind_versions(versions)
    svc = invocation.InvocationService(cat, store=artifacts.MemoryArtifactStore())
    t0 = time.time()
    env = svc.invoke(METHOD,
                     {'molecule': {'kind': 'molfile', 'content': molfile,
                                   'dimensionality': 3},
                      'parameters': {'basis': BASIS}},
                     inline_max=0)          # 0 = never inline, so we compare digests
    env['_wall'] = round(time.time() - t0, 2)
    return env


def via_http_v1(molfile: str) -> dict:
    """Leg 2: the live daemon's v1 /field route — today's production path."""
    req = urllib.request.Request(
        BASE + '/field',
        data=json.dumps({'molfile': molfile, 'kind': 'homo', 'basis': BASIS}).encode(),
        headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as fh:
        body = json.load(fh)
    body['_wall'] = round(time.time() - t0, 2)
    return body


def normalise_core(env: dict) -> dict:
    """The comparable facts, from the v2 envelope."""
    if not env.get('ok'):
        return {'ok': False, 'error_code': (env.get('error') or {}).get('code')}
    d = env['data']
    wf = d.get('wavefunction', {})
    arts = env.get('artifacts') or []
    cube = arts[0] if arts else {}
    return {
        'ok': True,
        'version': env['meta'].get('version'),
        'cube_sha256': cube.get('sha256'),
        'cube_bytes': cube.get('size_bytes'),
        'media_type': cube.get('media_type'),
        'grid_dimensions': d['field']['grid']['dimensions'],
        'spacing_angstrom': round(d['field']['grid']['spacing_angstrom'], 6),
        'extrema_min': round(d['field']['extrema']['min'], 10),
        'extrema_max': round(d['field']['extrema']['max'], 10),
        'converged': wf.get('converged'),
        'scf_method': wf.get('method'),
        'basis': wf.get('basis'),
        'n_basis_functions': wf.get('n_basis_functions'),
        'energy_hartree': round(wf['energy_hartree'], 9) if wf.get('energy_hartree') else None,
        'homo_ev': round(wf['homo_ev'], 9) if wf.get('homo_ev') is not None else None,
        'lumo_ev': round(wf['lumo_ev'], 9) if wf.get('lumo_ev') is not None else None,
        'n_atoms': (env['meta'].get('provenance') or {}).get('n_atoms'),
        'charge': (env['meta'].get('provenance') or {}).get('charge'),
        'spin': (env['meta'].get('provenance') or {}).get('spin'),
    }


def normalise_http(body: dict) -> dict:
    """The same facts, from the v1 response — which carries the cube INLINE, so its
    digest is computed here over exactly the bytes it shipped."""
    if not body.get('ok'):
        return {'ok': False, 'error_code': (body.get('error') or {}).get('code')
                if isinstance(body.get('error'), dict) else body.get('reason')}
    cube = body['cube']
    m = body.get('meta') or {}
    import handlers
    grid = handlers.parse_cube_header(cube)
    vmin, vmax = handlers.cube_extrema(cube)
    return {
        'ok': True,
        'version': m.get('method_version'),
        'cube_sha256': hashlib.sha256(cube.encode()).hexdigest(),
        'cube_bytes': len(cube.encode()),
        'media_type': 'application/vnd.dirac.gaussian-cube',
        'grid_dimensions': grid['dimensions'],
        'spacing_angstrom': round(grid['spacing_angstrom'], 6),
        'extrema_min': round(vmin, 10),
        'extrema_max': round(vmax, 10),
        'converged': m.get('converged'),
        'scf_method': m.get('method'),
        'basis': m.get('basis'),
        'n_basis_functions': m.get('nbasis'),
        'energy_hartree': round(m['scf_energy_ha'], 9) if m.get('scf_energy_ha') else None,
        'homo_ev': round(m['homo_ev'], 9) if m.get('homo_ev') is not None else None,
        'lumo_ev': round(m['lumo_ev'], 9) if m.get('lumo_ev') is not None else None,
        'n_atoms': m.get('natoms'),
        'charge': m.get('charge'),
        'spin': m.get('spin'),
    }


ABSENT_LEGS = {
    'python_sdk': 'PR-09 has not landed; there is no SDK to compare',
    'cli_json': 'PR-10/11 have not landed; there is no CLI to compare',
    'http_v2': 'PR-07 has not landed; /v2/invoke does not exist, so the v1 route is '
               'compared instead and the v2 leg is ABSENT',
    'mcp': 'PR-14 has not landed',
}


def main() -> int:
    molfile = molfile_with_nonce()
    try:
        with urllib.request.urlopen(BASE + '/health', timeout=5):
            pass
    except Exception as e:                                          # noqa: BLE001
        print(f'acceptance_parity: no daemon on 8901 ({e}). The HTTP leg cannot run, '
              f'and a single-leg parity test proves nothing.', file=sys.stderr)
        return 2

    print(f'method    {METHOD}   basis {BASIS}')
    print(f'molecule  water, title-nonced so BOTH legs compute cold')
    print()
    core_env = via_core(molfile)
    http_body = via_http_v1(molfile)
    core = normalise_core(core_env)
    http = normalise_http(http_body)

    keys = sorted(set(core) | set(http))
    width = max(len(k) for k in keys)
    diffs = []
    for k in keys:
        a, b = core.get(k, '<ABSENT>'), http.get(k, '<ABSENT>')
        same = a == b
        if not same:
            diffs.append((k, a, b))
        mark = '  ' if same else '≠≠'
        sa = str(a)[:34]
        sb = str(b)[:34]
        print(f'  {mark} {k:<{width}}  core={sa:<36} http={sb}')
    print()
    print(f'  core wall {core_env.get("_wall")}s · http wall {http_body.get("_wall")}s '
          f'(excluded from the comparison)')
    print()
    for leg, why in sorted(ABSENT_LEGS.items()):
        print(f'  ABSENT  {leg:<12} {why}')
    # ── the guard that stops this test agreeing about nothing ────────────────
    #
    # Both legs returning None for `version` is not parity — it is a shared blind
    # spot, and it read as a PASS on the one field the audit named first. Any field
    # in this set that is None or absent on BOTH sides is a HOLE, reported as a
    # failure of the test rather than a success of the system.
    MUST_BE_PRESENT = ('version', 'cube_sha256', 'n_basis_functions',
                       'energy_hartree', 'grid_dimensions', 'extrema_min')
    holes = [k for k in MUST_BE_PRESENT
             if core.get(k) in (None, '<ABSENT>') and http.get(k) in (None, '<ABSENT>')]
    print('─' * 100)
    if holes:
        print(f'VACUOUS AGREEMENT — {len(holes)} field(s) are absent on BOTH legs, so '
              f'they agree about nothing: {holes}')
        print('Each of these is a fact the acceptance test exists to compare. A test '
              'that scores them as equal-because-both-empty is a check that cannot '
              'fail.')
        return 1
    if diffs:
        print(f'PARITY FAILED — {len(diffs)} difference(s) between core and HTTP:')
        for k, a, b in diffs:
            print(f'  {k}: core={a!r} http={b!r}')
        print('\nTwo transports that disagree are two instruments, and the descriptor '
              'is only telling the truth about one of them.')
        return 1
    print(f'PARITY: core and HTTP agree on all {len(keys)} compared facts, including '
          f'the artifact SHA-256.')
    print(f'  cube sha256 {core["cube_sha256"]}')
    print(f'  {core["cube_bytes"]:,} bytes · {core["n_basis_functions"]} basis functions '
          f'· E = {core["energy_hartree"]} Ha')
    print(f'{len(ABSENT_LEGS)} further leg(s) ABSENT and reported as such, never as '
          f'passing.')
    if '--json' in sys.argv:
        print(json.dumps({'core': core, 'http': http, 'absent': ABSENT_LEGS}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
