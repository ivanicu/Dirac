declare module '*.glb' {
    const url: string;
    export default url;
}

declare module '*.cif' {
    const url: string;
    export default url;
}

declare module '*.pdb' {
    const url: string;
    export default url;
}

declare module '*.wasm' {
    const url: string;
    export default url;
}

interface Window {
    initRDKitModule?: (config?: { locateFile?: (file: string) => string }) => Promise<unknown>;
}

interface RDKitModule {
    get_mol(molblock_or_smiles: string): { get_molblock(): string; delete(): void } | null;
    get_mol(molblock_or_smiles: string, details: string): { get_molblock(): string; delete(): void } | null;
}
