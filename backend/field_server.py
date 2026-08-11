#!/usr/bin/env python3
"""Dirac fields backend — 3D energy/potential fields for the focused ligand.

Serves Gaussian-cube scalar fields computed from a ligand molfile POSTed by the
Dirac frontend. The molfile carries the ligand's scene coordinates (Å), so every
cube this server emits is already aligned with the mol* scene.

Field kinds:
  mep      — classical electrostatic potential well: RDKit Gasteiger charges,
             Coulomb sum on a regular grid. Instant, no SCF.
  mep_qm   — quantum electrostatic potential (nuclei + SCF electron density).
  homo     — highest occupied molecular orbital amplitude (pyscf HF).
  lumo     — lowest unoccupied molecular orbital amplitude (pyscf HF).
  density  — SCF electron density.

Protocol (all JSON):
  GET  /health          → {ok, rdkit, pyscf}
  POST /field           → {molfile, kind, basis?} → {ok, cube, meta} | {ok: false, error}

Run:  backend/env/bin/python backend/field_server.py   (binds 0.0.0.0:8901 —
      LAN-reachable and unauthenticated; Host/Origin allowlist only)

The quantum path is honest: convergence status, energies, basis and timing are
reported in meta; a failed SCF returns an error instead of a decorative field.
"""
from __future__ import annotations

import hashlib
import math
import io
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from collections import OrderedDict
from pathlib import Path
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The ops surface, imported defensively at two levels of failure that must NOT
# look alike. A bare import would let a broken admin module kill the compute
# daemon, and a silent `handle_admin = lambda p: None` would make every ops
# route 404 — indistinguishable from "no such route", which is the fallback-
# hides-the-primary's-death shape. So a failed import is REMEMBERED and served
# as a 503 that names the exception, while /field keeps working.
import jobs
from envelope import normalize_meta

# `meta.stored: true` is set when the persist thread is STARTED, not when it
# finishes — Ivan's 自动入库自动缓存 means the render must never wait on the
# database. That is the right trade and it leaves one hole: a write that fails
# after the response has already claimed `stored`. The response cannot be
# corrected retroactively, so the honest fix is to make the failure COUNTABLE
# and visible at /health and /admin, rather than to let it exist only as one
# line in a log nobody tails. A silent persist failure looks exactly like a
# cache that is simply cold — and a cold cache that nobody can distinguish from
# a broken one is how 19 rows sat unservable for a day.
# app.job's writer. Created at db_init (it needs the worker name, which carries
# the derived producer version) and left None when the database is unreachable —
# every call site treats None as "no ledger", never as an error, because a job
# row may not cost a result.
_jobs: 'jobs.JobLedger | None' = None

_persist = {'queued': 0, 'ok': 0, 'failed': 0, 'last_error': None}
_persist_lock = threading.Lock()

_ADMIN_IMPORT_ERROR: str | None = None
try:
    from admin_routes import handle_admin as _handle_admin
except Exception as _exc:                                        # noqa: BLE001
    _ADMIN_IMPORT_ERROR = f'{type(_exc).__name__}: {_exc}'

    def _handle_admin(path: str):
        if not path.startswith('/admin/'):
            return None
        return 503, {'ok': False,
                     'error': {'code': 'DB_UNAVAILABLE',
                               'message': 'the ops module failed to import',
                               'detail': _ADMIN_IMPORT_ERROR,
                               'retryable': True},
                     'meta': {'envelope': 2}}

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem

try:
    import psycopg
except ImportError:      # cache degrades to in-memory only; /health says so
    psycopg = None

PORT = 8901
BOHR = 0.529177210859  # Å per Bohr
MAX_QM_ATOMS = 120     # with hydrogens; STO-3G HF beyond this is not interactive
DEFAULT_BASIS = 'sto-3g'

# ── the wall-clock bound ────────────────────────────────────────────────────
#
# MAX_QM_ATOMS caps SIZE, and size was never what ran away. Measured
# 2026-08-10: one click on HEM (43 heavy atoms — comfortably under the cap)
# held 22 cores for 36 minutes and was still going when it was killed by hand.
# HF cost is O(nao^4) per ITERATION and the iteration count is unbounded, so an
# atom count cannot bound the clock. This does, exactly, from inside the SCF
# loop — a prediction can be wrong, a deadline cannot.
DEFAULT_MAX_SECONDS = 90.0
MAX_MAX_SECONDS = 900.0        # a caller may raise the budget, but not to infinity
SOSCF_MIN_REMAINING = 15.0     # do not start the second-order rescue without room

# ── cube-step cost, MEASURED on this box 2026-08-10, not assumed ────────────
#
# The MEP cube evaluates one-electron potential integrals — cost per grid point
# scales with nao². Fitted on benzene/aspirin/caffeine (nao 36/73/80):
#     cube_s ≈ 2.6 + 7.4e-9 × npoints × nao²      (predicts benzene 3.8 vs 3.6 measured)
#
# Orbital and density cubes are a DIFFERENT story and the honest answer is that
# three points did not establish a law: measured 2.76 / 1.05 / 2.43 s, which is
# not monotonic in nao — aspirin (nao 73) came in faster than benzene (nao 36),
# so what is being timed is mostly fixed overhead and thread warm-up. Rather
# than fit a scaling to noise, the orbital branch carries the LARGEST observed
# coefficient as an upper envelope. It over-predicts by up to ~3x, which is the
# safe direction for a gate whose job is to refuse before burning the clock,
# and the refusal message tells the caller exactly how to overrule it.
CUBE_GRID_MEP = 50             # explicit: the potential is smooth, 80³ costs 4x
CUBE_GRID_ORB = 80             # pyscf's cubegen default for orbital/density
CUBE_MEP_FIXED = 2.6
CUBE_MEP_MARGINAL = 7.4e-9     # seconds per (grid point × nao²)
CUBE_ORB_FIXED = 1.0
CUBE_ORB_MARGINAL = 1.5e-7     # upper envelope, per (grid point × nao)

# Transition metals whose ground state is NOT the closed-shell singlet that
# `nelec % 2` silently assumes. Group 12 (Zn/Cd/Hg) is deliberately absent: d10
# really is a singlet, and Zn sits in a great many drug targets — refusing it
# would be over-refusal. This is the iodine-ECP lesson one element over: the
# wrong answer converges, balances charge, and passes every honesty gate.
OPEN_SHELL_METAL_Z = (set(range(21, 30)) | set(range(39, 48))
                      | set(range(57, 80)) | set(range(89, 104)))
# The whole d and f block, group 12 included. Used only to decide whether salt
# stripping is about to throw away a coordination centre — for THAT question Zn
# counts, because discarding the zinc out of a zinc complex is wrong whether or
# not its ground state is a singlet.
COORDINATION_METAL_Z = (set(range(21, 31)) | set(range(39, 49))
                        | set(range(57, 81)) | set(range(89, 104)))
DB_DSN = 'dbname=dirac user=ivan'
CUBE_MEDIA_TYPE = 'chemical/x-gaussian-cube'
# Bound on POST bodies: the daemon binds 0.0.0.0 now, and an unbounded read
# lets any LAN peer feed it a multi-GB body (peer session's hardening, ported).
MAX_BODY_BYTES = 8 * 1024 * 1024

# OrderedDict, not dict: this is an LRU with a hard ceiling. Six is enough for
# the interaction it exists to serve — the four quantum fields of the molecule
# in front of you, plus the one you just came from.
SCF_CACHE_MAX = 6
_scf_cache: 'OrderedDict[str, dict]' = OrderedDict()
_scf_lock = threading.Lock()


# ── persistent cube cache (app.field_cube / app.blob in the dirac DB) ───────
#
# Key: (sha256(molfile), kind, basis) — the same request served twice costs one
# computation across daemon restarts. The schema enforces the backend's honesty
# rule independently: a quantum row with converged != TRUE is unwritable
# (field_cube CHECK), so the cache cannot resurrect a field the backend would
# refuse to ship. Classical mep rows carry basis='none', scf_reference='none'.

_db_ok = False
_toolkit_ids: dict[str, str] = {}
_producer_id: str | None = None
# method_id -> meta.method row id, filled at startup by method_registry.
# The producer above versions the WHOLE service file; these version one compute
# unit each (migration 007), which is why a comment edit no longer darkens
# every cached SCF. Both are stamped during the transition window.
_method_ids: dict[str, str] = {}

# Bump on ANY behaviour change. meta.register_producer RAISES at startup when
# this version is re-registered with different source — a forgotten bump is a
# loud startup error, never a silently stale cache (design: migration 006).
PRODUCER_SERVICE = 'dirac-fields'
# DERIVED, never typed. This constant was hand-bumped eleven times today and
# the 006 tripwire fired on me three separate times for forgetting it — twice
# while writing the commit that fixed the tripwire. A discipline that fails that
# often is not a discipline, it is a trap with a person standing in it.
#
# WHY THIS IS ONLY SAFE NOW: auto-deriving the version means every source edit
# opens a new producer generation. Before migration 009 that would have been
# catastrophic — the read path filtered on producer currency, so each edit
# emptied the cache (measured: 1 servable row out of 19). 009 moved the read
# path onto METHOD currency, which moves only when a compute unit's source
# moves. So producer identity is now free to be exact, and being exact is what
# makes a forgotten bump impossible rather than merely loud.
#
# The human-readable story lives in PRODUCER_NOTES, which is what a person
# actually wants to read; the version is for machines.
def _producer_version() -> str:
    try:
        src = open(__file__, 'rb').read()
    except OSError:
        return 'unknown'
    return hashlib.sha256(src).hexdigest()[:12]


PRODUCER_VERSION = _producer_version()
PRODUCER_NOTES = ('wall-clock deadline inside the SCF loop + measured cube-cost '
                  'refusal; open-shell d/f metals refused without an explicit '
                  'spin (group 12 exempt); salt stripping refuses to discard a '
                  'coordination metal; iso_suggest = 97th percentile of |field| '
                  'for mep/mlp RETIRED — it was a measurement of the padding (7.6x '
                  'swing on aspirin); fixed physical contour + adaptive box instead; '
                  '_eri dropped and the SCF cache bounded to 6 LRU; '
                  'molblock refusals name the defect; /field/region separates SOURCE '
                  'from FRAME for arbitrary classical atom sets')



def _db(): return psycopg.connect(DB_DSN, autocommit=True)


def canonical_heavy_coords(mol_with_h: Chem.Mol) -> tuple[list[str], np.ndarray]:
    """Heavy atoms in RDKit canonical rank order — the shared ordering that
    conformer_hash_for and the coarse-hit Kabsch correspondence both use."""
    heavy = Chem.RemoveHs(mol_with_h)
    n = heavy.GetNumAtoms()
    ranks = list(Chem.CanonicalRankAtoms(heavy, breakTies=True))
    order = sorted(range(n), key=lambda i: ranks[i])
    conf = heavy.GetConformer()
    coords = np.array([[conf.GetAtomPosition(i).x, conf.GetAtomPosition(i).y,
                        conf.GetAtomPosition(i).z] for i in order])
    syms = [heavy.GetAtomWithIdx(i).GetSymbol() for i in order]
    return syms, coords


def conformer_hash_for(mol_with_h: Chem.Mol) -> tuple[bytes, str]:
    """32-byte conformer identity per the 006 contract.

    Heavy atoms in RDKit canonical rank order → centroid at origin → rotate
    onto principal axes of the (unit-mass) gyration tensor with per-axis sign
    fixed by the third moment — and the third axis is the CROSS PRODUCT of the
    first two, so det = +1 by construction: a mirror image cannot land on the
    same frame, which is what keeps an enantiomer from being served its
    partner's field. Coordinates quantised to 0.01 Å, hashed with the parent
    InChIKey.
    """
    syms, coords = canonical_heavy_coords(mol_with_h)

    x = coords - coords.mean(axis=0)
    _, vecs = np.linalg.eigh(x.T @ x)
    axes = vecs[:, ::-1]                      # largest-variance axis first
    for k in range(2):                        # sign convention on axes 0, 1
        y = x @ axes[:, k]
        m3 = float((y ** 3).sum())
        s = m3 if abs(m3) > 1e-8 else float(y[int(np.argmax(np.abs(y)))])
        if s < 0:
            axes[:, k] = -axes[:, k]
    axes[:, 2] = np.cross(axes[:, 0], axes[:, 1])   # det = +1, always
    y = np.round(x @ axes, 2)
    y[y == 0.0] = 0.0                         # normalise -0.00 → 0.00

    inchikey = Chem.MolToInchiKey(Chem.RemoveHs(mol_with_h))
    payload = inchikey.encode() + b'|' + b';'.join(
        f'{s},{px:.2f},{py:.2f},{pz:.2f}'.encode()
        for s, (px, py, pz) in zip(syms, y))
    return hashlib.sha256(payload).digest(), inchikey


def db_init() -> bool:
    global _db_ok, _producer_id, _method_ids
    if psycopg is None:
        print('[db] psycopg not importable — persistent cache OFF', flush=True)
        return False
    try:
        import pyscf
        import rdkit
        with _db() as conn, conn.cursor() as cur:
            for name, version, note in (
                ('rdkit', rdkit.__version__, 'fields backend Gasteiger MEP'),
                ('pyscf', pyscf.__version__, 'fields backend HF + cubegen'),
            ):
                cur.execute(
                    'INSERT INTO meta.toolkit (name, version, build_note) '
                    'VALUES (%s, %s, %s) ON CONFLICT (name, version) DO NOTHING',
                    (name, version, note))
                cur.execute(
                    'SELECT id FROM meta.toolkit WHERE name = %s AND version = %s',
                    (name, version))
                _toolkit_ids[name] = cur.fetchone()[0]
            source_sha = hashlib.sha256(open(__file__, 'rb').read()).digest()
            cur.execute(
                'SELECT meta.register_producer(%s, %s, %s, %s, %s)',
                (PRODUCER_SERVICE, PRODUCER_VERSION, source_sha,
                 _toolkit_ids['pyscf'], PRODUCER_NOTES))
            _producer_id = cur.fetchone()[0]
        # Compute-unit registration. Kept OUT of this function's body on
        # purpose: the table of which functions can change which number is a
        # fact about the physics, not about the HTTP server, and it lives in
        # method_registry.py so this file's source hash stops being the thing
        # that decides whether a cached SCF is still valid.
        try:
            import method_registry
            _method_ids = method_registry.register_all(_db, sys.modules[__name__],
                                                      _toolkit_ids.get('pyscf'))
            print(f'[db] {len(_method_ids)} compute units registered', flush=True)
            global _jobs
            _jobs = jobs.JobLedger(
                _db, jobs.make_worker_name(os.getpid(), _producer_version()))
            # A restart leaves rows 'running' that no process is doing. Without
            # the reap, v_job_live's age_seconds grows without bound and the
            # ledger reports work nobody is performing — worse than no ledger,
            # because it reads as a hung system.
            reaped = _jobs.reap()
            if any(reaped.values()):
                # Reported per CRITERION, not as one total: "the process died" and
                # "the job overran its ceiling" are different operational facts,
                # and a single number would hide whichever is rarer.
                print(f"[job] reaped {reaped['dead_worker']} row(s) from dead "
                      f"workers and {reaped['overran']} past the hard ceiling",
                      flush=True)
        except Exception as e:
            # A method registry that cannot be written must not take the cache
            # down with it: rows keep their producer stamp, which is what the
            # read path still uses today. Loud, not fatal.
            print(f'[db] method registration failed ({e}) — rows will carry '
                  'producer_id only', flush=True)
            _method_ids = {}
        _db_ok = True
        print(f'[db] persistent cube cache ON (producer {PRODUCER_SERVICE}/'
              f'{PRODUCER_VERSION} — derived from source)', flush=True)
    except Exception as e:
        # TWO DIFFERENT FAILURES WERE WEARING ONE HANDLER, and it cost me the
        # decisive test of the whole method-registry line: I edited this file
        # without bumping PRODUCER_VERSION, register_producer RAISED exactly as
        # migration 006 designed it to, and this handler turned that into
        # "db_cache: off" — so the cache was globally disabled and the test I
        # was running reported a recompute for the wrong reason.
        #
        #   PG unreachable            -> DEGRADE. A recomputable cache being
        #                                unavailable must never take the compute
        #                                path down with it (fail-closed here
        #                                means a suspended laptop bricks fields).
        #   identity conflict (006)   -> EXIT. The whole point is that a
        #                                forgotten bump is LOUD. Serving with a
        #                                stale producer identity is the one thing
        #                                the tripwire exists to prevent, and a
        #                                print() does not prevent it.
        if psycopg is not None and isinstance(e, psycopg.OperationalError):
            print(f'[db] unreachable ({e}) — persistent cache OFF, compute continues',
                  flush=True)
            _db_ok = False
            return _db_ok
        print(f'[db] FATAL: {e}', flush=True)
        print('[db] refusing to start: bump PRODUCER_VERSION for the source change, '
              'or revert it. A stale producer identity would keep serving rows '
              'the new code would not produce.', flush=True)
        raise SystemExit(1)
    return _db_ok


def db_get_cube(molfile_sha: bytes, kind: str, basis: str):
    """Return (cube_text, meta) on a cache hit, else None."""
    if not _db_ok:
        return None
    try:
        with _db() as conn, conn.cursor() as cur:
            # Reads go through the current-producer view (006): a row cached
            # by a since-fixed generation of this service cannot be served.
            cur.execute(
                'SELECT b.bytes, fc.scf_energy_ha, fc.converged, fc.n_atoms, '
                '       fc.n_basis, fc.homo_ev, fc.lumo_ev, fc.seconds, '
                '       app.scf_method_label(fc.scf_reference, fc.scf_converger), '
                '       fc.computed_at '
                # Servable-on-METHOD, not on producer: an edit to a log line
                # used to darken every cached SCF (measured: 1 of 19 rows
                # readable, i.e. every request a forced recompute). 009.
                'FROM app.v_field_cube_servable fc '
                'JOIN app.blob b ON b.sha256 = fc.blob_sha256 '
                'WHERE fc.molfile_sha256 = %s AND fc.kind = %s AND fc.basis = %s '
                'ORDER BY fc.computed_at DESC LIMIT 1',
                (molfile_sha, kind, basis))
            row = cur.fetchone()
        if row is None:
            return None
        cube = bytes(row[0]).decode()
        meta = {'kind': kind, 'cache': 'db', 'computed_at': row[9].isoformat()}
        if row[8] != 'gasteiger':
            meta.update({
                'basis': basis, 'method': row[8], 'converged': row[2],
                'scf_energy_ha': float(row[1]) if row[1] is not None else None,
                'natoms': row[3], 'nbasis': row[4],
                'homo_ev': float(row[5]) if row[5] is not None else None,
                'lumo_ev': float(row[6]) if row[6] is not None else None,
                'scf_seconds': float(row[7]) if row[7] is not None else None,
            })
        else:
            meta.update({'units': 'kcal/mol', 'charges': 'gasteiger', 'method': 'gasteiger'})
        # ONE meta shape for both exits. A cache hit used to return a
        # SMALLER dict than a fresh compute of the same kind, so rows the panel
        # renders per-key ("Net charge", "Compute time") vanished when the
        # answer came from the database — the field was identical, the readout
        # silently lost half its rows, and the faster path looked like the
        # poorer one. Missing keys become None, which SAYS "not recorded";
        # absent keys say nothing and the UI cannot tell the difference.
        # Strict here on purpose: a schema violation on this path costs a
        # recompute, and losing that is the correct price for a loud failure.
        return cube, normalize_meta(meta, source='db')
    except Exception as e:
        print(f'[db] read failed ({e}) — serving from compute', flush=True)
        return None


def db_put_cube(molfile_sha: bytes, kind: str, basis: str, cube: str, meta: dict,
                mol: Chem.Mol | None = None):
    """Persist a computed cube, stamped with this producer generation.
    Quantum rows reach here only if converged — the schema CHECK would reject
    them anyway. The coarse key (compound_id + conformer_hash) is written
    all-or-nothing: it exists only when the compound is registered."""
    if not _db_ok or _producer_id is None:
        return
    try:
        blob = cube.encode()
        blob_sha = hashlib.sha256(blob).digest()
        toolkit = _toolkit_ids['rdkit' if kind == 'mep' else 'pyscf']
        label = meta.get('method', 'gasteiger' if kind == 'mep' else None)
        # DUAL WRITE, deliberately: producer_id stays because the read path and
        # app.v_field_cube_current are keyed on it, method_row_id lands so the
        # finer identity accumulates from today. Cutting over in one step would
        # darken every existing row; this way the transition is a query, not an
        # outage. NULL when registration failed — never a guessed id, because a
        # wrong provenance stamp is worse than an absent one.
        try:
            import method_registry
            method_row = _method_ids.get(method_registry.KIND_TO_METHOD.get(kind, ''))
        except Exception:
            method_row = None

        compound_id = None
        conf_hash = None
        if mol is not None:
            try:
                conf_hash_candidate, inchikey = conformer_hash_for(mol)
            except Exception:
                conf_hash_candidate, inchikey = None, None
        else:
            conf_hash_candidate, inchikey = None, None

        with _db() as conn, conn.cursor() as cur:
            if inchikey:
                cur.execute('SELECT id FROM chem.compound WHERE inchikey = %s',
                            (inchikey,))
                row = cur.fetchone()
                if row:
                    compound_id, conf_hash = row[0], conf_hash_candidate
            cur.execute(
                'INSERT INTO app.blob (sha256, media_type, byte_len, bytes) '
                'VALUES (%s, %s, %s, %s) ON CONFLICT (sha256) DO NOTHING',
                (blob_sha, CUBE_MEDIA_TYPE, len(blob), blob))
            cur.execute(
                'INSERT INTO app.field_cube '
                '  (molfile_sha256, kind, basis, blob_sha256, scf_reference, '
                '   scf_converger, scf_energy_ha, converged, n_atoms, n_basis, '
                '   homo_ev, lumo_ev, seconds, toolkit_id, producer_id, '
                '   compound_id, conformer_hash, method_row_id) '
                'SELECT %s, %s, %s, %s, p.scf_reference, p.scf_converger, '
                '       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s '
                'FROM app.parse_scf_method(%s) AS p '
                'ON CONFLICT ON CONSTRAINT field_cube_exact_key DO NOTHING',
                (molfile_sha, kind, basis, blob_sha,
                 meta.get('scf_energy_ha'),
                 True if kind != 'mep' else None,
                 meta.get('natoms'), meta.get('nbasis'),
                 meta.get('homo_ev'), meta.get('lumo_ev'),
                 meta.get('scf_seconds') if kind != 'mep' else meta.get('total_seconds'),
                 toolkit, _producer_id, compound_id, conf_hash, method_row, label))
        print(f'[db] cached kind={kind} basis={basis} coarse={"yes" if conf_hash else "no"}', flush=True)
        with _persist_lock:
            _persist['ok'] += 1
    except Exception as e:
        # LOUD, and counted. The response for this cube already said stored:true;
        # the only remaining honesty available is that the discrepancy is
        # visible to whoever asks the service how it is doing.
        print(f'[db] WRITE FAILED ({e}) — result was served with stored:true and is '
              f'NOT persisted', flush=True)
        with _persist_lock:
            _persist['failed'] += 1
            _persist['last_error'] = f'{type(e).__name__}: {e}'


# ── molecule import: SMILES/molfile → embedded 3D structure ─────────────────

def embed_molecule(smiles: str | None, molblock: str | None, seed: int = 42):
    """Parse a molecule and give it real 3D coordinates.

    ETKDG + MMFF94 live here because the vendored RDKit-JS wasm has neither
    (verified against the binary) — this endpoint is what makes 'paste a
    SMILES, get the full facet cascade' possible at all.
    """
    if smiles:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError(f'RDKit cannot parse SMILES {smiles!r}')
    elif molblock:
        mol = Chem.MolFromMolBlock(molblock, removeHs=False)
        if mol is None:
            raise ValueError('RDKit cannot parse the molfile')
    else:
        raise ValueError('provide smiles or molfile')

    # Salts and mixtures: keep the largest fragment by heavy-atom count —
    # standard med-chem behavior; the counter-ion is not the SAR object.
    frags = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False)
    stripped = 0
    if len(frags) > 1:
        frags = sorted(frags, key=lambda f: f.GetNumHeavyAtoms(), reverse=True)
        # ...unless what is about to be discarded is a d- or f-block metal, in
        # which case the rule is exactly backwards: the metal IS the object.
        # Measured 2026-08-10 — "CC(=O)[O-].CC(=O)[O-].[Fe+2]" came back as a
        # converged RHF with 23 basis functions, which is one acetate. The
        # iron had been thrown away as the smallest fragment and the field was
        # computed, cached and served for a molecule nobody asked about.
        #
        # Alkali and alkaline-earth counter-ions are deliberately NOT covered:
        # a sodium carboxylate really is a salt and stripping it is right. The
        # line is drawn at the block where the metal is a coordination centre.
        discarded = [a.GetSymbol() for f in frags[1:] for a in f.GetAtoms()
                     if a.GetAtomicNum() in COORDINATION_METAL_Z]
        if discarded:
            raise ValueError(
                f'{"/".join(sorted(set(discarded)))} sits in a separate fragment '
                f'from the rest of the structure. Salt stripping would discard '
                f'the metal and compute the organic fragment alone — and a '
                f'coordination sphere written as disconnected ions has no '
                f'geometry for the embedder to find either. Submit the complex '
                f'with explicit bonds to the metal.'
            )
        mol, stripped = frags[0], len(frags) - 1

    mol = Chem.AddHs(mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    if AllChem.EmbedMolecule(mol, params) != 0:
        # ETKDG can fail on macrocycles/exotics; random-coord fallback is the
        # documented rescue, and MMFF then cleans it up.
        if AllChem.EmbedMolecule(mol, useRandomCoords=True, randomSeed=seed) != 0:
            raise ValueError('3D embedding failed (ETKDG and random-coords)')
    ff_ok = False
    energy = None
    try:
        res = AllChem.MMFFOptimizeMoleculeConfs(mol, maxIters=2000)
        if res and res[0][0] == 0:
            ff_ok = True
        props = AllChem.MMFFGetMoleculeProperties(mol)
        if props is not None:
            ff = AllChem.MMFFGetMoleculeForceField(mol, props)
            if ff is not None:
                energy = float(ff.CalcEnergy())
    except Exception:
        pass  # unparameterized atoms: geometry is still ETKDG-valid

    heavy = Chem.RemoveHs(mol)
    meta = {
        'natoms': mol.GetNumAtoms(),
        'natoms_heavy': heavy.GetNumAtoms(),
        'smiles_canonical': Chem.MolToSmiles(heavy),
        'inchikey': Chem.MolToInchiKey(mol),
        'mmff_optimized': ff_ok,
        'mmff_energy_kcal': energy,
        'embed': 'ETKDGv3',
        'seed': seed,
        'fragments_stripped': stripped,
    }
    # Ship WITHOUT explicit hydrogens: the lab's ligand pipeline (perception,
    # depiction, pharmacophore, fields) expects heavy-atom molfiles exactly
    # like the ones it extracts from deposited structures.
    return Chem.MolToMolBlock(heavy), meta


def describe_bad_molblock(molblock: str) -> str:
    """Say WHAT is wrong with the molblock, not merely that something is.

    'RDKit cannot parse the molfile' is a true sentence that names no cause,
    and it cost a debugging round: RDKit itself had printed 'CTAB version
    string invalid at line 4' to the daemon's stderr while the browser was
    shown the generic line. The caller is usually another part of this app
    building a molblock from scene geometry, and the counts line is where that
    goes wrong — so the counts line is what gets reported.
    """
    lines = molblock.split('\n')
    if not molblock.strip():
        return 'the molfile is empty — no structure was sent'
    if len(lines) < 5:
        return (f'the molfile has only {len(lines)} lines; a molblock needs at '
                f'least four header lines plus an atom block')
    counts = lines[3]
    if 'V2000' not in counts and 'V3000' not in counts:
        return (f'the counts line (line 4) declares no CTAB version: '
                f'{counts.rstrip()!r}. A V2000 molblock must end that line with '
                f'" V2000" — this is a molblock BUILDER bug upstream, not a '
                f'chemistry problem')
    try:
        n_atoms = int(counts[:3])
    except ValueError:
        return (f'the counts line (line 4) has no readable atom count: '
                f'{counts.rstrip()!r}')
    if 'V2000' in counts and n_atoms == 0 and len(lines) > 6:
        return ('the counts line declares 0 atoms while an atom block follows — '
                'V2000 cannot express more than 999 atoms, so a large selection '
                'must be written as V3000')
    return (f'RDKit cannot parse the molfile (counts line: {counts.rstrip()!r}, '
            f'{n_atoms} atoms declared, {len(lines)} lines)')


# ── molfile → RDKit mol with explicit hydrogens, coordinates preserved ──────

def prepare_mol(molblock: str) -> Chem.Mol:
    mol = Chem.MolFromMolBlock(molblock, removeHs=False, sanitize=True)
    if mol is None:
        mol = Chem.MolFromMolBlock(molblock, removeHs=False, sanitize=False)
        if mol is None:
            raise ValueError(describe_bad_molblock(molblock))
        Chem.SanitizeMol(
            mol,
            Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_KEKULIZE,
        )
    mol = Chem.AddHs(mol, addCoords=True)
    return mol


def mol_atoms(mol: Chem.Mol):
    conf = mol.GetConformer()
    syms, coords = [], []
    for atom in mol.GetAtoms():
        p = conf.GetAtomPosition(atom.GetIdx())
        syms.append(atom.GetSymbol())
        coords.append((p.x, p.y, p.z))
    return syms, np.array(coords, dtype=float)


# ── cube writer (standard Gaussian cube, Bohr units) ────────────────────────

def write_cube(origin_a, axes_a, dims, values, syms, coords_a, comment: str) -> str:
    """origin/axes/coords in Å; written in Bohr per the cube convention."""
    pt = Chem.GetPeriodicTable()
    out = io.StringIO()
    out.write('Dirac fields backend\n')
    out.write(comment + '\n')
    o = np.asarray(origin_a) / BOHR
    out.write(f'{len(syms):5d} {o[0]:11.6f} {o[1]:11.6f} {o[2]:11.6f}\n')
    for i in range(3):
        ax = np.asarray(axes_a[i]) / BOHR
        out.write(f'{dims[i]:5d} {ax[0]:11.6f} {ax[1]:11.6f} {ax[2]:11.6f}\n')
    for sym, c in zip(syms, coords_a):
        z = pt.GetAtomicNumber(sym)
        cb = np.asarray(c) / BOHR
        out.write(f'{z:5d} {float(z):11.6f} {cb[0]:11.6f} {cb[1]:11.6f} {cb[2]:11.6f}\n')
    flat = np.asarray(values).reshape(dims[0], dims[1], dims[2])
    for ix in range(dims[0]):
        for iy in range(dims[1]):
            row = flat[ix, iy, :]
            for start in range(0, len(row), 6):
                out.write(''.join(f'{v:13.5e}' for v in row[start:start + 6]) + '\n')
    return out.getvalue()


GRID_MAX_DIM = 128       # 128 cubed ~ 2M voxels
ISO_ENCLOSED_FRACTION = 0.03   # the surface should wrap ~3% of the box


def grid_spacing_meta(lo, hi, dims, requested: float) -> dict:
    """The spacing the grid ACTUALLY has, not the one that was asked for.

    dims is clamped to the 128 voxel cap and `axes` is then recomputed from the
    clamped dims — so past a ~51 A box the grid silently coarsens while meta
    kept reporting the requested 0.4 A. Nobody could see it, because the number
    that was wrong was the number describing the number.

    Invisible today at ligand scale; it fires the moment a selection is a
    pocket, which is the feature being built next. A resolution claim is a
    claim, and it gets measured like one.
    """
    actual = ((hi - lo) / (dims - 1))
    return {
        'spacing_requested': requested,
        'spacing': [round(float(x), 4) for x in actual],
        'grid_capped': bool((dims >= GRID_MAX_DIM).any()),
    }


# ── the isovalue is a PHYSICAL CONSTANT, and the BOX is what adapts ─────────
#
# Three attempts at this, and the third is the one that is actually about the
# molecule:
#
#   1. iso = 0.05 MLP, a constant tuned on aspirin. Measured: clipped the grid
#      wall on all four molecules tried, INCLUDING aspirin. The lipophilicity
#      field rendered as a gold crate.
#   2. iso = 97th percentile of |field|. Never clips — and is a function of
#      `pad`. Measured on aspirin: pad 3->10 A moves iso_suggest 25.5 -> 3.4
#      kcal/mol, a 7.6x swing with the physics IDENTICAL. It is a measurement
#      of the BOX. Worse, it selects the highest-|V| voxels, which are always
#      INTERIOR: 84-94% of the enclosed volume sits inside the vdW surface,
#      where nothing binds and where the softened Coulomb has the wrong sign.
#      And it moves per molecule, so two analogues render equally hot.
#   3. This. The contour is a FIXED value in physical units, identical across
#      molecules and identical between the classical and quantum paths, so the
#      same colour means the same kcal/mol everywhere. The crate was never an
#      isovalue problem — it was a BOX problem — so the box is what adapts:
#      grow the padding until the field on the wall falls below the contour.
#
# The lesson worth keeping: an adaptive RULER destroys comparison, an adaptive
# BOX costs nothing. When a picture clips, widen the frame; do not move the
# ruler and call the result measured.

HARTREE_PER_E_TO_KCAL = 627.5094740631

# Contours a chemist can hold in their head, in the units the quantity is
# quoted in. MEP: +-10 kcal/mol is roughly where a hydrogen-bond-relevant
# feature sits. Both electrostatic paths share it — mep_qm's cube is in Ha/e
# and is converted at the isovalue, so toggling classical/quantum no longer
# silently moves the contour from 18 to 31.4 kcal/mol.
FIXED_ISO = {
    'mep': 10.0,        # kcal/mol
    'mep_qm': 10.0,     # kcal/mol, converted to Ha/e for the cube
    'mlp': 0.25,        # Crippen/Fauchere units
}
# The UI slider multiplies the contour by 10^[-1,1]. The box must therefore be
# sized for the LOWEST contour the slider can reach, not for the default one —
# otherwise the bottom of the slider walks the contour below the box's wall and
# the surface exits through the side, drawn as a flat face. Measured on 1CBS:
# wall 3.75 against a default contour of 10, so truncation began at slider
# -0.43 and the bottom 29% of the range produced cut-off lobes.
#
# Classical fields cost ~0.1 s, so sizing for the floor is free. The quantum
# grid belongs to pyscf's cubegen and cannot be grown this way, so those report
# the wall instead and the UI says the surface is open.
ISO_SLIDER_FLOOR = 0.1
PAD_START = 4.0
PAD_MAX = 12.0
PAD_STEP = 2.0


def wall_max(v: np.ndarray) -> float:
    """The largest |field| anywhere on the six faces of the grid.

    An isosurface only closes if this is BELOW the contour. Above it, the
    surface runs off the edge of the box and is drawn as a flat face — the
    crate. This is the quantity that was never measured while three different
    isovalues were argued about.
    """
    faces = [v[0], v[-1], v[:, 0], v[:, -1], v[:, :, 0], v[:, :, -1]]
    return float(max(np.abs(f).max() for f in faces))


# ── classical MEP (Gasteiger + Coulomb) ─────────────────────────────────────

def field_mep(mol: Chem.Mol, spacing=0.4, pad=4.0):
    AllChem.ComputeGasteigerCharges(mol)
    syms, coords = mol_atoms(mol)
    charges = np.array(
        [float(a.GetProp('_GasteigerCharge')) for a in mol.GetAtoms()], dtype=float
    )
    # Gasteiger returns NaN for atoms it cannot parameterize (hypervalent P,
    # many metals). Silently zeroing them once shipped a perfectly flat "well"
    # for PF6- that rendered as a normal result — a zero field is silence, not
    # a measurement. Refuse and point at the path that actually works.
    bad = ~np.isfinite(charges)
    if bad.any():
        bad_syms = sorted({syms[i] for i in np.where(bad)[0]})
        raise ValueError(
            f'Gasteiger cannot parameterize {"/".join(bad_syms)} — no classical '
            'MEP for this molecule. The QM potential (mep_qm) handles it.')
    if float(np.abs(charges).max()) < 1e-6:
        raise ValueError('all Gasteiger charges are zero — classical MEP would '
                         'be an empty picture; use mep_qm')

    iso = FIXED_ISO['mep'] * ISO_SLIDER_FLOOR

    def evaluate(pad_a: float):
        lo = coords.min(axis=0) - pad_a
        hi = coords.max(axis=0) + pad_a
        dims = np.maximum(np.ceil((hi - lo) / spacing).astype(int) + 1, 8)
        dims = np.minimum(dims, GRID_MAX_DIM)
        xs = np.linspace(lo[0], hi[0], dims[0])
        ys = np.linspace(lo[1], hi[1], dims[1])
        zs = np.linspace(lo[2], hi[2], dims[2])
        gx, gy, gz = np.meshgrid(xs, ys, zs, indexing='ij')
        pts = np.stack([gx, gy, gz], axis=-1)
        vv = np.zeros(tuple(dims), dtype=float)
        for q, c in zip(charges, coords):
            r = np.linalg.norm(pts - c, axis=-1)
            vv += q / np.maximum(r, 0.5)
        return lo, hi, dims, vv * 332.06   # kcal/mol per (e²/Å)

    # Grow the BOX, not the ruler, until the contour closes inside it.
    pad_used = pad
    lo, hi, dims, v = evaluate(pad_used)
    while wall_max(v) >= iso and pad_used < PAD_MAX:
        pad_used = min(pad_used + PAD_STEP, PAD_MAX)
        lo, hi, dims, v = evaluate(pad_used)
    clear = wall_max(v) < iso

    axes = np.diag((hi - lo) / (dims - 1))
    cube = write_cube(lo, axes, dims, v, syms, coords,
                      'kind=mep units=kcal/mol charges=gasteiger')
    meta = {
        'kind': 'mep', 'units': 'kcal/mol', 'charges': 'gasteiger',
        'net_charge': int(round(charges.sum())),
        'dims': dims.tolist(), **grid_spacing_meta(lo, hi, dims, spacing),
        'vmin': float(v.min()), 'vmax': float(v.max()),
        # A point charge is spherical, so this model has NO higher multipoles
        # and a sigma-hole is structurally impossible in it — not merely
        # inaccurate. Measured: Gasteiger gives bromobenzene -6.2 kcal/mol at
        # the cap while the QM surface route gives +9.9, a ~16 kcal/mol
        # contradiction with the OPPOSITE SIGN, inside one application. Named
        # here so the UI can route the question to the instrument that can
        # answer it instead of quietly answering it wrongly.
        'sigma_hole_representable': not any(
            sym in {'Cl', 'Br', 'I', 'S', 'Se', 'Te'} for sym in syms),
        'model_caveat': ('Gasteiger point charges: no lone-pair or sigma-hole '
                         'anisotropy, and ~0.4x the QM molecular dipole. A '
                         'qualitative map, not an interaction energy.'),
        'iso_fixed': FIXED_ISO['mep'],
        'iso_sized_for': round(iso, 4),
        'pad_used_angstrom': round(pad_used, 1),
        'wall_max': round(wall_max(v), 3),
        # False means the surface still runs off the edge of the grid and is
        # being drawn as a flat face. Stated, not hidden: a charged ligand's
        # monopole does not decay fast enough to close inside any usable box.
        'contour_closes_in_box': bool(clear),
    }
    return cube, meta


# ── classical lipophilicity field (Crippen MLP) ─────────────────────────────

def field_mlp(mol: Chem.Mol, spacing=0.4, pad=4.0):
    """Molecular lipophilicity potential: Crippen atomic logP contributions
    spread with the Fauchère exponential kernel MLP(r) = Σ f_i · e^(−d_i/2).
    Positive lobes = lipophilic surface, negative = hydrophilic. This is the
    'where is the molecule greasy' field — the SAR question logP alone hides.
    Instant, no SCF, not DB-cached (recompute is cheaper than the roundtrip).
    """
    from rdkit.Chem import rdMolDescriptors
    contribs = rdMolDescriptors._CalcCrippenContribs(mol)
    f = np.array([c[0] for c in contribs], dtype=float)
    if not np.isfinite(f).all():
        raise ValueError('Crippen contributions undefined for this molecule')
    syms, coords = mol_atoms(mol)

    iso = FIXED_ISO['mlp'] * ISO_SLIDER_FLOOR

    def evaluate(pad_a: float):
        lo = coords.min(axis=0) - pad_a
        hi = coords.max(axis=0) + pad_a
        dims = np.maximum(np.ceil((hi - lo) / spacing).astype(int) + 1, 8)
        dims = np.minimum(dims, GRID_MAX_DIM)
        xs = np.linspace(lo[0], hi[0], dims[0])
        ys = np.linspace(lo[1], hi[1], dims[1])
        zs = np.linspace(lo[2], hi[2], dims[2])
        gx, gy, gz = np.meshgrid(xs, ys, zs, indexing='ij')
        pts = np.stack([gx, gy, gz], axis=-1)
        vv = np.zeros(tuple(dims), dtype=float)
        for fi, c in zip(f, coords):
            d = np.linalg.norm(pts - c, axis=-1)
            vv += fi * np.exp(-d / 2.0)
        return lo, hi, dims, vv

    pad_used = pad
    lo, hi, dims, v = evaluate(pad_used)
    while wall_max(v) >= iso and pad_used < PAD_MAX:
        pad_used = min(pad_used + PAD_STEP, PAD_MAX)
        lo, hi, dims, v = evaluate(pad_used)
    clear = wall_max(v) < iso

    axes = np.diag((hi - lo) / (dims - 1))
    cube = write_cube(lo, axes, dims, v, syms, coords,
                      'kind=mlp units=logP kernel=fauchere_exp_d_over_2 charges=crippen')
    meta = {
        'kind': 'mlp', 'units': 'MLP (Crippen/Fauchère)', 'method': 'crippen',
        'total_logp': float(f.sum()),
        'dims': dims.tolist(), **grid_spacing_meta(lo, hi, dims, spacing),
        'vmin': float(v.min()), 'vmax': float(v.max()),
        'iso_fixed': FIXED_ISO['mlp'],
        'iso_sized_for': round(iso, 4),
        'pad_used_angstrom': round(pad_used, 1),
        'wall_max': round(wall_max(v), 4),
        'contour_closes_in_box': bool(clear),
        # The Fauchere kernel decays over ~2 A, so every atom contributes
        # everywhere and the field is one-signed for most drug-like molecules.
        # Measured: aspirin is 99.9% positive, ibuprofen 100%. Calling it
        # "diverging" and drawing a negative lobe that does not exist is a
        # claim about the molecule; this states the truth instead.
        'single_signed': bool(v.min() >= 0 or v.max() <= 0),
    }
    return cube, meta


# ── quantum fields (pyscf HF + cubegen) ─────────────────────────────────────

ECP_FROM_Z = 37  # def2 bases replace core electrons with an ECP from Rb up


def ecp_for(syms: list[str], basis: str) -> dict:
    """Attach the matching ECP to every heavy element the basis defines one
    for. WITHOUT this, pyscf silently treats e.g. iodine all-electron under a
    basis never designed for it: SCF converges, charge balances, the far
    field decays to zero — and the sigma-hole comes out 58 kcal/mol wrong
    with the WRONG SIGN (measured by the physics session on iodobenzene).
    Every honesty gate passes; only the physics is false."""
    # The try/except that used to live here was a CHECK THAT COULD NOT FIRE:
    # gto.basis.load_ecp RETURNS [] for a basis that defines no ECP, it does
    # not raise. So every Z>=37 element got an entry unconditionally, and with
    # DEFAULT_BASIS='sto-3g' — which has no iodine ECP at all — pyscf printed
    # "ECP sto-3g not found for I", built the ALL-ELECTRON molecule, and
    # meta['ecp'] told the UI and app.field_cube that an ECP was used.
    # The incident this function exists to prevent was shipping on the default
    # path, wearing this function's own green light. Found 2026-08-11 by
    # backend/tests/test_physics_contracts.py, which asserts the CLAIM implies
    # the core was actually replaced (nelectron < sum of atomic numbers).
    from pyscf import gto
    pt = Chem.GetPeriodicTable()
    ecp = {}
    for s in set(syms):
        if pt.GetAtomicNumber(s) < ECP_FROM_Z:
            continue
        try:
            if not gto.basis.load_ecp(basis, s):
                continue          # basis defines no ECP for this element
        except Exception:
            continue              # or refuses to describe one
        ecp[s] = basis
    return ecp


def basis_covers(syms: list[str], basis: str) -> list[str]:
    """Elements in `syms` that `basis` cannot describe at all.

    Separate from ecp_for because they are different questions: ecp_for asks
    "is the core replaced", this asks "does this basis exist for the element".
    Measured coverage among the whitelist: 6-31g has neither Br nor I,
    6-31g* has Br but not I, def2-svp has both. Without this, a bromo or iodo
    compound under 6-31g raised pyscf's BasisNotFoundError — a RuntimeError,
    so the HTTP layer reported reason='internal' with a traceback for a
    request whose remedy ("use def2-svp") is obvious. A chemistry limit must
    be refused as chemistry.
    """
    from pyscf import gto
    missing = []
    for s in sorted(set(syms)):
        try:
            if not gto.basis.load(basis, s):
                missing.append(s)
        except Exception:
            missing.append(s)
    return missing


class FieldBudgetExceeded(Exception):
    """Raised from inside the SCF loop when the wall-clock budget runs out.

    Its own class, not ValueError: the HTTP layer reports it with the elapsed
    time and the budget, and a caller can tell 'too expensive' apart from
    'chemically impossible' — the two need different next moves from a chemist.
    """


def required_spin(syms: list[str], charge: int) -> tuple[int, str | None]:
    """The spin to run, and — if the molecule contains an open-shell metal —
    the reason a caller must state one explicitly.

    `nelec % 2` answers 'is the electron count odd', which is not the same
    question as 'what is the ground state'. Fe(II) porphyrin has an even count
    and a QUINTET ground state; computing it as a singlet does not fail, it
    converges to a state the molecule is not in. Refusing costs a second;
    the alternative cost 36 minutes and produced a number with no referent.
    """
    pt = Chem.GetPeriodicTable()
    nelec = sum(pt.GetAtomicNumber(s) for s in syms) - charge
    default = nelec % 2
    open_shell = sorted({s for s in syms
                         if pt.GetAtomicNumber(s) in OPEN_SHELL_METAL_Z})
    if not open_shell:
        return default, None
    return default, (
        f'{"/".join(open_shell)} is an open-shell metal: the ground state is '
        f'almost certainly not the spin={default} state this would compute, and '
        f'the SCF would either grind or converge to a state the molecule is not '
        f'in. Pass an explicit "spin" (number of unpaired electrons, e.g. 4 for '
        f'high-spin Fe(II)) to run it anyway.'
    )


def run_scf(mol: Chem.Mol, basis: str, max_seconds: float = DEFAULT_MAX_SECONDS,
            spin: int | None = None):
    from pyscf import gto, scf

    # The clamp lived ONLY in the HTTP handler, so run_scf(max_seconds=nan)
    # ran unbounded: NaN fails every comparison, so the watchdog's
    # `time.time() > deadline` was False forever. The library is about to be
    # called by a CLI, a worker and a notebook — the 36-minute lesson must not
    # be one nan away from being off at every one of those call sites.
    # (backend/tests/test_physics_contracts.py, 2026-08-11)
    # NON-FINITE is nonsense and falls back to the default. ZERO IS NOT: it is
    # a legitimate, if extreme, request meaning "refuse before doing any work",
    # and the deadline test uses exactly that to prove the watchdog lives inside
    # the SCF loop. Conflating the two (my first version did) turned a 0 s
    # budget into a 90 s run — a regression caught by that test within a minute.
    if not math.isfinite(max_seconds):
        max_seconds = DEFAULT_MAX_SECONDS
    max_seconds = min(max(max_seconds, 0.0), MAX_MAX_SECONDS)

    syms, coords = mol_atoms(mol)
    # A basis that cannot describe an element is a CHEMISTRY refusal, not an
    # internal error: pyscf's BasisNotFoundError is a RuntimeError, which the
    # HTTP layer reported as reason='internal' plus a traceback.
    uncovered = basis_covers(syms, basis)
    if uncovered:
        raise ValueError(
            f'basis {basis} does not cover {"/".join(uncovered)} — '
            'def2-svp is the only whitelisted basis with iodine')
    if len(syms) > MAX_QM_ATOMS:
        raise ValueError(
            f'{len(syms)} atoms (with H) exceeds the interactive QM cap of {MAX_QM_ATOMS}'
        )
    charge = sum(a.GetFormalCharge() for a in mol.GetAtoms())
    default_spin, open_shell_reason = required_spin(syms, charge)
    if spin is None:
        if open_shell_reason:
            raise ValueError(open_shell_reason)
        spin = default_spin
    elif (spin % 2) != (default_spin % 2):
        raise ValueError(
            f'spin={spin} is impossible for {sum(1 for _ in syms)} atoms at '
            f'charge {charge}: the electron count fixes the parity, so the '
            f'number of unpaired electrons must be {default_spin}, {default_spin + 2}, …'
        )
    ecp = ecp_for(syms, basis)

    key = hashlib.sha256(
        (basis + repr(sorted(ecp.items())) + repr(syms)
         + repr(coords.round(4).tolist()) + str(charge) + f'|s{spin}').encode()
    ).hexdigest()
    with _scf_lock:
        if key in _scf_cache:
            _scf_cache.move_to_end(key)     # LRU: a hit is a use
            return _scf_cache[key]

    gmol = gto.M(
        atom=[(s, tuple(c)) for s, c in zip(syms, coords)],
        unit='Angstrom', basis=basis, charge=charge, spin=spin,
        ecp=ecp or None,
        verbose=0,
    )
    # ── PRE-FLIGHT, and it did not exist until now ────────────────────────
    # The only cost gate on this path was the CUBE estimate (after the SCF) plus
    # the in-loop watchdog. So `max_seconds=0` — documented everywhere in this
    # repo as "refuse before doing any work, tell me the cost" — actually entered
    # the SCF and was stopped by the watchdog after one cycle: measured 1.03 s and
    # 1.28 s against the job ledger, recorded as BUDGET. A rule stated in four
    # comments and implemented in none of them is a rule about my intentions.
    #
    # The model is the one already measured in this repo over 47 molecules
    # (backend/physics/README.md): seconds ~ 5.9e-9 * nao^4.03, HF being O(N^4).
    # It is a screen, not a prediction — the fit underestimates the middle of the
    # range by up to ~2.8x, so the 2.8x safety factor from that same measurement
    # is applied here rather than quietly dropped.
    nao = gmol.nao_nr()
    predicted_scf = 2.8 * 5.9e-9 * float(nao) ** 4.03
    if predicted_scf > max_seconds:
        raise FieldBudgetExceeded(
            f'estimated {predicted_scf:.1f} s of SCF for {nao} basis functions '
            f'({basis}, {len(syms)} atoms) against a {max_seconds:g} s budget — '
            f'refused before starting. Send max_seconds >= {predicted_scf:.0f}, '
            f'or a smaller basis. The estimate is an O(N^4) screen with a 2.8x '
            f'safety factor, not a promise.')

    mf = scf.RHF(gmol) if spin == 0 else scf.UHF(gmol)
    mf.max_cycle = 120
    t0 = time.time()
    deadline = t0 + max_seconds

    # The bound that actually holds. pyscf calls this once per SCF cycle, so an
    # unbounded iteration count becomes a bounded wall clock; raising here
    # unwinds out of kernel() instead of running to max_cycle.
    cycles = [0]

    def _watchdog(envs):
        cycles[0] += 1
        if time.time() > deadline:
            raise FieldBudgetExceeded(
                f'SCF exceeded its {max_seconds:.0f} s budget after {cycles[0]} '
                f'cycles ({int(gmol.nao)} basis functions, spin={spin}). Raise '
                f'"max_seconds", pick a smaller basis, or choose a classical '
                f'field — the quantum path is not interactive for this molecule.'
            )
    mf.callback = _watchdog

    energy = mf.kernel()
    method = 'RHF' if spin == 0 else 'UHF'
    if not mf.converged:
        # Plain DIIS stalls on transition-metal ligands (measured: Fe-heme,
        # 120 cycles, no convergence). Second-order SCF restarted from the
        # stalled MOs is the standard rescue — but it is not free, and starting
        # it with two seconds left just spends the rest of the budget to fail
        # in a second place. Convergence is reported either way.
        remaining = deadline - time.time()
        if remaining >= SOSCF_MIN_REMAINING:
            mf = mf.newton()
            mf.callback = _watchdog
            energy = mf.kernel()
            method += '+SOSCF'
    result = {
        'gmol': gmol, 'mf': mf, 'energy': float(energy), 'method': method,
        'converged': bool(mf.converged), 'charge': charge, 'spin': spin,
        'natoms': len(syms), 'nbasis': int(gmol.nao), 'seconds': time.time() - t0,
        'ecp': sorted(ecp) if ecp else [], 'scf_cycles': cycles[0],
    }
    # Only positive results are cached: caching an unconverged SCF would make
    # every retry fail instantly from the cache, forever.
    if result['converged']:
        # Drop the two-electron integrals BEFORE the object is retained. This
        # cache exists so that homo/lumo/density/mep_qm on one molecule share a
        # single SCF, and every one of those needs mo_coeff, mo_energy, the
        # density matrix and the Mole — none of them needs _eri. Measured on
        # this box: _eri is 29 MB for aspirin, 42 for caffeine, 327 for
        # porphine, against 0.14 MB of mo_coeff. Keeping it meant retaining
        # ~2300x the memory the cache is actually for, per molecule, forever.
        #
        # This is what killed the daemon mid-sweep: a hard process death with
        # no traceback, after swap had been driven to zero. The molecule it
        # died on (a nitroxide radical) runs fine in isolation — the crash
        # belonged to everything that had been cached BEFORE it, which is
        # exactly the kind of defect a one-molecule test can never find.
        mf._eri = None
        with _scf_lock:
            _scf_cache[key] = result
            # Bounded, LRU. An unbounded cache on a long-lived daemon is not a
            # cache, it is a leak with a fast path.
            while len(_scf_cache) > SCF_CACHE_MAX:
                _scf_cache.popitem(last=False)
    return result


def _cubegen_to_text(fn, *args, **kwargs) -> str:
    with tempfile.NamedTemporaryFile(suffix='.cube', delete=False, mode='r') as f:
        path = f.name
    try:
        fn(*args, path, **kwargs)  # pyscf cubegen signature: (mol, outfile, data)
        with open(path) as fh:
            return fh.read()
    finally:
        os.unlink(path)


# ── what a level of theory may be QUOTED for ────────────────────────────────
#
# Measured by an independent computational-chemistry review, in this env:
#
#   HOMO ordering, substituted benzenes, STO-3G vs def2-SVP: STO-3G ranks
#   NITROBENZENE 4th of 7 — more electron-rich than benzene, chlorobenzene and
#   benzonitrile. def2-SVP ranks it last. The strongest pi-acceptor in the set
#   comes out as a donor, and the error is chemotype-dependent (1.37-2.68 eV
#   within one series) so orderings are NOT protected by cancellation.
#
#   LUMO: water +16.19 eV at STO-3G vs +4.74 at def2-SVP. A 12 eV basis
#   dependence on a quantity the panel printed to one decimal. An HF virtual
#   orbital is not a bound state; a minimal basis has no diffuse functions, so
#   it artificially confines a continuum function and returns a spuriously
#   well-behaved-looking number.
#
#   Anions are qualitatively broken: acetate HOMO = +2.30 eV at STO-3G, i.e.
#   the electron is unbound. The coverage sweep marks that row OK, because
#   "a cube came back" was the only question it asked.
#
# So the number is not withheld — it is LABELLED, and the label travels with it
# in meta rather than living in a doc nobody reads at the moment of reading the
# number.
MINIMAL_BASES = {'sto-3g', 'sto3g'}


def frontier_energy_caveat(basis: str, charge: int) -> str | None:
    """Why these orbital energies must not be quoted, or None if they may be."""
    if str(basis).lower() in MINIMAL_BASES:
        return ('minimal basis: no polarisation or diffuse functions. HOMO is '
                '1.4-2.7 eV off experimental IP and its ORDERING inverts within '
                'a substituted-benzene series; the LUMO is not a bound state and '
                'moves ~12 eV to def2-SVP. Not quotable — switch to def2-SVP for '
                'a number, or read these as shapes only.')
    if charge < 0:
        return ('anion: without diffuse functions the highest occupied orbital '
                'can come out unbound (acetate reads +2.3 eV at STO-3G). Use a '
                'basis with diffuse functions before quoting this.')
    return None


def field_quantum(mol: Chem.Mol, kind: str, basis: str,
                  max_seconds: float = DEFAULT_MAX_SECONDS, spin: int | None = None):
    from pyscf.tools import cubegen

    res = run_scf(mol, basis, max_seconds=max_seconds, spin=spin)
    if not res['converged']:
        raise ValueError(
            f"SCF did not converge (E={res['energy']:.6f} Ha, basis={basis}, "
            f"charge={res['charge']}, spin={res['spin']}) — refusing to ship a "
            'decorative field'
        )
    gmol, mf = res['gmol'], res['mf']

    if res['spin'] == 0:
        mo_coeff, nocc = mf.mo_coeff, gmol.nelectron // 2
        mo_energy = mf.mo_energy
    else:  # UHF: alpha channel
        mo_coeff, nocc = mf.mo_coeff[0], (gmol.nelectron + gmol.spin) // 2
        mo_energy = mf.mo_energy[0]

    meta = {
        'kind': kind, 'basis': basis, 'method': res['method'],
        'scf_energy_ha': res['energy'], 'converged': True,
        'charge': res['charge'], 'spin': res['spin'],
        'natoms': res['natoms'], 'nbasis': res['nbasis'],
        'ecp': res['ecp'],
        'scf_seconds': round(res['seconds'], 2),
        'scf_cycles': res.get('scf_cycles'),
        'frontier_caveat': frontier_energy_caveat(basis, res['charge']),
        'homo_ev': float(mo_energy[nocc - 1] * 27.2114),
        'lumo_ev': float(mo_energy[nocc] * 27.2114) if nocc < len(mo_energy) else None,
    }

    # UHF rdm1 is (alpha, beta); cubegen wants one total density matrix.
    dm = mf.make_rdm1()
    if res['spin'] != 0:
        dm = dm[0] + dm[1]

    # ── the cube step needs a DIFFERENT bound than the SCF, and the difference
    # is not a compromise. An SCF has an unbounded loop, so only a deadline
    # inside it can hold; cubegen does a KNOWN amount of work — grid points
    # times basis functions, both fixed before it starts — so a prediction is
    # sound there in a way it never is for the SCF. Measured on this box.
    # (Found by the coverage sweep: a 30 s budget returned in 38.9 s, because
    # the watchdog stopped at the SCF and the cube ran free behind it.)
    if kind == 'mep_qm':
        npoints = CUBE_GRID_MEP ** 3
        predicted = CUBE_MEP_FIXED + CUBE_MEP_MARGINAL * npoints * res['nbasis'] ** 2
    else:
        npoints = CUBE_GRID_ORB ** 3
        predicted = CUBE_ORB_FIXED + CUBE_ORB_MARGINAL * npoints * res['nbasis']
    remaining = max_seconds - res['seconds']
    if predicted > remaining:
        raise FieldBudgetExceeded(
            f'the SCF converged in {res["seconds"]:.0f} s but the {kind} cube '
            f'needs about {predicted:.0f} s more ({npoints:,} grid points × '
            f'{res["nbasis"]} basis functions) and only {max(remaining, 0):.0f} s '
            f'of the {max_seconds:.0f} s budget is left. Raise "max_seconds" — '
            f'the wavefunction is already cached, so the retry pays for the '
            f'cube alone.'
        )
    t_cube = time.time()

    if kind == 'homo':
        cube = _cubegen_to_text(
            lambda m, p: cubegen.orbital(m, p, mo_coeff[:, nocc - 1]), gmol)
    elif kind == 'lumo':
        if nocc >= mo_coeff.shape[1]:
            raise ValueError('no virtual orbital in this basis')
        cube = _cubegen_to_text(
            lambda m, p: cubegen.orbital(m, p, mo_coeff[:, nocc]), gmol)
    elif kind == 'density':
        cube = _cubegen_to_text(
            lambda m, p: cubegen.density(m, p, dm), gmol)
    elif kind == 'mep_qm':
        # The electrostatic-potential integrals dominate: ~1 s per 10k grid
        # points on 24 cores (measured 52 s at the 80^3 default on 50 atoms).
        # The potential is smooth — 50^3 is visually indistinguishable and 4x
        # cheaper.
        cube = _cubegen_to_text(
            lambda m, p: cubegen.mep(m, p, dm, nx=CUBE_GRID_MEP,
                                     ny=CUBE_GRID_MEP, nz=CUBE_GRID_MEP), gmol)
    else:
        raise ValueError(f'unknown quantum kind {kind!r}')
    meta['cube_seconds'] = round(time.time() - t_cube, 2)
    meta['cube_predicted_seconds'] = round(predicted, 1)
    return cube, meta




# ── residue-template charges: the paved road's DATA, not its pipeline ───────
#
# The additivity measurement (5.2e-16) says the FIELD is exactly linear in the
# atom set. It also says, on the other edge, that the CHARGE MODEL must not be:
# Gasteiger is computed per-molecule, so a truncated pocket gets different
# charges than the intact protein and the "sum of parts" stops being the whole.
# A group therefore needs charges defined per atom WITHOUT seeing the rest of
# the system — which is exactly what a residue template is.
#
# pdb2pqr ships those tables (AMBER.DAT, 2257 rows of RESNAME ATOM charge
# radius type). Taking the table rather than running the whole pdb2pqr pipeline
# is deliberate: the pipeline wants a PDB file and does its own hydrogen
# placement and titration, and this route is handed atoms that mol* already
# has. Rolling my own residue→charge table would have been the L21 trap — the
# first thing anyone thinks of, and a worse re-derivation of a solved problem.

_charge_table: dict[tuple[str, str], float] | None = None
_charge_table_lock = threading.Lock()
CHARGE_FORCEFIELD = 'AMBER'
# Excluded from a pocket source unless the caller supplies hydrogens.
WATER_RESNAMES = {'HOH', 'WAT', 'DOD', 'H2O', 'TIP', 'TP3', 'SOL'}


def charge_table() -> dict[tuple[str, str], float]:
    global _charge_table
    with _charge_table_lock:
        if _charge_table is not None:
            return _charge_table
        import pdb2pqr
        path = (Path(pdb2pqr.__file__).parent / 'dat' / f'{CHARGE_FORCEFIELD}.DAT')
        raw: dict[str, dict[str, float]] = {}
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                raw.setdefault(parts[0].upper(), {})[parts[1].upper()] = float(parts[2])
            except ValueError:
                continue

        # ── UNITED ATOM, and this is not an optimisation ────────────────────
        #
        # A crystallographic protein has NO HYDROGENS. AMBER's charges assume
        # every one of them, so summing only the heavy atoms loses all the
        # compensating positive charge. Measured against the all-atom totals:
        #
        #     ALA   0.000 -> -0.535     PHE   0.000 -> -1.110
        #     ASP  -1.000 -> -1.357     LYS  +1.000 -> -0.882
        #     GLU  -1.000 -> -1.284     ARG  +1.000 -> -1.827
        #
        # Every residue reads too negative and THE TWO POSITIVE RESIDUES READ
        # NEGATIVE. Arginine, +1 in reality, comes out at -1.83. That inverts
        # the sign of exactly the question this field exists to answer — is the
        # pocket positive where my ligand is negative — and it would have
        # shipped looking entirely plausible: a coloured field, a named charge
        # model, a printed net charge, and nothing on screen saying the
        # hydrogens were never there.
        #
        # So each hydrogen's charge is folded onto the heavy atom it belongs
        # to. Parentage comes from the PDB naming convention, which encodes it:
        # strip the leading H and any trailing digits and you have the parent's
        # suffix — HG12 -> "G1" -> CG1, HB3 -> "B" -> CB, HH11 -> "H1" -> NH1,
        # and a bare H -> "" -> N.
        table: dict[tuple[str, str], float] = {}
        for res, atoms in raw.items():
            folded = {n: q for n, q in atoms.items() if not n.startswith('H')}
            suffix_to_heavy = {n[1:]: n for n in folded}
            for name, q in atoms.items():
                if not name.startswith('H'):
                    continue
                # Drop trailing digits ONE AT A TIME and take the first match.
                # Stripping them all was wrong in exactly the case that matters:
                # HH11 -> "H", which matches nothing, while its real parent NH1
                # has suffix "H1". The four guanidinium hydrogens therefore
                # never folded and arginine summed to -0.79 over its heavy
                # atoms instead of +1.
                suffix = name[1:]
                parent = None
                while True:
                    parent = suffix_to_heavy.get(suffix)
                    if parent is not None or not suffix or not suffix[-1].isdigit():
                        break
                    suffix = suffix[:-1]
                if parent is None and suffix == '':
                    parent = 'N' if 'N' in folded else None
                if parent is None:
                    # An unattachable hydrogen would silently vanish along with
                    # its charge, which is the bug being fixed. Keep it as its
                    # own site rather than dropping it.
                    folded[name] = folded.get(name, 0.0) + q
                    continue
                folded[parent] = folded.get(parent, 0.0) + q
            for n, q in folded.items():
                table[(res, n)] = q
        _charge_table = table
        return table


def resolve_charges(sources: list[dict]) -> tuple[list[float], list[str]]:
    """Per-atom charges from the residue template, and what could not be found.

    Unresolvable atoms are REPORTED, never defaulted to zero. A zero charge is
    not "unknown" — it is a claim that the atom is neutral, and the charged
    residues are the dominant sources in a pocket field. Silently zeroing them
    would switch off precisely the atoms the picture is about.
    """
    table = charge_table()
    charges: list[float] = []
    missing: list[str] = []
    for a in sources:
        # WATER IS EXCLUDED, and mapping it would be worse than refusing it.
        # AMBER's water is WAT:OW -0.834 plus two WAT:HW +0.417 — neutral, as
        # it must be. A crystallographic water is ONE OXYGEN: the hydrogens
        # were never resolved. Mapping HOH:O onto OW therefore contributes a
        # fictitious -0.834 MONOPOLE per water, and an electrostatic field is
        # dominated by its monopoles — six pocket waters would be five charge
        # units of invention sitting on top of the answer.
        #
        # Placing the hydrogens is not a repair either: a water's entire
        # electrostatic identity is the direction of its dipole, so inventing
        # an orientation points it confidently the wrong way.
        if str(a.get('resname', '')).upper() in WATER_RESNAMES:
            charges.append(0.0)
            continue
        if a.get('charge') is not None:
            charges.append(float(a['charge']))
            continue
        key = (str(a.get('resname', '')).upper(), str(a.get('atom_name', '')).upper())
        q = table.get(key)
        if q is None:
            missing.append(f'{key[0]}:{key[1]}')
            charges.append(0.0)
        else:
            charges.append(q)
    return charges, sorted(set(missing))


# ── SOURCE ⊥ FRAME: a classical field of an arbitrary atom set ──────────────

MAX_REGION_SOURCES = 20000
MAX_REGION_VOXELS = 2_200_000     # ~128³, the same ceiling the ligand path uses


def field_region(sources, lo, hi, spacing: float, kind: str,
                 req_dielectric: str = 'r-dependent'):
    """The classical field of an arbitrary SOURCE set, sampled in a given FRAME.

    Why this route exists at all, and why it needs no chemistry:

    Both classical fields are EXACTLY linear in the atom set —
        MEP  V(r) = 332.06 * sum_i q_i / max(|r-r_i|, 0.5)
        MLP  V(r) = sum_i f_i * exp(-d_i / 2)
    so V_{A∪B} = V_A + V_B pointwise. MEASURED on a shared grid, benzene plus
    acetate: |V_AB - (V_A + V_B)| / |V_AB| = 5.2e-16. Machine precision.

    Therefore a group field needs NO bond perception, NO capping, NO valence,
    NO molfile. It needs (element, position, charge) per atom and a box. That
    is the whole reason this is a separate route rather than a wider molfile:
    the molfile path has to perceive topology, and the topology builder cannot
    currently express more than one residue without silently dropping bonds.
    This route cannot hit that class of bug because it never asks.

    SOURCE and FRAME are separate arguments, which is the actual architectural
    move. Today they are welded — the grid is derived from the same molecule
    that generates the field — so you can only ever ask "what does my ligand
    look like?". Split, the interesting question becomes expressible: SOURCE =
    the pocket, FRAME = the ligand's box. That is "what field does my ligand
    SIT IN", and it is the one a designer actually has. It also keeps the grid
    ligand-sized while the source is the whole protein, which is what makes it
    cheap.

    The CHARGES are the caller's responsibility and are not invented here. The
    same additivity measurement shows why: Gasteiger is computed per-MOLECULE,
    so a truncated pocket gets different charges than the real one. A group
    needs a charge model that is well defined per atom without seeing the whole
    system — residue templates. Refusing beats guessing.
    """
    if kind not in ('mep', 'mlp'):
        raise ValueError(f'region fields are classical only; {kind!r} is not '
                         f'one of mep, mlp. A quantum field of a cut protein '
                         f'region needs capping and a basis this route does '
                         f'not attempt.')
    if not sources:
        raise ValueError('no source atoms were sent')
    if len(sources) > MAX_REGION_SOURCES:
        raise ValueError(f'{len(sources)} source atoms exceeds the '
                         f'{MAX_REGION_SOURCES} cap for one region field')

    # Charges come from the residue template when the caller sent residue
    # identity instead of a number. Unresolved atoms are named, not zeroed.
    n_water = sum(1 for a in sources
                  if str(a.get('resname', '')).upper() in WATER_RESNAMES)
    if kind == 'mep' and n_water:
        sources = [a for a in sources
                   if str(a.get('resname', '')).upper() not in WATER_RESNAMES]
    resolved, missing = (resolve_charges(sources) if kind == 'mep'
                         else ([None] * len(sources), []))
    if missing:
        raise ValueError(
            f'no {CHARGE_FORCEFIELD} template charge for {len(missing)} atom '
            f'type(s): {", ".join(missing[:8])}'
            + (' …' if len(missing) > 8 else '')
            + '. Zeroing them would switch off exactly the atoms a pocket '
              'field is about, so the request is refused instead.')

    syms, coords, weights = [], [], []
    for i, a in enumerate(sources):
        w = resolved[i] if kind == 'mep' else a.get('logp')
        if w is None:
            raise ValueError(
                f'source atom {i} ({a.get("element", "?")}) has no logp. This '
                f'route does not invent one: a per-molecule model gives a '
                f'truncated region different values than the intact system.')
        if not math.isfinite(float(w)):
            raise ValueError(f'source atom {i} has a non-finite weight')
        syms.append(str(a.get('element', 'C')))
        coords.append([float(a['x']), float(a['y']), float(a['z'])])
        weights.append(float(w))
    coords = np.asarray(coords, dtype=float)
    weights = np.asarray(weights, dtype=float)

    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    if not np.all(hi > lo):
        raise ValueError('frame hi must exceed lo on every axis')
    dims = np.maximum(np.ceil((hi - lo) / spacing).astype(int) + 1, 8)
    if int(np.prod(dims)) > MAX_REGION_VOXELS:
        raise ValueError(
            f'frame {dims.tolist()} is {int(np.prod(dims)):,} voxels, over the '
            f'{MAX_REGION_VOXELS:,} cap. Shrink the FRAME — it is meant to be '
            f'the size of what you are looking at, not the size of the source.')

    xs = np.linspace(lo[0], hi[0], dims[0])
    ys = np.linspace(lo[1], hi[1], dims[1])
    zs = np.linspace(lo[2], hi[2], dims[2])
    gx, gy, gz = np.meshgrid(xs, ys, zs, indexing='ij')
    pts = np.stack([gx, gy, gz], axis=-1)

    # A distance cutoff, because the ligand path's per-atom full-grid temporary
    # is ~50 MB at 128³ and a pocket has thousands of atoms. Sources far from
    # the frame contribute below the noise of the model itself.
    # ── SCREENING, and the reason it is on by default ───────────────────
    #
    # A bare Coulomb sum in vacuum is not what a ligand experiences in a
    # pocket. Two errors sit on top of an unscreened point-charge map and they
    # are different sizes:
    #
    #   NON-ADDITIVITY. Real densities polarise each other, so the true
    #   V(A∪B) is not V(A)+V(B). Measured here on a water dimer at H-bonding
    #   distance, RHF/6-31G* on a 5 Å probe shell: the non-additive part is
    #   1.95 kcal/mol against a 14.07 peak — 13.8%. Larger for charged
    #   residues against a polarisable ligand. A point-charge model cannot
    #   represent this at all, at any screening.
    #
    #   NO SCREENING. Larger still, and this one IS fixable here. A truncated
    #   Asp- at 4 Å contributes ~83 kcal/mol unscreened, against the ±5-10
    #   features a chemist actually reads. Unscreened, the charged residues
    #   drown the map they are supposed to inform.
    #
    # eps(r) = 4r is the standard minimum distance-dependent dielectric for
    # exactly this situation — cheap, and far closer than vacuum. It is NOT
    # Poisson-Boltzmann and does not pretend to be; the model is named in the
    # response so the picture cannot be mistaken for an interaction energy.
    screen = str(req_dielectric).lower()
    cutoff = 25.0 if kind == 'mep' else 12.0
    near = ((coords >= lo - cutoff).all(axis=1) & (coords <= hi + cutoff).all(axis=1))
    used = int(near.sum())

    v = np.zeros(tuple(dims), dtype=float)
    for w, c in zip(weights[near], coords[near]):
        d = np.linalg.norm(pts - c, axis=-1)
        if kind == 'mep':
            dd = np.maximum(d, 0.5)
            # eps(r) = 4r  =>  V = q / (4 r^2). Vacuum is available but must be
            # asked for, because it is the answer to a different question.
            v += w / (4.0 * dd * dd) if screen == 'r-dependent' else w / dd
        else:
            v += w * np.exp(-d / 2.0)
    if kind == 'mep':
        v *= 332.06

    axes = np.diag((hi - lo) / (dims - 1))
    iso = FIXED_ISO[kind]
    cube = write_cube(lo, axes, dims, v,
                      [syms[i] for i in np.where(near)[0]],
                      coords[near],
                      f'kind={kind}_region units='
                      f'{"kcal/mol" if kind == "mep" else "logP"} source=caller')
    meta = {
        'kind': f'{kind}_region', 'method': 'caller-supplied point weights',
        'units': 'kcal/mol' if kind == 'mep' else 'MLP (Crippen/Fauchère)',
        'n_sources_sent': len(sources), 'n_sources_used': used,
        'cutoff_angstrom': cutoff,
        'net_charge': round(float(weights.sum()), 3),
        'charge_model': (f'{CHARGE_FORCEFIELD} residue templates (pdb2pqr)'
                         if kind == 'mep' else 'caller-supplied'),
        'dielectric': screen if kind == 'mep' else None,
        'physics_caveat': (
            'Point charges with eps(r)=4r screening. This is a QUALITATIVE MAP '
            'of where the pocket is positive or negative, not an interaction '
            'energy. Two things it cannot contain: mutual polarisation and '
            'charge transfer (measured non-additive by ~14% of peak on an '
            'H-bonded water dimer at RHF/6-31G*), and any solvent beyond the '
            'distance-dependent dielectric.'
            if kind == 'mep' else None),
        'waters_excluded': n_water,
        'waters_note': (f'{n_water} crystallographic water(s) left OUT: their '
                        f'hydrogens were never resolved, and a bare oxygen would '
                        f'contribute a fictitious monopole while an invented '
                        f'orientation would point the dipole confidently wrong.'
                        if n_water else None),
        'dims': dims.tolist(), **grid_spacing_meta(lo, hi, dims, spacing),
        'vmin': float(v.min()), 'vmax': float(v.max()),
        'iso_fixed': iso,
        'wall_max': round(wall_max(v), 3),
        # The frame is the CALLER's, so this route cannot grow the box to close
        # the contour the way the ligand path does. It reports instead.
        'contour_closes_in_box': bool(wall_max(v) < iso),
        'frame_is_callers': True,
    }
    return cube, meta


# ── HTTP layer ──────────────────────────────────────────────────────────────

def _allowed_hosts() -> set[str]:
    """localhost + this box's own names/addresses. A Host outside this set is
    DNS rebinding: a page Ivan opened anywhere resolving its own hostname to
    this LAN IP. The Host check kills that class; echoing Origin only for
    same-set origins kills drive-by reads; requiring application/json forces
    a preflight so the check actually gates compute (a text/plain simple POST
    skips preflight entirely)."""
    import socket
    hosts = {'localhost', '127.0.0.1', '[::1]'}
    try:
        hosts.add(socket.gethostname())
        for info in socket.getaddrinfo(socket.gethostname(), None):
            hosts.add(info[4][0])
    except OSError:
        pass
    try:  # LAN IP via routing table, no traffic sent
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('192.0.2.1', 80))
        hosts.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass

    # EVERY address on EVERY interface, because the routing trick above only
    # finds the DEFAULT route's source address. Measured symptom: the Mac
    # reached the page over Tailscale (100.78.155.10:1338 → 200) and the fields
    # daemon answered 403, so the UI said "backend offline" — a security refusal
    # wearing the costume of a dead service. The default route goes out the LAN
    # interface, so the Tailscale address was never in this set, and
    # gethostname() does not resolve to it either.
    #
    # This does NOT widen the threat model: the rebinding attack this check
    # exists to stop is a page resolving ITS OWN hostname to one of this box's
    # addresses. Allowing all of the box's own addresses is precisely the
    # intent; what must never be allowed is a name that is not ours.
    try:
        import subprocess
        out = subprocess.run(['ip', '-o', 'addr', 'show'],
                             capture_output=True, text=True, timeout=5)
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2] in ('inet', 'inet6'):
                addr = parts[3].split('/')[0]
                hosts.add(addr)
                if parts[2] == 'inet6':
                    hosts.add(f'[{addr}]')          # Host headers bracket v6
    except Exception as e:                                          # noqa: BLE001
        # LOUD: a silent failure here means every non-default-route client gets
        # a 403 that reads as an outage, which is the bug this block fixes.
        print(f'[host] could not enumerate interfaces ({e}) — only localhost and '
              f'the default-route address are allowed; a client on another '
              f'interface (Tailscale, a second NIC) will see 403', flush=True)

    # Names cannot be enumerated from an interface — MagicDNS names, a LAN
    # hostname, a reverse-proxy name. One documented escape hatch instead of
    # a wildcard.
    extra = os.environ.get('DIRAC_EXTRA_HOSTS', '')
    hosts.update(h.strip() for h in extra.split(',') if h.strip())
    return hosts


ALLOWED_HOSTS = _allowed_hosts()
ALLOWED_BASIS = ('sto-3g', '6-31g', '6-31g*', 'def2-svp')  # = DB CHECK minus 'none'

# How long a duplicate request may wait for the one already running. Bounded
# because the alternative — waiting as long as the winner's budget allows — makes
# a client's own timeout the only thing that ends the request, and a client
# socket timeout does not stop anything on this side.
JOIN_WAIT_CEILING = 120.0
# After the winner reports 'done', its cube is written by a BACKGROUND thread. So
# 'done' and 'readable' are different moments and this is the gap between them.
JOIN_CACHE_GRACE = 3.0
# Codes that cannot come out differently on a second attempt: the same molecule
# and the same method will refuse the same way, so the waiter serves the refusal
# instead of spending the CPU to reproduce it. BUDGET / UNCONVERGED / INTERNAL are
# deliberately absent — those CAN differ on a retry.
NON_RETRYABLE_JOB_ERRORS = frozenset({'PARSE', 'UNSUPPORTED', 'UNPARAMETERIZED',
                                      'TOO_LARGE'})


class Handler(BaseHTTPRequestHandler):
    def _host_ok(self) -> bool:
        host = (self.headers.get('Host') or '').rsplit(':', 1)[0]
        ok = host in ALLOWED_HOSTS
        if not ok:
            # The RESPONSE stays opaque — echoing the allowlist would hand an
            # attacker this box's addresses. The LOG is where the diagnosis
            # lives, because the 403 is otherwise indistinguishable from a dead
            # daemon from the client's side, and that cost a Mac-side debugging
            # session already.
            print(f'[host] REFUSED Host={host!r} — not one of this box\'s '
                  f'{len(ALLOWED_HOSTS)} known names/addresses. If this is a real '
                  f'interface or DNS name, add it: DIRAC_EXTRA_HOSTS={host}',
                  flush=True)
        return ok

    def _cors_origin(self) -> str | None:
        origin = self.headers.get('Origin')
        if not origin:
            return None
        try:
            host = origin.split('://', 1)[1].rsplit(':', 1)[0]
        except IndexError:
            return None
        return origin if host in ALLOWED_HOSTS else None

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        origin = self._cors_origin()
        if origin:
            self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        origin = self._cors_origin()
        if origin:
            self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if not self._host_ok():
            self._send(403, {'ok': False, 'error': 'unrecognized Host'})
            return
        # Ops surface. ONE dispatch line by design: handle_admin returns None
        # for a path it does not own, so this daemon's routing table stays
        # readable here while the query bodies live in one auditable module.
        # Read-only: there is no admin route that writes or deletes.
        admin = _handle_admin(self.path)
        if admin is not None:
            self._send(*admin)
            return
        if self.path == '/health':
            import pyscf
            import rdkit
            # Resident memory is reported because the daemon has already died
            # of it once, silently, mid-sweep. A number nobody can see is a
            # number nobody notices growing.
            try:
                with open('/proc/self/statm') as fh:
                    rss_mb = int(fh.read().split()[1]) * os.sysconf('SC_PAGE_SIZE') // (1 << 20)
            except Exception:                                # noqa: BLE001
                rss_mb = None
            self._send(200, {'ok': True, 'rdkit': rdkit.__version__,
                             'pyscf': pyscf.__version__,
                             'db_cache': 'on' if _db_ok else 'off',
                             'scf_cached': len(_scf_cache),
                             'scf_cache_max': SCF_CACHE_MAX,
                             'persist': dict(_persist),
                             'jobs': jobs.counters(),
                             'rss_mb': rss_mb})
        else:
            self._send(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        if not self._host_ok():
            self._send(403, {'ok': False, 'error': 'unrecognized Host'})
            return
        ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip()
        if ctype != 'application/json':
            # A text/plain "simple" cross-origin POST skips the CORS preflight
            # entirely; requiring JSON forces the preflight so the Origin
            # allowlist actually gates compute.
            self._send(415, {'ok': False, 'error': 'Content-Type must be application/json'})
            return
        if self.path == '/field/region':
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length > MAX_BODY_BYTES:
                    self._send(413, {'ok': False, 'error': 'request body too large'})
                    return
                req = json.loads(self.rfile.read(length))
                t0 = time.time()
                frame = req['frame']
                cube, meta = field_region(
                    req['sources'], frame['lo'], frame['hi'],
                    float(frame.get('spacing', 0.5)), req.get('kind', 'mep'),
                    req_dielectric=req.get('dielectric', 'r-dependent'))
                meta['total_seconds'] = round(time.time() - t0, 2)
                print(f"[field/region] kind={meta['kind']} "
                      f"sources={meta['n_sources_used']}/{meta['n_sources_sent']} "
                      f"t={meta['total_seconds']}s", flush=True)
                self._send(200, {'ok': True, 'cube': cube, 'meta': meta})
            except Exception as e:
                traceback.print_exc()
                reason = 'unsupported' if isinstance(e, (ValueError, KeyError)) else 'internal'
                self._send(200, {'ok': False, 'error': str(e), 'reason': reason})
            return
        if self.path == '/embed':
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length > MAX_BODY_BYTES:
                    self._send(413, {'ok': False, 'error': 'request body too large'})
                    return
                req = json.loads(self.rfile.read(length))
                t0 = time.time()
                molfile, meta = embed_molecule(
                    req.get('smiles'), req.get('molfile'),
                    seed=int(req.get('seed', 42)))
                meta['seconds'] = round(time.time() - t0, 2)
                print(f"[embed] {meta['smiles_canonical']} atoms={meta['natoms_heavy']} "
                      f"t={meta['seconds']}s", flush=True)
                self._send(200, {'ok': True, 'molfile': molfile, 'meta': meta})
            except Exception as e:
                traceback.print_exc()
                self._send(200, {'ok': False, 'error': str(e)})
            return
        if self.path != '/field':
            self._send(404, {'ok': False, 'error': 'not found'})
            return
        # Declared BEFORE the try so the failure branches can close the row.
        # A job that stays 'running' after its request has already failed is
        # the shape reap() exists to clean up, and leaving it to reap means the
        # ledger reports work nobody is doing until the next restart.
        job_id = None
        t_job = time.time()
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length > MAX_BODY_BYTES:
                self._send(413, {'ok': False, 'error': 'request body too large'})
                return
            req = json.loads(self.rfile.read(length))
            molblock = req['molfile']
            kind = req.get('kind', 'mep')
            basis = req.get('basis', DEFAULT_BASIS)
            # Whitelist BEFORE any compute: an arbitrary basis (cc-pv5z on a
            # 120-atom molecule) allocates unbounded memory in the init-guess
            # phase the deadline provably cannot see, and an off-list basis
            # that computes anyway strands an orphan blob against the DB
            # CHECK. Same literal set as app.field_cube's constraint.
            if basis not in ALLOWED_BASIS:
                self._send(200, {'ok': False, 'error':
                    f'basis {basis!r} not in {sorted(ALLOWED_BASIS)}'})
                return
            # Classical mep has no basis; key it as 'none' so the cache row
            # satisfies the schema's classical/quantum pairing checks. mlp is
            # not in the app.field_kind enum and costs ~0.03 s — never cached.
            basis_key = 'none' if kind == 'mep' else basis
            # NaN fails every comparison, so min(float('nan'), cap) is nan and
            # `time.time() > nan` is False: one JSON token would disable the
            # deadline entirely, failing OPEN. Non-finite -> default.
            req_seconds = float(req.get('max_seconds', DEFAULT_MAX_SECONDS))
            if not math.isfinite(req_seconds):
                req_seconds = DEFAULT_MAX_SECONDS
            # ZERO IS PRESERVED, and the 1.0 floor that used to be here defeated
            # the feature it looked like it was protecting. `max_seconds: 0` means
            # "tell me the cost, do not run", which run_scf's pre-flight now
            # answers before building an SCF object. The floor turned that into
            # "run for one second, then fail with BUDGET" — measured against the
            # job ledger at 1.03 s and 1.28 s, which is a different question
            # answered at a cost.
            #
            # A NEGATIVE budget clamps to 0 rather than to the default: it is not
            # a request for the default, it is nonsense, and the safest reading of
            # nonsense is "refuse and tell me what it would have cost".
            max_seconds = min(max(req_seconds, 0.0), MAX_MAX_SECONDS)
            spin = req.get('spin')
            spin = int(spin) if spin is not None else None
            molfile_sha = hashlib.sha256(molblock.encode()).digest()
            # An explicit spin makes the SAME molfile give a DIFFERENT field,
            # and the persistent cache is keyed (molfile, kind, basis) with no
            # room for it. Rather than let a high-spin heme be served to a
            # request that asked for the singlet, spin-overridden runs bypass
            # the durable cache in both directions. The in-process _scf_cache
            # does carry spin in its key and still applies.
            cacheable = (kind in ('mep', 'mep_qm', 'homo', 'lumo', 'density')
                         and spin is None)

            hit = db_get_cube(molfile_sha, kind, basis_key) if cacheable else None
            if hit is not None:
                cube, meta = hit
                print(f'[field] kind={kind} basis={basis_key} cache=db', flush=True)
                self._send(200, {'ok': True, 'cube': cube, 'meta': meta})
                return

            t0 = time.time()
            # The job row exists for the DURATION of the compute, which is the
            # whole point: a 6-minute SCF was previously invisible until it
            # finished. `open` returns None when the DB is down or an identical
            # job is already in flight, and every path below tolerates None.
            # Imported locally, matching every other use in this file — the
            # module-level import list is deliberately minimal here.
            import method_registry as _mr
            method_row_id = _method_ids.get(_mr.KIND_TO_METHOD.get(kind, ''))
            job_id = None
            job_params = {'kind': kind, 'basis': basis_key,
                          'spin': spin, 'max_seconds': max_seconds}
            if _jobs is not None and method_row_id is not None:
                job_id, conflicted = _jobs.open(
                    method_row_id=method_row_id, input_sha256=molfile_sha,
                    params=job_params, budget_seconds=max_seconds)
                if conflicted:
                    # SOMEONE ELSE IS ALREADY COMPUTING EXACTLY THIS. Until today
                    # both requests computed, and the counter proved that happens
                    # in production. Now the second one waits — which is the point
                    # of job_one_inflight and the difference between a ledger that
                    # observes and one that coordinates.
                    outcome = _jobs.wait_for(
                        method_row_id=method_row_id, input_sha256=molfile_sha,
                        params=job_params,
                        timeout=min(max_seconds or JOIN_WAIT_CEILING,
                                    JOIN_WAIT_CEILING))
                    print(f"[job] joined an in-flight {kind} "
                          f"({outcome['state']} after {outcome.get('waited')}s)",
                          flush=True)
                    if outcome['state'] == 'done' and cacheable:
                        # The winner's persist runs in a BACKGROUND thread, so
                        # 'done' does not yet imply a readable row. Poll the cache
                        # briefly rather than either racing it or trusting it.
                        for _ in range(int(JOIN_CACHE_GRACE / 0.25)):
                            hit = db_get_cube(molfile_sha, kind, basis_key)
                            if hit is not None:
                                cube, meta = hit
                                print(f'[field] kind={kind} basis={basis_key} '
                                      f'cache=db (joined)', flush=True)
                                self._send(200, {'ok': True, 'cube': cube,
                                                 'meta': meta})
                                return
                            time.sleep(0.25)
                    if outcome['state'] in ('failed', 'cancelled') and \
                            outcome.get('error_code') in NON_RETRYABLE_JOB_ERRORS:
                        # A DETERMINISTIC refusal does not become true on a second
                        # attempt: the same molecule and the same method will fail
                        # the same way. Serving the winner's refusal spends no CPU
                        # and says the same thing. A RETRYABLE failure (BUDGET,
                        # UNCONVERGED, INTERNAL) falls through and computes,
                        # because those can differ on a second run.
                        self._send(200, {'ok': False,
                                        'error': outcome.get('error_detail')
                                        or f"identical request failed: "
                                           f"{outcome.get('error_code')}",
                                        'reason': 'unsupported'})
                        return
                    # done-but-uncached, timeout, or a retryable failure: compute
                    # it ourselves. A waiter that gives up must degrade to doing
                    # the work, never to an error the molecule did not cause.
                    job_id, _ = _jobs.open(
                        method_row_id=method_row_id, input_sha256=molfile_sha,
                        params=job_params, budget_seconds=max_seconds)
            mol = prepare_mol(molblock)
            if kind == 'mep':
                cube, meta = field_mep(mol)
            elif kind == 'mlp':
                cube, meta = field_mlp(mol)
            else:
                cube, meta = field_quantum(mol, kind, basis,
                                           max_seconds=max_seconds, spin=spin)
            meta['total_seconds'] = round(time.time() - t0, 2)
            meta['cache'] = 'computed'
            print(f"[field] kind={kind} atoms={mol.GetNumAtoms()} "
                  f"t={meta['total_seconds']}s", flush=True)
            # 自动入库自动缓存 (Ivan): the browser cache serves the interaction,
            # and every computed field ALSO persists — in a background thread,
            # so the render never waits on the database write.
            if cacheable:
                threading.Thread(
                    target=db_put_cube,
                    args=(molfile_sha, kind, basis_key, cube, meta),
                    kwargs={'mol': mol}, daemon=True).start()
                with _persist_lock:
                    _persist['queued'] += 1
                # ENQUEUED, not completed. contracts/iface.pyi says so too — a
                # boolean that reads as "it is in the database" would be a
                # confidently wrong answer, which is the one failure this
                # service is built to refuse.
                meta['stored'] = True
            # `computed_at` was declared for every kind and set by NOBODY on
            # this path — only the cache-hit path had it, so "when was this
            # computed" was answerable only for fields that were not just
            # computed.
            meta.setdefault('computed_at',
                            datetime.now(timezone.utc).isoformat())
            if _jobs is not None:
                # field_cube_id is deliberately NOT set here: the row is written
                # by a background thread that may not have committed yet, and a
                # job pointing at a row that does not exist would be a worse lie
                # than a job with no pointer. The pointer lands when the async
                # write learns to report back.
                _jobs.done(job_id, seconds=meta['total_seconds'])
            # Lenient here, and the asymmetry with the cache path is deliberate:
            # a 6-minute SCF must not be discarded because a producer added a
            # key. It is logged at full volume instead — the drift is still
            # loud, just not fatal at the one point where the loss is
            # unrecoverable.
            try:
                meta = normalize_meta(meta, source='computed')
            except ValueError as exc:                        # noqa: BLE001
                print(f'[meta] SHAPE DRIFT, serving un-normalised: {exc}',
                      flush=True)
            self._send(200, {'ok': True, 'cube': cube, 'meta': meta})
        except FieldBudgetExceeded as e:
            # Not an error in the molecule — an error in what was asked of it.
            # Typed separately so the panel can offer "run it anyway with a
            # bigger budget" instead of showing a chemist a red failure for a
            # calculation that was merely slow.
            print(f'[field] budget exceeded: {e}', flush=True)
            if _jobs is not None:
                _jobs.failed(job_id, code='BUDGET', detail=str(e),
                             seconds=time.time() - t_job)
            self._send(200, {'ok': False, 'error': str(e), 'reason': 'budget'})
        except Exception as e:
            traceback.print_exc()
            reason = 'unsupported' if isinstance(e, ValueError) else 'internal'
            if _jobs is not None:
                # ValueError is this service's chemistry refusal (an element with
                # no basis, an unparameterised atom), which is UNSUPPORTED — not
                # INTERNAL. Recording every failure as INTERNAL would make the
                # ledger's error_code column useless for the one question it is
                # for: was it us or the molecule?
                _jobs.failed(job_id,
                             code='UNSUPPORTED' if reason == 'unsupported' else 'INTERNAL',
                             detail=str(e), seconds=time.time() - t_job)
            self._send(200, {'ok': False, 'error': str(e), 'reason': reason})

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    db_init()
    # 0.0.0.0: the app is used from other machines on the LAN (Ivan's Mac);
    # a loopback-only daemon reads as "backend offline" everywhere but here.
    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print(f'Dirac fields backend on 0.0.0.0:{port}', flush=True)
    server.serve_forever()
