import { handoffFor, WORKFLOW_HANDOFFS } from '../workflow-handoffs';
import type { ScientificContext } from '../../context/scientific-context-store';

const context = (patch: Partial<ScientificContext> = {}): ScientificContext => ({
    programRef: { kind: 'program', id: 'p' }, workItemRef: { kind: 'work_item', id: 'w' },
    selectedObjects: [], activeHypotheses: [], origin: 'selection', generation: 1, ...patch,
});

describe('workflow handoffs', () => {
    it('forms one eight-workspace loop', () => {
        expect(WORKFLOW_HANDOFFS).toHaveLength(8);
        expect(new Set(WORKFLOW_HANDOFFS.map(item => item.from)).size).toBe(8);
        expect(WORKFLOW_HANDOFFS.map(item => item.to.workspace)).toEqual([
            'structures', 'design', 'campaigns', 'synthesis',
            'experiments', 'knowledge', 'runs', 'programs',
        ]);
    });

    it('refuses a Design handoff without a molecule or compound', () => {
        expect(handoffFor('design', context()).ready).toBe(false);
        expect(handoffFor('design', context({ moleculeRef: { kind: 'molecule', id: 'm' } })).ready).toBe(true);
    });

    it('requires a physical sample before Experiments', () => {
        expect(handoffFor('synthesis', context({ moleculeRef: { kind: 'molecule', id: 'm' } })).ready).toBe(false);
        expect(handoffFor('synthesis', context({ focusedObject: { kind: 'sample', id: 's' } })).ready).toBe(true);
    });
});
