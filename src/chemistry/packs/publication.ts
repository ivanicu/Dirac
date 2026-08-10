import { GeometryControls } from '../../extensions/geo-export/controls';
import { exportHierarchy } from '../../extensions/model-export/export';
import { Mp4Controls } from '../../extensions/mp4-export/controls';
import type { PluginContext } from '../../mol-plugin/context';
import { defineChemPack } from '../types';
import { corePack } from './core';

export const publicationPack = defineChemPack({
    id: 'publication',
    label: 'Export and Publication',
    description: 'Model, image, geometry, and animation export through framework-free controllers.',
    dependencies: [corePack],
    capabilities: ['export.model', 'export.image', 'export.geometry', 'export.animation'],
});

export function createGeometryExportController(plugin: PluginContext) {
    return new GeometryControls(plugin);
}

export function createMp4ExportController(plugin: PluginContext) {
    return new Mp4Controls(plugin);
}

export { exportHierarchy };
