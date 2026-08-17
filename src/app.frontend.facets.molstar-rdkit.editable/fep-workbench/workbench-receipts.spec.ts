import { describe, expect, it } from '@jest/globals';
import {
    campaignScientificRefFrom,
    preparationAcknowledgementJobId,
    preparationReceiptFrom,
    preparationReceiptForSubmission,
    preparationRequestKey,
    plannerOutputReceiptFrom,
    plannerReceiptFrom,
    runReceiptFrom,
    runReceiptFromData,
} from './workbench-receipts';

const digest = `sha256:${'a'.repeat(64)}`;
const otherDigest = `sha256:${'b'.repeat(64)}`;
const jobId = '11111111-1111-4111-8111-111111111111';
const runId = '22222222-2222-4222-8222-222222222222';
const requestNonce = '33333333-3333-4333-8333-333333333333';
const artifact = (id: string, sha256 = digest) => ({ kind: 'artifact', id, sha256 });
const content = (kind: string, id: string, sha256 = digest) => ({ kind, id, sha256 });
const campaign = { kind: 'rbfe_campaign' as const, id: 'campaign-a', version: 7, sha256: digest };

describe('durable FEP receipt codecs',()=>{
    it('requires one positive scientific generation and full digest',()=>{
        expect(campaignScientificRefFrom('campaign-a',7,digest)).toEqual(campaign);
        expect(campaignScientificRefFrom('campaign-a',0,digest)).toBeNull();
        expect(campaignScientificRefFrom('campaign-a',7,'a'.repeat(64))).toBeNull();
    });

    it('preserves a pre-ack planner receipt with null job id',()=>{
        expect(plannerReceiptFrom({
            schema_version: 2, owner_token: 'owner', created_at: 'now',
            status: 'submitting', job_id: null, campaign_scientific_ref: campaign,
            prepared_system_ref: content('prepared_receptor_state','system'),
            input_signature: 'sealed-input',
        })?.job_id).toBeNull();
    });

    it('derives one exact preparation retry key from the scientific generation',()=>{
        const key=preparationRequestKey(campaign,requestNonce);
        expect(key).toBe(`rbfe-prepare:v1:campaign-a:7:${digest}:${requestNonce}`);
        const now='2026-08-17T16:00:00.000Z';
        expect(preparationReceiptFrom({
            schema_version: 3,owner_token: 'owner',created_at: now,updated_at: now,
            status: 'submitting',request_nonce: requestNonce,request_key: key,job_id: null,
            campaign_scientific_ref: campaign,input_version: 9,input_signature: 'sealed-input',
        })).toMatchObject({ request_key: key,job_id: null,status: 'submitting' });
    });

    it('refuses preparation receipt key rebinding and partial acknowledgements',()=>{
        const now='2026-08-17T16:00:00.000Z',base={
            schema_version: 3,owner_token: 'owner',created_at: now,updated_at: now,
            status: 'waiting',request_nonce: requestNonce,request_key: preparationRequestKey(campaign,requestNonce),job_id: jobId,
            campaign_scientific_ref: campaign,input_version: 9,input_signature: 'sealed-input',
        };
        expect(preparationReceiptFrom(base)?.job_id).toBe(jobId);
        expect(preparationReceiptFrom({ ...base,request_key: 'rbfe-prepare:v1:other' })).toBeNull();
        expect(preparationReceiptFrom({ ...base,campaign_scientific_ref: { ...campaign,sha256: otherDigest } })).toBeNull();
        expect(preparationReceiptFrom({ ...base,request_nonce: runId })).toBeNull();
        expect(preparationReceiptFrom({ ...base,job_id: 'partial' })).toBeNull();
        expect(preparationReceiptFrom({ ...base,status: 'unknown' })).toBeNull();
    });

    it('creates a pre-submit receipt and accepts only one echoed Job identity',()=>{
        const receipt=preparationReceiptForSubmission(
            'owner',campaign,9,'sealed-input',requestNonce,'2026-08-17T16:00:00.000Z',
        );
        expect(receipt).toMatchObject({ status: 'submitting',job_id: null });
        const key=receipt!.request_key;
        expect(preparationAcknowledgementJobId(
            { request_key: key,job: { id: jobId,ref: { id: jobId } } },{ job_id: jobId },key,
        )).toBe(jobId);
        expect(preparationAcknowledgementJobId(
            { request_key: 'other',job: { id: jobId } },{ job_id: jobId },key,
        )).toBeNull();
        expect(preparationAcknowledgementJobId(
            { request_key: key,job: { id: jobId } },{ job_id: runId },key,
        )).toBeNull();
        expect(preparationAcknowledgementJobId(
            { request_key: key,job: { id: 'partial' } },{},key,
        )).toBeNull();
    });

    it('rejects planner receipts with partial job ids or inexact system refs',()=>{
        const base={ schema_version: 2,owner_token: 'owner',created_at: 'now',status: 'waiting',job_id: jobId,campaign_scientific_ref: campaign,prepared_system_ref: content('prepared_receptor_state','system'),input_signature: 'sealed-input' };
        expect(plannerReceiptFrom(base)).not.toBeNull();
        expect(plannerReceiptFrom({ ...base,job_id: 'short' })).toBeNull();
        expect(plannerReceiptFrom({ ...base,prepared_system_ref: { kind: 'prepared_receptor_state',id: 'system' } })).toBeNull();
    });

    it('requires the exact plan-network output receipt',()=>{
        const base={ schema_version: 1,network_job_id: jobId,created_at: 'now',campaign_scientific_ref: campaign,prepared_system_ref: content('prepared_receptor_state','system'),plan_network_ref: artifact('plan') };
        expect(plannerOutputReceiptFrom(base)).not.toBeNull();
        expect(plannerOutputReceiptFrom({ ...base,plan_network_ref: artifact('plan',otherDigest) })?.plan_network_ref.sha256).toBe(otherDigest);
        expect(plannerOutputReceiptFrom({ ...base,plan_network_ref: { kind: 'artifact',id: 'plan',sha256: 'partial' } })).toBeNull();
    });

    it('accepts legacy RunSet receipts only for history and exact run provenance',()=>{
        const legacy={ schema_version: 2,owner_token: 'owner',created_at: 'now',updated_at: 'now',run_id: runId,request_key: 'request',state: 'blocked',campaign_scientific_ref: campaign,edge_id: 'edge',spec_digest: digest,edge_spec_ref: artifact('edge-spec'),edge_network_ref: artifact('edge-network'),complex_transformation_ref: artifact('complex'),solvent_transformation_ref: artifact('solvent') };
        const parsed=runReceiptFrom(legacy);
        expect(parsed?.schema_version).toBe(2);
        expect(parsed?.plan_network_ref).toBeUndefined();
        expect(runReceiptFrom({ ...legacy,edge_spec_ref: artifact('edge-spec',otherDigest) })?.edge_spec_ref.sha256).toBe(otherDigest);
        expect(runReceiptFrom({ ...legacy,edge_spec_ref: { kind: 'artifact',id: 'edge-spec' } })).toBeNull();
    });

    it('derives historical edge-spec digest from its exact ref, never RunSet specification_digest',()=>{
        const data={ ref: { kind: 'run',id: runId },state: 'blocked',request_key: 'request',
            campaign_scientific_ref: campaign,edge_id: 'edge',specification_digest: otherDigest,
            edge_spec_ref: artifact('edge-spec'),edge_network_ref: artifact('edge-network'),
            complex_transformation_ref: artifact('complex'),solvent_transformation_ref: artifact('solvent') };
        expect(runReceiptFromData(data,'history-owner','2026-08-17T16:00:00.000Z')).toMatchObject({
            schema_version: 2,spec_digest: digest,edge_spec_ref: artifact('edge-spec'),
        });
        expect(runReceiptFromData({ ...data,specification_digest: undefined },'history-owner')).not.toBeNull();
    });

    it('requires plan, system and both endpoint poses for a startable v3 receipt',()=>{
        const exact={ schema_version: 3,owner_token: 'owner',created_at: 'now',updated_at: 'now',run_id: null,request_key: 'request',state: 'creating',campaign_scientific_ref: campaign,edge_id: 'edge',spec_digest: digest,edge_spec_ref: artifact('edge-spec'),edge_network_ref: artifact('edge-network'),complex_transformation_ref: artifact('complex'),solvent_transformation_ref: artifact('solvent'),plan_network_job_id: jobId,plan_network_ref: artifact('plan'),prepared_system_ref: content('prepared_receptor_state','system'),parent_pose_ref: content('pose_hypothesis','parent'),proposal_pose_ref: content('pose_hypothesis','proposal') };
        expect(runReceiptFrom(exact)).toMatchObject({ schema_version: 3,run_id: null });
        expect(runReceiptFrom({ ...exact,parent_pose_ref: undefined })).toBeNull();
        expect(runReceiptFrom({ ...exact,plan_network_job_id: 'partial' })).toBeNull();
        expect(runReceiptFrom({ ...exact,proposal_pose_ref: content('wrong_kind','proposal') })).toBeNull();
    });
});
