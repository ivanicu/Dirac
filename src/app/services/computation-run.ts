import type { Envelope } from './dirac-client';

export type ComputationRunPhase = 'idle' | 'submitting' | 'queued' | 'running'
    | 'done' | 'refused' | 'failed' | 'cancelled' | 'observed';

export type ComputationArtifact = {
    role: string;
    id?: string | null;
    sha256?: string;
};

export type ComputationRunRecord = {
    command: string;
    phase: ComputationRunPhase;
    executor: 'service' | 'browser' | 'static';
    jobId?: string;
    methodId?: string;
    version?: string | null;
    seconds?: number;
    cache?: string;
    artifacts: ComputationArtifact[];
    provenance: Record<string, unknown>;
    note?: string;
    error?: { code: string; message: string; callerAction?: string };
};

const expectedRefusals = new Set([
    'PARSE', 'UNCONVERGED', 'UNPARAMETERIZED', 'BUDGET', 'UNSUPPORTED',
    'TOO_LARGE', 'OPEN_SHELL_SPIN_REQUIRED', 'INVALID_PARAMETERS',
]);

export function computationRunFromEnvelope(command: string, envelope: Envelope,
    requestedPhase?: ComputationRunPhase): ComputationRunRecord {
    const error = envelope.error;
    const phase = requestedPhase ?? (envelope.ok
        ? 'done'
        : expectedRefusals.has(error?.code || '') ? 'refused' : 'failed');
    return {
        command,
        phase,
        executor: 'service',
        jobId: String(envelope.meta?.job_id || '') || undefined,
        methodId: String(envelope.meta?.method_id || '') || undefined,
        version: envelope.meta?.version,
        seconds: typeof envelope.meta?.seconds === 'number' ? envelope.meta.seconds : undefined,
        cache: typeof envelope.meta?.cache === 'string' ? envelope.meta.cache : undefined,
        artifacts: (envelope.artifacts || []).map(artifact => ({
            role: artifact.role,
            id: artifact.id,
            sha256: artifact.sha256,
        })),
        provenance: (envelope.meta?.provenance || {}) as Record<string, unknown>,
        error: error ? {
            code: error.code,
            message: error.user_message || error.message,
            callerAction: error.caller_action,
        } : undefined,
    };
}

export function observedComputationRun(command: string,
    options: Omit<ComputationRunRecord, 'command' | 'phase' | 'artifacts'>
        & { artifacts?: ComputationArtifact[] }): ComputationRunRecord {
    return { command, phase: 'observed', artifacts: [], ...options };
}

export function failedComputationRun(command: string, error: unknown,
    phase: 'failed' | 'cancelled' = 'failed'): ComputationRunRecord {
    const message = error instanceof Error ? error.message : String(error);
    return {
        command, phase, executor: 'service', artifacts: [], provenance: {},
        error: { code: phase === 'cancelled' ? 'CANCELLED' : 'INTERNAL', message },
    };
}

function printable(value: unknown): string {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
        return String(value);
    }
    try { return JSON.stringify(value); } catch { return String(value); }
}

export function computationRunRows(run: ComputationRunRecord): Array<[string, string]> {
    const rows: Array<[string, string | undefined]> = [
        ['Executor', run.executor],
        ['Durable job', run.jobId || (run.executor === 'service' ? 'not recorded' : 'not used')],
        ['Method', run.methodId],
        ['Version', run.version ?? undefined],
        ['Compute time', run.seconds === undefined ? undefined : `${run.seconds} s`],
        ['Cache', run.cache],
        ['Artifacts', run.artifacts.length
            ? run.artifacts.map(artifact => `${artifact.role}${artifact.id ? ` · ${artifact.id}` : ''}`).join('; ')
            : 'none'],
        ['Provenance', Object.keys(run.provenance).length
            ? Object.entries(run.provenance).slice(0, 8)
                .map(([key, value]) => `${key}=${printable(value)}`).join(' · ')
            : 'not reported'],
    ];
    if (run.error) rows.push(['Refusal / error', `${run.error.code} · ${run.error.message}`]);
    if (run.error?.callerAction) rows.push(['Caller action', run.error.callerAction]);
    if (run.note) rows.push(['Evidence boundary', run.note]);
    return rows.filter((row): row is [string, string] => row[1] !== undefined);
}

const phaseLabels: Record<ComputationRunPhase, string> = {
    idle: 'Not started', submitting: 'Submitting', queued: 'Queued', running: 'Running',
    done: 'Done', refused: 'Refused', failed: 'Failed', cancelled: 'Cancelled',
    observed: 'Observed',
};

export function renderComputationRun(target: string | HTMLElement,
    run: ComputationRunRecord | null): void {
    const host = typeof target === 'string' ? document.getElementById(target) : target;
    if (!host) return;
    host.replaceChildren();
    if (!run) {
        host.hidden = true;
        return;
    }
    host.hidden = false;
    host.dataset.phase = run.phase;
    host.setAttribute('role', run.phase === 'failed' ? 'alert' : 'status');
    const header = document.createElement('header');
    const command = document.createElement('strong');
    command.textContent = run.command;
    const phase = document.createElement('span');
    phase.textContent = phaseLabels[run.phase];
    header.append(command, phase);
    const rows = document.createElement('dl');
    for (const [label, value] of computationRunRows(run)) {
        const group = document.createElement('div');
        const term = document.createElement('dt'); term.textContent = label;
        const description = document.createElement('dd'); description.textContent = value;
        group.append(term, description); rows.append(group);
    }
    host.append(header, rows);
}
