import { describe, expect, it } from '@jest/globals';
import { decideWorkbenchBoot } from './workbench-boot';

describe('serialized FEP workbench boot reducer',()=>{
    const decide=(overrides:Partial<Parameters<typeof decideWorkbenchBoot>[0]>)=>decideWorkbenchBoot({ hasRunReceipt: false,hasLegacyRunId: false,hasPlannerReceipt: false,hasPreparationReceipt: false,blankRequested: false,...overrides });
    it.each([
        [{ hasRunReceipt: true },'reconcile-run'],
        [{ hasLegacyRunId: true },'reconcile-run'],
        [{ hasRunReceipt: true,blankRequested: true },'reconcile-run'],
        [{ blankRequested: true,hasPlannerReceipt: true,hasPreparationReceipt: true },'new-campaign'],
        [{ hasPlannerReceipt: true,hasPreparationReceipt: true },'resume-planner'],
        [{ hasPreparationReceipt: true },'resume-preparation'],
        [{},'load-network'],
    ] as const)('chooses one authoritative lane for %j', (input, expected) => {
        expect(decide(input)).toBe(expected);
    });
});
