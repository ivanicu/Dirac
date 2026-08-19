import { DiracClient, type ArtifactRef, type Envelope } from '../../app/services/dirac-client';

export type ResearchProvider = {
    profile_id: string;
    profile_digest?: string;
    label: string;
    configured_model: string;
    locality: 'local_network' | 'external_cloud';
    external_egress: boolean;
    allowed_classifications: string[];
    configured: boolean;
    reason?: string;
};

export type ResearchProgram = {
    ref: { kind: 'program'; id: string };
    code?: string;
    name: string;
    lifecycle?: string;
};

export type ResearchArtifactRef = {
    kind: 'artifact'; id: string; sha256?: string;
};

export type ResearchLoopSnapshot = {
    run_ref: { kind: 'run'; id: string };
    state: string; stage: string; version: number; iteration: number;
    goal: { intent: string };
    provider: { profile_id: string; profile_digest: string };
    budget: { remaining: Record<string, number>; spent: Record<string, number> };
    context_ref?: ResearchArtifactRef | null;
    proposal_ref?: ResearchArtifactRef | null;
    pending_action?: Record<string, any> | null;
    attention: Record<string, any>;
    events: Array<Record<string, any>>;
    deep_links: Record<string, string>;
    claim_boundary: 'model_proposal_not_scientific_evidence';
};

export type ResearchContext = {
    facts?: Array<Record<string, any>>;
    action_history?: Array<Record<string, any>>;
    created_at?: string;
};

export type ResearchProposal = {
    summary?: string;
    hypothesis_drafts?: Array<Record<string, any>>;
    candidate_actions?: Array<Record<string, any>>;
    preferred_action_id?: string | null;
    stop_recommendation?: Record<string, any>;
};

export type LoopCreateInput = {
    request_key: string;
    program_ref: { kind: 'program'; id: string };
    campaign_ref: { kind: 'campaign'; id: string };
    intent: string;
    autonomy_class: 'A2';
    provider_profile_id: string;
    data_classification: string;
    budget: {
        max_reasoner_calls: number; max_iterations: number;
        max_fep_runsets: number; max_gpu_hours: number;
        max_external_cost: number;
    };
    policy: {
        auto_risk_classes: string[]; per_action_risk_classes: string[];
        human_only_risk_classes: string[];
        stop_on_campaign_stale: true; stop_on_open_identity_conflict: true;
        max_same_subject_actions: number; cloud_egress_approved: boolean;
    };
};

function refusal(envelope: Envelope): Error {
    const error = envelope.error;
    const message = error?.user_message || error?.message || 'Dirac refused the research-loop operation';
    return Object.assign(new Error(message), {
        code: error?.code || 'INTERNAL', callerAction: error?.caller_action || '',
    });
}

function data<T>(envelope: Envelope): T {
    if (!envelope.ok) throw refusal(envelope);
    return (envelope.data || {}) as T;
}

function artifact(ref: ResearchArtifactRef, role: string): ArtifactRef {
    return {
        id: ref.id,
        sha256: String(ref.sha256 || '').replace(/^sha256:/, ''),
        role, media_type: 'application/json', size_bytes: 0,
        encoding: 'identity', url: `/v2/artifacts/${ref.id}`,
    };
}

export class ResearchLoopClient {
    constructor(readonly client: DiracClient) {}

    async providers(): Promise<ResearchProvider[]> {
        return data<{ profiles: ResearchProvider[] }>(
            await this.client.execute('ai.provider.list')).profiles;
    }

    async programs(): Promise<ResearchProgram[]> {
        return data<{ programs: ResearchProgram[] }>(
            await this.client.execute('program.list', { limit: 200 })).programs;
    }

    async create(input: LoopCreateInput): Promise<Record<string, any>> {
        return data(await this.client.execute(
            'research.loop.create', input, { requestId: input.request_key }));
    }

    async get(runId: string): Promise<ResearchLoopSnapshot> {
        return data(await this.client.execute('research.loop.get', {
            run_ref: { kind: 'run', id: runId },
        }));
    }

    async approve(loop: ResearchLoopSnapshot, fingerprint: string,
                  acknowledgements: string[], rationale: string): Promise<Record<string, any>> {
        return data(await this.client.execute('research.loop.approve', {
            run_ref: loop.run_ref, expected_version: loop.version,
            action_fingerprint: fingerprint, acknowledgements, rationale,
        }));
    }

    async reject(loop: ResearchLoopSnapshot, fingerprint: string,
                 rationale: string): Promise<Record<string, any>> {
        return data(await this.client.execute('research.loop.reject', {
            run_ref: loop.run_ref, expected_version: loop.version,
            action_fingerprint: fingerprint, rationale,
        }));
    }

    async control(loop: ResearchLoopSnapshot, action: string, rationale: string,
                  extra: Record<string, unknown> = {}): Promise<Record<string, any>> {
        return data(await this.client.execute('research.loop.control', {
            run_ref: loop.run_ref, expected_version: loop.version,
            action, rationale, ...extra,
        }));
    }

    async context(ref?: ResearchArtifactRef | null): Promise<ResearchContext | null> {
        return ref ? this.jsonArtifact<ResearchContext>(ref, 'research.context_snapshot') : null;
    }

    async proposal(ref?: ResearchArtifactRef | null): Promise<ResearchProposal | null> {
        return ref ? this.jsonArtifact<ResearchProposal>(ref, 'research.proposal') : null;
    }

    private async jsonArtifact<T>(ref: ResearchArtifactRef, role: string): Promise<T> {
        const fetched = await this.client.fetchArtifact(artifact(ref, role));
        return JSON.parse(fetched.text()) as T;
    }
}
