import { describe, expect, it } from '@jest/globals';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve } from 'node:path';

const facadePath = resolve(__dirname, 'fep-workbench.ts');
const facade = readFileSync(facadePath, 'utf8');
const sources = readdirSync(__dirname)
    .filter(name => name.endsWith('.ts') && !name.endsWith('.spec.ts'))
    .map(name => ({ name, source: readFileSync(resolve(__dirname, name), 'utf8') }));

const owners: Readonly<Record<string, string>> = {
    decideWorkbenchBoot: 'workbench-boot.ts',
    setSafeText: 'workbench-dom.ts',
    escapeHtml: 'workbench-dom.ts',
    safeElement: 'workbench-dom.ts',
    preparationReceiptFrom: 'workbench-receipts.ts',
    preparationRequestKey: 'workbench-receipts.ts',
    runReceiptFromData: 'workbench-receipts.ts',
    operationBindingFromReceipt: 'workbench-receipts.ts',
    preparationPolicyGate: 'workbench-view-model.ts',
    runAggregateViewFrom: 'workbench-view-model.ts',
    aggregatePanelViewFrom: 'workbench-view-model.ts',
    runHistoryViewFrom: 'workbench-view-model.ts',
    submitPreparationExactlyOnce: 'workbench-preparation.ts',
    preparationResultMatchesOpenCampaign: 'workbench-preparation.ts',
};

describe('FEP controller architecture ratchets', () => {
    it('keeps the facade below the accepted monolith ceiling', () => {
        // The research-loop mount added 403 bytes but no controller ownership;
        // the line ceiling remains the structural ratchet and this byte ceiling
        // catches only substantial regrowth.
        expect(statSync(facadePath).size).toBeLessThan(211_000);
        expect(facade.split(/\r?\n/).length).toBeLessThanOrEqual(1_200);
    });

    it.each(Object.entries(owners))('%s has one module owner and no facade definition', (symbol, owner) => {
        const definition = new RegExp(`(?:export\\s+)?function\\s+${symbol}\\b`);
        expect(sources.filter(file => definition.test(file.source)).map(file => file.name)).toEqual([owner]);
        expect(definition.test(facade)).toBe(false);
    });

    it('keeps receipt parsing and obsolete mock routes out of the facade', () => {
        expect(facade).not.toMatch(/JSON\.parse[^\n]*(?:pending_prepare|pending_planner|active_run)/);
        expect(facade).not.toContain('/time-tunnel/#/free-energy');
        expect(facade).not.toContain('/time-tunnel/field-workbench.html');
    });
});
