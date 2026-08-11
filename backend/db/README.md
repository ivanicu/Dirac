# Dirac database

PostgreSQL 18. The persistence and registration layer behind Dirac: the compound
registry, the measurements filed against it, the design artefacts Dirac produces,
and the audit trail over all of it.

**Dirac remains usable without this database.** Every facet in the browser works
against a loaded structure with no backend at all; the database adds registration,
persistence, cross-session sharing and provenance. A feature that *requires* it
must say so in the UI, per NORTHSTAR.

## Layout

| schema   | holds | answers |
|---|---|---|
| `meta`   | toolkits, sources, unit legality, migrations | *who computed this, with what* |
| `chem`   | compound / form / batch, descriptors, fingerprints, conformers | *what the molecule is* |
| `bio`    | targets, structures, assays, dose-response curves, results | *what was measured on it* |
| `design` | projects, series, ideas, pharmacophore models, screening runs | *what a chemist decided* |
| `app`    | content-addressed blobs, field-cube cache, workspaces | *what the client persists* |
| `audit`  | row history | *what we believed last month* |

## Operate

```bash
createdb dirac                                    # once
psql -U ivan -d dirac -v ON_ERROR_STOP=1 -f backend/db/migrations/001_core.sql
psql -U ivan -d dirac -v ON_ERROR_STOP=1 -f backend/db/migrations/002_vocabulary.sql
psql -U ivan -d dirac -v ON_ERROR_STOP=1 -f backend/db/migrations/003_audit_partition_root.sql
psql -U ivan -d dirac -v ON_ERROR_STOP=1 -f backend/db/migrations/004_scf_method_split.sql

# gates — run after EVERY migration; 22 attacks + 12 positive controls
psql -U ivan -d dirac -v ON_ERROR_STOP=1 -f backend/db/check_constraints.sql

# register the Designer screening library (68 molecules, RDKit-standardized parents)
backend/env/bin/python backend/db/load_library.py | psql -U ivan -d dirac -v ON_ERROR_STOP=1

# persist a pharmacophore model exported from the Designer facet
backend/env/bin/python backend/db/load_pharmacophore.py export.json | psql -U ivan -d dirac -v ON_ERROR_STOP=1
```

Loaders emit SQL on stdout rather than writing directly, so any load can be read
before it runs. Nothing in this directory imports `psycopg`.

## Three things to know before writing a query

**1. Identity is the standardized parent, never the SMILES.** `chem.compound` is
unique on the InChIKey of the parent after salt-stripping, neutralization and
tautomer canonicalization (`chem.standardizer.rules` records which protocol ran).
`chem.compound.smiles` is display-only — canonical SMILES is toolkit-version
dependent and joining on it will silently split a series.

**2. Results belong to batches, not compounds.** `bio.result.compound_id` exists
for query speed and is held true by a trigger; the scientific object is the
batch. Two batches at 98% and 62% purity are two experiments.

**3. `>` is not `=`.** Every result carries a `qualifier`. `bio.v_compound_activity`
reports `n_exact` and `n_censored` separately and computes the geometric mean over
exact rows only; when `has_censored` is true, the honest summary is a bound.
Averaging censored values is how a series acquires a SAR trend that is not there.

## Structure search

The RDKit cartridge is **not** installed on this server (no `mol` type, no GiST
substructure index), so search is split:

- **Similarity — in the database.** Morgan-2 fingerprints as `bit(2048)`, HNSW
  index with pgvector's `bit_jaccard_ops`. Tanimoto = `1 - (bits <%> query)`,
  positive-controlled against a hand-computed case.
- **Exact substructure — in the Python backend.** Fingerprint prescreen in SQL,
  match verification with RDKit 2026.03.5.

If `postgresql-18-rdkit` is ever installed (`sudo apt install postgresql-18-rdkit`,
then `CREATE EXTENSION rdkit`), add a `mol` column to `chem.compound` plus a GiST
index; nothing else in the schema has to change.

## Rules the schema enforces so code does not have to

- No `DELETE` for `dirac_app` on fact tables. Retraction is a column.
- A measured result with no batch, or a literature result with no citation, is
  rejected at INSERT.
- `(result_type, unit)` is a foreign key into `meta.result_type_unit`: "IC50 in
  percent" and "logP in nM" cannot be written.
- Every computed number references the `meta.toolkit` row that produced it.
  RDKit's logP moves between releases; a descriptor without a version is
  unreproducible.
- `app.blob` verifies `digest(bytes,'sha256') = sha256` — the content-addressed
  store cannot hold a mislabelled object.
- A cached quantum field must carry `converged = true`. The backend refuses to
  ship a decorative field and the cache may not resurrect one.
- The SCF method is two columns, not one string: `scf_reference` (RHF / UHF /
  ROHF / none) and `scf_converger` (diis / soscf / newton / none). Pass the
  backend's own label through `app.parse_scf_method('RHF+SOSCF')` to get the
  pair, and `app.scf_method_label(...)` to get it back for display. An
  unrecognised label raises: a new solver is a migration, not a free-text row.
