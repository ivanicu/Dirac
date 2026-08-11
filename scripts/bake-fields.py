#!/usr/bin/env python3
"""Bake precomputed fields into the static bundle so the deployed site needs no backend.

    backend/env/bin/python scripts/bake-fields.py

WHY THIS EXISTS. ivan.icu is a static site. The fields panel talks to
http://<hostname>:8901, which on a visitor's machine is nothing at all — and
the two daemons must never be exposed publicly, because they are unauthenticated
services that run quantum chemistry on whatever is posted to them. So the
deployed fields are dead unless the answers travel WITH the page.

FORMAT, chosen by measurement rather than habit. One 80³ orbital cube:

    ascii             6.75 MB
    ascii + gzip      2.51 MB   2.7x
    float32           2.05 MB   3.3x
    float32 + gzip    2.00 MB   3.4x
    float16 + gzip    0.57 MB  11.8x   <-

float16 costs 6.09e-05 of absolute error against a peak of 1.805e-01 — 0.034%,
on a field contoured at 0.04. The error is three orders of magnitude below the
isovalue, so it cannot move a surface anyone can see. Twelve cubes come to
6.9 MB, which a static site can carry.

The browser REHYDRATES the ascii cube from the float16 payload and hands it to
the parser that already exists. That is deliberate: a bespoke binary loader
would be a second cube reader, and the one in mol* is proven. Decompression is
DecompressionStream, which is standard, and the whole rehydrate is ~200 ms.

The manifest is keyed on sha256(molfile) — the SAME key the durable cache uses.
Anything else would be a second notion of identity that could disagree with the
first, and the app can compute that hash in the browser with SubtleCrypto.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build' / 'dirac' / 'fields'
MOLFILE_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    '/tmp/claude-1000/-home-ivan/ff9c7c07-88fe-4345-a0cc-59d17b42683e/scratchpad/warm/molfiles')
BACKEND = 'http://127.0.0.1:8901'
KINDS = ['mep', 'mlp', 'mep_qm', 'homo', 'lumo', 'density']


def fetch(molfile: str, kind: str) -> dict | None:
    import urllib.request
    body = json.dumps({'molfile': molfile, 'kind': kind, 'basis': 'sto-3g',
                       'max_seconds': 1800}).encode()
    req = urllib.request.Request(f'{BACKEND}/field', data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=2100) as r:
        out = json.loads(r.read())
    return out if out.get('ok') else None


def split_cube(text: str):
    """Header lines and the value array. The split point is 6 + |natoms|,
    and natoms is NEGATIVE when the file carries orbital indices — taking it
    unsigned would slice the data one line short and shift every value."""
    lines = text.split('\n')
    natoms = int(lines[2].split()[0])
    start = 6 + abs(natoms)
    header = lines[:start]
    values = np.array(' '.join(lines[start:]).split(), dtype=np.float32)
    return header, values


def bake(name: str, molfile: str) -> dict:
    key = hashlib.sha256(molfile.encode()).hexdigest()
    entry: dict = {'molecule': name, 'molfile_sha256': key, 'fields': {}}
    (OUT / key).mkdir(parents=True, exist_ok=True)
    for kind in KINDS:
        try:
            got = fetch(molfile, kind)
        except Exception as e:                                   # noqa: BLE001
            print(f'  {kind:8s} HARNESS {type(e).__name__}: {e}')
            continue
        if got is None:
            # A refusal is a RESULT and is baked as one: the deployed site must
            # be able to say "Gasteiger cannot parameterize Fe" without a
            # backend, rather than falling back to "offline" and blaming the
            # network for a chemistry answer.
            entry['fields'][kind] = {'refused': True}
            print(f'  {kind:8s} refused (baked as a refusal)')
            continue
        header, values = split_cube(got['cube'])
        payload = gzip.compress(values.astype(np.float16).tobytes(), 9)
        (OUT / key / f'{kind}.f16.gz').write_bytes(payload)
        (OUT / key / f'{kind}.header.json').write_text(json.dumps({
            'header': header, 'n_values': int(values.size), 'meta': got['meta'],
        }))
        entry['fields'][kind] = {
            'data': f'fields/{key}/{kind}.f16.gz',
            'header': f'fields/{key}/{kind}.header.json',
            'bytes': len(payload),
        }
        print(f'  {kind:8s} {len(payload) / 1e6:6.2f} MB')
    return entry


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {'version': 1, 'molecules': []}
    total = 0
    for path in sorted(MOLFILE_DIR.glob('*.mol')):
        text = path.read_text()
        if len(text.splitlines()) < 5:
            # RECORDED, not skipped. Five of the ten bundled fixtures carry no
            # deposited small molecule — crambin, B-DNA, GFP (its chromophore is
            # part of the chain), p53-DNA, the MHC assembly. That is a FACT
            # about the structure, and the deployed site should say it. Leaving
            # them out of the manifest would make them indistinguishable from a
            # molecule whose bake failed, and the panel would fall through to
            # "backend offline" — blaming the network for crystallography.
            manifest['molecules'].append({
                'molecule': path.stem, 'molfile_sha256': None,
                'no_ligand': True, 'fields': {},
            })
            print(f'{path.stem}: no deposited ligand (recorded as such)')
            continue
        print(f'{path.stem}:')
        entry = bake(path.stem, text)
        manifest['molecules'].append(entry)
        total += sum(f.get('bytes', 0) for f in entry['fields'].values())
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    print(f'\nbaked {len(manifest["molecules"])} molecules, '
          f'{total / 1e6:.1f} MB total -> {OUT}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
