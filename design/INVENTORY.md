# Dirac — the complete design inventory

Ivan's ask, verbatim: 「不只是登录页,是所有有可能要用到的:药物化学家、登录、系统后台
administration 等等——这一个东西的设计的所有组件,一整套视觉语言的所有 VI,把所有要设计的
完全列出来。」

Every status below is **measured from the repository**, not estimated. A list written
from imagination is a wish; this one was produced by reading what exists. The commands
are in the footer so any row can be re-checked rather than believed.

**Baseline, measured 2026-08-10:** the chemist's workbench is substantially built —
8 master tabs, 3 facets, 40 semantic layer definitions, 5 physics/QM endpoints. Beyond
it there is **nothing**: authentication, administration, onboarding, billing and email
are zero files. (`auth` appears 1127 times in the source and every one is
`auth_asym_id` / `auth_seq_id`, mmCIF's crystallographic author numbering. A grep for
the word would have reported the opposite.)

Legend: **✔ built** · **◐ partial** (exists, incomplete or unverified) · **✗ absent**

---

## A · Visual identity — 8

| # | item | status | evidence / gap |
|---|---|---|---|
| A1 | Wordmark & logo (app icon, favicon, monochrome, dark/light lockups) | ✗ | `index.html` renders the word "Dirac" in Inter. No mark exists. |
| A2 | Colour system | ✔ | `design/tokens.css`, gated by `check_palette.py`; chroma ceiling 0.106 per DESIGN.md §8 |
| A3 | Type system | ◐ | scale and roles fixed in tokens; **no licensed display face**, and DESIGN.md forbids external font links |
| A4 | Icon language | ✗ | the app currently uses text glyphs (`✕ ⚡ ⚛ ◉`) as icons. No set, no grid, no stroke rule. |
| A5 | Motion language | ◐ | durations and easing are tokens; no choreography spec (what enters, what is instant, what may never animate) |
| A6 | Voice & copy | ◐ | `design/index.html §05` sketches it; the operative rules are scattered across code comments |
| A7 | **The 3D scene as brand** | ◐ | the strongest identity asset Dirac owns and the least specified: field colours now follow tokens, but framing, default camera, lighting and "what a Dirac render looks like" are undefined |
| A8 | Outward assets (README hero, screenshots, social card, deck, paper figures) | ✗ | none |

## B · Foundations — 5

| # | item | status | evidence / gap |
|---|---|---|---|
| B1 | Token source of truth | ◐ | `design/tokens.css` is canonical for new surfaces; the app still carries an inline `:root` block. **Two homes for one fact until P2 migrates.** |
| B2 | Density & spacing | ✔ | 4px base, enforced scale in STYLE.md |
| B3 | Grid / layout system | ◐ | the workbench layout is hard-coded (380px sidebar); no reusable grid for pages that do not exist yet |
| B4 | Accessibility floor | ◐ | contrast now gated per theme; **keyboard traversal, focus rings, screen-reader labels and reduced-motion are unverified** |
| B5 | The nine-state matrix | ◐ | defined in DESIGN.md, demonstrated for primitives in the showcase, not applied to scientific components |

## C · Components

### C1 · Generic atoms — 30

Built and shown in `design/index.html` (**10**): button (5 variants), input, select, checkbox,
radio, slider, status pill, toast, table, progress.

Absent (**20**): modal · drawer · dropdown menu · context menu · tooltip · popover ·
tabs (as a component, not the hard-coded master-nav) · accordion · breadcrumb · pagination ·
segmented control · switch · textarea · file drop zone · search field with typeahead ·
combobox · date field · avatar · badge/counter · empty state · skeleton · split pane ·
resize handle · keyboard-shortcut hint.

### C2 · Scientific components — 32

Built (**11**): field card · colorbar with decoder · descriptor cell · provenance panel ·
long-task panel · 2D depiction with atom highlights · pharmacophore feature row ·
screening hit row · inter-feature distance matrix · contact ledger row · layer toggle
with availability line.

Absent (**21**), each tied to something the backend already returns or the DB already stores:

- **torsion profile plot** (small multiples, observed angle marked) — `/torsion/strain` returns the curves today with no way to see them
- **σ-hole surface legend + extrema table** — `/surface/mep` returns them today
- uncertainty-bearing number (the `±25%` display rule has no component)
- censored-value display (`>` `<` — the DB stores the qualifier and nothing renders it)
- dose-response curve with excluded points
- SAR table with activity cliffs
- compound registry card (registry id, parent, salts, batches)
- batch/purity chip
- assay result row with unit + qualifier + n
- structure/ligand picker
- binding-site residue list
- conformer strip / energy ladder
- superposition control
- basis & method selector with cost prediction (`predicted_scf_seconds` exists, unshown)
- **producer/staleness badge** — `app.v_field_cube_stale` exists and nothing surfaces it
- cache-hit indicator (exact vs coarse key)
- molecule input (SMILES / molfile / PDB fetch / file drop)
- selection & measurement HUD
- render/export panel
- citation & method block for figures
- diff view for two models or two runs

## D · Pages

### D1 · Chemist — 14
✔ workbench (8 tabs). ✗ molecule import · project home · compound page · series/SAR page ·
comparison view · screening run report · saved-workspace gallery · export/figure builder ·
history & audit view · search results · shared read-only link · print/PDF layout · empty
first-run state.

### D2 · Authentication — 9 · **all absent**
sign in · sign up · SSO callback · forgot / reset password · email verification · MFA
challenge · session expired · account locked · invite acceptance.
*Zero files. Nothing in the codebase performs authentication, and the two backends are
unauthenticated endpoints on the LAN — the design question is downstream of a product
decision that has not been made.*

### D3 · Administration — 10 · **all absent**
user & role management · project/tenant admin · audit log viewer · job & queue monitor ·
**producer registry and stale-cache sweep** (the DB models it, `meta.producer` +
`app.v_field_cube_stale`) · toolkit/version registry · library management · assay & unit
vocabulary editor · storage/blob usage · system health (both daemons expose `/health`).

### D4 · Platform periphery — 8 · **all absent**
marketing/landing · docs · changelog · pricing · status page · error pages (404/500) ·
legal · onboarding tour.

### D5 · Email — 4 · **all absent**
verification · password reset · invitation · long-job completion.

## E · Edge and system states — 6

| state | status | note |
|---|---|---|
| offline / backend unreachable | ◐ | fields panel reports online/offline; physics daemon has no UI at all |
| degraded (GPU absent, cartridge absent, ECP missing) | ✗ | the API now reports `ran_on` and `gpu_unavailable_reason`; nothing displays them |
| long task in flight | ◐ | panel exists in the showcase, not wired to the 37 s QM path |
| **cost refusal** | ✗ | the backend refuses with a reasoned message ("673 basis functions, predicted ~297 s"); there is no component to show a *reasoned* refusal, and rendering it as a generic error throws away the reason |
| stale producer | ✗ | modelled in the DB, invisible in the UI |
| empty / first run | ✗ | the app boots into a hard-coded structure |

## F · Delivery — 4
Living showcase (◐ `design/index.html`, served at :1340) · tokens as a consumable
artifact (◐) · Figma or equivalent handoff (✗) · contribution rules for adding a colour
or a component (◐ DESIGN.md).

---

## The honest cut

Enumerated: **~150 items**. Building all of them would be scope theatre — most of D2/D3/D4
is downstream of product decisions nobody has made, and a design system for pages that may
never exist is the most comfortable fake progress available.

What is actually blocked *right now*, in order:

1. **Components for values the backend already returns and nothing can show.** Torsion
   profiles, σ-hole extrema, uncertainty, cost refusals, `ran_on`. Every one of these is a
   computed result with no path to a human — the work is done and unusable.
2. **Molecule input.** The app loads 9 hard-coded fixtures; a chemist cannot look at their
   own compound. This single absence caps every other feature's value at zero for an
   outside user.
3. **B1 — one token home.** The inline `:root` block and `tokens.css` both exist; the copy
   is never the one that gets corrected.
4. **A7 — the 3D scene as brand.** It is the one thing Dirac has that nothing else does,
   and it is unspecified.
5. Everything in D2/D3/D4 waits on the product question: is Dirac a local tool, a hosted
   product, or a portfolio artefact? Designing login screens before that is answered is
   designing for a guess.

Open, and Ivan's call rather than a lint fix: the light theme's accent is `#e5372b` (red)
while the dark theme's is `#7dd3c0` (teal). A theme swap changes the brand hue, which
contradicts STYLE.md's "Single accent".

---

*Re-check any row:*
`grep -oP 'data-jump="\K[^"]+' src/app.frontend.facets.molstar-rdkit.editable/index.html` ·
`ls src/app.frontend.facets.molstar-rdkit.editable/facets/` ·
`grep -ohP "self\.path == '\K[^']+" backend/*.py backend/physics/*.py` ·
`python3 design/check_palette.py`
