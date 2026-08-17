import {
    exactRef,
    exactRunBindingMatches,
    type ExactOperationBinding,
    type ExactRunBinding,
} from './workbench-state';

export type CampaignScientificRef = {
    kind: 'rbfe_campaign';
    id: string;
    version: number;
    sha256: string;
};

export type ContentRef = { kind: string; id: string; sha256: string };
export type ArtifactRef = { kind: 'artifact'; id: string; sha256: string };

export type PreparationReceiptStatus = 'submitting' | 'waiting'
    | 'detached' | 'cancel_requested';

/**
 * Written before the prepare command leaves the browser. A null job_id means
 * the acknowledgement is unknown, not that no server Job exists.
 */
export type PreparationReceipt = {
    schema_version: 3;
    owner_token: string;
    created_at: string;
    updated_at: string;
    status: PreparationReceiptStatus;
    request_nonce: string;
    request_key: string;
    job_id: string | null;
    campaign_scientific_ref: CampaignScientificRef;
    input_version: number;
    input_signature: string;
};

export type LegacyPreparationReceipt = {
    schema_version: 2;
    job_id: string;
    campaign_id: string;
    submitted_at: string;
    input_version: number;
    input_scientific_generation: number;
    input_scientific_digest: string;
    input_signature: string;
};

export type AnyPreparationReceipt = PreparationReceipt | LegacyPreparationReceipt;
export type AcknowledgedPreparationReceipt = AnyPreparationReceipt & { job_id: string };

export type PreparationReceiptBinding = Readonly<{
    campaignScientificRef: CampaignScientificRef;
    inputVersion: number;
    inputSignature: string;
    submittedAt: string;
    jobId: string | null;
    requestKey: string | null;
}>;

export type PlannerReceipt = {
    schema_version: 2;
    owner_token: string;
    created_at: string;
    status: 'submitting' | 'waiting' | 'detached' | 'cancel_requested';
    job_id: string | null;
    campaign_scientific_ref: CampaignScientificRef;
    prepared_system_ref: ContentRef;
    input_signature: string;
};

export type PlannerOutputReceipt = {
    schema_version: 1;
    network_job_id: string;
    created_at: string;
    campaign_scientific_ref: CampaignScientificRef;
    prepared_system_ref: ContentRef;
    plan_network_ref: ArtifactRef;
};

export type RunReceiptState = 'creating' | 'pending' | 'running'
    | 'aggregating' | 'cancel_requested' | 'blocked' | 'completed'
    | 'cancelled' | 'failed' | 'refused';

export type RunReceipt = {
    schema_version: 2 | 3;
    owner_token: string;
    created_at: string;
    updated_at: string;
    run_id: string | null;
    request_key: string;
    state: RunReceiptState;
    campaign_scientific_ref: CampaignScientificRef;
    edge_id: string;
    spec_digest: string;
    edge_spec_ref: ArtifactRef;
    edge_network_ref: ArtifactRef;
    complex_transformation_ref: ArtifactRef;
    solvent_transformation_ref: ArtifactRef;
    plan_network_job_id?: string;
    plan_network_ref?: ArtifactRef;
    prepared_system_ref?: ContentRef;
    parent_pose_ref?: ContentRef;
    proposal_pose_ref?: ContentRef;
};

const RUN_STATES: readonly RunReceiptState[] = [
    'creating', 'pending', 'running', 'aggregating', 'cancel_requested',
    'blocked', 'completed', 'cancelled', 'failed', 'refused',
];
const PREPARATION_STATES: readonly PreparationReceiptStatus[] = [
    'submitting', 'waiting', 'detached', 'cancel_requested',
];
const PREPARATION_REQUEST_KEY_PREFIX = 'rbfe-prepare:v1';

export function fullJobId(value: string): boolean {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export function campaignScientificRefFrom(
    campaignId: string,
    generation: number,
    digest: string,
): CampaignScientificRef | null {
    return campaignId && Number.isInteger(generation) && generation > 0
        && /^sha256:[0-9a-f]{64}$/.test(digest)
        ? { kind: 'rbfe_campaign', id: campaignId, version: generation, sha256: digest }
        : null;
}

/**
 * One durable logical attempt gets one key. The exact saved generation is
 * visible in the key, while a UUID nonce permits an explicit new attempt only
 * after a known terminal failure. The backend still seals the complete payload
 * digest and rejects key rebinding.
 */
export function preparationRequestKey(ref: CampaignScientificRef, requestNonce: string): string {
    return `${PREPARATION_REQUEST_KEY_PREFIX}:${ref.id}:${ref.version}:${ref.sha256}:${requestNonce}`;
}

export function preparationRequestKeyMatches(
    requestKey: string,
    ref: CampaignScientificRef,
    requestNonce: string,
): boolean {
    return fullJobId(requestNonce) && requestKey.length <= 256
        && requestKey === preparationRequestKey(ref, requestNonce);
}

export function contentRef(value: unknown, kind: string): ContentRef | null {
    const ref = exactRef(value);
    return ref?.kind === kind ? { kind, id: ref.id, sha256: ref.sha256 } : null;
}

export function strictArtifactRef(value: unknown): ArtifactRef | null {
    const ref = exactRef(value);
    return ref?.kind === 'artifact'
        ? { kind: 'artifact', id: ref.id, sha256: ref.sha256 } : null;
}

export function preparationReceiptFrom(value: unknown): PreparationReceipt | null {
    try {
        const row = value as Record<string, any> | null;
        if (row?.schema_version !== 3
            || row.campaign_scientific_ref?.kind !== 'rbfe_campaign') return null;
        const campaign = campaignScientificRefFrom(
            String(row.campaign_scientific_ref?.id || ''),
            Number(row.campaign_scientific_ref?.version),
            String(row.campaign_scientific_ref?.sha256 || ''),
        );
        const jobId = row.job_id == null ? null : String(row.job_id);
        const status = String(row.status) as PreparationReceiptStatus;
        const requestNonce = String(row.request_nonce || '');
        const requestKey = String(row.request_key || '');
        const inputVersion = Number(row.input_version);
        const createdAt = String(row.created_at || '');
        const updatedAt = String(row.updated_at || '');
        if (!campaign || !String(row.owner_token || '')
            || !PREPARATION_STATES.includes(status)
            || !preparationRequestKeyMatches(requestKey, campaign, requestNonce)
            || (jobId !== null && !fullJobId(jobId))
            || !Number.isInteger(inputVersion) || inputVersion < 1
            || !String(row.input_signature || '')
            || !Number.isFinite(Date.parse(createdAt))
            || !Number.isFinite(Date.parse(updatedAt))) return null;
        return {
            schema_version: 3,
            owner_token: String(row.owner_token),
            created_at: createdAt,
            updated_at: updatedAt,
            status,
            request_nonce: requestNonce,
            request_key: requestKey,
            job_id: jobId,
            campaign_scientific_ref: campaign,
            input_version: inputVersion,
            input_signature: String(row.input_signature),
        };
    } catch { return null; }
}

export function legacyPreparationReceiptFrom(value: unknown): LegacyPreparationReceipt | null {
    try {
        const row = value as Record<string, unknown> | null;
        const jobId = String(row?.job_id || ''), campaignId = String(row?.campaign_id || '');
        const inputVersion = Number(row?.input_version);
        const generation = Number(row?.input_scientific_generation);
        const digest = String(row?.input_scientific_digest || '');
        const signature = String(row?.input_signature || '');
        const submittedAt = String(row?.submitted_at || '');
        return row?.schema_version === 2 && fullJobId(jobId) && !!campaignId
            && Number.isInteger(inputVersion) && inputVersion > 0
            && Number.isInteger(generation) && generation > 0
            && /^sha256:[0-9a-f]{64}$/.test(digest) && !!signature
            && Number.isFinite(Date.parse(submittedAt))
            ? {
                schema_version: 2,
                job_id: jobId,
                campaign_id: campaignId,
                submitted_at: submittedAt,
                input_version: inputVersion,
                input_scientific_generation: generation,
                input_scientific_digest: digest,
                input_signature: signature,
            } : null;
    } catch { return null; }
}

export function anyPreparationReceiptFrom(value: unknown): AnyPreparationReceipt | null {
    return preparationReceiptFrom(value) || legacyPreparationReceiptFrom(value);
}

export function preparationReceiptBinding(
    receipt: AnyPreparationReceipt,
): PreparationReceiptBinding {
    return receipt.schema_version === 3 ? {
        campaignScientificRef: receipt.campaign_scientific_ref,
        inputVersion: receipt.input_version,
        inputSignature: receipt.input_signature,
        submittedAt: receipt.created_at,
        jobId: receipt.job_id,
        requestKey: receipt.request_key,
    } : {
        campaignScientificRef: {
            kind: 'rbfe_campaign',
            id: receipt.campaign_id,
            version: receipt.input_scientific_generation,
            sha256: receipt.input_scientific_digest,
        },
        inputVersion: receipt.input_version,
        inputSignature: receipt.input_signature,
        submittedAt: receipt.submitted_at,
        jobId: receipt.job_id,
        requestKey: null,
    };
}

export function acknowledgedPreparationReceipt(
    receipt: AnyPreparationReceipt,
): receipt is AcknowledgedPreparationReceipt {
    return fullJobId(String(receipt.job_id || ''));
}

export function preparationReceiptForSubmission(
    ownerToken: string,
    campaignScientificRef: CampaignScientificRef,
    inputVersion: number,
    inputSignature: string,
    requestNonce: string,
    now = new Date().toISOString(),
): PreparationReceipt | null {
    return preparationReceiptFrom({
        schema_version: 3,
        owner_token: ownerToken,
        created_at: now,
        updated_at: now,
        status: 'submitting',
        request_nonce: requestNonce,
        request_key: preparationRequestKey(campaignScientificRef, requestNonce),
        job_id: null,
        campaign_scientific_ref: campaignScientificRef,
        input_version: inputVersion,
        input_signature: inputSignature,
    });
}

/** A prepare acknowledgement is adopted only when every advertised handle agrees. */
export function preparationAcknowledgementJobId(
    dataValue: unknown,
    metaValue: unknown,
    expectedRequestKey: string,
): string | null {
    const data = dataValue && typeof dataValue === 'object' && !Array.isArray(dataValue)
        ? dataValue as Record<string, any> : null;
    const meta = metaValue && typeof metaValue === 'object' && !Array.isArray(metaValue)
        ? metaValue as Record<string, any> : null;
    if (!data || String(data.request_key || '') !== expectedRequestKey) return null;
    const job = data.job && typeof data.job === 'object' && !Array.isArray(data.job)
        ? data.job as Record<string, any> : null;
    const candidates = [meta?.job_id, job?.id, job?.ref?.id]
        .map(value => String(value || '')).filter(Boolean);
    if (!candidates.length || candidates.some(value => !fullJobId(value))) return null;
    return new Set(candidates).size === 1 ? candidates[0] : null;
}

export function plannerReceiptFrom(value: unknown): PlannerReceipt | null {
    try {
        const row = value as Record<string, any> | null;
        if (row?.schema_version !== 2
            || row.campaign_scientific_ref?.kind !== 'rbfe_campaign') return null;
        const campaign = campaignScientificRefFrom(
            String(row.campaign_scientific_ref?.id || ''),
            Number(row.campaign_scientific_ref?.version),
            String(row.campaign_scientific_ref?.sha256 || ''),
        );
        const system = contentRef(row.prepared_system_ref, 'prepared_receptor_state');
        const jobId = row.job_id == null ? null : String(row.job_id);
        if (!campaign || !system || !String(row.owner_token || '')
            || !String(row.input_signature || '')
            || (jobId !== null && !fullJobId(jobId))
            || !['submitting', 'waiting', 'detached', 'cancel_requested']
                .includes(String(row.status))) return null;
        return {
            schema_version: 2,
            owner_token: String(row.owner_token),
            created_at: String(row.created_at || ''),
            status: row.status,
            job_id: jobId,
            campaign_scientific_ref: campaign,
            prepared_system_ref: system,
            input_signature: String(row.input_signature),
        };
    } catch { return null; }
}

export function plannerOutputReceiptFrom(value: unknown): PlannerOutputReceipt | null {
    try {
        const row = value as Record<string, any> | null;
        const campaign = row?.campaign_scientific_ref as Record<string, unknown> | undefined;
        const campaignRef = campaignScientificRefFrom(
            String(campaign?.id || ''), Number(campaign?.version),
            String(campaign?.sha256 || ''),
        );
        const system = contentRef(row?.prepared_system_ref, 'prepared_receptor_state');
        const network = strictArtifactRef(row?.plan_network_ref);
        const jobId = String(row?.network_job_id || '');
        return row?.schema_version === 1 && campaign?.kind === 'rbfe_campaign'
            && campaignRef && system && network && fullJobId(jobId)
            ? {
                schema_version: 1,
                network_job_id: jobId,
                created_at: String(row.created_at || ''),
                campaign_scientific_ref: campaignRef,
                prepared_system_ref: system,
                plan_network_ref: network,
            } : null;
    } catch { return null; }
}

export function runReceiptFrom(value: unknown): RunReceipt | null {
    try {
        const row = value as Record<string, any> | null;
        const schema = Number(row?.schema_version);
        if (![2, 3].includes(schema)
            || row?.campaign_scientific_ref?.kind !== 'rbfe_campaign') return null;
        const campaign = campaignScientificRefFrom(
            String(row.campaign_scientific_ref?.id || ''),
            Number(row.campaign_scientific_ref?.version),
            String(row.campaign_scientific_ref?.sha256 || ''),
        );
        const edgeSpec = strictArtifactRef(row.edge_spec_ref);
        const edgeNetwork = strictArtifactRef(row.edge_network_ref);
        const complex = strictArtifactRef(row.complex_transformation_ref);
        const solvent = strictArtifactRef(row.solvent_transformation_ref);
        const runId = row.run_id == null ? null : String(row.run_id);
        const state = String(row.state) as RunReceiptState;
        if (!campaign || !edgeSpec || !edgeNetwork || !complex || !solvent
            || !String(row.owner_token || '') || !String(row.request_key || '')
            || !String(row.edge_id || '')
            || !/^sha256:[0-9a-f]{64}$/.test(String(row.spec_digest || ''))
            || (runId !== null && !fullJobId(runId))
            || !RUN_STATES.includes(state)) return null;
        const base: RunReceipt = {
            schema_version: schema as 2 | 3,
            owner_token: String(row.owner_token),
            created_at: String(row.created_at || ''),
            updated_at: String(row.updated_at || ''),
            run_id: runId,
            request_key: String(row.request_key),
            state,
            campaign_scientific_ref: campaign,
            edge_id: String(row.edge_id),
            spec_digest: String(row.spec_digest),
            edge_spec_ref: edgeSpec,
            edge_network_ref: edgeNetwork,
            complex_transformation_ref: complex,
            solvent_transformation_ref: solvent,
        };
        if (schema === 2) return base;
        const planNetwork = strictArtifactRef(row.plan_network_ref);
        const system = contentRef(row.prepared_system_ref, 'prepared_receptor_state');
        const parent = contentRef(row.parent_pose_ref, 'pose_hypothesis');
        const proposal = contentRef(row.proposal_pose_ref, 'pose_hypothesis');
        const planJob = String(row.plan_network_job_id || '');
        return planNetwork && system && parent && proposal && fullJobId(planJob)
            ? {
                ...base,
                schema_version: 3,
                plan_network_job_id: planJob,
                plan_network_ref: planNetwork,
                prepared_system_ref: system,
                parent_pose_ref: parent,
                proposal_pose_ref: proposal,
            } : null;
    } catch { return null; }
}

export function runReceiptState(value: unknown): RunReceiptState | null {
    const state = String(value) as RunReceiptState;
    return RUN_STATES.includes(state) && state !== 'creating'
        && state !== 'cancel_requested' ? state : null;
}

export function runBinding(receipt: RunReceipt): ExactRunBinding {
    return {
        requestKey: receipt.request_key,
        campaign: {
            id: receipt.campaign_scientific_ref.id,
            version: receipt.campaign_scientific_ref.version,
            sha256: receipt.campaign_scientific_ref.sha256,
        },
        edgeId: receipt.edge_id,
        specDigest: receipt.spec_digest,
        edgeSpecRef: receipt.edge_spec_ref,
        edgeNetworkRef: receipt.edge_network_ref,
        complexTransformationRef: receipt.complex_transformation_ref,
        solventTransformationRef: receipt.solvent_transformation_ref,
    };
}

export function runReceiptMatchesData(
    data: Record<string, unknown>,
    receipt: RunReceipt,
): boolean {
    return exactRunBindingMatches(data, runBinding(receipt))
        && (!receipt.run_id
            || String((data.ref as Record<string, unknown> | undefined)?.id || '') === receipt.run_id);
}

export function operationBindingFromReceipt(receipt: RunReceipt): ExactOperationBinding | null {
    return receipt.schema_version === 3 && receipt.plan_network_job_id
        && receipt.plan_network_ref && receipt.prepared_system_ref
        && receipt.parent_pose_ref && receipt.proposal_pose_ref
        ? {
            ...runBinding(receipt),
            planNetworkJobId: receipt.plan_network_job_id,
            planNetworkRef: receipt.plan_network_ref,
            preparedSystemRef: receipt.prepared_system_ref,
            parentPoseRef: receipt.parent_pose_ref,
            proposalPoseRef: receipt.proposal_pose_ref,
        } : null;
}

/** Convert a complete server RunSet response into a legacy/history receipt. */
export function runReceiptFromData(
    data: Record<string, any>,
    ownerToken: string,
    now = new Date().toISOString(),
): RunReceipt | null {
    const rawCampaign = data.campaign_scientific_ref as Record<string, unknown> | undefined;
    const campaign = rawCampaign?.kind === 'rbfe_campaign'
        ? campaignScientificRefFrom(
            String(rawCampaign.id || ''), Number(rawCampaign.version),
            String(rawCampaign.sha256 || ''),
        ) : null;
    const state = runReceiptState(data.state), runId = String(data.ref?.id || '');
    const edgeSpec = strictArtifactRef(data.edge_spec_ref);
    const edgeNetwork = strictArtifactRef(data.edge_network_ref);
    const complex = strictArtifactRef(data.complex_transformation_ref);
    const solvent = strictArtifactRef(data.solvent_transformation_ref);
    // RunSet responses expose exact provenance through the content-addressed
    // edge_spec_ref. `specification_digest` belongs to the RunSet definition
    // and must never be substituted for the edge-spec artifact digest.
    const specDigest = edgeSpec?.sha256 || '';
    const requestKey = String(data.request_key || ''), edgeId = String(data.edge_id || '');
    if (data.ref?.kind !== 'run' || !campaign || !state || !fullJobId(runId)
        || !edgeSpec || !edgeNetwork || !complex || !solvent || !requestKey
        || !edgeId || !/^sha256:[0-9a-f]{64}$/.test(specDigest)) return null;
    return {
        schema_version: 2,
        owner_token: ownerToken,
        created_at: now,
        updated_at: now,
        run_id: runId,
        request_key: requestKey,
        state,
        campaign_scientific_ref: campaign,
        edge_id: edgeId,
        spec_digest: specDigest,
        edge_spec_ref: edgeSpec,
        edge_network_ref: edgeNetwork,
        complex_transformation_ref: complex,
        solvent_transformation_ref: solvent,
    };
}
