import { ScientificContextStore } from '../../context/scientific-context-store';
import { objectRef } from '../../domain/object-ref';
import { AppShell } from '../app-shell';
import { assertRegistryIntegrity, availableViews, MODULES, VIEWS, WORKSPACES } from '../registries';
import { SceneService } from '../scene-service';

describe('canonical AppShell architecture', () => {
    it('defines exactly eight workspaces and thirty views but exposes only implemented views', () => {
        expect(WORKSPACES).toHaveLength(8);
        expect(VIEWS).toHaveLength(30);
        expect(() => assertRegistryIntegrity()).not.toThrow();
        expect(WORKSPACES.flatMap(w => availableViews(w.id)).every(v => v.implemented)).toBe(true);
    });

    it('convicts a module that names an unknown command', () => {
        const broken = MODULES.map((m, i) => i === 0 ? { ...m, providesCommands: ['ghost.command'] } : m);
        expect(() => assertRegistryIntegrity(WORKSPACES, VIEWS, broken)).toThrow('missing command ghost.command');
    });

    it('restores route and scientific context from one URL', () => {
        const context = new ScientificContextStore();
        const shell = new AppShell(context, new SceneService());
        const route = shell.restore({
            pathname: '/p/prog-7/structures/complex',
            search: '?focus=molecule:mol-42&target=target:t-1',
        } as Location);
        expect(route).toEqual({ workspace: 'structures', view: 'structures.complex', programId: 'prog-7' });
        expect(context.current().focusedObject).toEqual(objectRef('molecule', 'mol-42'));
        expect(context.current().targetRef).toEqual(objectRef('target', 't-1'));
    });

    it('keeps one molstar instance across navigation and rejects replacement', () => {
        const scene = new SceneService();
        const plugin = { name: 'persistent' } as never;
        scene.attach(plugin);
        const shell = new AppShell(new ScientificContextStore(), scene);
        shell.navigate({ workspace: 'structures', view: 'structures.dynamics', programId: 'p' });
        expect(scene.current()).toBe(plugin);
        expect(() => scene.attach({ name: 'replacement' } as never)).toThrow('different mol* instance');
    });
});
