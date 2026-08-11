/**
 * ChemistryCache — the single RDKit computation pass per molecule switch.
 *
 * S0 item 2+3: LigandStore generation semantics + ChemistryCache.
 *
 * Before: 10× RDKit.get_mol + 4× computeLigandChemistry + 8N redundant
 * SMARTS matches per molecule switch (measured: ~170 WASM calls).
 *
 * After: 1× RDKit.get_mol for chemistry + 1× for descriptors + 1× for
 * identifiers + 1× for depiction = 4 total. Chemistry results cached and
 * read by all consumers.
 *
 * Generation token: every update increments generation. Async consumers
 * that started with an old generation discard their results ("stale field
 * discarded" — same rule as SPEC.md T2 cube-cache).
 */

import { computeLigandChemistry, type LigandChemistry } from './semantic-chemistry-rdkit';
import { computeLigandDescriptors, type DescriptorReport } from './descriptors';
import { computeLigandIdentifiers } from './semantic-chemistry-rdkit';

export interface ChemistryCacheData {
    readonly generation: number;
    readonly molfile: string;
    readonly atomCount: number;
    readonly chemistry: LigandChemistry | null;
    readonly descriptors: DescriptorReport | null;
    readonly identifiers: Awaited<ReturnType<typeof computeLigandIdentifiers>>;
}

export class ChemistryCache {
    private data: ChemistryCacheData | null = null;
    private generation = 0;


    /** Current cached data, or null if nothing computed yet. */
    current(): ChemistryCacheData | null {
        return this.data;
    }

    /** Generation token — increments on every update call. */
    currentGeneration(): number {
        return this.generation;
    }

    /**
     * Compute ALL RDKit results for a molfile. Called ONCE per molecule switch.
     * If a second update arrives while the first is still computing, the first
     * result is discarded (stale — generation mismatch).
     *
     * Returns the generation at which this computation started. Callers can
     * check `cache.currentGeneration() === startGen` to verify their result
     * is still current.
     */
    async update(molfile: string, atomCount: number): Promise<number> {
        this.generation++;
        const startGen = this.generation;


        // Run ALL independent RDKit computations in parallel.
        const [chemistry, descriptors, identifiers] = await Promise.all([
            computeLigandChemistry(molfile, atomCount),
            computeLigandDescriptors(molfile),
            computeLigandIdentifiers(molfile),
        ]);

        // Stale check: if generation changed during computation, discard.
        if (this.generation !== startGen) {
            return startGen; // caller checks and discards
        }

        this.data = Object.freeze({
            generation: startGen,
            molfile,
            atomCount,
            chemistry,
            descriptors,
            identifiers,
        });
        
        return startGen;
    }

    /** Convenience: is there cached data matching the given generation? */
    isValid(generation: number): boolean {
        return this.data !== null && this.data.generation === generation;
    }

    /** Clear the cache (no active ligand). */
    clear(): void {
        this.data = null;
        this.generation++;
    }

    /** Convenience: get chemistry or throw. */
    getChemistry(): LigandChemistry | null {
        return this.data?.chemistry ?? null;
    }

    /** Convenience: get descriptors or null. */
    getDescriptors(): DescriptorReport | null {
        return this.data?.descriptors ?? null;
    }

    /** Convenience: get molfile or null. */
    getMolfile(): string | null {
        return this.data?.molfile ?? null;
    }

    /** Convenience: get identifiers or null. */
    getIdentifiers() {
        return this.data?.identifiers ?? null;
    }
}
