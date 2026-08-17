import { describe, expect, it } from '@jest/globals';
import type { RunReceipt } from './workbench-receipts';
import { aggregatePanelViewFrom, preparationPolicyGate, preparationPolicyViewFrom, runAggregateViewFrom, runHistoryViewFrom, runJobsViewFrom } from './workbench-view-model';

const digest=`sha256:${'a'.repeat(64)}`;
const aggregate={
    status:'computed_unattested',result_digest:digest,passed_leg_count:6,
    node_estimates:[{compound_id:'B',relative_dg_kcal_mol:-1.25,uncertainty_kcal_mol:.31}],
    failed_edges:[],cycle_closure:[{}],convergence_verdicts:[{},{}],
};

describe('aggregate and RunSet history view models',()=>{
    it('fails incomplete summaries closed instead of defaulting to six legs',()=>{
        expect(runAggregateViewFrom({...aggregate,passed_leg_count:0})?.passedLegCount).toBe(0);
        expect(runAggregateViewFrom({...aggregate,passed_leg_count:undefined})).toBeNull();
        expect(runAggregateViewFrom({...aggregate,result_digest:undefined})).toBeNull();
        expect(runAggregateViewFrom({...aggregate,passed_leg_count:7})).toBeNull();
        expect(aggregatePanelViewFrom({...aggregate,node_estimates:undefined})).toMatchObject({
            verified:false,acceptedLegs:'— / 6',ddg:'UNVERIFIED',
        });
    });

    it('formats exact server values for the selected endpoint',()=>{
        expect(aggregatePanelViewFrom(aggregate,'B')).toMatchObject({
            verified:true,acceptedLegs:'6 / 6',convergence:'2 SERVER VERDICTS',
            ddg:'-1.250 ± 0.310 kcal/mol',
        });
        expect(runAggregateViewFrom({...aggregate,node_estimates:[{compound_id:'B',relative_dg_kcal_mol:'NaN',uncertainty_kcal_mol:.31}]})).toBeNull();
    });

    it('normalizes active and detached receipts without rendering HTML',()=>{
        const active={
            state:'running',run_id:null,request_key:'prepare-safe',edge_id:'edge-a',
            campaign_scientific_ref:{version:7},
        } as unknown as RunReceipt;
        const view=runHistoryViewFrom(active,[{
            run_id:'<img onerror=owned>',edge_id:'edge-b',detached_at:'now',
            run_snapshot:{state:'completed',aggregate_output:{result:{data:aggregate}}},
        },null]);
        expect(view).toMatchObject({activeCount:1,historicalCount:1,meta:'1 ACTIVE · 1 HISTORICAL'});
        expect(view.rows[0]).toMatchObject({active:true,heading:'ACTIVE · RUNNING',runIdentifier:'prepare-safe'});
        expect(view.rows[1]).toMatchObject({
            active:false,heading:'COMPLETED',runIdentifier:'<img onerror=owned>',
            aggregate:`6/6 legs · B -1.250 ± 0.310 kcal/mol · ${digest}`,
        });
    });

    it('rejects malformed detached aggregate data without inventing a result',()=>{
        const view=runHistoryViewFrom(null,[{run_id:'run',run_snapshot:{aggregate_output:{result:{data:{status:'done'}}}}}]);
        expect(view.rows[0].aggregate).toBe('AGGREGATE UNVERIFIED / NOT APPLICABLE');
        expect(view.meta).toBe('0 ACTIVE · 1 HISTORICAL');
    });

    it('derives the six-job execution boundary without DOM access',()=>{
        const active=runJobsViewFrom([
            {leg:'complex',repeat:1,jobId:'job-1',state:'running'},
            {leg:'solvent',repeat:1,jobId:'<job-2>',state:'blocked',error:'<unsafe>'},
        ],true);
        expect(active).toMatchObject({
            empty:false,executionMeta:'0 / 6 COMPLETE · RUNNING',resultCount:'0 · RUNNING',
            boundary:'PHYSICAL EXECUTION · NOT YET AN RBFE RESULT',
        });
        expect(active.rows[1]).toMatchObject({jobId:'<job-2>',state:'<unsafe>',stateClass:'blocked'});
        expect(runJobsViewFrom([],true)).toMatchObject({
            empty:true,ready:true,emptyHeading:'SYSTEM READY · 6 JOBS NOT STARTED',
        });
    });

    it('does not turn an unknown server state into a CSS class',()=>{
        const view=runJobsViewFrom([{
            leg:'complex',repeat:1,jobId:'job',state:'done injected',
        }],false);
        expect(view.rows[0].stateClass).toBe('unknown');
        expect(view.executionMeta).toBe('0 / 6 COMPLETE · TERMINAL');
    });

    it('fails preparation policy closed and uses only server verdicts/witnesses',()=>{
        expect(preparationPolicyViewFrom(null)).toMatchObject({
            generated:false,blocked:false,summary:'NOT GENERATED',
            blockers:[{axis:'policy_execution',verdict:'UNVERIFIED'}],
        });
        const system={receptor_report:{policy_execution:{
            waters:{verdict:'CONFIRMED',witness:'removed by policy'},
            protonation:{verdict:'changed',witness:'<server witness>'},
        }}} as any;
        expect(preparationPolicyGate(system)).toMatchObject({
            ok:false,blockers:[{axis:'protonation',verdict:'UNVERIFIED',witness:'<server witness>'}],
        });
    });
});
