"""`dirac` — the CLI. A thin shell over DiracClient, and thin is the specification.

THE AUDIT'S ORDERING, and this file is where it pays off: the CLI is the first thing
shown to a person, and the LAST thing built, because everything it does must already
exist underneath. Concretely, this file contains no HTTP, no schema validation, no
refusal classification, no artifact digest logic and no science. If it did, `dirac run`
and an MCP tool call would eventually disagree, and the CLI — being the one a human
watches — would be believed.

WHAT A CLI OWES A MACHINE, which is different from what it owes a person:
  --json     the envelope VERBATIM on stdout, nothing else. No progress lines, no
             banner, no human formatting. A caller pipes it to jq and it works.
  exit code  0 success · 1 typed refusal · 2 usage/environment · 3 internal fault.
             A refusal and a crash must not share an exit code, or a script cannot tell
             "your molecule is too large" from "the daemon is down".
  stderr     everything human. So `dirac run … --json > out.json` is clean even while
             the terminal shows what happened.

WHAT IT OWES A PERSON: the refusal's `caller_action` and `hint`, printed, because a
refusal that names no way forward is a dead end — and the kernel already computed both.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import errors
from .client import DiracClient

EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_FAULT = 0, 1, 2, 3


def _client(a: argparse.Namespace) -> DiracClient:
    return DiracClient(a.transport, base_url=a.url, timeout=a.timeout)


def _emit_json(obj: Any) -> None:
    """stdout gets JSON and nothing else, ever. sort_keys so two runs are diffable."""
    json.dump(obj, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write('\n')


def _molecule(a: argparse.Namespace) -> dict:
    if a.molfile == '-':
        content = sys.stdin.read()
    else:
        with open(a.molfile, encoding='utf-8') as fh:
            content = fh.read()
    if len(content) < 40:
        raise SystemExit2(f'{a.molfile} is {len(content)} bytes — too short to be a '
                          f'molfile with 3D coordinates. A field method needs 3D, and '
                          f'the explicit step that produces it is molecule.embed.')
    return {'kind': 'molfile', 'content': content, 'dimensionality': 3}


class SystemExit2(Exception):
    """Usage/environment problem — exit 2, distinct from a refusal (1) and a fault (3)."""


def _params(a: argparse.Namespace) -> dict:
    out: dict[str, Any] = {}
    if getattr(a, 'basis', None):
        out['basis'] = a.basis
    if getattr(a, 'spin', None) is not None:
        out['spin'] = a.spin
    for kv in getattr(a, 'param', None) or []:
        if '=' not in kv:
            raise SystemExit2(f'--param expects key=value, got {kv!r}')
        k, v = kv.split('=', 1)
        # JSON first so numbers and booleans stay typed; a bare word falls back to a
        # string. Guessing types with a regex is how `--param spin=0` becomes the string
        # "0" and the schema rejects it with a message about the wrong thing.
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


# ── commands ──────────────────────────────────────────────────────────────────
def _emit_command(env: dict, json_mode: bool) -> int:
    if json_mode:
        _emit_json(env)
    elif env.get('ok'):
        print(json.dumps(env.get('data') or {}, indent=2, sort_keys=True))
    else:
        error = env.get('error') or {}
        print(f'dirac: {error.get("code", "INTERNAL")}: '
              f'{error.get("user_message") or error.get("message")}', file=sys.stderr)
    return EXIT_OK if env.get('ok') else EXIT_REFUSED


def cmd_health(a: argparse.Namespace) -> int:
    return _emit_command(_client(a).health(), a.json)


def cmd_commands(a: argparse.Namespace) -> int:
    commands = _client(a).commands()
    if a.json:
        _emit_json({'commands': commands})
    else:
        for c in commands:
            print(f'{c["id"]:<34} {c["mutability"]:<8} '
                  f'{c["execution_class"]:<11} job={c["job_policy"]}')
    return EXIT_OK


def cmd_jobs(a: argparse.Namespace) -> int:
    return _emit_command(_client(a).jobs(state=a.state, limit=a.limit), a.json)


def cmd_job(a: argparse.Namespace) -> int:
    client = _client(a)
    if a.job_action == 'get':
        env = client.job_get(a.job_id)
    elif a.job_action == 'wait':
        env = client.job_wait(a.job_id, timeout=a.wait_timeout)
    else:
        env = client.job_cancel(a.job_id)
    return _emit_command(env, a.json)


def cmd_molecule_describe(a: argparse.Namespace) -> int:
    molecule = {'smiles': a.smiles} if a.smiles else _molecule(a)
    return _emit_command(_client(a).molecule_describe(molecule), a.json)


def cmd_field_compute(a: argparse.Namespace) -> int:
    client = _client(a)
    env = client.field_compute(
        molecule=_molecule(a), field_kind=a.kind,
        parameters=_params(a) or None, budget_seconds=a.max_seconds)
    if env.get('ok') and a.wait:
        job_id = (env.get('meta') or {}).get('job_id')
        if job_id:
            env = client.job_wait(job_id, timeout=a.wait_timeout)
    return _emit_command(env, a.json)


def cmd_methods(a: argparse.Namespace) -> int:
    ms = _client(a).methods()
    if a.json:
        _emit_json({'methods': ms})
        return EXIT_OK
    w = max((len(m['method_id']) for m in ms), default=10)
    for m in ms:
        mark = ' ' if m.get('executable') else '·'
        print(f'{mark} {m["method_id"]:<{w}}  {m.get("summary", "")[:90]}')
    n_exec = sum(1 for m in ms if m.get('executable'))
    print(f'\n{len(ms)} method(s), {n_exec} executable. A `·` marks one that is '
          f'described but has no handler — listable, not runnable.', file=sys.stderr)
    return EXIT_OK


def cmd_describe(a: argparse.Namespace) -> int:
    d = _client(a).describe(a.method_id)
    if a.json:
        _emit_json(d)
        return EXIT_OK
    print(f'{d["method_id"]}  version {d.get("version") or "<unregistered>"}')
    print(f'\n{d.get("summary", "")}\n')
    if d.get('description'):
        print(d['description'] + '\n')
    props = ((d.get('input_schema') or {}).get('properties') or {})
    params = ((props.get('parameters') or {}).get('properties') or {})
    if params:
        print('parameters:')
        for k, spec in params.items():
            allowed = spec.get('enum')
            print(f'  {k:<12} default={spec.get("default")!r}'
                  + (f'  one of {allowed}' if allowed else ''))
    if d.get('refusals'):
        print('\nrefusals this method can return:')
        for r in d['refusals']:
            print(f'  {r.get("code", "?"):<26} {str(r.get("when", ""))[:70]}')
    if d.get('artifacts'):
        print('\nartifacts:')
        for art in d['artifacts']:
            typ = art.get('typical_bytes')
            print(f'  {art["role"]:<14} {art["media_type"]}'
                  + (f'  ~{typ:,} bytes' if typ else ''))
    return EXIT_OK


def cmd_estimate(a: argparse.Namespace) -> int:
    payload = {'molecule': _molecule(a)}
    p = _params(a)
    if p:
        payload['parameters'] = p
    est = _client(a).estimate(a.method_id, payload)
    if a.json:
        _emit_json(est)
        return EXIT_OK
    if not est.get('available'):
        print(f'no estimate available: {est.get("reason")}', file=sys.stderr)
        # NOT an error: "I cannot predict this" is a legitimate, honest answer, and a
        # non-zero exit would make a script treat it as a failure.
        return EXIT_OK
    print(f'{est["method_id"]}  ~{est["seconds"]}s  '
          f'~{est.get("artifact_bytes_estimated", 0):,} bytes of artifact')
    if est.get('seconds_breakdown'):
        print(f'  {est["seconds_breakdown"]}')
    print(f'  {est.get("confidence", "")}', file=sys.stderr)
    return EXIT_OK


def cmd_run(a: argparse.Namespace) -> int:
    payload = {'molecule': _molecule(a)}
    p = _params(a)
    if p:
        payload['parameters'] = p
    kw: dict[str, Any] = {}
    if a.inline_max is not None:
        kw['inline_max'] = a.inline_max
    if a.max_seconds is not None:
        kw['budget_seconds'] = a.max_seconds
    c = _client(a)
    method_id = c.method_for_kind(a.method_id)
    env = c.invoke(method_id, payload, **kw)

    if a.json:
        # VERBATIM. This is the line that makes the CLI a machine surface: whatever the
        # kernel produced is what a caller sees, with no CLI-specific reshaping. It is
        # also what lets scripts/acceptance_parity.py compare this leg to the others at
        # all — a CLI that pretty-printed its own view could not be compared.
        _emit_json(env)
        return EXIT_OK if env.get('ok') else EXIT_REFUSED

    if not env.get('ok'):
        raise errors.from_envelope(env, method_id=method_id)

    data = env.get('data') or {}
    meta = env.get('meta') or {}
    field = data.get('field') or {}
    wf = data.get('wavefunction') or {}
    print(f'{method_id}  version {meta.get("version") or "<unregistered>"}  '
          f'{meta.get("cache", "?")}  {meta.get("seconds", "?")}s')
    if field.get('grid'):
        print(f'  grid     {field["grid"].get("dimensions")} @ '
              f'{field["grid"].get("spacing_angstrom")} Å')
    if field.get('extrema'):
        print(f'  range    {field["extrema"].get("min")} … '
              f'{field["extrema"].get("max")} {field.get("native_units", "")}')
    if wf:
        print(f'  scf      {wf.get("method")}/{wf.get("basis")}  '
              f'E={wf.get("energy_hartree")} Ha  nbasis={wf.get("n_basis_functions")}')
        if wf.get('homo_ev') is not None:
            print(f'  frontier HOMO {wf["homo_ev"]:.3f} eV'
                  + (f'  LUMO {wf["lumo_ev"]:.3f} eV' if wf.get('lumo_ev') is not None
                     else ''))
    for art in env.get('artifacts') or []:
        where = 'inline' if art.get('inline') else art.get('url')
        print(f'  artifact {art["role"]}  {art["size_bytes"]:,} bytes  '
              f'sha256 {art["sha256"][:16]}…  ({where})')
    for w in env.get('warnings') or []:
        print(f'  ⚠ {w.get("code")}: {str(w.get("message", ""))[:150]}', file=sys.stderr)
    if a.output:
        for art in env.get('artifacts') or []:
            if art.get('role') == a.role:
                with open(a.output, 'wb') as fh:
                    fh.write(c.fetch(art))
                print(f'  wrote    {a.output} ({art["size_bytes"]:,} bytes, digest '
                      f'verified)')
                break
        else:
            raise SystemExit2(f'no artifact in role {a.role!r} to write')
    return EXIT_OK


def cmd_artifact_get(a: argparse.Namespace) -> int:
    c = _client(a)
    ref = {'sha256': a.address.replace('sha256:', ''),
           'url': f'/v2/artifacts/{a.address}'}
    if not a.address.replace('sha256:', '').strip():
        raise SystemExit2('an artifact address is required')
    data = c.fetch(ref)                      # verifies the digest before returning
    if a.output == '-':
        sys.stdout.buffer.write(data)
    else:
        with open(a.output, 'wb') as fh:
            fh.write(data)
        print(f'{a.output}  {len(data):,} bytes  digest verified', file=sys.stderr)
    return EXIT_OK


def cmd_artifact_verify(a: argparse.Namespace) -> int:
    """Recompute the digest of what the server serves and compare to the address.

    The one command whose whole value is that it can FAIL. `fetch` already verifies, so
    this exists to make the verification explicit and scriptable — and to report the
    number, because a check that only ever says "fine" teaches nobody anything.
    """
    import hashlib
    c = _client(a)
    want = a.address.replace('sha256:', '')
    try:
        data = c.fetch({'sha256': want, 'url': f'/v2/artifacts/{a.address}'})
    except errors.DiracDigestMismatch as e:
        print(f'MISMATCH  {e}', file=sys.stderr)
        return EXIT_REFUSED
    got = hashlib.sha256(data).hexdigest()
    if a.json:
        _emit_json({'address': a.address, 'sha256': got, 'bytes': len(data),
                    'verified': got == want})
    else:
        print(f'OK  {got}  {len(data):,} bytes')
    return EXIT_OK if got == want else EXIT_REFUSED


# ── argument surface ──────────────────────────────────────────────────────────
def _global_flags() -> argparse.ArgumentParser:
    """The flags every subcommand accepts, as a REUSED PARENT rather than a copy.

    Defined once and attached to the top parser and to each subparser, because argparse
    only accepts a top-level option BEFORE the subcommand — and `dirac run x --json` is
    where every human puts it. Measured: written the obvious way first, `run … --json`
    was an argparse error, exit 2, and stdout was EMPTY. The machine surface was
    unreachable in the only position anyone types.
    """
    g = argparse.ArgumentParser(add_help=False)
    g.add_argument('--transport', default='auto', choices=('auto', 'local', 'http'),
                   help='auto prefers in-process and falls back to HTTP; the choice is '
                        'always reported in meta.transport')
    g.add_argument('--url', default=None, help='daemon base URL (default $DIRAC_URL)')
    g.add_argument('--timeout', type=float, default=600.0)
    g.add_argument('--json', action='store_true',
                   help='emit the envelope VERBATIM on stdout; all human output goes to '
                        'stderr')
    return g


def build_parser() -> argparse.ArgumentParser:
    G = _global_flags()
    p = argparse.ArgumentParser(
        prog='dirac', parents=[G],
        description='Molecular fields, addressable and typed.')
    sub = p.add_subparsers(dest='cmd', required=True)

    m = sub.add_parser('methods', parents=[G], help='list what this system can compute')
    m.add_argument('methods_action', nargs='?', choices=('list',), default='list')
    m.set_defaults(fn=cmd_methods)

    health = sub.add_parser('health', parents=[G], help='application and stores health')
    health.set_defaults(fn=cmd_health)

    commands = sub.add_parser('commands', parents=[G], help='semantic command catalog')
    commands.set_defaults(fn=cmd_commands)

    jobs = sub.add_parser('jobs', parents=[G], help='list durable jobs')
    jobs.add_argument('jobs_action', nargs='?', choices=('list',), default='list')
    jobs.add_argument('--state')
    jobs.add_argument('--limit', type=int, default=100)
    jobs.set_defaults(fn=cmd_jobs)

    job = sub.add_parser('job', parents=[G], help='get, wait for, or cancel a job')
    job.add_argument('job_action', choices=('get', 'wait', 'cancel'))
    job.add_argument('job_id')
    job.add_argument('--wait-timeout', type=float, default=300)
    job.set_defaults(fn=cmd_job)

    molecule = sub.add_parser('molecule', parents=[G], help='molecule commands')
    molecule.add_argument('molecule_action', choices=('describe',))
    source = molecule.add_mutually_exclusive_group(required=True)
    source.add_argument('--smiles')
    source.add_argument('--molfile')
    molecule.set_defaults(fn=cmd_molecule_describe)

    field = sub.add_parser('field', parents=[G], help='semantic field computation')
    field.add_argument('field_action', choices=('compute',))
    field.add_argument('--molfile', required=True)
    field.add_argument('--kind', required=True,
                       choices=('mep', 'mlp', 'homo', 'lumo', 'density', 'mep_qm'))
    field.add_argument('--basis')
    field.add_argument('--spin', type=int)
    field.add_argument('--param', action='append')
    field.add_argument('--max-seconds', type=float)
    field.add_argument('--wait', action='store_true')
    field.add_argument('--wait-timeout', type=float, default=300)
    field.set_defaults(fn=cmd_field_compute)

    d = sub.add_parser('describe', parents=[G], help='the full contract for one method')
    d.add_argument('method_id')
    d.set_defaults(fn=cmd_describe)

    for name, fn, helptext in (('estimate', cmd_estimate, 'cost, without running it'),
                               ('run', cmd_run, 'invoke a method')):
        c = sub.add_parser(name, parents=[G], help=helptext)
        c.add_argument('method_id', help='full id or a short kind such as `homo`')
        c.add_argument('--molfile', required=True, help='path, or - for stdin')
        c.add_argument('--basis')
        c.add_argument('--spin', type=int)
        c.add_argument('--param', action='append',
                       help='key=value, JSON-typed (repeatable)')
        if name == 'run':
            c.add_argument('--max-seconds', dest='max_seconds', type=float)
            c.add_argument('--inline-max', dest='inline_max', type=int,
                           help='0 keeps every artifact out of the response — what an '
                                'agent wants when it does not need the bytes')
            c.add_argument('-o', '--output', help='write the artifact here')
            c.add_argument('--role', default='field.cube')
        c.set_defaults(fn=fn)

    art = sub.add_parser('artifacts', parents=[G], help='fetch and verify stored artifacts')
    asub = art.add_subparsers(dest='artcmd', required=True)
    g = asub.add_parser('get', parents=[G])
    g.add_argument('address', help='uuid, sha256 hex, or sha256:<hex>')
    g.add_argument('-o', '--output', default='-')
    g.set_defaults(fn=cmd_artifact_get)
    v = asub.add_parser('verify', parents=[G])
    v.add_argument('address')
    v.set_defaults(fn=cmd_artifact_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except SystemExit2 as e:
        print(f'dirac: {e}', file=sys.stderr)
        return EXIT_USAGE
    except errors.DiracError as e:
        # A REFUSAL, printed with what to do about it. Exit 1, never 3: a script must be
        # able to distinguish "this molecule cannot be done" from "the tool broke".
        print(f'dirac: {e}', file=sys.stderr)
        if e.details:
            print(f'  details: {json.dumps(e.details)[:400]}', file=sys.stderr)
        return EXIT_REFUSED
    except FileNotFoundError as e:
        print(f'dirac: {e}', file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        return EXIT_USAGE
    except Exception as e:                                          # noqa: BLE001
        import traceback
        print(f'dirac: internal fault — {type(e).__name__}: {e}', file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return EXIT_FAULT


if __name__ == '__main__':
    sys.exit(main())
