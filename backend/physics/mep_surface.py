"""Electrostatic potential ON the molecular surface — and the σ-hole it reveals.

Standard medicinal-chemistry software renders the electrostatic potential, if
at all, as a volume isosurface. That picture cannot show the one thing a
halogen bond depends on: the potential is ANISOTROPIC around a halogen. Along
the C–X axis, beyond the halogen, there is a small POSITIVE cap — the σ-hole —
while a negative belt wraps the same atom perpendicular to that axis. A single
isovalue slice through the volume shows neither.

This module computes V(r) on the 0.001 a.u. isodensity surface, which is
Politzer and Murray's definition of "the molecular surface" for exactly this
purpose, and reports the surface extrema V_S,max / V_S,min that their work
established as the quantities correlating with halogen- and hydrogen-bond
strength. The output is a point cloud with per-point potential (for rendering)
plus a table of extrema (for deciding).

Physics validation (backend/physics/validate.py, run it before trusting a
number): the implementation must reproduce two orderings that are not free
parameters —

    CH3Cl  -5.2   CF3Cl  +15.8      electron withdrawal switches the hole ON
    CH3Br  +4.0   CF3Br  +24.9      polarizability: Br > Cl

both measured with this code at RHF/def2-SVP. A σ-hole implementation that
gets the ANISOTROPY right but the ordering wrong is fitting, not computing.

Units: potentials in kcal/mol per unit charge, coordinates in Ångström
(the molfile's frame, so everything lands registered with the mol* scene).
"""
from __future__ import annotations

import time

import numpy as np
from rdkit import Chem
from pyscf import dft, gto, scf

BOHR = 0.529177210859
HARTREE_KCAL = 627.5094740631
DEFAULT_ISOVALUE = 0.001          # a.u., the Politzer/Murray surface
# def2-SVP, not 6-31G*: the latter has NO IODINE, and iodine is the strongest
# halogen-bond donor there is — the single case this module most exists for.
DEFAULT_BASIS = 'def2-svp'
MAX_QM_ATOMS = 120

# def2 basis sets replace the core of every element from Rb (Z=37) onward with
# an effective core potential, and pyscf does NOT attach it just because you
# asked for 'def2-svp'. Without it iodine is treated all-electron by a basis
# never designed for that, and the result is WRONG WITHOUT COMPLAINING:
# charge balances, the potential still decays to zero at infinity, the SCF
# converges — and iodobenzene's σ-hole comes out at -37 kcal/mol instead of
# +21, with the anisotropy INVERTED. The one element this module exists for
# was the one it got wrong, and nothing in the output said so.
ECP_FROM_Z = 37


def _ecp_for(atoms, basis: str) -> dict:
    """Attach the matching ECP to every heavy element the basis defines one for."""
    from rdkit.Chem import GetPeriodicTable
    table = GetPeriodicTable()
    ecp = {}
    for symbol in {s for s, _ in atoms}:
        if table.GetAtomicNumber(symbol) < ECP_FROM_Z:
            continue
        try:
            gto.basis.load_ecp(basis, symbol)
        except Exception:                       # noqa: BLE001 — basis defines none
            continue
        ecp[symbol] = basis
    return ecp

# Atoms that can carry a σ-hole (group 15-17 heavy elements). Fluorine is
# included deliberately: it almost never has one, and a tool that silently
# omits the negative case cannot be checked.
SIGMA_HOLE_ELEMENTS = {'Cl', 'Br', 'I', 'F', 'S', 'Se', 'Te', 'P', 'As'}
SIGMA_HOLE_MIN_ANGLE = 150.0      # degrees, C–X···P; a real σ-hole is ~180°


def _prepare(molblock: str):
    """Molfile → (rdkit mol with H, pyscf mol). Coordinates are preserved."""
    rdmol = Chem.MolFromMolBlock(molblock, removeHs=False, sanitize=True)
    if rdmol is None:
        rdmol = Chem.MolFromMolBlock(molblock, removeHs=False, sanitize=False)
        if rdmol is None:
            raise ValueError('RDKit cannot parse the molfile')
        Chem.SanitizeMol(rdmol, Chem.SanitizeFlags.SANITIZE_ALL
                         ^ Chem.SanitizeFlags.SANITIZE_KEKULIZE)
    rdmol = Chem.AddHs(rdmol, addCoords=True)

    conf = rdmol.GetConformer()
    atoms = []
    for atom in rdmol.GetAtoms():
        p = conf.GetAtomPosition(atom.GetIdx())
        atoms.append((atom.GetSymbol(), (p.x, p.y, p.z)))
    if len(atoms) > MAX_QM_ATOMS:
        raise ValueError(f'{len(atoms)} atoms exceeds the interactive cap of {MAX_QM_ATOMS}')

    charge = Chem.GetFormalCharge(rdmol)
    nelec = sum(a.GetAtomicNum() for a in rdmol.GetAtoms()) - charge
    return rdmol, atoms, charge, nelec % 2


def _fibonacci_directions(n: int) -> np.ndarray:
    """Near-uniform directions on the unit sphere. Deterministic, so two runs
    on the same molecule produce the same surface and the same V_S,max."""
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = np.pi * (1 + 5 ** 0.5) * i
    return np.stack([np.cos(theta) * np.sin(phi),
                     np.sin(theta) * np.sin(phi),
                     np.cos(phi)], axis=1)


class _Field:
    """Density and potential evaluators over a converged SCF."""

    def __init__(self, mol, dm):
        self.mol = mol
        self.dm = dm
        self.nao = mol.nao

    def rho(self, pts_ang: np.ndarray) -> np.ndarray:
        p = np.atleast_2d(np.asarray(pts_ang, float)) / BOHR
        return dft.numint.eval_rho(self.mol, dft.numint.eval_ao(self.mol, p), self.dm)

    def mep(self, pts_ang: np.ndarray) -> np.ndarray:
        """Electrostatic potential in kcal/mol per unit charge.

        Chunked because `int1e_grids` materialises an (npoints, nao, nao)
        tensor: 10 000 points over 400 basis functions is 12.8 GB in one call
        and an OOM kill rather than an exception.
        """
        p = np.atleast_2d(np.asarray(pts_ang, float)) / BOHR
        chunk = max(1, int(2.0e8 / max(self.nao * self.nao * 8, 1)))
        out = np.empty(len(p))
        for start in range(0, len(p), chunk):
            block = p[start:start + chunk]
            ints = self.mol.intor('int1e_grids', grids=block)
            v = -np.einsum('pij,ij->p', ints, self.dm)          # electrons
            for i in range(self.mol.natm):                       # nuclei
                v += self.mol.atom_charge(i) / np.linalg.norm(
                    block - self.mol.atom_coord(i), axis=1)
            out[start:start + chunk] = v
        return out * HARTREE_KCAL


def _outer_isosurface_points(field: _Field, centers: np.ndarray,
                             isovalue: float, per_atom: int) -> np.ndarray:
    """Sample the OUTER 0.001 a.u. envelope.

    For each atom and each direction, the ray is walked INWARD from outside
    the molecule and stopped at the first crossing. Walking outward from the
    atom instead would stop at the first crossing it meets, which for a buried
    atom is an interior pocket wall — a surface that looks plausible and is
    not the one V_S,max is defined on.
    """
    directions = _fibonacci_directions(per_atom)
    span = float(np.max(np.linalg.norm(centers - centers.mean(axis=0), axis=1))) + 6.0

    found = []
    for center in centers:
        starts = center + directions * span
        # Coarse inward march, vectorised over directions.
        t = np.full(len(directions), span)
        hit = np.zeros(len(directions), dtype=bool)
        t_hit = np.zeros(len(directions))
        step = 0.25
        while t.max() > 0.3:
            probe = center + directions * t[:, None]
            inside = field.rho(probe) > isovalue
            newly = inside & ~hit
            t_hit[newly] = t[newly]
            hit |= newly
            t = np.where(hit, t, t - step)
            if hit.all():
                break
        if not hit.any():
            continue
        # Bisect between the last outside point and the first inside point.
        lo = t_hit[hit]                       # inside
        hi = lo + step                        # outside
        d = directions[hit]
        for _ in range(18):
            mid = 0.5 * (lo + hi)
            inside = field.rho(center + d * mid[:, None]) > isovalue
            lo = np.where(inside, mid, lo)
            hi = np.where(inside, hi, mid)
        found.append(center + d * (0.5 * (lo + hi))[:, None])

    pts = np.vstack(found)

    # Deduplicate: rays from different atoms converge on the same envelope.
    key = np.round(pts / 0.25).astype(np.int64)
    _, keep = np.unique(key, axis=0, return_index=True)
    return pts[np.sort(keep)]


def _extrema(points: np.ndarray, values: np.ndarray, radius: float = 1.2,
             top: int = 8, always_keep: np.ndarray | None = None):
    """Local maxima and minima of V on the surface point cloud.

    `always_keep` marks points sitting on σ-hole-capable atoms, and they are
    exempt from the top-N cut. Ranking every maximum together and keeping the
    eight largest silently deleted EVERY iodine σ-hole in the coverage sweep:
    on 5-iodouracil the N–H maxima reach +79 kcal/mol while iodine's cap is a
    modest +15 to +25, so the chemically decisive feature ranked ninth and
    disappeared. The truncation was a display limit that had quietly become a
    scientific filter.
    """
    from scipy.spatial import cKDTree

    tree = cKDTree(points)
    neighbours = tree.query_ball_point(points, radius)
    maxima, minima = [], []
    for i, nb in enumerate(neighbours):
        if len(nb) < 4:
            continue
        v = values[i]
        others = [values[j] for j in nb if j != i]
        if not others:
            continue
        if v >= max(others):
            maxima.append(i)
        elif v <= min(others):
            minima.append(i)

    def _cluster(idx, sign, exempt=None):
        """Keep one representative per spatial cluster, best value first."""
        chosen = []
        for i in sorted(idx, key=lambda k: -sign * values[k]):
            protected = exempt is not None and exempt[i]
            if len(chosen) >= top and not protected:
                continue
            if all(np.linalg.norm(points[i] - points[j]) > 2.0 for j in chosen):
                chosen.append(i)
        return chosen

    return _cluster(maxima, +1, always_keep), _cluster(minima, -1)


def _classify_sigma_hole(rdmol, atom_positions: np.ndarray, point: np.ndarray,
                         nearest_atom: int):
    """Is this maximum a σ-hole? Then it lies on the extension of a covalent
    bond to the heavy atom, at ~180°, and the angle is the evidence."""
    atom = rdmol.GetAtomWithIdx(int(nearest_atom))
    if atom.GetSymbol() not in SIGMA_HOLE_ELEMENTS:
        return None
    best = None
    for nb in atom.GetNeighbors():
        r_partner = atom_positions[nb.GetIdx()]
        r_x = atom_positions[nearest_atom]
        v1 = r_partner - r_x
        v2 = point - r_x
        cosine = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-12))
        angle = float(np.degrees(np.arccos(np.clip(cosine, -1, 1))))
        if best is None or angle > best['angle_deg']:
            best = {'angle_deg': round(angle, 1),
                    'bonded_to': nb.GetSymbol(),
                    'bonded_to_index': int(nb.GetIdx())}
    if best is None:
        return None
    best['is_sigma_hole'] = best['angle_deg'] >= SIGMA_HOLE_MIN_ANGLE
    return best


def compute_surface_mep(molblock: str, basis: str = DEFAULT_BASIS,
                        isovalue: float = DEFAULT_ISOVALUE,
                        points_per_atom: int = 120):
    """Full σ-hole analysis for one molecule.

    Returns a dict with `points` (Å, in the molfile frame), `values`
    (kcal/mol), `extrema`, and `meta`. Callers that only need the numbers can
    ignore the cloud; callers that only need the picture can ignore the table.
    """
    t0 = time.time()
    rdmol, atoms, charge, spin = _prepare(molblock)

    mol = gto.M(atom=[(s, c) for s, c in atoms], unit='Angstrom',
                basis=basis, ecp=_ecp_for(atoms, basis) or None,
                charge=charge, spin=spin, verbose=0)
    mf = scf.RHF(mol) if spin == 0 else scf.UHF(mol)
    mf.max_cycle = 120
    energy = mf.kernel()
    if not mf.converged:
        # Same rule as the fields backend: a field from an unconverged SCF is
        # decoration, and V_S,max read off it is a number with no referent.
        raise ValueError(f'SCF did not converge (E={energy:.6f} Ha, basis={basis})')
    t_scf = time.time() - t0

    field = _Field(mol, mf.make_rdm1())
    centers = np.array([c for _, c in atoms])

    t1 = time.time()
    points = _outer_isosurface_points(field, centers, isovalue, points_per_atom)
    values = field.mep(points)
    t_surface = time.time() - t1

    from scipy.spatial import cKDTree
    atom_tree = cKDTree(centers)
    _, nearest = atom_tree.query(points)

    # Points on an atom that can carry a σ-hole survive the top-N cut.
    hole_capable = np.array([atoms[int(a)][0] in SIGMA_HOLE_ELEMENTS for a in nearest])
    max_idx, min_idx = _extrema(points, values, always_keep=hole_capable)
    extrema = []
    for kind, idx_list in (('maximum', max_idx), ('minimum', min_idx)):
        for i in idx_list:
            a = int(nearest[i])
            entry = {
                'kind': kind,
                'value_kcal_per_mol': round(float(values[i]), 2),
                'position': [round(float(x), 3) for x in points[i]],
                'atom_index': a,
                'element': atoms[a][0],
                'distance_to_atom_a': round(float(np.linalg.norm(points[i] - centers[a])), 3),
            }
            if kind == 'maximum':
                sigma = _classify_sigma_hole(rdmol, centers, points[i], a)
                if sigma:
                    # An anion's entire surface is negative, so the absolute
                    # value cannot decide whether there is a σ-hole: PF6- has a
                    # real one at -46 kcal/mol, against a belt at -154. The
                    # anisotropy against the SAME atom's belt is the quantity
                    # that means the same thing for ions and neutrals.
                    on_atom = nearest == a
                    belt = values[on_atom & (np.abs(values - values[i]) > 0)]
                    sigma['belt_value_kcal_per_mol'] = (
                        round(float(belt.min()), 2) if belt.size else None)
                    if sigma['belt_value_kcal_per_mol'] is not None:
                        sigma['anisotropy_kcal_per_mol'] = round(
                            float(values[i]) - sigma['belt_value_kcal_per_mol'], 2)
                    sigma['positive_cap'] = bool(values[i] > 0)
                    entry['sigma_hole'] = sigma
            extrema.append(entry)

    sigma_holes = [e for e in extrema
                   if e.get('sigma_hole', {}).get('is_sigma_hole')]

    return {
        'points': points.astype(np.float32),
        'values': values.astype(np.float32),
        'extrema': extrema,
        'meta': {
            'method': 'RHF' if spin == 0 else 'UHF',
            'basis': basis,
            'ecp': sorted(_ecp_for(atoms, basis)) or None,
            'scf_energy_ha': float(energy),
            'converged': True,
            'charge': charge,
            'spin': spin,
            'n_atoms': len(atoms),
            'n_basis': int(mol.nao),
            'surface': f'{isovalue} a.u. isodensity (Politzer/Murray)',
            'n_surface_points': int(len(points)),
            'v_s_max_kcal_per_mol': round(float(values.max()), 2),
            'v_s_min_kcal_per_mol': round(float(values.min()), 2),
            'sigma_holes_found': len(sigma_holes),
            'scf_seconds': round(t_scf, 2),
            'surface_seconds': round(t_surface, 2),
            'total_seconds': round(time.time() - t0, 2),
        },
    }


def mep_at_points(molblock: str, points_ang, basis: str = DEFAULT_BASIS):
    """Potential at caller-supplied coordinates.

    Exists so the front end can colour mol*'s OWN molecular surface: mol*
    already builds a better surface mesh than this module should try to
    duplicate, and the physics belongs where the wavefunction is.
    """
    _, atoms, charge, spin = _prepare(molblock)
    mol = gto.M(atom=[(s, c) for s, c in atoms], unit='Angstrom',
                basis=basis, ecp=_ecp_for(atoms, basis) or None,
                charge=charge, spin=spin, verbose=0)
    mf = scf.RHF(mol) if spin == 0 else scf.UHF(mol)
    mf.max_cycle = 120
    energy = mf.kernel()
    if not mf.converged:
        raise ValueError(f'SCF did not converge (E={energy:.6f} Ha, basis={basis})')
    values = _Field(mol, mf.make_rdm1()).mep(np.asarray(points_ang, float))
    return values.astype(np.float32), {
        'method': 'RHF' if spin == 0 else 'UHF', 'basis': basis,
        'scf_energy_ha': float(energy), 'n_points': int(len(values)),
    }
