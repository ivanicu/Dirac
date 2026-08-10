import type { PresetTrajectoryHierarchy } from '../mol-plugin-state/builder/structure/hierarchy-preset';
import type { BuiltInTrajectoryFormat } from '../mol-plugin-state/formats/trajectory';
import type { BuildInVolumeFormat } from '../mol-plugin-state/formats/volume';
import type { PluginStateObject } from '../mol-plugin-state/objects';
import { PluginCommands } from '../mol-plugin/commands';
import { PluginContext } from '../mol-plugin/context';
import type { PluginSpec } from '../mol-plugin/spec';
import type { StateObjectRef } from '../mol-state';
import { Asset } from '../mol-util/assets';
import type { Color } from '../mol-util/color';
import { composeChemSpec, resolveChemPacks } from './compose';
import { corePack } from './packs/core';
import type { ChemCapability, ChemCapabilityManifest, ChemPack } from './types';

type RawStructureData = string | number[] | ArrayBuffer | Uint8Array<ArrayBuffer>;
type RawVolumeData = RawStructureData;

export interface CreateChemWorkbenchOptions {
    target: string | HTMLElement;
    packs?: readonly ChemPack[];
    baseSpec?: PluginSpec;
    checkeredCanvasBackground?: boolean;
}

export interface LoadStructureOptions {
    format?: BuiltInTrajectoryFormat;
    label?: string;
    preset?: keyof PresetTrajectoryHierarchy;
    clear?: boolean;
}

export interface LoadVolumeOptions {
    format?: BuildInVolumeFormat;
    label?: string;
    entryId?: string;
    clear?: boolean;
    visuals?: boolean;
}

export class ChemWorkbench {
    readonly packs: readonly ChemPack[];
    readonly manifest: ChemCapabilityManifest;

    private disposed = false;

    constructor(readonly plugin: PluginContext, packs: readonly ChemPack[]) {
        this.packs = Object.freeze([...packs]);
        this.manifest = Object.freeze({
            packs: Object.freeze(this.packs.map(pack => pack.id)),
            capabilities: Object.freeze([...new Set(this.packs.flatMap(pack => pack.capabilities))]),
            fileExtensions: Object.freeze([...new Set(this.packs.flatMap(pack => pack.fileExtensions ?? []))].sort()),
        });
    }

    hasPack(id: string) {
        return this.packs.some(pack => pack.id === id);
    }

    hasCapability(capability: ChemCapability) {
        return this.manifest.capabilities.includes(capability);
    }

    requireCapability(capability: ChemCapability) {
        if (!this.hasCapability(capability)) {
            throw new Error(`Chemistry capability '${capability}' is not installed`);
        }
    }

    async loadStructureFromUrl(url: string, options: LoadStructureOptions & { isBinary?: boolean } = {}) {
        if (options.clear ?? true) await this.plugin.clear();
        const data = await this.plugin.builders.data.download({
            url: Asset.Url(url),
            isBinary: options.isBinary,
            label: options.label,
        }, { state: { isGhost: true } });
        return this.buildStructure(data, options);
    }

    async loadStructureFromData(data: RawStructureData, options: LoadStructureOptions = {}) {
        if (options.clear ?? true) await this.plugin.clear();
        const raw = await this.plugin.builders.data.rawData({ data, label: options.label }, { state: { isGhost: true } });
        return this.buildStructure(raw, options);
    }

    async loadVolumeFromUrl(url: string, options: LoadVolumeOptions & { isBinary?: boolean } = {}) {
        if (options.clear ?? false) await this.plugin.clear();
        const data = await this.plugin.builders.data.download({
            url: Asset.Url(url),
            isBinary: options.isBinary,
            label: options.label,
        }, { state: { isGhost: true } });
        return this.buildVolume(data, options);
    }

    async loadVolumeFromData(data: RawVolumeData, options: LoadVolumeOptions = {}) {
        if (options.clear ?? false) await this.plugin.clear();
        const raw = await this.plugin.builders.data.rawData({ data, label: options.label }, { state: { isGhost: true } });
        return this.buildVolume(raw, options);
    }

    clear(resetViewportSettings = false) {
        return this.plugin.clear(resetViewportSettings);
    }

    resetCamera(durationMs = 250) {
        return PluginCommands.Camera.Reset(this.plugin, { durationMs });
    }

    setBackground(backgroundColor: Color) {
        return PluginCommands.Canvas3D.SetSettings(this.plugin, {
            settings: old => ({ renderer: { ...old.renderer, backgroundColor } })
        });
    }

    dispose() {
        if (this.disposed) return;
        for (const pack of [...this.packs].reverse()) pack.teardown?.(this.plugin);
        this.plugin.dispose();
        this.disposed = true;
    }

    private async buildStructure(
        data: StateObjectRef<PluginStateObject.Data.String | PluginStateObject.Data.Binary>,
        options: LoadStructureOptions
    ) {
        const trajectory = await this.plugin.builders.structure.parseTrajectory(data, options.format ?? 'mmcif');
        const preset = await this.plugin.builders.structure.hierarchy.applyPreset(trajectory, options.preset ?? 'default');
        return { data, trajectory, preset };
    }

    private async buildVolume(
        data: StateObjectRef<PluginStateObject.Data.String | PluginStateObject.Data.Binary>,
        options: LoadVolumeOptions,
    ) {
        this.requireCapability('volume.density');
        const provider = this.plugin.dataFormats.get(options.format ?? 'cube');
        if (!provider) throw new Error(`Volume format '${options.format ?? 'cube'}' is not registered`);
        const parsed = await provider.parse(this.plugin, data, { entryId: options.entryId });
        const visuals = (options.visuals ?? true) ? await provider.visuals?.(this.plugin, parsed) : undefined;
        return { data, parsed, visuals };
    }
}

export async function createChemWorkbench(options: CreateChemWorkbenchOptions): Promise<ChemWorkbench> {
    const target = typeof options.target === 'string' ? document.getElementById(options.target) : options.target;
    if (!target) throw new Error(`Could not find chemistry workbench target '${options.target}'`);

    const packs = resolveChemPacks([corePack, ...(options.packs ?? [])]);
    const plugin = new PluginContext(composeChemSpec(packs, options.baseSpec));

    try {
        await plugin.init();
        const mounted = await plugin.mountAsync(target, {
            checkeredCanvasBackground: options.checkeredCanvasBackground,
        });
        if (!mounted) throw new Error('WebGL viewer could not be initialized');
        for (const pack of packs) await pack.setup?.(plugin);
        return new ChemWorkbench(plugin, packs);
    } catch (error) {
        plugin.dispose();
        throw error;
    }
}
