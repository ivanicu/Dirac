export type PairWitness=Record<string,unknown>;

export function proteinPairIdentity(witness:PairWitness):string {
    return [witness.protein_residue_name??witness.residue_name,witness.protein_chain_id??witness.chain_id,witness.protein_residue_number??witness.residue_number]
        .filter(value=>value!==undefined&&value!==null&&value!=='').join(' ');
}
