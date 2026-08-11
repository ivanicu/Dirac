/**
 * Pharmacophore Designer — live SMARTS screening engine.
 *
 * Screens the shipped library against the current pharmacophore model's
 * feature-count requirements. Counts come from the SAME substrate call the
 * ligand perception uses (`computeLigandChemistry`), so "3 acceptors" means
 * the identical SMARTS on both sides of the comparison. The only facet-local
 * SMARTS is the hydrophobic-carbon pattern, which is the topological
 * equivalent of the 3D layer's "carbon with no N/O neighbor" rule.
 *
 * This is TOPOLOGICAL screening: a molecule matches when it has at least the
 * required number of each enabled feature kind (plus an optional custom
 * SMARTS substructure). Feature positions and tolerance radii describe the
 * 3D model and its export — the library ships no conformers, so no 3D
 * alignment is performed or implied.
 */

import { computeLigandChemistry, getRDKit, type LigandChemistry } from '../../../chemistry.backend.perception.rdkit-wasm.editable/semantic-chemistry-rdkit';
import type { DesignerFeatureKind } from './model';
import { ScreeningLibrary, type LibraryEntry } from './library';

/**
 * Topological form of pharmacophore-features.ts's hydrophobic rule
 * (carbon whose bonded neighbors include no N and no O).
 */
export const HydrophobicCarbonSmarts = '[#6;!$([#6]~[#7,#8])]';

export interface ScreenedEntry {
    readonly entry: LibraryEntry;
    readonly valid: boolean;
    readonly molblock: string | null;
    readonly atomCount: number;
    readonly chemistry: LigandChemistry | null;
    readonly hydrophobicFlags: Uint8Array | null;
    readonly counts: Record<DesignerFeatureKind, number> | null;
}

export interface ScreeningQuery {
    readonly required: Record<DesignerFeatureKind, number>;
    /** Optional extra substructure constraint. */
    readonly smarts: string | null;
}

export interface ScreeningVerdict {
    readonly entry: LibraryEntry;
    readonly counts: Record<DesignerFeatureKind, number>;
    readonly satisfied: Record<DesignerFeatureKind, boolean>;
    /** null when the query has no custom SMARTS. */
    readonly smartsMatched: boolean | null;
    readonly matches: boolean;
    readonly satisfiedKinds: number;
    readonly requiredKinds: number;
}

export interface ScreeningResult {
    readonly verdicts: ScreeningVerdict[];
    readonly matchCount: number;
    readonly screenedCount: number;
    readonly invalidCount: number;
    /** Set when the custom SMARTS failed to parse; verdicts then ignore it. */
    readonly smartsError: string | null;
}

interface JSMolLike {
    is_valid(): boolean;
    get_molblock(): string;
    get_substruct_matches(q: JSMolLike): string;
    delete(): void;
}

function countSetBits(flags: Uint8Array): number {
    let n = 0;
    for (let i = 0; i < flags.length; i++) if (flags[i]) n++;
    return n;
}

function parseMolblockAtomCount(molblock: string): number {
    const lines = molblock.split('\n');
    if (lines.length < 4) return 0;
    const n = parseInt(lines[3].slice(0, 3), 10);
    return Number.isFinite(n) ? n : 0;
}

export class ScreeningEngine {
    private entries: ScreenedEntry[] | null = null;
    private initPromise: Promise<ScreenedEntry[]> | null = null;
    /** JSMol cache, built lazily on first custom-SMARTS screen. */
    private molCache = new Map<string, JSMolLike>();

    /**
     * Parse + perceive the whole library once. Idempotent; concurrent callers
     * share one in-flight promise.
     */
    init(): Promise<ScreenedEntry[]> {
        if (this.entries) return Promise.resolve(this.entries);
        if (!this.initPromise) {
            this.initPromise = this.buildEntries().then(entries => {
                this.entries = entries;
                return entries;
            });
        }
        return this.initPromise;
    }

    private async buildEntries(): Promise<ScreenedEntry[]> {
        const RDKit = await getRDKit();
        const out: ScreenedEntry[] = [];
        for (const entry of ScreeningLibrary) {
            let mol: JSMolLike | null = null;
            try {
                mol = RDKit.get_mol(entry.smiles) as unknown as JSMolLike | null;
                if (!mol || !mol.is_valid()) {
                    out.push({ entry, valid: false, molblock: null, atomCount: 0, chemistry: null, hydrophobicFlags: null, counts: null });
                    continue;
                }
                const molblock = mol.get_molblock();
                const atomCount = parseMolblockAtomCount(molblock);
                const chemistry = await computeLigandChemistry(molblock, atomCount);
                if (!chemistry) {
                    out.push({ entry, valid: false, molblock, atomCount, chemistry: null, hydrophobicFlags: null, counts: null });
                    continue;
                }
                const hydrophobicFlags = this.matchAtomFlags(RDKit, mol, HydrophobicCarbonSmarts, atomCount);
                out.push({
                    entry,
                    valid: true,
                    molblock,
                    atomCount,
                    chemistry,
                    hydrophobicFlags,
                    counts: {
                        hba: countSetBits(chemistry.acceptors),
                        hbd: countSetBits(chemistry.donors),
                        aromatic: chemistry.aromaticRings.length,
                        hydrophobic: countSetBits(hydrophobicFlags),
                    },
                });
            } finally {
                if (mol) mol.delete();
            }
        }
        return out;
    }

    private matchAtomFlags(RDKit: { get_qmol(s: string): unknown }, mol: JSMolLike, smarts: string, atomCount: number): Uint8Array {
        const flags = new Uint8Array(atomCount);
        const qmol = RDKit.get_qmol(smarts) as JSMolLike | null;
        if (!qmol) return flags;
        try {
            const parsed = JSON.parse(mol.get_substruct_matches(qmol)) as unknown;
            if (Array.isArray(parsed)) {
                for (const match of parsed as Array<{ atoms?: number[] }>) {
                    for (const idx of match.atoms ?? []) {
                        if (idx >= 0 && idx < atomCount) flags[idx] = 1;
                    }
                }
            }
        } catch { /* leave zero flags */ }
        finally { qmol.delete(); }
        return flags;
    }

    async screen(query: ScreeningQuery): Promise<ScreeningResult> {
        const entries = await this.init();
        const requiredKinds = (Object.keys(query.required) as DesignerFeatureKind[]).filter(k => query.required[k] > 0);

        let smartsError: string | null = null;
        let smartsHits: Map<string, boolean> | null = null;
        if (query.smarts && query.smarts.trim().length > 0) {
            const result = await this.matchCustomSmarts(query.smarts.trim(), entries);
            if (typeof result === 'string') smartsError = result;
            else smartsHits = result;
        }

        const verdicts: ScreeningVerdict[] = [];
        let matchCount = 0;
        let invalidCount = 0;
        for (const e of entries) {
            if (!e.valid || !e.counts) {
                invalidCount++;
                continue;
            }
            const satisfied = {} as Record<DesignerFeatureKind, boolean>;
            let satisfiedKinds = 0;
            for (const kind of ['hba', 'hbd', 'aromatic', 'hydrophobic'] as DesignerFeatureKind[]) {
                const ok = e.counts[kind] >= query.required[kind];
                satisfied[kind] = ok;
                if (ok && query.required[kind] > 0) satisfiedKinds++;
            }
            const smartsMatched = smartsHits ? (smartsHits.get(e.entry.id) ?? false) : null;
            const matches = requiredKinds.every(k => satisfied[k]) && (smartsMatched !== false);
            if (matches) matchCount++;
            verdicts.push({
                entry: e.entry,
                counts: e.counts,
                satisfied,
                smartsMatched,
                matches,
                satisfiedKinds,
                requiredKinds: requiredKinds.length,
            });
        }

        verdicts.sort((a, b) => {
            if (a.matches !== b.matches) return a.matches ? -1 : 1;
            if (a.satisfiedKinds !== b.satisfiedKinds) return b.satisfiedKinds - a.satisfiedKinds;
            return a.entry.name.localeCompare(b.entry.name);
        });

        return { verdicts, matchCount, screenedCount: verdicts.length, invalidCount, smartsError };
    }

    /** Returns a hit map, or an error string when the SMARTS does not parse. */
    private async matchCustomSmarts(smarts: string, entries: ScreenedEntry[]): Promise<Map<string, boolean> | string> {
        const RDKit = await getRDKit() as unknown as { get_qmol(s: string): JSMolLike | null; get_mol(s: string): JSMolLike | null };
        const qmol = RDKit.get_qmol(smarts);
        if (!qmol) return `SMARTS does not parse: ${smarts}`;
        try {
            const hits = new Map<string, boolean>();
            for (const e of entries) {
                if (!e.valid || !e.molblock) continue;
                let mol = this.molCache.get(e.entry.id);
                if (!mol) {
                    const parsed = RDKit.get_mol(e.molblock);
                    if (!parsed || !parsed.is_valid()) continue;
                    mol = parsed;
                    this.molCache.set(e.entry.id, mol);
                }
                let matched = false;
                try {
                    const raw = JSON.parse(mol.get_substruct_matches(qmol)) as unknown;
                    matched = Array.isArray(raw) && raw.length > 0;
                } catch { /* count as no match */ }
                hits.set(e.entry.id, matched);
            }
            return hits;
        } finally {
            qmol.delete();
        }
    }

    getEntry(id: string): ScreenedEntry | undefined {
        return this.entries?.find(e => e.entry.id === id);
    }

    dispose(): void {
        for (const mol of this.molCache.values()) mol.delete();
        this.molCache.clear();
    }
}
