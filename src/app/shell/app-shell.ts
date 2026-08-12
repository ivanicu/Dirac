import { scientificContext, type ScientificContextStore } from '../context/scientific-context-store';
import { availableViews, VIEWS, WORKSPACES, type ViewDefinition, type WorkspaceId } from './registries';
import { sceneService, type SceneService } from './scene-service';
import { objectRef } from '../domain/object-ref';

export interface ShellRoute { workspace: WorkspaceId; view: string; programId?: string; }

/** Router + global context coordinator. It never owns or disposes mol*. */
export class AppShell {
    private route: ShellRoute = { workspace: 'structures', view: 'structures.complex' };
    private listeners = new Set<(route: ShellRoute) => void>();

    constructor(readonly context: ScientificContextStore = scientificContext,
                readonly scene: SceneService = sceneService) {}

    current(): ShellRoute { return this.route; }
    workspaces() { return WORKSPACES.filter(w => availableViews(w.id).length > 0); }
    views(workspace: WorkspaceId) { return availableViews(workspace); }

    navigate(next: ShellRoute, replace = false): void {
        const definition = VIEWS.find(v => v.id === next.view && v.workspace === next.workspace);
        if (!definition?.implemented) throw new Error(`view ${next.view} is not available`);
        this.route = next;
        if (next.programId && next.programId !== 'current') {
            this.context.patch({ programRef: objectRef('program', next.programId),
                origin: 'navigation' });
        }
        const path = this.pathFor(definition, next.programId);
        const query = this.context.toUrlParams().toString();
        const url = path + (query ? `?${query}` : '');
        if (typeof history !== 'undefined') history[replace ? 'replaceState' : 'pushState'](next, '', url);
        for (const listener of [...this.listeners]) listener(next);
    }

    restore(locationLike: Pick<Location, 'pathname' | 'search'> = location): ShellRoute {
        this.context.restore(new URLSearchParams(locationLike.search));
        const match = VIEWS.find(v => v.implemented && this.matches(v.route, locationLike.pathname));
        if (match) {
            const programId = this.programId(match.route, locationLike.pathname);
            this.route = { workspace: match.workspace, view: match.id, programId };
            // The canonical program lives in the path. A missing or stale query value
            // must not make the global context disagree with the route after reload.
            if (programId && programId !== 'current'
                && this.context.current().programRef?.id !== programId) {
                this.context.patch({ programRef: objectRef('program', programId),
                    origin: 'restore' });
            }
        }
        return this.route;
    }

    subscribe(listener: (route: ShellRoute) => void): () => void {
        this.listeners.add(listener); listener(this.route);
        return () => this.listeners.delete(listener);
    }

    private pathFor(view: ViewDefinition, programId?: string): string {
        return view.route.replace(':programId', encodeURIComponent(programId || 'current'));
    }
    private matches(template: string, path: string): boolean {
        return new RegExp('^' + template.replace(':programId', '[^/]+') + '$').test(path);
    }
    private programId(template: string, path: string): string | undefined {
        if (!template.includes(':programId')) return undefined;
        const before = template.split(':programId')[0];
        return decodeURIComponent(path.slice(before.length).split('/')[0]);
    }
}

export const appShell = new AppShell();
