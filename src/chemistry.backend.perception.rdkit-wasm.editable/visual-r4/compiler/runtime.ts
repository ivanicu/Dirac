import { MolecularNodesCompiledManifest } from '../compiled/mn-r4.manifest';
import type {
    MolecularNodesCompiledBundle,
    MolecularNodesCompiledGraph,
    MolecularNodesCompiledPack,
    MolecularNodesCompiledProgram,
} from './types';

export { MolecularNodesCompiledManifest };

export interface LoadMolecularNodesBundleOptions {
    readonly signal?: AbortSignal;
    readonly verifyIntegrity?: boolean;
}

export interface SelectMolecularNodesPackOptions {
    /** Material names to include. Omit to expose every compiled Molecular Nodes material. */
    readonly materials?: readonly string[];
}

function canonicalStringify(value: unknown): string {
    if (Array.isArray(value)) return `[${value.map(canonicalStringify).join(',')}]`;
    if (value !== null && typeof value === 'object') {
        const object = value as Record<string, unknown>;
        return `{${Object.keys(object).sort().map(key => `${JSON.stringify(key)}:${canonicalStringify(object[key])}`).join(',')}}`;
    }
    return JSON.stringify(value);
}

async function sha256(value: string): Promise<string> {
    if (!globalThis.crypto?.subtle) throw new Error('Web Crypto is required to verify the Molecular Nodes bundle');
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

function validateBundleShape(value: unknown): asserts value is MolecularNodesCompiledBundle {
    if (value === null || typeof value !== 'object') throw new TypeError('compiled Molecular Nodes bundle must be an object');
    const bundle = value as Partial<MolecularNodesCompiledBundle>;
    if (bundle.schema !== MolecularNodesCompiledManifest.schema) throw new Error(`unsupported Molecular Nodes bundle schema: ${String(bundle.schema)}`);
    if (!Array.isArray(bundle.programs) || !Array.isArray(bundle.groups) || !Array.isArray(bundle.materials)) {
        throw new TypeError('compiled Molecular Nodes bundle is missing graph arrays');
    }
    if (bundle.artifact?.algorithm !== 'sha256' || typeof bundle.artifact.hash !== 'string') {
        throw new TypeError('compiled Molecular Nodes bundle is missing its integrity record');
    }
    if (!bundle.runtime || !Array.isArray(bundle.runtime.kernels) || !bundle.compiler || !bundle.diagnostics) {
        throw new TypeError('compiled Molecular Nodes bundle is missing compiler/runtime metadata');
    }
    const groups = [...bundle.groups, ...bundle.materials];
    const groupIds = new Set<string>();
    const groupNames = new Set(bundle.groups.map(group => group.name));
    for (const graph of groups) {
        if (groupIds.has(graph.id)) throw new Error(`duplicate compiled graph id: ${graph.id}`);
        groupIds.add(graph.id);
        const nodes = new Map<string, MolecularNodesCompiledGraph['nodes'][number]>();
        for (const node of graph.nodes) {
            if (nodes.has(node.id)) throw new Error(`duplicate node id in ${graph.name}: ${node.id}`);
            nodes.set(node.id, node);
            if (node.call && !groupNames.has(node.call)) throw new Error(`compiled node ${graph.name}/${node.name} calls missing group ${node.call}`);
        }
        for (const dependency of graph.dependencies) {
            if (!groupNames.has(dependency)) throw new Error(`compiled graph ${graph.name} references missing dependency ${dependency}`);
        }
        for (const link of graph.links) {
            const source = nodes.get(link.from);
            const target = nodes.get(link.to);
            if (!source?.outputs.some(socket => socket.id === link.out)) throw new Error(`dangling output link in ${graph.name}: ${link.from}.${link.out}`);
            if (!target?.inputs.some(socket => socket.id === link.in)) throw new Error(`dangling input link in ${graph.name}: ${link.to}.${link.in}`);
        }
    }
    for (const program of bundle.programs) {
        if (!groupIds.has(program.entry)) throw new Error(`compiled program ${program.name} references missing entry ${program.entry}`);
    }
    for (const kernel of bundle.runtime.kernels) {
        if (!/^[0-9a-f]{64}$/.test(kernel.hash)) throw new Error(`compiled kernel ${kernel.id} has no valid content hash`);
    }
}

/** Resolve the generated bundle without coupling the Mol* JavaScript chunk to its 1+ MB graph payload. */
export function resolveMolecularNodesBundleUrl(assetBaseUrl: string | URL): URL {
    return new URL(MolecularNodesCompiledManifest.bundleFile, assetBaseUrl);
}

export async function loadMolecularNodesBundle(
    url: string | URL,
    options: LoadMolecularNodesBundleOptions = {},
): Promise<MolecularNodesCompiledBundle> {
    const response = await fetch(url, { signal: options.signal });
    if (!response.ok) throw new Error(`failed to load Molecular Nodes bundle: ${response.status} ${response.statusText}`);
    const value: unknown = await response.json();
    validateBundleShape(value);
    if (options.verifyIntegrity !== false) {
        const payload = { ...value } as Record<string, unknown>;
        delete payload.artifact;
        const actual = await sha256(canonicalStringify(payload));
        const trusted = MolecularNodesCompiledManifest.artifact.hash;
        if (value.artifact.hash !== trusted || actual !== trusted) {
            throw new Error(`Molecular Nodes bundle integrity mismatch: expected ${trusted}, declared ${value.artifact.hash}, got ${actual}`);
        }
    }
    return value;
}

/** Load a WGSL source only after both its manifest entry and its actual bytes pass SHA-256 verification. */
export async function loadMolecularNodesKernel(
    bundle: MolecularNodesCompiledBundle,
    bundleUrl: string | URL,
    kernelId: string,
    options: Pick<LoadMolecularNodesBundleOptions, 'signal'> = {},
): Promise<string> {
    const kernel = bundle.runtime.kernels.find(candidate => candidate.id === kernelId);
    if (!kernel) throw new Error(`unknown Molecular Nodes kernel: ${kernelId}`);
    const response = await fetch(new URL(kernel.source, bundleUrl), { signal: options.signal });
    if (!response.ok) throw new Error(`failed to load Molecular Nodes kernel ${kernelId}: ${response.status} ${response.statusText}`);
    const source = await response.text();
    const actual = await sha256(source);
    if (actual !== kernel.hash) throw new Error(`Molecular Nodes kernel ${kernelId} integrity mismatch: expected ${kernel.hash}, got ${actual}`);
    return source;
}

function findPrograms(bundle: MolecularNodesCompiledBundle, requested: readonly string[]): MolecularNodesCompiledProgram[] {
    const byName = new Map(bundle.programs.flatMap(program => [[program.name, program], [program.kind, program], [program.id, program]]));
    return requested.map(name => {
        const program = byName.get(name);
        if (!program) throw new Error(`unknown Molecular Nodes program: ${name}`);
        return program;
    });
}

function dependencyClosure(
    bundle: MolecularNodesCompiledBundle,
    programs: readonly MolecularNodesCompiledProgram[],
    materials: readonly MolecularNodesCompiledGraph[],
): MolecularNodesCompiledGraph[] {
    const byId = new Map(bundle.groups.map(group => [group.id, group]));
    const byName = new Map(bundle.groups.map(group => [group.name, group]));
    const selected = new Set<string>();
    const visit = (group: MolecularNodesCompiledGraph) => {
        if (selected.has(group.id)) return;
        selected.add(group.id);
        for (const name of group.dependencies) {
            const dependency = byName.get(name);
            if (!dependency) throw new Error(`compiled group ${group.name} references missing dependency ${name}`);
            visit(dependency);
        }
    };
    for (const program of programs) {
        const entry = byId.get(program.entry);
        if (!entry) throw new Error(`compiled program ${program.name} references missing entry ${program.entry}`);
        visit(entry);
    }
    for (const material of materials) {
        for (const name of material.dependencies) {
            const dependency = byName.get(name);
            if (!dependency) throw new Error(`compiled material ${material.name} references missing dependency ${name}`);
            visit(dependency);
        }
    }
    return bundle.groups.filter(group => selected.has(group.id));
}

/** Select one or more complete program closures for a product without losing atom/residue semantic data. */
export function selectMolecularNodesPack(
    bundle: MolecularNodesCompiledBundle,
    requestedPrograms: readonly string[],
    options: SelectMolecularNodesPackOptions = {},
): MolecularNodesCompiledPack {
    if (requestedPrograms.length === 0) throw new Error('at least one Molecular Nodes program is required');
    const programs = findPrograms(bundle, requestedPrograms);
    const requestedMaterials = options.materials ? new Set(options.materials) : undefined;
    const materials = requestedMaterials
        ? bundle.materials.filter(material => requestedMaterials.has(material.name))
        : bundle.materials;
    if (requestedMaterials && materials.length !== requestedMaterials.size) {
        const found = new Set(materials.map(material => material.name));
        const missing = [...requestedMaterials].filter(name => !found.has(name));
        throw new Error(`unknown Molecular Nodes materials: ${missing.join(', ')}`);
    }
    return {
        schema: bundle.schema,
        sourceArtifact: bundle.artifact,
        runtime: bundle.runtime,
        programs,
        groups: dependencyClosure(bundle, programs, materials),
        materials,
    };
}
