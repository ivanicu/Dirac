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
| **job ledger (`app.job`)** | **WIRED** (was SEAM ONLY this morning) | every compute opens a row and closes it: 267 done · 73 failed · 7 cancelled, `SELECT count(*) FROM app.v_job_live` → 0 phantom rows. Failures carry the right code (`UNSUPPORTED` for a PF6⁻ Gasteiger refusal, `BUDGET` for a deadline) — a ledger that only records successes is the easiest one to lie with |
| **job dedup / coordination** | SHIPPED | two identical concurrent requests now produce **ONE** computation: the second waits on `job_one_inflight` and reads the winner's row. Measured on a never-seen HOMO, 0.4 s apart — A 2.85 s `computed`, B 2.62 s `db` (waited 2.563 s), `opened 1 · joined 1`. It waits in the DATABASE, not on an in-process Event, because the requests that hurt come from different clients and will come from different hosts (P2) |
| **orphan reaping** | SHIPPED | two independent criteria: the worker's pid is gone, OR the row is past a hard 1800 s ceiling. The pid check is precise and blind to pid reuse; the ceiling covers that AND is the only one that still works when a worker runs off-box. Reported separately, because "the process died" and "the job overran" are different facts |
| **concurrency bound** | ABSENT, by decision | `ThreadingHTTPServer` still runs every DISTINCT request immediately — dedup removes duplicate work, not load. Nothing queues, nothing rejects a fifth simultaneous SCF. This is the next thing `app.job`'s `queued` state is for |
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
| **frontend ↔ backend meta drift** | **CLOSED** (was 21 keys, then 25) | the gap reached ZERO on 2026-08-11 and gate 7's exit 2 is now a FAILURE, in `gates.sh` and in CI. The tolerance was right while the gap was 25 keys — failing then trains everyone to `--skip`, and a suite people skip enforces nothing — and the whole value of reaching zero is that the next divergence is caught at one key. Three times today an undeclared key was live in production; each fix took two minutes |

## Frontend state layer — measured, and the measurement is unflattering

`src/app/services/ligand-store.ts` is **SEAM ONLY**, and the audit that says so
(`docs/FRONTEND_STATE_AUDIT.md`, 453 lines, every claim with a `file:line`) is
worth more than the module: **8 modules hold "the current ligand" in some form and
0 import the store.** One commit has ever touched it. `ARCHITECTURE.md` says the
same thing from the other side — "reimplemented inline".

What the audit found that a status table alone would have hidden:

| finding | measured | state |
|---|---|---|
| the store shipped a heavy-atom count that a sibling facet had already fixed 11 h earlier — counting explicit H as heavy, returning 0 for V3000 (and 0 heavy atoms passes every affordability gate) | 2 red tests, then green | **FIXED today**, and the corrected logic now lives in the services layer so `facets/field-wells` can import it and delete its private copy |
| its spec had a failing test nobody had run | 1 red of 9 | **FIXED** — the TEST was wrong, not the store: `subscribe()` replays the current value even when it is null, deliberately, so a late-mounting facet can clear itself. Now 12/12, with that intent asserted rather than assumed |
| 10 async paths produce a ligand-shaped result; 7 committed with no currency check | 10 paths audited | **2 of 7 GUARDED** — property-cockpit and pharmacophore-designer, via `RequestGeneration`. NOT the store's own `generation()`: nothing writes through the store yet, so it never advances, and a guard on a value that cannot change is a check that cannot fail. The two pyscf paths in ligand-physics were guarded by the session that owns them (`isCurrentLigandGeneration`, discard-and-say). **3 remain** |
| the guard's tests cover the MECHANISM, not the two facets' DOM | no jsdom in this repo | **UNVERIFIED, not clean.** "The panel does not keep A's values under B's identity" was checked by reading the call sites, not by execution. Adding jsdom is a dependency decision, deliberately not made inside a bug fix |
| 3 facts have more than one home (heavy-atom count, atom-index walker, staleness token) | 3 | 1 of 3 closed today |

**The decision, stated so it is not re-litigated silently:** the store is NOT
deleted. The bug class it exists to prevent was demonstrated live today — a pasted
molecule survived loading a deposited structure and won a branch in five facets,
because "which molecule is active" had four writers and no owner. But shelfware
with a known defect is worse than no module, so its defects are fixed first and
adoption is incremental: **one consumer at a time, each with the currency check
wired, starting with the two unguarded SCF paths.** A big-bang adoption of a module
nothing imports is how the 21-key contract drift happened on the other side.

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

1. ~~Wire the job ledger~~ — **DONE.** Rows open and close, orphans are reaped on
   two criteria, and identical concurrent requests share one computation.
2. ~~Close the frontend meta drift~~ — **DONE**, gap zero, and gate 7 is strict.
3. **Bound concurrency.** Dedup removed duplicate work; it did not remove LOAD.
   Five simultaneous distinct SCFs still all start. `app.job`'s `queued` state and
   `v_job_live` are already there for it — this is the last piece of P1.
4. **Adopt the store on the write side** — the remaining 3 unguarded async paths,
   and then `index.ts` writing through `ligandStore` so the store's own generation
   advances and `RequestGeneration` can be retired rather than multiplied.
5. **Unblock the conformer facet** through the backend's real RDKit (ETKDG + MMFF).
6. **A DOM test harness.** Three of today's defects were only visible on screen —
   a vanished meta row, a "null" rendered as text, a stale panel. Everything above
   is verified by execution except the parts that paint.

Trajectory beyond these: `ROADMAP.md`. Layer structure: `ARCHITECTURE.md`.
Interfaces and data flows: `SPEC.md`.
