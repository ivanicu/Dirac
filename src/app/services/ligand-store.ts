/**
 * LigandStore — THE authority on which molecule the app is looking at.
 *
 * WHY THIS EXISTS (the one structural fix all five architecture reviewers
 * endorsed): the focused molecule currently lives in three-to-four parallel
 * fields inside the lab class — `smilesMolfile`, `smartsSearchMolfile`, a
 * chemistry cache, plus a copy inside the field-wells facet. Each consumer
 * reads whichever one its author knew about. Measured consequences, all real:
 *
 *   · six fields rendered nothing while the 2D panel looked fine, because one
 *     builder's output was malformed and only the strictest consumer noticed;
 *   · a completed SCF rendered into the WRONG molecule's scene, because the
 *     response of a request issued for molecule A arrived after the user had
 *     moved to molecule B;
 *   · the SMILES path deliberately never loaded into mol* — so the only thing
 *     preventing a FLAT (2D) molecule from reaching the cube pipeline was that
 *     one code path happened to return early.
 *
 * Three properties, and each kills one of those:
 *
 *   1. ONE HOME. Two write entrances (`setFromLoci`, `setFromImport`), one
 *      read (`current`). A consumer cannot pick the wrong copy because there
 *      is no other copy.
 *   2. A GENERATION TOKEN. Every write increments it. An async consumer
 *      captures it before the request and discards the response if it moved.
 *      Stale-async is solved once, here, instead of five times in five facets
 *      with five subtly different guards.
 *   3. coordSpace IS PART OF THE TYPE. A `'2d'` ligand is representable, so
 *      consumers that need real geometry must narrow — the compiler asks the
 *      question, rather than a comment asking the reader.
 *
 * NOT WIRED YET, deliberately: index.ts is being edited by other sessions
 * right now, and this file is the seam it will import. It is standalone,
 * typechecked, and tested; adoption is a separate commit that deletes the
 * three fields.
 */

/** Where the coordinates came from, and therefore what they may be used for. */
export type CoordSpace = '2d' | 'scene';

export interface LigandCommon {
    /** V2000 molblock. The single source consumers parse. */
    readonly molfile: string;
    /** Human label for panels: 'REA · A:200', 'Imported · aspirin'. */
    readonly label: string;
    /** Heavy-atom count, read from the counts line — used by prefetch policy. */
    readonly heavyAtoms: number;
    /** Parent InChIKey when known; '' when it has not been computed yet. */
    readonly inchikey: string;
    readonly coordSpace: CoordSpace;
}

/** A ligand selected inside a loaded structure: carries the scene identity a
 *  3D consumer needs (which copy of the ligand, and the pocket radius). */
export interface LociLigand extends LigandCommon {
    readonly kind: 'loci';
    readonly coordSpace: 'scene';
    /** mol* Structure. Typed `unknown` on purpose: this service must not
     *  depend on the viewer, or it becomes unusable in a worker or a test. */
    readonly structureRef: unknown;
    /** mol* StructureElement.Bundle identifying the ligand instance. */
    readonly bundleRef: unknown;
    /** Residue-shell radius the focus was computed with. */
    readonly cutoffA: number;
}

/** A molecule embedded in 3D by the backend (ETKDG + MMFF). */
export interface ImportedLigand extends LigandCommon {
    readonly kind: 'import';
    readonly coordSpace: 'scene';
    /** ETKDG seed — same (smiles, seed) reproduces the same bytes, which is
     *  what makes the field cache hit across sessions. */
    readonly seed: number;
}

/** A molecule with 2D coordinates only (RDKit depiction coords). Legal to
 *  depict and to describe; ILLEGAL to send through a 3D field pipeline. */
export interface Sketch2dLigand extends LigandCommon {
    readonly kind: 'sketch2d';
    readonly coordSpace: '2d';
}

export type Ligand = LociLigand | ImportedLigand | Sketch2dLigand;

export type LigandListener = (ligand: Ligand | null, generation: number) => void;
export type Unsubscribe = () => void;

/** Thrown when a 3D-only consumer is handed 2D coordinates. Named so a panel
 *  can render the actor and the remedy rather than "an error occurred". */
export class CoordSpaceError extends Error {
    constructor(readonly got: CoordSpace, readonly consumer: string) {
        super(`${consumer} needs scene coordinates; this molecule has ${got} ` +
              `coordinates only. Import it (SMILES → 3D) to get a real geometry.`);
        this.name = 'CoordSpaceError';
    }
}

/**
 * Narrowing guard for every 3D consumer (fields, pharmacophore, interactions).
 * Throws rather than returning false: a caller that forgets to branch gets a
 * loud, named failure instead of a confidently wrong, perfectly aligned field.
 */
export function requireScene(ligand: Ligand, consumer: string): LociLigand | ImportedLigand {
    if (ligand.coordSpace !== 'scene') throw new CoordSpaceError(ligand.coordSpace, consumer);
    return ligand as LociLigand | ImportedLigand;
}

/** Atom count from a V2000 counts line (columns 0-3). 0 when unreadable. */
export function heavyAtomsFromMolfile(molfile: string): number {
    const counts = molfile.split('\n')[3] ?? '';
    const n = parseInt(counts.slice(0, 3).trim(), 10);
    return Number.isFinite(n) && n > 0 ? n : 0;
}

export class LigandStore {
    private ligand: Ligand | null = null;
    private gen = 0;
    private listeners = new Set<LigandListener>();
    /** Where a subscriber's exception goes. Injectable so a test can assert
     *  isolation without polluting the console. */
    private readonly onListenerError: (e: unknown) => void;

    constructor(onListenerError?: (e: unknown) => void) {
        this.onListenerError = onListenerError ?? (e => console.error('[ligand-store] subscriber threw', e));
    }

    current(): Ligand | null { return this.ligand; }
    generation(): number { return this.gen; }

    /**
     * Subscribe and receive the CURRENT value immediately. Late-mounting
     * consumers are the normal case (a facet mounted after a molecule loaded),
     * and a store that only emits future changes leaves them blank until the
     * user happens to act — the bug class this replay closes.
     */
    subscribe(fn: LigandListener): Unsubscribe {
        this.listeners.add(fn);
        this.emitTo(fn);
        return () => { this.listeners.delete(fn); };
    }

    /** Entrance 1: a ligand selected inside a loaded structure. */
    setFromLoci(args: {
        molfile: string; label: string; inchikey?: string;
        structureRef: unknown; bundleRef: unknown; cutoffA: number;
    }): void {
        this.commit({
            kind: 'loci', coordSpace: 'scene',
            molfile: args.molfile, label: args.label,
            inchikey: args.inchikey ?? '',
            heavyAtoms: heavyAtomsFromMolfile(args.molfile),
            structureRef: args.structureRef, bundleRef: args.bundleRef,
            cutoffA: args.cutoffA,
        });
    }

    /** Entrance 2: a molecule embedded in 3D by the backend. */
    setFromImport(args: {
        molfile: string; label: string; inchikey: string; seed: number;
    }): void {
        this.commit({
            kind: 'import', coordSpace: 'scene',
            molfile: args.molfile, label: args.label, inchikey: args.inchikey,
            heavyAtoms: heavyAtomsFromMolfile(args.molfile), seed: args.seed,
        });
    }

    /**
     * Entrance 3: 2D-only. Separate from the other two BECAUSE it is not
     * interchangeable with them — it exists so the depiction path has a home
     * that the field path structurally cannot consume by accident.
     */
    setFromSketch2d(args: { molfile: string; label: string; inchikey?: string }): void {
        this.commit({
            kind: 'sketch2d', coordSpace: '2d',
            molfile: args.molfile, label: args.label,
            inchikey: args.inchikey ?? '',
            heavyAtoms: heavyAtomsFromMolfile(args.molfile),
        });
    }

    /** No molecule in focus (structure without a ligand, or cleared). */
    clear(): void {
        if (this.ligand === null) return;   // idempotent: no spurious generation bump
        this.ligand = null;
        this.gen++;
        this.emitAll();
    }

    /**
     * True while `generation` is still the current one. The contract for every
     * async consumer:
     *
     *     const g = store.generation();
     *     const result = await slowThing();
     *     if (!store.isCurrent(g)) return;   // discard, do not render
     */
    isCurrent(generation: number): boolean { return generation === this.gen; }

    private commit(next: Ligand): void {
        // Identical molfile AND kind is not a change: re-emitting would clear
        // every consumer's cache for nothing (the prefetch cascade is the
        // expensive one — it can start five SCFs).
        if (this.ligand && this.ligand.molfile === next.molfile && this.ligand.kind === next.kind) {
            return;
        }
        this.ligand = next;
        this.gen++;
        this.emitAll();
    }

    private emitAll(): void {
        // Snapshot: a listener may unsubscribe (or subscribe) during emit.
        for (const fn of [...this.listeners]) this.emitTo(fn);
    }

    private emitTo(fn: LigandListener): void {
        // One subscriber's exception must not stop the others. A facet that
        // throws in onLigand used to abort the whole cascade, so the facets
        // that came after it silently kept the previous molecule's state.
        try { fn(this.ligand, this.gen); } catch (e) { this.onListenerError(e); }
    }
}

/** The app-wide instance. One home, module-scoped so a facet cannot make a second. */
export const ligandStore = new LigandStore();
