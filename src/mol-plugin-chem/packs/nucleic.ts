import { DnatcoNtCs } from '../../extensions/dnatco/behavior';
import { PluginSpec } from '../../mol-plugin/spec';
import { defineChemPack } from '../types';
import { corePack } from './core';

export const nucleicPack = defineChemPack({
    id: 'nucleic',
    label: 'Nucleic Acid Conformations',
    description: 'DNATCO NtC, Confal Pyramids, and nucleic-acid conformation representations.',
    dependencies: [corePack],
    capabilities: ['nucleic.conformation'],
    spec: {
        behaviors: [PluginSpec.Behavior(DnatcoNtCs)],
    },
});

export { ConfalPyramidsProvider } from '../../extensions/dnatco/confal-pyramids/property';
export { NtCTubeProvider } from '../../extensions/dnatco/ntc-tube/property';
