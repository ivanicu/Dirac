import { shareableContextParams, transitionScientificContext,
    type SemanticScientificContext, type StructureSelection } from '../semantic-context';

const structureSelection = (): StructureSelection => ({
    kind: 'structure', scope: 'named', locality: 'cross-workspace', lifecycle: 'active',
    size: 2, version: 1, structureRef: { kind: 'complex', id: 'CX-1' },
    structureVersion: 3, model: '1', chain: 'A', residues: ['A:42', 'A:45'],
});

const context = (): SemanticScientificContext => ({
    tenantId: 'tenant-a', programRef: { kind: 'program', id: 'P-1' },
    workThreadId: 'WT-1', questionRef: 'Q-1',
    objectPath: [{ kind: 'complex', id: 'CX-1' }],
    focus: { kind: 'complex', id: 'CX-1' }, selection: structureSelection(),
    sourceVersions: { 'complex:CX-1': 3 }, permissionSnapshot: 'perm-1',
    contextHandle: 'opaque-handle', origin: 'selection', generation: 4,
});

describe('semantic scientific context', () => {
    it('clears subordinate context when Program changes', () => {
        const next = transitionScientificContext(context(), {
            kind: 'program', programRef: { kind: 'program', id: 'P-2' },
        });
        expect(next.programRef?.id).toBe('P-2');
        expect(next.workThreadId).toBeUndefined();
        expect(next.selection).toBeUndefined();
        expect(next.contextHandle).toBeUndefined();
    });

    it('marks exact selections stale when a source version changes', () => {
        const next = transitionScientificContext(context(), {
            kind: 'source-changed', objectKey: 'complex:CX-1', version: 4,
        });
        expect(next.selection?.lifecycle).toBe('stale');
        expect(next.sourceVersions['complex:CX-1']).toBe(4);
    });

    it('removes revoked focus and preserves only a non-leaking tombstone', () => {
        const next = transitionScientificContext(context(), {
            kind: 'permission-revoked', objectKeys: ['complex:CX-1'],
            permissionSnapshot: 'perm-2',
        });
        expect(next.focus).toBeUndefined();
        expect(next.objectPath).toEqual([]);
        expect(next.selection?.lifecycle).toBe('unauthorized');
    });

    it('does not serialize object identity or selection into a share URL', () => {
        const params = shareableContextParams(context());
        expect(params.toString()).toBe('ctx=opaque-handle');
        expect(params.toString()).not.toContain('CX-1');
        expect(params.toString()).not.toContain('P-1');
    });
});
