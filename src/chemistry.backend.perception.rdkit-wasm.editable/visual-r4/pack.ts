import type { PluginContext } from '../../mol-plugin/context';
import { defineChemPack } from '../types';
import { corePack } from '../packs/core';
import type { R4HybridRenderer } from './renderer';

const instances = new WeakMap<PluginContext, R4HybridRenderer>();

export const visualR4Pack = defineChemPack({
    id: 'visual-r4',
    label: 'Molecular Nodes R4 Visual Runtime',
    description: 'Three.js WebGPU visual runtime driven by the complete Mol* semantic and state model.',
    dependencies: [corePack],
    capabilities: ['structure.representations', 'structure.selection', 'structure.sequence', 'trajectory.coordinates', 'volume.density', 'annotation.labels'],
    async setup(plugin: PluginContext) {
        const molCanvas = plugin.canvas3d?.webgl.gl.canvas;
        const target = molCanvas instanceof HTMLCanvasElement ? molCanvas.parentElement : undefined;
        if (!target) throw new Error('R4 visual runtime requires a mounted Mol* canvas');
        const { R4HybridRenderer } = await import('./renderer');
        const renderer = new R4HybridRenderer(plugin, target);
        instances.set(plugin, renderer);
        await renderer.init();
    },
    teardown(plugin: PluginContext) {
        instances.get(plugin)?.dispose();
        instances.delete(plugin);
    },
});

export function getR4Renderer(plugin: PluginContext) {
    return instances.get(plugin);
}
