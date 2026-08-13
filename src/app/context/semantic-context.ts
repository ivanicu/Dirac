import type { ObjectRef } from '../domain/object-ref';

export type SelectionScope = 'transient' | 'named' | 'comparison' | 'bulk';
export type SelectionLocality = 'workspace' | 'cross-workspace';
export type SelectionLifecycle = 'active' | 'stale' | 'deleted' | 'unauthorized' | 'superseded';

interface SelectionBase {
    readonly scope: SelectionScope;
    readonly locality: SelectionLocality;
    readonly lifecycle: SelectionLifecycle;
    readonly size: number;
    readonly version: number;
}

export interface ObjectSelection extends SelectionBase {
    readonly kind: 'object';
    readonly refs: readonly ObjectRef[];
    readonly sourceVersions: Readonly<Record<string, number>>;
}

export interface StructureSelection extends SelectionBase {
    readonly kind: 'structure';
    readonly structureRef: ObjectRef<'protein_structure'> | ObjectRef<'complex'>;
    readonly structureVersion: number;
    readonly model: string;
    readonly assembly?: string;
    readonly chain?: string;
    readonly residues?: readonly string[];
    readonly atoms?: readonly number[];
    readonly altloc?: string;
    readonly snapshotRef?: ObjectRef<'analysis_snapshot'>;
}

export interface MolecularSelection extends SelectionBase {
    readonly kind: 'molecular';
    readonly moleculeRef: ObjectRef<'molecule'>;
    readonly atomIndices?: readonly number[];
    readonly bondIndices?: readonly number[];
    readonly representationVersion: number;
}

export interface MaterialQuantitySelection extends SelectionBase {
    readonly kind: 'material-quantity';
    readonly sampleRef: ObjectRef<'sample'>;
    readonly quantity: number;
    readonly unit: 'g' | 'mg' | 'ug' | 'mol' | 'mmol' | 'umol' | 'l' | 'ml' | 'ul';
    readonly sampleVersion: number;
    readonly reservationRef?: string;
}

export interface PlateWellSelection extends SelectionBase {
    readonly kind: 'plate-well';
    readonly plateRef: string;
    readonly wells: readonly string[];
    readonly layoutVersion: number;
}

export interface DatasetSliceSelection extends SelectionBase {
    readonly kind: 'dataset-slice';
    readonly datasetVersionRef: ObjectRef<'dataset_version'>;
    readonly filterAst: Readonly<Record<string, unknown>>;
    readonly projection: readonly string[];
    readonly digest: string;
}

export interface DerivedSetSelection extends SelectionBase {
    readonly kind: 'derived-set';
    readonly definitionRef: string;
    readonly memberDigest: string;
    readonly sourceVersions: Readonly<Record<string, number>>;
}

export type SelectionRef = ObjectSelection | StructureSelection | MolecularSelection
    | MaterialQuantitySelection | PlateWellSelection | DatasetSliceSelection
    | DerivedSetSelection;

export type SemanticContextOrigin = 'navigation' | 'selection' | 'import' | 'action' | 'restore';

export interface SemanticScientificContext {
    readonly tenantId: string;
    readonly programRef?: ObjectRef<'program'>;
    readonly workThreadId?: string;
    readonly questionRef?: string;
    readonly objectPath: readonly ObjectRef[];
    readonly focus?: ObjectRef;
    readonly selection?: SelectionRef;
    readonly comparison?: SelectionRef;
    readonly sourceVersions: Readonly<Record<string, number>>;
    readonly permissionSnapshot: string;
    readonly contextHandle?: string;
    readonly origin: SemanticContextOrigin;
    readonly generation: number;
}

export type ContextTransition =
    | { readonly kind: 'tenant'; readonly tenantId: string; readonly permissionSnapshot: string }
    | { readonly kind: 'program'; readonly programRef?: ObjectRef<'program'> }
    | { readonly kind: 'work-thread'; readonly workThreadId?: string; readonly reachable: readonly string[] }
    | { readonly kind: 'focus'; readonly focus?: ObjectRef; readonly objectPath?: readonly ObjectRef[] }
    | { readonly kind: 'selection'; readonly selection?: SelectionRef }
    | { readonly kind: 'comparison'; readonly comparison?: SelectionRef }
    | { readonly kind: 'permission-revoked'; readonly objectKeys: readonly string[]; readonly permissionSnapshot: string }
    | { readonly kind: 'source-changed'; readonly objectKey: string; readonly version: number };

const key = (ref: ObjectRef): string => `${ref.kind}:${ref.id}`;
const selectionKeys = (selection?: SelectionRef): readonly string[] => {
    if (!selection) return [];
    switch (selection.kind) {
        case 'object': return selection.refs.map(key);
        case 'structure': return [key(selection.structureRef)];
        case 'molecular': return [key(selection.moleculeRef)];
        case 'material-quantity': return [key(selection.sampleRef)];
        case 'dataset-slice': return [key(selection.datasetVersionRef)];
        case 'plate-well': return [selection.plateRef];
        case 'derived-set': return [selection.definitionRef];
    }
};

const markSelection = (selection: SelectionRef | undefined,
                       lifecycle: SelectionLifecycle): SelectionRef | undefined =>
    selection ? { ...selection, lifecycle } : undefined;

/** Pure transition policy. Labels and authorization are resolved outside this state. */
export function transitionScientificContext(
    current: SemanticScientificContext,
    transition: ContextTransition,
): SemanticScientificContext {
    let next: SemanticScientificContext;
    switch (transition.kind) {
        case 'tenant':
            next = {
                tenantId: transition.tenantId,
                objectPath: [], sourceVersions: {},
                permissionSnapshot: transition.permissionSnapshot,
                origin: 'navigation', generation: current.generation,
            };
            break;
        case 'program':
            next = {
                ...current, programRef: transition.programRef,
                workThreadId: undefined, questionRef: undefined, objectPath: [],
                focus: undefined, selection: undefined, comparison: undefined,
                sourceVersions: {}, contextHandle: undefined, origin: 'navigation',
            };
            break;
        case 'work-thread': {
            const reachable = new Set(transition.reachable);
            const keep = (selection?: SelectionRef) => selection
                && selectionKeys(selection).every(item => reachable.has(item)) ? selection : undefined;
            next = {
                ...current, workThreadId: transition.workThreadId,
                focus: current.focus && reachable.has(key(current.focus)) ? current.focus : undefined,
                objectPath: current.objectPath.filter(item => reachable.has(key(item))),
                selection: keep(current.selection), comparison: keep(current.comparison),
                contextHandle: undefined, origin: 'navigation',
            };
            break;
        }
        case 'focus':
            next = { ...current, focus: transition.focus,
                objectPath: transition.objectPath || current.objectPath,
                contextHandle: undefined, origin: 'selection' };
            break;
        case 'selection':
            next = { ...current, selection: transition.selection,
                contextHandle: undefined, origin: 'selection' };
            break;
        case 'comparison':
            next = { ...current, comparison: transition.comparison,
                contextHandle: undefined, origin: 'selection' };
            break;
        case 'permission-revoked': {
            const revoked = new Set(transition.objectKeys);
            const affected = (selection?: SelectionRef) =>
                selectionKeys(selection).some(item => revoked.has(item));
            next = {
                ...current,
                focus: current.focus && revoked.has(key(current.focus)) ? undefined : current.focus,
                objectPath: current.objectPath.filter(item => !revoked.has(key(item))),
                selection: affected(current.selection)
                    ? markSelection(current.selection, 'unauthorized') : current.selection,
                comparison: affected(current.comparison)
                    ? markSelection(current.comparison, 'unauthorized') : current.comparison,
                permissionSnapshot: transition.permissionSnapshot,
                contextHandle: undefined, origin: 'action',
            };
            break;
        }
        case 'source-changed': {
            const affects = (selection?: SelectionRef) =>
                selectionKeys(selection).includes(transition.objectKey);
            next = {
                ...current,
                sourceVersions: { ...current.sourceVersions,
                    [transition.objectKey]: transition.version },
                selection: affects(current.selection)
                    ? markSelection(current.selection, 'stale') : current.selection,
                comparison: affects(current.comparison)
                    ? markSelection(current.comparison, 'stale') : current.comparison,
                contextHandle: undefined, origin: 'action',
            };
            break;
        }
    }
    return Object.freeze({ ...next, generation: current.generation + 1 });
}

/** Shareable links carry only an opaque, server-issued handle. */
export function shareableContextParams(context: SemanticScientificContext): URLSearchParams {
    const params = new URLSearchParams();
    if (context.contextHandle) params.set('ctx', context.contextHandle);
    return params;
}
