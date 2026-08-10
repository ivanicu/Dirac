import { composeChemSpec, resolveChemPacks } from '../compose';
import { getChemFileExtension, suggestChemPacks } from '../detect';
import { allChemPacks, mdProductPacks, quantumProductPacks, selectChemProductPacks } from '../presets';
import { corePack } from '../packs/core';
import { orderR4Graph } from '../visual-r4/graph';
import { r4StyleGraphs } from '../visual-r4/catalog';

describe('computational chemistry packs', () => {
    it('resolves core once and before dependent packs', () => {
        const resolved = resolveChemPacks(mdProductPacks);
        expect(resolved[0]).toBe(corePack);
        expect(resolved.filter(pack => pack.id === 'core')).toHaveLength(1);
        expect(new Set(resolved.map(pack => pack.id)).size).toBe(resolved.length);
    });

    it('composes each registration list without mutating the base spec', () => {
        const base = { actions: [], behaviors: [], animations: [], customFormats: [], config: [] };
        const composed = composeChemSpec(quantumProductPacks, base);
        expect(composed).not.toBe(base);
        expect(base.actions).toHaveLength(0);
        expect(composed.actions?.length).toBeGreaterThan(0);
    });

    it('detects compressed file extensions and suggests only supplied packs', () => {
        expect(getChemFileExtension('trajectory.XTC.gz?download=1')).toBe('xtc');
        const suggestions = suggestChemPacks(['density.cube', 'trajectory.xtc.gz'], allChemPacks);
        expect(suggestions.map(pack => pack.id)).toEqual(expect.arrayContaining(['core', 'md', 'qm']));
    });

    it('selects the required-only or complete product matrix', () => {
        const required = selectChemProductPacks('structuralBiology', false).map(pack => pack.id);
        const complete = selectChemProductPacks('structuralBiology').map(pack => pack.id);
        expect(required).toEqual(['annotations', 'validation', 'density', 'nucleic']);
        expect(complete).toEqual([...required, 'sites', 'publication']);
    });

    it('orders every R4 representation graph without missing nodes or cycles', () => {
        for (const graph of Object.values(r4StyleGraphs)) {
            expect(orderR4Graph(graph)).toHaveLength(graph.nodes.length);
        }
    });
});
