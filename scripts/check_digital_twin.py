#!/usr/bin/env python3
"""Fail when the committed Architecture Optimization Twin is stale or incoherent."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re

from build_digital_twin import discovered_files


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODEL = ROOT / 'docs' / 'architecture' / 'dirac-digital-twin.json'
HTML = ROOT / 'docs' / 'architecture' / 'dirac-digital-twin.html'


def module_cycles(document: dict) -> list[list[str]]:
    """Return strongly connected import components; a self-import also counts."""
    modules = {node['id'] for node in document.get('nodes', [])
               if node.get('type') == 'module'}
    adjacency = {node: [] for node in modules}
    for edge in document.get('edges', []):
        if (edge.get('relation') == 'imports' and edge.get('source') in modules
                and edge.get('target') in modules):
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


def source_fingerprint(document: dict) -> str:
    paths = sorted({node['path'] for node in document['nodes'] if node.get('path')})
    return hashlib.sha256('\n'.join(
        f'{path}:{hashlib.sha256((ROOT / path).read_bytes()).hexdigest()}'
        for path in paths if (ROOT / path).is_file()).encode()).hexdigest()


def errors(document: dict, html: str) -> list[str]:
    findings: list[str] = []
    ids = [node['id'] for node in document.get('nodes', [])]
    id_set = set(ids)
    if len(ids) != len(id_set):
        findings.append('node IDs are not unique')
    for edge in document.get('edges', []):
        if edge.get('source') not in id_set or edge.get('target') not in id_set:
            findings.append(f'dangling edge: {edge}')
            break
    for flow in document.get('flows', []):
        if any(step.get('node') not in id_set for step in flow.get('steps', [])):
            findings.append(f'dangling flow step: {flow.get("id")}')
    if document.get('source_fingerprint_sha256') != source_fingerprint(document):
        findings.append('source fingerprint drifted; run python3 scripts/build_digital_twin.py')
    current_files = {path.relative_to(ROOT).as_posix() for path in discovered_files()}
    twin_files = {node['path'] for node in document.get('nodes', [])
                  if node.get('type') == 'source-file'}
    if current_files != twin_files:
        findings.append(f'file discovery drifted: missing={sorted(current_files - twin_files)[:5]} '
                        f'extra={sorted(twin_files - current_files)[:5]}')
    counts = document.get('summary', {}).get('by_type', {})
    expected = {
        'command': len(json.loads((ROOT / 'contracts/commands/registry.json').read_text())['commands']),
        'scientific-method': len(list((ROOT / 'contracts/methods').glob('*.json'))),
        'workspace': 8,
        'view': 30,
        'migration': len(list((ROOT / 'backend/db/migrations').glob('*.sql'))),
    }
    for kind, wanted in expected.items():
        if counts.get(kind) != wanted:
            findings.append(f'{kind} count is {counts.get(kind)}, expected {wanted}')
    analysis = document.get('analysis', {})
    product = analysis.get('product_reality', {})
    if product.get('platform_substrate') != 'complete':
        findings.append('platform substrate verdict missing')
    if product.get('product_implementation') != 'partial':
        findings.append('product reality must remain explicitly partial')
    if analysis.get('maturity', {}).get('level') != 'L3':
        findings.append('twin maturity must be explicit and currently L3')
    commands = [node for node in document.get('nodes', []) if node.get('type') == 'command']
    handled = {edge.get('source') for edge in document.get('edges', [])
               if edge.get('relation') == 'handled-by'}
    if missing := sorted(node['id'] for node in commands if node['id'] not in handled):
        findings.append(f'unhandled semantic commands: {missing[:5]}')
    methods = [node for node in document.get('nodes', [])
               if node.get('type') == 'scientific-method']
    implemented = {edge.get('source') for edge in document.get('edges', [])
                   if edge.get('relation') == 'implemented-by'}
    if missing := sorted(node['id'] for node in methods if node['id'] not in implemented):
        findings.append(f'unimplemented scientific methods: {missing[:5]}')
    for owner in ('system:scientific-context', 'system:scene'):
        if ids.count(owner) != 1:
            findings.append(f'architecture must have exactly one owner node {owner}')
    node_types = {node['id']: node.get('type') for node in document.get('nodes', [])}
    forbidden_targets = {'system:invocation', 'system:executor', 'store:jobs',
                         'store:artifacts', 'store:postgres'}
    bypasses = [edge for edge in document.get('edges', [])
                if node_types.get(edge.get('source')) in ('surface', 'transport')
                and edge.get('target') in forbidden_targets]
    if bypasses:
        findings.append(f'adapter bypasses semantic dispatcher: {bypasses[0]}')
    if cycles := module_cycles(document):
        findings.append(f'module import cycle exceeds zero-cycle ratchet: {cycles[0]}')
    metrics = document.get('runtime_snapshot', {}).get('operational_metrics', {})
    if int(metrics.get('command_traces', 0) or 0) < 1:
        findings.append('L3 twin has no durable command trace evidence')
    if not any(node.get('runtime_observation') for node in commands):
        findings.append('L3 twin has no node-keyed command observation')
    if any('/assets/rdkit/' in node.get('path', '') for node in document.get('nodes', [])):
        findings.append('third-party RDKit generated internals leaked into the first-party twin')
    match = re.search(r'<script id="twin-data" type="application/json">([\s\S]*?)</script>', html)
    if not match:
        findings.append('HTML has no embedded twin model')
    else:
        try:
            embedded = json.loads(match.group(1))
            if embedded.get('source_fingerprint_sha256') != document.get('source_fingerprint_sha256'):
                findings.append('HTML embedded model differs from JSON model')
            if embedded.get('summary', {}).get('nodes') != len(document.get('nodes', [])):
                findings.append('HTML embedded node count differs from JSON model')
        except json.JSONDecodeError as exc:
            findings.append(f'HTML embedded model is invalid JSON: {exc}')
    for marker in ('到底做完了什么？', '变更影响模拟', '函数与对象索引'):
        if marker not in html:
            findings.append(f'guided optimization UI is missing {marker!r}')
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args()
    document = json.loads(MODEL.read_text())
    html = HTML.read_text()
    if args.selftest:
        broken = json.loads(json.dumps(document))
        broken['edges'][0]['target'] = 'missing:selftest'
        if not any('dangling edge' in item for item in errors(broken, html)):
            print('FAIL selftest: checker pardoned a dangling edge')
            return 1
        cyclic = json.loads(json.dumps(document))
        cyclic['nodes'].extend([
            {'id': 'module:selftest:a', 'type': 'module', 'name': 'a'},
            {'id': 'module:selftest:b', 'type': 'module', 'name': 'b'},
        ])
        cyclic['edges'].extend([
            {'source': 'module:selftest:a', 'target': 'module:selftest:b',
             'relation': 'imports'},
            {'source': 'module:selftest:b', 'target': 'module:selftest:a',
             'relation': 'imports'},
        ])
        if not any('module import cycle' in item for item in errors(cyclic, html)):
            print('FAIL selftest: checker pardoned a module import cycle')
            return 1
        print('PASS selftest: checker convicted dangling edges and import cycles')
        return 0
    found = errors(document, html)
    if found:
        for item in found:
            print(f'FAIL {item}')
        return 1
    print(f'PASS architecture twin: {len(document["nodes"])} nodes, '
          f'{len(document["edges"])} edges, source and embedded model agree')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
