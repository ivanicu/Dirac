import { ScientificContextStore } from '../../context/scientific-context-store';
import { objectRef } from '../../domain/object-ref';
import { AppShell } from '../app-shell';
import { assertRegistryIntegrity, availableViews, MODULES, navigableViews, VIEWS,
    WORKBENCH_SURFACES, WORKSPACES } from '../registries';
import { SceneService } from '../scene-service';
import { assertExperienceCatalog, VIEW_EXPERIENCES } from '../workspace-catalog';
import { assertWorkspaceVisualCatalog, WORKSPACE_VISUALS } from '../workspace-visual-catalog';
import { assertViewPlans, VIEW_PLANS } from '../workspace-plans';

describe('canonical AppShell architecture', () => {
    it('defines a navigable shell for all eight workspaces and thirty views', () => {
        expect(WORKSPACES).toHaveLength(8);
        expect(VIEWS).toHaveLength(30);
        expect(() => assertRegistryIntegrity()).not.toThrow();
        expect(WORKSPACES.flatMap(w => navigableViews(w.id))).toHaveLength(30);
        expect(WORKSPACES.every(w => w.shellReady)).toBe(true);
        expect(WORKSPACES.flatMap(w => availableViews(w.id)).every(v => v.implemented)).toBe(true);
        expect(WORKSPACES.every(workspace =>
            VIEWS.some(view => view.workspace === workspace.id && view.delivery === 'connected'))).toBe(true);
        expect(Object.keys(VIEW_EXPERIENCES)).toHaveLength(30);
        expect(() => assertExperienceCatalog(VIEWS.map(v => v.id))).not.toThrow();
        expect(Object.keys(WORKSPACE_VISUALS)).toHaveLength(30);
        expect(() => assertWorkspaceVisualCatalog(VIEWS.map(v => v.id))).not.toThrow();
        expect(Object.keys(VIEW_PLANS)).toHaveLength(30);
        expect(() => assertViewPlans(VIEWS.map(v => v.id))).not.toThrow();
    });

    it('convicts a module that names an unknown command', () => {
        const broken = MODULES.map((m, i) => i === 0 ? { ...m, providesCommands: ['ghost.command'] } : m);
        expect(() => assertRegistryIntegrity(WORKSPACES, VIEWS, broken)).toThrow('missing command ghost.command');
    });

    it('keeps every original workbench surface absorbed by a Workspace module', () => {
        const absorbed = new Set(MODULES.flatMap(module => module.surfaces));
        expect(WORKBENCH_SURFACES.every(surface => absorbed.has(surface))).toBe(true);
        const broken = MODULES.map(module => ({
            ...module,
            surfaces: module.surfaces.filter(surface => surface !== 'ligand'),
        }));
        expect(() => assertRegistryIntegrity(WORKSPACES, VIEWS, broken))
            .toThrow('unabsorbed workbench surface ligand');
    });

    it('restores route and scientific context from one URL', () => {
        const context = new ScientificContextStore();
        const shell = new AppShell(context, new SceneService());
        const route = shell.restore({
            pathname: '/p/prog-7/structures/complex',
            search: '?work=work_item:w-9&molecule=molecule:mol-42&focus=molecule:mol-42&target=target:t-1',
        } as Location);
        expect(route).toEqual({ workspace: 'structures', view: 'structures.complex', programId: 'prog-7' });
        expect(context.current().programRef).toEqual(objectRef('program', 'prog-7'));
        expect(context.current().focusedObject).toEqual(objectRef('molecule', 'mol-42'));
        expect(context.current().workItemRef).toEqual(objectRef('work_item', 'w-9'));
        expect(context.current().moleculeRef).toEqual(objectRef('molecule', 'mol-42'));
        expect(context.current().targetRef).toEqual(objectRef('target', 't-1'));
    });

    it('rejects mismatched URL object kinds instead of casting them into context', () => {
        const context = new ScientificContextStore();
        const shell = new AppShell(context, new SceneService());
        shell.restore({
            pathname: '/p/prog-7/structures/complex',
            search: '?complex=molecule:not-a-complex&target=bogus:t-1',
        } as Location);
        expect(context.current().complexRef).toBeUndefined();
        expect(context.current().targetRef).toBeUndefined();
    });

    it('round-trips selected objects and active hypotheses through the URL', () => {
        const context = new ScientificContextStore();
        context.patch({
            selectedObjects: [objectRef('compound', 'cmp-1'), objectRef('sample', 'sample-2')],
            activeHypotheses: [objectRef('hypothesis', 'h-3')],
            origin: 'selection',
        });
        const restored = new ScientificContextStore();
        restored.restore(context.toUrlParams());
        expect(restored.current().selectedObjects).toEqual([
            objectRef('compound', 'cmp-1'), objectRef('sample', 'sample-2'),
        ]);
        expect(restored.current().activeHypotheses).toEqual([objectRef('hypothesis', 'h-3')]);
    });

    it('requires a scene only for molecular views, never for Runs', () => {
        expect(VIEWS.find(view => view.id === 'structures.complex')?.requiresScene).toBe(true);
        expect(VIEWS.find(view => view.id === 'design.builder')?.requiresScene).toBe(true);
        expect(VIEWS.find(view => view.id === 'runs.active')?.requiresScene).toBe(false);
        expect(VIEWS.find(view => view.id === 'runs.history')?.requiresScene).toBe(false);
    });

    it('restores every shell-ready View route', () => {
        const shell = new AppShell(new ScientificContextStore(), new SceneService());
        for (const view of VIEWS) {
            const pathname = view.route.replace(':programId', 'program-30');
            expect(shell.restore({ pathname, search: '' } as Location).view).toBe(view.id);
        }
    });

    it('writes navigation program identity into the global scientific context', () => {
        const context = new ScientificContextStore();
        const shell = new AppShell(context, new SceneService());
        shell.navigate({ workspace: 'design', view: 'design.builder', programId: 'KRAS-G12D' });
        expect(context.current().programRef).toEqual(objectRef('program', 'KRAS-G12D'));
    });

    it('navigates to the connected Program aggregate view', () => {
        const shell = new AppShell(new ScientificContextStore(), new SceneService());
        shell.navigate({ workspace: 'programs', view: 'programs.overview', programId: 'KRAS-G12D' });
        expect(shell.current().view).toBe('programs.overview');
        expect(VIEWS.find(v => v.id === shell.current().view)?.implemented).toBe(true);
        expect(VIEWS.find(v => v.id === shell.current().view)?.delivery).toBe('connected');
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
