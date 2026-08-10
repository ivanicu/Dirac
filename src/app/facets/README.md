# Dirac facets

Dirac is ONE app. Its capabilities are organized as **facets** — pluggable
panels that share the same mol\* scene, the same RDKit-JS session, and the
same focused ligand. Each facet owns a sub-directory here and registers itself
with the app shell via a master-tab.

## Layout

```
src/app/
├── index.html                  # app shell, all master-tabs
├── index.ts                    # router, mounts every facet
├── facets/
│   ├── README.md               # this file
│   ├── pharmacophore-designer/ # NEW: drag-editable features + screening
│   ├── conformer-explorer/     # NEW: ETKDG conformers + morph + landscape
│   ├── property-cockpit/       # NEW: Lipinski/Veber descriptor dashboard
│   └── (lab facet lives in the parent index.ts as the existing baseline)
├── assets/
│   ├── rdkit/                  # SHARED RDKit-JS WASM, do not duplicate
│   └── structures/             # molecular fixtures
└── README.md
```

## Owner rules (per AGENTS.md)

| Directory | Owner | Edits by others |
|---|---|---|
| `facets/pharmacophore-designer/` | Pharmacophore agent | Read-only |
| `facets/conformer-explorer/` | Conformer agent | Read-only |
| `facets/property-cockpit/` | Property agent | Read-only |
| `assets/rdkit/` | Nobody (vendored, do not modify) | — |
| `assets/structures/` | Lab agent (existing) | Coord via issue |
| `index.html`, `index.ts` (shell) | Lead agent | Coord via issue |

## Facet registration contract (TBD)

Each facet module should export a function with a stable signature that the
shell calls to register it. Exact shape to be defined when the first new facet
ships; the existing lab facet is currently inlined in `../index.ts` and will
be refactored when the second facet arrives.

## Shared substrate (in src/chemistry/, not here)

Facets DO NOT own chemistry — they consume the shared substrate at
`src/chemistry/`:

- `semantic-chemistry-rdkit.ts` — RDKit singleton, molfile builder, SMARTS
- `ligand-depiction.ts` — 2D SVG with click sync
- `pharmacophore-features.ts` — 3D pharmacophore primitives

Changes to substrate files require a `[coord]` GitHub issue first.
