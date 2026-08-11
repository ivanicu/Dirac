/**
 * LigandStore contract tests — each one is a witness for a bug that shipped.
 */
import { LigandStore, requireScene, CoordSpaceError, heavyAtomsFromMolfile } from '../ligand-store';

const MOLFILE_22 = ['', '  mol*', '', ' 22 22  0  0  0  0  0  0  0  0999 V2000'].join('\n');
const MOLFILE_13 = ['', '  mol*', '', ' 13 13  0  0  0  0  0  0  0  0999 V2000'].join('\n');

function loci(store: LigandStore, molfile = MOLFILE_22) {
    store.setFromLoci({ molfile, label: 'REA · A:200', structureRef: {}, bundleRef: {}, cutoffA: 5 });
}

describe('LigandStore · one home', () => {
    it('replays the current value to a late subscriber', () => {
        const store = new LigandStore();
        loci(store);
        const seen: (string | null)[] = [];
        store.subscribe(l => seen.push(l ? l.label : null));
        // A facet mounted AFTER a molecule loaded must not sit blank until the
        // user happens to act — that was the practical failure.
        expect(seen).toEqual(['REA · A:200']);
    });

    it('bumps the generation on every real change and not on a no-op', () => {
        const store = new LigandStore();
        expect(store.generation()).toBe(0);
        loci(store);
        const g1 = store.generation();
        expect(g1).toBe(1);
        loci(store);                       // same molfile, same kind
        expect(store.generation()).toBe(g1);   // re-emitting would restart 5 SCFs
        loci(store, MOLFILE_13);
        expect(store.generation()).toBe(g1 + 1);
    });

    it('clear() is idempotent', () => {
        const store = new LigandStore();
        store.clear();
        expect(store.generation()).toBe(0);
        loci(store);
        store.clear();
        store.clear();
        expect(store.generation()).toBe(2);
        expect(store.current()).toBeNull();
    });
});

describe('LigandStore · stale-async guard', () => {
    it('isCurrent goes false once the focus moves', () => {
        const store = new LigandStore();
        loci(store);
        const g = store.generation();
        expect(store.isCurrent(g)).toBe(true);
        loci(store, MOLFILE_13);
        // The witness: a 6-minute SCF issued for the first molecule must be
        // discardable when it lands, or it renders into the wrong scene.
        expect(store.isCurrent(g)).toBe(false);
    });
});

describe('LigandStore · subscriber isolation', () => {
    it('one throwing subscriber does not stop the others', () => {
        const errors: unknown[] = [];
        const store = new LigandStore(e => errors.push(e));
        const reached: string[] = [];
        store.subscribe(() => { throw new Error('facet exploded'); });
        store.subscribe(l => { if (l) reached.push(l.label); });
        loci(store);
        expect(reached).toEqual(['REA · A:200']);
        expect(errors.length).toBe(1);
    });

    it('unsubscribing during emit is safe', () => {
        const store = new LigandStore();
        const calls: number[] = [];
        const off = store.subscribe(() => { calls.push(1); off(); });
        store.subscribe(() => { calls.push(2); });
        loci(store);
        expect(calls.length).toBeGreaterThanOrEqual(3);   // 1,2 on subscribe + emit
    });
});

describe('coordSpace is part of the type, not a comment', () => {
    it('requireScene throws a NAMED error for a 2D molecule', () => {
        const store = new LigandStore();
        store.setFromSketch2d({ molfile: MOLFILE_22, label: 'sketched' });
        const l = store.current()!;
        // The witness: only an early return kept a flat molecule out of the
        // cube pipeline. A flat molecule yields a confidently wrong field that
        // LOOKS perfectly aligned, which is the most dangerous shape of bug.
        expect(() => requireScene(l, 'field-wells')).toThrow(CoordSpaceError);
        expect(() => requireScene(l, 'field-wells')).toThrow(/needs scene coordinates/);
    });

    it('requireScene passes both 3D kinds through', () => {
        const store = new LigandStore();
        loci(store);
        expect(requireScene(store.current()!, 'field-wells').coordSpace).toBe('scene');
        store.setFromImport({ molfile: MOLFILE_13, label: 'aspirin', inchikey: 'BSYNRYMUTXBXSQ-UHFFFAOYSA-N', seed: 42 });
        expect(requireScene(store.current()!, 'field-wells').coordSpace).toBe('scene');
    });
});

describe('heavyAtomsFromMolfile', () => {
    it('reads the counts line, and returns 0 rather than NaN on garbage', () => {
        expect(heavyAtomsFromMolfile(MOLFILE_22)).toBe(22);
        expect(heavyAtomsFromMolfile(MOLFILE_13)).toBe(13);
        expect(heavyAtomsFromMolfile('')).toBe(0);
        expect(heavyAtomsFromMolfile('a\nb\nc\nnot a counts line')).toBe(0);
        // 0 matters: the prefetch policy compares heavyAtoms <= 40, and NaN
        // would make that comparison false and silently skip quantum prefetch.
    });
});
