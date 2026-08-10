# Vendored code in `src/`

Most of `src/` is **vendored from [mol\*](https://github.com/molstar/molstar)**. The vendored code is the 3D engine; Dirac sits on top of it. **Do not modify vendored code.**

## How to know what's vendored

Anything in `src/` that starts with `mol-` is vendored. The full list:

- `src/mol-app/`, `src/mol-canvas3d/`, `src/mol-geo/`, `src/mol-gl/`, `src/mol-io/`, `src/mol-math/`, `src/mol-model/`, `src/mol-model-formats/`, `src/mol-model-props/`, `src/mol-monorepo*/`, `src/mol-plugin/`, `src/mol-plugin-state/`, `src/mol-repr/`, `src/mol-state/`, `src/mol-task/`, `src/mol-theme/`, `src/mol-util/`, `src/mol-webgl/`

Other vendored locations:

- `src/apps/{viewer,docking-viewer,mesoscale-explorer,mvs-stories,kernel-interface}/` — mol\*'s own apps, reference only
- `src/examples/` — mol\*'s own example demos, reference only (Dirac is NOT here)
- `examples/` (top-level) — fixture data (CIF, PDB files) used by mol\*'s examples and by Dirac

## What's ours (editable)

- `src/app.frontend.facets.molstar-rdkit.editable/` — the Dirac application (UI, facets, assets)
- `src/chemistry.backend.perception.rdkit-wasm.editable/` — Dirac chemistry substrate (RDKit, depiction, pharmacophore)

If a directory is not in the two lists above and not prefixed with `mol-`, ask before modifying.

## Why this matters

mol\* uses **relative imports** (`'../mol-util/produce'`) with no tsconfig path mapping. Moving or renaming `mol-*` directories would break thousands of internal imports. The vendored boundary is therefore physical, not just conventional — touching vendored code makes future upstream sync expensive.

## How to absorb upstream changes

```bash
# One-time setup if not already done
git remote add upstream https://github.com/molstar/molstar.git
git fetch upstream master:master-upstream  # local-only tracking branch

# Per fix
git checkout master-upstream
git pull
git checkout main
git cherry-pick <commit-sha>    # bring a specific fix into Dirac
# resolve conflicts in our code (never modify vendored to make cherry-pick easier)
npm run build:dirac              # verify
git push origin main
```

## When you really need to modify vendored code

You almost certainly don't. If you believe you do:

1. Open a `[vendored-touch]` issue explaining why our code can't be the place for the change.
2. Wait for at least one other agent to agree.
3. Make the smallest possible patch.
4. Note in the commit message: *"vendored patch — will need re-application after upstream sync"*.
5. Track it in `docs/vendored-patches.md` (create if it doesn't exist).

The default answer is no.
