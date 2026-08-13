# Dirac system architecture · the buildable spec

> Historical build specification from 2026-08-11. The implementation has since
> moved to the Command/Method/Job/Artifact architecture described by
> `../../../ARCHITECTURE.md`; this file is retained for design provenance only.
class: S · status: DRAFT · date: 2026-08-10 22:54 PDT · repo@662c386 (PRE-REWRITE SHA — history was rewritten 2026-08-11 to strip tooling trailers, so this hash no longer resolves; the date is the durable half)
N: 8901-endpoints=2 consumers · facet-callsites=11 · field_cube-writers(py)=1 · tokens.css-consumers=4  [measured:M04]
spec-budget: 600 lines / one session
staleness-predicate: `python3 ~/.claude/skills/software-spec/scripts/spec_lint.py SPEC.md --rerun`
INTERFACE: `contracts/iface.pyi` (Python half) · `contracts/iface.d.ts` (TS half)
BUILD STATE: `STATUS.md` (what is shipped vs a seam with no writer) · `ROADMAP.md` (the phases and their rewrite tripwires). This spec describes the contracts, NOT how much of them is wired — asking one document to be both is how a spec starts lying about a system that has moved.
FIGURE-2: §4 = the two stubs + the three STATE×EVENT tables + JOIN LEDGER + CONSTANT LEDGER + failure cells. Prose outside §4 is WHY.

## 0 · MEASUREMENT
```
when: 2026-08-10 22:54 PDT   against: /home/ivan/dirac@662c386 [pre-rewrite]
$ curl -s http://127.0.0.1:8901/health
{"ok": true, "rdkit": "2026.03.5", "pyscf": "2.14.0", "db_cache": "off", "scf_cached": 6, "scf_cache_max": 6, "rss_mb": 1077}
$ (embed aspirin; field mep)  # full commands in §7 ledger M02
embed ok=True 0.02s | field ok=True cache=computed 0.17s cube_bytes=571745
$ psql -U ivan -d dirac -tA -c "SELECT (SELECT count(*) FROM app.field_cube),(SELECT count(*) FROM app.blob),(SELECT count(*) FROM meta.producer),(SELECT count(*) FROM chem.compound)"
14|24|9|68
EXPECTED : healthy daemon, sub-second classical field, populated graph
SAID     : all true — AND db_cache:off is itself a live finding (swallowed producer
           tripwire fired again between 22:18 and 22:54; failure row F7)
```

## 1 · PRIOR ART
```
L1 live code   : found — the system EXISTS and runs (this spec describes+extends it, not a greenfield)
L2 archive+git : found — 121 commits; 27 fix/guard commits in the last 24h ARE the failure corpus [measured:M06]
L3 project docs: ARCHITECTURE.md (peer session) claims an /embed returning fingerprints — WRONG vs code;
                 treated as blind spot per gate rule, superseded by this file for interface truth
L4 framework   : mol* ships ccp4/dsn6/dx/cube parsers + BinaryCIF volume quantization + 13,161-line
                 plugin-ui React (unused, verified React-free bundle) — cited where they kill scope
L5 five principal reviews 2026-08-10: frontend/backend/data/security/pragmatist — their findings are
                 inlined at the exact site each applies (marked ⚖)
=> REUSE: mol* volume formats, existing PG schemas, existing daemon. ADAPT: producer→method registry,
   facet functions→Facet seam. GENUINELY ABSENT: job ledger, LigandStore, envelope v2.
```

## 2 · PROBLEM
Six sessions build one product on one tree. Every feature currently crosses two god files
(index.ts 1687 lines / index.html 1421 lines — churn #1 and #2), every result's provenance
depends on hand-bumped versions, and every long computation shares one process with every
short one. Measured cost in 24h: 2 hard resets by a sync timer, 1 CSS collision blanking the
whole UI, 1 silently-wrong iodine physics path, 4 generations of cache invalidated by
comment edits. The system works; its seams are in the wrong places.

## 3 · NON-GOALS (each with WHY NOT and its reopening trigger)
| non-goal | why not now | trigger that reopens it |
|---|---|---|
| DAG workflow orchestration | zero multi-step workflows exist today | first chained computation (FEP λ-windows) |
| distributed compute control plane | one box; jobs are 0.02–365 s | second machine, or first >1h job class |
| job cancel / SSE / admission-by-prediction | ⚖ backend: pyscf cannot cancel in-thread; measured workload 1.6–4.3 s; cost law N⁴ was retracted in-repo | first interactive >90 s task users must abort → process-per-job then |
| React / any framework | facets are imperative-60fps; bundle verified React-free | form-screen count > 3 (auth/admin work starts) |
| microservice split | N=2 services already cost 22 duplicated code sites | multi-machine or independent scaling |
| custom binary volume format | ⚖ frontend: mol* BinaryCIF/ccp4 exist in-tree; 128³ Float32=8.39MB can't meet a 2MB gate | never — use in-tree formats; gzip header today |
| hypothesis/decision/evidence tables (Genesis graph) | no real decision has needed a row yet | first recorded go/no-go on a molecule |
| auth beyond Host/Origin allowlist | single user, LAN; shell is the admin boundary | first second user or first WAN exposure |

## 4 · THE INTERFACE — Figure 2

### 4.1 Layers (6 + contracts; dependencies point down; two declared exceptions)
```
L5 presentation   src/app/... facets + shell        — may import mol* (platform exception 1)
L4 client data    LigandStore FieldCache BackendClient JobTracker ThemeService (iface.d.ts)
L4.5 browser compute  src/chemistry...rdkit-wasm (7,272 lines) — facets+L4 may import (exception 2);
                      law: no DOM, no fetch, no plugin           ⚖ frontend blocker 6
L3 service        backend/field_server.py (today) → backend/server/ (target) — routes+envelope only
L2 compute core   backend/dirac_chem/ ⬜PLANNED (today: functions inside field_server.py, physics/)
L1 data           PostgreSQL dirac: meta/chem/bio/design/app/audit — constitution layer
L0 runtime        systemd units ⬜PLANNED · CI dirac.yml ⬜PLANNED · per-session worktrees
X  contracts      contracts/iface.pyi · contracts/iface.d.ts · design/tokens.css
```

### 4.2 Data flows (three canonical, WITH error edges)
```
FLOW A · paste SMILES → physics on screen
 input#import-smiles ─→ BackendClient.embed ──HTTP──→ POST /embed ─→ embed_molecule()
   │ err: PARSE → import-status names the input; nothing loads          │ 0.02 s [measured:M02]
   ▼ ok                                                                 ▼
 LigandStore.setFromImport (kind:'import', coordSpace:'scene', gen++)  molfile
   ▼ broadcast (generation token)
 every facet.onLigand ─→ FieldCache.prefetch (classical always; quantum iff heavy≤40)
   ▼ per kind: cubeCache hit? ── yes → render (113–547 ms, zero network)
   │  no → POST /field ─→ ALLOWED_BASIS gate → deadline-bounded run_scf → field_*()
   │        err edges: BAD_BASIS|BUDGET|UNPARAMETERIZED|UNCONVERGED → panel copy names
   │        the actor and the working alternative; NOTHING renders (a zero is not a field)
   ▼ ok    cube+meta → cubeCache; background thread → db_put_cube (blob+row, producer-stamped)
             │ stale guard: response generation ≠ current → DISCARD ("stale field discarded")
FLOW B · deposited PDB ligand: scene load → setFromLoci(structure,bundle,cutoff) → same tail as A
FLOW C · cache round-trip: identical molfile bytes → db_get_cube via v_field_cube_current
         (superseded producer rows structurally unservable) → 0.04 s vs 3.49 s recompute
```

### 4.3 STATE × EVENT tables

**T1 · field request lifecycle (server side)**
```
STATE      | valid req         | bad basis        | non-finite budget | deadline hit      | SCF diverged     | ok
-----------|-------------------|------------------|-------------------|-------------------|------------------|------------------
RECEIVED   | VALIDATE          | ERR BAD_BASIS→END| clamp→VALIDATE    | —                 | —                | —
VALIDATE   | run_scf→COMPUTING | —                | —                 | —                 | —                | —
COMPUTING  | —                 | —                | —                 | ERR BUDGET→END    | SOSCF once→COMPUTING; second time ERR UNCONVERGED→END | cube→PERSISTING
PERSISTING | —                 | —                | —                 | —                 | —                | bg thread; respond ok→END (stored=scheduled, not confirmed)
```

**T2 · browser cube-cache entry**
```
STATE    | fetch()        | response(gen=cur) | response(gen≠cur) | ligand change | basis change     | producer change (/health)
---------|----------------|-------------------|-------------------|---------------|------------------|--------------------------
EMPTY    | PENDING(dedup) | —                 | —                 | —             | —                | —
PENDING  | join existing  | CACHED            | DISCARD→EMPTY     | drop→EMPTY    | drop→EMPTY       | drop→EMPTY
CACHED   | serve local    | —                 | —                 | clear→EMPTY   | evict quantum→EMPTY | clear→EMPTY  ⬜PLANNED (F9)
```

**T3 · producer generation (L1)**
```
STATE      | startup, same src | startup, new src+new ver | startup, new src+SAME ver | PG unreachable
-----------|-------------------|--------------------------|---------------------------|----------------
CURRENT    | reuse id          | supersede→new CURRENT    | RAISE (identity conflict) | —
(process)  | serve cache       | old rows dark (query)    | target: exit(1) [T-03]    | target: degrade, cache OFF, keep computing
                                                            today: swallowed→silent off = failure row F7
```

### 4.4 JOIN LEDGER (⚖ every fatal hole in review was a join, not a signature)
| J | what | producer keys | consumer keys |
|---|---|---|---|
| J1 | field kinds | `iface.pyi FieldKind: mep,mep_qm,homo,lumo,density,mlp` | `iface.d.ts FieldKind (identical 6)` |
| J2 | basis set | `ALLOWED_BASIS: sto-3g,6-31g,6-31g*,def2-svp` | `app.field_cube CHECK: same + 'none' (server maps classical→'none')` |
| J3 | molfile identity | `sha256(molblock utf-8) by client? NO — server-side field_server` | `app.field_cube.molfile_sha256 bytea(32)` |
| J4 | conformer identity | `conformer_hash_for: canonical-rank heavy, det=+1, 0.01A, +InChIKey` | `006 contract comment: identical algorithm text` |
| J5 | scene coordinates | `embed_molecule molfile [Angstrom, scene frame]` | `cube header origin/axes [Bohr] — parser converts ×0.529177 both ways` |
| J6 | error copy | `server error strings name actor+alternative` | `field-wells panel renders payload.error verbatim` |
| J7 | envelope v1 | `{ok,cube,meta}\|{ok:false,error:str}` flat | `field-wells reads payload.cube, payload.meta, payload.error — NOT payload.data` |
| J8 | theme tokens | `design/tokens.css --viz-* --scene-bg` | `field-wells tokenColor()/hexLuminance reads computed style; Kinds hex = fallback only` |

### 4.5 NAMED-CONSTANT LEDGER (unit · compared-against · source)
| name | value | unit | compared against (its unit) | source |
|---|---|---|---|---|
| DEFAULT_MAX_SECONDS | 90.0 | s | wall-clock elapsed (s) | chosen after 36-min HEM incident |
| MAX_MAX_SECONDS | 900.0 | s | caller-supplied budget (s) | chosen ceiling |
| SOSCF_MIN_REMAINING | 15.0 | s | remaining budget (s) | chosen |
| MAX_QM_ATOMS | 120 | atoms(with H) | mol atom count (atoms) | chosen; superseded in physics by cost model |
| MAX_BODY_BYTES | 8×1024² | bytes | Content-Length (bytes) | ported from physics hardening |
| PREFETCH_QM_MAX_HEAVY | 40 | heavy atoms | Ligand.heavyAtoms (heavy atoms) | chosen from HEM=43 lesson |
| CUBE_GRID_MEP | 50 | points/axis | grid dims (points) | measured: 80³ costs 4× for smooth potential |
| deadline check cadence | 1 | DIIS cycle | — | pyscf callback granularity (measured) |
| scf_cache_max | 6 | entries | live count [measured:M01] | peer-set bound |
| coarse-hit gate | 0.10 | Å RMSD | Kabsch residual (Å) | 006 contract; read path ⬜PLANNED |
| chroma ceiling | 0.106 | OKLCH chroma | every color token (chroma) | derived from approved #e0af68 |

### 4.6 Wire schemas
```
POST /embed  req {smiles?|molfile?, seed?=42}            resp Envelope(molfile, EmbedMeta)
POST /field  req {molfile, kind, basis?=sto-3g, spin?, max_seconds?=90}
             resp Envelope(cube: Gaussian-cube TEXT [Bohr], FieldMeta)
GET  /health resp {ok, rdkit, pyscf, db_cache:'on'|'off', scf_cached, scf_cache_max, rss_mb}
version rule: flat v1 shape is LIVE; v2 {ok,data,meta{envelope:2,request_id,producer}} ships
  with app.job; readers accept both for one version. unknown fields: MUST-IGNORE.
identity binding: FieldMeta carries method+basis+ecp; producer stamp lives in DB row (v1 gap:
  not in wire meta on cache miss path — closed by v2). write mode DB: blob+row, single
  transaction ⬜PLANNED (today two autocommit statements = failure row F5).
```

### 4.7 Proxy ledger (the checks that could lie)
| check | PROPERTY wanted | PROXY evaluated | safe side | witness / gap |
|---|---|---|---|---|
| `converged=True` gate | numbers describe the molecule | SCF residual < threshold | fail⇒refuse sound; pass⇒UNVERIFIED (iodine converged WRONG pre-ECP by 58 kcal/mol) | commit 3d4e118 |
| deadline | bounded wall clock | callback per DIIS cycle | overrun ≤ 1 cycle; pre-loop init guess UNBOUNDED by it | ⚖ security B2: cc-pv5z init-guess → basis whitelist dominates this writer |
| grep N-counts | real dependents | text match | absence sound; presence may count comments | ctrl: garbage symbol→0 [measured:M05] |
| `stored:true` | row persisted | thread SCHEDULED | ⚠ unsound as written — says scheduled, may fail after | fix: 'pending' + failed-write counter ⬜PLANNED |
| palette gate | readable, calm colors | OKLCH numeric bounds | out-of-band⇒fail sound; in-band⇒taste UNVERIFIED | Ivan approves samples |

### 4.8 Null-output detectors
| artifact | its null | detector |
|---|---|---|
| field cube | all-zero grid (Gasteiger NaN laundered) | non-finite charges → REFUSE by name (shipped after PF6⁻ incident); vmin/vmax in meta |
| SCF result | initial guess echoed | converged flag + energy sanity vs element sum; unconverged rows unwritable (DB CHECK) |
| cache hit | stale generation | producer view (DB) + generation token (browser) |
| /health | process up but degraded | db_cache:'off' + rss_mb + scf_cached visible [measured:M01] |

## 5 · FAILURE MODEL — earned: every row OBSERVED in the last 24 h [measured:M06 = 27 fix/guard commits]
| F | observed failure | root cause | current containment | residual |
|---|---|---|---|---|
| F1 | 36-min 22-core runaway (HEM click) | unbounded SCF iterations | per-cycle deadline | pre-loop init guess unbounded → basis whitelist |
| F2 | iodine silently wrong 58 kcal/mol, right sign flipped | def2 ECP not auto-attached | ecp_for() + meta.ecp + Koopmans check vs experimental IP | other Z≥37 paths share fix; test ⬜PLANNED (S2 iodine gate) |
| F3 | PF6⁻ all-zero MEP served as normal | NaN→nan_to_num laundering | named refusal UNPARAMETERIZED | DB had cached the zero → producer sweep executed |
| F4 | deadline disabled by `max_seconds:"nan"` | NaN fails every comparison → fails OPEN | isfinite clamp + negative control | — |
| F5 | 67% of blob store orphaned (34 MB) | two autocommit statements | — | single-transaction write ⬜PLANNED |
| F6 | 4 generations of cache dark in 53 min | producer = whole-file hash, hand-bumped | — | per-compute-unit hash, auto version ⬜PLANNED |
| F7 | tripwire fired → silent `db_cache:off` (twice today, incl. §0) | blanket except around register | — | split: OperationalError degrades / identity conflict exits ⬜PLANNED |
| F8 | UI erased: 1 unclosed CSS brace dropped 411 lines | 1076-line inline style, 6 sessions | brace-balance check exists | wire as CI gate; extract CSS (S4) |
| F9 | stale browser cube after producer bump | browser layer outside invalidation | — | flush on /health producer change ⬜PLANNED |
| F10 | commits orphaned twice by sync timer | dirac-sync does reset --hard + auto-push | worktrees + backup branches | sync → fetch+ff-only, no push ⬜PLANNED |
| F11 | LAN page could disable deadline / burn compute | CORS * + no Host check | Host/Origin allowlist + JSON-only + 403/415 controls | admin surface stays read-only; deletes via shell |
| F12 | rss_mb 1077 and growing pre-bound | mf objects retained in cache | scf_cache_max=6 [measured:M01] | store (dm, mo-slice) not mf; MemoryMax ABOVE pyscf budget |

## 6 · COST
This spec + seams (job table ~40 lines SQL, method-registry columns ~30, LigandStore ~120 TS,
envelope v2 ~60): ≈ one working day inside the S0–S5 plan already agreed.
Do nothing / extend the incumbent (killed LAST and HARDEST, as required): keep shipping
features on the god files and hand-fix collisions. Build cost 0 lines — and it is not a straw
man: the incumbent DID ship six fields, an import pipeline and a DB in one day. It is
rejected on one number only: its observed failure rate is 27 fix/guard commits per day
(ledger M06) with two hard resets and one full-UI blackout, i.e. the incumbent's maintenance
bill is the spec's entire build cost, daily. If S0–S5 does not reduce that rate, the kill
condition below fires and the incumbent wins.

## 7 · MEASUREMENT LEDGER
| id | value | cmd | ctrl |
|---|---|---|---|
| M01 | 1 | `curl -s 127.0.0.1:8901/health \| grep -c '"ok": true'` | `curl -s 127.0.0.1:8901/health \| grep -c rss_mb` |
| M02 | True 571745 | `cd /home/ivan/dirac && backend/env/bin/python -c "import json,urllib.request as u; e=json.loads(u.urlopen(u.Request('http://127.0.0.1:8901/embed',json.dumps({'smiles':'CC(=O)Oc1ccccc1C(=O)O'}).encode(),{'Content-Type':'application/json'})).read()); f=json.loads(u.urlopen(u.Request('http://127.0.0.1:8901/field',json.dumps({'molfile':e['molfile'],'kind':'mep'}).encode(),{'Content-Type':'application/json'})).read()); print(f['ok'], len(f['cube']))"` | `cd /home/ivan/dirac && backend/env/bin/python -c "print(571745 > 0)"` |
| M03 | 57\|67\|18\|68 &nbsp;*(was 14\|24\|9\|68 when written; re-run 2026-08-11. producers 9→18 is the derived-version change working as designed, and it no longer costs cache currency because reads moved onto METHOD currency first — that order was load-bearing)* | `psql -U ivan -d dirac -tA -c "SELECT (SELECT count(*) FROM app.field_cube),(SELECT count(*) FROM app.blob),(SELECT count(*) FROM meta.producer),(SELECT count(*) FROM chem.compound)"` | `psql -U ivan -d dirac -tAc "SELECT count(*) FROM chem.compound" \| grep -vc '^0$'` |
| M04 | 2 | `cd /home/ivan/dirac && rg -lc 8901 src/app.frontend.facets.molstar-rdkit.editable/index.ts src/app.frontend.facets.molstar-rdkit.editable/facets/field-wells/index.ts \| wc -l` | `cd /home/ivan/dirac && rg -l field_cube backend/db \| wc -l` |
| M05 | 0 | `cd /home/ivan/dirac && rg -l definitely_not_a_symbol_xyzq src backend \| wc -l` | `cd /home/ivan/dirac && rg -l field_cube backend/db \| wc -l` |
| M06 | 27 | `cd /home/ivan/dirac && git log --oneline --since="24 hours ago" \| grep -cE "(fix\|guard)\."` | `cd /home/ivan/dirac && git log --oneline --since="24 hours ago" \| wc -l` |
| M07 | 21\|21 · 22\|22 identical | meta SHAPE PARITY across cache paths — `backend/env/bin/python -c "import json,time,urllib.request as u; from rdkit import Chem; from rdkit.Chem import AllChem; m=Chem.AddHs(Chem.MolFromSmiles('CCO')); AllChem.EmbedMolecule(m,randomSeed=int(__import__('os').environ.get('SEED','5551'))); mb=Chem.MolToMolBlock(m); r=lambda: json.loads(u.urlopen(u.Request('http://127.0.0.1:8901/field',json.dumps({'molfile':mb,'kind':'mep'}).encode(),{'Content-Type':'application/json'})).read())['meta']; a=r(); time.sleep(2); b=r(); print(len(a),len(b),set(a)==set(b),a['cache'],b['cache'])"` run it as `SEED=$RANDOM backend/env/bin/python -c ...` — a FRESH conformer is what makes the first read a compute. The command must print `computed db`. Both `db` ⇒ the parity claim was never exercised; both `computed` ⇒ the async persist has not committed (2 s is the measured wait — the first run of this measurement read computed→computed and I nearly filed a regression against an instrument that was simply faster than the write) |
| M08 | 18 generations \| max 3 per unit \| 6× | `curl -s 127.0.0.1:8901/admin/cache \| python3 -c "import sys,json; d=json.load(sys.stdin)['data']; print(d['producer_generations'], d['max_generations_per_unit'])"` | `psql -U ivan -d dirac -tAc "SELECT max_generations_per_unit > 0 FROM app.v_cache_health"` — a zero denominator makes the ratio infinite and the metric a fabrication, so the control is on the DENOMINATOR |
| M09 | 2 convictions \| 4 convictions | both new gates prove themselves: `node scripts/check_contract_drift.mjs --redproof \| tail -1` · `node scripts/check_physics_contract.mjs --selftest \| tail -1` | each harness asserts its own mutation CHANGED the bytes before any verdict is read (scripts/lib/mutate.mjs) — the control for a red proof is that the experiment ran at all |
| M10 | 0 | the red proof must not dirty the tree: `node scripts/check_contract_drift.mjs --redproof >/dev/null && git status --porcelain contracts backend/envelope.py \| wc -l` | `git status --porcelain \| wc -l` must be NON-zero, proving the instrument can see dirt at all |

TRACE: `cd /home/ivan/dirac && backend/env/bin/python -c "import json,urllib.request as u; e=json.loads(u.urlopen(u.Request('http://127.0.0.1:8901/embed',json.dumps({'smiles':'c1ccncc1'}).encode(),{'Content-Type':'application/json'})).read()); f=json.loads(u.urlopen(u.Request('http://127.0.0.1:8901/field',json.dumps({'molfile':e['molfile'],'kind':'mep'}).encode(),{'Content-Type':'application/json'})).read()); assert f['ok'] and len(f['cube'])>100000, f; print('TRACE OK', f['meta']['units'])"`

## KILL CONDITION
If adding the σ-hole facet (S5) requires editing ANY file outside `facets/sigma-hole/`,
one route registration, and one migration — the seam design is wrong and this spec is
falsified. Run at S5 completion; scheduled: within this week's S0–S5 execution.

## MECHANISATION DEBT
| law stated here | status | ending check |
|---|---|---|
| coordSpace '2d' refused by 3D consumers | prose | unit test feeding sketch2d ligand to FieldCache.fetch → must throw [T-01] |
| envelope v2 dual-read window | prose | contract test against both shapes [T-02] |
| tripwire split (degrade vs exit) | prose | startup test: same-version+new-source must exit 1 [T-03] |
| meta shape identical across cache paths | **MECHANISED** 2026-08-11 | `normalize_meta` at both exits + gate 7 check 4 (emitted keys ⊆ FIELD_META_SCHEMA); M07 |
| every SCF route bounded before AND during | **MECHANISED** 2026-08-11 | gate 8 `check_physics_contract.mjs`, ships a 4-conviction selftest; M09 |
| a gate must be SHOWN to convict | **MECHANISED** 2026-08-11 | gates 7 and 8 run their red proof FIRST and its failure IS the gate failure; M09, M10 |
| a red proof must not be a silent no-op | **MECHANISED** 2026-08-11 | `scripts/lib/mutate.mjs` throws NoOpMutation before any verdict is read |

Four rows moved from prose to mechanism in one day, and the reason belongs here rather than in a
commit nobody re-reads: **each was a law I had already WRITTEN DOWN and then violated within hours
of writing it.** The meta-shape law was stated in `normalize_meta`'s own docstring while both
response paths ignored the function entirely. The conviction law was stated in §4.7's proxy ledger
while two gates sat green with no evidence they could see anything at all. Prose I authored is the
weakest enforcement available, because I am the reader it fails to bind.
