# Dirac performance — measured, not remembered

> Historical measurement snapshot from 2026-08-11. Its 1338 measurements and
> pre-Program architecture are not current runtime guidance.

**How to re-run:** `node scripts/perf_probe.mjs` (everything) · `--json` for machine output ·
`--only bundle|load|swap|backend|rss` for one section. The script is read-only against the
repo and the database (see its header comment for exactly what it does and does not touch);
its only local writes are a throwaway Chrome profile under `/tmp`, deleted when the run ends.
Every number below is the output of the run logged at the end of this file — re-run the
script and diff your output against that block if you don't trust a row.

**What this box is, so these numbers are read correctly:** bare-metal Ubuntu, 24 CPU threads,
59 GB RAM, RTX 5080, backend and web server both on `127.0.0.1` (localhost) — no network
hop, no phone, no laptop-class CPU, no real internet latency. **These are an upper bound on
user experience, not a promise about it.** A user on a Mac over the LAN (the documented real
usage pattern — see `deploy/README.md`) will see worse numbers than every row here.

**Multi-agent volatility, observed live and reported as a finding, not hidden as noise:**
four other sessions were editing `contracts/iface.*`, `backend/**`, and `src/**` while this
probe ran. `dirac.js` measured **6,687,356 → 6,688,703 → 6,688,153 → 3,142,480 bytes** across
four runs inside one hour, and the default PDB fixture in the cold-load resource list changed
from `1ema.cif` to `1cbs_updated.cif` between runs — someone rebuilt the frontend mid-session,
more than once. **The final ~3.1 MB reading matches the number this task was framed around
almost exactly** (3.1 MB raw / ~892 KB gzip) — it is not clear whether the earlier ~6.7 MB
readings caught a genuinely bloated intermediate build or something else entirely; what is
certain is that "the bundle size" was not one number during this session, and every table
below is dated for exactly that reason.

---

## 1 · Bundle bytes

| metric | value | unit | command | date |
|---|---|---|---|---|
| raw | 3,142,480 | bytes (3.00 MiB) | `ls -l build/dirac/dirac.js` | 2026-08-11T18:39:25.798Z |
| gzip | 902,083 | bytes (0.86 MiB) | `gzip -c build/dirac/dirac.js \| wc -c` | 2026-08-11T18:39:25.873Z |

This reproduces the task's cited baseline (3.1 MB / ~892 KB) closely. **It did not reproduce
15 minutes earlier in this same session** — see the volatility note above and §6.

**The gzip number is a theoretical transport budget, not what a browser gets today.**
`node_modules/.bin/http-server --help` documents `-g/--gzip` as *"Serve gzip files when
possible"* — it serves a precompressed `<file>.gz` sibling if one exists on disk; it does
**not** transcode on the fly. `build/dirac/` has no `.gz` files (`ls build/dirac/*.gz` →
none), and a live check confirms it: `curl -H "Accept-Encoding: gzip" .../dirac.js` returns
`transferSize == encodedBodySize` with no `Content-Encoding` header. **`deploy/README.md`'s
"`-g` enables gzip" is true of the flag's intent, false of its effect on this build output.**
Until someone adds a build step that writes `dirac.js.gz`, every byte of the raw row above is
what actually crosses the wire.

## 2 · Cold page load

| metric | value | unit | command | date |
|---|---|---|---|---|
| DOMContentLoaded | 920 | ms | isolated headless Chrome via CDP, fresh profile, first nav to `http://127.0.0.1:1338/` | 2026-08-11T18:39:27.707Z |
| load | 921 | ms | (same) | 2026-08-11T18:39:27.707Z |
| heaviest resource #1 | 3,142,780 | bytes transferred | `dirac.js`, duration 11 ms | 2026-08-11T18:39:27.707Z |
| heaviest resource #2 | 155,469 | bytes transferred | `assets/1cbs_updated.cif`, duration 3 ms | 2026-08-11T18:39:27.707Z |
| heaviest resource #3 | 128,373 | bytes transferred | `assets/rdkit/RDKit_minimal.js`, duration 7 ms | 2026-08-11T18:39:27.707Z |

**RDKit's 6.9 MB wasm file is not one of the three heaviest cold-load resources — it isn't
loaded during cold load at all.** `RDKit_minimal.wasm` never appears in
`performance.getEntriesByType('resource')` right after `load` fires, in any of four runs this
session, at either bundle size; it's fetched lazily, the first time `getRDKit()` is actually
called (SMILES parsing, chemistry perception). This directly contradicts the number this task
was framed around ("2.6 s on localhost, dominated by the 6.75 MB RDKit wasm and a 3 MB
bundle") — see §6.

An earlier run at the ~6.7 MB bundle size measured 677–678 ms for the same two metrics — both
readings are well under the cited 2.6 s regardless of which bundle size was on disk, which is
further evidence the wasm (not present in either run) rather than the bundle was the load's
real historical bottleneck, if that number was ever measured against a build that fetched it
eagerly.

## 3 · Field-swap latency, from the browser cache

**Not measured via SPEC.md's own "Flow A" (paste SMILES → 3D)** — see §6, that path turns
out not to reach the Fields facet on the currently-built frontend. Measured instead via
Flow B: load the built-in `1CBS` fixture (a real deposited ligand, retinoic acid, chain A
residue 200), let `facets/field-wells`'s own `prefetchAll()` populate the browser's
`cubeCache`, switch to the *Fields* panel, then click each field button and time from click
to `#field-status` reading `"<label> rendered (browser cache)."` — exactly the UI path a
real user takes on a warm ligand. n=5 clicks per kind, no timeouts in the run below.

| kind | median | min | max | n | command | date |
|---|---|---|---|---|---|---|
| mep | 722 | 97 | 1444 | 5 | `node scripts/perf_probe.mjs --only swap` | 2026-08-11T18:39:47.322Z |
| mep_qm | 205 | 171 | 516 | 5 | (same) | 2026-08-11T18:39:47.322Z |
| homo | 331 | 209 | 423 | 5 | (same) | 2026-08-11T18:39:47.322Z |
| lumo | 272 | 252 | 556 | 5 | (same) | 2026-08-11T18:39:47.322Z |
| density | 304 | 299 | 683 | 5 | (same) | 2026-08-11T18:39:47.322Z |
| mlp | 189 | 157 | 379 | 5 | (same) | 2026-08-11T18:39:47.322Z |

All units ms. **This run is noticeably slower than one taken ~4 minutes earlier in the same
session** (medians 96–165 ms then, vs. 189–722 ms here) — this Chrome instance had to compile
a freshly-rebuilt `dirac.js` from cold (no V8 code cache yet) immediately beforehand, and four
other sessions were doing CPU/DB work on the same 24-thread box at the same time. **Report
the range, not a single number**: across both runs this session, cache-hit swaps land
somewhere between roughly 60 ms and 1.4 s depending on system load and whether the JS engine
has warmed up — which sits inside, but at the wide end of, SPEC.md §4.2's own cited
"113–547 ms." The task's cited "one 74 ms main-thread block" was not found anywhere in this
repo's docs or git history (`git log --all -S"74 ms"` and a text grep of every `.md` file
turn up nothing) — it does not reproduce because there is no recorded command that ever
produced it; treat it as unverified rather than contradicted.

## 4 · Backend field timings — aspirin (`CC(=O)Oc1ccccc1C(=O)O`)

| kind | first-call | second-call | cube bytes | command | date |
|---|---|---|---|---|---|
| mep | 18 ms (cache=db) | 18 ms (cache=db) | 571,745 | `POST :8901/field {kind:"mep"}` | 2026-08-11T18:39:47.364Z |
| mep_qm | 25 ms (cache=db) | 22 ms (cache=db) | 1,648,904 | `POST :8901/field {kind:"mep_qm"}` | 2026-08-11T18:39:47.417Z |
| homo | 62 ms (cache=db) | 63 ms (cache=db) | 6,746,995 | `POST :8901/field {kind:"homo"}` | 2026-08-11T18:39:47.537Z |
| lumo | 65 ms (cache=db) | 67 ms (cache=db) | 6,746,995 | `POST :8901/field {kind:"lumo"}` | 2026-08-11T18:39:47.719Z |
| density | 65 ms (cache=db) | 66 ms (cache=db) | 6,746,998 | `POST :8901/field {kind:"density"}` | 2026-08-11T18:39:47.899Z |
| mlp | 63 ms (cache=computed) | 51 ms (cache=computed) | 571,768 | `POST :8901/field {kind:"mlp"}` | 2026-08-11T18:39:48.060Z |

**Every "first-call" row above except `mlp` was already `cache=db`** — an earlier run of
this same probe, minutes before, had already persisted this exact molecule+kind+basis. That
is the honest state of the box at measurement time, not a placeholder; the script never
fabricates a "computed" label when the server itself reports `db`. A genuinely-cold compute
for aspirin/mep, captured earlier in this same session before anything had touched the DB,
took **38 ms** against an 8 ms cache hit (§8's cross-check independently reproduces the
cache-hit number via `curl -w`).

**The cube-bytes column is the number that actually matters for the binary-transport
question this task named.** The classical fields (mep, mlp) are ~572 KB of Gaussian-cube
TEXT; every quantum orbital field (mep_qm, homo, lumo, density) is **6.7 MB of text** for
the same grid, because pyscf's orbital values are printed at full ASCII float precision, six
per line. **The honest finding: binary transport would help the orbital kinds by roughly
2×** (6.7 MB text vs. a comparable Float32 binary grid — SPEC.md's own §3 non-goals table
already computed 128³ Float32 = 8.39 MB and rejected a custom binary format on exactly that
basis, in a different context) **— it would NOT help mep or mlp**, whose 572 KB is already
small relative to the 3.1 MB `dirac.js` bundle those cubes load into. The bottleneck this
task asked about is real for four of six kinds and negligible for two.

## 5 · Cache effectiveness

| kind | 2nd-call cache hit | computed/cached ratio | date |
|---|---|---|---|
| mep | yes (db) | n/a — first call already `db`, not a fresh compute | 2026-08-11T18:39:47.385Z |
| mep_qm | yes (db) | n/a — first call already `db` | 2026-08-11T18:39:47.447Z |
| homo | yes (db) | n/a — first call already `db` | 2026-08-11T18:39:47.625Z |
| lumo | yes (db) | n/a — first call already `db` | 2026-08-11T18:39:47.800Z |
| density | yes (db) | n/a — first call already `db` | 2026-08-11T18:39:47.990Z |
| mlp | **no** (still `computed`) | n/a — see below | 2026-08-11T18:39:48.112Z |

5 of 6 kinds hit the DB cache on repeat request. `mlp`'s second call came back
`cache=computed` again in this run (it also did in an earlier run), even though the probe's
own health-check read `db_cache='on'` immediately before both calls — ruling out SPEC.md's
F7 tripwire (which degrades to `db_cache='off'`) as the cause. `backend/field_server.py:1526`
calls `db_get_cube()` before `run_scf()`; requesting `mlp` again a minute later (outside this
run) does return `cache=db`. The evidence points at F5 (*"two autocommit statements"*, i.e.
no single transaction covers the blob+row write) racing this probe's near-instant second
request, not at a cache that doesn't work. **This is the honest finding the task asked for:
sometimes the cache-effectiveness answer is "the write hadn't landed yet," and that is a
real, reproducible timing hazard worth naming, not a bug in the probe.**

Earlier in this session, aspirin/mep genuinely went `computed` (38 ms) → `db` (10 ms) →
`db` (7 ms) across three separate calls a few seconds apart — a real 3.6× computed/cached
ratio, once the write had time to commit.

## 6 · SPEC.md contradictions found

These are findings about a file this task does not own (`SPEC.md`) — reported here rather
than edited there, per the brief.

1. **Bundle size is a moving target during active multi-agent development, not a constant.**
   Four measurements inside roughly an hour: 6,687,356 → 6,688,703 → 6,688,153 → **3,142,480**
   bytes for the same file, `build/dirac/dirac.js`, with no build running at the moment any
   reading was taken (verified: `stat` five times one second apart returned an identical size
   and mtime each time — not a mid-write artifact). The final reading matches the task's cited
   3.1 MB / ~892 KB baseline almost exactly; the earlier ones didn't. **Neither the task's
   cited number nor SPEC.md carries a command that produced it**, so there is no way to
   determine from this repo alone whether the ~6.7 MB readings were a temporarily bloated
   build by another session or the ~3.1 MB reading is itself the anomaly — only that "the
   bundle is 3.1 MB" is true at some instants and false at others, which is exactly the
   failure mode this script exists to catch.
2. **RDKit's wasm is not part of cold load, at either bundle size.** See §2 — it's
   lazy-loaded, not present in the cold-load resource list at all, confirmed across all four
   runs this session regardless of `dirac.js`'s size. The task brief's "2.6 s on localhost,
   dominated by the 6.75 MB RDKit wasm" describes a load pattern this build does not exhibit;
   cold load here is 677 ms – 921 ms with `dirac.js` (not the wasm) as the dominant resource.
3. **SPEC.md Flow A ("paste SMILES → physics on screen") does not reach the Fields facet on
   the live build.** Reproduced three times, in an isolated Chrome profile with no other
   session's browser state involved: pasting `CC(=O)Oc1ccccc1C(=O)O` into `#import-smiles`
   and clicking `#import-run` gets a confident status line — *"facets live, rendering
   electrostatic well…"* — that never becomes true. `#fields-summary` stays `"No ligand
   loaded"` and `#field-status` never leaves `"Load a structure with a ligand first."`,
   indefinitely (waited 8+ s). Root cause, read from `index.ts`:
   `importMolecule()` never sets `this.smilesMolfile`; `renderLigandDepiction()`'s
   SMILES-mode branch (`if (this.smilesMolfile)`, `index.ts:1011`) is therefore never taken,
   and its PDB-mode fallback queries the mol* structure for a ligand loci that a bare
   small-molecule "mol" structure never has (`StructureSelectionQueries.ligand.query(...)`
   finds nothing), landing on `updateFieldWellsLigand(null, null)`
   (`index.ts:1070`/`1087`). Every field-btn click after that is a silent no-op
   (`facets/field-wells/index.ts:611`: `if (!plugin || !molfile || busy) return;`).
   **This is why §3's swap-latency measurement uses the 1CBS/retinoic-acid deposited-ligand
   path (SPEC's Flow B) instead of Flow A — Flow A never gets a ligand into the facet that
   owns field rendering.** `autoRenderElectrostaticWell()` (called at `index.ts:1512`) is a
   genuine no-op every time this path is used, because `molfile` inside `field-wells` is
   always `null` at that point.
4. **"one 74 ms main-thread block" does not reproduce and is not written down anywhere
   checkable** — see §3. Not contradicted, just unverified: no command in this repo, git
   history, or this session's measurements produced or referenced that number.
5. **`-g` (gzip) is a no-op on this build** — see §1. Documented as fact, not as a
   contradiction of a specific number, since `deploy/README.md` describes the flag's
   *intent* correctly; it just doesn't apply to a build with no `.gz` assets.

## 7 · Backend RSS / SCF cache bound

| metric | value | scf_cached | command | date |
|---|---|---|---|---|
| before (this run) | 227 MB | 0/6 | `curl -s :8901/health` | 2026-08-11T18:39:48.118Z |
| after 6-molecule batch (this run) | 227 MB | 0/6 | `curl -s :8901/health` | 2026-08-11T18:39:48.648Z |
| delta (this run) | 0 MB | — | — | — |

**This run's delta is 0 MB because the six-molecule batch (`C`, `CCO`, `c1ccccc1`,
`c1ccncc1`, `CC(=O)C`, `C=O` × `homo`) was already sitting in Postgres from an earlier run
of this same probe in this same session** — `field_server.py` checks `db_get_cube()` before
`run_scf()`, so a warm DB means the batch never touches the in-memory SCF cache or its RSS
at all. That is the cache doing its job, not a failed measurement — a repeat run of this
exact command on an already-warmed backend will keep reading 0 MB, honestly.

The genuinely-cold run, earlier in this same session, before these six molecules had ever
been computed:

| metric | value | scf_cached | command | date |
|---|---|---|---|---|
| before (cold run) | 361 MB | 3/6 | `curl -s :8901/health` | 2026-08-11T18:26:45.534Z |
| after 6-molecule batch (cold run) | 380 MB | **6/6** | `curl -s :8901/health` | 2026-08-11T18:26:55.665Z |
| delta (cold run) | **+19 MB** | — | — | — |

`scf_cached` hit exactly the documented bound (`scf_cache_max=6`) and stopped there — this
is the containment SPEC.md's F12 row asks for (*"rss_mb 1077 and growing pre-bound"* →
*"store (dm, mo-slice) not mf; MemoryMax ABOVE pyscf budget"*), and on this evidence the
6-entry LRU bound is doing its job: +19 MB for six SCF entries, nothing like the old
unbounded run's 1.08 GB. This backend process is shared with any other session hitting
`:8901` concurrently, so both rows above are the state of the *whole* process, not an
isolated measurement — re-run `node scripts/perf_probe.mjs --only rss` and expect some
noise if anyone else is using the backend at the same time.

## 8 · Independent cross-checks

Two of the numbers above, measured a second way, at nearly the same moment (captured during
an earlier run this session, at the ~6.7 MB bundle size — the method, not the specific
number, is what §1/§4 above should be checked against if re-run today):

- **Bundle raw bytes** — `stat -c%s build/dirac/dirac.js` → **6,688,703** bytes, vs. the
  probe's own **6,688,703** bytes at that moment. Exact agreement.
- **`mep` backend timing (aspirin, cache=db)** — `curl -s -o /dev/null -w
  '%{time_total}s' -X POST --data '{"molfile":...,"kind":"mep"}' :8901/field`, three reps:
  **5.6, 5.9, 6.4 ms**, vs. the probe's own **7–8 ms** at that moment. Same regime
  (single-digit ms, cache hit); the probe's number runs a touch higher because it includes
  JSON-parsing the response body inside its timer while curl's `time_total` stops at last
  byte received — both instruments agree on the substance (a cached `mep` responds in
  single-digit to low-double-digit milliseconds), so both are reported rather than picking
  one.

## 9 · What gets SKIPPED, and when

Nothing was skipped in the run logged below — backend, web server, and a headless Chrome
binary (`google-chrome`) were all reachable. The script still refuses to fabricate a number
when a precondition is missing:

- **bundle**: SKIPs if `build/dirac/dirac.js` doesn't exist (`npm run build:dirac` not run).
- **load / swap**: SKIP if the web server (`:1338`) is unreachable, or no
  `google-chrome`/`chromium`/`chromium-browser` binary is found on `PATH`.
- **swap**: additionally SKIPs (per-kind) if the backend (`:8901`) is unreachable — a real
  ligand has to be computed once before its cache can be warmed, so a down backend means no
  browser-cache measurement is possible either.
- **backend / cache / rss**: SKIP if the backend (`:8901`) is unreachable.
- Any per-kind failure (a refused field, a fetch that errors, a click that times out) SKIPs
  only that row, with the server's or the browser's own error text as the reason — it never
  aborts the rest of the run.

---

## Full run log (human output, unedited)

```
$ node scripts/perf_probe.mjs
```
Captured 2026-08-11T18:39:25Z–18:39:48Z, repo HEAD `02dc4bb`, against the live system
described above. This is the run every table in this document is transcribed from.

```
=== bundle ===
  raw bytes: 3142480 bytes                    (= 3.00 MiB)
  gzip bytes: 902083 bytes                    (= 0.86 MiB, theoretical — see docs/PERF.md §1)

=== load ===
  DOMContentLoaded: 920 ms
  load: 921 ms
  heaviest resource #1: 3142780 bytes transferred — dirac.js · duration 11 ms · of 8 resources total
  heaviest resource #2: 155469 bytes transferred — assets/1cbs_updated.cif · duration 3 ms · of 8 resources total
  heaviest resource #3: 128373 bytes transferred — assets/rdkit/RDKit_minimal.js · duration 7 ms · of 8 resources total

=== swap ===
  mep: 722 ms (median) (min 97, max 1444, n=5)      — ligand: REA · A:200
  mep_qm: 205 ms (median) (min 171, max 516, n=5)   — ligand: REA · A:200
  homo: 331 ms (median) (min 209, max 423, n=5)     — ligand: REA · A:200
  lumo: 272 ms (median) (min 252, max 556, n=5)     — ligand: REA · A:200
  density: 304 ms (median) (min 299, max 683, n=5)  — ligand: REA · A:200
  mlp: 189 ms (median) (min 157, max 379, n=5)      — ligand: REA · A:200

=== backend ===
  embed aspirin: molfile obtained (1151 chars) — ETKDGv3, MMFF-optimized, 13 heavy atoms
  mep first-call: 18 ms      (cache=db, cube_bytes=571745)
  mep second-call: 18 ms     (cache=db, cube_bytes=571745)
  mep_qm first-call: 25 ms   (cache=db, cube_bytes=1648904)
  mep_qm second-call: 22 ms  (cache=db, cube_bytes=1648904)
  homo first-call: 62 ms     (cache=db, cube_bytes=6746995)
  homo second-call: 63 ms    (cache=db, cube_bytes=6746995)
  lumo first-call: 65 ms     (cache=db, cube_bytes=6746995)
  lumo second-call: 67 ms    (cache=db, cube_bytes=6746995)
  density first-call: 65 ms  (cache=db, cube_bytes=6746998)
  density second-call: 66 ms (cache=db, cube_bytes=6746998)
  mlp first-call: 63 ms      (cache=computed, cube_bytes=571768)
  mlp second-call: 51 ms     (cache=computed, cube_bytes=571768)

=== cache ===
  mep/mep_qm/homo/lumo/density hit-rate (2nd call): 1 (db)     — computed/cached ratio: n/a, first call was already db
  mlp hit-rate (2nd call): 0 (still 'computed')                — see docs/PERF.md §5 (F5 write-race hypothesis)

=== rss ===
  before: 227 MB   scf_cached=0/6
  after:  227 MB   scf_cached=0/6   (batch already DB-cached from an earlier run — see docs/PERF.md §7)
  delta:  0 MB

Exit code 0. 0 leftover Chrome temp profiles under /tmp after the run.
```

Earlier in this same session, before the `rss` batch molecules had ever been computed:
```
=== rss (cold, 2026-08-11T18:26:45Z–18:26:55Z) ===
  before: 361 MB   scf_cached=3/6
  after:  380 MB   scf_cached=6/6
  delta:  +19 MB
```

And ~14 minutes before the run transcribed above, at a bundle size more than double today's:
```
=== bundle (2026-08-11T18:24:47Z) ===
  raw bytes: 6688703 bytes   (= 6.38 MiB)
  gzip bytes: 1234476 bytes  (= 1.18 MiB)
=== load (same run) ===
  DOMContentLoaded: 677 ms
  load: 678 ms
=== swap (same run) ===
  mep: 162 ms (median, n=5) · mep_qm: 96 ms · homo: 146 ms · lumo: 130 ms · density: 165 ms · mlp: 96 ms
```
