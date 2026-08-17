import { describe, expect, it } from '@jest/globals';
import { DemoCompounds, FallbackEdges, FallbackNetwork } from './workbench-fixture';

describe('the T4L benchmark fixture', () => {
    it('shows a connected eight-ligand FEP planning network', () => {
        expect(DemoCompounds).toHaveLength(8);
        expect(new Set(DemoCompounds.map(row => row.id)).size).toBe(8);
        expect(FallbackNetwork.compounds).toBe(DemoCompounds);
        expect(FallbackEdges.length).toBeGreaterThanOrEqual(8);

        const known = new Set(DemoCompounds.map(row => row.id));
        const neighbours = new Map([...known].map(id => [id, new Set<string>()]));
        for (const edge of FallbackEdges) {
            expect(known.has(edge.left_id)).toBe(true);
            expect(known.has(edge.right_id)).toBe(true);
            expect(edge.left_id).not.toBe(edge.right_id);
            neighbours.get(edge.left_id)!.add(edge.right_id);
            neighbours.get(edge.right_id)!.add(edge.left_id);
        }
        const visited = new Set<string>();
        const pending = [DemoCompounds[0].id];
        while (pending.length) {
            const id = pending.pop()!;
            if (visited.has(id)) continue;
            visited.add(id);
            pending.push(...neighbours.get(id)!);
        }
        expect(visited).toEqual(known);
    });

    it('keeps the snapshot outside the free-energy result boundary', () => {
        expect(FallbackNetwork.claim_boundary).toMatch(/Network and atom-mapping proposal only/i);
        expect(FallbackNetwork.claim_boundary).toMatch(/No free energy is inferred/i);
    });
});
