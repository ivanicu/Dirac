# Changelog

All notable changes to **Dirac** are documented here. This project forks from [mol\*](https://github.com/molstar/molstar) at upstream SHA `e594cc6` (mol\* master, August 2026); mol\*'s pre-fork history is not included. When mol\* features are pulled forward they appear here with a `upstream:` tag.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/2.1.0/).

## [Unreleased]

### Added

- **Field Wells facet** (`facets/field-wells/`, new `Fields` master-tab) + **fields backend** (`backend/field_server.py`). 3D energy/potential wells for the focused ligand, rendered as signed x-ray-shaded isosurfaces inside the protein scene: classical electrostatic well (RDKit Gasteiger + Coulomb grid, ~0.1 s), and quantum HOMO / LUMO / electron density / QM electrostatic potential via pyscf HF + cubegen (real SCF; an unconverged SCF returns an error, never a field). The ligand molfile already carries scene coordinates, so every cube lands registered with zero alignment code. Isovalue slider updates the two `VolumeRepresentation3D` nodes in place; SCF results cached per (geometry, basis). Backend is a self-contained stdlib HTTP daemon on `127.0.0.1:8901` with its own conda env (RDKit 2026.03.5 + pyscf 2.14.0, gitignored); the panel reports online/offline honestly. Verified on 1CBS REA: MEP well in the β-barrel pocket 0.12 s; RHF/STO-3G E=−911.9424 Ha, HOMO −5.15 eV, LUMO +4.69 eV, 138 basis functions, 5.27 s first SCF, cached thereafter.
- **V2000 counts-line fix in both hand-rolled molfile builders** (`semantic-chemistry-rdkit.ts`, `pharmacophore-features.ts`): nine zero fields between the bond count and `999` where the spec says eight. RDKit-JS tolerates the malformed line, desktop RDKit rejects it (`CTAB version string invalid`) — invisible until the first spec-strict consumer (the fields backend) refused every molfile.

- **Pharmacophore Designer facet** (`facets/pharmacophore-designer/`, new `Designer` master-tab). Drag-editable pharmacophore model seeded from the focused ligand's RDKit perception (same `computePharmacophoreFeatures` as the read-only 3D layer): per-feature enable/disable, tolerance radius (0.5–3 Å), delete, add free-standing features, camera-plane 3D drag with the trackball suppressed while a feature is armed (hover → grab cursor). Live inter-feature distance matrix. Topological SMARTS screening of a shipped 68-molecule library (probes, fragments, drugs, natural products, steroids) against the model's feature-count requirements plus an optional custom SMARTS constraint; hit rows show per-kind have/required chips and click through to a 2D depiction with per-channel atom highlights. Model export/import as versioned JSON. Screening counts reuse the substrate's `computeLigandChemistry` (identical SMARTS both sides); the only facet-local pattern is the hydrophobic-carbon SMARTS, held in parity with the 3D layer's neighbor rule. Validation gate: `node scripts/check-pharmacophore-library.mjs` (node-side RDKit 2025.03.4 — parse validity, canonical-SMILES dedup, SMARTS parity vs the substrate source, exact probe counts for benzene / cyclohexane / pyridine / caffeine). Screening is topological only — the library ships no conformers and the UI says so. Verified end-to-end via CDP: 1CBS/REA seeds 2 HBA · 1 HBD · 19 HYD and the only library match is retinoic acid itself; drag moves a feature 5.4 Å with zero camera motion; 4HHB/HEM seeds 2 aromatic-ring features from SSSR.

### Pending product workstreams

- **Conformer Explorer** — RDKit ETKDG conformer generation, mol\* morph animation, overlay view, energy landscape. ⚠ The vendored RDKit-JS 2025.03.4 wasm exposes no 3D embedding (`ETKDG` / `EmbedMolecule` absent from the binary) — this workstream needs a custom RDKit-JS build with 3D coords enabled, precomputed conformer data, or the Python backend escalation path.

## [0.1.0] — 2026-08-10

Initial Dirac snapshot consolidating the mn-compiler-lab workbench and its RDKit-JS chemistry substrate.

### Added

- **mn-compiler-lab UI redesign.** Replaced the single-pane scrolling sidebar with a master-detail layout: 64-px top bar (molecule / representation / picking / status / clear), 380-px sidebar with five master-tabs (Focus / Semantics / Ligand / VFX / Ledger), sticky diagnostics strip with 4 always-visible metrics. Cuts vertical overflow from 260 px to 0 on a 900-px viewport.
- **RDKit-JS chemistry integration.** Vendored `@rdkit/rdkit` 2025.3.4-1.0.0 WASM at `src/app.frontend.facets.molstar-rdkit.editable/assets/rdkit/` (7 MB). Singleton loader at `src/chemistry.backend.perception.rdkit-wasm.editable/semantic-chemistry-rdkit.ts` exposes SMARTS-based perception for aromaticity, Lipinski H-bond donor, and Lipinski H-bond acceptor over the focused ligand. Acceptor SMARTS excludes positively charged atoms (no quaternary-ammonium false positives). Donor SMARTS includes protonated species (NH3+ is a real donor).
- **2D ligand depiction panel.** New master-tab `Ligand` renders `mol.get_svg_with_highlights()` with synchronized atom highlights matching the enabled RDKit chemistry layers. Uses `mol.get_new_coords()` + re-parse to regenerate 2D coordinates from the 3D molfile (the documented `forceCoords: true` flag does not work in the 2025.03.4 build). Atom-click → mol\* selection + camera focus is wired via the same atom-index contract as the molfile builder.
- **3D pharmacophore feature rendering.** New module `src/chemistry.backend.perception.rdkit-wasm.editable/pharmacophore-features.ts` draws H-bond acceptor cones (red, ~1 Å, oriented by neighbor centroid), H-bond donor sticks (blue), aromatic ring disks (amber, perpendicular to ring plane via `Mat4.targetTo` + `Circle` primitive), and hydrophobic halos (grey translucent spheres) as mol\* `Shape` primitives. Each feature is its own mesh group, so the picker returns the originating feature. Aromatic rings are detected via RDKit SSSR (`a1aaaaa1` + `a1aaaa1` with dedup), correctly handling fused systems (caffeine, naphthalene, indole, adenine).
- **AGENTS.md.** Defines branch model (`main` integration, `feature/<product>` per workstream, `master` upstream-molstar-sync only), file ownership rules per workstream, PR verification gate, commit-message convention, and the RDKit-JS 2025.03.4 known-limitations list.

### Fixed

- **Pharmacophore availability badge leak.** Switching to a ligand-free structure (e.g., 1CRN) now clears the pharmacophore availability text instead of leaving stale counts from the previous molecule.
- **Fused-ring detection.** Replaced the BFS connected-component approach (which over-grouped fused systems into 9-10-atom blobs that failed the 5-7 size filter) with RDKit SSSR queries.
- **Charged-species SMARTS.** Acceptor pattern now excludes positively charged atoms (was matching quaternary ammonium as a false-positive acceptor). Donor pattern keeps positive charges (NH3+ is a real donor).

### Known limitations

- `compute_gasteiger_charges` is not exposed in RDKit-JS 2025.03.4. The partial-charge layer is a graceful-stub — displays "Gasteiger computation unavailable in this RDKit-JS build" instead of silent failure.
- `<metadata>` block is not emitted by `get_svg_with_highlights` regardless of the `includeMetadata` flag. Atom click mapping falls back to a bond-path centroid computation over the rendered SVG.
- The lab's `getLigandFocusTargets` exposes a malformed label on `1GRM` (option text starts with the CIF file header). Pre-existing in the mol\* lab code, not introduced here.

### Verification

Tested against 9 lab fixture structures (zero runtime errors across all), 22 external reference molecules for SMARTS correctness (22 / 22 expected counts after the SMARTS fixes), 15-cycle reload test (heap stable at 58-63 MB, zero state-cell leak), 8-cycle rapid molecule switching (zero stuck states), and end-to-end 2D-click → 3D-selection on 1CBS-REA and 4HHB-HEM.
