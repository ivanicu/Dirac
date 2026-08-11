# Dirac Architecture Redesign

> Designed from the audit findings: 1687-line God Object, 10× redundant RDKit parsing, 3 independent molfile builders, zero caching, blocking perform() lock.

## Design Principles

1. **One molecule, one compute pass.** A molecule switch triggers RDKit ONCE; every consumer reads from cache.
2. **Panels don't know about each other.** Each panel receives a `MoleculeContext` + `ChemistryCache` and renders itself. No cross-panel method calls.
3. **Layers are data, not code paths.** A single `LayerOrchestrator` dispatches to typed appliers; `applySemanticLayers`'s 10-line serial chain is replaced by a registry lookup.
4. **The shell only wires.** `LabShell` creates modules, subscribes to events, forwards to the right module. Zero business logic.
5. **perform() is a queue, not a lock.** Last-write-wins: if the user toggles three layers fast, only the final state is applied.

---

## Module map

```
src/app…/                                    src/chemistry…/
├── index.html  (DOM structure, zero JS)     ├── rdkit/
├── index.ts    (6-line boot)                │   ├── loader.ts        (singleton WASM)
├── shell/                                   │   └── types.ts         (JSMol interface)
│   ├── lab-shell.ts                        ├── molfile/
│   ├── perform-queue.ts                    │   └── builder.ts        (ONE V2000 builder)
│   └── event-bus.ts                        ├── smarts/
├── context/                                 │   ├── engine.ts        (match + dedup)
│   ├── molecule-context.ts                 │   ├── rings.ts         (SSSR ring detect)
│   └── chemistry-cache.ts                  │   └── patterns.ts       (PAINS + reactive)
├── layers/                                  ├── perception/
│   ├── registry.ts                         │   ├── chemistry.ts      (computeLigandChemistry)
│   ├── semantic-applier.ts                 │   ├── descriptors.ts    (existing)
│   ├── rdkit-applier.ts                    │   ├── identifiers.ts    (SMILES/InChI)
│   ├── pharmacophore-applier.ts            │   ├── pharmacophore.ts  (3D features)
│   └── vfx-applier.ts                      │   └── bond-order-3d.ts  (existing)
├── panels/                                  ├── depiction/
│   ├── topbar.ts                           │   └── ligand-depiction.ts (existing)
│   ├── ligand-panel.ts                     ├── application/
│   ├── properties-panel.ts                 │   ├── overpaint.ts       (Overpaint applier)
│   ├── ledger-panel.ts                     │   └── smart-search.ts    (SMARTS overlay)
│   └── diagnostics.ts                      ├── pains-smarts.ts        (existing data)
├── facets/                                  ├── shared/
│   ├── property-cockpit/  (existing)       │   ├── loci.ts            (ONE filterLociByAtomIndex)
│   ├── field-wells/       (other agent)    │   ├── focus.ts           (ONE lociFromFocusOptions)
│   └── pharmacophore-designer/ (other)     │   └── colors.ts          (all color constants)
│                                           └── semantic-*/           (existing mol* layers)
```

---

## Core types

### MoleculeContext

The single source of truth. Created once per molecule switch. Every module reads from this.

```typescript
interface MoleculeContext {
  readonly id: string;                    // '1CBS' | 'smiles:CC(=O)Oc1...' 
  readonly label: string;                 // 'REA · A:200' | 'SMILES molecule'
  readonly source: MoleculeSource;
  readonly molfile: string;               // V2000, built ONCE
  readonly atomCount: number;
  readonly structure: Structure | null;   // null in SMILES mode
  readonly ligandTarget: LigandFocusTarget | null;
}

type MoleculeSource =
  | { kind: 'pdb'; structure: Structure; ligandTarget: LigandFocusTarget }
  | { kind: 'smiles'; smiles: string };
```

### ChemistryCache

Computed ONCE per MoleculeContext. All consumers read from here — no independent `RDKit.get_mol` calls.

```typescript
class ChemistryCache {
  readonly context: MoleculeContext;
  readonly chemistry: LigandChemistry | null;     // SMARTS flags + charges + stereo
  readonly descriptors: DescriptorReport | null;   // MW/LogP/TPSA + Lipinski/Veber
  readonly identifiers: LigandIdentifiers | null;  // SMILES/InChI/InChIKey

  static async compute(context: MoleculeContext): Promise<ChemistryCache>;
}
```

### EventBus

Decouples panels from the shell. Panels subscribe to what they need.

```typescript
type LabEvent =
  | { type: 'molecule-loaded'; context: MoleculeContext; cache: ChemistryCache }
  | { type: 'layers-changed'; enabledLayers: Set<LayerId> }
  | { type: 'selection-changed'; selectedAtomIndices: Set<number> }
  | { type: 'smarts-search'; result: SmartsSearchResult | null };

class EventBus {
  on(event: LabEvent['type'], handler: (event: LabEvent) => void): () => void;
  emit(event: LabEvent): void;
}
```

---

## Information flow (one molecule switch)

```
User: selects '4HHB' from dropdown
  ↓
Topbar → LabShell.switchMolecule('4HHB')
  ↓
LabShell:
  1. workbench.loadStructureFromData(cifData)           ← mol* loads structure
  2. MoleculeContext.create(structure, ligandTarget)     ← builds molfile ONCE
  3. ChemistryCache.compute(context)                     ← RDKit.get_mol ONCE
                                                          ← ALL SMARTS ONCE
                                                          ← descriptors ONCE
                                                          ← identifiers ONCE
  4. eventBus.emit('molecule-loaded', context, cache)    ← broadcast
  5. LayerOrchestrator.apply(enabledLayers, context, cache)  ← reads cache, no RDKit
  ↓
Every panel receives 'molecule-loaded' event:
  - LigandPanel:     cache.identifiers → render SMILES/InChI
                     context.molfile → LigandDepiction.depict (1 RDKit call for SVG)
  - PropertiesPanel: cache.descriptors → render 12 cells + Lipinski/Veber
  - LedgerPanel:     plugin interactions → render contact table
  - Diagnostics:     context.structure → render metrics
  ↓
RDKit.get_mol calls: 1 (context) + 1 (depict) = 2 total
SMARTS matches: N (one pass, cached)
Previously: 10 get_mol + 8N SMARTS = ~170 WASM calls
```

---

## Layer system

### Registry (replaces the 11 `isXxxLayer` predicates + serial apply chain)

```typescript
type LayerCategory =
  | 'structural'    // mol* DSSP, roles, disulfide
  | 'chemical'      // mol* aromatic/charge/metal (CCD-based)
  | 'rdkit'         // RDKit SMARTS (WASM)
  | 'evidence'      // occupancy/B-factor/alt-locs
  | 'context'       // water/lipid/glycan/ion
  | 'focus'         // ligand detail/interface/pocket
  | 'interaction'   // H-bond/ionic/pi/metal
  | 'pharmacophore' // 3D feature primitives
  | 'bond-order-3d' // 3D parallel cylinders
  | 'vfx';          // lighting/material/postprocessing

interface LayerApplier {
  category: LayerCategory;
  apply(plugin: PluginContext, layerIds: readonly string[], context: MoleculeContext, cache: ChemistryCache): Promise<void>;
}

const registry = new Map<LayerCategory, LayerApplier>();
```

### LayerOrchestrator (replaces applySemanticLayers + applyVisuals)

```typescript
class LayerOrchestrator {
  constructor(plugin: PluginContext, registry: Map<LayerCategory, LayerApplier>);

  async apply(enabledLayers: Set<LayerId>, context: MoleculeContext, cache: ChemistryCache): Promise<void> {
    // Group layers by category
    const byCategory = groupBy(enabledLayers, layer => this.categorize(layer));
    // Apply in dependency order: base → chemistry → interaction → features → vfx
    for (const category of APPLICATION_ORDER) {
      const applier = this.registry.get(category);
      const layerIds = byCategory.get(category) ?? [];
      if (layerIds.length > 0 || applier.alwaysApply) {
        await applier.apply(this.plugin, layerIds, context, cache);
      }
    }
  }
}
```

---

## Panel architecture

Each panel is a self-contained module with the same shape:

```typescript
interface Panel {
  mount(container: HTMLElement): void;
  onMoleculeLoaded(context: MoleculeContext, cache: ChemistryCache): void;
  onLayersChanged(enabledLayers: Set<LayerId>): void;
  onSelectionChanged(selectedAtoms: Set<number>): void;
  dispose?(): void;
}
```

### LigandPanel (was 300 lines in index.ts)

```typescript
class LigandPanel implements Panel {
  mount(container) { /* create SVG container, SMARTS input, identifiers block */ }
  
  onMoleculeLoaded(context, cache) {
    // 1. Depict: LigandDepiction.depict(context.molfile, highlights)  ← 1 RDKit call
    // 2. Identifiers: cache.identifiers → fill SMILES/InChI/InChIKey fields
    // 3. Highlights: derive from enabledLayers + cache.chemistry
  }
  
  onSelectionChanged(selectedAtoms) {
    // Draw/remove highlight rings on the SVG
  }
  
  // SMARTS input: local event handler, calls eventBus.emit('smarts-search', result)
}
```

### PropertiesPanel (existing, minimal change)

```typescript
class PropertiesPanel implements Panel {
  onMoleculeLoaded(context, cache) {
    this.content.innerHTML = renderPropertiesHtml(cache.descriptors);
    // Click→highlight: data-toggle-layer attribute → eventBus.emit('layers-changed', ...)
  }
}
```

---

## PerformQueue (replaces perform())

```typescript
class PerformQueue {
  private current: Promise<void> = Promise.resolve();
  private pending: (() => Promise<void>) | null = null;

  enqueue(action: () => Promise<void>): void {
    if (this.pending) {
      this.pending = action;  // last-write-wins: replace pending with latest
    } else {
      this.pending = action;
      this.drain();
    }
  }

  private async drain() {
    while (this.pending) {
      const action = this.pending;
      this.pending = null;
      try { await action(); } catch (e) { console.error(e); }
    }
  }
}
```

User toggles 3 layers rapidly → queue processes first, coalesces next two into one apply.

---

## Migration path

Phase 1 (zero behavior change): Extract shared code, no wiring changes.
1. Create `shared/loci.ts` — move `filterLociByAtomIndex` + `lociFromFocusOptions` here. Both `semantic-chemistry-rdkit.ts` and `pharmacophore-features.ts` import from it. Delete duplicates.
2. Create `molfile/builder.ts` — move `ligandLociToMolfile`. `pharmacophore-features.ts`'s `parseLigandLoci` + `buildMolfile` deleted, imports the shared one.
3. Create `rdkit/loader.ts` + `rdkit/types.ts` — extract from `semantic-chemistry-rdkit.ts`.

Phase 2 (internal refactor, same DOM): Split God Object.
4. Create `context/molecule-context.ts` + `context/chemistry-cache.ts`.
5. Wire `LabShell.init()` to compute context + cache once, pass to consumers.
6. Create `shell/perform-queue.ts`, replace `perform()`.

Phase 3 (panel extraction): Each panel becomes a module.
7. Extract `panels/ligand-panel.ts` from index.ts (the ~300 lines of renderLigandDepiction + SMARTS + identifiers + click sync).
8. Extract `panels/properties-panel.ts` (already mostly done as facet).
9. Extract `panels/ledger-panel.ts` + `panels/diagnostics.ts`.

Phase 4 (layer system): Registry + orchestrator.
10. Create `layers/registry.ts` + `layers/*-applier.ts`.
11. Replace `applySemanticLayers`'s serial chain with `LayerOrchestrator.apply()`.

Each phase ships independently. No phase changes user-visible behavior. Each phase reduces `index.ts` by ~300-400 lines.
