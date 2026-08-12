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
            complexRef: { kind: 'complex', id: 'CX9' },
            focusedObject: { kind: 'compound', id: 'C7' }, origin: 'navigation' });
        const restored = new ScientificContextStore();
        restored.restore(store.toUrlParams());
        expect(restored.current().programRef?.id).toBe('P1');
        expect(restored.current().complexRef?.id).toBe('CX9');
        expect(restored.current().focusedObject).toEqual({ kind: 'compound', id: 'C7' });
    });
});
