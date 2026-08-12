import type { ObjectRef } from '../domain/object-ref';
import { sameObject } from '../domain/object-ref';

export type ContextOrigin = 'navigation' | 'selection' | 'import' | 'command' | 'restore';

export interface ScientificContext {
    readonly programRef?: ObjectRef<'program'>;
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
        put('focus', this.state.focusedObject);
        put('target', this.state.targetRef);
        put('campaign', this.state.campaignRef);
        put('series', this.state.seriesRef);
        return p;
    }

    restore(params: URLSearchParams): number {
        const parse = (key: string): ObjectRef | undefined => {
            const value = params.get(key);
            if (!value || !value.includes(':')) return undefined;
            const [kind, ...id] = value.split(':');
            return { kind: kind as ObjectRef['kind'], id: id.join(':') };
        };
        return this.commit({
            programRef: parse('program') as ObjectRef<'program'> | undefined,
            focusedObject: parse('focus'), targetRef: parse('target') as ObjectRef<'target'>,
            campaignRef: parse('campaign') as ObjectRef<'campaign'>,
            seriesRef: parse('series') as ObjectRef<'series'>, origin: 'restore',
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
