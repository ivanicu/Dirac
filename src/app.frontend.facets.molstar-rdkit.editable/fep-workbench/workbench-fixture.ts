import type { Edge, Network } from './workbench-types';

export const DemoCompounds = [
    ['T4L-BEN', 'c1ccccc1'], ['T4L-FLU', 'Fc1ccccc1'],
    ['T4L-CL', 'Clc1ccccc1'], ['T4L-TOL', 'Cc1ccccc1'],
    ['T4L-ETB', 'CCc1ccccc1'], ['T4L-OXY', 'Cc1ccccc1C'],
    ['T4L-MXY', 'Cc1cccc(C)c1'], ['T4L-PXY', 'Cc1ccc(C)cc1'],
].map(([id, canonical_smiles]) => ({ id, canonical_smiles }));
const FallbackEdgeRows: Array<[string, string, string, number, number, number | null]> = [
    ['0000','T4L-BEN','T4L-FLU',.9512,11,0],
    ['0001','T4L-BEN','T4L-CL',.9512,11,0],
    ['0002','T4L-FLU','T4L-CL',.9512,12,0],
    ['0003','T4L-BEN','T4L-TOL',.9320,11,0],
    ['0004','T4L-FLU','T4L-TOL',.8840,11,0],
    ['0005','T4L-CL','T4L-TOL',.8620,11,0],
    ['0006','T4L-TOL','T4L-ETB',.9180,14,0],
    ['0007','T4L-TOL','T4L-OXY',.8740,14,0],
    ['0008','T4L-TOL','T4L-MXY',.8860,14,0],
    ['0009','T4L-TOL','T4L-PXY',.8920,14,0],
    ['0010','T4L-OXY','T4L-MXY',.8210,16,0],
    ['0011','T4L-MXY','T4L-PXY',.8330,16,0],
    ['0012','T4L-ETB','T4L-PXY',.7840,14,0],
];
export const FallbackEdges: Edge[] = FallbackEdgeRows.map(([n, left, right, score, atoms, disagreement]) => ({
    edge_id: `rbfe-edge-${n}`, left_id: String(left), right_id: String(right), status: 'planned', mapping_score: Number(score), mapped_atom_count: Number(atoms),
    mapping_methods: ['kartograf', 'lomap'], mapping_disagreement_jaccard: disagreement == null ? null : Number(disagreement),
}));
export const FallbackNetwork: Network = {
    kind: 'rbfe_network_plan', digest: 'sha256:unresolved-t4l-eight-ligand-benchmark', mode: 'pilot', compounds: DemoCompounds, edges: FallbackEdges,
    policy: { planner: 'openfe', mapping: 'OpenFE Lomap + Kartograf; RDKit FMCS diagnostic', minimum_similarity: .15, extra_edge_fraction: .5 },
    claim_boundary: 'Network and atom-mapping proposal only. No free energy is inferred until a separately versioned engine produces edge observations.',
};
