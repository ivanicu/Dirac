import { QualityAssessmentProvider } from '../../extensions/model-archive/quality-assessment/prop';
import { PLDDTConfidenceColorThemeProvider } from '../../extensions/model-archive/quality-assessment/color/plddt';
import { QmeanScoreColorThemeProvider } from '../../extensions/model-archive/quality-assessment/color/qmean';
import { PDBeStructureQualityReport } from '../../extensions/pdbe/structure-quality-report/behavior';
import { RCSBValidationReport } from '../../extensions/rcsb/validation-report/behavior';
import { PluginBehavior } from '../../mol-plugin/behavior/behavior';
import { PluginSpec } from '../../mol-plugin/spec';
import { ParamDefinition as PD } from '../../mol-util/param-definition';
import { defineChemPack } from '../types';
import { corePack } from './core';

/** Model Archive quality data without the React pairwise-score panel. */
const HeadlessModelArchiveQualityAssessment = PluginBehavior.create<{ autoAttach: boolean }>({
    name: 'chem-model-archive-quality-assessment',
    category: 'custom-props',
    display: {
        name: 'Model Archive Quality Assessment',
        description: 'Registers pLDDT, QMEAN, PAE, and other Model Archive quality data without UI components.',
    },
    ctor: class extends PluginBehavior.Handler<{ autoAttach: boolean }> {
        register() {
            this.ctx.customModelProperties.register(QualityAssessmentProvider, this.params.autoAttach);
            this.ctx.representation.structure.themes.colorThemeRegistry.add(PLDDTConfidenceColorThemeProvider);
            this.ctx.representation.structure.themes.colorThemeRegistry.add(QmeanScoreColorThemeProvider);
        }

        update(params: { autoAttach: boolean }) {
            const changed = this.params.autoAttach !== params.autoAttach;
            this.params.autoAttach = params.autoAttach;
            this.ctx.customModelProperties.setDefaultAutoAttach(QualityAssessmentProvider.descriptor.name, params.autoAttach);
            return changed;
        }

        unregister() {
            this.ctx.customModelProperties.unregister(QualityAssessmentProvider.descriptor.name);
            this.ctx.representation.structure.themes.colorThemeRegistry.remove(PLDDTConfidenceColorThemeProvider);
            this.ctx.representation.structure.themes.colorThemeRegistry.remove(QmeanScoreColorThemeProvider);
        }
    },
    params: () => ({ autoAttach: PD.Boolean(false) }),
});

export const validationPack = defineChemPack({
    id: 'validation',
    label: 'Validation and Quality',
    description: 'PDBe/RCSB validation plus headless Model Archive pLDDT, QMEAN, and pairwise quality data.',
    dependencies: [corePack],
    capabilities: ['annotation.validation', 'annotation.quality-assessment'],
    spec: {
        behaviors: [
            PluginSpec.Behavior(PDBeStructureQualityReport),
            PluginSpec.Behavior(RCSBValidationReport),
            PluginSpec.Behavior(HeadlessModelArchiveQualityAssessment),
        ],
    },
});

export { QualityAssessmentProvider };
export { PLDDTConfidenceColorThemeProvider, QmeanScoreColorThemeProvider };
export { StructureQualityReportProvider } from '../../extensions/pdbe/structure-quality-report/prop';
export { ValidationReportProvider } from '../../extensions/rcsb/validation-report/prop';
