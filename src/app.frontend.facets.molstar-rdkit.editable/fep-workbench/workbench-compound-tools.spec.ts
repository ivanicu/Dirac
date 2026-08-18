import { describe,expect,it,jest } from '@jest/globals';
import { chemblCandidate,parseAssayCsv,pubChemCandidates,resolveAssayRows,resolveCompoundQuery,resolveSmartImport,transformationRisk } from './workbench-compound-tools';

describe('novice compound workflow',()=>{
    it('turns PubChem identity records into exact visible structures',()=>{
        expect(pubChemCandidates({ PropertyTable: { Properties: [{ CID: 2244,Title: 'Aspirin',MolecularFormula: 'C9H8O4',SMILES: 'CC(=O)Oc1ccccc1C(=O)O',Charge: 0 }] } })).toEqual([expect.objectContaining({ id: 'ASPIRIN',sourceId: 'CID 2244',smiles: 'CC(=O)Oc1ccccc1C(=O)O',charge: 0 })]);
        expect(chemblCandidate({ molecule_chembl_id: 'CHEMBL25',pref_name: 'ASPIRIN',molecule_structures: { canonical_smiles: 'CC(=O)Oc1ccccc1C(=O)O' },molecule_properties: { full_molformula: 'C9H8O4',full_molcharge: 0 } })).toMatchObject({ id: 'CHEMBL25',source: 'CHEMBL' });
    });

    it('resolves a name and a CID through the official PubChem property shape',async()=>{
        const mock=jest.fn(async()=>new Response(JSON.stringify({ PropertyTable: { Properties: [{ CID: 2244,Title: 'Aspirin',SMILES: 'CCO',Charge: 0 }] } }),{ status: 200 })),fetcher=mock as unknown as typeof fetch;
        await expect(resolveCompoundQuery('aspirin',fetcher)).resolves.toMatchObject([{ sourceId: 'CID 2244' }]);
        await expect(resolveCompoundQuery('CID 2244',fetcher)).resolves.toMatchObject([{ sourceId: 'CID 2244' }]);
        const calls=mock.mock.calls as unknown as Array<[string]>; expect(String(calls[0][0])).toContain('/name/aspirin/property/'); expect(String(calls[1][0])).toContain('/cid/2244/property/');
    });

    it('parses quoted assay CSV and preserves endpoint, reference and priority',()=>{
        const rows=parseAssayCsv('compound_id,smiles,endpoint,value,unit,is_reference,priority\nPARENT,"CC(=O)O",IC50,12,nM,yes,HIGH\nA2,CCO,IC50,35,nM,no,MEDIUM');
        expect(rows).toEqual([
            { id: 'PARENT',smiles: 'CC(=O)O',query: '',endpoint: 'IC50',activity: '12',unit: 'nM',reference: true,priority: 'HIGH' },
            { id: 'A2',smiles: 'CCO',query: '',endpoint: 'IC50',activity: '35',unit: 'nM',reference: false,priority: 'MEDIUM' },
        ]);
    });

    it('resolves identifier-only assay rows while preserving explicit IDs',async()=>{
        const fetcher=jest.fn(async()=>new Response(JSON.stringify({ PropertyTable: { Properties: [{ CID: 2244,Title: 'Aspirin',SMILES: 'CCO',Charge: 0 }] } }),{ status: 200 })) as unknown as typeof fetch;
        await expect(resolveAssayRows(parseAssayCsv('id,cid,reference\nLead,2244,true'),fetcher)).resolves.toMatchObject([{ id: 'LEAD',smiles: 'CCO',reference: true }]);
    });

    it('makes low similarity, charge, and stereo changes visibly risky',()=>{
        expect(transformationRisk('CCO',{ id: 'A',name: 'A',smiles: 'CCCO',formula: '',charge: 0,source: 'PUBCHEM',sourceId: 'CID 1',sourceUrl: '',similarity: .9 })).toBe('LOW');
        expect(transformationRisk('CCO',{ id: 'A',name: 'A',smiles: 'CC[C@H](O)C',formula: '',charge: 0,source: 'PUBCHEM',sourceId: 'CID 1',sourceUrl: '',similarity: .9 })).toBe('HIGH');
        expect(transformationRisk('CCO',{ id: 'A',name: 'A',smiles: 'CC[NH3+]',formula: '',charge: 1,source: 'PUBCHEM',sourceId: 'CID 1',sourceUrl: '',similarity: .9 })).toBe('HIGH');
    });

    it('accepts names, IDs, SMILES and ID + SMILES through one import surface',async()=>{
        const fetcher=jest.fn(async()=>new Response(JSON.stringify({ PropertyTable: { Properties: [{ CID: 2244,Title: 'Aspirin',SMILES: 'ASP',Charge: 0 }] } }),{ status: 200 })) as unknown as typeof fetch;
        const canonicalize=jest.fn(async(value:string)=>({ CCO: 'CCO','LEAD CCC': null,CCC: 'CCC' } as Record<string,string|null>)[value]??null);
        await expect(resolveSmartImport('Aspirin\nCCO\nLEAD CCC\nAspirin',fetcher,canonicalize)).resolves.toEqual({ rows: [{ id: 'ASPIRIN',smiles: 'ASP' },{ id: 'CMPD-002',smiles: 'CCO' },{ id: 'LEAD',smiles: 'CCC' }],resolved: 2,duplicates: 1 });
    });

});
