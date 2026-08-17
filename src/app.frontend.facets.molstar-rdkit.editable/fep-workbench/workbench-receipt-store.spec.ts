import { describe, expect, it } from '@jest/globals';
import { RECEIPT_KEYS, WorkbenchReceiptStore, type ReceiptStorage } from './workbench-receipt-store';
import { preparationRequestKey, type LegacyPreparationReceipt, type PlannerReceipt, type PreparationReceipt, type RunReceipt } from './workbench-receipts';

class MemoryStorage implements ReceiptStorage {
    readonly values=new Map<string,string>();
    get(key:string):string|null{return this.values.get(key)??null;}
    set(key:string,value:string):void{this.values.set(key,value);}
    remove(key:string):void{this.values.delete(key);}
}

const digest=`sha256:${'a'.repeat(64)}`,job='11111111-1111-4111-8111-111111111111';
const campaign={kind:'rbfe_campaign' as const,id:'campaign',version:2,sha256:digest};

describe('durable workbench receipt store',()=>{
    it('owns preparation parsing, terminal migration, CAS, and detached history',()=>{
        const storage=new MemoryStorage(),store=new WorkbenchReceiptStore(storage);
        const receipt={schema_version:2,job_id:job,campaign_id:'campaign',submitted_at:'2026-08-17T16:00:00.000Z',
            input_version:2,input_scientific_generation:2,input_scientific_digest:digest,
            input_signature:'sealed'} satisfies LegacyPreparationReceipt;
        store.writePreparation(receipt);
        expect(store.removePreparationReceipt({...receipt,job_id:'22222222-2222-4222-8222-222222222222'})).toBe(false);
        expect(store.archivePreparation(receipt)).toBe(true);
        expect(storage.get(RECEIPT_KEYS.preparation)).toBeNull();
        expect(JSON.parse(storage.get(RECEIPT_KEYS.detachedPreparation)||'[]')).toEqual([receipt]);
        expect(storage.get(RECEIPT_KEYS.preparationLastJobId)).toBe(job);

        storage.set(RECEIPT_KEYS.preparation,JSON.stringify({schema_version:1,job_id:job}));
        expect(store.readPreparation()).toBeNull();
        expect(storage.get(RECEIPT_KEYS.preparation)).toBeNull();
        expect(storage.get(RECEIPT_KEYS.preparationLastJobId)).toBe(job);
    });

    it('uses owner-and-job CAS when removing planner receipts',()=>{
        const storage=new MemoryStorage(),store=new WorkbenchReceiptStore(storage);
        const receipt={schema_version:2,owner_token:'owner',created_at:'now',status:'waiting',job_id:job,campaign_scientific_ref:campaign,prepared_system_ref:{kind:'prepared_receptor_state',id:'system',sha256:digest},input_signature:'sealed'} as PlannerReceipt;
        store.writePlanner(receipt);
        expect(store.removePlanner('other',job)).toBe(false);
        expect(store.removePlanner('owner','22222222-2222-4222-8222-222222222222')).toBe(false);
        expect(store.removePlanner('owner',job)).toBe(true);
        expect(storage.get(RECEIPT_KEYS.planner)).toBeNull();
    });

    it('uses owner, request key, and acknowledgement as preparation CAS identity',()=>{
        const storage=new MemoryStorage(),store=new WorkbenchReceiptStore(storage),now='2026-08-17T16:00:00.000Z';
        const receipt={schema_version:3,owner_token:'first',created_at:now,updated_at:now,
            status:'submitting',request_nonce:'33333333-3333-4333-8333-333333333333',
            request_key:preparationRequestKey(campaign,'33333333-3333-4333-8333-333333333333'),
            job_id:null,campaign_scientific_ref:campaign,input_version:2,
            input_signature:'sealed'} satisfies PreparationReceipt;
        store.writePreparation(receipt);
        const claimed=store.claimPreparation(receipt,'second','2026-08-17T16:00:01.000Z');
        expect(claimed?.owner_token).toBe('second');
        expect(store.updatePreparation(receipt,{job_id:job,status:'waiting'},now)).toBeNull();
        expect(store.removePreparationReceipt(receipt)).toBe(false);
        expect(store.archivePreparation(receipt)).toBe(false);
        const acknowledged=store.updatePreparation(claimed!,{job_id:job,status:'waiting'},
            '2026-08-17T16:00:02.000Z');
        expect(acknowledged).toMatchObject({owner_token:'second',job_id:job,status:'waiting'});
        expect(store.removePreparationReceipt(claimed!)).toBe(false);
        expect(store.removePreparationReceipt(acknowledged!)).toBe(true);
    });

    it('does not delete a newly claimed preparation receipt during archive cleanup',()=>{
        const campaignNonce='33333333-3333-4333-8333-333333333333',now='2026-08-17T16:00:00.000Z';
        const receipt={schema_version:3,owner_token:'first',created_at:now,updated_at:now,
            status:'submitting',request_nonce:campaignNonce,
            request_key:preparationRequestKey(campaign,campaignNonce),job_id:null,
            campaign_scientific_ref:campaign,input_version:2,input_signature:'sealed'} satisfies PreparationReceipt;
        class ArchiveRaceStorage extends MemoryStorage {
            raced=false;
            override set(key:string,value:string):void {
                super.set(key,value);
                if(key===RECEIPT_KEYS.detachedPreparation&&!this.raced){
                    this.raced=true;
                    super.set(RECEIPT_KEYS.preparation,JSON.stringify({
                        ...receipt,owner_token:'second',updated_at:'2026-08-17T16:00:01.000Z',
                    }));
                }
            }
        }
        const storage=new ArchiveRaceStorage(),store=new WorkbenchReceiptStore(storage);
        store.writePreparation(receipt);
        expect(store.archivePreparation(receipt)).toBe(false);
        expect(store.readPreparation()).toMatchObject({owner_token:'second',request_key:receipt.request_key});
    });

    it('mirrors main-copy physical receipts but keeps audit storage independent',()=>{
        const local=new MemoryStorage(),global=new MemoryStorage();
        const receipt={schema_version:2,owner_token:'owner',created_at:'now',updated_at:'now',run_id:job,request_key:'request',state:'running',campaign_scientific_ref:campaign,edge_id:'edge',spec_digest:digest,edge_spec_ref:{kind:'artifact',id:'spec',sha256:digest},edge_network_ref:{kind:'artifact',id:'network',sha256:digest},complex_transformation_ref:{kind:'artifact',id:'complex',sha256:digest},solvent_transformation_ref:{kind:'artifact',id:'solvent',sha256:digest}} as RunReceipt;
        new WorkbenchReceiptStore(local,global).writeRun(receipt);
        expect(global.get('dirac.rbfe.global_physical_receipt.v1.campaign')).toBe(JSON.stringify(receipt));
        const audit=new MemoryStorage();new WorkbenchReceiptStore(audit).writeRun(receipt);
        expect(audit.get(RECEIPT_KEYS.run)).toBe(JSON.stringify(receipt));
        expect(global.values.size).toBe(1);
    });

    it('deduplicates bounded detached RunSet history by run id',()=>{
        const storage=new MemoryStorage(),store=new WorkbenchReceiptStore(storage);
        store.archiveRun({run_id:job,state:'failed'});
        store.archiveRun({run_id:job,state:'completed'});
        expect(store.detachedRuns()).toEqual([{run_id:job,state:'completed'}]);
    });
});
