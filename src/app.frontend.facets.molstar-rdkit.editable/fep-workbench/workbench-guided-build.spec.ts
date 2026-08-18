import { describe,expect,it } from '@jest/globals';
import { campaignEstimate,parseGpuHourCap,prepLigandsNext,restoreParentCompoundSelection,T4L_EIGHT_LIGAND_EXAMPLE,suggestedGuideStep,validateDecisionInputs } from './workbench-guided-build';

describe('guided FEP campaign entry',()=>{
    it('ships a complete, unique eight-ligand example',()=>{
        const rows=T4L_EIGHT_LIGAND_EXAMPLE.ligands.split('\n').map(row=>row.trim().split(/\s+/,2));
        expect(T4L_EIGHT_LIGAND_EXAMPLE.pdb).toBe('181L');
        expect(T4L_EIGHT_LIGAND_EXAMPLE.referenceResname).toBe('BNZ');
        expect(rows).toHaveLength(8);
        expect(new Set(rows.map(([id])=>id)).size).toBe(8);
        expect(new Set(rows.map(([,smiles])=>smiles)).size).toBe(8);
        expect(T4L_EIGHT_LIGAND_EXAMPLE.fields['ligand-stereo']).toBe('enumerate_unknown');
        for (const id of ['campaign-name','campaign-question','assay-anchor','portfolio-priority','cost-cap','next-action','stop-rule']) expect(T4L_EIGHT_LIGAND_EXAMPLE.fields[id]).toBeTruthy();
    });

    it('routes a new user to the first incomplete task',()=>{
        expect(suggestedGuideStep(false,false,false)).toBe('target');
        expect(suggestedGuideStep(true,false,false)).toBe('ligands');
        expect(suggestedGuideStep(true,true,false)).toBe('setup');
        expect(suggestedGuideStep(true,true,true)).toBe('review');
    });

    it('uses one estimate for setup, review and the budget gate',()=>{
        expect(campaignEstimate(8,'balanced')).toEqual({ nodes: 8,edges: 12,jobs: 72,gpuHours: 360 });
        expect(campaignEstimate(8,'minimum')).toEqual({ nodes: 8,edges: 7,jobs: 42,gpuHours: 210 });
        expect(campaignEstimate(2,'dense')).toEqual({ nodes: 2,edges: 1,jobs: 6,gpuHours: 30 });
        expect(parseGpuHourCap('420 GPU hours')).toBe(420);
        expect(parseGpuHourCap('about 420')).toBeNull();
    });

    it('refuses placeholders, missing assay units and an undersized compute cap',()=>{
        const estimate=campaignEstimate(8,'balanced'),base={
            'campaign-question': 'Which analogue should advance to synthesis after this comparison?',
            'assay-anchor': 'Biochemical IC50 · nM',
            'portfolio-priority': 'HIGH · DECISION-CHANGING',
            'cost-cap': '420 GPU hours',
            'next-action': 'Synthesize the supported analogue and confirm it in the assay.',
            'stop-rule': 'Stop if pose evidence or convergence remains unresolved.',
        };
        expect(validateDecisionInputs(base,estimate).ready).toBe(true);
        expect(validateDecisionInputs({ ...base,'campaign-question': 'xxxx','assay-anchor': 'potency assay','cost-cap': '120 GPU hours' },estimate)).toMatchObject({ ready: false,invalid: expect.arrayContaining([
            'Project question needs a specific decision, not a placeholder',
            'Assay anchor needs an endpoint and unit',
            'Estimated 360 GPU hours exceeds the 120 GPU-hour cap',
        ]) });
    });

    it('restores only an available explicit reference compound',()=>{
        const select={ value: '',options: [{ value: '' },{ value: 'BEN' }] },fakeDocument={ getElementById: ()=>select } as unknown as Document;
        restoreParentCompoundSelection(fakeDocument,'BEN'); expect(select.value).toBe('BEN');
        restoreParentCompoundSelection(fakeDocument,'MISSING'); expect(select.value).toBe('BEN');
    });

    it('turns next into validation plus a reversible first-reference suggestion',async()=>{
        const select={ value: '',options: [{ value: '' },{ value: 'BEN' },{ value: 'TOL' }] },document={ getElementById: ()=>select } as unknown as Document;
        await expect(prepLigandsNext(document,async()=>({ rows: [{ id: 'BEN',smiles: 'c1ccccc1' },{ id: 'TOL',smiles: 'Cc1ccccc1' }],valid: 2 }))).resolves.toBe(true);
        expect(select.value).toBe('BEN');
        select.value='TOL'; await prepLigandsNext(document,async()=>({ rows: [{ id: 'BEN',smiles: 'c1ccccc1' },{ id: 'TOL',smiles: 'Cc1ccccc1' }],valid: 2 })); expect(select.value).toBe('TOL');
        await expect(prepLigandsNext(document,async()=>({ rows: [{ id: 'BAD',smiles: '?' },{ id: 'TOL',smiles: 'Cc1ccccc1' }],valid: 1 }))).resolves.toBe(false);
    });
});
