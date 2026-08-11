// Dirac frontend — THE interface stub (Figure-2, TypeScript half).
// L4 services and the facet seam. Mirrors contracts/iface.pyi for wire types.

export type FieldKind = 'mep' | 'mep_qm' | 'homo' | 'lumo' | 'density' | 'mlp';
export type Basis = 'sto-3g' | '6-31g' | '6-31g*' | 'def2-svp';
export type CacheSource = 'browser' | 'memory' | 'db' | 'computed';

/** Discriminated union — a molfile alone is lossy (frontend review, blocker 3). */
export type Ligand =
    | { kind: 'loci'; molfile: string; coordSpace: 'scene'; label: string;
        inchikey: string; heavyAtoms: number;
        structureRef: unknown; bundleRef: unknown; cutoffA: number }
    | { kind: 'import'; molfile: string; coordSpace: 'scene'; label: string;
        inchikey: string; heavyAtoms: number; seed: number }
    | { kind: 'sketch2d'; molfile: string; coordSpace: '2d'; label: string;
        inchikey: string; heavyAtoms: number };
// coordSpace '2d' MUST be refused by any consumer that renders 3D physics:
// a planar molecule through the cube pipeline yields a confidently wrong,
// perfectly aligned-looking field.

export interface LigandStore {
    /** Fires immediately with current value; isolates subscriber throws;
     *  generation token solves stale-async ONCE for all consumers. */
    subscribe(cb: (l: Ligand | null, generation: number) => void): () => void;
    setFromImport(molfile: string, meta: { inchikey: string; label: string; seed: number }): void;
    setFromLoci(structureRef: unknown, bundleRef: unknown, cutoffA: number): Promise<void>;
    current(): Ligand | null;
    generation(): number;
}

export interface CubeEntry { cube: string; meta: Record<string, unknown>; source: CacheSource }

export interface FieldCache {
    get(kind: FieldKind, basis: Basis): CubeEntry | undefined;
    /** Dedup in-flight; discard results whose generation != current. */
    fetch(kind: FieldKind, basis: Basis): Promise<CubeEntry | null>;
    /** Classical always; quantum iff heavyAtoms <= 40 (a background prefetch
     *  may never start a six-minute Fe-heme SCF). */
    prefetch(): void;
    clear(): void; // on ligand change AND on producer version change (/health)
}

export interface BackendClient {
    embed(req: { smiles?: string; molfile?: string; seed?: number }): Promise<Envelope>;
    field(req: { molfile: string; kind: FieldKind; basis?: Basis;
                 spin?: number; max_seconds?: number }): Promise<Envelope>;
    health(): Promise<{ ok: boolean; rdkit: string; pyscf: string;
                        db_cache: 'on' | 'off'; scf_cached: number; rss_mb: number }>;
}

export type Envelope =
    | { ok: true; cube?: string; molfile?: string; meta: Record<string, unknown> }
    | { ok: false; error: string };
// v1 wire shape (flat, live today). v2 ({data, meta:{envelope:1, request_id,
// producer}}) arrives with the app.job seam; both accepted one version.

export interface ThemeService {
    /** data-theme attribute + ONE scene.setBackground(--scene-bg). Polling
     *  repaint loops are forbidden (two shipped themes violated this). */
    setTheme(name: 'night' | 'chamber' | string): void;
}

/** The facet seam (target shape; current integration = 3 named functions). */
export interface FacetCtx {
    plugin: unknown;            // mol* PluginContext — first-class, not hidden
    ligand: LigandStore;
    fields: FieldCache;
    backend: BackendClient;
    theme: ThemeService;
}
export interface Facet {
    id: string;                 // also namespaces mol* state refs: `${id}:${name}`
    tab: { label: string; order: number };
    mount(ctx: FacetCtx, slot: HTMLElement): void;   // once, at boot
    onLigand(ctx: FacetCtx, l: Ligand | null, generation: number): void;
    onShow?(): void;
    onHide?(): void;
    dispose(): void;            // teardown/HMR only — facets are factories
}
