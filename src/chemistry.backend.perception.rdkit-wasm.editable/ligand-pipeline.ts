/**
 * Shared ligand-pipeline utilities — the ONE copy of each function that was
 * previously duplicated across semantic-chemistry-rdkit.ts and
 * pharmacophore-features.ts.
 *
 * S0 exit test: `rg -l 'function ligandLociToMolfile|function parseLigandLoci|function lociFromFocusOptions' src/chemistry*` must return ONLY this file.
 */

import { Structure, StructureElement, Unit, StructureProperties, StructureSelection, QueryContext } from '../mol-model/structure';
import { OrderedSet } from '../mol-data/int';
import { Vec3 } from '../mol-math/linear-algebra';
import { ComponentBond } from '../mol-model-formats/structure/property/bonds/chem_comp';
import { StructureSelectionQueries } from '../mol-plugin-state/helpers/structure-selection-query';
import type { LigandFocusOptions } from './semantic-focus';

// === lociFromFocusOptions — the ONE copy ===

export function lociFromFocusOptions(structure: Structure, options: LigandFocusOptions): StructureElement.Loci {
    if (options.target && options.target.hash === structure.hashCode) {
        return StructureElement.Bundle.toLoci(options.target, structure);
    }
    const selection = StructureSelectionQueries.ligand.query(new QueryContext(structure.root));
    return StructureSelection.toLociWithCurrentUnits(selection);
}

// === ligandLociToMolfile — the ONE copy ===

export interface MolfileBuild {
    molfile: string;
    atomCount: number;
    bondCount: number;
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

export function ligandLociToMolfile(loci: StructureElement.Loci): MolfileBuild | null {
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
            atoms.push({
                element: StructureProperties.atom.type_symbol(location),
                x: 0, y: 0, z: 0, // filled below
                charge: StructureProperties.atom.pdbx_formal_charge(location) || 0,
                name: StructureProperties.atom.label_atom_id(location),
                compId: StructureProperties.residue.label_comp_id(location),
            });
            unit.conformation.position(location.element, position);
            atoms[atoms.length - 1].x = position[0];
            atoms[atoms.length - 1].y = position[1];
            atoms[atoms.length - 1].z = position[2];
        }
    }

    if (atoms.length === 0 || atoms.length > 999) return null;

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

    const lines: string[] = ['', '  mol*', ''];
    lines.push(countsLine(atoms.length, bonds.length));
    for (const a of atoms) {
        lines.push(
            a.x.toFixed(4).padStart(10, ' ')
            + a.y.toFixed(4).padStart(10, ' ')
            + a.z.toFixed(4).padStart(10, ' ')
            + ' ' + a.element.padEnd(2, ' ')
            + '  0 ' + mapChargeToMolfileCode(a.charge).toString().padStart(3, ' ')
            + '  0  0  0  0  0  0  0  0  0  0'
        );
    }
    for (const b of bonds) {
        lines.push(
            (b.a1 + 1).toString().padStart(3, ' ')
            + (b.a2 + 1).toString().padStart(3, ' ')
            + b.order.toString().padStart(3, ' ')
            + '  0  0  0  0'
        );
    }
    lines.push('M  END');

    return { molfile: lines.join('\n'), atomCount: atoms.length, bondCount: bonds.length };
}

/**
 * THE V2000 counts line. One writer, because this exact line has now been
 * written wrong three times (fixed 2026-08-10 in two builders, then
 * reintroduced by the pipeline dedup that merged them). The spec allows eight
 * zero fields between the bond count and 999; a ninth pushes ' V2000' off its
 * fixed column. RDKit-JS forgives that, RDKit's C++ MolFromMolBlock does not —
 * so the browser panels stay green while every field in the Python daemon dies
 * with "CTAB version string invalid at line 4".
 *
 * >999 atoms cannot be expressed in V2000's 3-column count field; a silent
 * overflow corrupts the same line, so it throws instead.
 * Contract test: `_spec/ligand-pipeline.spec.ts` (violation witness).
 */
export function countsLine(atomCount: number, bondCount: number): string {
    if (atomCount > 999 || bondCount > 999) {
        throw new Error(
            `V2000 cannot express ${atomCount} atoms / ${bondCount} bonds ` +
            '(3-column fields); the selection is larger than one ligand');
    }
    return atomCount.toString().padStart(3, ' ')
        + bondCount.toString().padStart(3, ' ')
        + '  0  0  0  0  0  0  0  0999 V2000';
}

// === filterLociByAtomIndex — the ONE copy ===

export function filterLociByAtomIndex(
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
                indices: OrderedSet.ofSortedArray<StructureElement.UnitIndex>(kept.sort((a, b) => a - b) as unknown as ArrayLike<StructureElement.UnitIndex>),
            });
        }
    }
    return StructureElement.Loci(loci.structure, newElements);
}

// === LigandAtomData — richer extraction for pharmacophore features ===

export interface LigandAtomData {
    atomPositions: Vec3[];
    atomElements: string[];
    atomNames: string[];
    bonds: Array<{ a1: number; a2: number; order: number }>;
}

export function extractLigandAtomData(loci: StructureElement.Loci): LigandAtomData | null {
    if (StructureElement.Loci.isEmpty(loci)) return null;
    const structure = loci.structure;
    const model = structure.models[0];
    const bondData = ComponentBond.Provider.get(model);
    if (!bondData) return null;

    const atomPositions: Vec3[] = [];
    const atomElements: string[] = [];
    const atomNames: string[] = [];
    const position = Vec3();
    const location = StructureElement.Location.create(structure);
    let compId = '';

    for (const e of loci.elements) {
        if (!Unit.isAtomic(e.unit)) continue;
        const count = OrderedSet.size(e.indices);
        for (let i = 0; i < count; i++) {
            const unitIndex = OrderedSet.getAt(e.indices, i);
            location.unit = e.unit;
            location.element = e.unit.elements[unitIndex];
            if (!compId) compId = StructureProperties.residue.label_comp_id(location);
            atomElements.push(StructureProperties.atom.type_symbol(location));
            atomNames.push(StructureProperties.atom.label_atom_id(location));
            e.unit.conformation.position(location.element, position);
            atomPositions.push(Vec3.clone(position));
        }
    }
    if (atomPositions.length === 0) return null;

    const nameToIdx = new Map<string, number>();
    for (let i = 0; i < atomNames.length; i++) nameToIdx.set(atomNames[i], i);

    const bonds: Array<{ a1: number; a2: number; order: number }> = [];
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

    return { atomPositions, atomElements, atomNames, bonds };
}
