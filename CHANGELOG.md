# Changelog

All notable changes to **Dirac** are documented here. This project forks from [mol\*](https://github.com/molstar/molstar) at upstream SHA `e594cc6` (mol\* master, August 2026); mol\*'s pre-fork history is not included. When mol\* features are pulled forward they appear here with a `upstream:` tag.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/2.1.0/).

## [Unreleased]

### Pending product workstreams

- **Pharmacophore Designer** — drag-editable HBA / HBD / aromatic / hydrophobic features with live SMARTS screening against a precomputed ligand library. Branch: `feature/pharmacophore-designer`.
- **Conformer Explorer** — RDKit ETKDG conformer generation, mol\* morph animation, overlay view, energy landscape. Branch: `feature/conformer-explorer`.
- **Property Optimization Cockpit** — Lipinski / Veber dashboard driven by `mol.get_descriptors()`. Branch: `feature/property-cockpit`.

## [0.1.0] — 2026-08-10

Initial Dirac snapshot consolidating the mn-compiler-lab workbench and its RDKit-JS chemistry substrate.

### Added

- **mn-compiler-lab UI redesign.** Replaced the single-pane scrolling sidebar with a master-detail layout: 64-px top bar (molecule / representation / picking / status / clear), 380-px sidebar with five master-tabs (Focus / Semantics / Ligand / VFX / Ledger), sticky diagnostics strip with 4 always-visible metrics. Cuts vertical overflow from 260 px to 0 on a 900-px viewport.
- **RDKit-JS chemistry integration.** Vendored `@rdkit/rdkit` 2025.3.4-1.0.0 WASM at `src/apps/dirac/assets/rdkit/` (7 MB). Singleton loader at `src/mol-plugin-chem/semantic-chemistry-rdkit.ts` exposes SMARTS-based perception for aromaticity, Lipinski H-bond donor, and Lipinski H-bond acceptor over the focused ligand. Acceptor SMARTS excludes positively charged atoms (no quaternary-ammonium false positives). Donor SMARTS includes protonated species (NH3+ is a real donor).
- **2D ligand depiction panel.** New master-tab `Ligand` renders `mol.get_svg_with_highlights()` with synchronized atom highlights matching the enabled RDKit chemistry layers. Uses `mol.get_new_coords()` + re-parse to regenerate 2D coordinates from the 3D molfile (the documented `forceCoords: true` flag does not work in the 2025.03.4 build). Atom-click → mol\* selection + camera focus is wired via the same atom-index contract as the molfile builder.
- **3D pharmacophore feature rendering.** New module `src/mol-plugin-chem/pharmacophore-features.ts` draws H-bond acceptor cones (red, ~1 Å, oriented by neighbor centroid), H-bond donor sticks (blue), aromatic ring disks (amber, perpendicular to ring plane via `Mat4.targetTo` + `Circle` primitive), and hydrophobic halos (grey translucent spheres) as mol\* `Shape` primitives. Each feature is its own mesh group, so the picker returns the originating feature. Aromatic rings are detected via RDKit SSSR (`a1aaaaa1` + `a1aaaa1` with dedup), correctly handling fused systems (caffeine, naphthalene, indole, adenine).
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
