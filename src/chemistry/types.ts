/**
 * Framework-free composition types for computational chemistry products.
 *
 * Packs contribute scientific capabilities and PluginSpec registrations. They
 * never own application UI, so products can provide React, Vue, native DOM, or
 * another interface without changing the engine contract.
 */

import type { PluginContext } from '../mol-plugin/context';
import type { PluginSpec } from '../mol-plugin/spec';

export type ChemCapability =
    | 'data.formats'
    | 'data.provenance'
    | 'structure.hierarchy'
    | 'structure.assemblies'
    | 'structure.representations'
    | 'structure.selection'
    | 'structure.query'
    | 'structure.sequence'
    | 'structure.superposition'
    | 'structure.measurements'
    | 'annotation.custom'
    | 'annotation.labels'
    | 'annotation.validation'
    | 'annotation.quality-assessment'
    | 'chemistry.valence'
    | 'chemistry.partial-charges'
    | 'chemistry.interactions'
    | 'chemistry.chemical-components'
    | 'trajectory.models'
    | 'trajectory.coordinates'
    | 'trajectory.playback'
    | 'volume.density'
    | 'volume.streaming'
    | 'volume.masking'
    | 'quantum.orbitals'
    | 'quantum.electron-density'
    | 'site.tunnels'
    | 'site.membrane-orientation'
    | 'site.assembly-symmetry'
    | 'nucleic.conformation'
    | 'state.snapshots'
    | 'state.remote'
    | 'export.model'
    | 'export.image'
    | 'export.geometry'
    | 'export.animation';

export type ChemPackSpec = Partial<Pick<PluginSpec, 'actions' | 'behaviors' | 'animations' | 'customFormats' | 'config'>>;

export interface ChemPack {
    readonly id: string;
    readonly label: string;
    readonly description: string;
    readonly capabilities: readonly ChemCapability[];
    readonly fileExtensions?: readonly string[];
    readonly dependencies?: readonly ChemPack[];
    readonly spec?: ChemPackSpec;
    readonly setup?: (plugin: PluginContext) => void | Promise<void>;
    readonly teardown?: (plugin: PluginContext) => void;
}

export interface ChemCapabilityManifest {
    readonly packs: readonly string[];
    readonly capabilities: readonly ChemCapability[];
    readonly fileExtensions: readonly string[];
}

export function defineChemPack<const T extends ChemPack>(pack: T): T {
    return Object.freeze(pack);
}
