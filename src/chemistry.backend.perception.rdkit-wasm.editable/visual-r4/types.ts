import type { ElementIndex, Structure, StructureElement, Unit } from '../../mol-model/structure';
import type { MoleculeType } from '../../mol-model/structure/model/types';

export type R4RepresentationKind =
    | 'cartoon'
    | 'ribbon'
    | 'spheres'
    | 'sticks'
    | 'ball-and-stick'
    | 'surface'
    | 'nucleic'
    | 'density'
    | 'ensemble'
    | 'annotations';

export type R4MaterialKind =
    | 'default'
    | 'ambient-occlusion'
    | 'flat-outline'
    | 'squishy'
    | 'transparent-outline';

export type R4Operator =
    | 'source.structure'
    | 'select'
    | 'separate-polymers'
    | 'atoms-to-curves'
    | 'secondary-structure'
    | 'curve-profile'
    | 'curve-to-mesh'
    | 'instance-spheres'
    | 'build-bonds'
    | 'surface-density'
    | 'surface-mesh'
    | 'surface-relax'
    | 'sample-colors'
    | 'nucleic-bases'
    | 'density-volume'
    | 'ensemble-instances'
    | 'annotations'
    | 'material'
    | 'join';

export type R4Input = { readonly node: string, readonly output?: string } | { readonly value: unknown };

export interface R4GraphNode {
    readonly id: string;
    readonly operator: R4Operator;
    readonly inputs?: Readonly<Record<string, R4Input>>;
    readonly parameters?: Readonly<Record<string, unknown>>;
}

export interface R4RepresentationGraph {
    readonly id: string;
    readonly label: string;
    readonly nodes: readonly R4GraphNode[];
    readonly outputs: Readonly<Record<string, R4Input>>;
}

export interface R4AtomRef {
    readonly pickId: number;
    readonly unit: Unit.Atomic;
    readonly unitIndex: StructureElement.UnitIndex;
    readonly element: ElementIndex;
    readonly elementSymbol: string;
    readonly atomName: string;
    readonly residueName: string;
    readonly residueId: number;
    readonly chainId: string;
    readonly moleculeType: MoleculeType;
}

export interface R4BondRef {
    readonly pickId: number;
    readonly atomA: number;
    readonly atomB: number;
    readonly order: number;
    readonly flags: number;
}

export interface R4StructureSnapshot {
    readonly structure: Structure;
    readonly positions: Float32Array;
    readonly radii: Float32Array;
    readonly colors: Float32Array;
    readonly atoms: readonly R4AtomRef[];
    readonly bonds: readonly R4BondRef[];
}

export interface R4Annotation {
    readonly id: string;
    readonly label: string;
    readonly atomIndex?: number;
    readonly position?: readonly [number, number, number];
    readonly color?: number;
}

export interface R4GraphExecutionContext {
    readonly snapshot: R4StructureSnapshot;
    readonly values: ReadonlyMap<string, unknown>;
}

export type R4OperatorHandler = (
    context: R4GraphExecutionContext,
    node: R4GraphNode,
    inputs: Readonly<Record<string, unknown>>,
) => unknown | Promise<unknown>;
