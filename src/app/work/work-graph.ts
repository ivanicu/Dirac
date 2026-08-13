export type WorkEdgeKind = 'depends_on' | 'informs' | 'blocks' | 'delegates_to'
    | 'supersedes' | 'returns_to' | 'shares_dependency' | 'splits_to'
    | 'merges_from' | 'fans_out' | 'fans_in';

export interface ScientificQuestion {
    readonly id: string;
    readonly statement: string;
    readonly falsificationCriteria: readonly string[];
    readonly version: number;
}

export interface WorkNode {
    readonly id: string;
    readonly title: string;
    readonly state: 'planned' | 'active' | 'blocked' | 'done' | 'cancelled' | 'superseded';
    readonly owners: readonly string[];
    readonly accountableRole: string;
    readonly sourceVersions: Readonly<Record<string, number>>;
}

export interface WorkEdge {
    readonly id: string;
    readonly from: string;
    readonly to: string;
    readonly kind: WorkEdgeKind;
    readonly version: number;
}

export interface WorkThread {
    readonly id: string;
    readonly programId: string;
    readonly question: ScientificQuestion;
    readonly nodes: readonly WorkNode[];
    readonly edges: readonly WorkEdge[];
    readonly version: number;
}

const ACYCLIC: ReadonlySet<WorkEdgeKind> = new Set([
    'depends_on', 'blocks', 'splits_to', 'merges_from', 'fans_out', 'fans_in',
]);

export function validateWorkThread(thread: WorkThread): readonly string[] {
    const errors: string[] = [];
    const ids = new Set(thread.nodes.map(node => node.id));
    if (ids.size !== thread.nodes.length) errors.push('duplicate work node id');
    if (new Set(thread.edges.map(edge => edge.id)).size !== thread.edges.length) {
        errors.push('duplicate work edge id');
    }
    for (const edge of thread.edges) {
        if (!ids.has(edge.from) || !ids.has(edge.to)) errors.push(`edge ${edge.id} has missing endpoint`);
        if (edge.from === edge.to) errors.push(`edge ${edge.id} is self-referential`);
    }
    const adjacency = new Map<string, string[]>();
    for (const edge of thread.edges.filter(edge => ACYCLIC.has(edge.kind))) {
        adjacency.set(edge.from, [...(adjacency.get(edge.from) || []), edge.to]);
    }
    const visiting = new Set<string>();
    const visited = new Set<string>();
    const walk = (id: string): boolean => {
        if (visiting.has(id)) return true;
        if (visited.has(id)) return false;
        visiting.add(id);
        const cycle = (adjacency.get(id) || []).some(walk);
        visiting.delete(id); visited.add(id);
        return cycle;
    };
    if ([...ids].some(walk)) errors.push('dependency topology contains a cycle');
    return errors;
}
