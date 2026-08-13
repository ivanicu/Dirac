import type { ObjectRef } from '../domain/object-ref';

export type ProjectionChannel = 'human' | 'cli' | 'agent' | 'automation';
export type ConsequenceClass = 'exploration' | 'scientific-record' | 'scientific-identity'
    | 'material' | 'external-release' | 'compute' | 'governance';

export interface ApplicationActionDefinition {
    readonly id: string;
    readonly version: number;
    readonly intent: string;
    readonly inputSchema: Readonly<Record<string, unknown>>;
    readonly consequenceClass: ConsequenceClass;
    readonly authorizationPolicy: string;
    readonly preconditionPolicy: string;
    readonly idempotencyPolicy: string;
    readonly conflictPolicy: string;
    readonly transactionPolicy: string;
    readonly receiptSchema: Readonly<Record<string, unknown>>;
}

export interface ActionOffer {
    readonly offerId: string;
    readonly actionId: string;
    readonly actionVersion: number;
    readonly actorId: string;
    readonly subjects: readonly ObjectRef[];
    readonly selectionDigest?: string;
    readonly permissionEnvelope: string;
    readonly preconditions: readonly { readonly id: string; readonly satisfied: boolean }[];
    readonly expiresAt: string;
}

export interface ActionPreview {
    readonly previewId: string;
    readonly preconditionToken: string;
    readonly sourceVersions: Readonly<Record<string, number>>;
    readonly proposedEffects: readonly Readonly<Record<string, unknown>>[];
    readonly warnings: readonly string[];
    readonly requiredAcknowledgements: readonly string[];
    readonly expiresAt: string;
}

export interface ActionReceipt {
    readonly operationId: string;
    readonly actionId: string;
    readonly actionVersion: number;
    readonly attemptId: string;
    readonly actorId: string;
    readonly status: 'committed' | 'partial' | 'refused' | 'compensated';
    readonly appliedEffects: readonly Readonly<Record<string, unknown>>[];
    readonly failedEffects: readonly Readonly<Record<string, unknown>>[];
    readonly compensation: readonly Readonly<Record<string, unknown>>[];
    readonly outputRefs: readonly ObjectRef[];
    readonly sourceVersions: Readonly<Record<string, number>>;
    readonly committedAt: string;
    readonly recoveryActions: readonly string[];
}

export interface ActionProjection {
    readonly channel: ProjectionChannel;
    readonly actionId: string;
    readonly actionVersion: number;
    readonly surfaceId: string;
    readonly label: string;
}

export type HumanActionProjection = ActionProjection & {
    readonly channel: 'human';
    readonly accessibleName: string;
    readonly inputMode: 'button' | 'keyboard' | 'context-menu' | 'drag-drop' | 'touch' | 'palette';
};
export type CliActionProjection = ActionProjection & { readonly channel: 'cli'; readonly command: string };
export type AgentActionProjection = ActionProjection & { readonly channel: 'agent'; readonly tool: string };
export type AutomationActionProjection = ActionProjection & { readonly channel: 'automation'; readonly trigger: string };

export interface ApplicationActionClient {
    offers(subjects: readonly ObjectRef[], actionId?: string): Promise<readonly ActionOffer[]>;
    preview(offerId: string, input: Readonly<Record<string, unknown>>): Promise<ActionPreview>;
    commit(preconditionToken: string, input: Readonly<Record<string, unknown>>, options: {
        readonly idempotencyKey: string;
        readonly attemptId: string;
        readonly acknowledgements: readonly string[];
    }): Promise<ActionReceipt>;
}

export function actionKey(action: Pick<ApplicationActionDefinition, 'id' | 'version'>): string {
    if (!/^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+$/.test(action.id)) {
        throw new Error(`invalid semantic action id ${action.id}`);
    }
    if (!Number.isInteger(action.version) || action.version < 1) {
        throw new Error(`invalid action version ${action.version}`);
    }
    return `${action.id}@${action.version}`;
}
