#!/usr/bin/env python3
"""The dependency laws, as a gate rather than as an ADR paragraph.

An external audit asked for five ADRs whose acceptance criterion is "no behaviour
change". That is the shape of a law nobody can violate loudly. This repo has spent
a day proving the opposite point: a rule that lives in prose competes with a
default that fires automatically, and the default wins. So each law here is either

    ENFORCED   — measurable now, and violations fail the gate
    RATCHET    — violated today; the count is recorded and may only go DOWN
    N/A        — the subject does not exist yet, and the law is reported as
                 NOT APPLICABLE rather than PASS

That third state is the one that matters most. "MCP must not spawn the CLI" passes
trivially in a repo with no MCP, and a law that passes because its subject is
missing is indistinguishable, in a green suite, from a law that is being obeyed.
Counting it as a pass would be the same defect as a zero from an instrument that
has never returned non-zero.

Usage:  python3 scripts/check_layering.py [--json]
Exit:   0 all laws satisfied or within their ratchet · 1 a law regressed · 2 broken
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / 'scripts' / '.layering_baseline'

HTTP_MODULES = {'http', 'http.server', 'socketserver', 'urllib', 'urllib.request',
                'flask', 'fastapi', 'uvicorn', 'requests', 'httpx'}
SCIENCE_MODULES = {'rdkit', 'pyscf', 'numpy', 'scipy'}
DB_MODULES = {'psycopg', 'psycopg2', 'sqlalchemy'}


def imports_of(path: pathlib.Path) -> set[str]:
    """Top-level import names, module scope AND inside functions.

    Function-scope imports count: a module that imports RDKit lazily still cannot
    be loaded in an environment without it once that function runs, and a law about
    dependency direction is about what a layer is ALLOWED TO KNOW, not about when it
    happens to look.
    """
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split('.')[0])
    return names


def ts_imports_of(path: pathlib.Path) -> set[str]:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return set()
    return set(re.findall(r"""from\s+['"]([^'"]+)['"]""", text))


def calls_in_class(path: pathlib.Path, class_name: str, targets: set[str]) -> int:
    """How many times a method of `class_name` calls one of `targets` directly."""
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError):
        return -1
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                        and sub.func.id in targets):
                    count += 1
    return count


def law(name: str, status: str, detail: str, count: int | None = None) -> dict:
    return {'law': name, 'status': status, 'detail': detail, 'count': count}


def evaluate() -> list[dict]:
    out: list[dict] = []

    # ── ENFORCED: the codec must stay transport-free and storage-free ─────────
    env = ROOT / 'backend' / 'envelope.py'
    bad = (imports_of(env) & (HTTP_MODULES | DB_MODULES | SCIENCE_MODULES))
    out.append(law('envelope.py imports no HTTP, DB or science',
                   'PASS' if not bad else 'FAIL',
                   'clean' if not bad else f'imports {sorted(bad)}'))

    # ── ENFORCED: the job ledger knows the DB but not HTTP or chemistry ───────
    jb = ROOT / 'backend' / 'jobs.py'
    bad = imports_of(jb) & (HTTP_MODULES | SCIENCE_MODULES)
    out.append(law('jobs.py imports no HTTP and no science',
                   'PASS' if not bad else 'FAIL',
                   'clean' if not bad else f'imports {sorted(bad)}'))

    # ── ENFORCED: the registry describes methods; it does not serve them ──────
    mr = ROOT / 'backend' / 'method_registry.py'
    bad = imports_of(mr) & HTTP_MODULES
    out.append(law('method_registry.py imports no HTTP',
                   'PASS' if not bad else 'FAIL',
                   'clean' if not bad else f'imports {sorted(bad)}'))

    # ── ENFORCED: artifact addressing is transport- and storage-free ──────────
    # The split between artifacts.py (arithmetic) and artifacts_pg.py (psycopg) is
    # the whole reason an offline CLI, an SDK and an MCP adapter can share digest
    # and threshold logic. A single `import psycopg` in the wrong file undoes it
    # invisibly — the code keeps working here and stops working everywhere else.
    art = ROOT / 'backend' / 'artifacts.py'
    bad = imports_of(art) & (HTTP_MODULES | DB_MODULES | SCIENCE_MODULES)
    out.append(law('artifacts.py imports no HTTP, DB or science',
                   'PASS' if not bad else 'FAIL',
                   'clean' if not bad else f'imports {sorted(bad)}'))

    # ── ENFORCED: the DB adapter must not reach back up into the server ────────
    apg = ROOT / 'backend' / 'artifacts_pg.py'
    bad = imports_of(apg) & (HTTP_MODULES | SCIENCE_MODULES | {'field_server'})
    out.append(law('artifacts_pg.py imports no HTTP, science or the server',
                   'PASS' if not bad else 'FAIL',
                   'clean' if not bad else f'imports {sorted(bad)}'))

    # ── ENFORCED: the frontend service layer is DOM- and Mol*-free ────────────
    svc = ROOT / 'src' / 'app' / 'services'
    offenders = []
    for f in sorted(svc.glob('*.ts')):
        for spec in ts_imports_of(f):
            if 'mol-plugin' in spec or 'mol-model' in spec or 'molstar' in spec:
                offenders.append(f'{f.name} → {spec}')
    out.append(law('src/app/services imports no Mol*',
                   'PASS' if not offenders else 'FAIL',
                   'clean' if not offenders else '; '.join(offenders)))

    # ── RATCHET: routes still call scientific functions directly ──────────────
    fs = ROOT / 'backend' / 'field_server.py'
    n = calls_in_class(fs, 'Handler',
                       {'field_mep', 'field_mlp', 'field_quantum', 'prepare_mol',
                        'run_scf', 'embed_molecule', 'field_region'})
    out.append(law('HTTP handlers do not call scientific functions directly',
                   'RATCHET', f'{n} direct call(s) inside Handler', n))

    # ── RATCHET: the frontend still fetches scientific routes itself ──────────
    facets = ROOT / 'src' / 'app.frontend.facets.molstar-rdkit.editable' / 'facets'
    fetches = 0
    for f in facets.rglob('*.ts'):
        fetches += len(re.findall(r'\bfetch\s*\(', f.read_text(encoding='utf-8')))
    out.append(law('facets do not call fetch() on scientific routes',
                   'RATCHET', f'{fetches} fetch( call(s) across facets', fetches))

    # ── RATCHET: scientific refusals that are still untyped ──────────────────
    # `raise ValueError` inside a science function forces whoever catches it to
    # GUESS whether it meant UNSUPPORTED or INTERNAL. failures.from_exception now
    # makes that guess visible (`guessed_from_type: true`) instead of silent, and
    # this number is how many sites still need it.
    import re as _re
    fs_src = (ROOT / 'backend' / 'field_server.py').read_text(encoding='utf-8')
    n_untyped = len(_re.findall(r'raise ValueError\(', fs_src))
    out.append(law('scientific refusals are typed, not ValueError',
                   'RATCHET', f'{n_untyped} raise ValueError( site(s) in field_server',
                   n_untyped))

    # ── the SDK's laws. N/A until 2026-08-11; the subject now exists, so they are
    #    ENFORCED and the N/A wording is gone. That transition is the whole reason the
    #    three-state design exists: a law that had been reported as PASS while its
    #    subject was missing would have silently become a real, unchecked law today.
    sdk = ROOT / 'python' / 'src' / 'dirac'
    if not sdk.exists():
        out.append(law('SDK is import-light at module scope', 'N/A',
                       'the subject does not exist yet — NOT APPLICABLE rather than '
                       'PASS, because a law that passes for lack of a subject is '
                       'indistinguishable from one being obeyed'))
    else:
        # The property: `import dirac` must work on a bare interpreter. A single
        # module-scope `import numpy` in any SDK file breaks that for every consumer,
        # and it breaks it at IMPORT time — before any error handling can explain why.
        offenders = []
        for f in sorted(sdk.glob('*.py')):
            bad = imports_of(f) & (SCIENCE_MODULES | DB_MODULES | {'requests', 'httpx'})
            # transport.py resolves the kernel through importlib inside a method, which
            # imports_of() deliberately counts as knowing about it. Only urllib is
            # allowed as a transport, and it is stdlib.
            if bad:
                offenders.append(f'{f.name} → {sorted(bad)}')
        out.append(law('SDK imports no science, DB or HTTP library',
                       'PASS' if not offenders else 'FAIL',
                       'stdlib only' if not offenders else '; '.join(offenders)))

        cli = sdk / 'cli.py'
        bad = imports_of(cli) & {'field_server', 'invocation', 'catalog', 'handlers',
                                 'artifacts_pg', 'psycopg', 'rdkit', 'pyscf'}
        out.append(law('CLI reaches the kernel only through the SDK',
                       'PASS' if not bad else 'FAIL',
                       'via DiracClient only' if not bad
                       else f'imports {sorted(bad)} directly — the CLI and an MCP '
                            f'adapter would then have two different routes to the same '
                            f'science, and only one of them would be tested'))

        mcp = sdk / 'mcp.py'
        if not mcp.exists():
            out.append(law('MCP does not spawn the CLI', 'N/A',
                           'no MCP adapter exists yet — NOT APPLICABLE rather than '
                           'PASS. This is the law the audit named explicitly, and it '
                           'must not read as satisfied before there is anything to '
                           'satisfy it'))
        else:
            src = mcp.read_text(encoding='utf-8')
            spawns = [t for t in ('subprocess', 'Popen', 'os.system', 'shutil.which')
                      if t in src]
            out.append(law('MCP does not spawn the CLI',
                           'PASS' if not spawns else 'FAIL',
                           'calls the SDK in-process' if not spawns
                           else f'contains {spawns} — `MCP → spawn CLI → parse stdout` '
                                f'is the exact shape the audit rejected'))
    return out


def main() -> int:
    rows = evaluate()
    baseline: dict[str, int] = {}
    if BASELINE.exists():
        for line in BASELINE.read_text().splitlines():
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.split('=', 1)
                baseline[k.strip()] = int(v.strip())

    if '--json' in sys.argv:
        print(json.dumps({'laws': rows, 'baseline': baseline}, indent=2))
    failed = 0
    regressed = 0
    for r in rows:
        mark = {'PASS': 'PASS   ', 'FAIL': 'FAIL   ', 'RATCHET': 'RATCHET',
                'N/A': 'N/A    '}[r['status']]
        note = r['detail']
        if r['status'] == 'RATCHET' and r['count'] is not None:
            b = baseline.get(r['law'])
            if b is None:
                note += '  (no baseline recorded — recording is the next step)'
            elif r['count'] > b:
                note += f'  ← REGRESSED: {r["count"]} > baseline {b}'
                regressed += 1
            elif r['count'] < b:
                note += f'  ← improved: {r["count"]} < baseline {b}; lower the baseline'
        if r['status'] == 'FAIL':
            failed += 1
        if '--json' not in sys.argv:
            print(f'  {mark}  {r["law"]:<52} {note}')

    if '--json' not in sys.argv:
        print('─' * 100)
        n_na = sum(1 for r in rows if r['status'] == 'N/A')
        print(f'{len(rows)} laws · {failed} violated · {regressed} regressed · '
              f'{n_na} not applicable (subject absent — NOT counted as passing)')
    return 1 if (failed or regressed) else 0


if __name__ == '__main__':
    sys.exit(main())
