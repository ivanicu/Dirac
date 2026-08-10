# Dirac

> **Dirac is to Schrödinger (the company) what the Dirac equation is to the Schrödinger equation: an open-source, browser-native upgrade.**

Schrödinger's Maestro / LiveDesign stack is the commercial state-of-the-art for structure-based molecular design. It is closed, expensive, and desktop-native. **Dirac is the open-source, browser-native answer** — built on the [mol\*](https://github.com/molstar/molstar) 3D engine and the [RDKit-JS](https://github.com/rdkit/rdkit-js) cheminformatics runtime, with every computation happening in the browser via WebAssembly. No license server, no install, no Python backend required for the core workflow.

The name is intentional. In physics, Schrödinger's equation describes matter at non-relativistic energies; Dirac's equation is its upgrade to the relativistic regime, predicting spin, antimatter, and the fine structure of hydrogen. **Dirac the project aims to be that kind of upgrade over Schrödinger the product** — same domain, deeper formulation, open and accessible.

## What Dirac is today

Dirac is **one integrated app** with multiple facets, all sharing the same mol\* scene, the same RDKit-JS session, and the same focused ligand. Facets are organized as sub-directories of `src/app.frontend.facets.molstar-rdkit.editable/facets/`; agents develop them in parallel against the shared substrate in `src/chemistry.backend.perception.rdkit-wasm.editable/`.

| Status | Facet | What it does |
|---|---|---|
| ✅ shipped | **Lab** (in `index.ts`) | 3D structure + RDKit chemistry perception (aromaticity / donor / acceptor / Gasteiger stub) + 2D ligand depiction with click-sync + 3D pharmacophore primitives (HBA cones / HBD sticks / aromatic disks / hydrophobic halos) |
| 🚧 next | **Pharmacophore Designer** (`facets/pharmacophore-designer/`) | Drag-editable pharmacophore features, live SMARTS screening against a ligand library |
| 🚧 next | **Conformer Explorer** (`facets/conformer-explorer/`) | RDKit ETKDG conformer generation, mol\* morph animation, energy landscape |
| 🚧 next | **Property Optimization Cockpit** (`facets/property-cockpit/`) | Lipinski / Veber dashboard driven by `mol.get_descriptors()` |

## Run it

```bash
git clone https://github.com/ivanicu/Dirac.git
cd Dirac
npm ci                                                       # use ci, not install (see AGENTS.md)
node ./scripts/build.mjs -a dirac --prd                       # one-shot production build
node_modules/.bin/http-server build/dirac -p 1338 -g
# open http://localhost:1338/
```

First page load fetches `RDKit_minimal.wasm` (~7 MB) from `src/app.frontend.facets.molstar-rdkit.editable/assets/rdkit/`. Browser-cached afterwards.

## Demo scenes

| Structure | What to enable | What you'll see |
|---|---|---|
| **1CBS** (retinoic-acid binding protein) | Focus → `REA · A:200`, then Semantics → RDKit → **H-bond donor / acceptor** | 1 HBD + 2 HBA on the carboxylate. Switch to Ligand tab for the 2D depiction with the same atoms highlighted. |
| **4HHB** (hemoglobin) | Focus → `HEM · A:142`, then Pharmacophore → **Pharmacophore features · 3D** | 8 HBA cones, 2 HBD sticks, 2 aromatic disks over pyrrole rings, 24 hydrophobic halos. |
| **1CBS** → Ligand tab | Click an atom in the 2D SVG | The 3D viewer selects the same atom + camera focuses on it. |

## Architecture in one paragraph

```
mol* Structure ──► V2000 molfile (via ComponentBond from CCD)
                 │
                 └─► RDKit-JS (WASM, in-browser)
                        ├── get_qmol + get_substruct_matches → SMARTS flags
                        ├── get_new_coords → 2D molfile → SVG (publication depiction)
                        └── (compute_gasteiger_charges — NOT exposed in 2025.03.4 build)

3D pharmacophore ─► mol* Shape (MeshBuilder) ─► ShapeRepresentation3D state node
2D SVG ─► bond-path centroid fallback ─► atom-position table ─► click → mol* Loci
```

The atom-index contract is the load-bearing seam: ligand loci iteration order is preserved through molfile construction, RDKit parsing, SMARTS predicates, 2D SVG generation, and 3D click-back selection. Every layer uses the same walker.

## Visual channel allocation

Dirac treats color, geometry, label, and halo as orthogonal carriers — each piece of chemistry information is assigned exactly one channel so they never compete.

| Information | Channel |
|---|---|
| Element identity | Atom CPK color (default) |
| Bond order | Bond visual style (single/double/triple parallel lines) — *future work* |
| Formal charge | Atom text label |
| Partial charge | Atomic color gradient (mutually exclusive with CPK) — *pending RDKit API* |
| Donor / acceptor | Atom halo + 2D highlight color |
| Aromaticity | Ring color + 3D ring disk |
| Hydrophobicity | 3D hazy halo (separate from atom color) |

## What's deliberately not here

- **MMFF / OPLS ligand strain**: RDKit-JS does not expose force-field APIs in the 2025.03.4 build. Implemented as a clean "unavailable" badge, not silently missing.
- **FEP / ΔΔG**: requires HPC backend; not faked.
- **APBS electrostatics**: requires an external service; deferred.
- **Retrosynthesis**: RDKit-JS does not expose the retrosynthesis API; would need a Python backend.
- **3D bond-order double-line rendering**: data is available (CCD `ComponentBond.order`), the renderer is not yet written. The 2D RDKit depiction shows proper bond orders today.

## Honest limitations

- **Single-residue ligand assumption.** The molfile generator and atom-index walker assume one `LigandFocusTarget` bundle = one residue. Covalent multi-residue ligands and UNL entries are not supported.
- **CCD dependency.** mol*'s `MolEncoder` requires `ComponentBond` data from the Chemical Component Dictionary. Novel ligands not in CCD will report "RDKit cannot parse this ligand" rather than silently failing.
- **Porphyrin aromaticity undercount.** RDKit's default aromaticity model perceives only 2 of the 4 pyrrole rings in HEME. Faithfully reported, not a bug.

## Repository topology

```
origin   → github.com/ivanicu/Dirac.git     (canonical)
master   → tracks upstream mol* locally for cherry-pick (never pushed to origin)
```

Single-tree model: all facets live in `main`. See `AGENTS.md` for directory-based ownership and the `[coord]` issue protocol for shared substrate changes.

## Acknowledgments

Dirac is built on top of [mol\*](https://github.com/molstar/molstar) (MIT) and [RDKit-JS](https://github.com/rdkit/rdkit-js) (BSD-3-Clause). The mol\* engine, semantic-chemistry substrate, and example lab structure are derivative work; please cite mol\* as:

David Sehnal, Sebastian Bittrich, Mandar Deshpande, Radka Svobodová, Karel Berka, Václav Bazgier, Sameer Velankar, Stephen K Burley, Jaroslav Koča, Alexander S Rose: *Mol\* Viewer: modern web app for 3D visualization and analysis of large biomolecular structures*, Nucleic Acids Research, 2021; https://doi.org/10.1093/nar/gkab314.

## License

MIT. See [LICENSE](./LICENSE). The bundled RDKit-WASM is BSD-3-Clause.
