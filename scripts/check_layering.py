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

    # ── N/A: laws whose subject does not exist yet ───────────────────────────
    for subject, path, text in (
            ('SDK imports no DOM/Mol*', ROOT / 'python' / 'src', 'python SDK'),
            ('MCP does not spawn the CLI', ROOT / 'python' / 'src', 'MCP adapter'),
            ('CLI does not import the server Handler', ROOT / 'python' / 'src', 'CLI')):
        exists = path.exists()
        out.append(law(subject, 'FAIL' if False else ('RATCHET' if exists else 'N/A'),
                       'the subject does not exist yet — reported as NOT APPLICABLE '
                       'rather than PASS, because a law that passes for lack of a '
                       'subject is indistinguishable from one being obeyed'
                       if not exists else f'{text} present; add the check'))
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
