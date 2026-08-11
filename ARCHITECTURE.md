# Dirac — Complete Interface & Architecture Report

> Read from GitHub main branch HEAD (8322070). Every component, every interface boundary, every protocol. No implementation detail — only contracts.

---

## System map

```
┌─────────────────────────────────────────────────────────────────┐
│                        BROWSER (client)                          │
│                                                                  │
│  ┌──────────┐   ┌───────────────────┐   ┌──────────────────┐   │
│  │ index.html│   │  index.ts          │   │  RDKit-JS WASM   │   │
│  │ (DOM +CSS)│   │  (LabShell)        │   │  (7MB, vendored) │   │
│  └─────┬─────┘   └──┬───┬───┬───┬───┘   └────────┬─────────┘   │
│        │            │   │   │   │                │              │
│        │   ┌────────┘   │   │   │      ┌─────────┘              │
│        │   │  ┌─────────┘   │   └──┐  │                        │
│        │   │  │  ┌──────────┘      │  │                        │
│  ┌─────▼───▼──▼──▼──────────────────▼──▼────┐                   │
│  │         CHEMISTRY SUBSTRATE               │                   │
│  │  (src/chemistry.backend.perception...)    │                   │
│  │                                           │                   │
│  │  semantic-chemistry-rdkit.ts  (1067 lines)│                   │
│  │  pharmacophore-features.ts    (530 lines) │                   │
│  │  ligand-depiction.ts          (233 lines) │                   │
│  │  descriptors.ts               (187 lines) │                   │
│  │  bond-order-3d.ts             (234 lines) │                   │
│  │  pains-smarts.ts              (141 lines) │                   │
│  │  semantic-{structural,chemistry,          │                   │
│  │    context,evidence,focus,               │                   │
│  │    interactions}.ts                       │                   │
│  │  workbench.ts / compose.ts / types.ts     │                   │
│  │  visual-presets.ts / visual-r4/           │                   │
│  └───────────────────┬───────────────────────┘                   │
│                      │                                           │
│  ┌───────────────────▼───────────────────────┐                   │
│  │         FACETS (3 products)                │                   │
│  │  property-cockpit/ (143 lines)            │                   │
│  │  field-wells/      (454 lines)  ──────────┼─── HTTP :8901 ──┐ │
│  │  pharmacophore-designer/ (1469 lines)     │                │ │
│  └───────────────────────────────────────────┘                │ │
│                                                                │ │
│  ┌────────────────────────────────────────────────────────┐    │ │
│  │         VENDORED ENGINE (read-only)                    │    │ │
│  │  mol-* (20 dirs: mol-gl, mol-model, mol-plugin, ...)   │    │ │
│  │  extensions/ (interactions, mp4-export, ...)           │    │ │
│  └────────────────────────────────────────────────────────┘    │ │
│                                                                │ │
└────────────────────────────────────────────────────────────────┼─┘
                                                                 │
┌────────────────────────────────────────────────────────────────┼─┐
│                    SERVER (optional backend)                    │ │
│                                                                 │ │
│  ┌──────────────────────────┐    ┌─────────────────────────┐   │ │
│  │  field_server.py:8901    │    │  physics/server.py       │   │ │
│  │  RDKit Gasteiger MEP     │    │  MEP surface + torsion   │   │ │
│  │  PySCF HF (HOMO/LUMO/   │    │  σ-hole detection        │   │ │
│  │    density/QM-MEP)       │    └─────────────────────────┘   │ │
│  └────────────┬─────────────┘                                  │ │
│               │                                                 │ │
│  ┌────────────▼─────────────┐                                  │ │
│  │  PostgreSQL (dirac DB)   │                                  │ │
│  │  compound / measurement /│                                  │ │
│  │  scf_result / vendor     │                                  │ │
│  │  7 migrations            │                                  │ │
│  └──────────────────────────┘                                  │ │
└────────────────────────────────────────────────────────────────┘─┘
```

---

## Interface 1: Browser ↔ RDKit-JS WASM

**Protocol:** in-process function calls (no network)

```
Caller (TypeScript)  →  window.initRDKitModule({ locateFile })  →  WASM
                     ←  Promise<RDKitModule>

RDKitModule:
  get_mol(smiles_or_molblock) → JSMol | null
  get_qmol(smarts)           → JSMol | null      (query mol)
  get_inchikey_for_inchi()   → string

JSMol:
  get_molblock()              → V2000 string
  get_smiles()                → canonical SMILES
  get_cxsmiles()              → extended SMILES
  get_inchi()                 → InChI string
  get_svg_with_highlights(d)  → SVG string
  get_descriptors()           → JSON (40+ fields)
  get_stereo_tags()           → JSON {CIP_atoms, CIP_bonds}
  get_substruct_matches(qmol) → JSON [{atoms:[],bonds:[]}]
  set_new_coords(useCoordGen) → void (mutates in place)
  get_new_coords(useCoordGen) → V3000 molblock string
  compute_gasteiger_charges() → void (NOT EXPOSED in 2025.03.4)
  delete()                    → void (MUST call to free WASM memory)

Contract: atom index in molfile == RDKit internal index == SVG atom index.
          get_substruct_matches returns "{}" (empty object) when no match.
          Always call mol.delete() in finally block.
```

**Singleton:** `getRDKit()` in `semantic-chemistry-rdkit.ts` — lazy init, promise cached.

**Known gaps (2025.03.4 build):**
- `compute_gasteiger_charges` — exists in interface but throws at runtime. Approximated via Allred-Rochow electronegativity.
- `forceCoords: true` in `get_svg_with_highlights` details JSON — silently ignored. Use `set_new_coords(true)` instead.
- `<metadata>` block in SVG — not emitted regardless of `includeMetadata: true`. Fallback: parse bond-path centroids.

---

## Interface 2: LabShell (index.ts) ↔ Chemistry Substrate

**Protocol:** direct TypeScript function imports

```
LabShell imports from chemistry.backend.perception.rdkit-wasm.editable:

  WORKBENCH:
    createChemWorkbench({ target }) → ChemWorkbench
      .plugin: PluginContext
      .loadStructureFromData(data, { format, label })
      .resetCamera(durationMs)
      .setBackground(color)

  RDKit LAYERS (Overpaint-based):
    RdkitChemicalLayers          → readonly array of layer definitions
    applyRdkitChemicalLayers(plugin, enabledIds, focusOptions) → Promise<void>
    getRdkitChemicalLayerCounts(structure, focusOptions) → Promise<Counts>

  PHARMACOPHORE (Shape-based):
    PharmacophoreLayers          → readonly array
    applyPharmacophoreFeatures(plugin, enabled, focusOptions) → Promise<void>
    getPharmacophoreFeatureCounts(structure, focusOptions) → Promise<Counts>

  BOND ORDER 3D (Shape-based):
    BondOrder3DLayers            → readonly array
    applyBondOrder3D(plugin, enabled, focusOptions) → Promise<void>

  DEPICTION:
    LigandDepiction.depict(molfile, options) → Promise<{svgString, atomPositions} | null>
    LigandDepiction.getAtomIndexFromClick(svg, positions, clientX, clientY) → number

  DESCRIPTORS:
    computeLigandDescriptors(molfile) → Promise<DescriptorReport | null>

  IDENTIFIERS:
    computeLigandIdentifiers(molfile) → Promise<{smiles, inchi, inchiKey, cxsmiles} | null>

  SMARTS SEARCH:
    searchLigandSmarts(molfile, smarts) → Promise<SmartsSearchResult | null>
    applySmartsSearchOverlay(plugin, focusOptions, result) → Promise<void>

  CHEMISTRY DATA:
    prepareLigandAnalysis(loci) → Promise<{molfile, atomCount, chemistry} | null>
    computeLigandChemistry(molfile, atomCount) → Promise<LigandChemistry | null>
    getRDKit() → Promise<RDKitModule>

  MOL* NATIVE SEMANTIC LAYERS (existing, pre-RDKit):
    applyStructuralSemanticLayers(plugin, layerIds)
    applyChemicalSemanticLayers(plugin, layerIds)
    applyEvidenceSemanticLayers(plugin, layerIds)
    applyInteractionSemanticLayers(plugin, layerIds, target)
    applyContextSemanticLayers(plugin, layerIds)
    applyFocusSemanticLayers(plugin, layerIds, focusOptions)

  LIGAND FOCUS:
    getLigandFocusTargets(structure) → LigandFocusTarget[]
    getLigandFocusLoci(structure, focusOptions) → StructureElement.Loci
    getFocusSemanticLayerCounts(structure, focusOptions) → Record<id, stats>
```

**Contract violations (current bugs):**
1. `ligandLociToMolfile` is defined TWICE (in semantic-chemistry-rdkit.ts AND pharmacophore-features.ts as `parseLigandLoci` + `buildMolfile`). Must be ONE.
2. `lociFromFocusOptions` is defined TWICE (same two files). Must be ONE.
3. LabShell calls `computeLigandChemistry` 4× per molecule switch (once per consumer). No cache.
4. LabShell calls `RDKit.get_mol` 10× per molecule switch. No cache.

---

## Interface 3: Field-Wells Facet ↔ Python Backend

**Protocol:** HTTP JSON on port 8901

```
Frontend (facets/field-wells/index.ts):
  const BACKEND = `http://${window.location.hostname}:8901`;

  GET  /health
    → { ok: true, rdkit: "2025.03.5", pyscf: "2.14.0", db: "connected" | null }
    Used on init to show backend status badge.

  POST /field
    Request:  { molfile: string, kind: "mep"|"homo"|"lumo"|"density"|"mep_qm", basis?: string }
    Response: { ok: true, cube: string, meta: { kind, basis, energies, convergence, timing } }
         or:  { ok: false, error: string }

    cube = Gaussian cube format string (ATOMES × GRID³ floats)
    Frontend: ParseCube → VolumeFromCube → two signed isosurfaces

  POST /embed
    Request:  { molfile: string }
    Response: { ok: true, embedding: number[], meta: {...} }
    (molecular fingerprint embedding for similarity)

Backend (field_server.py):
  - Port 8901, binds 0.0.0.0 (accessible from LAN)
  - ThreadingHTTPServer (concurrent requests)
  - In-memory SCF cache: {geometry_hash+basis → SCF result}
  - PostgreSQL cache (optional): compound-level persistence
  - MAX_QM_ATOMS = 120 (rejects large molecules for QM)
  - RDKit for Gasteiger charges (MEP)
  - PySCF for HF/STO-3G (HOMO/LUMO/density/QM-MEP)

Physics server (physics/server.py):
  - Separate from field_server.py
  - MEP surface calculation (σ-hole detection)
  - Torsion strain analysis
```

**Contract:** molfile must carry scene coordinates (Å). The backend does NOT transform coordinates — the cube it returns is in the same frame as the mol* scene. This is the load-bearing assumption.

**Failure modes:**
- Backend down → ERR_CONNECTION_REFUSED → facet shows "backend offline"
- SCF not converged → `{ ok: false, error: "SCF did not converge" }` → facet shows error
- Too many atoms → 400 → facet shows "molecule too large for QM"
- Iodine handling → special path in pyscf (effective core potential)

---

## Interface 4: Pharmacophore Designer ↔ Screening Library

**Protocol:** in-process (WASM), no network

```
facets/pharmacophore-designer/
├── index.ts     (447 lines) — panel UI + lifecycle
├── model.ts     (265 lines) — editable pharmacophore model (features + positions)
├── drag.ts      (187 lines) — 3D drag controller (mouse → feature position)
├── shape.ts     (193 lines) — mol* Shape rendering (cones/sticks/disks/halos)
├── screening.ts (250 lines) — library screening engine
└── library.ts   (127 lines) — shipped compound library (JSON)

Screening contract:
  User edits pharmacophore model (add/move/remove features)
    → model.toSmarts() generates a combined SMARTS query
    → screening.screen(model, library) iterates library molecules
    → each molecule: RDKit.get_mol(smiles) → get_substruct_matches(qmol)
    → returns: { matches: LibraryEntry[], matchCount: number }

Library format (library.ts):
  LibraryEntry = { smiles: string, name: string, id: string }
  Library is shipped as TypeScript array (no external data file)

The designer also uses the SHARED substrate:
  computePharmacophoreFeatures(structure, focusOptions) → seeds initial model
  LigandDepiction.depict(molfile, highlights) → renders hit preview SVG
```

---

## Interface 5: LabShell ↔ mol* Plugin

**Protocol:** direct PluginContext API calls

```
LabShell → workbench.plugin:
  .managers.structure.component.pivotStructure     → current structure
  .managers.structure.component.applyPreset(...)    → change representation
  .managers.structure.selection.events.changed      → selection subscription
  .managers.structure.selection.getLoci(structure)  → current selection loci
  .managers.interactivity.lociSelects.select(...)   → programmatic selection
  .managers.camera.focusLoci(loci, options)         → camera animation
  .behaviors.interaction.click.subscribe(...)       → click subscription
  .state.data.build()                               → state tree update
  .state.data.select(StateSelection.Generators...)  → query state tree

mol* → LabShell:
  Events: selection.changed, interaction.click
  State: every visual change is a state tree cell with tags

Ownership tags (each module owns its cells, cleans up by tag):
  'chemical-semantic-layers'          — semantic-chemistry.ts
  'structural-semantic-layers'        — semantic-structural.ts
  'evidence-semantic-layers'          — semantic-evidence.ts
  'rdkit-chemical-semantic-layers'    — semantic-chemistry-rdkit.ts
  'rdkit-smarts-search'               — semantic-chemistry-rdkit.ts
  'mol-plugin-chem-pharmacophore'     — pharmacophore-features.ts
  'mol-plugin-chem-bond-order-3d'     — bond-order-3d.ts
  'mol-plugin-chem-ligand-focus'      — semantic-focus.ts
  'mol-plugin-chem-semantic-interaction' — semantic-interactions.ts
  'field-wells-data' / 'field-wells-volume' — field-wells facet
  'mn-vfx-mesoscale-copy'             — index.ts (mesoscale copies)
```

---

## Interface 6: Build system ↔ App

**Protocol:** esbuild bundling

```
scripts/build.mjs:
  -e dirac  →  entry: src/app.../index.ts
  --prd     →  minified, no sourcemap, one-shot

Output:
  build/dirac/
  ├── index.html  (copied from src by esbuild onLoad handler)
  ├── dirac.js    (bundled, ~3MB)
  ├── dirac.js.map (dev only)
  └── assets/
      ├── rdkit/RDKit_minimal.{js,wasm}  (copied by build:dirac npm script)
      └── structures/*.cif                (copied by esbuild onLoad)

npm run build:dirac:
  1. node ./scripts/build.mjs -a dirac --prd
  2. cpSync(src/.../assets/rdkit, build/dirac/assets/rdkit)

RDKit WASM loading:
  <script src="./assets/rdkit/RDKit_minimal.js"></script>  (in index.html)
  window.initRDKitModule({ locateFile: f => './assets/rdkit/' + f })
```

---

## Dependency direction (who imports whom)

```
index.ts (LabShell)
  ├── imports → chemistry.backend.perception.* (ALL semantic + RDKit + workbench)
  ├── imports → facets/property-cockpit
  ├── imports → facets/field-wells
  ├── imports → facets/pharmacophore-designer
  ├── imports → mol-* (vendored, read-only)
  └── imports → camera-slab.ts (local)

chemistry.backend.perception.*
  ├── imports → mol-* (vendored, read-only)
  ├── imports → extensions/interactions (vendored)
  └── internal cross-imports:
      pharmacophore-features.ts → semantic-chemistry-rdkit.ts (computeLigandChemistry)
      bond-order-3d.ts         → (no chemistry imports, standalone)
      descriptors.ts            → semantic-chemistry-rdkit.ts (getRDKit)
      ligand-depiction.ts       → semantic-chemistry-rdkit.ts (getRDKit)
      pains-smarts.ts           → (data only, no imports)

facets/*
  ├── import → chemistry.backend.perception.* (shared substrate)
  └── import → mol-* (for Shape/Volume representations)

backend/ (Python)
  ├── field_server.py → RDKit, PySCF, PostgreSQL
  ├── physics/        → RDKit, numpy
  └── db/             → PostgreSQL migrations + data loading

NO circular imports in chemistry substrate (verified).
ONE directional dependency: pharmacophore → chemistry-rdkit (not vice versa).
```

---

## Component size & complexity

| Component | Lines | Responsibilities | Health |
|---|---|---|---|
| **index.ts** | 1687 | God Object: UI + lifecycle + 10 layer systems + panels + search | 🔴 Critical |
| **semantic-chemistry-rdkit.ts** | 1067 | 11 concerns (loader, builder, SMARTS, charges, apply, search, ...) | 🔴 Critical |
| **pharmacophore-designer/** | 1469 | 5 files, clear separation | 🟢 Good |
| **field-wells/index.ts** | 454 | Single concern, backend protocol clean | 🟢 Good |
| **pharmacophore-features.ts** | 530 | Single concern + duplicate molfile builder | 🟡 Fixable |
| **ligand-depiction.ts** | 233 | Single concern, clean | 🟢 Good |
| **bond-order-3d.ts** | 234 | Single concern, clean | 🟢 Good |
| **descriptors.ts** | 187 | Single concern, clean | 🟢 Good |
| **property-cockpit/** | 143 | Pure consumer, cleanest module | 🟢 Good |
| **workbench.ts** | 163 | mol* wrapper, clean | 🟢 Good |
| **semantic-structural.ts** | 202 | mol* query + Overpaint, clean | 🟢 Good |
| **visual-r4/** | ~2000 | MN compiler integration (pre-existing) | 🟡 Not audited |
| **backend/field_server.py** | 739 | HTTP + RDKit + PySCF + DB cache | 🟢 Good |
| **backend/physics/** | ~1000 | MEP surface + torsion + validation | 🟢 Good |
| **backend/db/** | ~50K SQL | PostgreSQL schema, 7 migrations | 🟢 Good |

---

## What an architect needs to decide

1. **MoleculeContext** — one object or event? (I propose object + EventBus)
2. **ChemistryCache** — eager (compute all on molecule load) or lazy (compute on first request)? (I propose eager: ~200ms for 43-atom ligand, acceptable)
3. **LayerOrchestrator** — registry pattern or hardcoded order? (I propose registry: each applier self-registers)
4. **PerformQueue** — last-write-wins or full queue? (I propose last-write-wins: layer toggles are idempotent)
5. **Panel lifecycle** — mount/unmount or always-alive? (I propose always-alive: panels hide/show via CSS, don't destroy)
6. **SMILES mode** — separate code path or unified MoleculeContext? (I propose unified: `{ kind: 'smiles' }` variant)
