import { defineChemPack } from '../types';

/**
 * The non-negotiable semantic and stateful base. PluginContext already owns
 * the built-in format, representation, theme, query, and manager registries.
 */
export const corePack = defineChemPack({
    id: 'core',
    label: 'Computational Chemistry Core',
    description: 'Data, provenance, hierarchy, selection, representations, measurements, state, and image export.',
    capabilities: [
        'data.formats',
        'data.provenance',
        'structure.hierarchy',
        'structure.assemblies',
        'structure.representations',
        'structure.selection',
        'structure.query',
        'structure.sequence',
        'structure.superposition',
        'structure.measurements',
        'chemistry.valence',
        'chemistry.interactions',
        'state.snapshots',
        'export.image',
    ],
    fileExtensions: [
        'cif', 'mmcif', 'bcif', 'pdb', 'ent', 'pdbqt', 'pqr',
        'mol', 'sdf', 'sd', 'mol2', 'xyz', 'gro', 'data', 'lammpstrj',
    ],
});
