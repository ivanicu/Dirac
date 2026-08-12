import type { ScientificContext } from '../context/scientific-context-store';
import { modulesForView, VIEWS, type ModuleDefinition } from './registries';

/** Lifecycle boundary for a reusable module; Views declare composition, never imports. */
export interface ModuleAdapter {
    mount(definition: ModuleDefinition, context: ScientificContext): void;
    unmount(definition: ModuleDefinition): void;
    update?(definition: ModuleDefinition, context: ScientificContext): void;
}

export class ModuleHost {
    private active = new Map<string, ModuleDefinition>();

    constructor(private readonly adapters: ReadonlyMap<string, ModuleAdapter>) {}

    activate(viewId: string, context: ScientificContext): readonly ModuleDefinition[] {
        if (!VIEWS.some(view => view.id === viewId && view.shellReady)) {
            throw new Error(`view ${viewId} has no product shell`);
        }
        const desired = modulesForView(viewId);
        const ids = new Set(desired.map(module => module.id));
        for (const [id, definition] of [...this.active]) {
            if (!ids.has(id)) {
                this.adapter(id).unmount(definition);
                this.active.delete(id);
            }
        }
        for (const definition of desired) {
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

    private adapter(id: string): ModuleAdapter {
        const adapter = this.adapters.get(id);
        if (!adapter) throw new Error(`module ${id} has no runtime adapter`);
        return adapter;
    }
}
