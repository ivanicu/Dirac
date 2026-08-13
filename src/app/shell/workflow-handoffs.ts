import type { ObjectRef } from '../domain/object-ref';
import type { ScientificContext } from '../context/scientific-context-store';
import type { ObjectKind } from '../generated/commands';
import type { ShellRoute } from './app-shell';
import type { WorkspaceId } from './registries';

export type HandoffLifecycle = 'draft' | 'offered' | 'accepted' | 'rejected'
    | 'returned' | 'delivered' | 'verified' | 'cancelled' | 'superseded';

export type HandoffTarget =
    | { readonly kind: 'person'; readonly actorId: string }
    | { readonly kind: 'role'; readonly role: string }
    | { readonly kind: 'team-queue'; readonly queueId: string }
    | { readonly kind: 'service'; readonly serviceId: string }
    | { readonly kind: 'multi'; readonly targets: readonly Exclude<HandoffTarget, { kind: 'multi' }>[] };

export interface AcceptanceClause {
    readonly id: string;
    readonly label: string;
    readonly satisfied: boolean;
    readonly evidenceRefs: readonly ObjectRef[];
    readonly reason?: string;
}

export interface HandoffRationale {
    readonly reasonCodes: readonly string[];
    readonly evidenceRefs: readonly ObjectRef[];
    readonly riskAcceptance?: { readonly code: string; readonly accountableActor: string };
    readonly note?: string;
}

export interface WorkflowHandoff {
    readonly id: string;
    readonly schemaVersion: 1;
    readonly version: number;
    readonly workThreadId: string;
    readonly sourceNodeIds: readonly string[];
    readonly target: HandoffTarget;
    readonly frozenPayloadRefs: readonly ObjectRef[];
    readonly liveQueryRefs: readonly string[];
    readonly acceptanceContract: readonly AcceptanceClause[];
    readonly permissionEnvelope: string;
    readonly lifecycle: HandoffLifecycle;
    readonly offeredBy: string;
    readonly accountableRole: string;
    readonly rationale: HandoffRationale;
    readonly deliveryParts: readonly {
        readonly id: string;
        readonly state: 'pending' | 'delivered' | 'verified' | 'returned';
        readonly outputRefs: readonly ObjectRef[];
    }[];
    readonly sla?: { readonly dueAt: string; readonly escalationPolicy: string };
}

export interface HandoffReadiness {
    readonly ready: boolean;
    readonly missing: readonly AcceptanceClause[];
}

/** Readiness has one source of truth: the versioned acceptance contract. */
export function deriveHandoffReadiness(handoff: WorkflowHandoff): HandoffReadiness {
    const missing = handoff.acceptanceContract.filter(clause => !clause.satisfied);
    return { ready: missing.length === 0, missing };
}

const TRANSITIONS: Readonly<Record<HandoffLifecycle, readonly HandoffLifecycle[]>> = {
    draft: ['offered', 'cancelled', 'superseded'],
    offered: ['accepted', 'rejected', 'cancelled', 'superseded'],
    accepted: ['delivered', 'returned', 'cancelled', 'superseded'],
    delivered: ['verified', 'returned', 'superseded'],
    returned: ['offered', 'cancelled', 'superseded'],
    rejected: ['superseded'],
    verified: ['superseded'],
    cancelled: ['superseded'],
    superseded: [],
};

export function transitionHandoff(handoff: WorkflowHandoff,
                                  lifecycle: HandoffLifecycle): WorkflowHandoff {
    if (!TRANSITIONS[handoff.lifecycle].includes(lifecycle)) {
        throw new Error(`handoff cannot transition ${handoff.lifecycle} -> ${lifecycle}`);
    }
    if (lifecycle === 'offered' && !deriveHandoffReadiness(handoff).ready) {
        throw new Error('handoff cannot be offered while acceptance clauses are missing');
    }
    return Object.freeze({ ...handoff, lifecycle, version: handoff.version + 1 });
}

/** Navigation is only a projection hint after a receipt; it never proves readiness. */
export interface HandoffRouteSuggestion {
    readonly from: WorkspaceId;
    readonly to: ShellRoute;
    readonly label: string;
}

export const HANDOFF_ROUTE_SUGGESTIONS: readonly HandoffRouteSuggestion[] = [
    { from: 'programs', to: { workspace: 'structures', view: 'structures.complex' }, label: 'Open Structures' },
    { from: 'structures', to: { workspace: 'design', view: 'design.builder' }, label: 'Open accepted work in Design' },
    { from: 'design', to: { workspace: 'campaigns', view: 'campaigns.compounds' }, label: 'Open candidate in Campaigns' },
    { from: 'campaigns', to: { workspace: 'synthesis', view: 'synthesis.make' }, label: 'Open accepted request in Make' },
    { from: 'synthesis', to: { workspace: 'experiments', view: 'experiments.design' }, label: 'Open allocated sample in Test' },
    { from: 'experiments', to: { workspace: 'knowledge', view: 'knowledge.evidence' }, label: 'Inspect released evidence' },
    { from: 'knowledge', to: { workspace: 'runs', view: 'runs.active' }, label: 'Inspect supporting execution' },
    { from: 'runs', to: { workspace: 'programs', view: 'programs.overview' }, label: 'Return to originating question' },
] as const;

/**
 * Transitional projection for the legacy shell button.
 *
 * This answers only whether the old client has enough local context to expose
 * its compatibility path. It is not an ActionOffer and must never be treated as
 * authoritative readiness. Phase 2 replaces its caller with Handoff offers.
 */
export function handoffFor(workspace: WorkspaceId, context: ScientificContext): {
    readonly definition: HandoffRouteSuggestion & { readonly purpose: string };
    readonly ready: boolean;
    readonly missing: readonly ObjectKind[];
} {
    const route = HANDOFF_ROUTE_SUGGESTIONS.find(item => item.from === workspace)!;
    const requirements: Partial<Record<WorkspaceId, readonly ObjectKind[]>> = {
        structures: ['complex'], design: ['molecule', 'compound'],
        campaigns: ['molecule', 'compound'], synthesis: ['sample'],
        experiments: ['experiment', 'dataset_version', 'measurement'],
    };
    const present = new Set<ObjectKind>([
        context.programRef?.kind, context.workItemRef?.kind, context.complexRef?.kind,
        context.moleculeRef?.kind, context.compoundRef?.kind, context.sampleRef?.kind,
        context.experimentRef?.kind, context.datasetVersionRef?.kind,
        context.focusedObject?.kind, ...context.selectedObjects.map(ref => ref.kind),
    ].filter((value): value is ObjectKind => !!value));
    const alternatives = requirements[workspace] || [];
    const ready = !!context.programRef && !!context.workItemRef
        && (!alternatives.length || alternatives.some(value => present.has(value)));
    const missing: readonly ObjectKind[] = ready ? [] : !context.programRef ? ['program']
        : !context.workItemRef ? ['work_item'] : alternatives;
    return {
        definition: { ...route, purpose: 'Legacy navigation compatibility; authority must revalidate.' },
        ready, missing,
    };
}
