import { describe,expect,it } from '@jest/globals';
import { proteinPairIdentity } from './pose-reviewer-evidence';

describe('pose-review server evidence labels',()=>{
    it('renders the backend protein-prefixed residue identity',()=>{
        expect(proteinPairIdentity({ protein_residue_name: 'VAL',protein_chain_id: 'A',protein_residue_number: '111' })).toBe('VAL A 111');
    });

    it('keeps compatibility with the compact residue witness shape',()=>{
        expect(proteinPairIdentity({ residue_name: 'LEU',chain_id: 'B',residue_number: 84 })).toBe('LEU B 84');
    });
});
