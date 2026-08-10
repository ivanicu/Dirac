import type { AlphaOrbital, Basis } from '../../extensions/alpha-orbitals/data-model';
import { BasisAndOrbitals, CreateOrbitalDensityVolume, CreateOrbitalRepresentation3D, CreateOrbitalVolume, StaticBasisAndOrbitals } from '../../extensions/alpha-orbitals/transforms';
import type { SphericalBasisOrder } from '../../extensions/alpha-orbitals/spherical-functions';
import { canComputeGrid3dOnGPU } from '../../mol-gl/compute/grid3d';
import type { PluginContext } from '../../mol-plugin/context';
import { PluginSpec } from '../../mol-plugin/spec';
import type { StateObjectSelector } from '../../mol-state';
import type { Color } from '../../mol-util/color';
import { ColorNames } from '../../mol-util/color/names';
import { defineChemPack } from '../types';
import { corePack } from './core';

export interface OrbitalBasisInput {
    basis: Basis;
    order: SphericalBasisOrder;
    orbitals: AlphaOrbital[];
    label?: string;
}

export interface OrbitalRepresentationOptions {
    index: number;
    relativeIsovalue?: number;
    alpha?: number;
    positiveColor?: Color;
    negativeColor?: Color;
    tryUseGpu?: boolean;
}

export const qmPack = defineChemPack({
    id: 'qm',
    label: 'Quantum Chemistry',
    description: 'Cube/DX fields, basis/orbital grids, electron density, and positive/negative isosurfaces.',
    dependencies: [corePack],
    capabilities: ['volume.density', 'quantum.orbitals', 'quantum.electron-density'],
    fileExtensions: ['cube', 'cub', 'dx', 'dxbin'],
    spec: {
        actions: [
            PluginSpec.Action(StaticBasisAndOrbitals),
            PluginSpec.Action(CreateOrbitalVolume),
            PluginSpec.Action(CreateOrbitalDensityVolume),
            PluginSpec.Action(CreateOrbitalRepresentation3D),
        ],
    },
});

export function canComputeOrbitalsOnGpu(plugin: PluginContext) {
    return canComputeGrid3dOnGPU(plugin.canvas3d?.webgl);
}

export function addOrbitalBasis(plugin: PluginContext, input: OrbitalBasisInput) {
    return plugin.build().toRoot().apply(StaticBasisAndOrbitals, {
        label: input.label ?? 'Orbital Data',
        basis: input.basis,
        order: input.order,
        orbitals: input.orbitals,
    }).commit();
}

export async function showOrbital(
    plugin: PluginContext,
    basis: StateObjectSelector<BasisAndOrbitals>,
    options: OrbitalRepresentationOptions,
) {
    const relativeIsovalue = options.relativeIsovalue ?? 1;
    const alpha = options.alpha ?? 0.85;
    const tryUseGpu = options.tryUseGpu ?? true;
    const update = plugin.build();
    const volume = update.to(basis).apply(CreateOrbitalVolume, { index: options.index });
    const positive = volume.apply(CreateOrbitalRepresentation3D, {
        alpha,
        color: options.positiveColor ?? ColorNames.blue,
        kind: 'positive',
        relativeIsovalue,
        pickable: false,
        xrayShaded: true,
        tryUseGpu,
    }).selector;
    const negative = volume.apply(CreateOrbitalRepresentation3D, {
        alpha,
        color: options.negativeColor ?? ColorNames.red,
        kind: 'negative',
        relativeIsovalue,
        pickable: false,
        xrayShaded: true,
        tryUseGpu,
    }).selector;
    await update.commit();
    return { volume: volume.selector, positive, negative };
}

export { CreateOrbitalDensityVolume, CreateOrbitalRepresentation3D, CreateOrbitalVolume, StaticBasisAndOrbitals };
