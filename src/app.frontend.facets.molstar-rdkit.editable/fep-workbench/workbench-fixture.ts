import type { Edge, Network } from './workbench-types';

export const DemoCompounds = [
    ['T4L-BEN', 'c1ccccc1'], ['T4L-FLU', 'Fc1ccccc1'],
    ['T4L-CL', 'Clc1ccccc1'],
].map(([id, canonical_smiles]) => ({ id, canonical_smiles }));
const FallbackEdgeRows: Array<[string, string, string, number, number, number | null]> = [
    ['0000','T4L-BEN','T4L-FLU',.9512,11,0],
    ['0001','T4L-BEN','T4L-CL',.9512,11,0],
    ['0002','T4L-FLU','T4L-CL',.9512,12,0],
];
export const FallbackEdges: Edge[] = FallbackEdgeRows.map(([n, left, right, score, atoms, disagreement]) => ({
    edge_id: `rbfe-edge-${n}`, left_id: String(left), right_id: String(right), status: 'planned', mapping_score: Number(score), mapped_atom_count: Number(atoms),
    mapping_methods: ['kartograf', 'lomap'], mapping_disagreement_jaccard: disagreement == null ? null : Number(disagreement),
}));
export const FallbackNetwork: Network = {
    kind: 'rbfe_network_plan', digest: 'sha256:unresolved-t4l-benchmark', mode: 'pilot', compounds: DemoCompounds, edges: FallbackEdges,
    policy: { planner: 'openfe', mapping: 'OpenFE Lomap + Kartograf; RDKit FMCS diagnostic', minimum_similarity: .15, extra_edge_fraction: .5 },
    claim_boundary: 'Network and atom-mapping proposal only. No free energy is inferred until a separately versioned engine produces edge observations.',
};
