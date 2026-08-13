import { scientificContext, type ScientificContextStore } from '../context/scientific-context-store';
import { navigableViews, VIEWS, WORKSPACES, type ViewDefinition, type WorkspaceId } from './registries';
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
    workspaces() { return WORKSPACES.filter(w => w.shellReady); }
    views(workspace: WorkspaceId) { return navigableViews(workspace); }

    navigate(next: ShellRoute, replace = false): void {
        const definition = VIEWS.find(v => v.id === next.view && v.workspace === next.workspace);
        if (!definition?.shellReady) throw new Error(`view ${next.view} has no product shell`);
        const programId = next.programId && next.programId !== 'current'
            ? next.programId : this.context.current().programRef?.id || next.programId;
        this.route = { ...next, programId };
        if (programId && programId !== 'current') {
            this.context.patch({ programRef: objectRef('program', programId),
                origin: 'navigation' });
        }
        const url = this.urlFor({ ...next, programId });
        if (typeof history !== 'undefined') history[replace ? 'replaceState' : 'pushState'](next, '', url);
        for (const listener of [...this.listeners]) listener(this.route);
    }

    restore(locationLike: Pick<Location, 'pathname' | 'search'> = location): ShellRoute {
        this.context.restore(new URLSearchParams(locationLike.search));
        const match = VIEWS.find(v => v.shellReady && this.matches(v.route, locationLike.pathname));
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

    urlFor(route: ShellRoute): string {
        const definition = VIEWS.find(v => v.id === route.view && v.workspace === route.workspace);
        if (!definition?.shellReady) throw new Error(`view ${route.view} has no product shell`);
        const programId = route.programId && route.programId !== 'current'
            ? route.programId : this.context.current().programRef?.id || route.programId;
        const path = this.pathFor(definition, programId);
        const query = this.context.toUrlParams().toString();
        return path + (query ? `?${query}` : '');
    }

    /** Persist a user-selected scientific object without adding a history entry. */
    replaceCurrentUrl(): void {
        if (typeof history === 'undefined') return;
        history.replaceState(this.route, '', this.urlFor(this.route));
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
