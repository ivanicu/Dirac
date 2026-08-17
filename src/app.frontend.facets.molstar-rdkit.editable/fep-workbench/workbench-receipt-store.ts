import {
    fullJobId,
    anyPreparationReceiptFrom,
    plannerOutputReceiptFrom,
    plannerReceiptFrom,
    runReceiptFrom,
    type AnyPreparationReceipt,
    type PreparationReceipt,
    type PreparationReceiptStatus,
    type PlannerOutputReceipt,
    type PlannerReceipt,
    type RunReceipt,
} from './workbench-receipts';

export const RECEIPT_KEYS = Object.freeze({
    detachedPreparation: 'dirac.rbfe.detached_prepare_jobs.v1',
    detachedPlanner: 'dirac.rbfe.detached_planner_jobs.v2',
    detachedRun: 'dirac.rbfe.detached_runsets.v1',
    planner: 'dirac.rbfe.pending_planner_job.v2',
    plannerLegacyJobId: 'dirac.rbfe.pending_planner_job_id',
    plannerOutput: 'dirac.rbfe.planner_output_receipt.v1',
    preparation: 'dirac.rbfe.pending_prepare_job.v1',
    preparationLastJobId: 'dirac.rbfe.last_prepare_job_id',
    run: 'dirac.rbfe.active_run.v2',
    runLegacyId: 'dirac.rbfe.active_run_id',
});

export function globalPhysicalReceiptKey(campaignId: string): string {
    return `dirac.rbfe.global_physical_receipt.v1.${campaignId}`;
}

export type ReceiptStorage = Readonly<{
    get(key: string): string | null;
    set(key: string, value: string): void;
    remove(key: string): void;
}>;

function readJson(storage: ReceiptStorage, key: string): unknown {
    try { return JSON.parse(storage.get(key) || 'null'); } catch { return null; }
}

function arrayValue(value: unknown): Array<Record<string, unknown>> {
    return Array.isArray(value)
        ? value.filter(item => !!item && typeof item === 'object') as Array<Record<string, unknown>>
        : [];
}

export class WorkbenchReceiptStore {
    constructor(
        private readonly storage: ReceiptStorage,
        private readonly mainPhysicalStorage?: ReceiptStorage,
    ) {}

    readPreparation(): AnyPreparationReceipt | null {
        const value = readJson(this.storage, RECEIPT_KEYS.preparation);
        const record = !!value && typeof value === 'object' && !Array.isArray(value)
            ? value as Record<string, unknown> : null;
        if (record && Number(record.schema_version) === 1
            && fullJobId(String(record.job_id || ''))) {
            this.markPreparationTerminal(String(record.job_id));
            this.storage.remove(RECEIPT_KEYS.preparation);
            return null;
        }
        return anyPreparationReceiptFrom(value);
    }

    writePreparation(receipt: AnyPreparationReceipt): void {
        this.storage.set(RECEIPT_KEYS.preparation, JSON.stringify(receipt));
        if (receipt.job_id) this.markPreparationTerminal(receipt.job_id);
    }

    preparationOwned(ownerToken: string): PreparationReceipt | null {
        const receipt = this.readPreparation();
        return receipt?.schema_version === 3 && receipt.owner_token === ownerToken
            ? receipt : null;
    }

    claimPreparation(
        receipt: PreparationReceipt,
        ownerToken: string,
        now: string,
    ): PreparationReceipt | null {
        const current = this.readPreparation();
        if (current?.schema_version !== 3
            || current.request_key !== receipt.request_key
            || current.job_id !== receipt.job_id) return null;
        const next = { ...current, owner_token: ownerToken, updated_at: now };
        this.writePreparation(next);
        return next;
    }

    updatePreparation(
        receipt: PreparationReceipt,
        changes: Readonly<{ job_id?: string | null; status?: PreparationReceiptStatus }>,
        now: string,
    ): PreparationReceipt | null {
        const current = this.preparationOwned(receipt.owner_token);
        if (!current || current.request_key !== receipt.request_key
            || current.job_id !== receipt.job_id) return null;
        const next = anyPreparationReceiptFrom({ ...current, ...changes, updated_at: now });
        if (next?.schema_version !== 3) return null;
        this.writePreparation(next);
        return next;
    }

    markPreparationTerminal(jobId: string): void {
        if (fullJobId(jobId)) this.storage.set(RECEIPT_KEYS.preparationLastJobId, jobId);
    }

    preparationReceiptMatches(receipt: AnyPreparationReceipt): boolean {
        const current = this.readPreparation();
        if (!current || current.schema_version !== receipt.schema_version) return false;
        let matches = false;
        if (receipt.schema_version === 3 && current.schema_version === 3) {
            matches = current.owner_token === receipt.owner_token
                && current.request_key === receipt.request_key
                && current.job_id === receipt.job_id;
        } else if (receipt.schema_version === 2 && current.schema_version === 2) {
            matches = current.job_id === receipt.job_id;
        }
        return matches;
    }

    removePreparationReceipt(receipt: AnyPreparationReceipt): boolean {
        if (!this.preparationReceiptMatches(receipt)) return false;
        this.storage.remove(RECEIPT_KEYS.preparation);
        return true;
    }

    archivePreparation(receipt: AnyPreparationReceipt): boolean {
        if (!this.preparationReceiptMatches(receipt)) return false;
        const rows = arrayValue(readJson(this.storage, RECEIPT_KEYS.detachedPreparation))
            .map(anyPreparationReceiptFrom)
            .filter((row): row is AnyPreparationReceipt => row !== null);
        const identity = receipt.schema_version === 3 ? receipt.request_key : receipt.job_id;
        this.storage.set(RECEIPT_KEYS.detachedPreparation, JSON.stringify([
            receipt, ...rows.filter(item =>
                (item.schema_version === 3 ? item.request_key : item.job_id) !== identity),
        ].slice(0, 20)));
        if (receipt.job_id) this.markPreparationTerminal(receipt.job_id);
        return this.removePreparationReceipt(receipt);
    }

    readPlanner(): PlannerReceipt | null {
        return plannerReceiptFrom(readJson(this.storage, RECEIPT_KEYS.planner));
    }

    writePlanner(receipt: PlannerReceipt): void {
        this.storage.set(RECEIPT_KEYS.planner, JSON.stringify(receipt));
        if (receipt.job_id) {
            this.storage.set(RECEIPT_KEYS.plannerLegacyJobId, receipt.job_id);
        }
    }

    plannerOwned(ownerToken: string): PlannerReceipt | null {
        const receipt = this.readPlanner();
        return receipt?.owner_token === ownerToken ? receipt : null;
    }

    removePlanner(ownerToken: string, jobId?: string | null): boolean {
        const receipt = this.readPlanner();
        if (!receipt || receipt.owner_token !== ownerToken
            || (jobId !== undefined && receipt.job_id !== jobId)) return false;
        this.storage.remove(RECEIPT_KEYS.planner);
        if (!jobId || this.storage.get(RECEIPT_KEYS.plannerLegacyJobId) === jobId) {
            this.storage.remove(RECEIPT_KEYS.plannerLegacyJobId);
        }
        return true;
    }

    archivePlanner(receipt: PlannerReceipt, reason: string, now: string): void {
        const rows = arrayValue(readJson(this.storage, RECEIPT_KEYS.detachedPlanner));
        const archived = { ...receipt, status: 'detached', detached_at: now, reason };
        this.storage.set(RECEIPT_KEYS.detachedPlanner, JSON.stringify([
            archived,
            ...rows.filter(item => item.owner_token !== receipt.owner_token),
        ].slice(0, 20)));
        this.removePlanner(receipt.owner_token, receipt.job_id);
    }

    readPlannerOutput(): PlannerOutputReceipt | null {
        return plannerOutputReceiptFrom(readJson(this.storage, RECEIPT_KEYS.plannerOutput));
    }

    writePlannerOutput(receipt: PlannerOutputReceipt): void {
        this.storage.set(RECEIPT_KEYS.plannerOutput, JSON.stringify(receipt));
    }

    readRun(): RunReceipt | null {
        return runReceiptFrom(readJson(this.storage, RECEIPT_KEYS.run));
    }

    writeRun(receipt: RunReceipt): void {
        const serialized = JSON.stringify(receipt);
        this.storage.set(RECEIPT_KEYS.run, serialized);
        this.mainPhysicalStorage?.set(globalPhysicalReceiptKey(receipt.campaign_scientific_ref.id), serialized);
        if (receipt.run_id) this.storage.set(RECEIPT_KEYS.runLegacyId, receipt.run_id);
    }

    runOwned(ownerToken: string): RunReceipt | null {
        const receipt = this.readRun();
        return receipt?.owner_token === ownerToken ? receipt : null;
    }

    removeRun(ownerToken: string, runId?: string | null): boolean {
        const receipt = this.readRun();
        if (!receipt || receipt.owner_token !== ownerToken
            || (runId !== undefined && receipt.run_id !== runId)) return false;
        this.storage.remove(RECEIPT_KEYS.run);
        if (!runId || this.storage.get(RECEIPT_KEYS.runLegacyId) === runId) {
            this.storage.remove(RECEIPT_KEYS.runLegacyId);
        }
        return true;
    }

    detachedRuns(): Array<Record<string, unknown>> {
        return arrayValue(readJson(this.storage, RECEIPT_KEYS.detachedRun));
    }

    archiveRun(row: Record<string, unknown>): void {
        const runId = String(row.run_id || '');
        const rows = this.detachedRuns();
        this.storage.set(RECEIPT_KEYS.detachedRun, JSON.stringify([
            row, ...rows.filter(item => item.run_id !== runId),
        ].slice(0, 50)));
    }
}
