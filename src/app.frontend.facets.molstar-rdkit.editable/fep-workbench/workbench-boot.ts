export type WorkbenchBootAction = 'reconcile-run' | 'new-campaign'
    | 'resume-planner' | 'resume-preparation' | 'load-network';

export type WorkbenchBootState = Readonly<{
    hasRunReceipt: boolean;
    hasLegacyRunId: boolean;
    hasPlannerReceipt: boolean;
    hasPreparationReceipt: boolean;
    blankRequested: boolean;
}>;

/**
 * Reduce all durable startup evidence to one authoritative recovery lane.
 * Keeping this decision pure prevents boot from racing independent restore
 * promises whose completion order would otherwise become application state.
 */
export function decideWorkbenchBoot(input: WorkbenchBootState): WorkbenchBootAction {
    if (input.hasRunReceipt || input.hasLegacyRunId) return 'reconcile-run';
    if (input.blankRequested) return 'new-campaign';
    if (input.hasPlannerReceipt) return 'resume-planner';
    if (input.hasPreparationReceipt) return 'resume-preparation';
    return 'load-network';
}
