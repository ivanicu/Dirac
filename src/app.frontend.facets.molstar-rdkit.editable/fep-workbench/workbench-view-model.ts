import type { RunReceipt } from './workbench-receipts';
import type { PolicyExecutionAxis, PreparedSystemOption, RunJob } from './workbench-types';

export type RunAggregateNode = {
    compoundId: string;
    relativeDdgKcalMol: number;
    uncertaintyKcalMol: number;
};

export type RunAggregateView = {
    status: string;
    resultDigest: string;
    passedLegCount: number;
    nodes: RunAggregateNode[];
    failedEdgeCount: number;
    cycleClosureCount: number;
    convergenceVerdictCount: number;
};

export type AggregatePanelView = Readonly<{
    verified: boolean;
    state: string;
    detail: string;
    acceptedLegs: string;
    convergence: string;
    ddg: string;
    boundary: string;
}>;

export type RunHistoryRowView = Readonly<{
    active: boolean;
    heading: string;
    runIdentifier: string;
    context: string;
    aggregate: string | null;
}>;

export type RunHistoryView = Readonly<{
    rows: RunHistoryRowView[];
    activeCount: number;
    historicalCount: number;
    meta: string;
}>;

export type RunJobRowView = Readonly<{
    leg: string;
    repeat: string;
    jobId: string;
    state: string;
    stateClass: string;
}>;

export type RunJobsView = Readonly<{
    empty: boolean;
    ready: boolean;
    emptyHeading: string;
    emptyDetail: string;
    rows: RunJobRowView[];
    executionMeta: string | null;
    resultCount: string | null;
    boundary: string | null;
}>;

export type PolicyExecutionRow = Readonly<{
    axis: string;
    verdict: 'CONFIRMED' | 'UNVERIFIED' | 'OVERTURNED';
    witness: string;
}>;

export type PreparationPolicyView = Readonly<{
    generated: boolean;
    ok: boolean;
    blocked: boolean;
    summary: string;
    blockers: PolicyExecutionRow[];
    rows: PolicyExecutionRow[];
}>;

function policyWitness(axis: PolicyExecutionAxis): string {
    const value = axis.witness ?? axis.witnesses ?? axis.observed_action
        ?? axis.reason ?? axis.details;
    if (value == null || value === '') return 'NO WITNESS RETURNED';
    if (typeof value === 'string' || typeof value === 'number'
        || typeof value === 'boolean') return String(value);
    try { return JSON.stringify(value); } catch { return 'WITNESS COULD NOT BE SERIALIZED'; }
}

export function preparationPolicyGate(system: PreparedSystemOption | null): {
    ok: boolean; blockers: PolicyExecutionRow[]; rows: PolicyExecutionRow[];
} {
    const execution = system?.receptor_report?.policy_execution;
    const rows: PolicyExecutionRow[] = !execution || !Object.keys(execution).length
        ? [{ axis: 'policy_execution', verdict: 'UNVERIFIED',
            witness: 'Backend returned no per-axis preparation execution evidence' }]
        : Object.entries(execution).sort(([left], [right]) => left.localeCompare(right))
            .map(([axis, evidence]) => {
                const raw = String(evidence?.verdict || 'UNVERIFIED').toUpperCase();
                const verdict: PolicyExecutionRow['verdict'] = raw === 'CONFIRMED'
                    ? 'CONFIRMED' : raw === 'OVERTURNED' ? 'OVERTURNED' : 'UNVERIFIED';
                return { axis, verdict, witness: policyWitness(evidence || {}) };
            });
    const blockers = rows.filter(row => row.verdict !== 'CONFIRMED');
    return { ok: blockers.length === 0, blockers, rows };
}

export function preparationPolicyViewFrom(
    system: PreparedSystemOption | null,
): PreparationPolicyView {
    const gate = preparationPolicyGate(system);
    return {
        generated: !!system,
        ok: gate.ok,
        blocked: !!system && !gate.ok,
        summary: system
            ? gate.ok ? 'ALL AXES CONFIRMED'
                : `${gate.blockers.length} BLOCKING AXIS${gate.blockers.length === 1 ? '' : 'ES'}`
            : 'NOT GENERATED',
        blockers: gate.blockers,
        rows: gate.rows,
    };
}

const RUN_JOB_STATE_CLASSES = new Set([
    'pending', 'queued', 'running', 'done', 'failed', 'blocked', 'refused', 'cancelled',
]);

export function runJobsViewFrom(jobs: readonly RunJob[], validated: boolean): RunJobsView {
    if (!jobs.length) return {
        empty: true,
        ready: validated,
        emptyHeading: validated
            ? 'SYSTEM READY · 6 JOBS NOT STARTED' : 'AWAITING PREPARED SYSTEM',
        emptyDetail: validated
            ? 'Complex + solvent × repeats 1–3. Start only after reviewing the bound system and protocol above.'
            : 'Six physical jobs will appear here: complex/solvent × repeats 1–3.',
        rows: [],
        executionMeta: null,
        resultCount: null,
        boundary: null,
    };
    const done = jobs.filter(job => job.state === 'done').length;
    const active = jobs.some(job => ['queued', 'running', 'pending'].includes(job.state));
    const failed = jobs.some(job => ['failed', 'blocked', 'refused'].includes(job.state));
    const cancelled = jobs.every(job => ['done', 'cancelled'].includes(job.state))
        && jobs.some(job => job.state === 'cancelled');
    const label = done === 6 ? 'PHYSICAL'
        : active ? 'RUNNING' : failed ? 'FAILED' : cancelled ? 'CANCELLED' : 'TERMINAL';
    return {
        empty: false,
        ready: false,
        emptyHeading: '',
        emptyDetail: '',
        rows: jobs.map(job => ({
            leg: String(job.leg).toUpperCase(),
            repeat: String(job.repeat),
            jobId: String(job.jobId),
            state: String(job.error || job.state.toUpperCase()),
            stateClass: RUN_JOB_STATE_CLASSES.has(job.state) ? job.state : 'unknown',
        })),
        executionMeta: `${done} / 6 COMPLETE · ${label}`,
        resultCount: `${done} · ${label}`,
        boundary: done === 6 ? '6 PHYSICAL LEG RESULTS · REVIEWING'
            : active ? 'PHYSICAL EXECUTION · NOT YET AN RBFE RESULT'
                : `${label} · NO RBFE RESULT CLAIMED`,
    };
}

function record(value: unknown): Record<string, unknown> | null {
    return !!value && typeof value === 'object' && !Array.isArray(value)
        ? value as Record<string, unknown> : null;
}

function visible(value: unknown, fallback: string, max = 512): string {
    const rendered = typeof value === 'string' || typeof value === 'number'
        ? String(value) : '';
    return (rendered.trim() || fallback).slice(0, max);
}

/** Missing or partial aggregate output remains UNVERIFIED; no UI defaults. */
export function runAggregateViewFrom(value: unknown): RunAggregateView | null {
    const candidate = record(value);
    if (!candidate) return null;
    const status = typeof candidate.status === 'string' ? candidate.status.trim() : '';
    const resultDigest = typeof candidate.result_digest === 'string'
        ? candidate.result_digest : '';
    const passedLegCount = Number(candidate.passed_leg_count);
    if (!status || !/^sha256:[0-9a-f]{64}$/.test(resultDigest)
        || !Number.isInteger(passedLegCount) || passedLegCount < 0
        || passedLegCount > 6 || !Array.isArray(candidate.node_estimates)
        || !Array.isArray(candidate.failed_edges)
        || !Array.isArray(candidate.cycle_closure)
        || !Array.isArray(candidate.convergence_verdicts)) return null;
    const nodes: RunAggregateNode[] = [];
    for (const raw of candidate.node_estimates) {
        const node = record(raw), compoundId = String(node?.compound_id || '');
        const relativeDdgKcalMol = Number(node?.relative_dg_kcal_mol);
        const uncertaintyKcalMol = Number(node?.uncertainty_kcal_mol);
        if (!node || !compoundId || !Number.isFinite(relativeDdgKcalMol)
            || !Number.isFinite(uncertaintyKcalMol) || uncertaintyKcalMol < 0) return null;
        nodes.push({ compoundId, relativeDdgKcalMol, uncertaintyKcalMol });
    }
    return {
        status,
        resultDigest,
        passedLegCount,
        nodes,
        failedEdgeCount: candidate.failed_edges.length,
        cycleClosureCount: candidate.cycle_closure.length,
        convergenceVerdictCount: candidate.convergence_verdicts.length,
    };
}

export function aggregatePanelViewFrom(
    value: unknown,
    endpointCompoundId?: string,
): AggregatePanelView {
    const aggregate = runAggregateViewFrom(value);
    if (!aggregate) return {
        verified: false,
        state: 'UNVERIFIED · MALFORMED / INCOMPLETE SERVER SUMMARY',
        detail: 'No UI default was substituted. Inspect the immutable RunSet artifacts before making a scientific claim.',
        acceptedLegs: '— / 6',
        convergence: 'UNVERIFIED',
        ddg: 'UNVERIFIED',
        boundary: 'COMPUTED STATE · AGGREGATE SUMMARY UNVERIFIED',
    };
    const endpoint = aggregate.nodes.find(node => node.compoundId === endpointCompoundId)
        || aggregate.nodes.find(node => node.relativeDdgKcalMol !== 0);
    return {
        verified: true,
        state: aggregate.status.toUpperCase(),
        detail: `${aggregate.resultDigest} · ${aggregate.failedEdgeCount} failed edges · ${aggregate.cycleClosureCount} cycle closures`,
        acceptedLegs: `${aggregate.passedLegCount} / 6`,
        convergence: `${aggregate.convergenceVerdictCount} SERVER VERDICTS`,
        ddg: endpoint
            ? `${endpoint.relativeDdgKcalMol.toFixed(3)} ± ${endpoint.uncertaintyKcalMol.toFixed(3)} kcal/mol`
            : 'UNVERIFIED',
        boundary: 'COMPUTED · UNRELEASED RBFE RESULT',
    };
}

function aggregateResult(snapshot: Record<string, unknown> | null): unknown {
    const output = record(snapshot?.aggregate_output);
    const result = record(output?.result);
    return result?.data ?? output?.result;
}

export function runHistoryViewFrom(
    active: RunReceipt | null,
    detachedValue: unknown,
): RunHistoryView {
    const detached = Array.isArray(detachedValue) ? detachedValue : [];
    const rows: RunHistoryRowView[] = [];
    if (active) rows.push({
        active: true,
        heading: `ACTIVE · ${active.state.toUpperCase()}`,
        runIdentifier: visible(active.run_id || active.request_key, 'RUN ID UNAVAILABLE'),
        context: `${visible(active.edge_id, 'EDGE UNKNOWN')} · generation ${active.campaign_scientific_ref.version}`,
        aggregate: null,
    });
    for (const raw of detached) {
        const row = record(raw);
        if (!row) continue;
        const snapshot = record(row.run_snapshot);
        const aggregate = runAggregateViewFrom(aggregateResult(snapshot));
        const state = visible(snapshot?.state ?? row.state ?? row.reason, 'detached').toUpperCase();
        const ddg = aggregate?.nodes.find(node => node.relativeDdgKcalMol !== 0)
            || aggregate?.nodes[0];
        rows.push({
            active: false,
            heading: state,
            runIdentifier: visible(row.run_id, 'RUN ID UNAVAILABLE'),
            context: `${visible(row.edge_id, 'EDGE UNKNOWN')} · ${visible(row.detached_at, 'TIME UNKNOWN')}`,
            aggregate: aggregate
                ? `${aggregate.passedLegCount}/6 legs · ${ddg
                    ? `${visible(ddg.compoundId, 'COMPOUND UNKNOWN')} ${ddg.relativeDdgKcalMol.toFixed(3)} ± ${ddg.uncertaintyKcalMol.toFixed(3)} kcal/mol`
                    : 'node estimate unavailable'} · ${aggregate.resultDigest}`
                : 'AGGREGATE UNVERIFIED / NOT APPLICABLE',
        });
    }
    return {
        rows,
        activeCount: active ? 1 : 0,
        historicalCount: rows.length - (active ? 1 : 0),
        meta: `${active ? 1 : 0} ACTIVE · ${rows.length - (active ? 1 : 0)} HISTORICAL`,
    };
}
