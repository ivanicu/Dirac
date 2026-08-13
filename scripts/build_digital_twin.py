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
from math import log2
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / 'docs' / 'architecture'
JSON_OUT = OUT_DIR / 'dirac-digital-twin.json'
HTML_OUT = OUT_DIR / 'dirac-digital-twin.html'
TEMPLATE = ROOT / 'scripts' / 'digital_twin_template.html'
SCOPE_FILE = ROOT / 'scripts' / 'digital_twin_scope.json'
SCOPE = json.loads(SCOPE_FILE.read_text())


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


def atomic_write(path: pathlib.Path, content: str) -> None:
    temporary = path.with_name(f'.{path.name}.tmp')
    temporary.write_text(content)
    temporary.replace(path)


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


def discovered_files() -> list[pathlib.Path]:
    """Discover tracked and not-yet-tracked first-party files from one ownership policy."""
    ok, raw = run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'])
    if not ok:
        ok, raw = run(['rg', '--files', '-0'])
    if not ok:
        raise RuntimeError(f'cannot discover workspace files: {raw}')
    roots = tuple(item.rstrip('/') for item in SCOPE['include_roots'])
    root_files = set(SCOPE['include_root_files'])
    extensions = set(SCOPE['extensions'])
    auto_extensions = set(SCOPE['auto_include_code_extensions'])
    external_roots = tuple(item.rstrip('/') for item in SCOPE['external_roots'])
    excluded = tuple(SCOPE['exclude_fragments'])
    suffixes = tuple(SCOPE['exclude_suffixes'])
    found = []
    for item in raw.split('\0'):
        if not item:
            continue
        rel = item.replace('\\', '/')
        wrapped = f'/{rel}'
        extension = pathlib.PurePosixPath(rel).suffix
        explicit = rel in root_files or any(rel == root or rel.startswith(f'{root}/')
                                            for root in roots)
        external = any(rel == root or rel.startswith(f'{root}/') for root in external_roots)
        if not explicit and (extension not in auto_extensions or external):
            continue
        if extension not in extensions:
            continue
        if any(fragment in wrapped for fragment in excluded) or rel.endswith(suffixes):
            continue
        path = ROOT / rel
        if path.is_file():
            found.append(path)
    return sorted(set(found))


def python_files() -> list[pathlib.Path]:
    return [path for path in discovered_files() if path.suffix == '.py']


def add_file_inventory(twin: Twin, files: list[pathlib.Path]) -> None:
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        data = path.read_bytes()
        twin.node(f'file:{rel}', 'source-file', path.name, layer=layer_for(rel),
                  path=rel, extension=path.suffix or '<none>', bytes=len(data),
                  sha256=hashlib.sha256(data).hexdigest(),
                  generated=('/generated/' in rel or rel in SCOPE['generated_outputs']))


def add_file_references(twin: Twin, files: list[pathlib.Path]) -> None:
    """Add explicit path references in configs, docs, scripts and source text."""
    known = {path.relative_to(ROOT).as_posix(): path for path in files}
    token_re = re.compile(r'(?<![\w.-])(?:\.?\.?/)?[A-Za-z0-9_@.-]+(?:/[A-Za-z0-9_@.-]+)+')
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(errors='strict')
        except (UnicodeDecodeError, OSError):
            continue
        source = f'file:{rel}'
        for token in set(token_re.findall(text)):
            cleaned = token.rstrip('.,:;)]}\'"')
            candidates = [cleaned.lstrip('./')]
            if cleaned.startswith('.'):
                try:
                    candidates.append((path.parent / cleaned).resolve().relative_to(ROOT).as_posix())
                except ValueError:
                    pass
            for candidate in candidates:
                if candidate in known and candidate != rel:
                    twin.edge(source, f'file:{candidate}', 'references-file')
                    break
    for node in list(twin.nodes.values()):
        rel = node.get('path')
        file_id = f'file:{rel}'
        if node['type'] == 'module' and rel and file_id in twin.nodes:
            twin.edge(file_id, node['id'], 'parsed-as')


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
    def is_shell(path: pathlib.Path) -> bool:
        if path.suffix == '.sh':
            return True
        if path.suffix:
            return False
        try:
            return path.read_text(errors='ignore').startswith(('#!/bin/bash', '#!/usr/bin/env bash',
                                                               '#!/bin/sh', '#!/usr/bin/env sh'))
        except OSError:
            return False

    for path in (path for path in discovered_files() if is_shell(path)):
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
    # Create all View nodes before their dependency edges; plans may point
    # forward to a View declared later in the registry.
    for view in registries['views']:
        twin.node(f'view:{view["id"]}', 'view', view['label'], layer='experience', **view)
    for view in registries['views']:
        vid = twin.node(f'view:{view["id"]}', 'view', view['label'], layer='experience', **view)
        twin.edge(f'workspace:{view["workspace"]}', vid, 'contains')
        for command in view.get('actions', []):
            twin.edge(vid, f'command:{command}', 'offers')
        for kind in view.get('primaryObjectKinds', []):
            twin.edge(vid, f'object-kind:{kind}', 'projects')
        plan = registries.get('plans', {}).get(view['id'], {})
        twin.nodes[vid]['plan_contract'] = plan
        for kind in plan.get('plannedInputs', []):
            twin.edge(f'object-kind:{kind}', vid, 'planned-input', state='planned',
                      declared_in='src/app/shell/workspace-plans.ts')
        for name in plan.get('plannedReadModels', []):
            rid = twin.node(f'planned-read-model:{name}', 'planned-read-model', name,
                            layer='experience', state='planned',
                            path='src/app/shell/workspace-plans.ts')
            twin.edge(vid, rid, 'plans-read-model', state='planned')
        for name in plan.get('plannedCommands', []):
            existing = f'command:{name}'
            cid = existing if existing in twin.nodes else twin.node(
                f'planned-command:{name}', 'planned-command', name, layer='commands',
                state='planned', path='src/app/shell/workspace-plans.ts')
            twin.edge(vid, cid, 'plans-command', state='planned')
        for kind in plan.get('emitsSelection', []):
            twin.edge(vid, f'object-kind:{kind}', 'plans-selection', state='planned')
        for target in plan.get('dependsOnViews', []):
            twin.edge(vid, f'view:{target}', 'plans-dependency', state='planned')
        for requirement in plan.get('requiredProvenance', []):
            key = re.sub(r'[^a-z0-9]+', '-', requirement.lower()).strip('-')
            pid = twin.node(f'provenance-requirement:{key}', 'provenance-requirement',
                            requirement, layer='evidence', state='planned')
            twin.edge(vid, pid, 'requires-provenance', state='planned')
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
        ('service:twin-watcher', 'service', 'dirac-digital-twin.service', 'runtime'),
        ('system:file-discovery', 'system', 'First-party source discovery', 'tooling'),
        ('store:twin-model', 'store', 'Architecture Twin JSON + HTML', 'tooling'),
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
    twin.edge('system:app-shell', 'system:scientific-context', 'projects',
              basis='declared', declared_in='src/app/shell/app-shell.ts')
    twin.edge('system:app-shell', 'system:scene', 'coordinates-without-owning',
              basis='declared', declared_in='src/app/shell/app-shell.ts')
    twin.edge('system:scene', 'external:molstar', 'hosts')
    twin.edge('service:web', 'surface:gui', 'serves')
    twin.edge('service:fields', 'transport:http-v2', 'serves')
    twin.edge('service:twin-watcher', 'system:file-discovery', 'runs')
    twin.edge('system:file-discovery', 'store:twin-model', 'regenerates')

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
    twin.flow('program-reference-loop', 'Program reference-job scientific loop',
              'A guided custody and decision tour of the Program aggregate: canonical identities stay unique '
              'while versioned evidence moves from scope through make/test/structure review and back into a '
              'governed decision snapshot. Steps show the information lineage, not a mandatory linear UI wizard.', [
                  ('actor:human', 'owns the scientific intent'),
                  ('object-kind:program', 'governs one drug-discovery aggregate'),
                  ('object-kind:target', 'defines the intervention target'),
                  ('object-kind:disease', 'anchors the disease context'),
                  ('object-kind:compound', 'reuses one canonical chemical entity'),
                  ('object-kind:substance_registration', 'governs substance identity'),
                  ('object-kind:batch', 'materializes a manufactured lot'),
                  ('object-kind:sample', 'tracks the aliquot and custody'),
                  ('object-kind:work_item', 'carries one durable job across stages'),
                  ('object-kind:protocol_version', 'pins the executed method'),
                  ('object-kind:experiment', 'records the scientific execution'),
                  ('object-kind:measurement', 'captures sample-level observations'),
                  ('object-kind:dataset_version', 'commits an immutable data release'),
                  ('object-kind:structure_observation', 'registers structure evidence'),
                  ('object-kind:annotation', 'adds collaborative interpretation'),
                  ('object-kind:review', 'records governed scientific review'),
                  ('object-kind:analysis_snapshot', 'freezes reproducible analysis'),
                  ('object-kind:external_evidence_release', 'pins an imported source release'),
                  ('object-kind:external_evidence_record', 'links external target-disease evidence'),
                  ('object-kind:stage_gate', 'evaluates explicit criteria'),
                  ('object-kind:decision', 'records the accountable outcome'),
                  ('object-kind:program_snapshot', 'returns the evidence state to the Program')])
    twin.flow('source-sync', 'Automatic source synchronization',
              'Every in-scope first-party file change regenerates structural nodes, references, diagnostics and the offline twin.', [
                  ('service:twin-watcher', 'observes recursive first-party roots'),
                  ('system:file-discovery', 'enumerates tracked and untracked files from ownership policy'),
                  ('store:twin-model', 'replaces JSON and embedded HTML atomically at generation completion')])


def runtime_snapshot(twin: Twin) -> dict:
    snapshot: dict[str, Any] = {'captured_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                                'source': 'best-effort live probes',
                                'refresh_policy_seconds': 60,
                                'freshness_state': 'fresh-at-generation'}
    ok, sha = run(['git', 'rev-parse', 'HEAD'])
    snapshot['git_commit'] = sha if ok else None
    ok, status = run(['systemctl', '--user', 'show', 'dirac-fields.service', 'dirac-web.service',
                      'dirac-digital-twin.service',
                      '-p', 'Id', '-p', 'ActiveState', '-p', 'SubState', '-p', 'MainPID', '--no-pager'])
    snapshot['systemd'] = status.splitlines() if ok else {'available': False, 'reason': status}
    snapshot['twin_watcher_active'] = bool(ok and re.search(
        r'Id=dirac-digital-twin\.service\nActiveState=active', status))
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
    metrics_query = """
    WITH states AS (
        SELECT coalesce(json_object_agg(state, n), '{}'::json) value
        FROM (SELECT state::text state, count(*) n FROM app.job GROUP BY state) q
    ), errors AS (
        SELECT coalesce(json_object_agg(error_code, n), '{}'::json) value
        FROM (SELECT error_code::text error_code, count(*) n FROM app.job
              WHERE state='failed' GROUP BY error_code) q
    ), outcomes AS (
        SELECT coalesce(json_object_agg(outcome_class, n), '{}'::json) value
        FROM (SELECT outcome_class::text outcome_class, count(*) n FROM app.job
              WHERE outcome_class IS NOT NULL GROUP BY outcome_class) q
    )
    SELECT json_build_object(
        'jobs_total', (SELECT count(*) FROM app.job),
        'job_states', (SELECT value FROM states),
        'failure_codes', (SELECT value FROM errors),
        'job_outcomes', (SELECT value FROM outcomes),
        'artifacts', (SELECT count(*) FROM app.artifact),
        'cached_results', (SELECT count(*) FROM app.result_cache),
        'registered_method_rows', (SELECT count(*) FROM meta.method),
        'attention_items', (SELECT count(*) FROM app.v_attention),
        'approval_waits', (SELECT count(*) FROM app.run WHERE state='waiting_approval'),
        'command_traces', (SELECT count(*) FROM app.command_trace),
        'observed_commands', (SELECT count(*) FROM app.v_command_observation));
    """
    ok, metrics = run(['psql', '-d', 'dirac', '-Atqc', metrics_query])
    snapshot['operational_metrics'] = json.loads(metrics) if ok and metrics.startswith('{') else {
        'available': False, 'reason': metrics}
    observations_query = """
    SELECT coalesce(json_agg(row_to_json(q)), '[]'::json)
      FROM (
        SELECT command_id::text, command_version, invocation_count, success_count,
               expected_refusal_count, scientific_failure_count,
               operational_failure_count, job_count, cache_hit_count,
               mean_dispatch_seconds, p95_dispatch_seconds,
               first_observed_at, last_observed_at
          FROM app.v_command_observation ORDER BY command_id
      ) q;
    """
    ok, observations = run(['psql', '-d', 'dirac', '-Atqc', observations_query])
    snapshot['command_observations'] = (json.loads(observations)
                                        if ok and observations.startswith('[') else [])
    method_query = """
    SELECT coalesce(json_agg(row_to_json(q)), '[]'::json)
      FROM (
        SELECT method_id::text, count(*) AS invocation_count,
               count(*) FILTER (WHERE outcome_class='success') AS success_count,
               count(*) FILTER (WHERE outcome_class='expected_refusal') AS expected_refusal_count,
               count(*) FILTER (WHERE outcome_class='scientific_failure') AS scientific_failure_count,
               count(*) FILTER (WHERE outcome_class='operational_failure') AS operational_failure_count,
               round(avg(duration_seconds), 6) AS mean_dispatch_seconds
          FROM app.v_command_trace WHERE method_id IS NOT NULL
         GROUP BY method_id ORDER BY method_id
      ) q;
    """
    ok, method_observations = run(['psql', '-d', 'dirac', '-Atqc', method_query])
    snapshot['method_observations'] = (json.loads(method_observations)
                                       if ok and method_observations.startswith('[') else [])
    for row in snapshot['command_observations']:
        node_id = f'command:{row["command_id"]}'
        if node_id in twin.nodes:
            twin.nodes[node_id]['runtime_observation'] = row
    for row in snapshot['method_observations']:
        node_id = f'method:{row["method_id"]}'
        if node_id in twin.nodes:
            twin.nodes[node_id]['runtime_observation'] = row
    if 'system:dispatcher' in twin.nodes:
        twin.nodes['system:dispatcher']['runtime_observation'] = {
            'traces': snapshot.get('operational_metrics', {}).get('command_traces', 0),
            'observed_commands': snapshot.get('operational_metrics', {}).get('observed_commands', 0),
        }
    expected_ports = {'1355', '1360', '8901'}
    actual_ports = set(snapshot['listening_ports'])
    canonical_command_count = sum(node['type'] == 'command' for node in twin.nodes.values())
    snapshot['drift'] = {
        'missing_expected_ports': sorted(expected_ports - actual_ports),
        'unexpected_legacy_port_8902': '8902' in actual_ports,
        'command_registry_matches_runtime': snapshot.get('http_v2_command_count') == canonical_command_count,
        'runtime_healthy': (not expected_ports - actual_ports
                            and '8902' not in actual_ports
                            and snapshot.get('http_v2_command_count') == canonical_command_count),
    }
    for node_id in ('service:fields', 'service:web', 'service:twin-watcher',
                    'store:postgres', 'store:twin-model'):
        if node_id in twin.nodes:
            twin.nodes[node_id]['runtime_evidence'] = snapshot
    return snapshot


def architecture_analysis(twin: Twin, ts_data: dict, runtime: dict) -> dict:
    """Derive optimization signals; keep every heuristic explicit and inspectable."""
    incoming: Counter[str] = Counter()
    outgoing: Counter[str] = Counter()
    boundary: Counter[str] = Counter()
    call_edges = [e for e in twin.edges.values() if e['relation'] == 'calls']
    for edge in call_edges:
        incoming[edge['target']] += 1
        outgoing[edge['source']] += 1
        source = twin.nodes[edge['source']]
        target = twin.nodes[edge['target']]
        if source.get('layer') != target.get('layer'):
            boundary[edge['source']] += 1
            boundary[edge['target']] += 1

    function_rows = []
    for node in twin.nodes.values():
        if node['type'] not in ('function', 'method'):
            continue
        lines = max(1, int(node.get('end_line', node.get('line', 1))) - int(node.get('line', 1)) + 1)
        raw = (2 * log2(1 + incoming[node['id']]) + log2(1 + outgoing[node['id']])
               + min(lines / 80, 5) + .75 * log2(1 + boundary[node['id']]))
        function_rows.append({
            'node': node['id'], 'name': node.get('qualified_name', node['name']),
            'path': node.get('path'), 'line': node.get('line'), 'layer': node.get('layer'),
            'lines': lines, 'fan_in': incoming[node['id']], 'fan_out': outgoing[node['id']],
            'boundary_calls': boundary[node['id']], 'raw_score': raw,
        })
    optimization_rows = [row for row in function_rows
                         if row['layer'] not in ('verification', 'tooling')
                         and '/assets/' not in (row['path'] or '')]
    max_score = max((row['raw_score'] for row in optimization_rows), default=1)
    for row in function_rows:
        row['change_risk_score'] = round(row.pop('raw_score') / max_score * 100)
    hotspots = sorted(optimization_rows,
                      key=lambda r: (-r['change_risk_score'], -r['lines'], r['node']))[:20]
    largest = sorted(optimization_rows, key=lambda r: (-r['lines'], r['node']))[:20]

    module_rows = []
    by_path: dict[str, list[dict]] = defaultdict(list)
    for row in function_rows:
        if row['path']:
            by_path[row['path']].append(row)
    for path, rows in by_path.items():
        module_rows.append({
            'path': path, 'layer': rows[0]['layer'], 'functions': len(rows),
            'lines_in_functions': sum(r['lines'] for r in rows),
            'fan_in': sum(r['fan_in'] for r in rows),
            'fan_out': sum(r['fan_out'] for r in rows),
            'boundary_calls': sum(r['boundary_calls'] for r in rows),
            'max_change_risk': max(r['change_risk_score'] for r in rows),
        })
    module_hotspots = sorted(
        [row for row in module_rows if row['layer'] not in ('verification', 'tooling')
         and '/assets/' not in row['path']],
        key=lambda r: (-(r['functions'] + r['boundary_calls'] + r['lines_in_functions'] / 100), r['path']))[:15]

    workspaces = ts_data['registries']['workspaces']
    views = ts_data['registries']['views']
    platform_commands = {e['source'] for e in twin.edges.values() if e['relation'] == 'handled-by'}
    platform_methods = {e['source'] for e in twin.edges.values() if e['relation'] == 'implemented-by'}
    platform_complete = (len(platform_commands) == sum(n['type'] == 'command' for n in twin.nodes.values())
                         and len(platform_methods) == sum(n['type'] == 'scientific-method'
                                                         for n in twin.nodes.values())
                         and not module_import_cycles(twin))
    product = {
        'platform_substrate': 'complete' if platform_complete else 'partial',
        'platform_substrate_evidence': {
            'handled_commands': len(platform_commands),
            'implemented_methods': len(platform_methods),
            'import_cycles': len(module_import_cycles(twin)),
        },
        'product_shell': 'complete',
        'product_implementation': 'partial',
        'workspaces_total': len(workspaces),
        'workspaces_shell_ready': sum(bool(w.get('shellReady')) for w in workspaces),
        'workspaces_implemented': sum(w.get('availability') == 'implemented' for w in workspaces),
        'workspaces_gated': sum(w.get('availability') != 'implemented' for w in workspaces),
        'views_total': len(views),
        'views_shell_ready': sum(bool(v.get('shellReady')) for v in views),
        'views_implemented': sum(bool(v.get('implemented')) for v in views),
        'views_gated': sum(not v.get('implemented') for v in views),
        'workspaces': workspaces,
        'views': views,
        'meaning': ('Every registered Workspace and View has a navigable product shell. Scientific capability remains '
                    'partial; a shell-ready entry is an interface contract, not a shipped scientific workflow.'),
    }
    command_count = sum(n['type'] == 'command' for n in twin.nodes.values())
    handler_count = len({e['source'] for e in twin.edges.values() if e['relation'] == 'handled-by'})
    method_count = sum(n['type'] == 'scientific-method' for n in twin.nodes.values())
    implemented_methods = len({e['source'] for e in twin.edges.values()
                               if e['relation'] == 'implemented-by'})
    operational = runtime.get('operational_metrics', {})
    failures = operational.get('failure_codes', {}) if isinstance(operational, dict) else {}
    expected_refusals = sum(int(failures.get(code, 0))
                            for code in ('PARSE', 'UNSUPPORTED', 'BUDGET',
                                         'UNPARAMETERIZED', 'TOO_LARGE'))
    internal_failures = int(failures.get('INTERNAL', 0))
    scientific_failures = int(failures.get('UNCONVERGED', 0))
    attention = int(operational.get('attention_items', 0)) if isinstance(operational, dict) else 0
    approval_waits = int(operational.get('approval_waits', 0)) if isinstance(operational, dict) else 0
    command_traces = int(operational.get('command_traces', 0)) if isinstance(operational, dict) else 0
    observed_commands = int(operational.get('observed_commands', 0)) if isinstance(operational, dict) else 0
    attention_is_actionable = attention == internal_failures + scientific_failures + approval_waits
    import_cycles = module_import_cycles(twin)

    checks = [
        {'id': 'command-handler-coverage', 'status': 'pass' if command_count == handler_count else 'fail',
         'label': 'Command → handler coverage', 'value': f'{handler_count}/{command_count}',
         'why': 'Every semantic command must resolve without transport-specific business logic.'},
        {'id': 'method-implementation-coverage', 'status': 'pass' if method_count == implemented_methods else 'fail',
         'label': 'Method → implementation coverage', 'value': f'{implemented_methods}/{method_count}',
         'why': 'A registered scientific method must map to inspectable source.'},
        {'id': 'runtime-topology', 'status': 'pass' if runtime.get('drift', {}).get('runtime_healthy') else 'fail',
         'label': 'Expected runtime topology', 'value': 'aligned' if runtime.get('drift', {}).get('runtime_healthy') else 'drift',
         'why': 'The observed ports and command count must match the intended deployment.'},
        {'id': 'source-auto-sync', 'status': 'pass' if runtime.get('twin_watcher_active') else 'warn',
         'label': 'Architecture source auto-sync',
         'value': f'{sum(n["type"] == "source-file" for n in twin.nodes.values())} files',
         'why': 'The watcher must be active and every discovered first-party file must have an inventory node.'},
        {'id': 'product-reality', 'status': 'warn', 'label': 'Product capability coverage',
         'value': f'{product["views_implemented"]}/{product["views_total"]} views',
         'why': 'Registry completeness is not implementation completeness.'},
        {'id': 'product-shell',
         'status': 'pass' if product['views_shell_ready'] == product['views_total'] else 'fail',
         'label': 'Navigable product shell',
         'value': f'{product["views_shell_ready"]}/{product["views_total"]} views',
         'why': 'Every declared View must have a stable route and an honest human-readable shell.'},
        {'id': 'attention-quality', 'status': 'pass' if attention_is_actionable else 'fail',
         'label': 'Attention signal quality', 'value': f'{attention} items',
         'why': ('Attention contains only operational/scientific failures and approval waits; '
                 'expected refusals remain queryable without becoming incidents.')},
        {'id': 'dynamic-observation', 'status': 'pass' if command_traces else 'warn',
         'label': 'Node-keyed execution telemetry',
         'value': f'{command_traces} traces · {observed_commands}/{command_count} commands observed',
         'why': 'Persistent traces bind real outcomes and latency to command and method nodes.'},
        {'id': 'architecture-fitness', 'status': 'pass' if not import_cycles else 'fail',
         'label': 'Architecture fitness ratchets',
         'value': f'{len(import_cycles)} import cycles',
         'why': ('Gate 14 enforces command/method coverage, adapter boundaries, singleton '
                 'state owners and a zero module-cycle ratchet.')},
    ]

    findings = [
        {'id': 'attention-semantics', 'priority': 'DONE', 'kind': 'signal-quality',
         'title': 'Attention now separates expected refusals from actionable failures',
         'evidence': (f'{expected_refusals} historical expected refusals remain in the Job ledger while Attention '
                      f'contains {attention} actionable items: {internal_failures} operational, '
                      f'{scientific_failures} scientific and {approval_waits} approval waits.'),
         'impact': ('Operators see a ranked intervention queue without losing scientific refusal history, and every '
                    'new Job carries actor, command and request identity.'),
         'action': 'Keep app.classify_job_outcome and app.v_attention under migration and contract gates.',
         'nodes': ['store:jobs', 'db:view:app.v_attention', 'system:dispatcher']},
        {'id': 'trace-fidelity', 'priority': 'DONE', 'kind': 'twin-fidelity',
         'title': 'Observed command traces are bound to architecture nodes',
         'evidence': (f'{command_traces} durable traces currently cover {observed_commands}/{command_count} commands; '
                      'linked Jobs resolve to their eventual terminal outcome.'),
         'impact': 'Command and method inspectors now expose empirical traffic, latency, cache and failure-path data.',
         'action': 'Let coverage accumulate from real use; do not manufacture traffic merely to turn coverage green.',
         'nodes': ['system:dispatcher', 'system:invocation', 'store:jobs']},
        {'id': 'fitness-gates', 'priority': 'DONE', 'kind': 'architecture-control',
         'title': 'Twin invariants are enforced as architecture fitness functions',
         'evidence': (f'Gate 14 verifies {command_count}/{command_count} handled commands, '
                      f'{method_count}/{method_count} implemented methods and {len(import_cycles)} import cycles.'),
         'impact': 'A future adapter bypass, duplicate owner or import cycle now fails the same gate that checks twin freshness.',
         'action': 'Keep each new invariant paired with a positive control that proves the checker can convict.',
         'nodes': ['registry:commands', 'registry:methods', 'system:scientific-context', 'system:scene']},
        {'id': 'product-scope', 'priority': 'SCOPE', 'kind': 'product-reality',
         'title': 'Shell completeness and capability completeness are separate facts',
         'evidence': (f'{product["workspaces_shell_ready"]}/{product["workspaces_total"]} Workspace shells and '
                      f'{product["views_shell_ready"]}/{product["views_total"]} View shells are navigable; '
                      f'{product["workspaces_implemented"]}/{product["workspaces_total"]} Workspaces and '
                      f'{product["views_implemented"]}/{product["views_total"]} Views have connected capability.'),
         'impact': ('The full product can be walked and optimized without presenting planned scientific modules as '
                    'working software.'),
         'action': 'Keep shell-ready, capability-connected and observed usage as three independent dimensions.',
         'nodes': ['system:app-shell']},
    ]
    source_sync = bool(runtime.get('twin_watcher_active'))
    runtime_sync = command_traces > 0 and isinstance(operational, dict) and not operational.get('available') is False
    maturity_level = 'L3' if source_sync and runtime_sync else 'L2'
    return {
        'maturity': {
            'level': maturity_level,
            'name': ('Observed architecture optimization twin' if maturity_level == 'L3'
                     else 'Source-synchronized architecture twin'),
            'assessment': {
                'source_watcher_observed_active': source_sync,
                'durable_command_traces_present': command_traces > 0,
                'runtime_metrics_available': runtime_sync,
            },
            'is_continuously_synchronized': source_sync,
            'synchronization': ('recursive source events plus periodic runtime refresh; source and telemetry '
                                'freshness are reported separately'),
            'source_sync': 'continuous while dirac-digital-twin.service is active',
            'runtime_sync': 'periodic snapshot every 60 seconds while the watcher is active',
            'can_do': ['explain architecture', 'detect declared/observed drift', 'rank static hotspots',
                       'simulate dependency-radius change impact', 'preserve function-level traceability',
                       'calibrate command latency and outcomes from observed traffic'],
            'cannot_yet_do': ['predict latency or failure probability for unseen inputs',
                              'replay commands without separately retained inputs',
                              'close the optimization loop automatically'],
            'next_level': 'L4 requires validated predictive models and governed optimization actions.',
        },
        'product_reality': product,
        'health_checks': checks,
        'findings': findings,
        'hotspots': hotspots,
        'largest_functions': largest,
        'module_hotspots': module_hotspots,
        'heuristic': {
            'change_risk_score': ('normalized 0–100 from static fan-in (2×), fan-out, function length capped at '
                                  '400 lines, and cross-layer calls; it is a prioritization signal, not failure probability.'),
            'call_graph_limit': 'Static AST resolution undercounts dynamic dispatch, reflection, callbacks and runtime framework wiring.',
        },
    }


def module_import_cycles(twin: Twin) -> list[list[str]]:
    modules = {node_id for node_id, node in twin.nodes.items()
               if node.get('type') == 'module'}
    adjacency = {node: [] for node in modules}
    for edge in twin.edges.values():
        if (edge['relation'] == 'imports' and edge['source'] in modules
                and edge['target'] in modules):
            adjacency[edge['source']].append(edge['target'])
    cursor = 0
    indexes: dict[str, int] = {}
    lows: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal cursor
        indexes[node] = lows[node] = cursor
        cursor += 1
        stack.append(node)
        on_stack.add(node)
        for target in adjacency[node]:
            if target not in indexes:
                visit(target)
                lows[node] = min(lows[node], lows[target])
            elif target in on_stack:
                lows[node] = min(lows[node], indexes[target])
        if lows[node] == indexes[node]:
            component: list[str] = []
            while True:
                item = stack.pop()
                on_stack.remove(item)
                component.append(item)
                if item == node:
                    break
            if len(component) > 1 or node in adjacency[node]:
                found.append(sorted(component))

    for node in sorted(modules):
        if node not in indexes:
            visit(node)
    return sorted(found)


def validate(twin: Twin, ts_data: dict) -> dict:
    errors: list[str] = []
    if len(ts_data['registries']['workspaces']) != 8:
        errors.append('AppShell must contain exactly 8 workspaces')
    if len(ts_data['registries']['views']) != 30:
        errors.append('AppShell must contain exactly 30 views')
    if len(ts_data['registries']['modules']) < 10:
        errors.append('AppShell must contain at least the 10 substrate modules')
    expected = {
        'command': len(json.loads((ROOT / 'contracts/commands/registry.json').read_text())['commands']),
        'object-kind': len(json.loads((ROOT / 'contracts/domain/object-kinds.json').read_text())['kinds']),
        'relation-kind': len(json.loads((ROOT / 'contracts/domain/relations.json').read_text())['relations']),
        'scientific-method': len(list((ROOT / 'contracts/methods').glob('*.json'))),
        'migration': len(list((ROOT / 'backend/db/migrations').glob('*.sql'))),
    }
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
    for method in (n for n in twin.nodes.values() if n['type'] == 'scientific-method'):
        if not any(e['source'] == method['id'] and e['relation'] == 'implemented-by'
                   for e in twin.edges.values()):
            errors.append(f'method lacks implementation: {method["name"]}')
    forbidden_targets = {'system:invocation', 'system:executor', 'store:jobs',
                         'store:artifacts', 'store:postgres'}
    for edge in twin.edges.values():
        if (twin.nodes[edge['source']]['type'] in ('surface', 'transport')
                and edge['target'] in forbidden_targets):
            errors.append(f'adapter bypasses dispatcher: {edge}')
    for owner in ('system:scientific-context', 'system:scene'):
        if sum(node_id == owner for node_id in twin.nodes) != 1:
            errors.append(f'exactly one owner required: {owner}')
    if cycles := module_import_cycles(twin):
        errors.append(f'module import cycles exceed zero-cycle ratchet: {cycles[0]}')
    discovered = {path.relative_to(ROOT).as_posix() for path in discovered_files()}
    inventoried = {node['path'] for node in twin.nodes.values()
                   if node['type'] == 'source-file'}
    if discovered != inventoried:
        errors.append(f'file inventory drift: missing={sorted(discovered - inventoried)[:5]} '
                      f'extra={sorted(inventoried - discovered)[:5]}')
    if errors:
        raise RuntimeError('Digital Twin invariant failure:\n' + '\n'.join(errors))
    return dict(sorted(counts.items()))


def main() -> None:
    twin = Twin()
    files = discovered_files()
    add_file_inventory(twin, files)
    py_symbols = add_python(twin)
    ts_data = add_typescript(twin)
    add_shell(twin)
    add_contracts(twin, py_symbols)
    add_app_shell(twin, ts_data['registries'])
    add_database(twin)
    add_system_and_flows(twin)
    add_file_references(twin, files)
    runtime = runtime_snapshot(twin)
    counts = validate(twin, ts_data)
    analysis = architecture_analysis(twin, ts_data, runtime)
    ok, git_sha = run(['git', 'rev-parse', 'HEAD'])
    source_files = sorted({n['path'] for n in twin.nodes.values() if n.get('path')})
    fingerprint = hashlib.sha256('\n'.join(
        f'{path}:{hashlib.sha256((ROOT / path).read_bytes()).hexdigest()}'
        for path in source_files if (ROOT / path).is_file()).encode()).hexdigest()
    document = {
        'schema': 'https://dirac.local/schemas/software-architecture-twin/v2',
        'title': 'Dirac Architecture Optimization Twin',
        'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'source_base_commit': git_sha if ok else None,
        'source_fingerprint_sha256': fingerprint,
        'scope': {
            'included': ['all first-party Python, JavaScript, and Shell functions in backend, SDK, tooling, gates, and operations',
                         'all custom TypeScript functions and methods in AppShell, facets, browser chemistry, and Mol* extensions',
                         'command, method, ObjectRef, AppShell, SQL migration, service, store, and information-flow contracts'],
            'boundary': 'Vendored/upstream Mol* and third-party library internals are represented as external systems, not copied function-by-function.',
            'function_definition': 'AST-declared functions, async functions, constructors, accessors, methods, arrows, function expressions, and named Shell functions.',
            'discovery_policy': 'scripts/digital_twin_scope.json',
            'automatic_discovery': True,
            'discovered_files': len(files),
        },
        'summary': {'nodes': len(twin.nodes), 'edges': len(twin.edges), 'flows': len(twin.flows),
                    'source_files': len(source_files), 'by_type': counts,
                    'typescript_diagnostics': ts_data['diagnostics']},
        'runtime_snapshot': runtime,
        'analysis': analysis,
        'nodes': sorted(twin.nodes.values(), key=lambda n: n['id']),
        'edges': sorted(twin.edges.values(), key=lambda e: (e['source'], e['relation'], e['target'])),
        'flows': twin.flows,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(JSON_OUT, json.dumps(document, indent=2, ensure_ascii=False) + '\n')
    template = TEMPLATE.read_text()
    if '__DIRAC_TWIN_DATA__' not in template:
        raise RuntimeError('Digital Twin HTML template lacks data placeholder')
    embedded = json.dumps(document, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    atomic_write(HTML_OUT, template.replace('__DIRAC_TWIN_DATA__', embedded))
    print(json.dumps({'json': str(JSON_OUT), 'html': str(HTML_OUT),
                      'nodes': len(twin.nodes), 'edges': len(twin.edges),
                      'flows': len(twin.flows), 'counts': counts,
                      'typescript_diagnostics': len(ts_data['diagnostics'])}, indent=2))


if __name__ == '__main__':
    main()
