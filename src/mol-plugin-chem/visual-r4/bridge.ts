import { OrderedSet } from '../../mol-data/int';
import { Vec3 } from '../../mol-math/linear-algebra';
import { VdwRadius } from '../../mol-model/structure/model/properties/atomic';
import { Structure, StructureElement, Unit } from '../../mol-model/structure';
import type { R4AtomRef, R4BondRef, R4StructureSnapshot } from './types';

const ElementColors: Record<string, readonly [number, number, number]> = {
    H: [0.92, 0.92, 0.92], C: [0.22, 0.25, 0.29], N: [0.18, 0.42, 0.95],
    O: [0.92, 0.16, 0.18], F: [0.3, 0.82, 0.34], P: [1.0, 0.5, 0.1],
    S: [0.95, 0.78, 0.12], CL: [0.2, 0.8, 0.25], BR: [0.62, 0.16, 0.12],
    I: [0.42, 0.12, 0.62], FE: [0.82, 0.36, 0.13], ZN: [0.48, 0.5, 0.68],
};

function atomKey(unitId: number, unitIndex: number) {
    return `${unitId}:${unitIndex}`;
}

export function buildR4StructureSnapshot(structure: Structure): R4StructureSnapshot {
    const positions: number[] = [];
    const radii: number[] = [];
    const colors: number[] = [];
    const atoms: R4AtomRef[] = [];
    const bonds: R4BondRef[] = [];
    const atomIndices = new Map<string, number>();

    for (const unit of structure.units) {
        if (!Unit.isAtomic(unit)) continue;
        const { atoms: atomTable, residues, chains, residueAtomSegments, chainAtomSegments } = unit.model.atomicHierarchy;
        for (let unitIndex = 0; unitIndex < unit.elements.length; unitIndex++) {
            const element = unit.elements[unitIndex];
            const position = unit.conformation.position(element, Vec3());
            const elementSymbol = atomTable.type_symbol.value(element).toUpperCase();
            const residueIndex = residueAtomSegments.index[element];
            const chainIndex = chainAtomSegments.index[element];
            const color = ElementColors[elementSymbol] ?? [0.62, 0.68, 0.74];
            const index = atoms.length;

            positions.push(position[0], position[1], position[2]);
            radii.push(VdwRadius(elementSymbol as any));
            colors.push(color[0], color[1], color[2]);
            atomIndices.set(atomKey(unit.id, unitIndex), index);
            atoms.push({
                pickId: index + 1,
                unit,
                unitIndex: unitIndex as StructureElement.UnitIndex,
                element,
                elementSymbol,
                atomName: atomTable.label_atom_id.value(element),
                residueName: atomTable.label_comp_id.value(element),
                residueId: residues.label_seq_id.value(residueIndex),
                chainId: chains.label_asym_id.value(chainIndex),
                moleculeType: unit.model.atomicHierarchy.derived.residue.moleculeType[residueIndex],
            });
        }
    }

    for (const unit of structure.units) {
        if (!Unit.isAtomic(unit)) continue;
        const { a, b, edgeProps } = unit.bonds;
        for (let edge = 0; edge < a.length; edge++) {
            const unitIndexA = a[edge];
            const unitIndexB = b[edge];
            if (unitIndexA >= unitIndexB) continue;
            const atomA = atomIndices.get(atomKey(unit.id, unitIndexA));
            const atomB = atomIndices.get(atomKey(unit.id, unitIndexB));
            if (atomA === undefined || atomB === undefined) continue;
            bonds.push({
                pickId: bonds.length + 1,
                atomA,
                atomB,
                order: edgeProps.order[edge],
                flags: edgeProps.flags[edge],
            });
        }
    }

    for (const edge of structure.interUnitBonds.edges) {
        const atomA = atomIndices.get(atomKey(edge.unitA, edge.indexA));
        const atomB = atomIndices.get(atomKey(edge.unitB, edge.indexB));
        if (atomA === undefined || atomB === undefined) continue;
        bonds.push({
            pickId: bonds.length + 1,
            atomA,
            atomB,
            order: edge.props.order,
            flags: edge.props.flag,
        });
    }

    return {
        structure,
        positions: Float32Array.from(positions),
        radii: Float32Array.from(radii),
        colors: Float32Array.from(colors),
        atoms,
        bonds,
    };
}

export function atomLoci(snapshot: R4StructureSnapshot, atomIndex: number) {
    const atom = snapshot.atoms[atomIndex];
    if (!atom) return StructureElement.Loci.none(snapshot.structure);
    return StructureElement.Loci(snapshot.structure, [{
        unit: atom.unit,
        indices: OrderedSet.ofSingleton(atom.unitIndex),
    }]);
}

export function bondLoci(snapshot: R4StructureSnapshot, bondIndex: number) {
    const bond = snapshot.bonds[bondIndex];
    if (!bond) return StructureElement.Loci.none(snapshot.structure);
    const atomA = snapshot.atoms[bond.atomA];
    const atomB = snapshot.atoms[bond.atomB];
    if (atomA.unit === atomB.unit) {
        return StructureElement.Loci(snapshot.structure, [{
            unit: atomA.unit,
            indices: OrderedSet.ofSortedArray([atomA.unitIndex, atomB.unitIndex].sort((a, b) => a - b) as any),
        }]);
    }
    return StructureElement.Loci(snapshot.structure, [
        { unit: atomA.unit, indices: OrderedSet.ofSingleton(atomA.unitIndex) },
        { unit: atomB.unit, indices: OrderedSet.ofSingleton(atomB.unitIndex) },
    ]);
}
