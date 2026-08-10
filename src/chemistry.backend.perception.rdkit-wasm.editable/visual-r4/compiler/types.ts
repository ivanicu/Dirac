export interface MolecularNodesCompiledSocket {
    readonly id: string;
    readonly name: string;
    readonly type: string;
    readonly default?: unknown;
}

export interface MolecularNodesCompiledNode {
    readonly id: string;
    readonly name: string;
    readonly label?: string;
    readonly op: string;
    readonly call?: string;
    readonly value?: number | boolean;
    readonly properties?: Readonly<Record<string, unknown>>;
    readonly inputs: readonly MolecularNodesCompiledSocket[];
    readonly outputs: readonly MolecularNodesCompiledSocket[];
}

export interface MolecularNodesCompiledLink {
    readonly from: string;
    readonly out: string;
    readonly to: string;
    readonly in: string;
}

export interface MolecularNodesCompiledGraph {
    readonly id: string;
    readonly name: string;
    readonly kind: 'group' | 'material';
    readonly type: string;
    readonly dependencies: readonly string[];
    readonly nodes: readonly MolecularNodesCompiledNode[];
    readonly links: readonly MolecularNodesCompiledLink[];
    readonly stats: {
        readonly sourceNodes: number;
        readonly retainedNodes: number;
        readonly eliminatedNodes: number;
        readonly links: number;
        readonly foldedConstants: number;
    };
}

export interface MolecularNodesCompiledProgram {
    readonly id: string;
    readonly name: string;
    readonly kind: string;
    readonly entry: string;
    readonly backend: string;
}

export interface MolecularNodesCompiledLibrary {
    readonly name: string;
    readonly entry: string;
}

export interface MolecularNodesBufferLayout {
    readonly stride: number;
    readonly fields: Readonly<Record<string, string>>;
}

export interface MolecularNodesKernel {
    readonly id: string;
    readonly stage: 'compute';
    readonly workgroupSize: number;
    readonly source: string;
    readonly hash: string;
}

export interface MolecularNodesCompiledBundle {
    readonly schema: 'molecular-representation-ir@1' | 'molecular-representation-aot@1';
    readonly artifact: {
        readonly algorithm: 'sha256';
        readonly hash: string;
    };
    readonly compiler: {
        readonly name: string;
        readonly stage: string;
        readonly target: string;
        readonly passes: readonly string[];
    };
    readonly source: Readonly<Record<string, unknown>>;
    readonly runtime: {
        readonly bufferLayouts: Readonly<Record<string, MolecularNodesBufferLayout>>;
        readonly kernels: readonly MolecularNodesKernel[];
    };
    readonly programs: readonly MolecularNodesCompiledProgram[];
    readonly libraries: readonly MolecularNodesCompiledLibrary[];
    readonly groups: readonly MolecularNodesCompiledGraph[];
    readonly materials: readonly MolecularNodesCompiledGraph[];
    readonly diagnostics: {
        readonly dependencyCycles: readonly (readonly string[])[];
    };
    readonly stats: Readonly<Record<string, number>>;
}

/** A product-specific view over the full R4 artifact; arrays reference the full bundle without copying graph data. */
export interface MolecularNodesCompiledPack {
    readonly schema: MolecularNodesCompiledBundle['schema'];
    readonly sourceArtifact: MolecularNodesCompiledBundle['artifact'];
    readonly runtime: MolecularNodesCompiledBundle['runtime'];
    readonly programs: readonly MolecularNodesCompiledProgram[];
    readonly groups: readonly MolecularNodesCompiledGraph[];
    readonly materials: readonly MolecularNodesCompiledGraph[];
}
