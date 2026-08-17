import type { Envelope } from '../../app/services/dirac-client';
import {
    acknowledgedPreparationReceipt,
    campaignScientificRefFrom,
    contentRef,
    preparationAcknowledgementJobId,
    preparationReceiptBinding,
    preparationReceiptForSubmission,
    type AcknowledgedPreparationReceipt,
    type AnyPreparationReceipt,
    type PreparationReceipt,
} from './workbench-receipts';
import { WorkbenchReceiptStore } from './workbench-receipt-store';
import type { PreparedSystemOption, PoseOption } from './workbench-types';

export type PreparationIdentitySnapshot = Readonly<{
    campaignId: string;
    auditVersion: number;
    scientificGeneration: number;
    scientificDigest: string;
}>;

export type PreparationClient = Readonly<{
    execute(command: string, input: Record<string, unknown>): Promise<Envelope>;
}>;

export function preparationSubmissionLockName(copyId: string, campaignId: string): string {
    return `dirac-rbfe-prepare:${copyId}:${campaignId}`;
}

export type ExactPreparationResult = Readonly<{
    campaignId: string;
    jobId: string;
    auditVersion: number;
    auditDigest: string;
    scientificGeneration: number;
    scientificDigest: string;
}>;

export function createStoredPreparationReceipt(
    store: WorkbenchReceiptStore,
    snapshot: PreparationIdentitySnapshot,
    inputSignature: string,
    ownerToken: string,
    requestNonce: string,
    now = new Date().toISOString(),
): PreparationReceipt {
    const campaignRef = campaignScientificRefFrom(
        snapshot.campaignId, snapshot.scientificGeneration, snapshot.scientificDigest,
    );
    const receipt = campaignRef ? preparationReceiptForSubmission(
        ownerToken, campaignRef, snapshot.auditVersion, inputSignature, requestNonce, now,
    ) : null;
    if (!receipt) {
        throw new Error('server-durable campaign has no exact scientific generation for preparation');
    }
    store.writePreparation(receipt);
    return receipt;
}

export function preparationElapsedSeconds(
    receipt: AnyPreparationReceipt,
    now = Date.now(),
): number {
    return Math.max(0, Math.floor(
        (now - Date.parse(preparationReceiptBinding(receipt).submittedAt)) / 1000,
    ));
}

export function preparationReceiptMatchesOpenCampaign(
    receipt: AnyPreparationReceipt,
    open: Readonly<{
        campaignId: string;
        auditVersion: number;
        scientificGeneration: number;
        scientificDigest: string;
        inputSignature: string;
    }>,
): boolean {
    const binding = preparationReceiptBinding(receipt);
    if (binding.campaignScientificRef.id !== open.campaignId
        || ![binding.inputVersion, binding.inputVersion + 1].includes(open.auditVersion)
        || binding.inputSignature !== open.inputSignature) return false;
    // At inputVersion the immutable scientific ref must still be the submitted
    // one. +1 is only a provisional reconnect state: result adoption separately
    // calls preparationResultMatchesOpenCampaign and requires the exact returned
    // audit digest plus scientific generation/digest before any CAS commit.
    return open.auditVersion !== binding.inputVersion
        || binding.campaignScientificRef.version === open.scientificGeneration
            && binding.campaignScientificRef.sha256 === open.scientificDigest;
}

/**
 * A completed job may be adopted while the open campaign is still at its
 * input revision, or after the server has committed that exact result.  A
 * different mutation at the same +1 audit version must not be overwritten.
 */
export function preparationResultMatchesOpenCampaign(
    receipt: AnyPreparationReceipt,
    exact: ExactPreparationResult,
    open: Readonly<{
        campaignId: string;
        auditVersion: number;
        auditDigest: string;
        scientificGeneration: number;
        scientificDigest: string;
        inputSignature: string;
    }>,
): boolean {
    const binding = preparationReceiptBinding(receipt);
    if (open.campaignId !== binding.campaignScientificRef.id
        || open.inputSignature !== binding.inputSignature) return false;
    if (open.auditVersion === binding.inputVersion) {
        return open.scientificGeneration === binding.campaignScientificRef.version
            && open.scientificDigest === binding.campaignScientificRef.sha256;
    }
    return open.auditVersion === exact.auditVersion
        && open.auditDigest === exact.auditDigest
        && open.scientificGeneration === exact.scientificGeneration
        && open.scientificDigest === exact.scientificDigest;
}

export function exactPreparationResultFrom(
    data: Record<string, unknown>,
    receipt: AcknowledgedPreparationReceipt,
): ExactPreparationResult {
    const binding = preparationReceiptBinding(receipt);
    const campaign = data.campaign_ref as Record<string, unknown> | undefined;
    const scientific = data.campaign_scientific_ref as Record<string, unknown> | undefined;
    const campaignId = String(campaign?.id || '');
    const auditVersion = Number(data.campaign_version);
    const auditDigest = String(data.campaign_state_digest || '');
    const scientificGeneration = Number(data.campaign_scientific_generation);
    const scientificDigest = String(data.campaign_scientific_digest || '');
    if (campaign?.kind !== 'rbfe_campaign'
        || campaignId !== binding.campaignScientificRef.id
        || campaign?.version !== auditVersion || campaign?.sha256 !== auditDigest
        || auditVersion !== binding.inputVersion + 1
        || !/^sha256:[0-9a-f]{64}$/.test(auditDigest)) {
        throw new Error(`preparation job ${receipt.job_id} returned no exact audit revision for campaign ${binding.campaignScientificRef.id}`);
    }
    if (scientific?.kind !== 'rbfe_campaign'
        || scientific?.id !== campaignId || scientific?.version !== scientificGeneration
        || scientific?.sha256 !== scientificDigest
        || !Number.isInteger(scientificGeneration) || scientificGeneration < 1
        || !/^sha256:[0-9a-f]{64}$/.test(scientificDigest)) {
        throw new Error('campaign preparation returned no exact immutable scientific generation');
    }
    return {
        campaignId,
        jobId: receipt.job_id,
        auditVersion,
        auditDigest,
        scientificGeneration,
        scientificDigest,
    };
}

export function preparedSystemFromPreparationResult(
    data: Record<string, unknown>,
    exact: ExactPreparationResult,
): PreparedSystemOption {
    const receptor = contentRef(data.prepared_receptor_state_ref, 'prepared_receptor_state');
    const target = data.target_ref as Record<string, unknown> | undefined;
    const protein = data.protein_structure_ref as Record<string, unknown> | undefined;
    const rawPoses = Array.isArray(data.poses) ? data.poses : [];
    const poses: PoseOption[] = rawPoses.map(raw => {
        const row = raw as Record<string, unknown>;
        const poseRef = contentRef(row?.pose_ref, 'pose_hypothesis');
        if (!poseRef || !String(row?.label || '') || !String(row?.canonical_smiles || '')) {
            throw new Error('campaign preparation returned a malformed pose hypothesis');
        }
        return { ...row, pose_ref: poseRef, label: String(row.label),
            canonical_smiles: String(row.canonical_smiles) } as PoseOption;
    });
    if (!receptor || target?.kind !== 'target' || !String(target.id || '')
        || protein?.kind !== 'protein_structure' || !String(protein.id || '')
        || !String(data.target_name || '') || !String(data.label || '')
        || !String(data.preparation_state || '') || !String(data.claim_boundary || '')
        || poses.length < 2) {
        throw new Error('campaign preparation returned no complete prepared-system card');
    }
    return {
        ...data,
        campaign_version: exact.auditVersion,
        campaign_state_digest: exact.auditDigest,
        campaign_scientific_generation: exact.scientificGeneration,
        campaign_scientific_digest: exact.scientificDigest,
        prepared_receptor_state_ref: receptor,
        target_ref: { kind: 'target', id: String(target.id) },
        target_name: String(data.target_name),
        protein_structure_ref: { kind: 'protein_structure', id: String(protein.id) },
        pdb_id: typeof data.pdb_id === 'string' ? data.pdb_id : null,
        resolution_angstrom: typeof data.resolution_angstrom === 'number'
            ? data.resolution_angstrom : null,
        preparation_state: String(data.preparation_state),
        claim_boundary: String(data.claim_boundary),
        label: String(data.label),
        poses,
    } as PreparedSystemOption;
}

/**
 * Submit or replay one logical preparation attempt. The caller supplies the
 * operation guard so an obsolete response cannot CAS-commit its acknowledgement.
 */
export async function submitPreparationExactlyOnce(
    client: PreparationClient,
    store: WorkbenchReceiptStore,
    receipt: PreparationReceipt,
    scientificInputs: Record<string, unknown>,
    stillCurrent: () => boolean,
    now = () => new Date().toISOString(),
): Promise<{ accepted: Envelope; receipt: PreparationReceipt & { job_id: string } } | null> {
    const binding = preparationReceiptBinding(receipt);
    const accepted = await client.execute('physics.rbfe-campaign.prepare', {
        ...scientificInputs,
        request_key: receipt.request_key,
        campaign_id: binding.campaignScientificRef.id,
        expected_version: binding.inputVersion,
    });
    if (!stillCurrent()) return null;
    if (!accepted.ok) {
        throw new Error(accepted.error?.user_message || accepted.error?.message
            || 'campaign preparation submission refused');
    }
    const jobId = preparationAcknowledgementJobId(
        accepted.data, accepted.meta, receipt.request_key,
    );
    if (!jobId) {
        throw new Error('campaign preparation returned no matching request key and complete durable Job ID');
    }
    const acknowledged = store.updatePreparation(
        receipt, { job_id: jobId, status: 'waiting' }, now(),
    );
    if (!acknowledged || !acknowledgedPreparationReceipt(acknowledged)) {
        throw new Error('campaign preparation acknowledgement lost exact receipt ownership');
    }
    return { accepted, receipt: acknowledged as PreparationReceipt & { job_id: string } };
}
