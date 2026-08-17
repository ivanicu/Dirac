import type { DiracClient, Envelope } from '../../app/services/dirac-client';
import { campaignsVisibleToCopy } from './workbench-state';
import type { ReceiptStorage } from './workbench-receipt-store';
import type {
    CampaignCacheRecord,
    CampaignCacheState,
    CampaignDraftV2,
    CampaignSaveResult,
    CampaignStateAdapter,
} from './workbench-types';

export const CAMPAIGN_CACHE_KEYS = Object.freeze({
    draft: 'dirac.rbfe.campaign_draft.v2',
    conflict: 'dirac.rbfe.campaign_conflict.v2',
    archive: 'dirac.rbfe.campaign_draft_archive.v2',
});

type CampaignStateRuntime = Readonly<{
    client: DiracClient;
    storage: ReceiptStorage;
    copyId: string;
    currentEpoch(): number;
    currentEditRevision(): number;
    requestStillCurrent(draft: CampaignDraftV2, epoch: number): boolean;
    envelopeFailure(envelope: Envelope): string;
}>;

export type CampaignState = Readonly<{
    adapter: CampaignStateAdapter;
    readCache(): CampaignCacheRecord | null;
    writeCache(draft: CampaignDraftV2, state: CampaignCacheState, error?: string): void;
    archiveCache(record: CampaignCacheRecord | null, reason: string): void;
}>;

export function draftFromCampaignEnvelope(data: Record<string, any>): CampaignDraftV2 {
    const document = data.state || {};
    const candidate = document.client_state || document.inputs?.state || document.state || document;
    if (candidate?.schema_version !== 2) {
        throw new Error('Server campaign does not contain a compatible interactive draft');
    }
    return {
        ...candidate,
        scientific_inputs: document.inputs || candidate.scientific_inputs,
        campaign_id: String(data.campaign_id || data.campaign_ref?.id || candidate.campaign_id),
        expected_version: Number(data.version),
        state_digest: String(data.state_digest || candidate.state_digest || ''),
        campaign_scientific_generation: Number(data.campaign_scientific_generation
            ?? candidate.campaign_scientific_generation),
        campaign_scientific_digest: String(data.campaign_scientific_digest
            || candidate.campaign_scientific_digest || ''),
        origin: 'server-campaign',
    } as CampaignDraftV2;
}

function conflict(envelope: Envelope): { actualVersion: number | null; message: string } | null {
    const details = envelope.error?.details || {};
    const stageError = (details.error || {}) as Record<string, unknown>;
    if (stageError.code !== 'CAMPAIGN_STATE_CONFLICT') return null;
    const value = Number(stageError.actual_version);
    return {
        actualVersion: Number.isInteger(value) ? value : null,
        message: String(stageError.message || envelope.error?.user_message
            || envelope.error?.message || 'campaign version conflict'),
    };
}

function offlineEligible(envelope: Envelope): boolean {
    return envelope.error?.code === 'DB_UNAVAILABLE';
}

export function createCampaignState(runtime: CampaignStateRuntime): CampaignState {
    const readCache = (): CampaignCacheRecord | null => {
        try {
            const parsed = JSON.parse(runtime.storage.get(CAMPAIGN_CACHE_KEYS.draft) || 'null') as CampaignCacheRecord | CampaignDraftV2 | null;
            if (!parsed) return null;
            if ('draft' in parsed) return parsed;
            return { draft: parsed, cache_state: 'offline-only', cached_at: parsed.saved_at };
        } catch { return null; }
    };
    const writeCache = (
        draft: CampaignDraftV2,
        state: CampaignCacheState,
        error?: string,
    ): void => {
        const record: CampaignCacheRecord = {
            draft,
            cache_state: state,
            cached_at: new Date().toISOString(),
            ...(error ? { server_error: error } : {}),
        };
        runtime.storage.set(CAMPAIGN_CACHE_KEYS.draft, JSON.stringify(record));
        if (state === 'version-conflict') {
            runtime.storage.set(CAMPAIGN_CACHE_KEYS.conflict, JSON.stringify(record));
        }
    };
    const archiveCache = (record: CampaignCacheRecord | null, reason: string): void => {
        if (!record) return;
        let rows: Array<CampaignCacheRecord & { archive_reason: string }> = [];
        try {
            const parsed = JSON.parse(runtime.storage.get(CAMPAIGN_CACHE_KEYS.archive) || '[]');
            if (Array.isArray(parsed)) rows = parsed.filter(item => item?.draft?.schema_version === 2);
        } catch { rows = []; }
        rows = [
            { ...record, archive_reason: reason },
            ...rows.filter(item => item.draft.campaign_id !== record.draft.campaign_id
                || item.cached_at !== record.cached_at),
        ].slice(0, 20);
        runtime.storage.set(CAMPAIGN_CACHE_KEYS.archive, JSON.stringify(rows));
    };
    const offlineSave = (
        draft: CampaignDraftV2,
        reason: string,
        persist = true,
    ): CampaignSaveResult => {
        if (persist) writeCache(draft, 'offline-only', reason);
        return {
            receipt: `offline-cache:${draft.campaign_id}:${draft.saved_at}`,
            durability: 'offline-cache',
            version: draft.expected_version,
            scientificGeneration: draft.campaign_scientific_generation,
            scientificDigest: draft.campaign_scientific_digest,
            warning: `NOT SERVER-DURABLE · ${reason}`,
        };
    };

    const adapter: CampaignStateAdapter = {
        mode: 'server-first',
        async list() {
            const cached = readCache();
            try {
                const response = await runtime.client.execute('physics.rbfe-campaign.list', {});
                if (!response.ok) {
                    if (offlineEligible(response) && cached) return {
                        campaigns: [{ campaign_id: cached.draft.campaign_id,
                            version: cached.draft.expected_version,
                            state: { inputs: { state: cached.draft } } }],
                        source: 'offline-cache',
                        warning: `NOT SERVER-DURABLE · ${runtime.envelopeFailure(response)}`,
                    };
                    throw new Error(runtime.envelopeFailure(response));
                }
                const campaigns = (response.data?.campaigns || []) as Array<Record<string, any>>;
                return {
                    campaigns: campaignsVisibleToCopy(
                        campaigns, runtime.copyId, cached?.draft.campaign_id || null,
                        row => String(row.campaign_id || row.campaign_ref?.id || ''),
                    ),
                    source: 'server',
                };
            } catch (error) {
                if (!(error instanceof TypeError) || !cached) throw error;
                const reason = error instanceof Error ? error.message : String(error);
                return {
                    campaigns: [{ campaign_id: cached.draft.campaign_id,
                        version: cached.draft.expected_version,
                        state: { inputs: { state: cached.draft } } }],
                    source: 'offline-cache',
                    warning: `NOT SERVER-DURABLE · ${reason}`,
                };
            }
        },
        async load() {
            const requestEpoch = runtime.currentEpoch();
            const requestEdit = runtime.currentEditRevision();
            const cached = readCache();
            let campaignId = cached?.draft.campaign_id || '';
            if (!campaignId) {
                const listed = await this.list(), latest = listed.campaigns[0];
                if (!latest) return null;
                if (listed.source === 'offline-cache') return {
                    draft: draftFromCampaignEnvelope(latest),
                    source: 'offline-cache',
                    version: Number(latest.version || 0),
                    warning: listed.warning,
                };
                campaignId = String(latest.campaign_id || latest.campaign_ref?.id || '');
            }
            if (!campaignId) return null;
            try {
                const response = await runtime.client.execute(
                    'physics.rbfe-campaign.get', { campaign_id: campaignId },
                );
                if (!response.ok) {
                    if ((offlineEligible(response) || response.error?.code === 'NOT_FOUND') && cached) {
                        return {
                            draft: cached.draft,
                            source: 'offline-cache',
                            version: cached.draft.expected_version,
                            warning: `NOT SERVER-DURABLE · server load failed: ${runtime.envelopeFailure(response)}`,
                        };
                    }
                    throw new Error(runtime.envelopeFailure(response));
                }
                const draft = draftFromCampaignEnvelope(response.data || {});
                if (requestEpoch === runtime.currentEpoch()
                    && requestEdit === runtime.currentEditRevision()) writeCache(draft, 'server-synced');
                return {
                    draft, source: 'server', version: draft.expected_version,
                    stateDigest: draft.state_digest,
                    scientificGeneration: draft.campaign_scientific_generation,
                    scientificDigest: draft.campaign_scientific_digest,
                };
            } catch (error) {
                if (!(error instanceof TypeError) || !cached) throw error;
                const reason = error instanceof Error ? error.message : String(error);
                return {
                    draft: cached.draft, source: 'offline-cache',
                    version: cached.draft.expected_version,
                    warning: `NOT SERVER-DURABLE · server unreachable: ${reason}`,
                };
            }
        },
        async save(draft) {
            const requestEpoch = runtime.currentEpoch();
            const isCurrent = () => runtime.requestStillCurrent(draft, requestEpoch);
            const payload = {
                campaign_id: draft.campaign_id,
                expected_version: draft.expected_version,
                status: draft.server_status || 'draft',
                state: draft,
                changed_domains: ['project_context'],
                reason: 'interactive_campaign_draft_saved',
            };
            try {
                const response = await runtime.client.execute('physics.rbfe-campaign.save', payload);
                if (!response.ok) {
                    const collision = conflict(response);
                    if (collision) {
                        if (isCurrent()) writeCache(draft, 'version-conflict', collision.message);
                        throw new Error(`VERSION CONFLICT · local v${draft.expected_version}, server v${collision.actualVersion ?? 'unknown'} · local edits retained in conflict cache; resume server state, then reapply`);
                    }
                    if (offlineEligible(response)) return offlineSave(
                        draft, `server save unavailable: ${runtime.envelopeFailure(response)}`, isCurrent(),
                    );
                    throw new Error(runtime.envelopeFailure(response));
                }
                const version = Number(response.data?.version);
                if (!Number.isInteger(version) || version < 1) {
                    throw new Error('Server save returned no campaign version');
                }
                const stateDigest = String(response.data?.state_digest || '');
                if (!/^sha256:[0-9a-f]{64}$/.test(stateDigest)) {
                    throw new Error('Server save returned no immutable campaign state digest');
                }
                const scientificGeneration = Number(response.data?.campaign_scientific_generation);
                const scientificDigest = String(response.data?.campaign_scientific_digest || '');
                if (!Number.isInteger(scientificGeneration) || scientificGeneration < 1
                    || !/^sha256:[0-9a-f]{64}$/.test(scientificDigest)) {
                    throw new Error('Server save returned no immutable campaign scientific generation');
                }
                const synced = {
                    ...draft, expected_version: version, state_digest: stateDigest,
                    campaign_scientific_generation: scientificGeneration,
                    campaign_scientific_digest: scientificDigest,
                    origin: 'server-campaign' as const,
                };
                if (isCurrent()) {
                    writeCache(synced, 'server-synced');
                    runtime.storage.remove(CAMPAIGN_CACHE_KEYS.conflict);
                }
                return {
                    receipt: `server:${synced.campaign_id}:v${version}:${stateDigest}`,
                    durability: 'server', version, stateDigest,
                    scientificGeneration, scientificDigest,
                    status: String(response.data?.status || draft.server_status || 'draft') as CampaignDraftV2['server_status'],
                };
            } catch (error) {
                if (error instanceof TypeError) return offlineSave(
                    draft, `server unreachable: ${error.message}`, isCurrent(),
                );
                throw error;
            }
        },
        async invalidate(campaignId, expectedVersion, changedDomains, reason) {
            const response = await runtime.client.execute('physics.rbfe-campaign.invalidate', {
                campaign_id: campaignId, expected_version: expectedVersion,
                changed_domains: changedDomains, reason,
            });
            if (!response.ok) {
                const collision = conflict(response);
                if (collision) throw new Error(`VERSION CONFLICT · local v${expectedVersion}, server v${collision.actualVersion ?? 'unknown'} · reload before invalidating`);
                throw new Error(runtime.envelopeFailure(response));
            }
            return {
                version: Number(response.data?.version),
                status: String(response.data?.status || 'stale'),
                stateDigest: String(response.data?.state_digest || '') || undefined,
                scientificGeneration: Number(response.data?.campaign_scientific_generation) || undefined,
                scientificDigest: String(response.data?.campaign_scientific_digest || '') || undefined,
            };
        },
        async importSystem(campaignId, expectedVersion, preparedSystemRef, reason) {
            if (!/^sha256:[0-9a-f]{64}$/.test(preparedSystemRef.sha256 || '')) {
                throw new Error('Prepared-system import requires the complete content-addressed receptor reference');
            }
            const response = await runtime.client.execute('physics.rbfe-campaign.import-system', {
                campaign_id: campaignId, expected_version: expectedVersion,
                prepared_receptor_state_ref: preparedSystemRef, reason,
            });
            if (!response.ok) {
                const collision = conflict(response);
                if (collision) throw new Error(`VERSION CONFLICT · local v${expectedVersion}, server v${collision.actualVersion ?? 'unknown'} · reload before importing`);
                throw new Error(runtime.envelopeFailure(response));
            }
            const version = Number(response.data?.version);
            const stateDigest = String(response.data?.state_digest || '');
            const scientificGeneration = Number(response.data?.campaign_scientific_generation);
            const scientificDigest = String(response.data?.campaign_scientific_digest || '');
            if (!Number.isInteger(version) || version < 1
                || !/^sha256:[0-9a-f]{64}$/.test(stateDigest)
                || !Number.isInteger(scientificGeneration) || scientificGeneration < 1
                || !/^sha256:[0-9a-f]{64}$/.test(scientificDigest)) {
                throw new Error('System import returned no complete audit and scientific generation receipt');
            }
            return {
                version,
                receipt: String(response.data?.receipt_digest
                    || response.data?.import_receipt?.receipt_digest || 'server import receipt'),
                stateDigest,
                scientificGeneration,
                scientificDigest,
            };
        },
        async clear() {
            runtime.storage.remove(CAMPAIGN_CACHE_KEYS.draft);
            runtime.storage.remove(CAMPAIGN_CACHE_KEYS.conflict);
        },
    };
    return { adapter, readCache, writeCache, archiveCache };
}
