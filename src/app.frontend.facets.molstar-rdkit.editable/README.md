# Dirac browser application

This directory contains the integrated browser projection of Dirac. It combines one
persistent mol\* scene, RDKit-JS chemistry and composable scientific modules inside the
8-Workspace / 30-View AppShell.

It is no longer a standalone “lab facet.” The historical Focus, Ligand, Semantics,
Properties, Fields, Physics, Designer, VFX, Ledger and Runs surfaces are assigned to
registry-owned modules and Views in `src/app/shell/registries.ts`.

## Main boundaries

| Path | Responsibility |
|---|---|
| `index.ts` | application composition and integration with the molecular workbench |
| `index.html` | product shell markup and styles |
| `facets/` | scientific UI modules over shared context and scene state |
| `assets/rdkit/` | vendored RDKit-JS runtime used for in-browser perception |
| `../app/shell/` | Workspace, View and Module registries plus routing |
| `../app/context/` | shared scientific context and staleness generation |
| `../app/scene/` | the single persistent mol\* SceneService |
| `../app/client/` | semantic application client over the Command boundary |

The atom-index contract remains load-bearing: ligand loci order is preserved through
molfile construction, RDKit parsing, SMARTS predicates, 2D SVG generation and 3D
selection.

## Build and view

From the repository root:

```bash
npm ci
npm run build:dirac
```

On the canonical workstation, reload the existing supervised site at
<http://localhost:1360/>. Do not start another server. On a clean checkout without the
supervised unit, follow the root [`README.md`](../../README.md) command, which also uses
1360.

## Capability boundary

Browser-only operation supports bundled structures, mol\* interaction and RDKit-JS
features. Program state, server-side Methods, durable Jobs and Artifacts require the
application service on 8901. A navigable View with `delivery: 'shell'` is an interface
contract, not a shipped scientific workflow.

## Known substrate constraints

- The vendored RDKit-JS build does not expose every desktop RDKit API; unavailable
  functions must be reported explicitly or routed through a declared backend Method.
- Molfile/selection logic assumes one focused ligand bundle; covalent multi-residue
  ligands need a separate identity and mapping design.
- CCD bond information is required for deposited-ligand bond orders.

See the root [README](../../README.md), [construction status](../../STATUS.md), and
[architecture](../../ARCHITECTURE.md) for product-wide guidance.
