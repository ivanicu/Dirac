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
import queue as queue_mod
import threading
import uuid
from collections import OrderedDict
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
# A queued job is not waited on by a socket, so its ceiling is a resource
# decision rather than a browser's patience. Still bounded: unbounded is
# how one heme click held 22 cores for 36 minutes.
JOB_MAX_SECONDS = 1800.0


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



# ── the job queue: the budget was a UI constraint wearing physics clothes ───
#
# Ivan, on reading a refusal: "75 atoms in def2-svp is 779 basis functions,
# predicted ~202 s against a 90 s budget. Use a smaller basis, raise
# max_seconds, or trim the ligand — 和现在这整个physics面板有什么意义呢"
#
# He is right, and the refusal's three options are the evidence. Every one of
# them is bad:
#   · "use a smaller basis" offers STO-3G, which this same codebase refuses to
#     quote HOMO/LUMO from, and which for a sigma-hole is not merely inaccurate
#     but structurally wrong — no d functions on Cl/Br/S, non-relativistic
#     all-electron I. The panel would be recommending the level of theory its
#     own caveat system disowns.
#   · "raise max_seconds" means sit on a synchronous HTTP request for minutes.
#   · "trim the ligand" means change the molecule to fit the computer.
#
# And 75 atoms is an ORDINARY LEAD COMPOUND. So the panel refused exactly the
# molecules it exists for, and the one decision it was justified by — is Cl→Br
# worth a synthesis — needs def2-SVP precisely on the halogenated compounds a
# medicinal chemist cares about.
#
# The diagnosis: 202 s is not a long quantum chemistry calculation. It is a
# long SYNCHRONOUS HTTP REQUEST. The budget was never a statement about
# physics; it was a statement about how long a browser tab will wait, wearing
# physics clothes. Submit-and-poll makes the wait a fact about the interaction
# instead of a limit on the science, and then a 200 s job is simply a job.
#
# One worker, deliberately: the GPU is one device and two concurrent SCFs
# thrash it. A queue with a depth is honest about that; unlimited threads would
# not be.

JOB_RETENTION = 32          # bounded, LRU — a job store is not a database
_jobs: 'OrderedDict[str, dict]' = OrderedDict()
_jobs_lock = threading.Lock()
_job_queue: 'queue.Queue[str]' = queue_mod.Queue()


def _new_job(kind: str, payload: dict, predicted: float | None) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            'id': job_id, 'kind': kind, 'state': 'queued',
            'submitted_at': time.time(), 'started_at': None, 'finished_at': None,
            'predicted_seconds': predicted, 'cancelled': False,
            'result': None, 'error': None, 'reason': None,
            'queue_position': _job_queue.qsize() + 1,
            '_payload': payload,
        }
        while len(_jobs) > JOB_RETENTION:
            _jobs.popitem(last=False)
    _job_queue.put(job_id)
    return job_id


def _public_job(job: dict) -> dict:
    """Everything except the payload and the result blob."""
    out = {k: v for k, v in job.items()
           if not k.startswith('_') and k != 'result'}
    now = time.time()
    if job['started_at']:
        out['elapsed_seconds'] = round((job['finished_at'] or now) - job['started_at'], 1)
    else:
        out['waiting_seconds'] = round(now - job['submitted_at'], 1)
    return out


def _worker():
    while True:
        job_id = _job_queue.get()
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is None or job['cancelled']:
                if job is not None:
                    job['state'] = 'cancelled'
                    job['finished_at'] = time.time()
                continue
            job['state'] = 'running'
            job['started_at'] = time.time()
        req = job['_payload']
        try:
            # A queued job answers to the QUEUE's ceiling, not to a socket's
            # patience. The watchdog still bounds it — the point is that the
            # bound is now a resource decision instead of a UI artefact.
            budget = float(req.get('max_seconds', JOB_MAX_SECONDS))
            if job['kind'] == 'surface/mep':
                out = compute_surface_mep(
                    req['molfile'],
                    basis=validated_basis(req.get('basis', DEFAULT_BASIS)),
                    isovalue=float(req.get('isovalue', 0.001)),
                    points_per_atom=int(req.get('points_per_atom', 120)),
                    max_seconds=budget, xc=req.get('xc'))
                result = {'points_b64': b64(out['points']),
                          'values_b64': b64(out['values']),
                          'n_points': int(len(out['values'])),
                          'extrema': out['extrema'], 'meta': out['meta']}
            elif job['kind'] == 'torsion/strain':
                out = compute_torsion_strain(
                    req['molfile'], steps=int(req.get('steps', 24)),
                    relax_hydrogens=bool(req.get('relax_hydrogens', True)),
                    max_torsions=int(req.get('max_torsions', 12)),
                    variant=req.get('variant', 'MMFF94s'))
                result = out
            else:
                raise ValueError(f'unknown job kind {job["kind"]!r}')
            with _jobs_lock:
                job['state'] = 'done'
                job['result'] = result
        except Exception as e:                               # noqa: BLE001
            traceback.print_exc()
            with _jobs_lock:
                job['state'] = 'cancelled' if job['cancelled'] else 'failed'
                job['error'] = str(e)
                job['reason'] = ('cancelled' if job['cancelled'] else
                                 'unsupported' if isinstance(e, (ValueError, KeyError))
                                 else 'internal')
        finally:
            with _jobs_lock:
                job['finished_at'] = time.time()
            print(f"[job {job_id}] {job['kind']} {job['state']} "
                  f"in {job['finished_at'] - (job['started_at'] or job['finished_at']):.1f}s",
                  flush=True)


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
        # A page served from a public origin — https://ivan.icu/dirac, say — reaching a
        # daemon on the visitor's own loopback is Local Network Access, and Chrome gates
        # it twice: the browser asks the USER for permission, and the server must opt in
        # on the preflight. Without this header the request fails even after the user
        # grants it. Measured from the deployed page before it was added, Chrome's own
        # words: "Permission was denied for this request to access the `loopback`
        # address space", corsError LocalNetworkAccessPermissionDenied.
        #
        # Both spellings are sent on purpose: the header was renamed between the
        # Private Network Access draft and the Local Network Access one that shipped,
        # and which name a given Chrome build honours is not worth branching on.
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Access-Control-Allow-Local-Network', 'true')
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/jobs/'):
            job_id = self.path.split('/jobs/', 1)[1].strip('/')
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job is None:
                    # NOT_FOUND is distinct from "still queued". A job store
                    # that has evicted a result must not answer like one that
                    # never had it.
                    self._send(200, {'ok': False, 'reason': 'not_found',
                                     'error': f'no job {job_id} — it may have '
                                              f'been evicted (the store keeps '
                                              f'the last {JOB_RETENTION})'})
                    return
                public = _public_job(job)
                done = job['state'] == 'done'
                result = job['result'] if done else None
                failed = job['state'] in ('failed', 'cancelled')
                err, reason = job['error'], job['reason']
            if done:
                self._send(200, {'ok': True, 'job': public, **result})
            elif failed:
                self._send(200, {'ok': False, 'job': public, 'error': err,
                                 'reason': reason})
            else:
                self._send(200, {'ok': True, 'job': public, 'pending': True})
            return
        if self.path != '/health':
            self._send(404, {'ok': False, 'error': 'not found'})
            return
        import pyscf
        import rdkit
        self._send(200, {'ok': True, 'service': 'dirac-physics',
                         'rdkit': rdkit.__version__, 'pyscf': pyscf.__version__,
                         'endpoints': ['/surface/mep', '/surface/mep_at',
                                       '/torsion/strain',
                                       'POST /jobs/surface/mep',
                                       'POST /jobs/torsion/strain',
                                       'GET /jobs/<id>', 'POST /jobs/<id>/cancel'],
                         'queue_depth': _job_queue.qsize(),
                         'jobs_retained': len(_jobs)})

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

            if self.path.startswith('/jobs/') and self.path.endswith('/cancel'):
                job_id = self.path[len('/jobs/'):-len('/cancel')].strip('/')
                with _jobs_lock:
                    job = _jobs.get(job_id)
                    if job is None:
                        self._send(200, {'ok': False, 'reason': 'not_found',
                                         'error': f'no job {job_id}'})
                        return
                    job['cancelled'] = True
                    if job['state'] == 'queued':
                        job['state'] = 'cancelled'
                        job['finished_at'] = time.time()
                    state = job['state']
                # A running job stops at its next SCF cycle: the watchdog is
                # already per-cycle, so cancellation rides the guard that
                # exists rather than needing a second mechanism.
                self._send(200, {'ok': True, 'state': state})
                return

            if self.path in ('/jobs/surface/mep', '/jobs/torsion/strain'):
                kind = self.path[len('/jobs/'):]
                predicted = None
                if kind == 'surface/mep':
                    # Predict BEFORE queueing so the caller sees the cost up
                    # front — but do not refuse on it. Refusing was the whole
                    # complaint: the number is advice now, not a gate.
                    try:
                        from physics.mep_surface import (estimated_scf_seconds,
                                                         GPU_CROSSOVER_NAO,
                                                         GPU_SPEEDUP, nao_for)
                        nao = nao_for(req['molfile'],
                                      validated_basis(req.get('basis', DEFAULT_BASIS)))
                        predicted = round(
                            estimated_scf_seconds(nao)
                            / (GPU_SPEEDUP if nao >= GPU_CROSSOVER_NAO else 1.0), 1)
                    except Exception:                        # noqa: BLE001
                        predicted = None
                job_id = _new_job(kind, req, predicted)
                with _jobs_lock:
                    public = _public_job(_jobs[job_id])
                print(f'[job {job_id}] {kind} queued, predicted '
                      f'{predicted if predicted is not None else "?"} s', flush=True)
                self._send(200, {'ok': True, 'job': public})
                return

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
    threading.Thread(target=_worker, daemon=True, name='physics-worker').start()
    ThreadingHTTPServer((host, port), Handler).serve_forever()
