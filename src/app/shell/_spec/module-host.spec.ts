import type { ScientificContext } from '../../context/scientific-context-store';
import { objectRef } from '../../domain/object-ref';
import { ModuleHost, type ModuleAdapter } from '../module-host';
import { MODULES } from '../registries';

describe('registry-driven ModuleHost', () => {
    it('mounts, preserves common modules, and unmounts only departed modules', () => {
        const events: string[] = [];
        const adapters = new Map<string, ModuleAdapter>(MODULES.map(module => [module.id, {
            mount: () => events.push(`mount:${module.id}`),
            unmount: () => events.push(`unmount:${module.id}`),
            update: () => events.push(`update:${module.id}`),
        }]));
        const host = new ModuleHost(adapters);
        const context = {
            complexRef: objectRef('complex', 'complex-1'),
            focusedObject: objectRef('molecule', 'ligand-1'),
            selectedObjects: [], activeHypotheses: [],
            origin: 'navigation', generation: 0,
        } as ScientificContext;
        host.activate('structures.complex', context);
        events.length = 0;
        host.activate('structures.site', context);
        expect(events).toContain('update:scene.viewport');
        expect(events).toContain('update:structure.field-overlay');
        expect(events).toContain('unmount:structure.interaction-map');
        expect(events).toContain('mount:design.pharmacophore');
        expect(host.activeModules()).toEqual([
            'scene.viewport', 'structure.field-overlay', 'design.pharmacophore',
        ]);
    });

    it('unmounts operational modules when a scaffold view takes over the canvas', () => {
        const events: string[] = [];
        const adapters = new Map<string, ModuleAdapter>(MODULES.map(module => [module.id, {
            mount: () => events.push(`mount:${module.id}`),
            unmount: () => events.push(`unmount:${module.id}`),
        }]));
        const host = new ModuleHost(adapters);
        const context = {
            selectedObjects: [], activeHypotheses: [], origin: 'navigation', generation: 0,
        } as ScientificContext;
        host.activate('structures.complex', context);
        expect(host.activate('knowledge.entities', context)).toEqual([]);
        expect(host.activeModules()).toEqual([]);
        expect(events.some(event => event.startsWith('unmount:'))).toBe(true);
    });

    it('still rejects an unknown View instead of treating it as an empty scaffold', () => {
        expect(() => new ModuleHost(new Map()).activate('unknown.view', {
            selectedObjects: [], activeHypotheses: [], origin: 'navigation', generation: 0,
        })).toThrow('has no product shell');
    });
});
