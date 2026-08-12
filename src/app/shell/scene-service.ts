import type { PluginContext } from '../../mol-plugin/context';

/** Owns the one mol* instance above views, so navigation cannot reconstruct it. */
export class SceneService {
    private plugin?: PluginContext;
    private host?: HTMLElement;

    attach(plugin: PluginContext, host?: HTMLElement): void {
        if (this.plugin && this.plugin !== plugin) {
            throw new Error('SceneService already owns a different mol* instance');
        }
        this.plugin = plugin;
        if (host) this.host = host;
    }

    current(): PluginContext | undefined { return this.plugin; }
    viewportHost(): HTMLElement | undefined { return this.host; }

    projectInto(container: HTMLElement): void {
        if (this.host && this.host.parentElement !== container) container.appendChild(this.host);
    }
}

export const sceneService = new SceneService();
