import type { ObjectKind, ObjectRef } from '../generated/commands';

export const WORK_LANES = ['understand', 'design', 'decide', 'make', 'test_learn'] as const;
export type WorkLane = typeof WORK_LANES[number];

export interface WorkVisualItem {
    readonly id: string;
    readonly key: string;
    readonly title: string;
    readonly lane: WorkLane;
    readonly status: string;
    readonly priority: number;
    readonly owner: string;
    readonly start?: string;
    readonly end?: string;
    readonly baselineStart?: string;
    readonly baselineEnd?: string;
    readonly progress: number;
    readonly dependencyIds: readonly string[];
}

export interface ScientificGraphNode {
    readonly id: string;
    readonly label: string;
    readonly kind: string;
    readonly status?: string;
    readonly ref?: ObjectRef;
    readonly x?: number;
    readonly y?: number;
}

export interface ScientificGraphEdge {
    readonly id: string;
    readonly source: string;
    readonly target: string;
    readonly label: string;
}

export interface ScientificGraphModel {
    readonly nodes: readonly ScientificGraphNode[];
    readonly edges: readonly ScientificGraphEdge[];
}

const nodeKey = (ref: ObjectRef): string => `${ref.kind}:${ref.id}`;
const humanize = (value: string): string => value.replace(/_/g, ' ');

export function toWorkVisualItems(items: readonly Record<string, any>[],
    packages: readonly Record<string, any>[] = []): WorkVisualItem[] {
    const packagesById = new Map(packages.map(item => [item.ref?.id, item]));
    return items.flatMap(item => {
        if (!item.ref?.id || !WORK_LANES.includes(item.lane)) return [];
        const baseline = packagesById.get(item.current_package?.supersedes_ref?.id);
        return [{
            id: String(item.ref.id), key: String(item.key || item.ref.id),
            title: String(item.title || item.key || item.ref.id), lane: item.lane,
            status: String(item.status || 'planned'), priority: Number(item.priority || 3),
            owner: String(item.owner?.id || 'Unassigned'),
            start: item.start_on ? String(item.start_on) : undefined,
            end: item.due_on ? String(item.due_on) : undefined,
            baselineStart: baseline?.start_on ? String(baseline.start_on) : undefined,
            baselineEnd: baseline?.due_on ? String(baseline.due_on) : undefined,
            progress: Number.isFinite(Number(item.progress_percent)) ? Number(item.progress_percent)
                : item.status === 'done' ? 100 : item.status === 'active' ? 50 : 0,
            dependencyIds: ((item.depends_on_refs || []) as Array<{ id?: string }>)
                .flatMap(ref => ref.id ? [String(ref.id)] : []),
        }];
    });
}

const durationDays = (item: WorkVisualItem): number => {
    if (!item.start || !item.end) return 0;
    return Math.max(1, Math.round((Date.parse(`${item.end}T00:00:00Z`)
        - Date.parse(`${item.start}T00:00:00Z`)) / 86_400_000) + 1);
};

/** Longest dated dependency chain; a deterministic schedule risk signal, not an invented forecast. */
export function criticalPathIds(items: readonly WorkVisualItem[]): readonly string[] {
    const byId = new Map(items.map(item => [item.id, item]));
    const memo = new Map<string, { duration: number; path: string[] }>();
    const visit = (id: string, visiting = new Set<string>()): { duration: number; path: string[] } => {
        if (memo.has(id)) return memo.get(id)!;
        if (visiting.has(id)) return { duration: 0, path: [] };
        const item = byId.get(id); if (!item) return { duration: 0, path: [] };
        const next = new Set(visiting); next.add(id);
        const bestParent = item.dependencyIds.map(parent => visit(parent, next))
            .sort((a, b) => b.duration - a.duration || a.path.join().localeCompare(b.path.join()))[0]
            || { duration: 0, path: [] };
        const result = { duration: bestParent.duration + durationDays(item), path: [...bestParent.path, id] };
        memo.set(id, result); return result;
    };
    return [...byId.keys()].map(id => visit(id))
        .sort((a, b) => b.duration - a.duration || a.path.join().localeCompare(b.path.join()))[0]?.path || [];
}

export interface ScheduleConflict { readonly first: string; readonly second: string; readonly owner: string; }

export function scheduleConflicts(items: readonly WorkVisualItem[]): readonly ScheduleConflict[] {
    const dated = items.filter(item => item.start && item.end && item.owner !== 'Unassigned');
    const conflicts: ScheduleConflict[] = [];
    for (let left = 0; left < dated.length; left++) for (let right = left + 1; right < dated.length; right++) {
        const a = dated[left]; const b = dated[right];
        if (a.owner === b.owner && a.start! <= b.end! && b.start! <= a.end!) {
            conflicts.push({ first: a.id, second: b.id, owner: a.owner });
        }
    }
    return conflicts;
}

export function workGraphModel(items: readonly WorkVisualItem[]): ScientificGraphModel {
    const laneIndex = new Map(WORK_LANES.map((lane, index) => [lane, index]));
    const nodes = items.map(item => ({
        id: item.id, label: `${item.key} · ${item.title}`, kind: item.lane,
        status: item.status, x: (laneIndex.get(item.lane) || 0) * 240,
        y: items.filter(candidate => candidate.lane === item.lane)
            .findIndex(candidate => candidate.id === item.id) * 100,
    }));
    const known = new Set(nodes.map(node => node.id));
    const edges = items.flatMap(item => item.dependencyIds
        .filter(dependency => known.has(dependency))
        .map(dependency => ({
            id: `depends:${dependency}:${item.id}`, source: dependency,
            target: item.id, label: 'depends on',
        })));
    return { nodes, edges };
}

export function laneLoadSeries(items: readonly WorkVisualItem[]): {
    readonly lanes: readonly string[]; readonly statuses: readonly string[];
    readonly values: Readonly<Record<string, readonly number[]>>;
} {
    const preferred = ['planned', 'ready', 'active', 'backlog', 'blocked', 'done'];
    const observed = new Set(items.map(item => item.status));
    const statuses = [...preferred.filter(status => observed.has(status)),
        ...[...observed].filter(status => !preferred.includes(status)).sort()];
    const values = Object.fromEntries(statuses.map(status => [status,
        WORK_LANES.map(lane => items.filter(item => item.lane === lane && item.status === status).length)]));
    return { lanes: WORK_LANES.map(humanize), statuses, values };
}

const refFrom = (value: unknown): ObjectRef | undefined => {
    const candidate = value as { kind?: string; id?: string } | undefined;
    return candidate?.kind && candidate.id
        ? { kind: candidate.kind as ObjectKind, id: candidate.id } : undefined;
};

export function programRelationGraph(program: Record<string, any>, workspace: string): ScientificGraphModel {
    const nodes = new Map<string, ScientificGraphNode>();
    const edges = new Map<string, ScientificGraphEdge>();
    const programRef = refFrom(program.ref) || { kind: 'program' as const, id: String(program.code || 'program') };
    const addNode = (ref: ObjectRef | undefined, label?: string, status?: string) => {
        if (!ref) return undefined;
        const id = nodeKey(ref);
        if (!nodes.has(id)) nodes.set(id, { id, label: label || ref.id, kind: ref.kind, status, ref });
        return id;
    };
    const addEdge = (source: ObjectRef | undefined, target: ObjectRef | undefined, label: string) => {
        const from = addNode(source); const to = addNode(target); if (!from || !to) return;
        const id = `${from}:${label}:${to}`;
        edges.set(id, { id, source: from, target: to, label: humanize(label) });
    };
    addNode(programRef, `${program.code || 'Program'} · ${program.name || programRef.id}`, program.lifecycle);
    for (const link of program.links || []) {
        const target = refFrom(link.object_ref); addNode(target, link.label || target?.id, link.status);
        addEdge(programRef, target, link.role || 'contains');
    }
    for (const edge of program.lineage || []) addEdge(refFrom(edge.source_ref), refFrom(edge.target_ref), edge.relation || 'related_to');
    for (const binding of program.evidence_bindings || []) {
        addEdge(refFrom(binding.subject_ref), refFrom(binding.evidence_ref), binding.relation || 'supported_by');
    }
    for (const work of program.work_items || []) {
        const ref = refFrom(work.ref); addNode(ref, `${work.key || 'Work'} · ${work.title || ref?.id}`, work.status);
        addEdge(programRef, ref, work.lane || 'work');
        for (const dependency of work.depends_on_refs || []) addEdge(refFrom(dependency), ref, 'unlocks');
        for (const execution of work.execution_refs || []) addEdge(ref, refFrom(execution), 'executes_as');
    }
    for (const record of program.reference_jobs || []) {
        const ref = refFrom(record.ref); if (!ref) continue;
        addNode(ref, record.title || record.name || record.sample_code || record.experiment_key
            || record.dataset_key || record.protocol_key || record.observation_key || ref.id, record.status);
        addEdge(programRef, ref, record.job_kind || 'record');
        addEdge(refFrom(record.batch_ref), ref, 'produces');
        addEdge(refFrom(record.sample_ref), ref, 'used_by');
        addEdge(refFrom(record.experiment_ref), ref, 'produces');
        addEdge(refFrom(record.protocol_version_ref), ref, 'governs');
    }
    const allowed: Record<string, ReadonlySet<string>> = {
        design: new Set(['program', 'objective', 'hypothesis', 'molecule', 'compound', 'compound_form', 'series', 'work_item']),
        structures: new Set(['program', 'complex', 'protein_structure', 'structure_observation', 'annotation',
            'review', 'analysis_snapshot', 'molecule', 'compound', 'sample', 'dataset_version', 'work_item']),
        campaigns: new Set(['program', 'campaign', 'series', 'molecule', 'compound', 'compound_form', 'substance_registration']),
        synthesis: new Set(['program', 'compound', 'compound_form', 'synthesis_route', 'reaction', 'batch', 'sample', 'quality_release']),
        experiments: new Set(['program', 'sample', 'assay', 'protocol', 'protocol_version', 'experiment', 'measurement', 'dataset', 'dataset_version']),
        knowledge: new Set(['program', 'hypothesis', 'claim', 'evidence', 'external_evidence_release',
            'external_evidence_record', 'dataset', 'dataset_version', 'structure_observation', 'annotation', 'review', 'analysis_snapshot']),
        runs: new Set(['program', 'work_item', 'work_package', 'mission', 'run', 'job', 'artifact', 'dataset_version']),
        programs: new Set([...nodes.values()].map(node => node.kind)),
    };
    const keep = allowed[workspace] || allowed.programs;
    const filteredNodes = [...nodes.values()].filter(node => keep.has(node.kind));
    const ids = new Set(filteredNodes.map(node => node.id));
    const filteredEdges = [...edges.values()].filter(edge => ids.has(edge.source) && ids.has(edge.target));
    return { nodes: filteredNodes, edges: filteredEdges };
}

export function graphKindSeries(model: ScientificGraphModel): readonly { name: string; value: number }[] {
    const counts = new Map<string, number>();
    for (const node of model.nodes) counts.set(node.kind, (counts.get(node.kind) || 0) + 1);
    return [...counts].map(([name, value]) => ({ name: humanize(name), value }))
        .sort((a, b) => b.value - a.value || a.name.localeCompare(b.name));
}
