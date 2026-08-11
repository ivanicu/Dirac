# Future architecture — how today's seams reach the terminal state without a rewrite

Written 2026-08-11. Companion to `STATUS.md` (what is built), `ARCHITECTURE.md`
(the layers as they are), `SPEC.md` (interfaces, data flows, failure model).

## The terminal state

Dirac's endpoint is not a viewer with more panels. It is **the shared
computational substrate of an AI-native scientific operating system for drug
discovery** — where a molecule, a field, a measurement, a hypothesis and an agent's
decision are all first-class persistent objects, and where doing science means
composing them rather than exporting files between tools.

The near-term consequence of that, and the only one this document is about:

> **A capability that lands as new rows, a new module, or a new route is on the
> path. A capability that requires an existing column, envelope, or key to MEAN
> something different is a rewrite, and the design has failed.**

Everything below is written to be falsifiable against the repo. Where a seam does
not exist yet, it says so.

---

## The seams that already carry the weight

Each of these exists today and was built for a reason that has already been paid
for — none is speculative infrastructure.

| seam | where | what it makes additive later |
|---|---|---|
| **content-addressed blob store** | `app.blob`, `CHECK (digest(bytes,'sha256') = sha256)` | any new artifact kind (trajectory, mesh, model checkpoint, figure) is a new row referencing bytes the store cannot mislabel. No new storage layer |
| **method registry** | `meta.method`, per-compute-unit versions | a new method (DFT, semiempirical, an ML surrogate) registers itself; cache invalidation stays a *query*, not a purge. Measured payoff: 18 producer generations vs max 3 per unit |
| **producer identity + servable views** | `meta.producer`, `app.v_field_cube_servable` | a second compute host, or a remote worker, is another producer row. Readers do not change |
| **job ledger** (WIRED 2026-08-11) | `app.job` + CHECK-enforced state machine + `reap_orphaned_jobs()` + `job_one_inflight` | the queue. Rows open and close around every compute, orphans are reaped, and identical concurrent requests share one computation. `queued` is the one state still unwritten — deliberately, until something bounds concurrency |
| **envelope v2** | `backend/envelope.py`, `{ok, data, meta}` + `to_v1()` | new response shapes ride `data`; `meta.envelope` versions the wire. A dual-read window already exists |
| **one error vocabulary** | `contracts/errors.json` → Python + generated TS + DB enum | a new refusal is a new code with a caller action, not a new free-text string. The subset relation is asserted at import |
| **generated contracts** | `contracts/*.pyi`/`*.d.ts`, gate 7 | drift between a producer and its consumers becomes a red gate instead of a support conversation |
| **one facet cascade** | `fanOutLigand()` | a new facet is wired once and cannot be wired into one entry path and not the other — the defect this replaced |
| **schema namespaces** | `meta` · `chem` · `bio` · `design` · `app` · `audit` | biology, design artifacts and provenance already have homes, so their first real table is not a re-organisation |

---

## Phases, and the one decision each closes

Each phase names the **decision it makes safe**. A phase that cannot name one is
filing, not planning.

### P0 · today — one user, one box, synchronous
Decision closed: *is browser-native structure work plus a local quantum backend
actually enough to do medicinal chemistry?* Answered yes for 6 field kinds,
σ-hole analysis, torsion strain, pharmacophores and descriptors.

### P1 · asynchronous compute — wire the job ledger &nbsp;·&nbsp; **MOSTLY DONE 2026-08-11**
Every long compute is a row: `running → done|failed|cancelled`, with
`job_one_inflight` preventing duplicates and orphans reaped on two criteria.
- **Additive, as predicted**: no schema change was needed. The table, states,
  constraints and views already existed; the HTTP routes kept working, and a
  synchronous call became "insert, run, update".
- Decision closed — *can two people, or a person and an agent, use this at once
  without stepping on each other's compute?* **Yes for IDENTICAL work**: the
  second request waits and reads the winner's result, measured at one SCF instead
  of two. **Not yet for DIFFERENT work**: nothing bounds concurrency, so five
  distinct SCFs still all start. `queued` exists and is still unused — that is the
  remainder of this phase, and it is a writer, not a migration.
- The `queued` state stayed valid and unwritten on purpose: in a synchronous
  service a row would be `queued` for microseconds, and a state the system cannot
  be OBSERVED in is noise in the ledger.
- What the wiring cost, kept because both were design errors and not typos: the
  reap was keyed on the worker's own name, and since the name contains the pid it
  reaped NOTHING across a restart (a row sat `running` for three hours while
  `v_job_live` reported it as live work). And the unit version hashed function
  source but not the CONSTANTS those functions read, so a geometry change served
  22 stale-geometry cubes under a method label that had not moved.
- Rewrite tripwire: adding a `status` **text** column beside the enum, or letting a
  worker mutate `app.field_cube` without a job row.

### P2 · compute off this box — remote producers
A worker elsewhere registers as a producer, claims jobs, writes blobs.
- **Additive because** producer identity and servable-on-method reads already make
  "who computed this, with what version" a first-class fact.
- Decision closed: *is the ceiling on this project the box, or the design?*
- Rewrite tripwire: hard-coding a host anywhere a producer id belongs.

### P3 · measurements and hypotheses as objects — beyond fields
Assay results, docking poses, FEP legs, literature claims: rows in `bio`/`design`
with provenance in `audit`, not files in a folder.
- **Additive because** the namespaces exist and the blob store is artifact-agnostic.
- Decision closed: *can a claim be traced to the instrument that produced it, months
  later, without asking the person who ran it?*
- Rewrite tripwire: a `jsonb` column with no exclusion rule. Free text is where a
  vocabulary goes to drift — the error vocabulary already demonstrated the cost.

### P4 · agents as first-class actors — the Genesis surface
An agent proposes a molecule, requests fields, reads refusals, and records what it
concluded. Its actions are jobs; its conclusions are objects; its provenance is the
same `audit` trail a human's is.
- **Additive because** every refusal already names the actor and the way forward in
  machine-readable form (`caller_action`, `points_at`, `retryable`), which is what
  makes an agent able to act on a failure instead of retrying blindly.
- Decision closed: *can a non-human actor use this system without a second API?*
- Rewrite tripwire: building that second API. An agent route that bypasses the job
  ledger or the error vocabulary forks the system in half.

---

## Rewrite tripwires — the list to check a PR against

These are the changes that would break the path to the terminal state. They are
listed because each is individually tempting and locally reasonable.

1. **Keying cache reads on the producer instead of the method.** Already measured:
   it invalidated ~6× more often than the physics changed and left 1 of 19 rows
   servable while `/health` honestly reported the cache as on.
2. **A second error vocabulary.** It has happened once (three disagreeing ones) and
   nearly happened again this week, when the ops router could not express 404/503.
   The fix is a code in the one home, never a local helper.
3. **Free text where an enum is possible.** `status`, `kind`, `method`, `reason`.
4. **A facet reading the database directly.** The service is the only writer and the
   only reader; a facet that learns SQL makes every schema change a frontend release.
5. **Meta keys that exist on one response path.** A cache hit and a fresh compute
   must be identical in SHAPE and differ only in VALUE — enforced by
   `normalize_meta` and gate 7 check 4.
6. **A "temporary" synchronous long compute after P1.** The seam only stays real if
   nothing bypasses it.
7. **A gate that has never convicted.** Gates 7 and 8 ship red proofs; a new gate
   without one is advice wearing a gate's clothes.
8. **A version that hashes code but not the numbers the code reads.** Measured:
   changing `PAD_MAX` changed every cube's geometry and moved no method version, so
   the cache served boxes the current code would not draw. Constants are part of a
   compute unit; scope them per unit, or a classical tuning pass throws away
   converged SCF work.
9. **A reap keyed on the reaper's own identity.** It reaps nothing across a
   restart. Reap what is not ALIVE, plus an age ceiling — the ceiling is the only
   criterion that survives a worker on another host.

## Deliberately not on the path

- **A second UI for "advanced users."** One app, facets on a shared scene, is the
  whole architectural bet.
- **Faking HPC-class science.** FEP/ΔΔG needs infrastructure this does not have; it
  is reported as absent rather than approximated into a number someone might quote.
- **Auth for its own sake.** It is missing and stated as missing. It becomes real
  when the system leaves this box (P2), not before — and then as a real boundary,
  not a login page over an unauthenticated daemon.
