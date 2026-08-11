# Dirac fields backend

Local daemon that turns the focused ligand's molfile into Gaussian-cube scalar
fields for the **Fields** master-tab. The molfile arrives in scene coordinates,
so every cube renders registered with the mol\* scene — there is no alignment
step anywhere.

## Run

```bash
backend/env/bin/python backend/field_server.py     # 127.0.0.1:8901
```

`backend/env` is a self-contained conda env (gitignored): RDKit 2026.03 +
pyscf 2.14 + numpy. Recreate with:

```bash
~/miniforge3/bin/conda create -p backend/env python=3.12 -y
backend/env/bin/pip install rdkit pyscf numpy
```

## Protocol

| Route | In | Out |
|---|---|---|
| `GET /health` | — | `{ok, rdkit, pyscf}` |
| `POST /field` | `{molfile, kind, basis?}` | `{ok, cube, meta}` or `{ok: false, error}` |

Kinds: `mep` (Gasteiger/Coulomb, ~0.1 s) · `homo` · `lumo` · `density` ·
`mep_qm` (pyscf HF + cubegen). `basis`: `sto-3g` (default) or `6-31g`.

## Persistent cube cache

With the `dirac` PostgreSQL database up (see `backend/db/`), every computed
cube is persisted to `app.field_cube` keyed `(sha256(molfile), kind, basis)`
with the cube bytes content-addressed in `app.blob`. A repeated request —
including after a daemon restart — is served from the database (~50 ms
measured vs 5 s recompute; a 6-minute Fe-heme SCF now survives restarts).
`GET /health` reports `db_cache: on|off`; without the database the daemon
falls back to the in-memory SCF cache only. SCF provenance is stored split
(`scf_reference` × `scf_converger`, migration 004) via
`app.parse_scf_method()`; the schema independently enforces that an
unconverged quantum field cannot be cached (`field_cube_check`, verified by
direct negative control: SQLSTATE 23514).

## Honesty invariants

- An SCF that does not converge returns an **error**, never a field. Plain
  DIIS that stalls is retried once with second-order SCF (`newton()`);
  `meta.method` says which path produced the numbers.
- Only converged SCF results are cached (per geometry × basis). Caching a
  failure would make every retry fail instantly from the cache.
- `meta` always carries basis, method, SCF energy, HOMO/LUMO (eV),
  convergence, atom/basis counts, and wall time.

## Measured timings (24-thread CPU, STO-3G)

| Case | Time |
|---|---|
| MEP, 50 atoms | 0.1 s |
| First SCF, caffeine (24 atoms) | 0.6 s |
| First SCF, retinoic acid (50 atoms) | ~3 s; orbital cube +2 s |
| `mep_qm`, 50 atoms, 50³ grid | ~12 s after cached SCF |
| Fe-heme (75 atoms) | DIIS stalls at 120 cycles → SOSCF rescue, minutes |

Known limits: `MAX_QM_ATOMS = 120` (with hydrogens); transition-metal ligands
are minutes, not seconds; no request cancellation — the panel's busy state is
per-page and one compute runs at a time.
