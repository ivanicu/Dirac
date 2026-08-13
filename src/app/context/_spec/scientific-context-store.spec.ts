import { ScientificContextStore } from '../scientific-context-store';

describe('ScientificContextStore', () => {
    it('rejects A after focus moves to B', async () => {
        const store = new ScientificContextStore();
        store.focus({ kind: 'molecule', id: 'A' });
        const a = store.generation();
        const painted: string[] = [];
        const slowA = (async () => {
            await new Promise(resolve => setTimeout(resolve, 20));
            if (store.isCurrent(a)) painted.push('A');
        })();
        store.focus({ kind: 'molecule', id: 'B' });
        const b = store.generation();
        if (store.isCurrent(b)) painted.push('B');
        await slowA;
        expect(painted).toEqual(['B']);
    });

    it('does not advance on an identical focus', () => {
        const store = new ScientificContextStore();
        store.focus({ kind: 'molecule', id: 'A' });
        const generation = store.generation();
        store.focus({ kind: 'molecule', id: 'A' });
        expect(store.generation()).toBe(generation);
    });

    it('round-trips meaningful URL context', () => {
        const store = new ScientificContextStore();
        store.patch({ programRef: { kind: 'program', id: 'P1' },
            workItemRef: { kind: 'work_item', id: 'W4' },
            complexRef: { kind: 'complex', id: 'CX9' },
            moleculeRef: { kind: 'molecule', id: 'M8' },
            compoundRef: { kind: 'compound', id: 'C8' },
            sampleRef: { kind: 'sample', id: 'S8' },
            experimentRef: { kind: 'experiment', id: 'E8' },
            datasetVersionRef: { kind: 'dataset_version', id: 'D8' },
            moleculeSmiles: 'CCO',
            focusedObject: { kind: 'compound', id: 'C7' }, origin: 'navigation' });
        const restored = new ScientificContextStore();
        restored.restore(store.toUrlParams());
        expect(restored.current().programRef?.id).toBe('P1');
        expect(restored.current().workItemRef?.id).toBe('W4');
        expect(restored.current().complexRef?.id).toBe('CX9');
        expect(restored.current().moleculeRef?.id).toBe('M8');
        expect(restored.current().compoundRef?.id).toBe('C8');
        expect(restored.current().sampleRef?.id).toBe('S8');
        expect(restored.current().experimentRef?.id).toBe('E8');
        expect(restored.current().datasetVersionRef?.id).toBe('D8');
        expect(restored.current().moleculeSmiles).toBe('CCO');
        expect(restored.current().focusedObject).toEqual({ kind: 'compound', id: 'C7' });
    });
});
