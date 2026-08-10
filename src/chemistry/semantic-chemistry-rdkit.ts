/**
 * RDKit-powered chemistry overlays for a deposited ligand in an already
 * displayed Mol* structure.
 *
 * Pipeline: focused ligand loci → V2000 molfile (using mol* ComponentBond
 * for bond orders) → RDKit-JS (WASM) chemistry perception → atom-indexed
 * properties → mol* Overpaint on ligand atoms.
 *
 * Atom-index contract: the molfile is written in the same iteration order
 * as the loci walker used here, so atom i in RDKit output equals atom i in
 * the loci. The same walker is reused when filtering the loci by predicate,
 * keeping the round-trip closed.
 *
 * Recolor-only overlays: native geometry, picking loci, and overlays owned
 * by other modules are never touched.
 */

import { Structure, StructureElement, Unit, StructureProperties, StructureSelection, QueryContext } from '../mol-model/structure';
import { PluginContext } from '../mol-plugin/context';
import { StateTransforms } from '../mol-plugin-state/transforms';
import { StateSelection } from '../mol-state';
import { Overpaint } from '../mol-theme/overpaint';
import { Color } from '../mol-util/color';
import { OrderedSet, SortedArray } from '../mol-data/int';
import { Vec3 } from '../mol-math/linear-algebra';
import { ComponentBond } from '../mol-model-formats/structure/property/bonds/chem_comp';
import { StructureSelectionQueries } from '../mol-plugin-state/helpers/structure-selection-query';
import type { LigandFocusOptions } from './semantic-focus';

export type RdkitChemicalLayerId =
    | 'partial-charge-rdkit'
    | 'donor-acceptor-rdkit'
    | 'aromaticity-rdkit';

export type RdkitChemicalLayerCost = 'low' | 'medium';

export interface RdkitChemicalLayerDefinition {
    readonly id: RdkitChemicalLayerId;
    readonly label: string;
    readonly cost: RdkitChemicalLayerCost;
    readonly source: string;
    readonly description: string;
}

export const RdkitChemicalLayers: readonly RdkitChemicalLayerDefinition[] = Object.freeze([
    {
        id: 'aromaticity-rdkit',
        label: 'Aromaticity · RDKit',
        cost: 'low',
        source: 'RDKit SSSR aromatic ring perception (V2000 molfile input)',
        description: 'Colors atoms in aromatic rings using RDKit perception. More accurate than mol* default for non-canonical aromatics such as fused heterocycles in drug fragments.',
    },
    {
        id: 'donor-acceptor-rdkit',
        label: 'H-bond donor / acceptor',
        cost: 'low',
        source: 'RDKit Lipinski H-bond donor / acceptor SMARTS',
        description: 'Highlights H-bond donors (cyan) and acceptors (orange) using Lipinski definitions. Donors and acceptors are non-overlapping visual channels.',
    },
    {
        id: 'partial-charge-rdkit',
        label: 'Partial charge · Gasteiger',
        cost: 'medium',
        source: 'RDKit Gasteiger-Marsili partial charges computed from ligand molfile',
        description: 'Colors ligand atoms by computed partial charge: deep blue (+), white (0), deep red (−). This is an estimate, not deposited experimental data; toggle it deliberately.',
    },
]);

export interface RdkitChemicalLayerCounts {
    readonly hasLigand: boolean;
    readonly atomCount: number;
    readonly aromatic: number;
    readonly donors: number;
    readonly acceptors: number;
    readonly partialChargeRange: readonly [number, number] | null;
}

const RdkitChemicalLayerTag = 'rdkit-chemical-semantic-layers';

// === RDKit-JS singleton ===

interface JSMol {
    get_molblock(): string;
    get_smiles(): string;
    delete(): void;
    compute_gasteiger_charges(): void;
    get_substruct_matches(q: JSMol): string;
    get_svg_with_highlights(details: string): string;
    has_prop(name: string): boolean;
    get_prop(name: string): string;
    get_prop_list(includePrivate?: boolean, includeComputed?: boolean): string[];
    /** Returns a fresh V3000 molblock with 2D coordinates computed by RDDepict. */
    get_new_coords(useCoordGen: boolean): string;
    is_valid(): boolean;
}

interface RDKitModule {
    get_mol(input: string): JSMol | null;
    get_qmol(input: string): JSMol | null;
    version(): string;
}

let rdkitPromise: Promise<RDKitModule> | null = null;

export async function getRDKit(): Promise<RDKitModule> {
    if (!rdkitPromise) {
        const factory = (window as unknown as { initRDKitModule?: (cfg?: { locateFile?: (f: string) => string }) => Promise<RDKitModule> }).initRDKitModule;
        if (!factory) throw new Error('RDKit script not loaded (window.initRDKitModule missing). Ensure ./assets/rdkit/RDKit_minimal.js is included before index.js.');
        rdkitPromise = factory({ locateFile: f => `./assets/rdkit/${f}` });
    }
    return rdkitPromise;
}

// === Ligand loci → V2000 molfile ===

interface MolfileBuild {
    molfile: string;
    atomCount: number;
}

interface AtomRecord {
    element: string;
    x: number;
    y: number;
    z: number;
    charge: number;
    name: string;
    compId: string;
}

function mapChargeToMolfileCode(formalCharge: number): number {
    if (formalCharge === 1) return 3;
    if (formalCharge === 2) return 2;
    if (formalCharge === 3) return 1;
    if (formalCharge === -1) return 5;
    if (formalCharge === -2) return 6;
    if (formalCharge === -3) return 7;
    return 0;
}

/**
 * Build a V2000 molfile from a single-residue ligand loci. Iteration order
 * over the loci defines the molfile atom order; downstream code must use the
 * same walker to map RDKit results back.
 */
function ligandLociToMolfile(loci: StructureElement.Loci): MolfileBuild | null {
    if (StructureElement.Loci.isEmpty(loci)) return null;
    const structure = loci.structure;
    const model = structure.models[0];
    const bondData = ComponentBond.Provider.get(model);
    if (!bondData) return null;

    const atoms: AtomRecord[] = [];
    const position = Vec3();
    const location = StructureElement.Location.create(structure);

    for (const e of loci.elements) {
        const unit = e.unit;
        if (!Unit.isAtomic(unit)) continue;
        const count = OrderedSet.size(e.indices);
        for (let i = 0; i < count; i++) {
            const unitIndex = OrderedSet.getAt(e.indices, i);
            location.unit = unit;
            location.element = unit.elements[unitIndex];
            const element = StructureProperties.atom.type_symbol(location);
            const compId = StructureProperties.residue.label_comp_id(location);
            const atomName = StructureProperties.atom.label_atom_id(location);
            const charge = StructureProperties.atom.pdbx_formal_charge(location) || 0;
            unit.conformation.position(location.element, position);
            atoms.push({
                element,
                x: position[0],
                y: position[1],
                z: position[2],
                charge,
                name: atomName,
                compId,
            });
        }
    }

    if (atoms.length === 0) return null;
    if (atoms.length > 999) return null;

    // Single-residue assumption: a LigandFocusTarget bundle is one residue.
    const compId = atoms[0].compId;
    const nameToIdx = new Map<string, number>();
    for (let i = 0; i < atoms.length; i++) nameToIdx.set(atoms[i].name, i);

    interface BondRec { a1: number; a2: number; order: number; }
    const bonds: BondRec[] = [];
    const compBonds = bondData.entries.get(compId);
    if (compBonds?.map) {
        for (const [name1, pairs] of compBonds.map) {
            const a1 = nameToIdx.get(name1);
            if (a1 === undefined) continue;
            for (const [name2, bond] of pairs.map) {
                const a2 = nameToIdx.get(name2);
                if (a2 === undefined) continue;
                if (a1 < a2) bonds.push({ a1, a2, order: bond.order ?? 1 });
            }
        }
    }

    const lines: string[] = [];
    lines.push('');
    lines.push('  mol*');
    lines.push('');
    lines.push(
        atoms.length.toString().padStart(3, ' ')
        + bonds.length.toString().padStart(3, ' ')
        + '  0  0  0  0  0  0  0  0  0999 V2000'
    );

    for (const a of atoms) {
        const x = a.x.toFixed(4).padStart(10, ' ');
        const y = a.y.toFixed(4).padStart(10, ' ');
        const z = a.z.toFixed(4).padStart(10, ' ');
        const sym = a.element.padEnd(2, ' ');
        const chargeCode = mapChargeToMolfileCode(a.charge).toString().padStart(3, ' ');
        lines.push(`${x}${y}${z} ${sym}  0 ${chargeCode}  0  0  0  0  0  0  0  0  0  0`);
    }

    for (const b of bonds) {
        const a1 = (b.a1 + 1).toString().padStart(3, ' ');
        const a2 = (b.a2 + 1).toString().padStart(3, ' ');
        const order = b.order.toString().padStart(3, ' ');
        lines.push(`${a1}${a2}${order}  0  0  0  0`);
    }

    lines.push('M  END');

    const out = { molfile: lines.join('\n'), atomCount: atoms.length };
    // Debug hook — expose last molfile for diagnostic tests.
    if (typeof window !== 'undefined') {
        (window as unknown as { __lastRdkitMolfile?: string }).__lastRdkitMolfile = out.molfile;
    }
    return out;
}

/**
 * One-shot preparation of ligand molfile + computed chemistry. Used by both
 * the 3D overpaint applier and the 2D depiction highlighter so they show the
 * same atom predicates without re-running RDKit twice.
 */
export async function prepareLigandAnalysis(loci: StructureElement.Loci): Promise<{
    molfile: string;
    atomCount: number;
    chemistry: LigandChemistry | null;
} | null> {
    const build = ligandLociToMolfile(loci);
    if (!build) return null;
    const chemistry = await computeLigandChemistry(build.molfile, build.atomCount);
    return { molfile: build.molfile, atomCount: build.atomCount, chemistry };
}

// === RDKit property computation ===

export interface LigandChemistry {
    aromaticAtoms: Uint8Array;
    donors: Uint8Array;
    acceptors: Uint8Array;
    /** Each entry is the list of atom indices in one aromatic ring (5- or 6-membered). */
    aromaticRings: number[][];
    partialCharges: Float32Array | null;
    partialChargeMin: number;
    partialChargeMax: number;
}

function smartsAtomIndices(RDKit: RDKitModule, mol: JSMol, smarts: string, atomCount: number): Uint8Array {
    const flags = new Uint8Array(atomCount);
    let qmol: JSMol | null = null;
    try {
        qmol = RDKit.get_qmol(smarts);
        if (!qmol) return flags;
        const raw = mol.get_substruct_matches(qmol);
        // RDKit returns "{}" (empty object literal) when there are no matches,
        // and "[{atoms:[...],bonds:[...]},...]" when there are. Guard both.
        const parsed = JSON.parse(raw) as unknown;
        if (!Array.isArray(parsed)) return flags;
        for (const match of parsed as Array<{ atoms?: number[]; bonds?: number[] }>) {
            const atoms = match.atoms;
            if (!atoms) continue;
            for (const idx of atoms) {
                if (typeof idx === 'number' && idx >= 0 && idx < atomCount) flags[idx] = 1;
            }
        }
    } catch { /* ignore */ }
    finally {
        if (qmol) qmol.delete();
    }
    return flags;
}

/**
 * Find aromatic 5- and 6-membered rings via SMARTS, deduped by atom set.
 * Each ring is found multiple times by get_substruct_matches (rotations +
 * directions); we collapse duplicates via a sorted-index signature.
 */
function smartsAromaticRings(RDKit: RDKitModule, mol: JSMol): number[][] {
    const rings: number[][] = [];
    const seen = new Set<string>();
    for (const smarts of ['a1aaaaa1', 'a1aaaa1']) {
        let qmol: JSMol | null = null;
        try {
            qmol = RDKit.get_qmol(smarts);
            if (!qmol) continue;
            const raw = mol.get_substruct_matches(qmol);
            const parsed = JSON.parse(raw) as unknown;
            if (!Array.isArray(parsed)) continue;
            for (const match of parsed as Array<{ atoms?: number[] }>) {
                const atoms = match.atoms;
                if (!atoms || atoms.length < 5) continue;
                const sig = [...atoms].sort((a, b) => a - b).join(',');
                if (seen.has(sig)) continue;
                seen.add(sig);
                rings.push([...atoms].sort((a, b) => a - b));
            }
        } catch { /* ignore */ }
        finally {
            if (qmol) qmol.delete();
        }
    }
    return rings;
}

function parseGasteigerCharges(mol: JSMol, atomCount: number): { charges: Float32Array; min: number; max: number } | null {
    let raw: string | null = null;
    try {
        if (mol.has_prop('_GasteigerCharges')) raw = mol.get_prop('_GasteigerCharges');
    } catch { /* ignore */ }
    if (!raw) return null;
    const tokens = raw.trim().split(/\s+/);
    if (tokens.length < atomCount) return null;
    const charges = new Float32Array(atomCount);
    let min = Infinity;
    let max = -Infinity;
    for (let i = 0; i < atomCount; i++) {
        const v = parseFloat(tokens[i]);
        if (!Number.isFinite(v)) return null;
        charges[i] = v;
        if (v < min) min = v;
        if (v > max) max = v;
    }
    return { charges, min, max };
}

export async function computeLigandChemistry(molfile: string, atomCount: number): Promise<LigandChemistry | null> {
    let mol: JSMol | null = null;
    try {
        const RDKit = await getRDKit();
        mol = RDKit.get_mol(molfile);
        if (!mol || !mol.is_valid()) return null;

        const aromatic = smartsAtomIndices(RDKit, mol, '[a]', atomCount);
        // Lipinski donor: N or O with at least one H. Includes positively charged
        // atoms like -NH3+ which are strong donors (H-bond via their H's).
        const donors = smartsAtomIndices(RDKit, mol, '[#7,#8;H1,H2,H3]', atomCount);
        // Lipinski acceptor: N or O with at least one lone pair, excluding positively
        // charged atoms (quaternary ammonium has no lone pair to donate).
        const acceptors = smartsAtomIndices(RDKit, mol, '[#7,#8;H0,H1,H2;!+]', atomCount);
        const aromaticRings = smartsAromaticRings(RDKit, mol);

        let partialCharges: Float32Array | null = null;
        let partialChargeMin = 0;
        let partialChargeMax = 0;
        try {
            mol.compute_gasteiger_charges();
            const parsed = parseGasteigerCharges(mol, atomCount);
            if (parsed) {
                partialCharges = parsed.charges;
                partialChargeMin = parsed.min;
                partialChargeMax = parsed.max;
            }
        } catch { /* leave null */ }

        return {
            aromaticAtoms: aromatic,
            donors,
            acceptors,
            aromaticRings,
            partialCharges,
            partialChargeMin,
            partialChargeMax,
        };
    } finally {
        if (mol) mol.delete();
    }
}

// === Color utilities ===

function mixRgb(a: [number, number, number], b: [number, number, number], t: number): [number, number, number] {
    return [
        Math.round(a[0] + (b[0] - a[0]) * t),
        Math.round(a[1] + (b[1] - a[1]) * t),
        Math.round(a[2] + (b[2] - a[2]) * t),
    ];
}

function partialChargeColor(v: number, bound: number): Color {
    const t = Math.max(-1, Math.min(1, v / bound));
    const NEG: [number, number, number] = [0xe4, 0x5a, 0x5a];
    const MID: [number, number, number] = [0xff, 0xff, 0xff];
    const POS: [number, number, number] = [0x5a, 0x8a, 0xe4];
    const [r, g, b] = t >= 0 ? mixRgb(MID, POS, t) : mixRgb(MID, NEG, -t);
    return Color((r << 16) | (g << 8) | b);
}

// === Loci helpers ===

function lociFromFocusOptions(structure: Structure, options: LigandFocusOptions): StructureElement.Loci {
    if (options.target && options.target.hash === structure.hashCode) {
        return StructureElement.Bundle.toLoci(options.target, structure);
    }
    const selection = StructureSelectionQueries.ligand.query(new QueryContext(structure.root));
    return StructureSelection.toLociWithCurrentUnits(selection);
}

/**
 * Filter a loci by an atom-index predicate. The atom index counter walks the
 * loci in the same order as ligandLociToMolfile, so the predicate receives
 * the SAME index that RDKit used.
 */
function filterLociByAtomIndex(
    loci: StructureElement.Loci,
    predicate: (atomIndex: number) => boolean,
): StructureElement.Loci {
    if (StructureElement.Loci.isEmpty(loci)) return loci;
    const newElements: Array<{ unit: Unit.Atomic; indices: OrderedSet<StructureElement.UnitIndex> }> = [];
    let counter = 0;
    for (const e of loci.elements) {
        if (!Unit.isAtomic(e.unit)) {
            counter += OrderedSet.size(e.indices);
            continue;
        }
        const kept: number[] = [];
        const count = OrderedSet.size(e.indices);
        for (let i = 0; i < count; i++) {
            const unitIndex = OrderedSet.getAt(e.indices, i);
            if (predicate(counter)) kept.push(unitIndex);
            counter++;
        }
        if (kept.length > 0) {
            kept.sort((a, b) => a - b);
            newElements.push({
                unit: e.unit,
                indices: OrderedSet.ofSortedArray(SortedArray.ofSortedArray<StructureElement.UnitIndex>(kept)),
            });
        }
    }
    return StructureElement.Loci(loci.structure, newElements);
}

// === Public layer application ===

export async function getRdkitChemicalLayerCounts(structure: Structure, options: LigandFocusOptions = {}): Promise<RdkitChemicalLayerCounts> {
    const loci = lociFromFocusOptions(structure, options);
    if (StructureElement.Loci.isEmpty(loci)) {
        return { hasLigand: false, atomCount: 0, aromatic: 0, donors: 0, acceptors: 0, partialChargeRange: null };
    }
    const build = ligandLociToMolfile(loci);
    if (!build) {
        return { hasLigand: true, atomCount: StructureElement.Loci.size(loci), aromatic: 0, donors: 0, acceptors: 0, partialChargeRange: null };
    }
    const chem = await computeLigandChemistry(build.molfile, build.atomCount);
    if (!chem) {
        return { hasLigand: true, atomCount: build.atomCount, aromatic: 0, donors: 0, acceptors: 0, partialChargeRange: null };
    }
    const range = chem.partialCharges ? ([chem.partialChargeMin, chem.partialChargeMax] as const) : null;
    return {
        hasLigand: true,
        atomCount: build.atomCount,
        aromatic: countSetBits(chem.aromaticAtoms),
        donors: countSetBits(chem.donors),
        acceptors: countSetBits(chem.acceptors),
        partialChargeRange: range,
    };
}

function countSetBits(flags: Uint8Array): number {
    let n = 0;
    for (let i = 0; i < flags.length; i++) if (flags[i]) n++;
    return n;
}

/**
 * Build the per-atom Overpaint layers for the currently enabled RDKit
 * chemistry layers. Returns null when there is no ligand or RDKit failed.
 */
async function buildRdkitOverpaintLayers(
    loci: StructureElement.Loci,
    enabledIds: Set<RdkitChemicalLayerId>,
): Promise<Array<{ loci: StructureElement.Loci; color: Color }>> {
    const build = ligandLociToMolfile(loci);
    if (!build) return [];
    const chem = await computeLigandChemistry(build.molfile, build.atomCount);
    if (!chem) return [];

    const out: Array<{ loci: StructureElement.Loci; color: Color }> = [];

    if (enabledIds.has('aromaticity-rdkit')) {
        const filtered = filterLociByAtomIndex(loci, i => chem.aromaticAtoms[i] === 1);
        if (!StructureElement.Loci.isEmpty(filtered)) out.push({ loci: filtered, color: Color(0xc792ea) });
    }

    if (enabledIds.has('donor-acceptor-rdkit')) {
        const donorLoci = filterLociByAtomIndex(loci, i => chem.donors[i] === 1);
        if (!StructureElement.Loci.isEmpty(donorLoci)) out.push({ loci: donorLoci, color: Color(0x5fd0c8) });
        const acceptorLoci = filterLociByAtomIndex(loci, i => chem.acceptors[i] === 1);
        if (!StructureElement.Loci.isEmpty(acceptorLoci)) out.push({ loci: acceptorLoci, color: Color(0xe1a14e) });
    }

    if (enabledIds.has('partial-charge-rdkit') && chem.partialCharges) {
        // Bucket to 0.1 step; skip the [-0.05, 0.05] neutral bucket so it stays uncolored.
        const bound = Math.max(Math.abs(chem.partialChargeMin), Math.abs(chem.partialChargeMax), 0.1);
        const buckets = new Map<number, { indices: number[]; color: Color }>();
        for (let i = 0; i < build.atomCount; i++) {
            const v = chem.partialCharges[i];
            if (Math.abs(v) < 0.05) continue;
            const bucketKey = Math.round(v * 10);
            if (!buckets.has(bucketKey)) {
                buckets.set(bucketKey, { indices: [], color: partialChargeColor(v, bound) });
            }
            buckets.get(bucketKey)!.indices.push(i);
        }
        for (const { indices, color } of buckets.values()) {
            const indexSet = new Set(indices);
            const filtered = filterLociByAtomIndex(loci, i => indexSet.has(i));
            if (!StructureElement.Loci.isEmpty(filtered)) out.push({ loci: filtered, color });
        }
    }

    return out;
}

/**
 * Replaces this module's overlays with exactly the requested layers. Native
 * geometry, picking, and overlays owned by other modules are left alone.
 */
export async function applyRdkitChemicalLayers(
    plugin: PluginContext,
    enabled: Iterable<RdkitChemicalLayerId>,
    options: LigandFocusOptions = {},
): Promise<void> {
    const enabledIds = new Set(enabled);
    const state = plugin.state.data;
    const update = state.build();

    const structure = plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
    if (!structure) {
        for (const cell of allOwnedOverlays(plugin)) update.delete(cell);
        await update.commit({ doNotUpdateCurrent: true });
        return;
    }

    const loci = lociFromFocusOptions(structure, options);
    const layerInputs = StructureElement.Loci.isEmpty(loci)
        ? []
        : await buildRdkitOverpaintLayers(loci, enabledIds);

    for (const structureRef of plugin.managers.structure.component.currentStructures) {
        for (const component of structureRef.components) {
            for (const representation of component.representations) {
                const repr = representation.cell;
                const sourceData = repr.obj?.data.sourceData;
                if (!sourceData) continue;

                const existing = state.select(
                    StateSelection.Generators.ofTransformer(
                        StateTransforms.Representation.OverpaintStructureRepresentation3DFromBundle,
                        repr.transform.ref,
                    ).withTag(RdkitChemicalLayerTag),
                )[0];

                const bundleLayers: Overpaint.BundleLayer[] = layerInputs.map(l => ({
                    bundle: StructureElement.Bundle.fromLoci(l.loci),
                    color: l.color,
                    clear: false,
                }));

                const overpaint = Overpaint.filter(Overpaint.ofBundle(bundleLayers, sourceData.root), sourceData) as Overpaint<StructureElement.Loci>;

                if (existing) {
                    if (bundleLayers.length) update.to(existing.transform.ref).update(Overpaint.toBundle(overpaint));
                    else update.delete(existing.transform.ref);
                } else if (bundleLayers.length) {
                    update.to(repr.transform.ref).apply(
                        StateTransforms.Representation.OverpaintStructureRepresentation3DFromBundle,
                        Overpaint.toBundle(overpaint),
                        { tags: RdkitChemicalLayerTag },
                    );
                }
            }
        }
    }

    await update.commit({ doNotUpdateCurrent: true });
}

function allOwnedOverlays(plugin: PluginContext) {
    return plugin.state.data.select(
        StateSelection.Generators.ofTransformer(
            StateTransforms.Representation.OverpaintStructureRepresentation3DFromBundle,
        ).withTag(RdkitChemicalLayerTag),
    );
}
