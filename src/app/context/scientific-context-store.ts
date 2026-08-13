import { OBJECT_KINDS, type ObjectKind } from '../generated/commands';
import type { ObjectRef } from '../domain/object-ref';
import { sameObject } from '../domain/object-ref';

export type ContextOrigin = 'navigation' | 'selection' | 'import' | 'command' | 'restore';

export interface ScientificContext {
    readonly programRef?: ObjectRef<'program'>;
    readonly workItemRef?: ObjectRef<'work_item'>;
    readonly complexRef?: ObjectRef<'complex'>;
    readonly moleculeRef?: ObjectRef<'molecule'>;
    readonly compoundRef?: ObjectRef<'compound'>;
    readonly sampleRef?: ObjectRef<'sample'>;
    readonly experimentRef?: ObjectRef<'experiment'>;
    readonly datasetVersionRef?: ObjectRef<'dataset_version'>;
    readonly moleculeSmiles?: string;
    readonly focusedObject?: ObjectRef;
    readonly selectedObjects: readonly ObjectRef[];
    readonly targetRef?: ObjectRef<'target'>;
    readonly campaignRef?: ObjectRef<'campaign'>;
    readonly seriesRef?: ObjectRef<'series'>;
    readonly activeHypotheses: readonly ObjectRef<'hypothesis'>[];
    readonly origin: ContextOrigin;
    readonly generation: number;
}

type Listener = (context: ScientificContext) => void;

const EMPTY: ScientificContext = {
    selectedObjects: [], activeHypotheses: [], origin: 'navigation', generation: 0,
};

/** The single scientific staleness clock for the application. */
export class ScientificContextStore {
    private state: ScientificContext = EMPTY;
    private readonly listeners = new Set<Listener>();

    current(): ScientificContext { return this.state; }
    generation(): number { return this.state.generation; }
    isCurrent(generation: number): boolean { return generation === this.state.generation; }

    subscribe(listener: Listener): () => void {
        this.listeners.add(listener);
        listener(this.state);
        return () => this.listeners.delete(listener);
    }

    focus(focusedObject: ObjectRef, origin: ContextOrigin = 'selection'): number {
        if (sameObject(this.state.focusedObject, focusedObject)) return this.state.generation;
        return this.commit({ focusedObject, origin });
    }

    clearFocus(origin: ContextOrigin = 'selection'): number {
        if (!this.state.focusedObject) return this.state.generation;
        return this.commit({ focusedObject: undefined, origin });
    }

    select(selectedObjects: readonly ObjectRef[], origin: ContextOrigin = 'selection'): number {
        const unique = [...new Map(selectedObjects.map(r => [`${r.kind}:${r.id}`, r])).values()];
        const unchanged = unique.length === this.state.selectedObjects.length
            && unique.every((r, i) => sameObject(r, this.state.selectedObjects[i]));
        return unchanged ? this.state.generation : this.commit({ selectedObjects: unique, origin });
    }

    patch(patch: Partial<Omit<ScientificContext, 'generation'>>): number {
        return this.commit(patch);
    }

    toUrlParams(): URLSearchParams {
        const p = new URLSearchParams();
        const put = (key: string, ref?: ObjectRef) => {
            if (ref) p.set(key, `${ref.kind}:${ref.id}`);
        };
        put('program', this.state.programRef);
        put('work', this.state.workItemRef);
        put('complex', this.state.complexRef);
        put('molecule', this.state.moleculeRef);
        put('compound', this.state.compoundRef);
        put('sample', this.state.sampleRef);
        put('experiment', this.state.experimentRef);
        put('dataset', this.state.datasetVersionRef);
        if (this.state.moleculeSmiles) p.set('smiles', this.state.moleculeSmiles);
        put('focus', this.state.focusedObject);
        put('target', this.state.targetRef);
        put('campaign', this.state.campaignRef);
        put('series', this.state.seriesRef);
        for (const ref of this.state.selectedObjects) p.append('selected', `${ref.kind}:${ref.id}`);
        for (const ref of this.state.activeHypotheses) p.append('hypothesis', `${ref.kind}:${ref.id}`);
        return p;
    }

    restore(params: URLSearchParams): number {
        const knownKinds = new Set<string>(OBJECT_KINDS);
        const parseValue = (value: string | null, expected?: ObjectKind): ObjectRef | undefined => {
            if (!value || !value.includes(':')) return undefined;
            const [kind, ...id] = value.split(':');
            const objectId = id.join(':');
            if (!knownKinds.has(kind) || !objectId || (expected && kind !== expected)) return undefined;
            return { kind: kind as ObjectRef['kind'], id: objectId };
        };
        const parse = (key: string, expected?: ObjectKind) => parseValue(params.get(key), expected);
        const selectedObjects = params.getAll('selected')
            .map(value => parseValue(value)).filter((value): value is ObjectRef => !!value);
        const activeHypotheses = params.getAll('hypothesis')
            .map(value => parseValue(value, 'hypothesis'))
            .filter((value): value is ObjectRef<'hypothesis'> => !!value);
        return this.commit({
            programRef: parse('program', 'program') as ObjectRef<'program'> | undefined,
            workItemRef: parse('work', 'work_item') as ObjectRef<'work_item'> | undefined,
            complexRef: parse('complex', 'complex') as ObjectRef<'complex'> | undefined,
            moleculeRef: parse('molecule', 'molecule') as ObjectRef<'molecule'> | undefined,
            compoundRef: parse('compound', 'compound') as ObjectRef<'compound'> | undefined,
            sampleRef: parse('sample', 'sample') as ObjectRef<'sample'> | undefined,
            experimentRef: parse('experiment', 'experiment') as ObjectRef<'experiment'> | undefined,
            datasetVersionRef: parse('dataset', 'dataset_version') as ObjectRef<'dataset_version'> | undefined,
            moleculeSmiles: params.get('smiles') || undefined,
            focusedObject: parse('focus'),
            targetRef: parse('target', 'target') as ObjectRef<'target'> | undefined,
            campaignRef: parse('campaign', 'campaign') as ObjectRef<'campaign'> | undefined,
            seriesRef: parse('series', 'series') as ObjectRef<'series'> | undefined,
            selectedObjects, activeHypotheses, origin: 'restore',
        });
    }

    private commit(patch: Partial<Omit<ScientificContext, 'generation'>>): number {
        this.state = Object.freeze({ ...this.state, ...patch,
            generation: this.state.generation + 1 });
        for (const listener of [...this.listeners]) listener(this.state);
        return this.state.generation;
    }
}

export const scientificContext = new ScientificContextStore();
