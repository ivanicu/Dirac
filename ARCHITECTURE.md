# Dirac — Architecture

This is the WHY and the growth map. For the WHAT — types, wire schemas, state
machines, failure model, named constants — read `SPEC.md`; this file does not
restate it. **Four documents, four rot rates, one home per fact:**

| document | answers | rots when |
|---|---|---|
| `ARCHITECTURE.md` | why the layers are cut where they are | a layer boundary moves |
| `SPEC.md` | the interfaces, flows and failure model | a contract changes |
| `STATUS.md` | what is actually built, and what is a seam with nothing writing to it | any commit |
| `ROADMAP.md` | how these seams reach the terminal state without a rewrite | the terminal state changes |

**Deployment status lives in exactly one place — `STATUS.md`.** Short version, so
nobody has to guess: NOT DEPLOYED, no auth, no public URL, three hand-started
processes. Verified against `HEAD` `54c24d4`, 2026-08-11. The previous version
of this file (removed here) was written 25 commits earlier, at `545f9f2`
(2026-08-10 20:30) — before either of the two migrations that this document is
mostly about existed. It described an `/embed` endpoint that returns "a
molecular fingerprint embedding," a `/health` shape that has never shipped,
a 400 status code the server has never sent, and a cache "keyed on compounds"
that has in fact never been true. All four are corrected below by reading the
code that actually runs, not by editing the old sentences.

## Read this in 10 minutes

1. **`SPEC.md`** — the buildable spec. Six layers, three data flows with
   error edges, three STATE×EVENT tables, the join ledger, the named-constant
   ledger, the proxy ledger, a 12-row failure model where every row is an
   observed incident, not a hypothetical.
2. **`backend/db/migrations/006_producer_identity.sql`** — why a cache is
   keyed on who computed a result, not only on what was asked. The best-argued
   file in the repo; read the comment block before the SQL.
3. **`backend/db/migrations/007_method_registry_and_job_ledger.sql`** — the
   two seams (method registry, job ledger) that let this grow into a
   multi-method scientific platform without a rewrite. Read `SEAM A` /
   `SEAM B` in the header comment.
4. **`backend/tests/`** — the gate suite that keeps the two migrations above
   honest (58 gates as of `8692d1f`: 37 rejections + 21 positive controls,
   referenced from the migration's own commit message). Being populated as a
   standalone directory as of this writing; `backend/db/check_constraints.sql`
   is the same gate suite's current on-disk form.
5. **`design/tokens.css`** — the single source of visual truth (170 lines).
   Every facet that draws a categorical or sequential channel reads from
   here; `design/INVENTORY.md` names the one place that still doesn't
   (`design/INVENTORY.md` B1: the app's inline `:root` block, migration
   planned as phase P2).

## What runs where

Dirac is a browser-native molecular design tool (mol* + RDKit-JS/WASM, no
network round-trip for the core workflow) with one **optional** local Python
backend for computation WASM cannot do: real 3D embedding (RDKit's ETKDG,
which the vendored WASM build does not expose) and real quantum chemistry
(pyscf HF, which no browser build offers at all). The backend is two
independent daemons today — `backend/field_server.py` on `:8901` and
`backend/physics/server.py` on `:8902` — plus PostgreSQL for durable results.
Every fact below is read from the code at the paths cited, not from a prior
description of it.

## Layer map

```
 X  CONTRACTS         contracts/iface.pyi · contracts/iface.d.ts · design/tokens.css
                       ── read by every layer below; owned by none of them ──

 L5 PRESENTATION       src/app.frontend.facets.molstar-rdkit.editable/
                       LabShell (index.ts) + facets: property-cockpit,
                       field-wells, pharmacophore-designer, bond-atlas
                       │
                       │  (exception 1: L5 may import mol* directly —
                       │   Shape/Volume representations need the plugin,
                       │   not a wrapper around it)
                       ▼
 L4 CLIENT DATA        LigandStore · FieldCache · BackendClient · JobTracker ·
                       ThemeService  — declared in iface.d.ts.
                       NOT YET A SEPARATE MODULE: today every one of these is
                       reimplemented inline inside index.ts and inside
                       field-wells/index.ts (its own cubeCache, its own
                       molfile tracking). iface.d.ts's own comment calls this
                       "the facet seam (target shape; current integration =
                       3 named functions)."
                       │
                       │  (exception 2: L4.5 may be imported by L5 directly,
                       │   not gated behind L4 — most facets need chemistry
                       │   before there is anything to cache)
                       ▼
 L4.5 BROWSER COMPUTE   src/chemistry.backend.perception.rdkit-wasm.editable/
                       RDKit-WASM substrate. Law: no DOM, no fetch, no plugin
                       import — the one rule that keeps this layer testable
                       outside a browser. Facets AND L4 both import it.
                       │
                       ▼  (crosses the network — the only layer boundary that does)
 L3 SERVICE            backend/field_server.py (today, one file, HTTP+compute
                       mixed) → backend/server/ (target: routes+envelope only)
                       │
                       ▼
 L2 COMPUTE CORE        ⬜ PLANNED as backend/dirac_chem/. Today the compute
                       functions (embed_molecule, run_scf, field_mep, ...)
                       live inline in field_server.py and backend/physics/.
                       meta.method + method_registry.py already treat these
                       as addressable units (see Growth map) — the directory
                       split is the one piece not yet done.
                       │
                       ▼
 L1 DATA               PostgreSQL `dirac`: meta / chem / bio / design / app /
                       audit schemas. Migrations 000-007 applied.
                       │
                       ▼
 L0 RUNTIME            ⬜ PLANNED. No systemd units, no dirac-specific CI gate
                       (.github/workflows/ has docs.yml + node.yml only).
                       Six sessions currently edit one working tree directly.
```

Dependency direction is strictly downward except the two exceptions marked
above. Both exceptions are declared, not accidental: SPEC.md §4.1 states them
in one line each, and this map exists so a third reader doesn't have to
reconstruct why `import`s that "shouldn't" exist do.

## Why the three most expensive boundaries are where they are

Architecture decisions in this repo are not proposed and then built; they are
extracted from an incident that already happened, on this tree, on
2026-08-10. Three examples, each verified against the commit that introduced
the fix and the code that still enforces it.

**1. Shared chemistry substrate (L4.5) exists because duplication silently
corrupts, not because of a style preference.** `ligandLociToMolfile` was
defined once in `semantic-chemistry-rdkit.ts` and again — as
`parseLigandLoci` + `buildMolfile` — in `pharmacophore-features.ts`, with a
third inline copy of a shared helper in `bond-order-3d.ts`. The V2000
molfile counts-line formatting (column-34 alignment, the `999 V2000` version
field) was written wrong three times across those copies before commit
`b3dbc9b` collapsed them into one file, `chemistry.../ligand-pipeline.ts`.
Commit `8692d1f`, landed the same day, names the mechanism directly: *"six
dead fields were not a chemistry bug, they were 'one fact, three homes'
collapsing into one home that happened to be the wrong copy."* The witness
test for this — `_spec/ligand-pipeline.spec.ts` — is titled "Violation
witnesses for the V2000 counts line" and exists specifically so a fourth
wrong copy fails a test instead of shipping a fourth silent regression. This
is why L4.5 is one directory with one law (no DOM, no fetch, no plugin) and
not "wherever the facet that needed it first happened to put it."

**2. The producer/method identity boundary (L1, `meta.producer` →
`meta.method`) exists because a cache keyed only on its inputs is wrong the
first time its producer changes — and then wrong again at the wrong
granularity.** Real incident, commit `d47d493`: RDKit's Gasteiger charge
assignment returned NaN for PF6⁻, the field came out uniformly zero, the zero
field was shipped as a normal result, and it was cached. Fixing the producer
did not fix the twelve rows already served from the broken one — the row was
internally self-consistent, so no `CHECK` constraint could have caught it.
The exact cache key is `(molfile_sha256, kind, basis, producer_id)` —
`producer_id` sits IN the key, not in a column beside it; a secondary,
coarser key (`compound_id, conformer_hash, kind, basis, producer_id`) exists
only for the cross-structure hit (two PDB entries carrying the same ligand in
the same pose). The cache is not, and has never been, "keyed on compounds."
Putting producer identity *in the cache key* rather than in a column means a
version bump makes old rows structurally unservable through
`app.v_field_cube_current` instead of relying on someone remembering to
delete them. That fix then exposed a second, opposite problem the same day:
006's producer was one row per **service**, versioned by the hash of an
849-line file, so any edit anywhere in that file — an HTTP comment, a CORS
header — forced a version bump that invalidated every cached SCF result.
Commit `8692d1f` measured the cost: *"8 generations in 53 minutes and 29% of
the cache dark."* Migration 007's `meta.method` fixes the grain: identity is
now the hash of one **compute unit** (`fields.mep`, `fields.qm.homo`, ...)
plus its declared import closure, computed by `method_registry.py`, never
typed by a human. Two migrations, because the first fix was correct at the
wrong resolution and the second one only became visible once the first was
live.

**3. The job ledger boundary (`app.job`, migration 007 SEAM B) exists because
a computation that lives only in a Python thread has no way to answer "what
happened to it" after the process is gone.** This is the one boundary here
that is a designed-ahead seam rather than a retrofit of an incident that
already burned someone — `app.job` has zero rows today (verified live,
2026-08-11) and nothing in this repo writes to it yet. But the failure mode
it closes is not hypothetical: the SCF result cache in `field_server.py`
retained the full two-electron integral tensor (`_eri`) per cached molecule —
327 MB for porphine against 0.14 MB of the coefficients the cache actually
needed — until the daemon died mid-sweep with no traceback, memory driven to
zero by accumulation across *unrelated* molecules, a defect no single-molecule
test could see (`field_server.py:786-794`). A crash like that, with a job
ledger, is a row whose `finished_at` is null and whose age is a query
(`app.v_job_live`); without one, it is a story reconstructed from server
stdout, if the terminal that ran it is still open. The migration's own words:
*"a restart answers 'what happened to that 6-minute SCF?' with 404."*

## Growth map: three seams, not a rewrite

Every future capability this system is meant to reach — an ML affinity
model, docking, MD, FEP — is designed to add through the same three seams,
none of which require touching the frontend, the HTTP layer, or the other
methods already registered.

**Seam 1 — method registry is the socket.** `meta.method` doesn't know
what a method computes; it stores `in_schema`/`out_schema` (JSON Schema),
`capabilities` (what it honestly refuses — the iodine/ECP lesson as data,
not as a comment), and `exec_class`. A checkpoint-based model registers
exactly like `fields.mep` does: *the version is the checkpoint's hash instead
of a compute-unit source hash*, and nothing downstream can tell the
difference, because nothing downstream is supposed to. Concretely, adding
`ml.pearl.affinity` touches:
1. one new directory (`backend/methods/pearl/` or similar) holding the
   inference call and its schema — no edits inside `field_server.py`;
2. one migration, only if the output needs a new table (`app.affinity_score`
   the way fields needed `app.field_cube` — a method whose output fits the
   existing `Envelope` shape needs no new table at all);
3. one call to `meta.register_method(...)` at that method's own startup.

Docking and MD/FEP register the same way — a new `exec_class='job'` compute
unit, its own schema, its own optional output table. The registry does not
grow a special case per method family; that is the entire claim this seam
makes, and it is falsifiable (SPEC.md's kill condition already runs the same
test against the field-wells facet).

**Seam 2 — the job ledger is the executor seam.** Today `db_put_cube` runs in
a `threading.Thread` inside the request-handling process. The planned
progression is in-thread → **process-per-job** (needed the day cancellation
must be exact — pyscf cannot be cancelled mid-SCF from inside the same
process) → **cluster** (needed the day there is a second machine). Callers
never change across any of those three, because a caller reads job state from
`app.job`/`app.v_job_live`, never from the process that is running it. The
`worker` column (nullable today, a pid or remote job id tomorrow) is the only
column that changes meaning across that progression; every other column
already means the same thing at every scale.

**Seam 3 — `parent_job_id` is the DAG seam.** It exists in the schema today,
unused, specifically so the first multi-step workflow (dock → relax → FEP
λ-window) is a **write**, not a migration. `SPEC.md` §3 states the trigger
explicitly: no DAG orchestration engine gets built until that first chained
workflow exists — until then, a DAG is just several `app.job` rows sharing a
`parent_job_id`, queried, not executed by anything new.

**Seam 4 — the envelope is what makes a neural-network result and a physics
result interchangeable at the consumer.** `Envelope` (`iface.d.ts`) is
deliberately payload-agnostic: `{ok, cube?, molfile?, meta}` today, and the
v2 shape (`{ok, data, meta:{envelope, request_id, producer}}`, arriving with
`app.job`) carries an ML embedding, a docking pose, or a field cube with the
same three fields. `FieldCache`/`BackendClient` on the frontend never need a
new type per method — only the JSON Schema each method declares in
`meta.method.out_schema` changes, and that lives in the database, not in a
frontend `switch` statement.

## Explicit non-goals (each with the trigger that reopens it)

| not building | why not now | reopens when |
|---|---|---|
| DAG workflow engine | zero multi-step workflows exist; `parent_job_id` already waits | first chained computation (e.g. FEP λ-windows) |
| distributed compute control plane | one box; observed job costs span 0.02–365 s | a second machine, or the first job class that runs over an hour |
| job cancel / SSE / predictive admission | pyscf cannot cancel mid-SCF from the same process; measured interactive workload is 1.6–4.3 s | the first task users need to abort mid-run |
| React or any UI framework | facets are imperative, target 60fps; the shipped bundle is verified React-free | form-screen count exceeds 3 (i.e. auth/admin surfaces begin) |
| microservice split | two services already duplicate ~22 code sites between them | a second machine, or a method that needs independent scaling |
| a custom binary volume format | mol* already ships ccp4/dsn6/BinaryCIF parsers in-tree; a 128³ float32 grid is 8.39 MB, which no format choice fixes | never — this is a closed decision, not a deferred one |
| auth beyond a Host/Origin allowlist | single user, LAN; the shell is the admin boundary | a second user, or the first WAN-facing deployment |

(SPEC.md §3 carries two additional non-goals — a hypothesis/decision graph,
and the σ-hole facet's own scope — omitted here because they are scoped to a
single feature rather than an architectural boundary.)

## Current state — read in the present tense, honestly

| layer | status | evidence |
|---|---|---|
| L5 presentation | **exists** | `index.ts` 1750 lines + 4 facets (property-cockpit 143, field-wells 802, pharmacophore-designer 1469 across 6 files, bond-atlas 232) |
| L4 client data | **contract only** | types declared in `iface.d.ts`; zero shared implementation — each facet reimplements its own cache/ligand-tracking inline |
| L4.5 browser compute | **exists** | `chemistry.backend.perception.rdkit-wasm.editable/`, ~7.3k lines (SPEC.md §0 N-count); deduplicated 2026-08-10 (`b3dbc9b`) |
| L3 service | **exists, unmerged** | two daemons: `field_server.py` (1121 lines, `:8901`), `physics/server.py` (179 lines, `:8902`) — not yet the single `backend/server/` target |
| L2 compute core | **seam built, not wired** | `meta.method` holds 6 registered methods (verified live: `fields.mep`, `fields.mlp`, `fields.qm.{homo,lumo,density,mep_qm}`) via a standalone run of `method_registry.py --apply`; `field_server.py` itself still stamps only the coarser `meta.producer` id — 0 of 14 `app.field_cube` rows carry a `method_row_id` (verified live) |
| L1 data | **exists** | PostgreSQL `dirac`, migrations 000–007 applied; `app.job` schema live, 0 rows (verified live) — pure seam, nothing writes to it yet |
| L0 runtime | **planned** | no systemd units, no `dirac.yml`; `.github/workflows/` holds `docs.yml` + `node.yml` only |
| contracts | **exists** | `contracts/iface.pyi`, `contracts/iface.d.ts`, `design/tokens.css` (170 lines) |

**Known-broken, as of this writing (not aspirational, not fixed by this
document):**
- `app.blob` orphan rate: two separate autocommit statements write blob and
  row; SPEC.md's F5 measured 67% orphaned (34 MB) from exactly this. Fix
  (single transaction) is `⬜ PLANNED`.
- The producer tripwire (`meta.register_producer` raising on an unbumped
  version) has twice degraded to a silently swallowed `db_cache: off`
  instead of the loud startup failure it's designed to be (SPEC.md F7);
  the split between "degrade" and "exit(1)" is `⬜ PLANNED`.
- A stale browser-side cube can survive a producer version bump across
  `/health` (SPEC.md F9) — the browser cache has no invalidation hook tied
  to the server announcing a new producer. `⬜ PLANNED`.
- Conformer Explorer facet is blocked outright: the vendored RDKit-JS WASM
  build has no `ETKDG`/`EmbedMolecule`/force-field support (verified against
  the binary, per `README.md`). This is precisely why `/embed` exists
  server-side — it is the one workaround for a WASM gap, not a
  general-purpose embedding service, and the previous version of this file
  described it as the latter.
