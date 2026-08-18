import { describe,expect,it } from '@jest/globals';
import { T4L_EIGHT_LIGAND_EXAMPLE,suggestedGuideStep } from './workbench-guided-build';

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
});
