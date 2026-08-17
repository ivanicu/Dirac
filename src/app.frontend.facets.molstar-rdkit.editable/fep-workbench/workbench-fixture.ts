import type { Edge, Network } from './workbench-types';
import { T4lBenchmarkResults } from './workbench-benchmark-results';

export const DemoCompounds = T4lBenchmarkResults.map(({ id,smiles: canonical_smiles })=>({ id,canonical_smiles }));
const FallbackEdgeRows: Array<[string, string, string, number, number, number | null]> = [
    ['0000','T4L-BEN','T4L-TOL',.951,11,0],
    ['0001','T4L-BEN','T4L-OXY',.938,12,0],
    ['0002','T4L-BEN','T4L-PXY',.938,12,0],
    ['0003','T4L-BEN','T4L-ETB',.924,12,0],
    ['0004','T4L-BEN','T4L-BZF',.906,13,0],
    ['0005','T4L-BEN','T4L-IDN',.892,13,0],
    ['0006','T4L-BEN','T4L-IDL',.874,13,0],
    ['0007','T4L-TOL','T4L-ETB',.918,14,0],
    ['0008','T4L-OXY','T4L-PXY',.902,16,0],
    ['0009','T4L-BZF','T4L-IDN',.881,17,0],
    ['0010','T4L-IDN','T4L-IDL',.866,16,0],
    ['0011','T4L-TOL','T4L-PXY',.842,15,0],
    ['0012','T4L-ETB','T4L-IDN',.814,17,0],
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
