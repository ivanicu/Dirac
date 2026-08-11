# Frontend state audit — is `ligand-store.ts` in use?

Audited: 2026-08-11, 11:45–12:15 PDT, starting against HEAD `f9dabd6`, last re-verified
against HEAD `6a04662`. **This repo is being edited live by other sessions while this audit
was written — three times, materially, inside the audit window itself (see the callouts in
§2.2 and §3).** Commit hashes below are as observed at the moment each claim was checked; line
numbers were re-verified against the tree after each live edit that touched a cited file, and
this document says so explicitly wherever a citation had to be corrected mid-audit. Read-only
audit — no application file was changed to produce this document; every line-number
correction below came from re-reading the object, not from memory of the earlier read.

**Verdict up front: `ligand-store.ts` is shelfware. Zero non-test files import it.** The
codebase it was written to replace has, in the meantime, independently re-solved the same
problem (a molecule-generation token that discards stale async results) three separate times,
in three different files, with three subtly different bugs and guarantees — which is exactly
the failure mode the module's own docstring says it exists to prevent. Separately, and
discovered independently by this audit, one of the exact bug classes `ligand-store.ts` exists
to make structurally impossible (§2.2) was live in the tree at the start of this audit, and a
different session landed a partial fix for it — commit `4f3fe20` — **while this document was
being written**, confirming the diagnosis from the outside.

## Scoreboard

| metric | count |
|---|---|
| modules that hold/produce "the currently focused ligand" in some form | **8** — lab (`index.ts`), field-wells, ligand-physics, pharmacophore-designer, bond-atlas, property-cockpit, halogen-audit (new during this audit window), `ChemistryCache` |
| of those, modules that import `ligand-store.ts` | **0 / 8** |
| commits since `ligand-store.ts` landed (316eece) that touch it again | **0** (41 other commits had landed on the tree by the end of this audit, three of them touching files this audit cites; measured live, will already be higher by the time this is read) |
| async paths that produce a ligand-shaped result | **10** identified |
| … of those, that check result currency before committing it | **3 / 10** (field-wells ×2 call sites sharing one mechanism, `ChemistryCache` ×1) |
| facts computed from a molfile in more than one independent place | **3 major** (heavy-atom count, atom-index walker, staleness/generation token) + 1 minor (raw counts-line byte-parsing repeated 4×) |
| of the 3 major duplications, confirmed to actually disagree today | **1** (heavy-atom count — confirmed via a sibling module's own bug-fix commit) |
| `ligand-store.spec.ts` test cases / currently failing | **9 / 1 failing** (measured: `npx jest src/app/services/_spec/ligand-store.spec.ts`) |
| `ligand-store.ts` + its spec, ESLint errors | **4** (measured: `npx eslint`) |
| FieldMeta keys the backend emits vs. what field-wells' frontend interface declares | **42 emitted, 21 missing** (measured: `node scripts/check_contract_drift.mjs`, exit 2) |

---

## 0 · Why this question has a clean answer

`git log --oneline -- src/app/services/ligand-store.ts` returns exactly one commit:

```
316eece [feat.frontend.sigma.D7+] LigandStore: one home for the molecule, one generation
        token for every async consumer                    2026-08-11 10:26:09 -0700
```

`rg -n "ligand-store|LigandStore|ligandStore" src` outside the file and its spec returns
exactly one hit, and it is a comment, not an import:

```
src/chemistry.backend.perception.rdkit-wasm.editable/chemistry-cache.ts:4:
    * S0 item 2+3: LigandStore generation semantics + ChemistryCache.
```

The file's own docstring says so (`src/app/services/ligand-store.ts:32–35`):

> "NOT WIRED YET, deliberately: index.ts is being edited by other sessions right now, and
> this file is the seam it will import. It is standalone, typechecked, and tested; adoption
> is a separate commit that deletes the three fields."

That commit never happened. The project's own architecture doc, committed **24 seconds**
after `ligand-store.ts` (`c078c1a`, 2026-08-11 10:26:53 -0700, 「the architecture doc
described a system that was not there; this one is verified line by line」), says the same
thing from the other side (`ARCHITECTURE.md:64–75`):

> "L4 CLIENT DATA  LigandStore · FieldCache · BackendClient · JobTracker · ThemeService —
> declared in iface.d.ts. **NOT YET A SEPARATE MODULE**: today every one of these is
> reimplemented inline inside index.ts and inside field-wells/index.ts (its own cubeCache,
> its own molfile tracking)."

And `SPEC.md:34` (written the evening before, `repo@662c386`) lists it as a target, not a
fact: `"GENUINELY ABSENT: job ledger, LigandStore, envelope v2."` `SPEC.md:196` prices it at
"~120 TS" lines (the shipped file is 226 — not free but not the reason it wasn't wired
either).

So three independent project artifacts — the file's own header, the architecture doc, and
the spec — all agree on the same fact from three angles. This audit's job is to check whether
that fact is still true (it is) and what it costs (below).

---

## 1 · Adoption — who reads the store, who keeps their own copy

Starting point: `src/app.frontend.facets.molstar-rdkit.editable/index.ts` (the `MolecularVfxLab`
class — 1786 lines when this audit started, 1850 by the end of it, live) and `facets/*/index.ts`.

| module | handles a ligand? | reads `ligand-store`? | actual mechanism |
|---|---|---|---|
| `index.ts` (`MolecularVfxLab`, the lab/orchestrator) | yes — the source of truth today | no | three private fields: `smartsSearchMolfile: string \| null`, `smilesMolfile: string \| null`, `chemistryCache: ChemistryCache` (`index.ts:380–382`) |
| `facets/field-wells/index.ts` | yes | no | module-level `let molfile: string \| null`, `let ligandLabel` (`field-wells/index.ts:177–178`), written by `updateFieldWellsLigand(nextMolfile, label)` (`field-wells/index.ts:802`) |
| `facets/ligand-physics/index.ts` | yes | no | module-level `let molfile`, `let ligandLabel` (`ligand-physics/index.ts:75–76`), written by `updateLigandPhysics(nextMolfile, label)` |
| `facets/pharmacophore-designer/index.ts` | yes | no | `PharmacophoreDesigner.model.source: {structureId, ligandLabel}` (a full `Structure`, not a molfile — different shape entirely), written by `updatePharmacophoreDesigner(structure, options, source)` |
| `facets/bond-atlas/index.ts` | yes | no | `state: {atlas, channels}` (`bond-atlas/index.ts:43`) — holds only the *derived* atlas, no memory of which molfile produced it at all |
| `facets/property-cockpit/index.ts` | yes | no | **no internal state** — `renderPropertiesPanel(molfile, ligandLabel)` (`property-cockpit/index.ts:115`) is a pure function called with fresh arguments each time |
| `facets/halogen-audit/index.ts` (new — landed mid-audit, commit `4f3fe20`) | yes | no | receives a `Structure` + `LigandFocusOptions` fresh on every call via `updateHalogenAudit(structure, options, qmBySymbol)` (`halogen-audit/index.ts:138`); fully synchronous, no async exposure at all — kept for completeness, not examined as deeply as the other six |
| `chemistry.backend.perception.rdkit-wasm.editable/chemistry-cache.ts` (`ChemistryCache`) | yes | no | its own `generation` counter + `data.molfile`, independently reinventing the exact mechanism `ligand-store.ts` provides (see §0 and §3) |

**Every one of the six facets receives the ligand as a plain function-call parameter from the
lab**, not via subscription to a shared store. This matches `ligand-store.ts`'s own diagnosis
of the problem it was built to fix (`ligand-store.ts:4–8`): "the focused molecule currently
lives in three-to-four parallel fields inside the lab class... Each consumer reads whichever
one its author knew about." That description is still accurate today, 0 commits after the fix
landed.

**Interface contract drift, on top of non-adoption.** `contracts/iface.d.ts:35–43` declares
the *target* `LigandStore` interface, and it already disagrees with the shipped
`ligand-store.ts` on method shape, not just on wiring:

| | `contracts/iface.d.ts:39–40` (target) | `src/app/services/ligand-store.ts:158–166` (shipped) |
|---|---|---|
| `setFromImport` | `(molfile: string, meta: {inchikey, label, seed}): void` | `(args: {molfile, label, inchikey, seed}): void` — flattened into one object |
| `setFromLoci` | `(structureRef, bundleRef, cutoffA): Promise<void>` — **async**, no `molfile`/`label` params (implies the store derives them internally) | `(args: {molfile, label, inchikey?, structureRef, bundleRef, cutoffA}): void` — **sync**, caller must pre-compute the molfile |

The contract also has no `setFromSketch2d`, `clear`, `isCurrent`, or `requireScene`/
`CoordSpaceError` — all present in the shipped file. **WHY IT BITES:** a future session
trying to "just wire it in" cannot do so by reading the contract; the contract and the
implementation describe two different APIs, and reconciling them is itself unscoped work.
CONFIDENCE: CONFIRMED (read both files in full).

---

## 2 · The coordinate-space hazard

`ligand-store.ts` exists partly because a molfile string carries no marker for whether its
coordinates are a 2D depiction layout or a real 3D scene position. Below, every molfile
boundary crossing found, with which space it is in and how that is known — and one place
where the answer is not "undecidable from the type system" (expected, since `coordSpace`
isn't adopted) but **actively wrong at runtime**, which is worse.

### 2.1 The boundaries that are correctly disciplined today

| boundary | space | how it's known |
|---|---|---|
| `backend/field_server.py` `POST /embed` response molfile, consumed at `index.ts:1569` (`importMolecule`) | scene (3D) | comment: "Generated in-backend: ETKDGv3 embed + MMFF94 optimization" (`index.ts:1566`); loaded straight into the mol* workbench via `loadStructureFromData` |
| PDB-deposited ligand loci → molfile, `ligand-pipeline.ts:ligandLociToMolfile` | scene (3D) | derived directly from `unit.conformation.position(...)` on a loaded `Structure` — cannot be anything else |
| `smiles-input` box → `loadFromSmiles` → `this.smilesMolfile`, `index.ts:1222–1281` | 2D | explicit: `mol.set_new_coords(true)` before `get_molblock()` (`index.ts:1250`), and the comment at `index.ts:1271–1273`: "Do NOT load into mol* ... 3D Overpaint is skipped in SMILES mode" |
| field-wells / ligand-physics receiving `molfile` from the lab | scene (3D), **by convention, not by type** | `field-wells/index.ts:6–8`: "The molfile carries scene coordinates, so every cube the backend returns is already registered with the mol* scene — no alignment step exists." Same sentence, same assumption, at `ligand-physics/index.ts:239–240`. Nothing in either file's type signature (`molfile: string \| null`) enforces this — the check is that the lab happens to only call these functions from the PDB/loci branch of `renderLigandDepiction`. |

**WHAT:** every 3D-consuming facet accepts `molfile: string` with zero runtime tag for
coordinate space; correctness rests entirely on the caller only ever invoking them from the
scene-coordinate branch. **WHY IT BITES:** this is exactly the situation `ligand-store.ts`'s
`CoordSpace`/`requireScene()` was designed to make impossible at the type level — and it is
still possible, because nothing calls `requireScene()`. CONFIDENCE: CONFIRMED.

### 2.2 The hazard that is not hypothetical: `this.smilesMolfile` is a sticky field, and this audit watched it get half-fixed live

This is the single highest-severity finding in this audit, and it is a **coordinate-space
bug caused directly by the absence of the one-home guarantee** `ligand-store.ts` would
provide. It is also the clearest instance in this repo of why the bug class matters, because
the timeline is not hypothetical: this audit found the defect by static reading, then watched
a separate live session land a partial fix for the *same* defect — independently, for its own
reasons — before this document was finished. That is reported here in full, including the
part that is still broken, rather than quietly rewritten as if the fix had always been there.

**What this audit read first (repo state at HEAD `316eece`..`f9dabd6`, before commit
`4f3fe20`).** `grep -n "smilesMolfile" index.ts` showed exactly three write sites in the whole
file: the declaration (`null`); a clear to `null` only inside `loadFromSmiles()` when the
`#smiles-input` box is emptied; and a set to the freshly-parsed 2D molfile, also only inside
`loadFromSmiles()`. Neither `loadMolecule()` (the `#molecule` dropdown handler) nor
`importMolecule()` (the `#import-smiles` ETKDG/MMFF 3D pipeline) touched the field at all. Since
`renderLigandDepiction()` checks `if (this.smilesMolfile)` first and returns early when truthy,
*any* prior use of the 2D search box made every subsequent PDB load or 3D import invisible to
`updateFieldWellsLigand`, `updateLigandPhysics`, `updateBondAtlas`, and
`updatePharmacophoreDesigner` — because those calls lived only in the branch the early return
skips.

**What landed mid-audit.** Commit `4f3fe20`, `[fix.lab.rho.D8+] import a molecule, click a
field, nothing happens — under a status line that says "rendering electrostatic well…"`,
addresses exactly the `importMolecule()` half of this, in its own words (comment now at
`index.ts:1541–1559`): *"S0 (the ligand-pipeline dedup) removed the `importedMolfile` field
and left only the comment that described it, so nothing on this path set an active molfile at
all... Reproduced 3x in an isolated browser before this fix."* The fix does two things,
verified against the current tree: (a) `importMolecule()` now sets
`this.smilesMolfile = payload.molfile` (`index.ts:1560`) on every successful import, so a
freshly imported molecule is no longer silently invisible to the 3D-consuming facets; (b) a
new private method `fanOutLigand(molfile, label, structure?)` (`index.ts:1177–1195`) unifies
the previously-diverged call lists for the SMILES/import branch and the PDB/loci branch — its
own docstring (`index.ts:1154–1176`) names the exact defect: *"The PDB path told seven
consumers; the SMILES/import path told two... So an imported molecule reached the screen with
a 2D picture, a Lipinski table — and a Fields facet whose `molfile` was still null."* This is
an independent, external confirmation — by a different session, working from the live browser
rather than from source reading — that the defect class this audit is built around is real
and user-visible, not a theoretical type-safety concern.

**What is still broken, verified against the current tree (HEAD `9b0f698` at time of
writing).** `loadMolecule()` (`index.ts:1585–1598`) — the plain `#molecule` dropdown handler
for the nine bundled PDB structures — was **not** touched by the fix and still never assigns
`this.smilesMolfile`. Re-verified a second and third time as the tree kept moving during this
audit (HEAD advanced `9b0f698` → `6a04662` across four more live commits to unrelated files
while this section was being finalized): the function body is unchanged, at the same line
range, on every check. Reproduction, current and live: (1) type any valid SMILES into
`#smiles-input`, **or** import one through `#import-smiles` — either way `this.smilesMolfile`
becomes non-null and self-perpetuating; (2) pick a *different* structure from the `#molecule`
dropdown. `renderLigandDepiction()` still takes the `if (this.smilesMolfile)` branch
(`index.ts:1025`) and calls `this.fanOutLigand(this.smilesMolfile, …)` (`index.ts:1058`) with
the **stale** molecule — feeding the wrong molfile into the 2D depiction, the RDKit
descriptor/property panel, canonical identifiers, field-wells, ligand-physics, and bond-atlas,
all via the single fan-out point that was just built to guarantee they'd agree.
`updateHalogenAudit`/`updatePharmacophoreDesigner` are the two exceptions — `fanOutLigand`
only calls them `if (structure)` (`index.ts:1184`), and the SMILES-branch call site never
passes one for the just-loaded PDB structure — so instead of showing wrong data they simply
stop updating, which is a smaller but still real symptom (a newly loaded structure's
halogen/pharmacophore panels silently freeze on whatever the previous molecule showed).
No test in `_spec/` covers this (`rg -n "smilesMolfile" .../\_spec` returns nothing).

**WHY IT BITES, today:** a chemist pastes a SMILES to eyeball its 2D structure, then picks a
real PDB complex from the dropdown to look at fields/physics around the deposited ligand — the
five fanned-out panels keep showing the discarded scratch molecule, and the two
structure-dependent panels quietly go stale, with no error and no indication anything is
wrong. This is precisely the class of bug `ligand-store.ts`'s "ONE HOME" property
(`ligand-store.ts:21–23`, "a consumer cannot pick the wrong copy because there is no other
copy") exists to make structurally impossible — and a live, independently-motivated attempt to
fix half of it from the outside, without adopting the store, has left the other half
(`loadMolecule`) exactly where it was. CONFIDENCE: CONFIRMED (full code path read against the
current tree at HEAD `9b0f698`; all `smilesMolfile` write/read sites re-enumerated after the
live edit, no others exist).

---

## 3 · Generation / staleness — which async paths actually check currency

Every async operation that can produce a "here is data for ligand X" result, and whether it
verifies X is still current before writing to the DOM/scene.

| # | path | mechanism | currency check? | confidence |
|---|---|---|---|---|
| 1 | `field-wells.fetchField` → `POST /field` (cube SCF/classical field), `field-wells/index.ts:263–312` | captures `requestMolfile = molfile` before `await fetch`; after response, `if (molfile !== requestMolfile) return null;` (`:289`) | **guarded** | CONFIRMED |
| 2 | `field-wells.prefetchAll`, `:336–382` | loop checks `if (molfile !== startedFor) return;` before and after each kind (`:353`, `:365`) | **guarded** | CONFIRMED |
| 3 | `field-wells.requestField`, `:645–704` | captures `requestMolfile`; after `fetchField` resolves, `if (entry === null \|\| molfile !== requestMolfile)` → "Ligand changed while computing — stale field discarded." (`:675–676`) | **guarded** | CONFIRMED |
| 4 | `ligand-physics.runSurface` → `POST /surface/mep` (σ-hole pyscf SCF), `ligand-physics/index.ts:298–324` | `post('/surface/mep', {molfile}, …)` is awaited; on success, writes `out.extrema/out.meta` straight into `#phys-surface-body` (`:311`) with **no post-await comparison of `molfile` against any captured value** | **unguarded** | CONFIRMED |
| 5 | `ligand-physics.runTorsion` → `POST /torsion/strain` (MMFF scan), `:333–360` | same shape as #4, same absence: writes into `#phys-torsion-body` (`:345`) unconditionally on `out.ok` | **unguarded** | CONFIRMED |
| 6 | `bond-atlas.updateBondAtlas`, `:214–232` | `state.atlas = await computeBondAtlas(molfile);` (`:225`) then draws — no capture of a request-time identity at all | **unguarded** | CONFIRMED |
| 7 | `property-cockpit.renderPropertiesPanel`, `:115–152` | `const report = await computeLigandDescriptors(molfile);` (`:129`) then writes DOM using the *argument* `molfile`/`ligandLabel` from this call — no module state to compare against, and no caller-side check either | **unguarded** | CONFIRMED |
| 8 | `pharmacophore-designer.update`, `:96–119` | only guards against overwriting a *user-edited* model of the *same* source (`:109–115`, `sameSource && dirty`); a fresh, non-dirty model has no protection — `await computePharmacophoreFeatures(structure, options)` (`:117`) result is seeded unconditionally | **unguarded** for the common (non-dirty) case | CONFIRMED |
| 9 | `ChemistryCache.update`, `chemistry-cache.ts:55–82` | `this.generation++`, `startGen`, runs 3 RDKit calls in parallel, then `if (this.generation !== startGen) return startGen; // caller checks and discards` (`:67–70`) | **guarded internally** | CONFIRMED |
| 10 | `index.ts.importMolecule` → `POST /embed`, `:1511–1583` | no generation captured before the `fetch`; on response, unconditionally sets `this.currentMolecule`, loads the structure, calls `applyRepresentationAndVisuals` | **unguarded** | CONFIRMED |

**3 of 10 guarded** (field-wells' one mechanism used at two call sites, plus `ChemistryCache`'s
internal check). **7 of 10 unguarded**, including the two that matter most because they are
genuine multi-second network calls to a quantum-chemistry backend (#4, #5) — the exact
scenario `ligand-store.ts`'s own docstring cites as an already-shipped bug (`ligand-store.ts:12–14`):
"a completed SCF rendered into the WRONG molecule's scene, because the response of a request
issued for molecule A arrived after the user had moved to molecule B."

**To trigger #4/#5 concretely:** click "Run surface electrostatics" on ligand A (pyscf SCF,
budget up to 90 s per `ligand-physics/index.ts:31`); before it returns, switch focus to ligand
B through any path that reaches `renderLigandDepiction`'s PDB branch. `updateLigandPhysics(B)`
does call `inFlight?.abort()` (`:407`) — but `AbortController.abort()` has no effect on a
`fetch` whose response has *already* fully arrived and whose `.json()` has already resolved;
it only cancels requests still in flight at the moment of the call. If A's SCF response lands
in that narrow window — plausible, since SCF wall-clock varies with molecule size and A may
simply finish around the same time B is selected — `runSurface`'s continuation runs after
`updateLigandPhysics(B)` has already cleared `#phys-surface-body` for the switch, and
overwrites it with **A's σ-hole table, rendered as if it belonged to B**, with no discard
check to catch it. **What appears on screen:** B's ligand physics panel shows A's electrostatic
extrema and 3D markers (`markExtrema`, `:246–296`, also unguarded, drops spheres into the
scene at A's atom positions) with B's label nowhere near them, and nothing signals the
mismatch. CONFIDENCE: CONFIRMED for the code path (both functions read end to end); the
narrow-timing-window framing is D6 (plausible, not reproduced by running the SCF live in this
audit — that would require driving the actual UI, which was out of scope for a read-only
static audit).

> **⚠ Live during this audit, this exposure grew a second consumer.** Another session added a
> publish/subscribe surface to this exact function while this document was being written:
> `let lastSurface` + `publishSurfaceResult()` (`ligand-physics/index.ts:195,203–206`), so that
> the halogen-audit facet can read the last σ-hole result without re-running the SCF. The new
> `publishSurfaceResult(out.extrema, out.meta)` call sits at `:316`, immediately after the
> already-unguarded `#phys-surface-body` write, and inherits the identical exposure: nothing
> between the `await post(...)` and the publish checks that `molfile` is still the one that was
> requested. `updateLigandPhysics()` does now clear `lastSurface = null` unconditionally on
> every call (`:400`, added in the same commit) specifically because, in the author's own words,
> "carrying it across a ligand switch is how a number ends up describing something it never
> saw" — but that clear is subject to the identical ordering race described above: if it runs
> *before* a late `publishSurfaceResult` for the previous ligand, the late publish overwrites
> the clear. **This means remediation item 5 below now closes two exposure points instead of
> one** — the DOM table and the new cross-facet channel a second facet already depends on — and
> the cost of leaving it open just increased, live, during the time it took to write this
> sentence. CONFIDENCE: CONFIRMED (diff read in full against the working tree).

---

## 4 · Duplicated derived state

### 4.1 Heavy-atom count — three homes, confirmed to disagree

| home | logic | correct? |
|---|---|---|
| `ligand-store.ts:109–113` `heavyAtomsFromMolfile` | `parseInt(molfile.split('\n')[3].slice(0,3))` — the raw V2000 counts-line total | **no** — counts explicit hydrogens as heavy, and silently returns garbage on V3000 |
| `field-wells/index.ts:216–233` `molfileHeavyAtoms` | walks each atom line, skips `H`/`D` by element symbol; has a separate V3000 branch | yes (this is the fixed version) |
| `chemistry.backend…/descriptors.ts:113` `numHeavyAtoms: raw.NumHeavyAtoms` | RDKit's own descriptor, authoritative | yes |

The field-wells version carries its own bug-fix docstring, word for word describing the exact
defect `ligand-store.ts`'s copy still has (`field-wells/index.ts:207–214`):

> "The old body took columns 0-3 of line 4, which is the TOTAL atom count and includes
> hydrogens, so the number driving the 'is quantum affordable' gate was wrong by a factor of
> about two on anything with explicit H. It also returned 0 for a V3000 molfile... and 0 heavy
> atoms passes every affordability check there is."

**Chronology matters here.** That fix landed in commit `579d65f`, 2026-08-10 23:23:55 -0700 —
**11 hours before** `ligand-store.ts` (`316eece`, 2026-08-11 10:26:09 -0700). The newer file
still shipped the older, already-diagnosed-and-fixed bug, because it was written without
importing or even referencing the sibling fix. `heavyAtoms` is stored on every `Ligand`
variant and documented as "used by prefetch policy" (`ligand-store.ts:46`) — if adoption ever
proceeds by wiring the store as-is, it reintroduces a bug this repo already paid to find and
fix once. **WHY IT BITES:** an ETKDG-imported ligand with explicit hydrogens (or any
future producer that emits explicit H) would be judged ~2× "heavier" than it is by anything
that later reads `Ligand.heavyAtoms`, silently skipping quantum prefetch for molecules that
should get it. CONFIDENCE: CONFIRMED (both function bodies read; the bug-fix commit message
matches the un-fixed function verbatim in mechanism).

A fourth, lower-severity site re-parses the same fixed-width counts line independently for a
*different* purpose (total atom count for sizing a SMARTS-match flag array, not "heaviness"):
`semantic-chemistry-rdkit.ts:820–822`. Not shown to disagree with anything (it wants the total,
and gets the total) — flagged only because it is a fourth hand-rolled parse of a fragile
fixed-column format with no shared helper.

### 4.2 Atom-index walker — three homes, same algorithm today, unenforced

`ligand-pipeline.ts:1–7` states the intended invariant explicitly:

> "Shared ligand-pipeline utilities — the ONE copy of each function that was previously
> duplicated across semantic-chemistry-rdkit.ts and pharmacophore-features.ts. S0 exit test:
> `rg -l 'function ligandLociToMolfile|...' src/chemistry*` must return ONLY this file."

That test's glob is `src/chemistry*` — it does not, and cannot, see
`src/app.frontend.facets.molstar-rdkit.editable/`. Two hand-rolled copies of the identical
walk live there:

- `index.ts:1349–1376` `selectLigandAtomByIndex` (2D-click → 3D-select)
- `index.ts:1404–1446` `updateLigandDepictionSelectionHighlights` (3D-select → 2D-highlight)

Both re-implement, verbatim in structure, the same loop as `ligand-pipeline.ts`'s
`ligandLociToMolfile` (`:63–80`): `for (const e of loci.elements) { if (!Unit.isAtomic(e.unit))
continue; const count = OrderedSet.size(e.indices); for (let i = 0; i < count; i++) {...} }`.
The comment at `index.ts:1402` even names the coupling: "Uses the same atom-index walker as
`ligandLociToMolfile` + `selectLigandAtomByIndex`" — acknowledging the duplication rather than
calling the shared function. **WHY IT BITES:** the three copies agree today only because
nobody has touched the iteration order since the S0 refactor. If `ligandLociToMolfile`'s walk
ever changes (a new unit-filter, multi-model handling, a different element-exclusion rule —
the kind of change the heavy-atom fix in §4.1 was), the two copies in `index.ts` will silently
diverge from it, and the failure mode is exactly the one the task brief named: a click on the
2D depiction selects the *wrong atom* in the 3D scene, or vice versa, with the two views
disagreeing about which atom index means what. CONFIDENCE: CONFIRMED (all three call sites
read; the S0 exit-test's own glob scope confirmed not to cover `index.ts`).

### 4.3 Staleness/generation mechanism — three independent implementations

Covered in §0 and §3 in detail; summarized here as a duplication:

1. `ligand-store.ts` — `generation: number` + `isCurrent(g)`, **unused**.
2. `chemistry-cache.ts` — its own `generation: number` + `isValid(g)`/`currentGeneration()`,
   used internally but its public check methods are themselves dead code (`index.ts` calls
   `chemistryCache.update()` and discards the returned generation at `index.ts:1757,1766` —
   `isValid`/`currentGeneration` have zero callers anywhere in `src/`).
3. `field-wells/index.ts` — no counter at all; identity is the *molfile string itself*
   (`molfile !== requestMolfile`), which is semantically closest to `ligand-store`'s
   "no-op on identical molfile+kind" rule (`ligand-store.ts:200–210`) but arrived at
   independently.

None of the three know about each other. **WHY IT BITES:** three different definitions of
"the same molecule" (a monotonic int, a monotonic int with a different owner, and string
equality) mean three different edge cases — e.g., navigating A→B→A: `ligand-store` and
`chemistry-cache` would both treat the second A as change #2 relative to A (new generation,
correct, since a generation bump is about *time*, not content-identity, per
`ligand-store.ts:200–206`'s own no-op rule which explicitly checks *content* too) while
field-wells' bare string compare treats returning to byte-identical A as "no change" and skips
re-invalidation — which is actually the same behavior `ligand-store.commit()` chose on purpose.
They happen to agree today; nothing enforces that they will keep agreeing if either is edited
in isolation. CONFIDENCE: CONFIRMED for the existence of three separate implementations;
D5/speculative for the A→B→A edge-case framing above (reasoned from the code, not executed).

### 4.4 What is *not* duplicated (for calibration)

`pharmacophore-features.ts` correctly reuses `ChemistryCache`'s `chemistry.acceptors[i]` /
`chemistry.donors[i]` / `chemistry.aromaticAtoms` (`pharmacophore-features.ts:104,117,149`)
rather than recomputing HBA/HBD/aromaticity itself — the S0 chemistry-substrate consolidation
did work as intended for that fact. Not every fact in this codebase has multiple homes; the
ones above were the ones found to.

---

## 5 · Performance-shaped findings (structural only)

- **`index.ts` re-parses the counts line by hand at least twice per SMILES render**
  (`index.ts:1030`, `1756`) instead of calling a shared parser — cheap in isolation (one
  `split('\n')[3]` per call) but a second, independent site that would need to be kept in sync
  with any future fix to the counts-line format (the exact class of drift documented in §4.1).
  Not reported as "slow" — reported as a *third* uncoordinated reader of a format one sibling
  module has already shown is easy to misparse.
- **`applySemanticLayers()` (`index.ts:1715–1745`) always calls `updateChemistryCache()`
  (`:1731`) before every layer toggle**, including layer toggles that touch neither chemistry
  nor the ligand (e.g., toggling a purely structural or VFX layer walks the same
  `enabledUpgrades` set through `applyStructuralSemanticLayers`/`applyChemicalSemanticLayers`/
  etc. — 9 sequential `await` calls per toggle, `index.ts:1733–1741`). `ChemistryCache.update()`
  itself is a no-op re-fetch only if the molfile is unchanged from `this.data`? — checked: **it
  is not** — `update()` (`chemistry-cache.ts:55`) unconditionally re-runs all three RDKit calls
  (`computeLigandChemistry`, `computeLigandDescriptors`, `computeLigandIdentifiers`) in
  parallel every time it is called, with no early-return for "molfile is identical to
  `this.data.molfile`". CONFIDENCE: CONFIRMED by reading `update()`'s body — there is no
  molfile-equality guard before the `Promise.all`, only the post-hoc generation-mismatch
  discard. **WHY IT BITES:** toggling a checkbox that has nothing to do with chemistry (e.g. a
  VFX lighting layer) re-runs 3 RDKit-WASM calls on every single toggle, structurally, not
  hypothetically — the mechanism is the missing guard, not a measured wall-clock number.

No other structural cost claims are made; nothing here was measured with a profiler, and
per this audit's own instructions, "might be slow" without a named mechanism is excluded.

---

## Ordered remediation sequence — cheapest first, none implemented

1. **Delete `heavyAtomsFromMolfile` from `ligand-store.ts` and import `molfileHeavyAtoms`'s
   fixed logic (or the RDKit `numHeavyAtoms`) instead**, before anything else touches this
   file. *Files: `src/app/services/ligand-store.ts`.* Cheapest because it fixes a bug that
   would otherwise ship the moment adoption starts, and touches nothing else.
2. **Fix the failing test and the 4 ESLint errors** so `npm test` is green on this file before
   it becomes load-bearing. *Files: `src/app/services/ligand-store.ts`,
   `src/app/services/_spec/ligand-store.spec.ts`.*
3. **Reconcile `contracts/iface.d.ts`'s `LigandStore` interface with the shipped class** (pick
   one shape for `setFromImport`/`setFromLoci`, update the other) so there is a single target
   to wire toward. *Files: `contracts/iface.d.ts`, `src/app/services/ligand-store.ts`.*
4. **Finish the `this.smilesMolfile` staleness fix (§2.2) independently of the store.** Commit
   `4f3fe20` already fixed the `importMolecule()` half mid-audit; `loadMolecule()`
   (`index.ts:1585–1598`) is the residual gap — it still never clears `this.smilesMolfile`, so
   picking a new structure from the `#molecule` dropdown after any prior SMILES paste or
   import still fans out the stale molecule. One line (`this.smilesMolfile = null;` at the top
   of `loadMolecule`) closes it; this does not require LigandStore adoption and is the single
   highest-value change left in this list relative to its cost. *Files:
   `src/app.frontend.facets.molstar-rdkit.editable/index.ts` (`loadMolecule`).*
5. **Add the missing post-await currency check to `ligand-physics.runSurface`/`runTorsion`**
   (capture `requestMolfile`, compare after `await post(...)`, mirroring field-wells' existing
   pattern at `field-wells/index.ts:675–676`) — closes the one path in this audit that reaches
   a real multi-second backend SCF call with zero protection today, and now, per §3's live
   callout, closes the new `publishSurfaceResult`/`onSurfaceResult` cross-facet channel
   (`ligand-physics/index.ts:195–206,316`) at the same time — one fix, two exposures, because
   the second one was added on top of the first without a guard while this audit was running.
   *Files: `src/app.frontend.facets.molstar-rdkit.editable/facets/ligand-physics/index.ts`.*
6. **Wire `LigandStore` itself**: replace `index.ts`'s two fields
   (`smilesMolfile`, `smartsSearchMolfile`) and `chemistryCache`'s ad hoc generation, and each
   facet's private `molfile`/`ligandLabel`/`state.atlas`, with `ligandStore.subscribe(...)`,
   deleting `ChemistryCache`'s now-redundant generation counter (keep it only as a *cache*, not
   a staleness authority) and field-wells' bespoke molfile-identity check. This is the
   expensive item because it touches all eight modules in the scoreboard and is exactly the
   "adoption is a separate commit that deletes the three fields" the file's own docstring
   deferred. *Files: `src/app.frontend.facets.molstar-rdkit.editable/index.ts` and all six
   `facets/*/index.ts`, `src/chemistry.backend.perception.rdkit-wasm.editable/chemistry-cache.ts`.*
7. **Only after (6): fold the FieldMeta contract gap (21 missing keys,
   `field-wells/index.ts:147–172`, listed by `node scripts/check_contract_drift.mjs`) into the
   same pass**, since both are "frontend interfaces that have drifted from what the backend/DB
   half of this system actually provides," and touching field-wells' types once for both is
   cheaper than twice.
