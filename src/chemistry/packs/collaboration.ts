import type { PluginContext } from '../../mol-plugin/context';
import { PluginCommands } from '../../mol-plugin/commands';
import type { PluginState } from '../../mol-plugin/state';
import { defineChemPack } from '../types';
import { corePack } from './core';

export const collaborationPack = defineChemPack({
    id: 'collaboration',
    label: 'Sessions and Collaboration',
    description: 'Local/remote snapshots and portable Mol* sessions.',
    dependencies: [corePack],
    capabilities: ['state.snapshots', 'state.remote'],
    fileExtensions: ['molj', 'molx'],
});

export function addSnapshot(plugin: PluginContext, options: { name?: string, description?: string } = {}) {
    return PluginCommands.State.Snapshots.Add(plugin, options);
}

export function downloadSession(plugin: PluginContext, options: { name?: string, type?: PluginState.SnapshotType } = {}) {
    return PluginCommands.State.Snapshots.DownloadToFile(plugin, {
        name: options.name,
        type: options.type ?? 'molx',
    });
}

export function openRemoteSession(plugin: PluginContext, url: string, type: PluginState.SnapshotType = 'molx') {
    return PluginCommands.State.Snapshots.OpenUrl(plugin, { url, type });
}
