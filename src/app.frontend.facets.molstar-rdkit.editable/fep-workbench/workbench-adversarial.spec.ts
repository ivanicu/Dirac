import { describe, expect, it } from '@jest/globals';
import { buildProteinSearchRequest, inspectBoundLigands } from './workbench-target-search';
import { compoundWorkflowSnapshot, compoundWorkflowSnapshotMatches, parseAssayCsv } from './workbench-compound-tools';
import { CHEMISTRY_DIMENSIONS, chemistryEvidenceFrom, executionEligibilityFrom } from './workbench-state';

function hetatm(serial:number,name:string,altloc:string,resname:string,chain:string,residue:number,element:string):string {
    return `HETATM${String(serial).padStart(5)} ${name.padEnd(4)}${altloc}${resname.padStart(3)} ${chain}${String(residue).padStart(4)}    10.000  10.000  10.000  1.00 20.00          ${element.padStart(2)}`;
}

describe('adversarial FEP import boundaries',()=>{
    it('counts one chemical atom once across alternate conformers and repeated models',()=>{
        const atomA=hetatm(1,'C1','A','LIG','A',401,'C');
        const atomB=hetatm(2,'C1','B','LIG','A',401,'C');
        const atom2=hetatm(3,'C2',' ','LIG','A',401,'C');
        const pdb=['MODEL        1',atomA,atomB,atom2,'ENDMDL','MODEL        2',atomA,atomB,atom2,'ENDMDL'].join('\n');
        expect(inspectBoundLigands(pdb)).toMatchObject([{ resname: 'LIG',heavy_atom_count: 2 }]);
    });

    it('refuses an unterminated quoted CSV field instead of guessing the series',()=>{
        expect(()=>parseAssayCsv('id,smiles,reference\nLEAD,"CCO,true')).toThrow(/quote/i);
    });

    it('refuses a header-only assay before it can replace the current series',()=>{
        expect(()=>parseAssayCsv('id,smiles,reference\n')).toThrow(/compound row/i);
    });

    it('refuses duplicate compound identities and multiple reference parents',()=>{
        expect(()=>parseAssayCsv('id,smiles,reference\nLEAD,CCO,true\nLEAD,CCC,false')).toThrow(/duplicate.*id/i);
        expect(()=>parseAssayCsv('id,smiles,reference\nA,CCO,true\nB,CCC,true')).toThrow(/one reference/i);
    });

    it('refuses rows whose field count does not match the header',()=>{
        expect(()=>parseAssayCsv('id,smiles,reference\nA,CCO,true,unexpected')).toThrow(/fields; expected/i);
    });

    it('bounds protein search input before constructing an external request',()=>{
        expect(()=>buildProteinSearchRequest('A'.repeat(161))).toThrow(/160 characters/i);
    });

    it('invalidates delayed compound work after any input, series, or parent change',()=>{
        const rows=[{ id: 'A',smiles: 'CCO' }],snapshot=compoundWorkflowSnapshot('aspirin',rows,'A');
        expect(compoundWorkflowSnapshotMatches(snapshot,'aspirin',rows,'A')).toBe(true);
        expect(compoundWorkflowSnapshotMatches(snapshot,'ibuprofen',rows,'A')).toBe(false);
        expect(compoundWorkflowSnapshotMatches(snapshot,'aspirin',[...rows,{ id: 'B',smiles: 'CCC' }],'A')).toBe(false);
        expect(compoundWorkflowSnapshotMatches(snapshot,'aspirin',rows,'B')).toBe(false);
    });

    it('cannot label partial heavy-atom coverage or malformed reasons executable',()=>{
        const chemistry={ schema_version: 'rbfe-chemistry-change.v1',verdict: 'CONFIRMED',full_heavy_atom_coverage: false,ledger: CHEMISTRY_DIMENSIONS.map(dimension=>({ dimension,verdict: 'CONFIRMED',summary: `${dimension} checked`,witnesses: [] })) };
        expect(chemistryEvidenceFrom(chemistry)).toBeNull();
        expect(executionEligibilityFrom({ verdict: 'CONFIRMED',reasons: [{ code: 'IGNORED' }] })).toBeNull();
        expect(executionEligibilityFrom({ verdict: 'CONFIRMED',reasons: [{ code: 'CONTRADICTION',message: 'but still marked confirmed' }] })).toBeNull();
    });
});
