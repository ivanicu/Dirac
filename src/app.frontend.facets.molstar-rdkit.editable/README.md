# Dirac lab facet — `dirac`

A browser-native, **chemistry-aware** molecular visualization workbench built on [mol*](https://github.com/molstar/molstar). It pairs mol*'s 3D structural engine with an embedded [RDKit-JS](https://github.com/rdkit/rdkit-js) (WASM) chemistry perception layer, exposing both through a curated master-detail UI.

The lab is the reference implementation of a single thesis:

> A ligand in a deposited structure carries far more information than any one visualization channel can show. The lab surfaces the most chemically meaningful subsets — atom-level chemistry, recognition features, geometry, experimental confidence — as **orthogonal, toggleable overlays** that never replace the underlying mol* geometry.

## What's in the box

### Three Sprint deliverables

| Sprint | Surface area | Files |
|---|---|---|
| **UI redesign** | Topbar + 380px sidebar + 4 master-tabs (Focus / Semantics / Ligand / VFX / Ledger) + sticky diagnostics. Replaced the chaotic single-pane scroll with master-detail navigation. | `index.html`, `index.ts` |
| **Atomic chemistry (RDKit)** | Aromaticity, Lipinski H-bond donor/acceptor, Gasteiger partial charge (gracefully unavailable when the WASM build lacks the API). Recolor-only Overpaint on ligand atoms. | `mol-plugin-chem/semantic-chemistry-rdkit.ts` |
| **2D ligand panel + 3D pharmacophore** | Publication-quality 2D SVG via `mol.get_svg_with_highlights`, with bidirectional atom selection sync (click 2D atom → mol* selects the 3D atom). 3D pharmacophore primitives: HBA cones, HBD sticks, aromatic disks, hydrophobic halos. | `mol-plugin-chem/ligand-depiction.ts`, `mol-plugin-chem/pharmacophore-features.ts` |

### Visual channel allocation

Every overlay is assigned a channel that doesn't conflict with others:

| Information | Channel |
|---|---|
| Element identity | Atom CPK color (default) |
| Bond order | Bond visual style (single/double/triple parallel lines) — *deferred* |
| Formal charge | Atom text label |
| Partial charge | Atomic color gradient (mutually exclusive with CPK) |
| Donor / acceptor | Atom halo + 2D highlight color |
| Aromaticity | Ring color + 3D ring disk |
| Hydrophobicity | 3D hazy halo (separate from atom color) |
| Experimental vs predicted | Source label in the availability badge |

## Architecture

```
mol* Structure ──┬── mol*'s MolEncoder pattern ──► V2000 molfile
                 │       (ComponentBond gives bond orders)
                 │
                 └──► RDKit-JS (WASM, 7MB)
                         ├── get_qmol + get_substruct_matches → SMARTS flags
                         ├── get_svg_with_highlights → 2D SVG
                         └── (compute_gasteiger_charges — not in current WASM build)
                                               
2D SVG ──(bond-path centroid fallback)──► atom-position table ──► click → mol* Loci
3D pharmacophore ──► mol* Shape (MeshBuilder) ──► ShapeRepresentation3D state node
```

The atom-index contract is the load-bearing seam: ligand loci iteration order is preserved through molfile construction, RDKit parsing, SMARTS predicates, 2D SVG generation, and 3D click-back selection. Every step uses the same walker.

## Run it

The lab is built as a mol* example. From the repo root:

```bash
npm install
node ./scripts/build.mjs -a dirac --prd    # one-shot production build
node_modules/.bin/http-server build/dirac -p 1338 -g
# Open http://localhost:1338/
```

The first page load pulls `RDKit_minimal.wasm` (~7 MB) from `./assets/rdkit/`. The WASM is cached by the browser afterwards.

## Try the demos

| Structure | What to enable | What you'll see |
|---|---|---|
| **1CBS** (retinoic-acid binding protein) | Focus → Ligand target = `REA · A:200`, then Semantics → RDKit → **H-bond donor / acceptor** | 1 HBD + 2 HBA overlaid on the carboxylate. Switch to Ligand tab to see the 2D depiction with the same atoms highlighted. |
| **4HHB** (hemoglobin) | Focus → `HEM · A:142`, then Semantics → Pharmacophore → **Pharmacophore features · 3D** | 8 HBA cones, 2 HBD sticks, 2 aromatic disks over pyrrole rings, 24 hydrophobic halos. |
| **1EMA** (GFP) | Semantics → Focus → Ligand focus target (chromophore) | 2D depiction + 3D Overpaint of the chromophore chemistry. |

## What's deliberately not here

- **MMFF / OPLS ligand strain**: RDKit-JS does not expose force-field APIs in the 2025.03.4 build. Implemented as a clean "unavailable" badge, not silently missing.
- **Trajectory variance envelope**: would require ensemble data and custom geometry; deferred.
- **APBS electrostatics**: would require an external service; deferred.
- **3D bond-order double-line rendering**: would require a custom `ShapeRepresentation` consuming CCD `ComponentBond.order` data; the data is available, the renderer is not yet written. The 2D RDKit depiction shows proper bond orders today.
- **3D→2D selection sync** (clicking in 3D highlights in 2D): the data path is built (`ligandDepictionAtomPositions` is populated), the wire is not. Easy to add in a follow-up.

## Honest limitations

- **Headless browser verification only.** WebGL works in puppeteer with `--use-gl=angle --use-angle=swiftshader`, but real-world visual fidelity must be checked in a real browser. The DOM-level smoke tests confirm: layer wiring, click handlers, RDKit availability counts, feature counts, master-tab navigation, and SVG rendering.
- **Single-residue ligand assumption.** The molfile generator and atom-index walker assume one `LigandFocusTarget` bundle = one residue. Covalent multi-residue ligands and UNL entries are not supported.
- **CCD dependency.** `mol*` `MolEncoder` requires `ComponentBond` data from the Chemical Component Dictionary. Novel ligands not in CCD will not get a molfile, and the chemistry layers will report "RDKit cannot parse this ligand" rather than silently failing.

## File map

```
src/app.frontend.facets.molstar-rdkit.editable/
├── index.html        — topbar + sidebar + Ligand master-tab markup, all design tokens
├── index.ts          — lab orchestration; wires RDKit layers + pharmacophore + 2D panel
├── typings.d.ts      — WASM module + RDKit global declarations
└── assets/
    ├── rdkit/        — vendored RDKit_minimal.js + .wasm
    └── structures/   — fixture .cif files

src/chemistry.backend.perception.rdkit-wasm.editable/
├── semantic-chemistry-rdkit.ts  — RDKit singleton, molfile builder, SMARTS, Overpaint applier
├── ligand-depiction.ts          — 2D SVG with highlights + click-to-atom (no mol* deps)
└── pharmacophore-features.ts    — 3D HBA/HBD/aromatic/hydrophobic primitives via mol* Shape
```

## License

Inherits the mol* MIT license. RDKit-JS is BSD-3-Clause.
