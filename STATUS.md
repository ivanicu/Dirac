# Construction status

Last re-derived: **2026-08-11**, against the running system, not from memory. Every
number in here has the command that produced it next to it — if a claim has no
command, it is marked as a judgement, not a measurement.

> **NOT DEPLOYED.** Nothing here runs as a supervised service. Three processes are
> started by hand (`bin/dev`), there is no authentication, and there is no public
> URL. The systemd units in `deploy/systemd/` were installed, proven (a `kill -9`
> came back in 8 s), and then uninstalled the same hour — see `deploy/README.md`.
> Treat every "SHIPPED" below as *shipped into the repo and runnable locally*.

## How to read the state column

| state | means |
|---|---|
| **SHIPPED** | runs, and there is a command in this file that shows it running |
| **SEAM ONLY** | the structure exists and nothing writes to it yet. Named, not hidden — an unwired seam that reads as finished is how a schema rots |
| **PARTIAL** | works on one path and not another, with the gap named |
| **BLOCKED** | cannot proceed, with the blocker and what would unblock it |
| **PLANNED** | not started. Listed only where the seam it will use already exists |

---

## Frontend — one app, six facets on a shared mol\* scene

| facet | state | evidence / gap |
|---|---|---|
| **Lab** (`index.ts`) | SHIPPED | 3D structure + RDKit perception + 2D depiction with click-sync |
| **Fields** (`facets/field-wells/`) | SHIPPED | 6 field kinds render as 3D wells; browser cache + auto-persist, no save button |
| **Properties** (`facets/property-cockpit/`) | SHIPPED | Lipinski / Veber / lead-likeness off `mol.get_descriptors()` |
| **Designer** (`facets/pharmacophore-designer/`) | SHIPPED | drag-editable model + SMARTS screen over a 68-molecule library |
| **Bond atlas** (`facets/bond-atlas/`) | SHIPPED | per-bond table; wired into both entry paths as of today |
| **Halogen audit** (`facets/halogen-audit/`) | PARTIAL | geometry only until a QM field has been run; says so rather than guessing a V<sub>S,max</sub> |
| **Ligand physics** (`facets/ligand-physics/`) | SHIPPED | σ-hole + torsion numbers from the physics daemon |
| **Conformer explorer** | BLOCKED | the vendored RDKit-JS wasm exposes no `ETKDG`/`EmbedMolecule`/force fields (checked against the binary, not the docs). Unblocks through the backend, which has RDKit 2026.03 with MMFF |
| **Ops console** (`ops/`) | SHIPPED | zero-build page over `/admin/snapshot`; OFFLINE / DEGRADED / HEALTHY-EMPTY render differently on purpose |

**The facet cascade has ONE home** as of today: `fanOutLigand()` in `index.ts`.
Before that it had two, and they had drifted — the deposited-ligand path told
seven consumers about a new molecule and the import path told two, which is why
importing a molecule and clicking a field did nothing while the status line said
it was rendering. A new facet is wired once, there.

## Backend — two Python daemons, deliberately separate processes

| piece | state | evidence / gap |
|---|---|---|
| fields daemon `:8901` | SHIPPED | `curl -s 127.0.0.1:8901/health` → `{ok:true, …}`; 6 methods registered (`SELECT method_id FROM meta.method WHERE superseded_at IS NULL`) |
| physics daemon `:8902` | SHIPPED | surface MEP (σ-hole), torsion strain, and `/field/region` (arbitrary atom set + caller's frame) |
| ops read surface | SHIPPED | `GET /admin/snapshot`, read-only; no route in the module writes or deletes |
| SCF budget enforcement | SHIPPED | refused before running on a cost prediction, AND bounded per SCF cycle by a watchdog, on **every** route — enforced by gate 8, which ships a 4-conviction selftest |
| cube cache | SHIPPED | reads via `app.v_field_cube_servable` (method currency, not producer). Measured: 18 producer generations vs max 3 per compute unit — producer-keyed reads invalidated ~6× more often than the physics changed |
| auth | ABSENT, by decision | LAN-reachable and unauthenticated; a `Host`/`Origin` allowlist blocks DNS rebinding but anyone who can reach the box can submit compute. Stated in `backend/README.md`, not discovered |

## Data layer — PostgreSQL `dirac`

| piece | state | evidence / gap |
|---|---|---|
| 11 migrations, forward-only | SHIPPED | `ls backend/db/migrations/*.sql \| wc -l` → 11 |
| content-addressed blobs | SHIPPED | `CHECK (digest(bytes,'sha256') = sha256)` — the store cannot hold a mislabelled blob |
| method registry (`meta.method`) | SHIPPED | per-compute-unit versioning; what makes cache invalidation a query instead of a purge |
| migration ledger integrity | SHIPPED | `backend/db/check_migration_hashes.sh --selftest` convicts a tampered applied migration |
| **job ledger (`app.job`)** | **SEAM ONLY** | table + CHECK-enforced state machine + `v_job_live` + `reap_orphaned_jobs()` exist; **`SELECT count(*) FROM app.job` → 0, and nothing outside `check_constraints.sql` inserts.** Compute is still synchronous per HTTP request. This is the seam a queue lands on |
| conformer coarse key | PARTIAL | `conformer_hash` with a det=+1 canonical frame (enantiomer-safe) is written; the Kabsch/RMSD coarse-hit *read* path is contract, not code |

## Contracts, gates, tests

| piece | state | evidence / gap |
|---|---|---|
| one error vocabulary | SHIPPED | `contracts/errors.json` → Python + generated TS + the DB enum, with the subset relation asserted at import |
| `contracts/iface.pyi` · `iface.d.ts` | SHIPPED | 42 FieldMeta keys, checked against what the backend really emits |
| 8 gates (`scripts/gates.sh`) | PARTIAL | 1–4 and 6–8 run in CI; **gate 5 (migration hashes) is local-only** because it needs the applied ledger, and a version that passed without a database would be a check that cannot fail |
| gates 7 and 8 self-conviction | SHIPPED | each runs its own red proof FIRST and its failure is the gate's failure; the harness asserts its mutation changed the bytes before reading a verdict |
| backend tests | SHIPPED | 6 files (`ls backend/tests/test_*.py`), including a `test_cannot_fire.py` whose job is to hunt checks that cannot convict |
| frontend specs | THIN | 4 spec files. The store has 15 tests; the facets have none |
| **frontend ↔ backend meta drift** | **KNOWN OPEN** | the facet's `FieldMeta` interface is 21 keys behind the backend. `node scripts/check_contract_drift.mjs` reports it as `FIND` (exit 2) rather than failing, because that file is owned by a parallel line of work — reported, dated, not hidden |

## What is measured, and what is only claimed

Re-derived today with commands (see `SPEC.md` §7 for the full ledger, M01–M10):
cold page load **920 ms** · bundle **3.14 MB raw / 902 KB gzip** · field swap medians
**189–722 ms** across 6 kinds · MEP **~0.1 s**, carbazole HOMO **15.5 s**, LUMO **8.9 s**
(real SCF, sto-3g) · cache hit **12–40 ms** vs recompute · RSS **+19 MB** across a
cold 6-molecule batch with the SCF cache capped at its documented bound of 6.

Three numbers that did **not** reproduce and were corrected rather than kept:
the bundle size is volatile under concurrent rebuilds, not a constant; RDKit's
6.9 MB wasm is lazy-loaded and is **not** part of cold load; and the "2.6 s" cold
load figure was wrong by ~3×.

## Nearest edges of the work

1. **Wire the job ledger** — the only way today's synchronous request model becomes
   a queue without a schema change. The seam is built and empty.
2. **Close the frontend meta drift** — 21 keys; then flip gate 7's exit 2 to a hard
   failure so it can never silently reopen.
3. **Frontend state layer adoption** — `ligand-store.ts` exists with 15 tests; how
   much of the app reads it rather than passing molfiles around is being audited.
4. **Unblock the conformer facet** through the backend's real RDKit.

Trajectory beyond these: `ROADMAP.md`. Layer structure: `ARCHITECTURE.md`.
Interfaces and data flows: `SPEC.md`.
