import type { ScientificContext } from '../context/scientific-context-store';
import type { ObjectKind } from '../generated/commands';
import type { ShellRoute } from './app-shell';
import type { WorkspaceId } from './registries';

export interface WorkflowHandoff {
    readonly from: WorkspaceId;
    readonly to: ShellRoute;
    readonly label: string;
    readonly purpose: string;
    readonly requires?: readonly ObjectKind[];
}

export const WORKFLOW_HANDOFFS: readonly WorkflowHandoff[] = [
    { from: 'programs', to: { workspace: 'structures', view: 'structures.complex' },
        label: 'Continue to Structures', purpose: 'Establish reviewed structural evidence.' },
    { from: 'structures', to: { workspace: 'design', view: 'design.builder' },
        label: 'Hand structure to Design', purpose: 'Design in the selected complex.', requires: ['complex'] },
    { from: 'design', to: { workspace: 'campaigns', view: 'campaigns.compounds' },
        label: 'Add molecule to Campaigns', purpose: 'Promote the current molecular proposal.', requires: ['molecule', 'compound'] },
    { from: 'campaigns', to: { workspace: 'synthesis', view: 'synthesis.make' },
        label: 'Request Make planning', purpose: 'Carry the selected chemical identity into synthesis.', requires: ['molecule', 'compound'] },
    { from: 'synthesis', to: { workspace: 'experiments', view: 'experiments.design' },
        label: 'Release sample to Experiments', purpose: 'Only a physical sample may enter testing.', requires: ['sample'] },
    { from: 'experiments', to: { workspace: 'knowledge', view: 'knowledge.evidence' },
        label: 'Publish results to Knowledge', purpose: 'Return governed experimental evidence.',
        requires: ['experiment', 'dataset_version', 'measurement'] },
    { from: 'knowledge', to: { workspace: 'runs', view: 'runs.active' },
        label: 'Inspect supporting Compute', purpose: 'Trace methods, Jobs, and artifacts.' },
    { from: 'runs', to: { workspace: 'programs', view: 'programs.overview' },
        label: 'Return evidence to Program', purpose: 'Review progress, evidence, and the next decision.' },
] as const;

const availableKinds = (context: ScientificContext): Set<ObjectKind> => new Set([
    context.programRef?.kind, context.workItemRef?.kind, context.complexRef?.kind,
    context.moleculeRef?.kind, context.compoundRef?.kind, context.sampleRef?.kind,
    context.experimentRef?.kind, context.datasetVersionRef?.kind,
    context.focusedObject?.kind,
    ...context.selectedObjects.map(ref => ref.kind),
].filter((kind): kind is ObjectKind => !!kind));

export function handoffFor(workspace: WorkspaceId, context: ScientificContext): {
    definition: WorkflowHandoff; ready: boolean; missing: readonly ObjectKind[];
} {
    const definition = WORKFLOW_HANDOFFS.find(item => item.from === workspace)!;
    const kinds = availableKinds(context);
    const alternatives = definition.requires || [];
    const ready = !!context.programRef && !!context.workItemRef
        && (!alternatives.length || alternatives.some(kind => kinds.has(kind)));
    return { definition, ready,
        missing: ready ? [] : (!context.programRef ? ['program']
            : !context.workItemRef ? ['work_item'] : alternatives) };
}
