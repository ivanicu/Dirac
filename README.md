# Dirac

**A browser-native, single-molecule deep design workbench** — pairs the mol\* 3D engine with an in-browser RDKit-JS (WASM) chemistry perception layer, surfaced through a curated master-detail UI. The reference implementation of a single thesis: *a ligand in a deposited structure carries far more information than any one visualization channel can show, so the most chemically meaningful subsets must be exposed as orthogonal, toggleable overlays that never replace the underlying mol\* geometry.*

> Named for Paul Dirac — because the project treats chemistry the way Dirac treated mechanics: a single, principled notation in which many phenomena become special cases.

## State of the project

Dirac ships one running product today and has three more in active development on parallel feature branches.

| Status | Product | Branch |
|---|---|---|
| ✅ shipped | **mn-compiler-lab** — 3D + RDKit chemistry + 2D ligand + 3D pharmacophore | `main` (`src/examples/mn-compiler-lab/`) |
| 🚧 in progress | **Pharmacophore Designer** — draggable HBA/HBD/aromatic/hydrophobic features with live SMARTS screening | `feature/pharmacophore-designer` |
| 🚧 in progress | **Conformer Explorer** — RDKit ETKDG conformers, mol\* morph animation, energy landscape | `feature/conformer-explorer` |
| 🚧 in progress | **Property Optimization Cockpit** — Lipinski/Veber dashboard driven by `mol.get_descriptors()` | `feature/property-cockpit` |

## Run the shipped product

```bash
git clone https://github.com/ivanicu/Dirac.git
cd Dirac
npm ci                                                            # use ci, not install (see AGENTS.md)
node ./scripts/build.mjs -e mn-compiler-lab --prd                 # one-shot production build
node_modules/.bin/http-server build/examples/mn-compiler-lab -p 1338 -g
# open http://localhost:1338/
```

First page load fetches `RDKit_minimal.wasm` (~7 MB) from `src/examples/mn-compiler-lab/assets/rdkit/`. Browser-cached afterwards.

## Demo scenes (try these first)

| Structure | Enable | What you'll see |
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

Every overlay is assigned a channel that doesn't conflict with others — the project treats color, geometry, label, and halo as orthogonal carriers.

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
- **Trajectory variance envelope**: would require ensemble data and custom geometry; deferred.
- **APBS electrostatics**: would require an external service; deferred.
- **3D bond-order double-line rendering**: data is available (CCD `ComponentBond.order`), the renderer is not yet written. The 2D RDKit depiction shows proper bond orders today.
- **3D→2D selection sync** (clicking in 3D highlights in 2D): the data path is built (`ligandDepictionAtomPositions` is populated), the wire is not.

## Honest limitations

- **Single-residue ligand assumption.** The molfile generator and atom-index walker assume one `LigandFocusTarget` bundle = one residue. Covalent multi-residue ligands and UNL entries are not supported.
- **CCD dependency.** mol*'s `MolEncoder` requires `ComponentBond` data from the Chemical Component Dictionary. Novel ligands not in CCD will report "RDKit cannot parse this ligand" rather than silently failing.
- **Porphyrin aromaticity undercount.** RDKit's default aromaticity model perceives only 2 of the 4 pyrrole rings in HEME. The chemistry engine faithfully reports what RDKit perceives — not a bug, a known aromaticity-model limitation.

## Repository topology

```
origin   → github.com/molstar/molstar.git   (UPSTREAM, read-only sync via cherry-pick)
origin   → github.com/ivanicu/Dirac.git     (canonical)
```

`master` tracks upstream mol\* for cherry-pick; `main` is the Dirac integration branch. See `AGENTS.md` for branch model, workstream ownership, and PR rules.

## Acknowledgments

Dirac is built on top of [mol\*](https://github.com/molstar/molstar) (MIT) and [RDKit-JS](https://github.com/rdkit/rdkit-js) (BSD-3-Clause). The mol\* engine, semantic-chemistry substrate, and example lab structure are derivative work; please cite mol\* as:

David Sehnal, Sebastian Bittrich, Mandar Deshpande, Radka Svobodová, Karel Berka, Václav Bazgier, Sameer Velankar, Stephen K Burley, Jaroslav Koča, Alexander S Rose: *Mol\* Viewer: modern web app for 3D visualization and analysis of large biomolecular structures*, Nucleic Acids Research, 2021; https://doi.org/10.1093/nar/gkab314.

## License

MIT. See [LICENSE](./LICENSE). The bundled RDKit-WASM is BSD-3-Clause.
