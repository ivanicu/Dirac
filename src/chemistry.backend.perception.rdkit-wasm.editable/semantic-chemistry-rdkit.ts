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
    | 'aromaticity-rdkit'
    | 'stereo-rdkit'
    | 'ring-atoms-rdkit'
    | 'sp3-carbons-rdkit'
    | 'reactive-groups-rdkit'
    | 'bond-order-rdkit';

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
    {
        id: 'stereo-rdkit',
        label: 'Stereochemistry (R / S)',
        cost: 'low',
        source: 'RDKit CIP stereochemistry perception via get_stereo_tags',
        description: 'Highlights chiral atoms with assigned CIP configuration: R = blue, S = red, undefined (? potential center) = yellow. Useful for verifying deposited stereochemistry and spotting ambiguous centers.',
    },
    {
        id: 'ring-atoms-rdkit',
        label: 'Ring atoms',
        cost: 'low',
        source: 'RDKit SSSR ring membership via [R] SMARTS',
        description: 'Highlights all atoms in any SSSR ring (aliphatic or aromatic). Distinguishes scaffold ring atoms from chain atoms — fundamental for reading the molecular skeleton.',
    },
    {
        id: 'sp3-carbons-rdkit',
        label: 'sp³ carbons',
        cost: 'low',
        source: 'RDKit hybridization perception via [CX4] SMARTS',
        description: 'Highlights saturated sp³-hybridized carbons (tetrahedral, 4 single bonds). Indicates the 3D-character of the ligand scaffold — high sp³ fraction correlates with drug-likeness and lead-likeness.',
    },
    {
        id: 'reactive-groups-rdkit',
        label: 'Reactive groups alert',
        cost: 'low',
        source: 'RDKit SMARTS patterns for common assay-interference and covalent-reactive groups',
        description: 'Highlights atoms in known problematic substructures: aldehyde, Michael acceptor, epoxide, acyl halide, alkyl halide, nitro, disulfide, peroxide, azide, diazonium. These are the top-10 most common flags in medicinal chemistry screening cascade (complement to full PAINS which requires a 400-pattern library).',
    },
    {
        id: 'bond-order-rdkit',
        label: 'Bond order (double / triple)',
        cost: 'low',
        source: 'V2000 molfile bond block parsed from ligand molfile',
        description: 'Colors atoms participating in double bonds (blue) and triple bonds (purple). The 3D representation still draws single-line cylinders for all bonds; this layer at least shows WHERE the unsaturation is. Full double-line rendering requires a custom ShapeRepresentation (future work).',
    },
]);

export interface RdkitChemicalLayerCounts {
    readonly hasLigand: boolean;
    readonly atomCount: number;
    readonly aromatic: number;
    readonly donors: number;
    readonly acceptors: number;
    readonly partialChargeRange: readonly [number, number] | null;
    readonly chiralCentersR: number;
    readonly chiralCentersS: number;
    readonly chiralCentersUndefined: number;
    readonly ringAtoms: number;
    readonly sp3Carbons: number;
    readonly     reactiveGroups: readonly string[];
    readonly doubleBondAtoms: number;
    readonly tripleBondAtoms: number;
}

const RdkitChemicalLayerTag = 'rdkit-chemical-semantic-layers';

// === RDKit-JS singleton ===

interface JSMol {
    get_molblock(): string;
    get_smiles(): string;
    get_cxsmiles(): string;
    get_inchi(): string;
    delete(): void;
    compute_gasteiger_charges(): void;
    get_substruct_matches(q: JSMol): string;
    get_svg_with_highlights(details: string): string;
    get_stereo_tags(): string;
    has_prop(name: string): boolean;
    get_prop(name: string): string;
    get_prop_list(includePrivate?: boolean, includeComputed?: boolean): string[];
    /** Returns a fresh V3000 molblock with 2D coordinates computed by RDDepict. */
    get_new_coords(useCoordGen: boolean): string;
    /** Mutate the mol's conformer in place with new 2D coords (CoordGen if true). */
    set_new_coords(useCoordGen: boolean): void;
    /** Returns JSON with 40+ RDKit descriptors (MW, LogP, TPSA, HBD/HBA, rings, etc.). */
    get_descriptors(): string;
    is_valid(): boolean;
}

interface RDKitModule {
    get_mol(input: string): JSMol | null;
    get_qmol(input: string): JSMol | null;
    get_inchikey_for_inchi(inchi: string): string;
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
        // 8 zero fields + '999' per the V2000 spec (11 fields total before the
        // version). A 9th zero field shifts 'V2000' off its fixed column; the
        // RDKit-JS parser tolerates that, desktop RDKit (fields backend)
        // rejects the molfile. This fix has been reverted once already by a
        // stale-context file rewrite — if you regenerate this file, KEEP IT.
        + '  0  0  0  0  0  0  0  0999 V2000'
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
    /** Chiral centers from CIP perception: atomIdx → 'R' | 'S' | '?' */
    chiralCenters: Map<number, 'R' | 'S' | '?'>;
    ringAtoms: Uint8Array;            // [R] SMARTS — any SSSR ring atom
    sp3Carbons: Uint8Array;           // [CX4] SMARTS — tetrahedral saturated C
    reactiveGroups: Uint8Array;       // union of reactive SMARTS matches
    reactiveGroupLabels: string[];    // human-readable list of found groups
    doubleBondAtoms: Uint8Array;      // atoms with at least one double bond
    tripleBondAtoms: Uint8Array;      // atoms with at least one triple bond
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

function tryOrDefault<T>(fn: () => T, def: T = '' as unknown as T): T {
    try { return fn(); } catch { return def; }
}

// Allred-Rochow electronegativity values for partial-charge approximation.
const ELECTRONEGATIVITY: Record<string, number> = {
    H: 2.20, C: 2.55, N: 3.04, O: 3.44, F: 3.98,
    P: 2.19, S: 2.58, CL: 3.16, BR: 2.96, I: 2.66,
    B: 2.04, SI: 1.90, SE: 2.55, AS: 2.18,
    LI: 0.98, NA: 0.93, K: 0.82, MG: 1.31, CA: 1.00,
    FE: 1.83, CU: 1.90, ZN: 1.65, MN: 1.55, CO: 1.88,
    NI: 1.91, MO: 2.16, W: 2.36,
};

/**
 * Approximate partial charges via Allred-Rochow electronegativity differences.
 * Fallback when RDKit-JS `compute_gasteiger_charges` is unavailable.
 * δ_i = Σ_j (EN_i − EN_j) / degree_i, normalized so the range is ~[-0.5, +0.5].
 */
function approximatePartialCharges(molfile: string, atomCount: number): { charges: Float32Array; min: number; max: number } | null {
    const lines = molfile.split('\n');
    const countsLine = lines[3] || '';
    const nAtoms = parseInt(countsLine.slice(0, 3).trim(), 10) || 0;
    const nBonds = parseInt(countsLine.slice(3, 6).trim(), 10) || 0;
    if (nAtoms !== atomCount || nAtoms === 0) return null;

    // Parse element symbols
    const elements: string[] = [];
    for (let i = 0; i < nAtoms; i++) {
        const ln = lines[4 + i] || '';
        elements.push(ln.slice(31, 34).trim().toUpperCase());
    }

    // Parse bonds → neighbor adjacency + degree
    const neighbors: number[][] = Array.from({ length: nAtoms }, () => []);
    for (let b = 0; b < nBonds; b++) {
        const ln = lines[4 + nAtoms + b] || '';
        const a1 = parseInt(ln.slice(0, 3).trim(), 10) - 1;
        const a2 = parseInt(ln.slice(3, 6).trim(), 10) - 1;
        if (a1 >= 0 && a1 < nAtoms && a2 >= 0 && a2 < nAtoms) {
            neighbors[a1].push(a2);
            neighbors[a2].push(a1);
        }
    }

    const charges = new Float32Array(nAtoms);
    let min = Infinity;
    let max = -Infinity;
    for (let i = 0; i < nAtoms; i++) {
        const enI = ELECTRONEGATIVITY[elements[i]] ?? 2.5;
        const deg = neighbors[i].length || 1;
        let delta = 0;
        for (const j of neighbors[i]) {
            const enJ = ELECTRONEGATIVITY[elements[j]] ?? 2.5;
            delta += (enI - enJ);
        }
        charges[i] = delta / deg;
        if (charges[i] < min) min = charges[i];
        if (charges[i] > max) max = charges[i];
    }

    if (!Number.isFinite(min)) return null;
    return { charges, min, max };
}

/**
 * Compute SMILES and InChI canonical identifiers for the ligand. Used by
 * the Ligand panel's export field for copy-paste into external tools
 * (chemdraw, external databases, etc.).
 *
 * Returns null if RDKit fails to parse the molfile.
 */
export async function computeLigandIdentifiers(molfile: string): Promise<{
    smiles: string;
    inchi: string;
    inchiKey: string;
    cxsmiles: string;
} | null> {
    const RDKit = await getRDKit();
    const mol = RDKit.get_mol(molfile);
    if (!mol || !mol.is_valid()) return null;
    try {
        const inchi = tryOrDefault(() => mol.get_inchi());
        const inchiKey = inchi ? tryOrDefault(() => RDKit.get_inchikey_for_inchi(inchi)) : '';
        return {
            smiles: mol.get_smiles(),
            cxsmiles: tryOrDefault(() => mol.get_cxsmiles()),
            inchi,
            inchiKey,
        };
    } finally {
        mol.delete();
    }
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

        // Stereo (CIP) via get_stereo_tags. Format:
        //   {"CIP_atoms": [[atomIdx, "(R)" | "(S)" | "(?)"], ...], "CIP_bonds": [...]}
        const chiralCenters = new Map<number, 'R' | 'S' | '?'>();
        try {
            const stereoRaw = mol.get_stereo_tags();
            const parsed = JSON.parse(stereoRaw) as { CIP_atoms?: Array<[number, string]> };
            if (Array.isArray(parsed.CIP_atoms)) {
                for (const [idx, label] of parsed.CIP_atoms) {
                    if (typeof idx !== 'number' || idx < 0 || idx >= atomCount) continue;
                    const c = label.replace(/[()]/g, '');
                    if (c === 'R' || c === 'S' || c === '?') chiralCenters.set(idx, c);
                }
            }
        } catch { /* stereo unavailable */ }

        const ringAtoms = smartsAtomIndices(RDKit, mol, '[R]', atomCount);
        // [CX4] = carbon with 4 explicit single bonds → sp3-hybridized
        const sp3Carbons = smartsAtomIndices(RDKit, mol, '[CX4]', atomCount);

        // Reactive groups: top-10 most common assay-interference / covalent-reactive
        // substructures. Each is a separate SMARTS match; union into one flag array
        // and collect readable labels for the availability badge.
        const REACTIVE_GROUPS: Array<[string, string]> = [
            ['aldehyde',         '[CX3H1](=O)[#6]'],
            ['Michael acceptor', '[$([CX3]=[CX3]);![$(*#[#6])]]'],
            ['acyl halide',      '[CX3](=O)[F,Cl,Br,I]'],
            ['alkyl halide',     '[CX4][F,Cl,Br,I]'],
            ['nitro',            '[$([NX3](=O)=O)]'],
            ['disulfide',        '[SX2][SX2]'],
            ['peroxide',         '[OX2][OX2]'],
            ['azide',            '[NX2]=[NX2]=[NX1]'],
            ['epoxide',          '[OX2r3]'],
            ['isocyanate',       '[NX2]=[CX1]=[OX1]'],
        ];
        const reactiveGroups = new Uint8Array(atomCount);
        const reactiveGroupLabels: string[] = [];
        for (const [name, smarts] of REACTIVE_GROUPS) {
            const flags = smartsAtomIndices(RDKit, mol, smarts, atomCount);
            let found = false;
            for (let i = 0; i < atomCount; i++) {
                if (flags[i]) { reactiveGroups[i] = 1; found = true; }
            }
            if (found) reactiveGroupLabels.push(name);
        }

        // Bond orders from the molfile bond block (V2000 format).
        // Each bond line: a1(3) a2(3) order(3) ... where order is 1/2/3/4(aromatic).
        const doubleBondAtoms = new Uint8Array(atomCount);
        const tripleBondAtoms = new Uint8Array(atomCount);
        const mbLines = molfile.split('\n');
        const countsLine = mbLines[3] || '';
        const nAtoms = parseInt(countsLine.slice(0, 3).trim(), 10) || 0;
        const nBonds = parseInt(countsLine.slice(3, 6).trim(), 10) || 0;
        for (let b = 0; b < nBonds; b++) {
            const ln = mbLines[4 + nAtoms + b] || '';
            const a1 = parseInt(ln.slice(0, 3).trim(), 10) - 1;
            const a2 = parseInt(ln.slice(3, 6).trim(), 10) - 1;
            const order = parseInt(ln.slice(6, 9).trim(), 10);
            if (order === 2) { if (a1 >= 0 && a1 < atomCount) doubleBondAtoms[a1] = 1; if (a2 >= 0 && a2 < atomCount) doubleBondAtoms[a2] = 1; }
            if (order === 3) { if (a1 >= 0 && a1 < atomCount) tripleBondAtoms[a1] = 1; if (a2 >= 0 && a2 < atomCount) tripleBondAtoms[a2] = 1; }
        }

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

        // Fallback: Allred-Rochow electronegativity approximation when Gasteiger
        // is unavailable (RDKit-JS 2025.03.4 build doesn't expose the API).
        // δ_i = Σ_j (EN_i - EN_j) / degree_i. Not as accurate as Gasteiger-Marsili
        // but captures the electrostatic character: electronegative atoms (O, N, F)
        // go negative, electropositive (metals) go positive.
        if (!partialCharges) {
            const approx = approximatePartialCharges(molfile, atomCount);
            if (approx) {
                partialCharges = approx.charges;
                partialChargeMin = approx.min;
                partialChargeMax = approx.max;
            }
        }

        return {
            aromaticAtoms: aromatic,
            donors,
            acceptors,
            aromaticRings,
            partialCharges,
            partialChargeMin,
            partialChargeMax,
            chiralCenters,
            ringAtoms,
            sp3Carbons,
            reactiveGroups,
            reactiveGroupLabels,
            doubleBondAtoms,
            tripleBondAtoms,
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
        return { hasLigand: false, atomCount: 0, aromatic: 0, donors: 0, acceptors: 0, partialChargeRange: null, chiralCentersR: 0, chiralCentersS: 0, chiralCentersUndefined: 0, ringAtoms: 0, sp3Carbons: 0, reactiveGroups: [], doubleBondAtoms: 0, tripleBondAtoms: 0 };
    }
    const build = ligandLociToMolfile(loci);
    if (!build) {
        return { hasLigand: true, atomCount: StructureElement.Loci.size(loci), aromatic: 0, donors: 0, acceptors: 0, partialChargeRange: null, chiralCentersR: 0, chiralCentersS: 0, chiralCentersUndefined: 0, ringAtoms: 0, sp3Carbons: 0, reactiveGroups: [], doubleBondAtoms: 0, tripleBondAtoms: 0 };
    }
    const chem = await computeLigandChemistry(build.molfile, build.atomCount);
    if (!chem) {
        return { hasLigand: true, atomCount: build.atomCount, aromatic: 0, donors: 0, acceptors: 0, partialChargeRange: null, chiralCentersR: 0, chiralCentersS: 0, chiralCentersUndefined: 0, ringAtoms: 0, sp3Carbons: 0, reactiveGroups: [], doubleBondAtoms: 0, tripleBondAtoms: 0 };
    }
    const range = chem.partialCharges ? ([chem.partialChargeMin, chem.partialChargeMax] as const) : null;
    let chirR = 0, chirS = 0, chirU = 0;
    for (const c of chem.chiralCenters.values()) {
        if (c === 'R') chirR++; else if (c === 'S') chirS++; else chirU++;
    }
    return {
        hasLigand: true,
        atomCount: build.atomCount,
        aromatic: countSetBits(chem.aromaticAtoms),
        donors: countSetBits(chem.donors),
        acceptors: countSetBits(chem.acceptors),
        partialChargeRange: range,
        chiralCentersR: chirR,
        chiralCentersS: chirS,
        chiralCentersUndefined: chirU,
        ringAtoms: countSetBits(chem.ringAtoms),
        sp3Carbons: countSetBits(chem.sp3Carbons),
        reactiveGroups: chem.reactiveGroupLabels,
        doubleBondAtoms: countSetBits(chem.doubleBondAtoms),
        tripleBondAtoms: countSetBits(chem.tripleBondAtoms),
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

    if (enabledIds.has('stereo-rdkit')) {
        // R = blue, S = red, ? = yellow. Three orthogonal channels, three colors.
        const rIndices = new Set<number>();
        const sIndices = new Set<number>();
        const undefIndices = new Set<number>();
        for (const [idx, c] of chem.chiralCenters) {
            if (c === 'R') rIndices.add(idx);
            else if (c === 'S') sIndices.add(idx);
            else undefIndices.add(idx);
        }
        if (rIndices.size) {
            const filtered = filterLociByAtomIndex(loci, i => rIndices.has(i));
            if (!StructureElement.Loci.isEmpty(filtered)) out.push({ loci: filtered, color: Color(0x5a8ae4) });
        }
        if (sIndices.size) {
            const filtered = filterLociByAtomIndex(loci, i => sIndices.has(i));
            if (!StructureElement.Loci.isEmpty(filtered)) out.push({ loci: filtered, color: Color(0xe15555) });
        }
        if (undefIndices.size) {
            const filtered = filterLociByAtomIndex(loci, i => undefIndices.has(i));
            if (!StructureElement.Loci.isEmpty(filtered)) out.push({ loci: filtered, color: Color(0xd4a574) });
        }
    }

    if (enabledIds.has('ring-atoms-rdkit')) {
        const filtered = filterLociByAtomIndex(loci, i => chem.ringAtoms[i] === 1);
        if (!StructureElement.Loci.isEmpty(filtered)) out.push({ loci: filtered, color: Color(0x9d8cd4) });
    }

    if (enabledIds.has('sp3-carbons-rdkit')) {
        const filtered = filterLociByAtomIndex(loci, i => chem.sp3Carbons[i] === 1);
        if (!StructureElement.Loci.isEmpty(filtered)) out.push({ loci: filtered, color: Color(0x7dd3c0) });
    }

    if (enabledIds.has('reactive-groups-rdkit') && chem.reactiveGroupLabels.length > 0) {
        const filtered = filterLociByAtomIndex(loci, i => chem.reactiveGroups[i] === 1);
        if (!StructureElement.Loci.isEmpty(filtered)) out.push({ loci: filtered, color: Color(0xff4444) });
    }

    if (enabledIds.has('bond-order-rdkit')) {
        const dblLoci = filterLociByAtomIndex(loci, i => chem.doubleBondAtoms[i] === 1);
        if (!StructureElement.Loci.isEmpty(dblLoci)) out.push({ loci: dblLoci, color: Color(0x4dabf7) });
        const triLoci = filterLociByAtomIndex(loci, i => chem.tripleBondAtoms[i] === 1);
        if (!StructureElement.Loci.isEmpty(triLoci)) out.push({ loci: triLoci, color: Color(0xa06ec9) });
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

// === Interactive SMARTS substructure search ===

const SmartsSearchTag = 'rdkit-smarts-search';

export interface SmartsSearchResult {
    valid: boolean;
    error?: string;
    matchAtomIndices: Uint8Array;   // length = atomCount; 1 if atom is in any match
    matchCount: number;             // number of distinct matches
}

/**
 * Validate a SMARTS string and find matches in the given ligand molfile.
 * Returns { valid: false, error } if SMARTS is invalid; otherwise the
 * per-atom match flags + match count.
 *
 * Used by the interactive SMARTS search input in the Ligand panel.
 * Atom-index contract: matches return indices into the molfile's atom list,
 * identical to the rest of the substrate.
 */
export async function searchLigandSmarts(molfile: string, smarts: string): Promise<SmartsSearchResult | null> {
    if (!smarts.trim()) {
        return { valid: true, matchAtomIndices: new Uint8Array(0), matchCount: 0 };
    }
    const RDKit = await getRDKit();
    const mol = RDKit.get_mol(molfile);
    if (!mol || !mol.is_valid()) return null;
    let qmol: JSMol | null = null;
    try {
        qmol = RDKit.get_qmol(smarts);
        if (!qmol) return { valid: false, error: 'Invalid SMARTS syntax', matchAtomIndices: new Uint8Array(0), matchCount: 0 };

        // Molfile atom count from line 4 (V2000 counts line).
        const countsLine = molfile.split('\n')[3] || '';
        const atomCount = parseInt(countsLine.slice(0, 3).trim(), 10) || 0;
        if (atomCount === 0) return { valid: true, matchAtomIndices: new Uint8Array(0), matchCount: 0 };

        const raw = mol.get_substruct_matches(qmol);
        const parsed = JSON.parse(raw) as unknown;
        const flags = new Uint8Array(atomCount);
        let matchCount = 0;
        if (Array.isArray(parsed)) {
            for (const m of parsed as Array<{ atoms?: number[] }>) {
                if (!m.atoms) continue;
                matchCount++;
                for (const idx of m.atoms) {
                    if (typeof idx === 'number' && idx >= 0 && idx < atomCount) flags[idx] = 1;
                }
            }
        }
        return { valid: true, matchAtomIndices: flags, matchCount };
    } catch (e) {
        return { valid: false, error: e instanceof Error ? e.message : String(e), matchAtomIndices: new Uint8Array(0), matchCount: 0 };
    } finally {
        if (qmol) qmol.delete();
        mol.delete();
    }
}

/**
 * Apply (or remove) a SMARTS search Overpaint overlay on the focused ligand.
 * Pass `null` as result to clear. Caller is responsible for the debouncing;
 * this function applies immediately.
 */
export async function applySmartsSearchOverlay(
    plugin: PluginContext,
    options: LigandFocusOptions,
    result: SmartsSearchResult | null,
): Promise<void> {
    const state = plugin.state.data;
    const update = state.build();

    // Always remove this module's overlay first; re-add only if result has matches.
    for (const cell of state.select(
        StateSelection.Generators.ofTransformer(
            StateTransforms.Representation.OverpaintStructureRepresentation3DFromBundle,
        ).withTag(SmartsSearchTag),
    )) {
        update.delete(cell);
    }

    if (!result || !result.valid || result.matchCount === 0) {
        await update.commit({ doNotUpdateCurrent: true });
        return;
    }

    const structure = plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
    if (!structure) {
        await update.commit({ doNotUpdateCurrent: true });
        return;
    }
    const loci = lociFromFocusOptions(structure, options);
    if (StructureElement.Loci.isEmpty(loci)) {
        await update.commit({ doNotUpdateCurrent: true });
        return;
    }

    const matched = filterLociByAtomIndex(loci, i => result.matchAtomIndices[i] === 1);
    if (StructureElement.Loci.isEmpty(matched)) {
        await update.commit({ doNotUpdateCurrent: true });
        return;
    }

    for (const structureRef of plugin.managers.structure.component.currentStructures) {
        for (const component of structureRef.components) {
            for (const representation of component.representations) {
                const repr = representation.cell;
                const sourceData = repr.obj?.data.sourceData;
                if (!sourceData) continue;
                update.to(repr.transform.ref).apply(
                    StateTransforms.Representation.OverpaintStructureRepresentation3DFromBundle,
                    Overpaint.toBundle(Overpaint.filter(Overpaint.ofBundle([
                        { bundle: StructureElement.Bundle.fromLoci(matched), color: Color(0xff6b35), clear: false },
                    ], sourceData.root), sourceData) as Overpaint<StructureElement.Loci>),
                    { tags: SmartsSearchTag },
                );
            }
        }
    }

    await update.commit({ doNotUpdateCurrent: true });
}
