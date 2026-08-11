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
import io
import json
import os
import sys
import tempfile
import threading
import time
import traceback
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
DB_DSN = 'dbname=dirac user=ivan'
CUBE_MEDIA_TYPE = 'chemical/x-gaussian-cube'

_scf_cache: dict[str, object] = {}
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


def _db(): return psycopg.connect(DB_DSN, autocommit=True)


def db_init() -> bool:
    global _db_ok
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
        _db_ok = True
        print(f'[db] persistent cube cache ON (dirac db, toolkits: {list(_toolkit_ids)})', flush=True)
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
            cur.execute(
                'SELECT b.bytes, fc.scf_energy_ha, fc.converged, fc.n_atoms, '
                '       fc.n_basis, fc.homo_ev, fc.lumo_ev, fc.seconds, '
                '       app.scf_method_label(fc.scf_reference, fc.scf_converger), '
                '       fc.computed_at '
                'FROM app.field_cube fc JOIN app.blob b ON b.sha256 = fc.blob_sha256 '
                'WHERE fc.molfile_sha256 = %s AND fc.kind = %s AND fc.basis = %s',
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


def db_put_cube(molfile_sha: bytes, kind: str, basis: str, cube: str, meta: dict):
    """Persist a computed cube. Quantum rows reach here only if converged —
    and the schema CHECK would reject them anyway if this invariant broke."""
    if not _db_ok:
        return
    try:
        blob = cube.encode()
        blob_sha = hashlib.sha256(blob).digest()
        toolkit = _toolkit_ids['rdkit' if kind == 'mep' else 'pyscf']
        label = meta.get('method', 'gasteiger' if kind == 'mep' else None)
        with _db() as conn, conn.cursor() as cur:
            cur.execute(
                'INSERT INTO app.blob (sha256, media_type, byte_len, bytes) '
                'VALUES (%s, %s, %s, %s) ON CONFLICT (sha256) DO NOTHING',
                (blob_sha, CUBE_MEDIA_TYPE, len(blob), blob))
            cur.execute(
                'INSERT INTO app.field_cube '
                '  (molfile_sha256, kind, basis, blob_sha256, scf_reference, '
                '   scf_converger, scf_energy_ha, converged, n_atoms, n_basis, '
                '   homo_ev, lumo_ev, seconds, toolkit_id) '
                'SELECT %s, %s, %s, %s, p.scf_reference, p.scf_converger, '
                '       %s, %s, %s, %s, %s, %s, %s, %s '
                'FROM app.parse_scf_method(%s) AS p '
                'ON CONFLICT (molfile_sha256, kind, basis) DO NOTHING',
                (molfile_sha, kind, basis, blob_sha,
                 meta.get('scf_energy_ha'),
                 True if kind != 'mep' else None,
                 meta.get('natoms'), meta.get('nbasis'),
                 meta.get('homo_ev'), meta.get('lumo_ev'),
                 meta.get('scf_seconds') if kind != 'mep' else meta.get('total_seconds'),
                 toolkit, label))
        print(f'[db] cached kind={kind} basis={basis}', flush=True)
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


# ── molfile → RDKit mol with explicit hydrogens, coordinates preserved ──────

def prepare_mol(molblock: str) -> Chem.Mol:
    mol = Chem.MolFromMolBlock(molblock, removeHs=False, sanitize=True)
    if mol is None:
        mol = Chem.MolFromMolBlock(molblock, removeHs=False, sanitize=False)
        if mol is None:
            raise ValueError('RDKit cannot parse the molfile')
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
    }
    return cube, meta


# ── quantum fields (pyscf HF + cubegen) ─────────────────────────────────────

def run_scf(mol: Chem.Mol, basis: str):
    from pyscf import gto, scf

    syms, coords = mol_atoms(mol)
    if len(syms) > MAX_QM_ATOMS:
        raise ValueError(
            f'{len(syms)} atoms (with H) exceeds the interactive QM cap of {MAX_QM_ATOMS}'
        )
    charge = sum(a.GetFormalCharge() for a in mol.GetAtoms())
    nelec = sum(a.GetAtomicNum() for a in mol.GetAtoms()) - charge
    spin = nelec % 2  # singlet or doublet; anything fancier needs explicit input

    key = hashlib.sha256(
        (basis + repr(syms) + repr(coords.round(4).tolist()) + str(charge)).encode()
    ).hexdigest()
    with _scf_lock:
        if key in _scf_cache:
            return _scf_cache[key]

    gmol = gto.M(
        atom=[(s, tuple(c)) for s, c in zip(syms, coords)],
        unit='Angstrom', basis=basis, charge=charge, spin=spin,
        verbose=0,
    )
    mf = scf.RHF(gmol) if spin == 0 else scf.UHF(gmol)
    mf.max_cycle = 120
    t0 = time.time()
    energy = mf.kernel()
    method = 'RHF' if spin == 0 else 'UHF'
    if not mf.converged:
        # Plain DIIS stalls on transition-metal ligands (measured: Fe-heme,
        # 120 cycles, no convergence). Second-order SCF restarted from the
        # stalled MOs is the standard rescue.
        mf = mf.newton()
        energy = mf.kernel()
        method += '+SOSCF'
    result = {
        'gmol': gmol, 'mf': mf, 'energy': float(energy), 'method': method,
        'converged': bool(mf.converged), 'charge': charge, 'spin': spin,
        'natoms': len(syms), 'nbasis': int(gmol.nao), 'seconds': time.time() - t0,
    }
    # Only positive results are cached: caching an unconverged SCF would make
    # every retry fail instantly from the cache, forever.
    if result['converged']:
        with _scf_lock:
            _scf_cache[key] = result
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


def field_quantum(mol: Chem.Mol, kind: str, basis: str):
    from pyscf.tools import cubegen

    res = run_scf(mol, basis)
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
        'scf_seconds': round(res['seconds'], 2),
        'homo_ev': float(mo_energy[nocc - 1] * 27.2114),
        'lumo_ev': float(mo_energy[nocc] * 27.2114) if nocc < len(mo_energy) else None,
    }

    # UHF rdm1 is (alpha, beta); cubegen wants one total density matrix.
    dm = mf.make_rdm1()
    if res['spin'] != 0:
        dm = dm[0] + dm[1]

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
            lambda m, p: cubegen.mep(m, p, dm, nx=50, ny=50, nz=50), gmol)
    else:
        raise ValueError(f'unknown quantum kind {kind!r}')
    return cube, meta


# ── HTTP layer ──────────────────────────────────────────────────────────────

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
        if self.path == '/health':
            import pyscf
            import rdkit
            self._send(200, {'ok': True, 'rdkit': rdkit.__version__,
                             'pyscf': pyscf.__version__,
                             'db_cache': 'on' if _db_ok else 'off'})
        else:
            self._send(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        if self.path == '/embed':
            try:
                length = int(self.headers.get('Content-Length', '0'))
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
            req = json.loads(self.rfile.read(length))
            molblock = req['molfile']
            kind = req.get('kind', 'mep')
            basis = req.get('basis', DEFAULT_BASIS)
            # Classical mep has no basis; key it as 'none' so the cache row
            # satisfies the schema's classical/quantum pairing checks. mlp is
            # not in the app.field_kind enum and costs ~0.03 s — never cached.
            basis_key = 'none' if kind == 'mep' else basis
            molfile_sha = hashlib.sha256(molblock.encode()).digest()
            cacheable = kind in ('mep', 'mep_qm', 'homo', 'lumo', 'density')

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
                cube, meta = field_quantum(mol, kind, basis)
            meta['total_seconds'] = round(time.time() - t0, 2)
            meta['cache'] = 'computed'
            print(f"[field] kind={kind} atoms={mol.GetNumAtoms()} "
                  f"t={meta['total_seconds']}s", flush=True)
            if cacheable:
                db_put_cube(molfile_sha, kind, basis_key, cube, meta)
            self._send(200, {'ok': True, 'cube': cube, 'meta': meta})
        except Exception as e:
            traceback.print_exc()
            self._send(200, {'ok': False, 'error': str(e)})

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    db_init()
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'Dirac fields backend on http://127.0.0.1:{port}', flush=True)
    server.serve_forever()
