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

Run:  backend/env/bin/python backend/field_server.py   (listens on 127.0.0.1:8901)

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

# Bump on ANY behaviour change. meta.register_producer RAISES at startup when
# this version is re-registered with different source — a forgotten bump is a
# loud startup error, never a silently stale cache (design: migration 006).
PRODUCER_SERVICE = 'dirac-fields'
PRODUCER_VERSION = '1.7'
PRODUCER_NOTES = ('security hardening: Host/Origin allowlist, basis whitelist, '
                  'finite max_seconds clamp (5-principal review P0-prime)')



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
    global _db_ok, _producer_id
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
        _db_ok = True
        print(f'[db] persistent cube cache ON (producer {PRODUCER_SERVICE}/{PRODUCER_VERSION})', flush=True)
    except Exception as e:
        print(f'[db] unavailable ({e}) — persistent cache OFF', flush=True)
        _db_ok = False
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
                'FROM app.v_field_cube_current fc '
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
        return cube, meta
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
                '   compound_id, conformer_hash) '
                'SELECT %s, %s, %s, %s, p.scf_reference, p.scf_converger, '
                '       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s '
                'FROM app.parse_scf_method(%s) AS p '
                'ON CONFLICT ON CONSTRAINT field_cube_exact_key DO NOTHING',
                (molfile_sha, kind, basis, blob_sha,
                 meta.get('scf_energy_ha'),
                 True if kind != 'mep' else None,
                 meta.get('natoms'), meta.get('nbasis'),
                 meta.get('homo_ev'), meta.get('lumo_ev'),
                 meta.get('scf_seconds') if kind != 'mep' else meta.get('total_seconds'),
                 toolkit, _producer_id, compound_id, conf_hash, label))
        print(f'[db] cached kind={kind} basis={basis} coarse={"yes" if conf_hash else "no"}', flush=True)
    except Exception as e:
        print(f'[db] write failed ({e}) — result served but not persisted', flush=True)


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


ISO_ENCLOSED_FRACTION = 0.03   # the surface should wrap ~3% of the box


def suggest_iso(v: np.ndarray) -> float:
    """An isovalue taken from the field's OWN distribution.

    MEP and MLP are sums of atomic contributions: their scale is set by the
    molecule, not by the quantity. A constant default is therefore a guess
    about a number that moves, and it moved — the app shipped iso=0.05 for MLP,
    tuned on aspirin, which is 5–8% of the field's maximum on aspirin AND on
    retinoic acid. At that level the surface encloses nearly the whole padded
    grid and CLIPS AGAINST THE BOX WALL, so a chemist gets a gold crate with
    the ligand somewhere inside it. Found by screenshot; no amount of reading
    the code would have shown it, because the number is not wrong on its face.

    A quantile is scale-free: whatever the units, the surface wraps the top few
    percent of |field| and therefore looks like a lobe rather than a box.
    Orbital and density cubes deliberately do NOT get this — a wavefunction is
    normalised, which is exactly why the conventional 0.02–0.05 constants work
    there and are worth keeping.
    """
    return round(float(np.quantile(np.abs(v), 1.0 - ISO_ENCLOSED_FRACTION)), 6)


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

    lo = coords.min(axis=0) - pad
    hi = coords.max(axis=0) + pad
    dims = np.maximum(np.ceil((hi - lo) / spacing).astype(int) + 1, 8)
    dims = np.minimum(dims, 128)  # hard cap: 128³ ≈ 2M voxels
    axes = np.diag((hi - lo) / (dims - 1))

    xs = np.linspace(lo[0], hi[0], dims[0])
    ys = np.linspace(lo[1], hi[1], dims[1])
    zs = np.linspace(lo[2], hi[2], dims[2])
    gx, gy, gz = np.meshgrid(xs, ys, zs, indexing='ij')
    pts = np.stack([gx, gy, gz], axis=-1)  # (nx,ny,nz,3)

    v = np.zeros(tuple(dims), dtype=float)
    for q, c in zip(charges, coords):
        r = np.linalg.norm(pts - c, axis=-1)
        v += q / np.maximum(r, 0.5)
    v *= 332.06  # kcal/mol per (e²/Å)

    cube = write_cube(lo, axes, dims, v, syms, coords,
                      'kind=mep units=kcal/mol charges=gasteiger')
    meta = {
        'kind': 'mep', 'units': 'kcal/mol', 'charges': 'gasteiger',
        'net_charge': int(round(charges.sum())),
        'dims': dims.tolist(), 'spacing': spacing,
        'vmin': float(v.min()), 'vmax': float(v.max()),
        'iso_suggest': suggest_iso(v),
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

    lo = coords.min(axis=0) - pad
    hi = coords.max(axis=0) + pad
    dims = np.maximum(np.ceil((hi - lo) / spacing).astype(int) + 1, 8)
    dims = np.minimum(dims, 128)
    axes = np.diag((hi - lo) / (dims - 1))
    xs = np.linspace(lo[0], hi[0], dims[0])
    ys = np.linspace(lo[1], hi[1], dims[1])
    zs = np.linspace(lo[2], hi[2], dims[2])
    gx, gy, gz = np.meshgrid(xs, ys, zs, indexing='ij')
    pts = np.stack([gx, gy, gz], axis=-1)

    v = np.zeros(tuple(dims), dtype=float)
    for fi, c in zip(f, coords):
        d = np.linalg.norm(pts - c, axis=-1)
        v += fi * np.exp(-d / 2.0)

    cube = write_cube(lo, axes, dims, v, syms, coords,
                      'kind=mlp units=logP kernel=fauchere_exp_d_over_2 charges=crippen')
    meta = {
        'kind': 'mlp', 'units': 'MLP (Crippen/Fauchère)', 'method': 'crippen',
        'total_logp': float(f.sum()),
        'dims': dims.tolist(), 'spacing': spacing,
        'vmin': float(v.min()), 'vmax': float(v.max()),
        'iso_suggest': suggest_iso(v),
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
    from pyscf import gto
    pt = Chem.GetPeriodicTable()
    ecp = {}
    for s in set(syms):
        if pt.GetAtomicNumber(s) < ECP_FROM_Z:
            continue
        try:
            gto.basis.load_ecp(basis, s)
        except Exception:
            continue  # this basis defines no ECP for the element (e.g. Br)
        ecp[s] = basis
    return ecp


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

    syms, coords = mol_atoms(mol)
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
    return hosts


ALLOWED_HOSTS = _allowed_hosts()
ALLOWED_BASIS = ('sto-3g', '6-31g', '6-31g*', 'def2-svp')  # = DB CHECK minus 'none'


class Handler(BaseHTTPRequestHandler):
    def _host_ok(self) -> bool:
        host = (self.headers.get('Host') or '').rsplit(':', 1)[0]
        return host in ALLOWED_HOSTS

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
            max_seconds = min(max(req_seconds, 1.0), MAX_MAX_SECONDS)
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
                meta['stored'] = True
            self._send(200, {'ok': True, 'cube': cube, 'meta': meta})
        except FieldBudgetExceeded as e:
            # Not an error in the molecule — an error in what was asked of it.
            # Typed separately so the panel can offer "run it anyway with a
            # bigger budget" instead of showing a chemist a red failure for a
            # calculation that was merely slow.
            print(f'[field] budget exceeded: {e}', flush=True)
            self._send(200, {'ok': False, 'error': str(e), 'reason': 'budget'})
        except Exception as e:
            traceback.print_exc()
            reason = 'unsupported' if isinstance(e, ValueError) else 'internal'
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
