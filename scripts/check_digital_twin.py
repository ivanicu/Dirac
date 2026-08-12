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
    for kind, wanted in {'command': 17, 'scientific-method': 12, 'workspace': 8,
                         'view': 30, 'ui-module': 10, 'migration': 17}.items():
        if counts.get(kind) != wanted:
            findings.append(f'{kind} count is {counts.get(kind)}, expected {wanted}')
    analysis = document.get('analysis', {})
    product = analysis.get('product_reality', {})
    if product.get('platform_substrate') != 'complete':
        findings.append('platform substrate verdict missing')
    if product.get('product_implementation') != 'partial':
        findings.append('product reality must remain explicitly partial')
    if analysis.get('maturity', {}).get('level') != 'L2':
        findings.append('twin maturity must be explicit and currently L2')
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
        print('PASS selftest: checker convicted a dangling edge')
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
