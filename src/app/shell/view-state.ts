import type { ScientificContext } from '../context/scientific-context-store';
import type { ViewDefinition } from './registries';
import { VIEW_PLANS } from './workspace-plans';

export type DeliveryState = 'shell' | 'connected';
export type RuntimeState = 'needs-context' | 'loading' | 'partial' | 'ready' | 'degraded' | 'error';
export type EvidenceState = 'none' | 'unverified' | 'provenance-backed';

export interface ViewState {
    delivery: DeliveryState;
    runtime: RuntimeState;
    evidence: EvidenceState;
}

export function deriveViewState(definition: ViewDefinition, context: ScientificContext): ViewState {
    const present = new Set([
        context.programRef?.kind, context.complexRef?.kind, context.focusedObject?.kind,
        context.targetRef?.kind, context.campaignRef?.kind, context.seriesRef?.kind,
        ...context.selectedObjects.map(ref => ref.kind),
        ...context.activeHypotheses.map(ref => ref.kind),
    ].filter((kind): kind is NonNullable<typeof kind> => !!kind));
    const expected = definition.acceptedContext.length
        ? definition.acceptedContext : VIEW_PLANS[definition.id]?.plannedInputs || [];
    const hasContext = !expected.length || expected.some(kind => present.has(kind));
    return {
        delivery: definition.delivery,
        runtime: !hasContext ? 'needs-context'
            : definition.delivery === 'shell' ? 'partial' : 'loading',
        evidence: 'none',
    };
}
