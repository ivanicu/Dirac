# Dirac — Agent Operating Guide

> **Mission:** A browser-native single-molecule deep design workbench built on mol* + RDKit-JS. Three product workstreams operate in parallel on top of a shared chemistry-aware substrate.

This file is the **source of truth for any agent** working in this repo. Read it before opening a branch.

## Repo topology

```
origin    → github.com/molstar/molstar.git   (UPSTREAM, read-only sync)
dirac     → github.com/ivanicu/Dirac.git     (OUR canonical remote)
```

## Branch model

| Branch | Owner | Purpose |
|---|---|---|
| `master` | nobody (sync only) | Tracks upstream mol\* master. Never commit here. Use `git pull upstream master` to absorb mol\* releases. |
| `main` | lead agent | Our stable integration branch. All PRs merge here. Must always build + run. |
| `feature/<product>` | one agent each | Long-lived product workstreams. See "Workstreams" below. |
| `feature/<topic>` | any agent | Short-lived topic branches for cross-cutting work (bug fixes, refactors). |
| `docs/<topic>` | any agent | Documentation-only branches. No code changes. |

**Forbidden:** direct push to `main` or `master`. All changes via PR.

## Workstreams (the 3 product directions)

Each workstream owns a directory under `src/examples/<product-name>/`. Cross-workstream coordination happens through `src/mol-plugin-chem/` (shared chemistry substrate) via PR review.

| Branch | Owns | May modify with PR | May NOT touch |
|---|---|---|---|
| `feature/pharmacophore-designer` | `src/examples/pharmacophore-designer/**` | `src/mol-plugin-chem/pharmacophore-features.ts`, `src/mol-plugin-chem/ligand-depiction.ts` | Other workstreams' `src/examples/<other>/` |
| `feature/conformer-explorer` | `src/examples/conformer-explorer/**` | `src/mol-plugin-chem/semantic-chemistry-rdkit.ts` (extend conformer API) | Other workstreams' dirs |
| `feature/property-cockpit` | `src/examples/property-cockpit/**` | `src/mol-plugin-chem/semantic-chemistry-rdkit.ts` (descriptor accessors) | Other workstreams' dirs |
| `feature/lab-*` (existing) | `src/examples/mn-compiler-lab/**` | All `src/mol-plugin-chem/**` | Other workstreams' dirs |

**Conflict rule:** if you need to change `src/mol-plugin-chem/semantic-chemistry-rdkit.ts` and another agent also needs to, coordinate via GitHub issue before pushing.

## Pre-flight checklist (every agent, every session)

1. `git checkout main && git pull dirac main` — start from latest stable
2. `git checkout -b feature/<your-branch>` — create or reuse your branch
3. `npm ci` — sync deps (only if `package*.json` changed on main)
4. `node ./scripts/build.mjs -e mn-compiler-lab --prd` — verify baseline still builds
5. Read the latest `git log --oneline main -10` to know what changed recently

## Verification gate (before any PR)

A PR is mergeable only if ALL of these pass:

```bash
# 1. TypeScript clean
node_modules/.bin/tsc --noEmit -p tsconfig.json

# 2. Lab example builds
node ./scripts/build.mjs -e mn-compiler-lab --prd

# 3. (If you touched mol-plugin-chem/*) chem packs test
npm run test:chem-packs

# 4. (If you touched RDKit integration) smoke-test in browser
#    Load 1CBS and 4HHB, verify donor/acceptor + pharmacophore counts
#    match expected (see scripts/brutal-test*.mjs in /tmp or docs)
```

## Commit message convention

Use neural commit format from CLAUDE.md:

```
[type.region.impact.Dx{valence}] WHY in one line

(optional) Body explaining WHY, not WHAT. diff already shows what.
```

- `type`: `sense`/`think`/`act`/`fix`/`guard`/`memory`/`prune`/`reflex`/`predict`/`verify`/`feat`/`meta`/`docs`
- `region`: `lab`/`rdkit`/`pharmacophore`/`conformer`/`property`/`infra`/etc.
- `impact`: `mu` (micro) / `lambda` (local) / `rho` (regional) / `sigma` (system) / `Omega` (paradigm)
- `Dx`: D0-D9 confidence
- `valence`: `+` positive / `-` negative / `~` neutral / `!` breaking

Examples:
- `[feat.pharmacophore.sigma.D7+] editable HBA cone with mouse drag`
- `[fix.rdkit.lambda.D7+] exclude positively charged atoms from acceptor SMARTS`
- `[docs.lab.mu.D8~] clarify pharmacophore feature rendering in README`

## Build and run

```bash
# One-shot production build of the lab
node ./scripts/build.mjs -e mn-compiler-lab --prd

# Serve
node_modules/.bin/http-server build/examples/mn-compiler-lab -p 1338 -g
# Open http://localhost:1338/
```

Other examples (when added by feature branches): same pattern with `-e <example-name>`.

## RDKit-JS notes (critical)

- Vendored at `src/examples/mn-compiler-lab/assets/rdkit/RDKit_minimal.{js,wasm}` (7MB)
- Loaded via `<script>` tag in `index.html` — exposes `window.initRDKitModule`
- **2025.03.4 build limitations (verified):**
  - `compute_gasteiger_charges` is NOT exposed (partial-charge layer is a stub)
  - `forceCoords: true` in `get_svg_with_highlights` does NOT regenerate 2D coords — use `get_new_coords()` + re-parse instead
  - `<metadata>` block is NOT emitted regardless of `includeMetadata` flag — fall back to bond-path centroid for atom click mapping
  - Retrosynthesis, force fields, OPLS — not available

If you need any of the missing APIs, escalate to a Python backend; do not fake it in JS.

## What NOT to commit

- `node_modules/`
- `build/` (regenerated)
- `*.tsbuildinfo`
- Local credentials, API keys, browser login state (per CLAUDE.md constitution)
- PDB structure files > 5MB — use Git LFS or external hosting

## Source-of-truth docs

- `src/examples/mn-compiler-lab/README.md` — the existing lab (UI + chemistry + 2D + 3D pharmacophore)
- This file (`AGENTS.md`) — agent operating rules
- Per-product README under `src/examples/<product>/README.md` — written by each workstream agent

## Issue protocol (for cross-workstream coordination)

When an agent's work affects another agent's scope:
1. Open a GitHub issue titled `[coord] <topic>` describing the proposed change
2. `@` mention the affected workstream
3. Wait 1 business day (or use PR review as the coordination point)
4. Proceed with PR; reference the issue in the PR description
