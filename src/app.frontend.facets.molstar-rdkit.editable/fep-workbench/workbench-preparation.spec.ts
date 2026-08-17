import { describe, expect, it } from '@jest/globals';
import type { Envelope } from '../../app/services/dirac-client';
import { RECEIPT_KEYS, WorkbenchReceiptStore, type ReceiptStorage } from './workbench-receipt-store';
import {
    createStoredPreparationReceipt,
    exactPreparationResultFrom,
    preparationReceiptMatchesOpenCampaign,
    preparationResultMatchesOpenCampaign,
    preparationSubmissionLockName,
    preparedSystemFromPreparationResult,
    submitPreparationExactlyOnce,
    type PreparationClient,
} from './workbench-preparation';

class MemoryStorage implements ReceiptStorage {
    readonly values = new Map<string, string>();
    get(key: string): string | null { return this.values.get(key) ?? null; }
    set(key: string, value: string): void { this.values.set(key, value); }
    remove(key: string): void { this.values.delete(key); }
}

const digest = `sha256:${'a'.repeat(64)}`;
const otherDigest = `sha256:${'b'.repeat(64)}`;
const nonce = '33333333-3333-4333-8333-333333333333';
const jobId = '11111111-1111-4111-8111-111111111111';
const snapshot = {
    campaignId: 'campaign-a', auditVersion: 4,
    scientificGeneration: 7, scientificDigest: digest,
};

describe('exactly-once preparation submission', () => {
    it('serializes one campaign within a copy while keeping audit copies independent',()=>{
        expect(preparationSubmissionLockName('main','campaign-a')).toBe(
            preparationSubmissionLockName('main','campaign-a'));
        expect(preparationSubmissionLockName('copy-a','campaign-a')).not.toBe(
            preparationSubmissionLockName('copy-b','campaign-a'));
        expect(preparationSubmissionLockName('main','campaign-a')).not.toBe(
            preparationSubmissionLockName('main','campaign-b'));
    });

    it('persists before transport and adopts only the echoed request/Job pair', async () => {
        const storage = new MemoryStorage(), store = new WorkbenchReceiptStore(storage);
        const receipt = createStoredPreparationReceipt(
            store, snapshot, 'sealed', 'owner', nonce, '2026-08-17T16:00:00.000Z',
        );
        const client: PreparationClient = { execute: async (_command, input) => {
            expect(JSON.parse(storage.get(RECEIPT_KEYS.preparation) || '{}')).toMatchObject({
                request_key: input.request_key, job_id: null, status: 'submitting',
            });
            return { ok: true, data: { request_key: input.request_key, job: { id: jobId } },
                meta: { job_id: jobId } };
        } };
        const result = await submitPreparationExactlyOnce(
            client, store, receipt, { parent_id: 'parent' }, () => true,
            () => '2026-08-17T16:00:01.000Z',
        );
        expect(result?.receipt).toMatchObject({ job_id: jobId, status: 'waiting' });
        expect(store.readPreparation()).toEqual(result?.receipt);
    });

    it('keeps the same pre-submit receipt after ACK loss and replays its key', async () => {
        const storage = new MemoryStorage(), store = new WorkbenchReceiptStore(storage);
        const receipt = createStoredPreparationReceipt(
            store, snapshot, 'sealed', 'owner', nonce, '2026-08-17T16:00:00.000Z',
        );
        const seen: string[] = [];
        const lost: PreparationClient = { execute: async (_command, input) => {
            seen.push(String(input.request_key)); throw new Error('connection reset after commit');
        } };
        await expect(submitPreparationExactlyOnce(
            lost, store, receipt, {}, () => true,
        )).rejects.toThrow('connection reset after commit');
        expect(store.readPreparation()).toEqual(receipt);
        const replay: PreparationClient = { execute: async (_command, input): Promise<Envelope> => {
            seen.push(String(input.request_key));
            return { ok: true, data: { request_key: input.request_key, job: { id: jobId } },
                meta: { job_id: jobId } };
        } };
        const recovered = await submitPreparationExactlyOnce(
            replay, store, receipt, {}, () => true,
        );
        expect(new Set(seen)).toEqual(new Set([receipt.request_key]));
        expect(recovered?.receipt.job_id).toBe(jobId);
    });

    it('does not commit an acknowledgement after the operation is superseded', async () => {
        const storage = new MemoryStorage(), store = new WorkbenchReceiptStore(storage);
        const receipt = createStoredPreparationReceipt(store, snapshot, 'sealed', 'owner', nonce);
        const client: PreparationClient = { execute: async (_command, input) => ({
            ok: true, data: { request_key: input.request_key, job: { id: jobId } },
            meta: { job_id: jobId },
        }) };
        expect(await submitPreparationExactlyOnce(client, store, receipt, {}, () => false)).toBeNull();
        expect(store.readPreparation()).toEqual(receipt);
    });

    it('requires the original scientific generation until a committed +1 result exists', () => {
        const storage = new MemoryStorage(), store = new WorkbenchReceiptStore(storage);
        const receipt = createStoredPreparationReceipt(store, snapshot, 'sealed', 'owner', nonce);
        const open = { campaignId: 'campaign-a', auditVersion: 4,
            scientificGeneration: 7, scientificDigest: digest, inputSignature: 'sealed' };
        expect(preparationReceiptMatchesOpenCampaign(receipt, open)).toBe(true);
        expect(preparationReceiptMatchesOpenCampaign(receipt, {
            ...open, scientificDigest: otherDigest,
        })).toBe(false);
        expect(preparationReceiptMatchesOpenCampaign(receipt, {
            ...open, auditVersion: 5, scientificGeneration: 8, scientificDigest: otherDigest,
        })).toBe(true);
    });

    it('accepts only the exact +1 audit revision and immutable scientific ref', () => {
        const storage = new MemoryStorage(), store = new WorkbenchReceiptStore(storage);
        const pending = createStoredPreparationReceipt(store, snapshot, 'sealed', 'owner', nonce);
        const receipt = { ...pending, job_id: jobId, status: 'waiting' as const };
        const data = {
            campaign_ref: { kind: 'rbfe_campaign', id: 'campaign-a', version: 5, sha256: otherDigest },
            campaign_version: 5, campaign_state_digest: otherDigest,
            campaign_scientific_ref: { kind: 'rbfe_campaign', id: 'campaign-a', version: 8, sha256: otherDigest },
            campaign_scientific_generation: 8, campaign_scientific_digest: otherDigest,
        };
        expect(exactPreparationResultFrom(data,receipt)).toMatchObject({
            auditVersion: 5,scientificGeneration: 8,jobId,
        });
        expect(()=>exactPreparationResultFrom({
            ...data,campaign_ref: { ...data.campaign_ref,id: 'campaign-b' },
        },receipt)).toThrow('no exact audit revision');
        expect(()=>exactPreparationResultFrom({
            ...data,campaign_ref: { ...data.campaign_ref,kind: 'artifact' },
        },receipt)).toThrow('no exact audit revision');
        expect(()=>exactPreparationResultFrom({
            ...data,campaign_scientific_ref: { ...data.campaign_scientific_ref,sha256: digest },
        },receipt)).toThrow('no exact immutable scientific generation');
        expect(()=>exactPreparationResultFrom({
            ...data,campaign_scientific_ref: { ...data.campaign_scientific_ref,kind: 'artifact' },
        },receipt)).toThrow('no exact immutable scientific generation');
        const exact=exactPreparationResultFrom(data,receipt);
        const system=preparedSystemFromPreparationResult({
            ...data,prepared_receptor_state_ref: { kind: 'prepared_receptor_state',id: 'system',sha256: digest },
            target_ref: { kind: 'target',id: 'target' },target_name: 'Target',
            protein_structure_ref: { kind: 'protein_structure',id: 'structure' },label: 'Prepared target',
            preparation_state: 'review_pending',claim_boundary: 'hypothesis only',poses: [
                { pose_ref: { kind: 'pose_hypothesis',id: 'p1',sha256: digest },label: 'Parent',canonical_smiles: 'CC' },
                { pose_ref: { kind: 'pose_hypothesis',id: 'p2',sha256: digest },label: 'Proposal',canonical_smiles: 'CN' },
            ],
        },exact);
        expect(system).toMatchObject({ campaign_version: 5,poses: [{ label: 'Parent' },{ label: 'Proposal' }] });
        expect(()=>preparedSystemFromPreparationResult({ ...system,poses: [] },exact)).toThrow('no complete prepared-system card');

        const inputOpen={ campaignId: 'campaign-a',auditVersion: 4,auditDigest: digest,
            scientificGeneration: 7,scientificDigest: digest,inputSignature: 'sealed' };
        expect(preparationResultMatchesOpenCampaign(receipt,exact,inputOpen)).toBe(true);
        expect(preparationResultMatchesOpenCampaign(receipt,exact,{
            ...inputOpen,auditVersion: 5,auditDigest: otherDigest,
            scientificGeneration: 8,scientificDigest: otherDigest,
        })).toBe(true);
        expect(preparationResultMatchesOpenCampaign(receipt,exact,{
            ...inputOpen,auditVersion: 5,auditDigest: digest,
            scientificGeneration: 8,scientificDigest: otherDigest,
        })).toBe(false);
    });
});
