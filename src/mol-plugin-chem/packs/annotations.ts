import { loadMVS } from '../../extensions/mvs/load';
import { MolViewSpecBehavior } from '../../extensions/mvs/behavior';
import { loadMVSData } from '../../extensions/mvs/components/formats';
import { MVSData } from '../../extensions/mvs/mvs-data';
import { corePack } from './core';
import { defineChemPack } from '../types';

export const annotationsPack = defineChemPack({
    id: 'annotations',
    label: 'Annotations',
    description: 'MVS annotations, annotation-defined components, labels, tooltips, and color themes.',
    dependencies: [corePack],
    capabilities: ['annotation.custom', 'annotation.labels'],
    fileExtensions: ['mvsj', 'mvsx'],
    spec: {
        behaviors: [MolViewSpecBehavior],
    },
});

export { loadMVS, loadMVSData, MVSData };
export { MVSAnnotationsProvider } from '../../extensions/mvs/components/annotation-prop';
export { MVSAnnotationColorThemeProvider } from '../../extensions/mvs/components/annotation-color-theme';
export { MVSAnnotationTooltipsProvider } from '../../extensions/mvs/components/annotation-tooltips-prop';
