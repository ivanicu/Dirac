import { describe,expect,it,jest } from '@jest/globals';
import { applyRecommendedSetup,bindWorkflowAccelerators,recommendEight,repairLigandSeries,setExceptionView } from './workbench-accelerators';

describe('FEP workflow accelerators',()=>{
    it('repairs only deterministic formatting and duplicate issues',()=>{
        expect(repairLigandSeries('lead one  CCO\nlead-one  CCO\nlead one  CCC')).toMatchObject({ blockers: expect.arrayContaining(['Line 1: expected SMILES or ID + SMILES']) });
        expect(repairLigandSeries('lead@  CCO\nlead@  CCC\nother  CCO')).toEqual({ rows: [{ id: 'lead-',smiles: 'CCO' },{ id: 'lead--2',smiles: 'CCC' }],fixes: ['lead@ renamed to lead-','lead@ renamed to lead--2','Removed duplicate structure on line 3'],blockers: [] });
    });

    it('keeps the parent and selects the seven most similar compounds',async()=>{
        const rows=Array.from({ length: 10 },(_,index)=>({ id: `C${index}`,smiles: String(index) })),similarity=jest.fn(async(_left:string,right:string)=>Number(right)/10);
        const selected=await recommendEight(rows,'C0',similarity);
        expect(selected.rows.map(row=>row.id)).toEqual(['C0','C9','C8','C7','C6','C5','C4','C3']);
        expect(selected.parentId).toBe('C0');
    });

    it('applies available safe defaults and derives a balanced cost cap',()=>{
        const nodes=new Map<string,any>(),select=(id:string,values:string[],value=values[0])=>nodes.set(id,{ value,options: values.map(option=>({ value: option })),dispatchEvent: jest.fn() });
        select('assembly-select',['deposited_asymmetric_unit']); select('prep-waters',['keep_all','remove_all'],'keep_all'); select('protocol-select',['openfe-rfe-standard-v1']);
        nodes.set('cost-cap',{ value: '',dispatchEvent: jest.fn() }); nodes.set('pose-choice-align',{ classList: { contains: ()=>true },click: jest.fn() }); nodes.set('network-choice-balanced',{ classList: { contains: ()=>true },click: jest.fn() });
        const document={ getElementById: (id:string)=>nodes.get(id)||null } as unknown as Document;
        applyRecommendedSetup(document,8); expect(nodes.get('prep-waters').value).toBe('remove_all'); expect(nodes.get('cost-cap').value).toBe('360 GPU hours');
    });

    it('switches the whole review surface between all checks and exceptions',()=>{
        const root={ setAttribute: jest.fn() },button={ setAttribute: jest.fn(),textContent: '' },document={ getElementById: (id:string)=>id==='campaign-builder'?root:button } as unknown as Document;
        setExceptionView(document,true); expect(root.setAttribute).toHaveBeenCalledWith('data-only-exceptions','true'); expect(button.textContent).toBe('SHOW ALL CHECKS');
    });

    it('revalidates the series after recommended policy changes and restores its parent',async()=>{
        let recommended:((event?:unknown)=>Promise<void>)|undefined; const button={ addEventListener: (_type:string,listener:unknown)=>{ recommended=listener as typeof recommended; } };
        const document={ getElementById: (id:string)=>id==='use-recommended-setup'?button:null } as unknown as Document,replaceSeries=jest.fn<(rows:Array<{id:string;smiles:string}>,context:{parentId?:string})=>Promise<void>>(async()=>undefined),applyRecommended=jest.fn();
        bindWorkflowAccelerators(document,{ locked: ()=>false,notify: jest.fn(),getRawSeries: ()=>'',getRows: ()=>[{ id: 'BEN',smiles: 'c1ccccc1' }],getParent: ()=>'BEN',replaceSeries,similarity: async()=>null,duplicateCampaign: async()=>undefined,applyRecommended });
        await recommended?.(); expect(applyRecommended).toHaveBeenCalledTimes(1); expect(replaceSeries).toHaveBeenCalledWith([{ id: 'BEN',smiles: 'c1ccccc1' }],{ parentId: 'BEN' });
    });
});
