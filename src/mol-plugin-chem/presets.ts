import type { ChemPack } from './types';
import { annotationsPack } from './packs/annotations';
import { chemistryPack } from './packs/chemistry';
import { collaborationPack } from './packs/collaboration';
import { densityPack } from './packs/density';
import { mdPack } from './packs/md';
import { nucleicPack } from './packs/nucleic';
import { publicationPack } from './packs/publication';
import { qmPack } from './packs/qm';
import { sitesPack } from './packs/sites';
import { validationPack } from './packs/validation';
import { visualR4Pack } from './visual-r4/pack';

/** Import this registry only in products that need runtime pack selection. */
export const chemPackRegistry = Object.freeze({
    annotations: annotationsPack,
    chemistry: chemistryPack,
    validation: validationPack,
    md: mdPack,
    density: densityPack,
    qm: qmPack,
    sites: sitesPack,
    nucleic: nucleicPack,
    publication: publicationPack,
    collaboration: collaborationPack,
    visualR4: visualR4Pack,
});

export type ChemPackId = keyof typeof chemPackRegistry;

export function selectChemPacks(ids: readonly ChemPackId[]): ChemPack[] {
    return [...new Set(ids)].map(id => chemPackRegistry[id]);
}

/**
 * Product-level selection matrix. `required` is the scientific identity of
 * the product; `betterToHave` can be omitted for a smaller deployment.
 * The non-negotiable corePack is always installed by createChemWorkbench.
 */
export const chemProductMatrix = Object.freeze({
    workbench: {
        required: ['annotations', 'chemistry'],
        betterToHave: ['publication'],
    },
    docking: {
        required: ['annotations', 'chemistry', 'sites'],
        betterToHave: ['validation', 'publication'],
    },
    md: {
        required: ['chemistry', 'md'],
        betterToHave: ['annotations', 'publication'],
    },
    density: {
        required: ['annotations', 'density'],
        betterToHave: ['validation', 'publication'],
    },
    quantum: {
        required: ['chemistry', 'density', 'qm'],
        betterToHave: ['annotations', 'publication'],
    },
    structuralBiology: {
        required: ['annotations', 'validation', 'density', 'nucleic'],
        betterToHave: ['sites', 'publication'],
    },
} as const satisfies Record<string, { required: readonly ChemPackId[], betterToHave: readonly ChemPackId[] }>);

export type ChemProductId = keyof typeof chemProductMatrix;

export function selectChemProductPacks(product: ChemProductId, includeBetterToHave = true): ChemPack[] {
    const selection = chemProductMatrix[product];
    return selectChemPacks(includeBetterToHave
        ? [...selection.required, ...selection.betterToHave]
        : selection.required
    );
}

export const chemWorkbenchPacks = selectChemProductPacks('workbench');
export const dockingProductPacks = selectChemProductPacks('docking');
export const mdProductPacks = selectChemProductPacks('md');
export const densityProductPacks = selectChemProductPacks('density');
export const quantumProductPacks = selectChemProductPacks('quantum');
export const structuralBiologyProductPacks = selectChemProductPacks('structuralBiology');

export const allChemPacks = Object.freeze(Object.values(chemPackRegistry));
