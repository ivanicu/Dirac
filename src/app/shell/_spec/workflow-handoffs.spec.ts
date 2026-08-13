import { deriveHandoffReadiness, HANDOFF_ROUTE_SUGGESTIONS, transitionHandoff,
    type WorkflowHandoff } from '../workflow-handoffs';

const handoff = (satisfied = true): WorkflowHandoff => ({
    id: 'HO-1', schemaVersion: 1, version: 1, workThreadId: 'WT-1',
    sourceNodeIds: ['WN-1'], target: { kind: 'team-queue', queueId: 'design' },
    frozenPayloadRefs: [{ kind: 'complex', id: 'CX-1' }], liveQueryRefs: [],
    acceptanceContract: [{
        id: 'site-reviewed', label: 'Site is reviewed', satisfied,
        evidenceRefs: satisfied ? [{ kind: 'review', id: 'REV-1' }] : [],
        reason: satisfied ? undefined : 'Peer review is missing',
    }],
    permissionEnvelope: 'perm-1', lifecycle: 'draft', offeredBy: 'scientist:a',
    accountableRole: 'structural-scientist',
    rationale: { reasonCodes: ['design-input'], evidenceRefs: [] },
    deliveryParts: [],
});

describe('workflow handoff protocol', () => {
    it('keeps route suggestions separate from readiness authority', () => {
        expect(HANDOFF_ROUTE_SUGGESTIONS).toHaveLength(8);
        expect(new Set(HANDOFF_ROUTE_SUGGESTIONS.map(item => item.from)).size).toBe(8);
        expect(HANDOFF_ROUTE_SUGGESTIONS.every(item => !('ready' in item))).toBe(true);
    });

    it('derives missing clauses from one acceptance contract', () => {
        const result = deriveHandoffReadiness(handoff(false));
        expect(result.ready).toBe(false);
        expect(result.missing.map(item => item.id)).toEqual(['site-reviewed']);
    });

    it('refuses offering incomplete work', () => {
        expect(() => transitionHandoff(handoff(false), 'offered'))
            .toThrow('acceptance clauses are missing');
    });

    it('enforces lifecycle and version progression', () => {
        const offered = transitionHandoff(handoff(), 'offered');
        const accepted = transitionHandoff(offered, 'accepted');
        const delivered = transitionHandoff(accepted, 'delivered');
        expect(delivered.version).toBe(4);
        expect(() => transitionHandoff(delivered, 'accepted'))
            .toThrow('delivered -> accepted');
    });
});
