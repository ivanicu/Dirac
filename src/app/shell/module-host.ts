import type { ScientificContext } from '../context/scientific-context-store';
import { modulesForView, VIEWS, type ModuleDefinition } from './registries';

/** Lifecycle boundary for a reusable module; Views declare composition, never imports. */
export interface ModuleAdapter {
    mount(definition: ModuleDefinition, context: ScientificContext): void;
    unmount(definition: ModuleDefinition): void;
    update?(definition: ModuleDefinition, context: ScientificContext): void;
}

export type ModuleRuntimeState = 'needs-context' | 'ready';

export class ModuleHost {
    private active = new Map<string, ModuleDefinition>();
    private states = new Map<string, ModuleRuntimeState>();

    constructor(private readonly adapters: ReadonlyMap<string, ModuleAdapter>) {}

    activate(viewId: string, context: ScientificContext): readonly ModuleDefinition[] {
        if (!VIEWS.some(view => view.id === viewId && view.shellReady)) {
            throw new Error(`view ${viewId} has no product shell`);
        }
        const desired = modulesForView(viewId);
        const contextKinds = new Set([
            context.programRef?.kind, context.complexRef?.kind, context.focusedObject?.kind,
            context.targetRef?.kind, context.campaignRef?.kind, context.seriesRef?.kind,
            ...context.selectedObjects.map(ref => ref.kind),
            ...context.activeHypotheses.map(ref => ref.kind),
        ].filter((kind): kind is NonNullable<typeof kind> => !!kind));
        const ids = new Set(desired.map(module => module.id));
        for (const [id, definition] of [...this.active]) {
            if (!ids.has(id)) {
                this.adapter(id).unmount(definition);
                this.active.delete(id);
            }
        }
        for (const definition of desired) {
            const compatible = definition.requiresContext.every(kind => contextKinds.has(kind));
            this.states.set(definition.id, compatible ? 'ready' : 'needs-context');
            if (!compatible) {
                if (this.active.has(definition.id)) {
                    this.adapter(definition.id).unmount(definition);
                    this.active.delete(definition.id);
                }
                continue;
            }
            if (this.active.has(definition.id)) {
                this.adapter(definition.id).update?.(definition, context);
            } else {
                this.adapter(definition.id).mount(definition, context);
                this.active.set(definition.id, definition);
            }
        }
        return desired;
    }

    activeModules(): readonly string[] { return [...this.active.keys()]; }
    runtimeStates(): ReadonlyMap<string, ModuleRuntimeState> { return new Map(this.states); }

    private adapter(id: string): ModuleAdapter {
        const adapter = this.adapters.get(id);
        if (!adapter) throw new Error(`module ${id} has no runtime adapter`);
        return adapter;
    }
}
