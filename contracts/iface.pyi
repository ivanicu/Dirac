# Dirac backend — THE interface stub (Figure-2, Python half).
# Parsed by spec_lint M10: every `def` shown in SPEC.md exists here.
# Units in [brackets]. No `...` bodies carry unstated design: every param typed.
from typing import Literal, TypedDict

FieldKind = Literal['mep', 'mep_qm', 'homo', 'lumo', 'density', 'mlp']
Basis = Literal['sto-3g', '6-31g', '6-31g*', 'def2-svp']          # = ALLOWED_BASIS = DB CHECK minus 'none'
CacheSource = Literal['browser', 'memory', 'db', 'computed']       # browser never appears server-side
ErrorCode = Literal['PARSE', 'UNCONVERGED', 'UNPARAMETERIZED', 'BUDGET',
                    'UNSUPPORTED', 'TOO_LARGE', 'BAD_HOST', 'BAD_BASIS']
JobState = Literal['queued', 'running', 'done', 'failed', 'cancelled']  # seam: app.job (PLANNED)

class EmbedMeta(TypedDict):
    natoms: int                     # [count, with H]
    natoms_heavy: int               # [count]
    smiles_canonical: str           # RDKit canonical, heavy-atom mol
    inchikey: str                   # 27-char, parent
    mmff_optimized: bool
    mmff_energy_kcal: float | None  # [kcal/mol]; None = unparameterized atoms
    fragments_stripped: int         # [count] salts/co-solvents removed
    seed: int                       # ETKDG seed; same (smiles,seed) -> same bytes
    seconds: float                  # [s]

class FieldMeta(TypedDict, total=False):
    kind: FieldKind
    units: str                      # ALWAYS present (classical AND quantum)
    basis: Basis                    # quantum kinds only
    method: str                     # 'gasteiger'|'crippen'|'RHF'|'UHF'|'RHF+SOSCF'|'UHF+SOSCF'
    ecp: list[str]                  # element symbols carrying pseudopotentials
    scf_energy_ha: float            # [Hartree]
    homo_ev: float                  # [eV]
    lumo_ev: float | None           # [eV]; None = no virtual orbital in basis
    converged: bool
    natoms: int
    nbasis: int
    scf_seconds: float              # [s]
    total_seconds: float            # [s]
    cache: CacheSource
    stored: bool                    # True = background persist SCHEDULED (not confirmed)
    computed_at: str                # ISO-8601, cache hits only

def embed_molecule(smiles: str | None, molblock: str | None, seed: int = 42) -> tuple[str, EmbedMeta]:
    """SMILES|molfile -> heavy-atom 3D molfile (ETKDGv3+MMFF94, largest fragment).
    Raises ValueError(PARSE) on unparseable input; deterministic per (input, seed)."""

def prepare_mol(molblock: str) -> object:  # rdkit.Chem.Mol
    """Molfile -> RDKit mol with explicit H, coords preserved. The ONE parser."""

def run_scf(mol: object, basis: Basis, spin: int | None = None,
            max_seconds: float = 90.0) -> dict:
    """RHF/UHF + ECP(Z>=37 where basis defines) + SOSCF rescue + wall-clock
    deadline checked per DIIS cycle. Raises ValueError(BUDGET|UNCONVERGED).
    Returns {gmol, mf, energy[Ha], method, converged, charge, spin, natoms,
    nbasis, ecp, seconds[s]}. Cached (bounded LRU) keyed
    sha256(basis, ecp-set, syms, coords@1e-4A, charge, spin)."""

def field_mep(mol: object, spacing: float = 0.4, pad: float = 4.0) -> tuple[str, FieldMeta]:
    """[Angstrom] grid; Gasteiger Coulomb well [kcal/mol]. Raises
    ValueError(UNPARAMETERIZED) naming the atoms when Gasteiger yields
    non-finite charges (a zero field is silence, not a measurement)."""

def field_mlp(mol: object, spacing: float = 0.4, pad: float = 4.0) -> tuple[str, FieldMeta]:
    """Crippen logP x Fauchere exp(-d/2A) lipophilicity field [MLP]. Never DB-cached."""

def field_quantum(mol: object, kind: FieldKind, basis: Basis,
                  spin: int | None, max_seconds: float) -> tuple[str, FieldMeta]:
    """homo|lumo|density|mep_qm cube via run_scf + cubegen; cube-cost admission
    gate predicts grid seconds and refuses (BUDGET) with the override hint."""

def conformer_hash_for(mol_with_h: object) -> tuple[bytes, str]:
    """(32-byte conformer identity, parent InChIKey). Canonical-rank heavy atoms,
    centroid, principal axes, third axis = cross(a1,a2) so det=+1 BY CONSTRUCTION
    (an enantiomer cannot reach the same frame). Quantized 0.01 [Angstrom]."""

def db_get_cube(molfile_sha: bytes, kind: FieldKind, basis: str) -> tuple[str, FieldMeta] | None:
    """Read via app.v_field_cube_current ONLY (superseded producers unservable)."""

def db_put_cube(molfile_sha: bytes, kind: FieldKind, basis: str, cube: str,
                meta: FieldMeta, mol: object | None) -> None:
    """Background persist: blob+row. Stamps producer_id, compound_id+conformer_hash
    (all-or-nothing coarse key). Failures logged, never raised to the request."""

def ecp_for(syms: list[str], basis: Basis) -> dict[str, str]:
    """{element: basis} for Z>=37 where the basis defines an ECP. Without this,
    iodine runs all-electron: converges, balances charge, wrong by 58 kcal/mol."""

# ── SEAMS (declared, minimal, so the terminal state needs no rewrite) ────────
def register_method(method_id: str, source_sha256: bytes, in_schema: dict,
                    out_schema: dict, exec_class: Literal['interactive', 'job'],
                    capabilities: dict) -> str:
    """PLANNED seam: generalizes meta.register_producer. version := hash of the
    compute unit + its import closure (NEVER a whole service file). Returns row id."""

def job_create(method_id: str, inputs: dict, params: dict) -> str:
    """PLANNED seam: one app.job row per computation (even 0.1s ones). The
    executor behind it (in-thread -> process pool -> cluster) swaps freely
    because state lives here, not in the process."""
