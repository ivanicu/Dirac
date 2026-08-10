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

PORT = 8901
BOHR = 0.529177210859  # Å per Bohr
MAX_QM_ATOMS = 120     # with hydrogens; STO-3G HF beyond this is not interactive
DEFAULT_BASIS = 'sto-3g'

_scf_cache: dict[str, object] = {}
_scf_lock = threading.Lock()


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
    charges = np.nan_to_num(charges, nan=0.0, posinf=0.0, neginf=0.0)

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
    result = {
        'gmol': gmol, 'mf': mf, 'energy': float(energy),
        'converged': bool(mf.converged), 'charge': charge, 'spin': spin,
        'natoms': len(syms), 'nbasis': int(gmol.nao), 'seconds': time.time() - t0,
    }
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
        'kind': kind, 'basis': basis, 'method': 'RHF' if res['spin'] == 0 else 'UHF',
        'scf_energy_ha': res['energy'], 'converged': True,
        'charge': res['charge'], 'spin': res['spin'],
        'natoms': res['natoms'], 'nbasis': res['nbasis'],
        'scf_seconds': round(res['seconds'], 2),
        'homo_ev': float(mo_energy[nocc - 1] * 27.2114),
        'lumo_ev': float(mo_energy[nocc] * 27.2114) if nocc < len(mo_energy) else None,
    }

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
            lambda m, p: cubegen.density(m, p, mf.make_rdm1()), gmol)
    elif kind == 'mep_qm':
        cube = _cubegen_to_text(
            lambda m, p: cubegen.mep(m, p, mf.make_rdm1()), gmol)
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
                             'pyscf': pyscf.__version__})
        else:
            self._send(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        if self.path != '/field':
            self._send(404, {'ok': False, 'error': 'not found'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            req = json.loads(self.rfile.read(length))
            molblock = req['molfile']
            kind = req.get('kind', 'mep')
            basis = req.get('basis', DEFAULT_BASIS)
            t0 = time.time()
            mol = prepare_mol(molblock)
            if kind == 'mep':
                cube, meta = field_mep(mol)
            else:
                cube, meta = field_quantum(mol, kind, basis)
            meta['total_seconds'] = round(time.time() - t0, 2)
            print(f"[field] kind={kind} atoms={mol.GetNumAtoms()} "
                  f"t={meta['total_seconds']}s", flush=True)
            self._send(200, {'ok': True, 'cube': cube, 'meta': meta})
        except Exception as e:
            traceback.print_exc()
            self._send(200, {'ok': False, 'error': str(e)})

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'Dirac fields backend on http://127.0.0.1:{port}', flush=True)
    server.serve_forever()
