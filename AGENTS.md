# Dirac — Agent Operating Guide

> **Single-tree model.** All workstreams live in `main` together. Dirac is ONE integrated product with multiple facets, not three products that might merge someday. Agents work in different directories of the same tree, not in different branches.

This file is the **source of truth for any agent** working in this repo. Read it before committing.

> ## ⚠ ONE DEV SERVER: PORT 1360. DO NOT START ANOTHER.
> `http://192.168.1.3:1360/` serves `build/dirac` on `0.0.0.0`. Rebuild with
> `npm run build:dirac` and reload — do not spin up a second server on a second
> port, not even for one screenshot. **Rules and the measured reason they exist:
> `CLAUDE.md` in this directory.** (Short version: fifteen servers were running
> against this repo at once, one of them left over from an earlier session, and
> two rounds of verification screenshots turned out to be of a stale build.)

## Repository topology

```
origin   → github.com/ivanicu/Dirac.git     (canonical, the only remote that matters)
upstream → github.com/molstar/molstar.git   (reference only, cherry-pick incoming)
```

There is one branch that anyone develops against: **`main`**.

| Branch | Purpose |
|---|---|
| `main` | Canonical integrated state. The whole product, all facets, all the time. |
| `wip/<topic>` | Short-lived (< 1 day) branches for individual changes too big for a single commit. Must merge or delete within 24 hours. |
| `master` | Read-only local pointer to upstream mol\*. Never pushed to `origin`. Used only for `git cherry-pick master` to absorb mol\* fixes. |

**Forbidden:** long-lived per-feature branches. If you need to develop in isolation for more than a day, you are doing two things at once — split the work into smaller commits against `main`.

## Workstream ownership (by directory, not by branch)

Three facets of Dirac are developed in parallel. Each lives in its own directory under `src/examples.reference.mini-demos.vendored-readonly/`. Conflicts are prevented by directory isolation, not branch isolation.

| Facet | Owner scope | Read-only for others |
|---|---|---|
| **mn-compiler-lab** (existing baseline + RDKit + 2D ligand + 3D pharmacophore) | `src/app.frontend.facets.molstar-rdkit.editable/**` | Everyone reads this for the chemistry substrate. |
| **Pharmacophore Designer** | `src/examples.reference.mini-demos.vendored-readonly/pharmacophore-designer/**` | Other agents do not commit here. |
| **Conformer Explorer** | `src/examples.reference.mini-demos.vendored-readonly/conformer-explorer/**` | Other agents do not commit here. |
| **Property Optimization Cockpit** | `src/examples.reference.mini-demos.vendored-readonly/property-cockpit/**` | Other agents do not commit here. |
| **Field Wells** | `src/app.frontend.facets.molstar-rdkit.editable/facets/field-wells/**` + `backend/field_server.py` + `backend/env/` + `backend/README.md` | Other agents do not commit here. Backend daemon: `backend/env/bin/python backend/field_server.py` (:8901). `backend/db/` belongs to the database workstream, not to Field Wells. |

**Shared substrate** (modifications need explicit coordination via GitHub issue before push):

- `src/chemistry.backend.perception.rdkit-wasm.editable/semantic-chemistry-rdkit.ts` — RDKit singleton + SMARTS + LigandChemistry contract
- `src/chemistry.backend.perception.rdkit-wasm.editable/ligand-depiction.ts` — 2D SVG with click sync
- `src/chemistry.backend.perception.rdkit-wasm.editable/pharmacophore-features.ts` — 3D pharmacophore primitives
- `package.json` and `package-lock.json` — adding deps requires a coordination issue
- `AGENTS.md`, root `README.md`, `CHANGELOG.md` — docs

If you need to change a shared file, open a `[coord]` issue first.

**Multi-session worktree discipline** (learned 2026-08-10, three sessions on one tree):
1. `git pull --rebase` before editing a shared file, and again immediately before committing.
2. Shared files take **anchored incremental edits only** — never whole-file rewrites from your session's stale context. A stale-context rewrite has already (a) erased two sessions' uncommitted hunks, (b) reverted a committed one-line fix, and (c) swallowed another session's uncommitted hunk into an unrelated commit.
3. Commit shared-file hunks within minutes of editing. The measured half-life of an uncommitted shared-file hunk on this tree is ~10 minutes.
4. Surgical staging (hash-object / update-index) must assert both "anchor count == 1" **and** "payload count == 0" — the second check is the one that prevents double-injection.
5. The V2000 counts line in the molfile builders is spec-exact (8 zero fields + `999`); desktop RDKit rejects the 9-field variant. If it regresses a third time, extract a single shared molfile-writer.
6. **Never `git reset --hard` / `git clean -fd` on this shared worktree.** A sync is `git pull --rebase` (local commits replay on top), never a reset to `origin/main` (local commits are orphaned — one commit already had to be recovered from a backup branch, and `clean -fd` has deleted two sessions' uncommitted files). Push promptly after committing: on this tree an unpushed commit is as mortal as an uncommitted hunk.

## Pre-flight checklist (every agent, every session)

```bash
git checkout main && git pull origin main         # start from latest canonical
npm ci                                             # only if package*.json changed
node ./scripts/build.mjs -a dirac --prd  # verify baseline still builds
git log --oneline main -10                         # know what changed recently
```

## Daily workflow

1. `git pull origin main` before each session.
2. Develop in your facet directory. Commit to `main` directly when the change builds and is logically atomic.
3. For changes that take more than a few hours, use a `wip/<topic>` branch and merge into `main` as soon as it builds. Don't accumulate.
4. Push to `origin main` after each logical unit of work. Don't batch.
5. If you touched shared substrate (`src/chemistry.backend.perception.rdkit-wasm.editable/`, `package.json`), open a `[coord]` issue describing the change after pushing.

## Verification gate (before push to main)

```bash
# 1. TypeScript clean
node_modules/.bin/tsc --noEmit -p tsconfig.json

# 2. Your facet builds
node ./scripts/build.mjs -a dirac --prd

# 3. Baseline lab still builds (regression check)
node ./scripts/build.mjs -a dirac --prd

# 4. (If you touched mol-plugin-chem/*)
npm run test:chem-packs
```

If any step fails, fix before pushing. Do not push red.

## Conflict resolution

Because facets are isolated by directory, real conflicts should be rare. When they happen:

1. **Shared file conflict** (someone else changed `semantic-chemistry-rdkit.ts` while you did): rebase on latest `main`, re-test, push.
2. **Semantic conflict** (your change depends on an assumption that someone else invalidated): open a `[coord]` issue, talk it out, then commit a fix.
3. **Build break on main**: the agent who broke it owns the fix. Others can `git revert` if the breaker is offline.

## Commit message convention

Use neural commit format:

```
[type.facet.impact.Dx{valence}] WHY in one line

(optional) Body explaining WHY, not WHAT. diff already shows what.
```

- `type`: `feat`/`fix`/`docs`/`meta`/`reflex`/`guard`/`verify`
- `facet`: `lab`/`pharmacophore`/`conformer`/`property`/`rdkit`/`infra`/`docs`
- `impact`: `mu` (micro) / `lambda` (local) / `rho` (regional) / `sigma` (system) / `Omega` (paradigm)
- `Dx`: D0-D9 confidence
- `valence`: `+` positive / `-` negative / `~` neutral / `!` breaking

Examples:
- `[feat.pharmacophore.sigma.D7+] drag-editable HBA cone with mouse`
- `[fix.rdkit.lambda.D7+] exclude positive charges from acceptor SMARTS`
- `[docs.lab.mu.D8~] clarify pharmacophore rendering in README`

## Build and run

```bash
# Baseline (existing shipped facet)
node ./scripts/build.mjs -a dirac --prd
node_modules/.bin/http-server build/examples/mn-compiler-lab -p 1338 -g
# open http://localhost:1338/

# Each new facet uses the same pattern with its own -a dirac
```

## RDKit-JS notes (critical)

- Vendored at `src/app.frontend.facets.molstar-rdkit.editable/assets/rdkit/RDKit_minimal.{js,wasm}` (7 MB).
- All facets **reuse the same vendored WASM**. Do not duplicate it per-facet.
- Loaded via `<script>` tag in each facet's `index.html`. Exposes `window.initRDKitModule`.
- **2025.03.4 build limitations (verified):**
  - `compute_gasteiger_charges` is NOT exposed (partial-charge layer is a stub).
  - `forceCoords: true` in `get_svg_with_highlights` does NOT regenerate 2D coords. Use `get_new_coords()` + re-parse instead.
  - `<metadata>` block is NOT emitted. Use bond-path centroid fallback for atom click mapping.
  - Retrosynthesis, force fields, OPLS — not available.

If you need any of the missing APIs, escalate to a Python backend; do not fake it in JS.

## What NOT to commit

- `node_modules/`, `build/`, `*.tsbuildinfo`
- Local credentials, API keys, browser login state
- PDB structure files > 5 MB — use Git LFS or external hosting
- Per-facet copies of the RDKit WASM (reuse the existing one)

## Source-of-truth docs

- `README.md` — Dirac product overview, what facets exist, how to run
- `CHANGELOG.md` — what's new in each Dirac version
- `AGENTS.md` (this file) — agent operating rules
- `src/examples.reference.mini-demos.vendored-readonly/<facet>/README.md` — per-facet design notes, written by the agent owning that facet

## Issue protocol (for cross-facet coordination)

When your work affects another facet's scope:
1. Open a GitHub issue titled `[coord] <topic>` describing the proposed change.
2. Reference the affected facet directories.
3. Proceed with the commit but mention the issue in the commit body.
4. The other facet's agent reads open `[coord]` issues at session start.
