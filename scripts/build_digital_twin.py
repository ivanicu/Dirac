#!/usr/bin/env python3
"""Build Dirac's source-derived, function-level architecture Digital Twin."""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import pathlib
import re
import subprocess
import urllib.request
from collections import Counter, defaultdict
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / 'docs' / 'architecture'
JSON_OUT = OUT_DIR / 'dirac-digital-twin.json'
HTML_OUT = OUT_DIR / 'dirac-digital-twin.html'
TEMPLATE = ROOT / 'scripts' / 'digital_twin_template.html'


class Twin:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str, str], dict] = {}
        self.flows: list[dict] = []

    def node(self, node_id: str, node_type: str, name: str, **attrs: Any) -> str:
        # Registry records may themselves carry an ``id`` or ``type`` field. The
        # graph identity is canonical and must never be shadowed by source data.
        value = {**attrs, 'id': node_id, 'type': node_type, 'name': name}
        if node_id in self.nodes:
            old = self.nodes[node_id]
            for key, item in value.items():
                if item not in (None, '', [], {}):
                    old[key] = item
        else:
            self.nodes[node_id] = value
        return node_id

    def edge(self, source: str, target: str, relation: str, **attrs: Any) -> None:
        if source not in self.nodes or target not in self.nodes:
            return
        key = (source, relation, target)
        self.edges[key] = {'source': source, 'target': target,
                           'relation': relation, **attrs}

    def flow(self, flow_id: str, name: str, summary: str,
             steps: list[tuple[str, str]]) -> None:
        self.flows.append({'id': flow_id, 'name': name, 'summary': summary,
                           'steps': [{'node': node, 'label': label}
                                     for node, label in steps if node in self.nodes]})
        present = [node for node, _ in steps if node in self.nodes]
        for left, right in zip(present, present[1:]):
            self.edge(left, right, 'flow', flow=flow_id)


def run(command: list[str], timeout: int = 10) -> tuple[bool, str]:
    try:
        proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True,
                              timeout=timeout, check=False)
        return proc.returncode == 0, (proc.stdout or proc.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def layer_for(path: str) -> str:
    if '/_spec/' in path or '/test' in path or pathlib.Path(path).name.startswith('test_'):
        return 'verification'
    if path.startswith('backend/physics/'):
        return 'scientific-compute'
    if path.startswith('backend/dirac_app/'):
        return 'application'
    if path.startswith('backend/'):
        return 'infrastructure'
    if path.startswith('python/src/dirac/'):
        return 'client-sdk'
    if path.startswith('scripts/'):
        return 'tooling'
    return 'runtime'


def python_files() -> list[pathlib.Path]:
    roots = [ROOT / 'backend', ROOT / 'python' / 'src' / 'dirac', ROOT / 'scripts']
    paths: list[pathlib.Path] = []
    for base in roots:
        if not base.exists():
            continue
        for path in base.rglob('*.py'):
            rel = path.relative_to(ROOT).as_posix()
            if any(part in rel.split('/') for part in ('env', '__pycache__', 'site-packages')):
                continue
            paths.append(path)
    return sorted(set(paths))


def module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(ROOT).with_suffix('').as_posix()
    if rel.startswith('python/src/'):
        rel = rel.removeprefix('python/src/')
    if rel.endswith('/__init__'):
        rel = rel[:-9]
    return rel.replace('/', '.')


def add_python(twin: Twin) -> dict[str, str]:
    trees: dict[pathlib.Path, ast.Module] = {}
    defs: dict[tuple[str, str], str] = {}
    simple: dict[tuple[str, str], list[str]] = defaultdict(list)

    for path in python_files():
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(), filename=rel)
        except (SyntaxError, UnicodeDecodeError):
            continue
        trees[path] = tree
        mid = twin.node(f'module:py:{rel}', 'module', path.name,
                        language='python', path=rel, line=1,
                        layer=layer_for(rel), qualified_name=module_name(path))

        class Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.stack: list[str] = []

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                qual = '.'.join([*self.stack, node.name])
                cid = twin.node(f'class:py:{rel}:{node.lineno}:{qual}', 'class', node.name,
                                language='python', path=rel, line=node.lineno,
                                end_line=getattr(node, 'end_lineno', node.lineno),
                                layer=layer_for(rel), qualified_name=qual,
                                bases=[ast.unparse(b) for b in node.bases])
                twin.edge(mid, cid, 'contains')
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                qual = '.'.join([*self.stack, node.name])
                fid = twin.node(f'function:py:{rel}:{node.lineno}:{qual}',
                                'method' if self.stack else 'function', node.name,
                                language='python', path=rel, line=node.lineno,
                                end_line=getattr(node, 'end_lineno', node.lineno),
                                layer=layer_for(rel), qualified_name=qual,
                                owner=self.stack[-1] if self.stack else None,
                                async_function=isinstance(node, ast.AsyncFunctionDef),
                                parameters=[a.arg for a in [*node.args.posonlyargs,
                                                            *node.args.args,
                                                            *node.args.kwonlyargs]])
                twin.edge(mid, fid, 'contains')
                defs[(module_name(path), qual)] = fid
                simple[(module_name(path), node.name)].append(fid)
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            visit_FunctionDef = _function
            visit_AsyncFunctionDef = _function

        Visitor().visit(tree)

    for path, tree in trees.items():
        rel = path.relative_to(ROOT).as_posix()
        mod = module_name(path)
        mid = f'module:py:{rel}'
        aliases: dict[str, tuple[str, str | None]] = {}
        for node in tree.body:
            if isinstance(node, ast.Import):
                for name in node.names:
                    aliases[name.asname or name.name.split('.')[0]] = (name.name, None)
                    target = next((nid for nid, n in twin.nodes.items()
                                   if n.get('qualified_name') == name.name and n['type'] == 'module'), None)
                    if target:
                        twin.edge(mid, target, 'imports')
            elif isinstance(node, ast.ImportFrom) and node.module:
                base = node.module
                if node.level:
                    parts = mod.split('.')[:-1]
                    base = '.'.join(parts[:len(parts) - node.level + 1] + ([node.module] if node.module else []))
                for name in node.names:
                    aliases[name.asname or name.name] = (base, name.name)
                target = next((nid for nid, n in twin.nodes.items()
                               if n.get('qualified_name') == base and n['type'] == 'module'), None)
                if target:
                    twin.edge(mid, target, 'imports')

        class Calls(ast.NodeVisitor):
            def __init__(self) -> None:
                self.current: list[tuple[str, str]] = []

            def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                parent = self.current[-1][0] if self.current else ''
                qual = f'{parent}.{node.name}'.strip('.')
                fid = defs.get((mod, qual))
                self.current.append((qual, fid or ''))
                self.generic_visit(node)
                self.current.pop()

            visit_FunctionDef = _enter
            visit_AsyncFunctionDef = _enter

            def visit_Call(self, node: ast.Call) -> None:
                if not self.current or not self.current[-1][1]:
                    return self.generic_visit(node)
                target = None
                if isinstance(node.func, ast.Name):
                    candidates = simple.get((mod, node.func.id), [])
                    target = candidates[0] if len(candidates) == 1 else None
                    if node.func.id in aliases:
                        imported_mod, symbol = aliases[node.func.id]
                        candidates = simple.get((imported_mod, symbol or node.func.id), [])
                        target = candidates[0] if len(candidates) == 1 else target
                elif isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name) and node.func.value.id in ('self', 'cls'):
                        owner = self.current[-1][0].rsplit('.', 1)[0]
                        target = defs.get((mod, f'{owner}.{node.func.attr}'))
                    elif isinstance(node.func.value, ast.Name) and node.func.value.id in aliases:
                        imported_mod, _ = aliases[node.func.value.id]
                        candidates = simple.get((imported_mod, node.func.attr), [])
                        target = candidates[0] if len(candidates) == 1 else None
                if target:
                    twin.edge(self.current[-1][1], target, 'calls')
                self.generic_visit(node)

        Calls().visit(tree)
    return {f'{mod}:{name}': ids[0] for (mod, name), ids in simple.items() if len(ids) == 1}


def add_typescript(twin: Twin) -> dict:
    ok, raw = run(['node', 'scripts/extract_ts_digital_twin.mjs'], timeout=90)
    if not ok:
        raise RuntimeError(f'TypeScript extraction failed: {raw}')
    data = json.loads(raw)
    for node in data['nodes']:
        twin.node(node.pop('id'), node.pop('type'), node.pop('name'), **node)
    for edge in data['edges']:
        twin.edge(edge['source'], edge['target'], edge['type'])
    return data


SHELL_FUNCTION_RE = re.compile(
    r'^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\s*\))?\s*\{', re.M)


def add_shell(twin: Twin) -> None:
    """Represent declared first-party shell functions used by gates and operations."""
    roots = [ROOT / 'scripts', ROOT / 'backend' / 'db', ROOT / 'deploy']
    for base in roots:
        if not base.exists():
            continue
        for path in sorted(base.rglob('*.sh')):
            rel = path.relative_to(ROOT).as_posix()
            text = path.read_text(errors='replace')
            mid = twin.node(f'module:sh:{rel}', 'module', path.name,
                            language='shell', path=rel, line=1, layer=layer_for(rel))
            for match in SHELL_FUNCTION_RE.finditer(text):
                line = text.count('\n', 0, match.start()) + 1
                name = match.group(1)
                fid = twin.node(f'function:sh:{rel}:{line}:{name}', 'function', name,
                                language='shell', path=rel, line=line,
                                qualified_name=name, layer=layer_for(rel))
                twin.edge(mid, fid, 'contains')


def add_contracts(twin: Twin, py_symbols: dict[str, str]) -> None:
    domain = json.loads((ROOT / 'contracts/domain/object-kinds.json').read_text())
    relations = json.loads((ROOT / 'contracts/domain/relations.json').read_text())
    commands = json.loads((ROOT / 'contracts/commands/registry.json').read_text())['commands']
    contract = twin.node('contract:object-ref', 'contract', 'ObjectRef', layer='domain',
                         path='contracts/domain/object-kinds.json', schema_version=domain['schema_version'])
    for kind in domain['kinds']:
        kid = twin.node(f'object-kind:{kind}', 'object-kind', kind, layer='domain')
        twin.edge(contract, kid, 'permits')
    for relation in relations['relations']:
        twin.node(f'relation-kind:{relation}', 'relation-kind', relation, layer='domain')

    registry = twin.node('registry:commands', 'registry', 'Command Registry', layer='contracts',
                         path='contracts/commands/registry.json', count=len(commands))
    for cmd in commands:
        cid = twin.node(f'command:{cmd["id"]}', 'command', cmd['id'], layer='commands',
                        path='contracts/commands/registry.json', version=cmd['version'],
                        category=cmd['category'], mutability=cmd['mutability'],
                        execution_class=cmd['execution_class'], executors=cmd['executors'],
                        job_policy=cmd['job_policy'], provenance_policy=cmd['provenance_policy'],
                        errors=cmd['errors'], availability=cmd.get('availability', 'available'))
        twin.edge(registry, cid, 'registers')
        for kind in cmd['input_object_kinds']:
            twin.edge(f'object-kind:{kind}', cid, 'input-to')
        for kind in cmd['output_object_kinds']:
            twin.edge(cid, f'object-kind:{kind}', 'outputs')
        module, handler = cmd['handler'].split(':', 1)
        target = py_symbols.get(f'backend.dirac_app.{module}:{handler}')
        if target:
            twin.edge(cid, target, 'handled-by')

    method_registry = twin.node('registry:methods', 'registry', 'Scientific Method Registry',
                                layer='contracts', path='contracts/methods', count=0)
    method_count = 0
    for path in sorted((ROOT / 'contracts' / 'methods').glob('*.json')):
        item = json.loads(path.read_text())
        method_count += 1
        mid = twin.node(f'method:{item["method_id"]}', 'scientific-method', item['method_id'],
                        layer='scientific-methods', path=path.relative_to(ROOT).as_posix(),
                        schema_version=item.get('schema_version'), summary=item.get('summary'),
                        description=item.get('description'), refusals=len(item.get('refusals', [])))
        twin.edge(method_registry, mid, 'registers')
        impl = item.get('implementation', {})
        for fn in impl.get('functions', []):
            target = py_symbols.get(f'{impl.get("module")}:{fn}')
            if target:
                twin.edge(mid, target, 'implemented-by')
        invocation = item.get('invocation', {})
        for role in [*invocation.get('artifacts', []), *item.get('output', {}).get('artifacts', [])]:
            name = role.get('role')
            if not name:
                continue
            aid = twin.node(f'artifact-role:{name}', 'artifact-role', name, layer='artifacts',
                            media_type=role.get('media_type'), required=role.get('required'))
            twin.edge(mid, aid, 'produces')
    twin.nodes[method_registry]['count'] = method_count


def add_app_shell(twin: Twin, registries: dict) -> None:
    shell = twin.node('system:app-shell', 'system', 'AppShell', layer='experience',
                      path='src/app/shell/app-shell.ts')
    for workspace in registries['workspaces']:
        wid = twin.node(f'workspace:{workspace["id"]}', 'workspace', workspace['label'],
                        layer='experience', **workspace)
        twin.edge(shell, wid, 'contains')
    for view in registries['views']:
        vid = twin.node(f'view:{view["id"]}', 'view', view['label'], layer='experience', **view)
        twin.edge(f'workspace:{view["workspace"]}', vid, 'contains')
        for command in view.get('actions', []):
            twin.edge(vid, f'command:{command}', 'offers')
        for kind in view.get('primaryObjectKinds', []):
            twin.edge(vid, f'object-kind:{kind}', 'projects')
    for module in registries['modules']:
        module_data = dict(module)
        name = module_data.pop('id')
        mid = twin.node(f'ui-module:{name}', 'ui-module', name, layer='experience', **module_data)
        for view in module.get('supportedViews', []):
            twin.edge(f'view:{view}', mid, 'composes')
        for command in module.get('providesCommands', []):
            twin.edge(mid, f'command:{command}', 'invokes')
        for kind in module.get('consumesObjects', []):
            twin.edge(f'object-kind:{kind}', mid, 'consumed-by')


CREATE_RE = re.compile(r'CREATE\s+(?:OR\s+REPLACE\s+)?(MATERIALIZED\s+VIEW|TABLE|VIEW|TYPE|FUNCTION)\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.]+)', re.I)


def add_database(twin: Twin) -> None:
    db = twin.node('store:postgres', 'store', 'PostgreSQL · dirac', layer='persistence')
    for path in sorted((ROOT / 'backend' / 'db' / 'migrations').glob('*.sql')):
        rel = path.relative_to(ROOT).as_posix()
        migration = twin.node(f'migration:{path.stem}', 'migration', path.stem,
                              layer='persistence', path=rel)
        twin.edge(migration, db, 'migrates')
        text = path.read_text()
        for match in CREATE_RE.finditer(text):
            kind = match.group(1).lower().replace(' ', '-')
            name = match.group(2)
            line = text.count('\n', 0, match.start()) + 1
            oid = twin.node(f'db:{kind}:{name}', f'db-{kind}', name, layer='persistence',
                            path=rel, line=line, defined_by=path.stem)
            twin.edge(migration, oid, 'defines')
            twin.edge(oid, db, 'stored-in')
        for source, target in re.findall(r'([\w.]+)\s+[^,;]*?REFERENCES\s+([\w.]+)', text, re.I):
            source_id = next((nid for nid in twin.nodes if nid.startswith('db:table:') and nid.endswith(source)), None)
            target_id = next((nid for nid in twin.nodes if nid.startswith('db:table:') and nid.endswith(target)), None)
            if source_id and target_id:
                twin.edge(source_id, target_id, 'references')


def add_system_and_flows(twin: Twin) -> None:
    systems = [
        ('actor:human', 'actor', 'Human scientist', 'actors'),
        ('actor:agent', 'actor', 'AI agent', 'actors'),
        ('surface:gui', 'surface', 'Dirac GUI', 'surfaces'),
        ('surface:cli', 'surface', 'Dirac CLI', 'surfaces'),
        ('surface:python-sdk', 'surface', 'Python SDK', 'surfaces'),
        ('surface:typescript-sdk', 'surface', 'TypeScript SDK', 'surfaces'),
        ('surface:mcp', 'surface', 'MCP tools', 'surfaces'),
        ('transport:http-v2', 'transport', 'HTTP /v2/execute', 'transport'),
        ('system:dispatcher', 'system', 'CommandDispatcher', 'application'),
        ('system:invocation', 'system', 'InvocationService', 'application'),
        ('system:kernel', 'system', 'DiracKernel', 'application'),
        ('system:executor', 'system', 'Executor boundary', 'execution'),
        ('store:jobs', 'store', 'Durable JobStore', 'persistence'),
        ('store:result-cache', 'store', 'Method-current ResultCache', 'persistence'),
        ('store:artifacts', 'store', 'Content-addressed ArtifactStore', 'persistence'),
        ('system:scientific-context', 'system', 'ScientificContext · one clock', 'experience'),
        ('system:scene', 'system', 'SceneService · one mol* instance', 'experience'),
        ('external:molstar', 'external-system', 'Mol* rendering engine', 'external'),
        ('external:rdkit', 'external-system', 'RDKit chemistry', 'external'),
        ('external:pyscf', 'external-system', 'PySCF quantum chemistry', 'external'),
        ('external:xtb', 'external-system', 'xTB quantum chemistry', 'external'),
        ('service:fields', 'service', 'dirac-fields.service :8901', 'runtime'),
        ('service:web', 'service', 'dirac-web.service :1360', 'runtime'),
    ]
    for node_id, kind, name, layer in systems:
        twin.node(node_id, kind, name, layer=layer)

    for surface in ('surface:gui', 'surface:cli', 'surface:python-sdk',
                    'surface:typescript-sdk', 'surface:mcp'):
        twin.edge(surface, 'transport:http-v2', 'calls')
    twin.edge('actor:human', 'surface:gui', 'uses')
    twin.edge('actor:human', 'surface:cli', 'uses')
    twin.edge('actor:agent', 'surface:mcp', 'uses')
    twin.edge('transport:http-v2', 'system:dispatcher', 'dispatches-to')
    twin.edge('registry:commands', 'system:dispatcher', 'governs')
    twin.edge('system:dispatcher', 'system:kernel', 'invokes')
    twin.edge('system:kernel', 'system:invocation', 'composes')
    twin.edge('system:invocation', 'system:executor', 'schedules')
    twin.edge('system:executor', 'store:jobs', 'updates')
    twin.edge('system:invocation', 'store:result-cache', 'reads-writes')
    twin.edge('system:invocation', 'store:artifacts', 'writes')
    twin.edge('store:jobs', 'store:postgres', 'persists-in')
    twin.edge('store:result-cache', 'store:postgres', 'persists-in')
    twin.edge('store:artifacts', 'store:postgres', 'indexes-in')
    twin.edge('system:app-shell', 'system:scientific-context', 'projects')
    twin.edge('system:app-shell', 'system:scene', 'owns')
    twin.edge('system:scene', 'external:molstar', 'hosts')
    twin.edge('service:web', 'surface:gui', 'serves')
    twin.edge('service:fields', 'transport:http-v2', 'serves')

    twin.flow('semantic-command', 'Semantic command · one meaning everywhere',
              'All human and machine surfaces converge on the same versioned command contract.', [
                  ('actor:human', 'chooses intent'), ('surface:gui', 'emits command'),
                  ('transport:http-v2', 'carries envelope'), ('system:dispatcher', 'validates actor + schemas'),
                  ('registry:commands', 'resolves handler'), ('system:kernel', 'executes capability')])
    twin.flow('durable-compute', 'Long scientific compute · durable Job',
              'Long work is recorded before execution and returns provenance-bearing artifacts.', [
                  ('actor:agent', 'requests compute'), ('surface:mcp', 'uses generated safe tool'),
                  ('transport:http-v2', 'submits'), ('system:dispatcher', 'enforces required Job'),
                  ('system:invocation', 'identifies request + method version'), ('store:result-cache', 'checks current result'),
                  ('store:jobs', 'creates durable job'), ('system:executor', 'runs workload'),
                  ('store:artifacts', 'stores content by hash'), ('store:jobs', 'records terminal summary')])
    twin.flow('cache-hit', 'Method-current cache hit',
              'Identity includes canonical input and method version; stale science cannot masquerade as current.', [
                  ('system:invocation', 'computes identity'), ('registry:methods', 'supplies method version'),
                  ('store:result-cache', 'finds current entry'), ('store:artifacts', 'resolves immutable bytes'),
                  ('system:dispatcher', 'returns source=db')])
    twin.flow('scientific-context', 'Scientific context and scene continuity',
              'Route transitions project one generation-clock context into one persistent molecular scene.', [
                  ('actor:human', 'navigates deep link'), ('system:app-shell', 'restores workspace + view'),
                  ('system:scientific-context', 'commits one generation'), ('system:scene', 'projects selection'),
                  ('external:molstar', 'renders without plugin reconstruction')])
    twin.flow('provenance', 'Result provenance chain',
              'Mission, Run, Job and artifact identities remain distinct while retaining causal linkage.', [
                  ('object-kind:mission', 'sets objective'), ('object-kind:run', 'records attempt'),
                  ('object-kind:job', 'records execution'), ('object-kind:artifact', 'addresses result'),
                  ('store:postgres', 'retains relations + actors + versions')])


def runtime_snapshot(twin: Twin) -> dict:
    snapshot: dict[str, Any] = {'captured_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                                'source': 'best-effort live probes'}
    ok, sha = run(['git', 'rev-parse', 'HEAD'])
    snapshot['git_commit'] = sha if ok else None
    ok, status = run(['systemctl', '--user', 'show', 'dirac-fields.service', 'dirac-web.service',
                      '-p', 'Id', '-p', 'ActiveState', '-p', 'SubState', '-p', 'MainPID', '--no-pager'])
    snapshot['systemd'] = status.splitlines() if ok else {'available': False, 'reason': status}
    ok, ports = run(['ss', '-ltn'])
    snapshot['listening_ports'] = sorted(set(re.findall(r':(1355|1360|8901|8902)\b', ports))) if ok else []
    try:
        with urllib.request.urlopen('http://127.0.0.1:8901/v2/commands', timeout=3) as response:
            payload = json.load(response)
        commands = payload.get('data', payload)
        snapshot['http_v2_command_count'] = len(commands.get('commands', commands)) if isinstance(commands, dict) else len(commands)
        snapshot['http_v2_reachable'] = True
    except Exception as exc:  # noqa: BLE001
        snapshot['http_v2_reachable'] = False
        snapshot['http_v2_reason'] = str(exc)
    query = ("SELECT json_build_object('tables',count(*) FILTER (WHERE c.relkind IN ('r','p'))," 
             "'views',count(*) FILTER (WHERE c.relkind IN ('v','m')),'schemas',count(DISTINCT n.nspname)) "
             "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
             "WHERE n.nspname IN ('app','audit','bio','chem','design','meta');")
    ok, database = run(['psql', '-d', 'dirac', '-Atqc', query])
    snapshot['postgres'] = json.loads(database) if ok and database.startswith('{') else {
        'available': False, 'reason': database}
    for node_id in ('service:fields', 'service:web', 'store:postgres'):
        if node_id in twin.nodes:
            twin.nodes[node_id]['runtime_evidence'] = snapshot
    return snapshot


def validate(twin: Twin, ts_data: dict) -> dict:
    errors: list[str] = []
    if len(ts_data['registries']['workspaces']) != 8:
        errors.append('AppShell must contain exactly 8 workspaces')
    if len(ts_data['registries']['views']) != 30:
        errors.append('AppShell must contain exactly 30 views')
    if len(ts_data['registries']['modules']) != 10:
        errors.append('AppShell must contain exactly 10 composable modules')
    expected = {'command': 17, 'object-kind': 30, 'relation-kind': 17,
                'scientific-method': 12, 'migration': 17}
    counts = Counter(n['type'] for n in twin.nodes.values())
    for kind, wanted in expected.items():
        if counts[kind] != wanted:
            errors.append(f'{kind}: expected {wanted}, got {counts[kind]}')
    for node in twin.nodes.values():
        if node['type'] in ('function', 'method') and not all(k in node for k in ('path', 'line')):
            errors.append(f'function lacks source location: {node["id"]}')
    for edge in twin.edges.values():
        if edge['source'] not in twin.nodes or edge['target'] not in twin.nodes:
            errors.append(f'dangling edge: {edge}')
    for command in (n for n in twin.nodes.values() if n['type'] == 'command'):
        if not any(e['source'] == command['id'] and e['relation'] == 'handled-by'
                   for e in twin.edges.values()):
            errors.append(f'command lacks handler: {command["name"]}')
    if errors:
        raise RuntimeError('Digital Twin invariant failure:\n' + '\n'.join(errors))
    return dict(sorted(counts.items()))


def main() -> None:
    twin = Twin()
    py_symbols = add_python(twin)
    ts_data = add_typescript(twin)
    add_shell(twin)
    add_contracts(twin, py_symbols)
    add_app_shell(twin, ts_data['registries'])
    add_database(twin)
    add_system_and_flows(twin)
    runtime = runtime_snapshot(twin)
    counts = validate(twin, ts_data)
    ok, git_sha = run(['git', 'rev-parse', 'HEAD'])
    source_files = sorted({n['path'] for n in twin.nodes.values() if n.get('path')})
    fingerprint = hashlib.sha256('\n'.join(
        f'{path}:{hashlib.sha256((ROOT / path).read_bytes()).hexdigest()}'
        for path in source_files if (ROOT / path).is_file()).encode()).hexdigest()
    document = {
        'schema': 'https://dirac.local/schemas/software-digital-twin/v1',
        'title': 'Dirac Software Engineering Digital Twin',
        'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'source_commit': git_sha if ok else None,
        'source_fingerprint_sha256': fingerprint,
        'scope': {
            'included': ['all first-party Python, JavaScript, and Shell functions in backend, SDK, tooling, gates, and operations',
                         'all custom TypeScript functions and methods in AppShell, facets, browser chemistry, and Mol* extensions',
                         'command, method, ObjectRef, AppShell, SQL migration, service, store, and information-flow contracts'],
            'boundary': 'Vendored/upstream Mol* and third-party library internals are represented as external systems, not copied function-by-function.',
            'function_definition': 'AST-declared functions, async functions, constructors, accessors, methods, arrows, function expressions, and named Shell functions.',
        },
        'summary': {'nodes': len(twin.nodes), 'edges': len(twin.edges), 'flows': len(twin.flows),
                    'source_files': len(source_files), 'by_type': counts,
                    'typescript_diagnostics': ts_data['diagnostics']},
        'runtime_snapshot': runtime,
        'nodes': sorted(twin.nodes.values(), key=lambda n: n['id']),
        'edges': sorted(twin.edges.values(), key=lambda e: (e['source'], e['relation'], e['target'])),
        'flows': twin.flows,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(document, indent=2, ensure_ascii=False) + '\n')
    template = TEMPLATE.read_text()
    if '__DIRAC_TWIN_DATA__' not in template:
        raise RuntimeError('Digital Twin HTML template lacks data placeholder')
    embedded = json.dumps(document, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    HTML_OUT.write_text(template.replace('__DIRAC_TWIN_DATA__', embedded))
    print(json.dumps({'json': str(JSON_OUT), 'html': str(HTML_OUT),
                      'nodes': len(twin.nodes), 'edges': len(twin.edges),
                      'flows': len(twin.flows), 'counts': counts,
                      'typescript_diagnostics': len(ts_data['diagnostics'])}, indent=2))


if __name__ == '__main__':
    main()
