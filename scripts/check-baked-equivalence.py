#!/usr/bin/env python3
"""The baked tier and the live tier must draw the SAME picture.

    backend/env/bin/python scripts/check-baked-equivalence.py

Two sources for one answer is two homes for one fact, which is the defect shape
that cost most of today. The deployed site reads a baked cube; a developer with
the daemon running reads a computed one. If those ever diverge, the site shows
something the author never saw, and nothing on either side would say so.

So they are compared, per voxel, against the ONLY error the bake is allowed to
introduce: float16 quantisation. Anything larger is a real divergence — a stale
bake, a changed grid, a different level of theory — and this gate is what makes
the difference between "the bake is old" and "the bake is wrong" visible.

The tolerance is derived, not chosen. float16 has a 10-bit mantissa, so the
worst relative step is 2^-11 ≈ 4.9e-4; against the field's own peak that is the
largest error quantisation can produce. A tolerance of 1e-3 of peak leaves
headroom for the ascii round-trip and still convicts anything structural.
"""
from __future__ import annotations

import gzip
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BAKE = ROOT / 'build' / 'dirac' / 'fields'
BACKEND = 'http://127.0.0.1:8901'
MOLFILES = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    '/tmp/claude-1000/-home-ivan/ff9c7c07-88fe-4345-a0cc-59d17b42683e/scratchpad/warm/molfiles')
FLOAT16_RELATIVE_STEP = 2 ** -11        # 10-bit mantissa
TOLERANCE_OF_PEAK = 1e-3                # headroom over the ascii round-trip


def live(molfile: str, kind: str):
    body = json.dumps({'molfile': molfile, 'kind': kind, 'basis': 'sto-3g',
                       'max_seconds': 1800}).encode()
    req = urllib.request.Request(f'{BACKEND}/field', data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=2100) as r:
        return json.loads(r.read())


def cube_values(text: str) -> np.ndarray:
    lines = text.split('\n')
    start = 6 + abs(int(lines[2].split()[0]))
    return np.array(' '.join(lines[start:]).split(), dtype=np.float32)


def main() -> int:
    manifest_path = BAKE / 'manifest.json'
    if not manifest_path.exists():
        print(f'no bake at {BAKE} — nothing to compare')
        return 2
    manifest = json.loads(manifest_path.read_text())

    try:
        urllib.request.urlopen(f'{BACKEND}/health', timeout=5).read()
    except Exception as e:                                       # noqa: BLE001
        print(f'HARNESS: no live backend to compare against ({e})')
        return 2

    print(f'{"molecule":9s} {"field":8s} {"max |diff|":>12s} {"of peak":>9s}  verdict')
    failures = 0
    compared = 0
    for entry in manifest['molecules']:
        name = entry['molecule']
        mol_path = MOLFILES / f'{name}.mol'
        if not mol_path.exists():
            print(f'{name:9s} {"-":8s} {"":>12s} {"":>9s}  HARNESS: molfile missing')
            failures += 1
            continue
        molfile = mol_path.read_text()
        for kind, field in entry['fields'].items():
            if field.get('refused'):
                # A baked refusal must still be a refusal live, or the bake is
                # asserting an impossibility the backend no longer agrees with.
                got = live(molfile, kind)
                ok = not got.get('ok')
                print(f'{name:9s} {kind:8s} {"refused":>12s} {"":>9s}  '
                      f'{"ok" if ok else "DIVERGED — live now SUCCEEDS"}')
                failures += (not ok)
                compared += 1
                continue
            baked = np.frombuffer(
                gzip.decompress((BAKE / field['data'].split('fields/')[1]).read_bytes()),
                dtype=np.float16).astype(np.float32)
            got = live(molfile, kind)
            if not got.get('ok'):
                print(f'{name:9s} {kind:8s} {"":>12s} {"":>9s}  '
                      f'DIVERGED — baked exists, live REFUSES: {str(got.get("error"))[:40]}')
                failures += 1
                compared += 1
                continue
            fresh = cube_values(got['cube'])
            if fresh.shape != baked.shape:
                print(f'{name:9s} {kind:8s} {"":>12s} {"":>9s}  '
                      f'DIVERGED — grid changed: {fresh.shape} vs baked {baked.shape}')
                failures += 1
                compared += 1
                continue
            peak = float(np.abs(fresh).max()) or 1.0
            diff = float(np.abs(fresh - baked).max())
            rel = diff / peak
            ok = rel <= TOLERANCE_OF_PEAK
            failures += (not ok)
            compared += 1
            print(f'{name:9s} {kind:8s} {diff:12.3e} {rel:9.2e}  '
                  f'{"ok" if ok else "DIVERGED beyond float16"}')

    print(f'\nfloat16 worst relative step {FLOAT16_RELATIVE_STEP:.2e}; '
          f'tolerance {TOLERANCE_OF_PEAK:.0e} of peak')
    print(f'{compared} comparisons, {failures} divergent')
    if compared == 0:
        print('NOTHING WAS COMPARED — a green result here would be silence, not a pass')
        return 2
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
