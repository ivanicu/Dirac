import { criticalPathIds, graphKindSeries, laneLoadSeries, programRelationGraph, scheduleConflicts,
    toWorkVisualItems, workGraphModel } from '../visual-models';

describe('scientific visualization adapters', () => {
    const work = toWorkVisualItems([
        { ref: { kind: 'work_item', id: 'a' }, key: 'SITE', title: 'Map site',
            lane: 'understand', status: 'done', priority: 1, start_on: '2026-08-01',
            due_on: '2026-08-03', depends_on_refs: [] },
        { ref: { kind: 'work_item', id: 'b' }, key: 'DESIGN', title: 'Design series',
            lane: 'design', status: 'active', priority: 1, depends_on_refs: [{ id: 'a' }] },
    ]);

    it('preserves real work dependencies without inventing progress', () => {
        const graph = workGraphModel(work);
        expect(graph.nodes).toHaveLength(2);
        expect(graph.edges).toEqual([{ id: 'depends:a:b', source: 'a', target: 'b', label: 'depends on' }]);
        expect(work[1].start).toBeUndefined();
    });

    it('builds lane/status counts only from observed work', () => {
        const series = laneLoadSeries(work);
        expect(series.values.done[0]).toBe(1);
        expect(series.values.active[1]).toBe(1);
        expect(series.statuses).toEqual(['active', 'done']);
        expect(Object.values(series.values).flat().reduce((a, b) => a + b, 0)).toBe(2);
    });

    it('derives critical paths and resource collisions from declared schedule facts', () => {
        const scheduled = toWorkVisualItems([
            { ref: { kind: 'work_item', id: 'a' }, key: 'A', title: 'A', lane: 'understand',
                status: 'active', owner: { id: 'scientist' }, start_on: '2026-08-01', due_on: '2026-08-05' },
            { ref: { kind: 'work_item', id: 'b' }, key: 'B', title: 'B', lane: 'design',
                status: 'ready', owner: { id: 'scientist' }, start_on: '2026-08-04', due_on: '2026-08-08',
                depends_on_refs: [{ id: 'a' }] },
        ]);
        expect(criticalPathIds(scheduled)).toEqual(['a', 'b']);
        expect(scheduleConflicts(scheduled)).toEqual([{ first: 'a', second: 'b', owner: 'scientist' }]);
    });

    it('builds one canonical cross-workspace graph and filters projections', () => {
        const program = {
            ref: { kind: 'program', id: 'p' }, code: 'P', name: 'Program', links: [
                { object_ref: { kind: 'compound', id: 'c' }, role: 'candidate' },
                { object_ref: { kind: 'sample', id: 's' }, role: 'material' },
            ], lineage: [
                { source_ref: { kind: 'compound', id: 'c' }, target_ref: { kind: 'sample', id: 's' }, relation: 'has_sample' },
            ], reference_jobs: [], evidence_bindings: [],
        };
        const campaigns = programRelationGraph(program, 'campaigns');
        const synthesis = programRelationGraph(program, 'synthesis');
        expect(campaigns.nodes.map(node => node.kind)).toEqual(['program', 'compound']);
        expect(synthesis.nodes.map(node => node.kind)).toEqual(['program', 'compound', 'sample']);
        expect(synthesis.edges.some(edge => edge.label === 'has sample')).toBe(true);
        expect(graphKindSeries(synthesis).reduce((sum, item) => sum + item.value, 0)).toBe(3);
    });
});
