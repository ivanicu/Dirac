import { loadTrajectory, LoadTrajectoryParams } from '../../extensions/plugin/loaders';
import { PluginContext } from '../../mol-plugin/context';
import { defineChemPack } from '../types';
import { corePack } from './core';

export const mdPack = defineChemPack({
    id: 'md',
    label: 'Molecular Dynamics',
    description: 'Topology/coordinate pairing, trajectories, frame navigation, and trajectory animation.',
    dependencies: [corePack],
    capabilities: ['trajectory.models', 'trajectory.coordinates', 'trajectory.playback'],
    fileExtensions: ['gro', 'psf', 'prmtop', 'parm7', 'top', 'dcd', 'xtc', 'trr', 'nc', 'nctraj', 'lammpstrj'],
});

export type { LoadTrajectoryParams };

export function loadMdTrajectory(plugin: PluginContext, params: LoadTrajectoryParams) {
    return loadTrajectory(plugin, params);
}
