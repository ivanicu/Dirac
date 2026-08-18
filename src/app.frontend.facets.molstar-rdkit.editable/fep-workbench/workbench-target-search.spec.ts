import { describe,expect,it,jest } from '@jest/globals';
import { buildProteinSearchRequest,inspectBoundLigands,proteinStructureCandidate,rankProteinStructures,searchProteinStructures } from './workbench-target-search';

const record=(id:string,overrides:Record<string,unknown>={})=>({
    entry: { id },struct: { title: `${id} kinase structure` },exptl: [{ method: 'X-RAY DIFFRACTION' }],
    rcsb_entry_info: { resolution_combined: [1.8],nonpolymer_bound_components: ['LIG'],deposited_modeled_polymer_monomer_count: 280,deposited_polymer_monomer_count: 300,...overrides },
});

describe('protein-name to PDB recommendations',()=>{
    it('builds an experimental all-terms RCSB query',()=>{
        expect(buildProteinSearchRequest('KRAS G12D')).toMatchObject({
            query: { service: 'full_text',parameters: { value: 'kras + g12d' } },
            request_options: { results_content_type: ['experimental'],paginate: { rows: 12 } },
            return_type: 'entry',
        });
        expect(()=>buildProteinSearchRequest(' -- ')).toThrow('Enter a protein name');
    });

    it('explains and ranks ligand-bound high-resolution structures first',()=>{
        const strong=proteinStructureCandidate({ identifier: '1ABC',score: .9 },record('1ABC'),'kinase')!;
        const weak=proteinStructureCandidate({ identifier: '2DEF',score: .9 },record('2DEF',{ resolution_combined: [4.2],nonpolymer_bound_components: [],deposited_modeled_polymer_monomer_count: 150 }),'kinase')!;
        expect(strong).toMatchObject({ pdbId: '1ABC',resolutionAngstrom: 1.8,boundComponents: ['LIG'] });
        expect(strong.reasons).toEqual(expect.arrayContaining(['1 bound component type','93% polymer residues modeled']));
        expect(rankProteinStructures([weak,strong]).map(row=>row.pdbId)).toEqual(['1ABC','2DEF']);
    });

    it('ranks a specific target-name match above a merely related kinase',()=>{
        const egfr=proteinStructureCandidate({ identifier: '3IKA',score: .8 },{ ...record('3IKA'),struct: { title: 'Crystal structure of EGFR kinase' } },'EGFR kinase')!;
        const ack=proteinStructureCandidate({ identifier: '1U54',score: 1 },{ ...record('1U54'),struct: { title: 'ACK1 tyrosine kinase' } },'EGFR kinase')!;
        expect(rankProteinStructures([ack,egfr]).map(row=>row.pdbId)).toEqual(['3IKA','1U54']);
        expect(egfr.reasons[0]).toBe('100% specific name match');
    });

    it('drops malformed hits, deduplicates IDs and tolerates one failed metadata record',async()=>{
        const fetcher=jest.fn(async(input:RequestInfo|URL)=>{
            const url=String(input);
            if (url.includes('rcsbsearch')) return new Response(JSON.stringify({ result_set: [{ identifier: '1ABC',score: 1 },{ identifier: '1abc',score: .8 },{ identifier: 'BAD',score: 1 },{ identifier: '2DEF',score: .7 }] }),{ status: 200 });
            if (url.endsWith('1ABC')) return new Response(JSON.stringify(record('1ABC')),{ status: 200 });
            return new Response('',{ status: 503 });
        }) as unknown as typeof fetch;
        await expect(searchProteinStructures('example kinase',fetcher)).resolves.toMatchObject([{ pdbId: '1ABC' }]);
        expect(fetcher).toHaveBeenCalledTimes(3);
    });

    it('returns a usable empty state for an official 204 response',async()=>{
        const fetcher=jest.fn(async()=>new Response(null,{ status: 204 })) as unknown as typeof fetch;
        await expect(searchProteinStructures('missing target',fetcher)).resolves.toEqual([]);
    });

    it('keeps an ATP analogue out of the drug-like reference recommendations',()=>{
        const het=(serial:number,atom:string,resname:string,residue:number)=>`HETATM${String(serial).padStart(5)} ${atom.padEnd(4)} ${resname.padStart(3)} A${String(residue).padStart(4)}      10.000  10.000  10.000  1.00 20.00           C`;
        const candidates=inspectBoundLigands([het(1,'C1','ANP',1102),het(2,'C2','ANP',1102),het(3,'C1','57N',1103),het(4,'C2','57N',1103)].join('\n'));
        expect(candidates.map(row=>[row.resname,row.role])).toEqual([['57N','ligand'],['ANP','cofactor']]);
    });
});
