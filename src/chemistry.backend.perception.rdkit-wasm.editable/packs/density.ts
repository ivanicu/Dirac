import { loadFullResolutionEMDBMap, loadVolumeFromUrl, VolumeIsovalueInfo } from '../../extensions/plugin/loaders';
import { VolumeMaskBehavior } from '../../extensions/volume-mask/behavior';
import type { BuildInVolumeFormat } from '../../mol-plugin-state/formats/volume';
import type { PluginContext } from '../../mol-plugin/context';
import { PluginSpec } from '../../mol-plugin/spec';
import { defineChemPack } from '../types';
import { corePack } from './core';

export const densityPack = defineChemPack({
    id: 'density',
    label: 'Density and Volumes',
    description: 'Density maps, isosurfaces, streaming, structure factors, and volume masks.',
    dependencies: [corePack],
    capabilities: ['volume.density', 'volume.streaming', 'volume.masking'],
    fileExtensions: ['ccp4', 'mrc', 'map', 'dsn6', 'brix', 'dx', 'dxbin', 'mtz', 'bcif'],
    spec: {
        behaviors: [PluginSpec.Behavior(VolumeMaskBehavior)],
    },
});

export type { VolumeIsovalueInfo };

export function loadDensityFromUrl(
    plugin: PluginContext,
    source: { url: string, format: BuildInVolumeFormat, isBinary: boolean },
    isovalues: VolumeIsovalueInfo[],
    options?: { entryId?: string | string[], isLazy?: boolean },
) {
    return loadVolumeFromUrl(plugin, source, isovalues, options);
}

export { loadFullResolutionEMDBMap };
export { MaskVolumeFromSource } from '../../extensions/volume-mask/transformers';
