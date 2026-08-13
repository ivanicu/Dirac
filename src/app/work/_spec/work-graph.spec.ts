import { validateWorkThread, type WorkThread } from '../work-graph';

const thread = (): WorkThread => ({
    id: 'WT-1', programId: 'P-1', version: 1,
    question: { id: 'Q-1', statement: 'Which series should advance?',
        falsificationCriteria: ['No replicated selectivity'], version: 1 },
    nodes: [
        { id: 'structure', title: 'Interpret site', state: 'done', owners: ['a'],
            accountableRole: 'structural-scientist', sourceVersions: {} },
        { id: 'design-a', title: 'Design A', state: 'active', owners: ['b'],
            accountableRole: 'medicinal-chemist', sourceVersions: {} },
        { id: 'design-b', title: 'Design B', state: 'active', owners: ['c'],
            accountableRole: 'medicinal-chemist', sourceVersions: {} },
        { id: 'decision', title: 'Select series', state: 'planned', owners: ['d'],
            accountableRole: 'program-lead', sourceVersions: {} },
    ],
    edges: [
        { id: 'e1', from: 'structure', to: 'design-a', kind: 'fans_out', version: 1 },
        { id: 'e2', from: 'structure', to: 'design-b', kind: 'fans_out', version: 1 },
        { id: 'e3', from: 'design-a', to: 'decision', kind: 'fans_in', version: 1 },
        { id: 'e4', from: 'design-b', to: 'decision', kind: 'fans_in', version: 1 },
    ],
});

describe('WorkThread graph', () => {
    it('supports fan-out and fan-in', () => {
        expect(validateWorkThread(thread())).toEqual([]);
    });

    it('convicts dependency cycles without banning return/inform relations', () => {
        const value = thread();
        expect(validateWorkThread({ ...value, edges: [...value.edges,
            { id: 'cycle', from: 'decision', to: 'structure', kind: 'depends_on', version: 1 },
        ] })).toContain('dependency topology contains a cycle');
        expect(validateWorkThread({ ...value, edges: [...value.edges,
            { id: 'return', from: 'decision', to: 'structure', kind: 'returns_to', version: 1 },
        ] })).toEqual([]);
    });
});
