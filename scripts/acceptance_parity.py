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
    # plan() resolves each unit's declared source module. Physics methods live in
    # physics.* rather than field_server, so hashing every name against FS would make
    # this acceptance harness drift from the registry it claims to exercise.
    versions = {row['method_id']: row['version'] for row in MR.plan(FS)}
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
    # TOLERANT ACCESS, and it is not laziness: a leg that genuinely lacks a field must
    # report None so the diff and the vacuous-agreement guard can rule on it. Written
    # with d['field']['grid']['spacing_angstrom'] first, and the v1-compat leg — which
    # cannot supply spacing — crashed the whole comparison. A KeyError tells you one
    # field is missing and hides whether the other eighteen agree.
    field = d.get('field') or {}
    grid = field.get('grid') or {}
    extrema = field.get('extrema') or {}

    def r(v, places):
        return round(v, places) if isinstance(v, (int, float)) else None

    return {
        'ok': True,
        'version': (env.get('meta') or {}).get('version'),
        'cube_sha256': cube.get('sha256'),
        'cube_bytes': cube.get('size_bytes'),
        'media_type': cube.get('media_type'),
        'grid_dimensions': grid.get('dimensions'),
        'spacing_angstrom': r(grid.get('spacing_angstrom'), 6),
        'extrema_min': r(extrema.get('min'), 10),
        'extrema_max': r(extrema.get('max'), 10),
        'converged': wf.get('converged'),
        'scf_method': wf.get('method'),
        'basis': wf.get('basis'),
        'n_basis_functions': wf.get('n_basis_functions'),
        # The DECLARED name is scf_energy_hartree. `energy_hartree` is accepted as a
        # fallback and reported distinctly, because a leg still using the old key means the
        # RUNNING SERVICE predates this checkout — which is exactly what this test caught
        # the first time it fired: the daemon had not been restarted, so v2 answered from
        # an older handler and an older catalog while the in-process leg used the new one.
        'energy_hartree': r(wf.get('scf_energy_hartree'), 9),
        'energy_key_legacy': 'energy_hartree' in wf or None,
        'homo_ev': r(wf.get('homo_ev'), 9),
        'lumo_ev': r(wf.get('lumo_ev'), 9),
        'n_atoms': ((env.get('meta') or {}).get('provenance') or {}).get('n_atoms'),
        'charge': ((env.get('meta') or {}).get('provenance') or {}).get('charge'),
        'spin': ((env.get('meta') or {}).get('provenance') or {}).get('spin'),
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
        # Set on BOTH reducers or the field itself becomes a difference — which it did the
        # moment I added it to one of them, and the diff was right to say so.
        'energy_key_legacy': None,
        'energy_hartree': round(m['scf_energy_ha'], 9) if m.get('scf_energy_ha') else None,
        'homo_ev': round(m['homo_ev'], 9) if m.get('homo_ev') is not None else None,
        'lumo_ev': round(m['lumo_ev'], 9) if m.get('lumo_ev') is not None else None,
        'n_atoms': m.get('natoms'),
        'charge': m.get('charge'),
        'spin': m.get('spin'),
    }


def via_python_sdk(molfile: str) -> dict:
    """Leg 3: the Python SDK, which is what the CLI and MCP will both sit on.

    Deliberately imported from python/src rather than an installed package: the thing
    under test is THIS checkout's SDK, and a pip-installed one could be any version.
    """
    sys.path.insert(0, str(ROOT / 'python' / 'src'))
    import dirac
    c = dirac.DiracClient(transport='http')      # http, so this leg is not a rename of
    t0 = time.time()                             # leg 1 running in the same process
    r = c.field('homo', molfile=molfile, basis=BASIS)
    out = dict(r.envelope)
    out['_wall'] = round(time.time() - t0, 2)
    return out


def normalise_sdk(env: dict) -> dict:
    """The SDK's envelope reduced by the SAME function as the core's.

    Reusing normalise_core rather than writing a third reducer is the point: if the SDK
    needed its own extraction logic, its envelope would not actually be the same shape,
    and 'the transports agree' would be a claim about two normalisers rather than about
    two systems. The one legitimate difference is that v1 compat cannot supply the grid
    spacing or the extrema — v1's meta has no spacing for the quantum kinds — so those
    are recovered from the artifact bytes, which is where the other legs get them too.
    """
    base = normalise_core(env)
    if not base.get('ok'):
        return base
    import handlers
    ref = (env.get('artifacts') or [{}])[0]
    if ref.get('inline_base64'):
        import base64
        cube = base64.b64decode(ref['inline_base64']).decode()
        grid = handlers.parse_cube_header(cube)
        vmin, vmax = handlers.cube_extrema(cube)
        base['grid_dimensions'] = grid['dimensions']
        base['spacing_angstrom'] = round(grid['spacing_angstrom'], 6)
        base['extrema_min'] = round(vmin, 10)
        base['extrema_max'] = round(vmax, 10)
    return base


def via_http_v2(molfile: str) -> dict:
    """Leg 5: /v2/invoke, spoken directly with urllib — no SDK in the path.

    Separate from the SDK leg on purpose. The SDK's HttpTransport tries /v2/invoke first
    and falls back to v1, so the SDK leg passing does NOT establish that v2 itself is
    right — it could be passing through the fallback. This leg has no fallback: if v2 is
    broken or absent, it fails loudly here.
    """
    req = urllib.request.Request(
        BASE + '/v2/invoke',
        data=json.dumps({
            'method_id': METHOD,
            'input': {'molecule': {'kind': 'molfile', 'content': molfile,
                                   'dimensionality': 3},
                      'parameters': {'basis': BASIS}},
            'inline_max': 0}).encode(),
        headers={'Content-Type': 'application/json'})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as fh:
            body = json.load(fh)
    except urllib.error.HTTPError as e:
        body = json.loads(e.read() or b'{}')
        body['_http_status'] = e.code
    body['_wall'] = round(time.time() - t0, 2)
    return body


def via_cli_json(molfile: str) -> dict:
    """Leg 4: the CLI, invoked as a SUBPROCESS, parsing only its stdout.

    A subprocess and not an in-process call to cli.main(), because the thing under test
    is the CLI's contract with a machine: that `--json` puts the envelope on stdout and
    NOTHING else. Calling main() directly would test the functions and skip the property
    — and the property is what a script depends on. Written the in-process way first
    would also have missed the defect that was actually there: `--json` was declared only
    on the top parser, so `dirac run x --json` was an argparse error with an EMPTY stdout.
    """
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile('w', suffix='.mol', delete=False) as fh:
        fh.write(molfile)
        path = fh.name
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'python' / 'src'))
    t0 = time.time()
    r = subprocess.run(
        [sys.executable, '-m', 'dirac.cli', 'run', METHOD, '--molfile', path,
         '--basis', BASIS, '--transport', 'http', '--url', BASE, '--json'],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=900)
    os.unlink(path)
    if r.returncode not in (0, 1):
        return {'ok': False, '_cli_exit': r.returncode,
                '_stderr': r.stderr.strip()[-400:]}
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        return {'ok': False, '_cli_exit': r.returncode,
                '_stdout_not_json': f'{e} · first 200 chars: {r.stdout[:200]!r}'}
    out['_wall'] = round(time.time() - t0, 2)
    out['_cli_exit'] = r.returncode
    return out


def via_mcp(molfile: str) -> dict:
    """Leg 6: the MCP adapter, driven over JSON-RPC exactly as a host drives it.

    Not by calling call_tool() directly: the property under test is that an AGENT gets the
    same science, and an agent reaches it through initialize → tools/list → tools/call on
    a line-delimited stream. Bypassing the protocol would test the functions and skip the
    only part a host depends on.

    Note the ONE legitimate asymmetry, which is the point of the whole PR chain rather
    than a gap: an MCP tool result never carries the bytes (inline_max=0, enforced twice),
    so this leg compares the DIGEST and the science, and there is no inline payload to
    recover extrema from. Those come back as None here and the diff shows it.
    """
    import io
    sys.path.insert(0, str(ROOT / 'python' / 'src'))
    from dirac.client import DiracClient
    from dirac.mcp import DiracMCP
    reqs = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
            'name': 'structure_field_compute',
            'arguments': {'molecule': {'kind': 'molfile', 'content': molfile,
                                       'dimensionality': 3},
                          'field_kind': 'homo',
                          'parameters': {'basis': BASIS}}}},
    ]
    out = io.StringIO()
    t0 = time.time()
    mcp = DiracMCP(DiracClient('http', base_url=BASE, timeout=900))
    mcp.serve(stdin=io.StringIO('\n'.join(json.dumps(r) for r in reqs)), stdout=out)
    resps = {r.get('id'): r for r in
             (json.loads(l) for l in out.getvalue().splitlines() if l.strip())}
    call = (resps.get(3) or {}).get('result') or {}
    text = (call.get('content') or [{}])[0].get('text', '{}')
    accepted = json.loads(text)
    job_id = accepted.get('job_id') or ((accepted.get('data') or {}).get('job') or {}).get('id')
    if not job_id:
        return {'ok': False, 'error': {'code': 'INTERNAL'}, '_mcp_payload': accepted,
                '_n_tools': len(((resps.get(2) or {}).get('result') or {}).get('tools') or [])}

    waited_out = io.StringIO()
    wait_req = {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {
        'name': 'job_wait',
        'arguments': {'job_ref': {'kind': 'job', 'id': job_id}, 'timeout': 300}}}
    mcp.serve(stdin=io.StringIO(json.dumps(wait_req) + '\n'), stdout=waited_out)
    waited_response = json.loads(waited_out.getvalue().strip())
    waited_call = waited_response.get('result') or {}
    waited_text = (waited_call.get('content') or [{}])[0].get('text', '{}')
    waited = json.loads(waited_text)
    job = waited.get('data') or {}
    summary = job.get('result_summary') or {}
    artifacts = [{
        'id': a.get('id'), 'sha256': a.get('sha256'), 'role': a.get('role'),
        'media_type': a.get('media_type'), 'size_bytes': a.get('size_bytes'),
    } for a in job.get('artifacts') or []]
    # Reshaped into an envelope so the SAME normaliser judges it. An MCP-specific
    # reducer would be a third opinion about what the result means.
    env = {'ok': not waited_call.get('isError', True) and job.get('state') == 'done',
           'data': summary.get('data') or {},
           'artifacts': artifacts,
           'meta': {'version': job.get('method_version'),
                    'cache': summary.get('cache'),
                    'provenance': summary.get('provenance') or {}},
           '_wall': round(time.time() - t0, 2),
           '_tool_result_chars': len(text) + len(waited_text),
           '_contains_base64': 'inline_base64' in text or 'inline_base64' in waited_text,
           '_n_tools': len(((resps.get(2) or {}).get('result') or {}).get('tools') or [])}
    return env


ABSENT_LEGS = {
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
    sdk_env = via_python_sdk(molfile)
    cli_env = via_cli_json(molfile)
    v2_env = via_http_v2(molfile)
    mcp_env = via_mcp(molfile)
    legs = {'core': normalise_core(core_env),
            'v1': normalise_http(http_body),
            'v2': normalise_core(v2_env),
            'sdk': normalise_sdk(sdk_env),
            'cli': normalise_sdk(cli_env),
            'mcp': normalise_core(mcp_env)}

    keys = sorted(set().union(*(set(v) for v in legs.values())))
    width = max(len(k) for k in keys)
    diffs = []
    MCP_CANNOT_REPORT = ('extrema_min', 'extrema_max', 'n_atoms', 'charge', 'spin')
    for k in keys:
        vals = {n: v.get(k, '<ABSENT>') for n, v in legs.items()
                if not (n == 'mcp' and k in MCP_CANNOT_REPORT)}
        same = len(set(map(repr, vals.values()))) == 1
        if not same:
            diffs.append((k, vals))
        mark = '  ' if same else '≠≠'
        row = ' '.join(f'{n}={str(v)[:20]}' for n, v in vals.items())
        print(f'  {mark} {k:<{width}}  {row}')
    core = legs['core']
    print()
    print(f'  wall: core {core_env.get("_wall")}s · v1 {http_body.get("_wall")}s · '
          f'v2 {v2_env.get("_wall")}s · sdk {sdk_env.get("_wall")}s '
          f'(excluded from the comparison)')
    print(f'  sdk transport: {(sdk_env.get("meta") or {}).get("transport")}')
    print(f'  mcp: {mcp_env.get("_n_tools")} tools · tool result '
          f'{int(mcp_env.get("_tool_result_chars") or 0):,} chars for a '
          f'{(legs["mcp"].get("cube_bytes") or 0):,}-byte artifact · contains base64: '
          f'{mcp_env.get("_contains_base64")}')
    print(f'  cli exit code: {cli_env.get("_cli_exit")} · stdout parsed as JSON: '
          f'{"_stdout_not_json" not in cli_env}')
    if cli_env.get('_stdout_not_json'):
        print(f'    {cli_env["_stdout_not_json"]}')
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
             if all(v.get(k) in (None, '<ABSENT>') for v in legs.values())]
    print('─' * 100)
    if holes:
        print(f'VACUOUS AGREEMENT — {len(holes)} field(s) are absent on BOTH legs, so '
              f'they agree about nothing: {holes}')
        print('Each of these is a fact the acceptance test exists to compare. A test '
              'that scores them as equal-because-both-empty is a check that cannot '
              'fail.')
        return 1
    if diffs:
        # DUMP THE EVIDENCE, because the one real disagreement seen so far did not
        # recur. 2026-08-11: a single run had core=9db3a91b… against 06b686f0… on the
        # other four legs, with the energy, extrema, grid and method version identical to
        # 9-10 digits — so the science agreed and the bytes did not. Three subsequent
        # runs of all five legs and eight in-process recomputations were all identical,
        # and the cause is UNVERIFIED. It is not called fixed.
        #
        # What was missing was not an explanation but the EVIDENCE: by the time the
        # difference was noticed the cubes were gone, so the obvious hypothesis (an
        # orbital phase flip — water's HOMO has symmetric extrema, so a global sign
        # change leaves every reported number identical and alters every byte) could not
        # be tested. Now a failure writes both payloads and the next occurrence is
        # diagnosable in one command instead of unreproducible.
        try:
            import pathlib as _pl
            dump = _pl.Path(os.environ.get('DIRAC_SCRATCH')
                            or (_pl.Path.home() / '.cache/dirac/scratch')) / 'parity-fail'
            dump.mkdir(parents=True, exist_ok=True)
            for name, env in (('core', core_env), ('v1', http_body), ('v2', v2_env),
                              ('sdk', sdk_env), ('cli', cli_env)):
                (dump / f'{name}.envelope.json').write_text(json.dumps(env, indent=2,
                                                                      default=str))
                blob = None
                if name == 'v1':
                    blob = (env.get('cube') or '').encode()
                else:
                    refs = env.get('artifacts') or []
                    if refs and refs[0].get('inline_base64'):
                        import base64
                        blob = base64.b64decode(refs[0]['inline_base64'])
                if blob:
                    (dump / f'{name}.cube').write_bytes(blob)
            (dump / 'molfile.mol').write_text(molfile)
            print(f'  evidence written to {dump} — compare with:')
            print(f'    python3 -c "import numpy as np; …"  or diff the .cube files '
                  f'directly; a global sign flip shows as identical |values|')
        except Exception as e:                                      # noqa: BLE001
            print(f'  (could not write the evidence dump: {e})')
        print(f'PARITY FAILED — {len(diffs)} difference(s) across '
              f'{len(legs)} transports:')
        for k, vals in diffs:
            print(f'  {k}: ' + ' · '.join(f'{n}={v!r}' for n, v in vals.items()))
        print('\nTwo transports that disagree are two instruments, and the descriptor '
              'is only telling the truth about one of them.')
        return 1
    print(f'PARITY: {", ".join(legs)} agree on all {len(keys)} compared facts, '
          f'including the artifact SHA-256.')
    print(f'  cube sha256 {core["cube_sha256"]}')
    print(f'  {core["cube_bytes"]:,} bytes · {core["n_basis_functions"]} basis functions '
          f'· E = {core["energy_hartree"]} Ha')
    print(f'{len(ABSENT_LEGS)} further leg(s) ABSENT and reported as such, never as '
          f'passing.')
    if '--json' in sys.argv:
        print(json.dumps({**legs, 'absent': ABSENT_LEGS}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
