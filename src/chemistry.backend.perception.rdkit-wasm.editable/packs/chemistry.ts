import { SbNcbrPartialCharges } from '../../extensions/sb-ncbr/partial-charges/behavior';
import { wwPDBChemicalComponentDictionary } from '../../extensions/wwpdb/ccd/behavior';
import { PluginSpec } from '../../mol-plugin/spec';
import { defineChemPack } from '../types';
import { corePack } from './core';

export const chemistryPack = defineChemPack({
    id: 'chemistry',
    label: 'Chemical Properties',
    description: 'Partial charges, valence and non-covalent interactions, plus wwPDB chemical components.',
    dependencies: [corePack],
    capabilities: [
        'chemistry.valence',
        'chemistry.partial-charges',
        'chemistry.interactions',
        'chemistry.chemical-components',
    ],
    fileExtensions: ['mol', 'sdf', 'sd', 'mol2', 'pdbqt'],
    spec: {
        behaviors: [
            PluginSpec.Behavior(SbNcbrPartialCharges),
            PluginSpec.Behavior(wwPDBChemicalComponentDictionary),
        ],
    },
});

export { SbNcbrPartialChargesPropertyProvider } from '../../extensions/sb-ncbr/partial-charges/property';
export { SbNcbrPartialChargesColorThemeProvider } from '../../extensions/sb-ncbr/partial-charges/color';
export { InteractionsProvider } from '../../mol-model-props/computed/interactions';
