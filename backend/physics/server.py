#!/usr/bin/env python3
"""Dirac physics backend — surface electrostatics and torsional strain.

    backend/env/bin/python backend/physics/server.py     # 0.0.0.0:8902 (LAN-reachable, unauthenticated)

Deliberately a SECOND daemon rather than new routes on field_server.py: that
file belongs to the Field Wells workstream and is under active edit, and one
shared tree has already lost work to concurrent whole-file writes. The physics
lives in importable modules (`mep_surface`, `torsion`), so whoever wants one
process later merges by importing, not by copying.

Protocol (JSON in, JSON out; errors arrive as 200 with ok:false, matching the
fields backend so the front end has one error path):

  GET  /health
  POST /surface/mep       {molfile, basis?, xc?, isovalue?, points_per_atom?, max_seconds?}
       → {ok, points_b64, values_b64, n_points, extrema, meta}
  POST /surface/mep_at    {molfile, points | points_b64, basis?}
       → {ok, values_b64, meta}
  POST /torsion/strain    {molfile, steps?, relax_hydrogens?, max_torsions?, variant?}
       → {ok, torsions, total_strain_kcal, total_verdict, meta}

Binary payloads are base64 little-endian float32: `points_b64` is xyz triples
in Ångström in the molfile's own frame, `values_b64` one potential per point
in kcal/mol. JSON numbers would triple the size of a 10 000-point cloud for no
added precision.

`/surface/mep_at` exists so the front end can colour mol*'s OWN molecular
surface: mol* builds a better surface than this module should duplicate, and
the wavefunction stays where the wavefunction is.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from physics.mep_surface import (DEFAULT_BASIS, DEFAULT_MAX_SECONDS,   # noqa: E402
                                 compute_surface_mep, mep_at_points)
from physics.torsion import compute_torsion_strain                   # noqa: E402

PORT = 8902
# Bound to all interfaces, not loopback: Ivan drives this from a Mac on the LAN
# and a 127.0.0.1-only daemon reports itself as simply "offline" there, which is
# how the fields backend was found broken. The consequence is stated rather than
# discovered: this is an UNAUTHENTICATED endpoint that runs quantum chemistry and
# RDKit parsing on whatever is posted to it. Fine on a home LAN, not on a hostile
# one — bind HOST to 127.0.0.1 if that ever changes.
HOST = os.environ.get('DIRAC_PHYSICS_HOST', '0.0.0.0')
# A molfile is kilobytes. Anything past this is a mistake or an attempt to wedge
# the box, and either way it should be refused before it is read into memory.
MAX_BODY_BYTES = 8 * 1024 * 1024


# pyscf will happily accept an arbitrary string here and fail deep inside a
# basis parser, or worse, silently pick something. The classical fields daemon
# already whitelists; this route did not, and it is the one bound to 0.0.0.0.
ALLOWED_BASIS = {'sto-3g', '6-31g', '6-31g*', 'def2-svp', 'def2-tzvp'}


def validated_basis(name: str) -> str:
    key = str(name).strip().lower()
    if key not in ALLOWED_BASIS:
        raise ValueError(
            f'basis {name!r} is not one of {sorted(ALLOWED_BASIS)}. '
            f'Note 6-31g has no Br/I/Se/As and 6-31g* has no I — def2-svp is '
            f'the one with full coverage for halogenated ligands.')
    return key


def b64(array: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(array, dtype='<f4').tobytes()).decode()


def unb64(text: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype='<f4').reshape(-1, 3)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path != '/health':
            self._send(404, {'ok': False, 'error': 'not found'})
            return
        import pyscf
        import rdkit
        self._send(200, {'ok': True, 'service': 'dirac-physics',
                         'rdkit': rdkit.__version__, 'pyscf': pyscf.__version__,
                         'endpoints': ['/surface/mep', '/surface/mep_at', '/torsion/strain']})

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length > MAX_BODY_BYTES:
                self._send(200, {'ok': False,
                                 'error': f'request body {length} bytes exceeds the '
                                          f'{MAX_BODY_BYTES} byte limit'})
                return
            req = json.loads(self.rfile.read(length)) if length else {}
            t0 = time.time()

            if self.path == '/surface/mep':
                out = compute_surface_mep(
                    req['molfile'],
                    basis=validated_basis(req.get('basis', DEFAULT_BASIS)),
                    isovalue=float(req.get('isovalue', 0.001)),
                    points_per_atom=int(req.get('points_per_atom', 120)),
                    max_seconds=req.get('max_seconds', DEFAULT_MAX_SECONDS),
                    xc=req.get('xc'),
                )
                self._send(200, {
                    'ok': True,
                    'points_b64': b64(out['points']),
                    'values_b64': b64(out['values']),
                    'n_points': int(len(out['values'])),
                    'extrema': out['extrema'],
                    'meta': out['meta'],
                })
                print(f"[surface/mep] {out['meta']['n_atoms']} atoms "
                      f"V_S,max {out['meta']['v_s_max_kcal_per_mol']:+.1f} "
                      f"σ-holes {out['meta']['sigma_holes_found']} "
                      f"t={out['meta']['total_seconds']}s", flush=True)

            elif self.path == '/surface/mep_at':
                points = (unb64(req['points_b64']) if 'points_b64' in req
                          else np.asarray(req['points'], dtype=float))
                values, meta = mep_at_points(
                    req['molfile'], points,
                    basis=validated_basis(req.get('basis', DEFAULT_BASIS)),
                    max_seconds=float(req.get('max_seconds', DEFAULT_MAX_SECONDS)))
                meta['total_seconds'] = round(time.time() - t0, 2)
                self._send(200, {'ok': True, 'values_b64': b64(values), 'meta': meta})
                print(f"[surface/mep_at] {len(values)} points t={meta['total_seconds']}s", flush=True)

            elif self.path == '/torsion/strain':
                out = compute_torsion_strain(
                    req['molfile'],
                    steps=int(req.get('steps', 24)),
                    relax_hydrogens=bool(req.get('relax_hydrogens', True)),
                    max_torsions=int(req.get('max_torsions', 12)),
                    variant=req.get('variant', 'MMFF94s'),
                )
                self._send(200, {'ok': True, **out})
                print(f"[torsion/strain] {out['meta']['n_scanned']} torsions "
                      f"total {out['total_strain_kcal']:+.2f} kcal "
                      f"t={out['meta']['seconds']}s", flush=True)

            else:
                self._send(404, {'ok': False, 'error': 'not found'})

        except Exception as exc:                       # noqa: BLE001 — reported, never swallowed
            traceback.print_exc()
            self._send(200, {'ok': False, 'error': str(exc)})

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    # backend/env is a conda env with no pyvenv.cfg, so it reads
    # ~/.local/lib/python3.12/site-packages — where a cupy sits that has no
    # CUDA libraries behind it. Left alone, importing gpu4pyscf fails there and
    # the GPU path degrades to CPU silently. Set before anything imports.
    if not os.environ.get('PYTHONNOUSERSITE'):
        os.environ['PYTHONNOUSERSITE'] = '1'
        os.execv(sys.executable, [sys.executable] + sys.argv)

    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    host = os.environ.get('DIRAC_PHYSICS_HOST', HOST)
    print(f'Dirac physics backend on http://{host}:{port}'
          + ('  (reachable from the LAN — unauthenticated)' if host == '0.0.0.0' else ''),
          flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
