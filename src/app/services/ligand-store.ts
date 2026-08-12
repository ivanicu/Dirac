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
 * The application singleton is wired to ScientificContextStore, so all facets now
 * share this one generation clock. Tests may inject an isolated context explicitly.
 */

import { ScientificContextStore, scientificContext } from '../context/scientific-context-store';
import { objectRef } from '../domain/object-ref';

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
/**
 * HEAVY atoms — the number the "is a quantum field affordable" gate depends on.
 *
 * The body above this one took columns 0-3 of line 4, which is the TOTAL atom
 * count and includes hydrogens, so the gate was wrong by roughly 2x on anything
 * with explicit H. It also returned 0 for V3000, whose counts line reads
 * "0 0 0 0 0 999 V3000" — and ZERO HEAVY ATOMS PASSES EVERY AFFORDABILITY CHECK
 * THERE IS, so the one format that most needs the gate was the format that
 * disabled it. Both traps were found and fixed in facets/field-wells eleven
 * hours before this file was written, and this file shipped the old body anyway.
 *
 * That is why the logic lives HERE now, in the services layer: a facet holding
 * the canonical copy of a shared fact means the next module to need it writes a
 * third one. facets/field-wells can import this and delete its private copy —
 * one line, and then there is one home.
 */
export function heavyAtomsFromMolfile(molfile: string): number {
    const lines = molfile.split('\n');
    const counts = lines[3] ?? '';
    if (counts.includes('V3000')) {
        // The element symbol is the 4th whitespace-separated field of an ATOM line.
        return lines.filter(l => /^M {2}V30 \d+ [A-Z]/.test(l))
            .filter(l => (l.trim().split(/\s+/)[3] ?? '') !== 'H').length;
    }
    const total = parseInt(counts.slice(0, 3), 10);
    if (!Number.isFinite(total) || total <= 0) return 0;
    let heavy = 0;
    for (let i = 4; i < 4 + total && i < lines.length; i++) {
        // V2000 atom line: x, y, z in three 10-column fields, then the symbol.
        const sym = (lines[i] ?? '').slice(31, 34).trim();
        if (sym && sym !== 'H' && sym !== 'D') heavy++;
    }
    // `heavy || total` deliberately: a molfile whose atom block we could not
    // parse must not read as "0 heavy atoms, therefore free".
    return heavy || total;
}

export class LigandStore {
    private ligand: Ligand | null = null;
    private listeners = new Set<LigandListener>();
    /** Where a subscriber's exception goes. Injectable so a test can assert
     *  isolation without polluting the console. */
    private readonly onListenerError: (e: unknown) => void;

    constructor(onListenerError?: (e: unknown) => void,
                private readonly context = new ScientificContextStore()) {
        this.onListenerError = onListenerError ?? (e => console.error('[ligand-store] subscriber threw', e));
    }

    current(): Ligand | null { return this.ligand; }
    generation(): number { return this.context.generation(); }

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
        this.context.clearFocus('selection');
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
    isCurrent(generation: number): boolean { return this.context.isCurrent(generation); }

    private commit(next: Ligand): void {
        // Identical molfile AND kind is not a change: re-emitting would clear
        // every consumer's cache for nothing (the prefetch cascade is the
        // expensive one — it can start five SCFs).
        if (this.ligand && this.ligand.molfile === next.molfile && this.ligand.kind === next.kind) {
            return;
        }
        this.ligand = next;
        const identity = next.inchikey || stableMoleculeId(next.molfile);
        this.context.focus(objectRef('molecule', identity),
                           next.kind === 'import' ? 'import' : 'selection');
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
        try { fn(this.ligand, this.context.generation()); } catch (e) { this.onListenerError(e); }
    }
}

/** The app-wide instance. One home, module-scoped so a facet cannot make a second. */
export const ligandStore = new LigandStore(undefined, scientificContext);

function stableMoleculeId(text: string): string {
    // FNV-1a is an identity key for session context, not scientific provenance.
    let hash = 0x811c9dc5;
    for (let i = 0; i < text.length; i++) {
        hash ^= text.charCodeAt(i);
        hash = Math.imul(hash, 0x01000193);
    }
    return `mol_${(hash >>> 0).toString(16).padStart(8, '0')}`;
}

/** @deprecated Use scientificContext.generation()/isCurrent().
 * Kept only as a source-compatible facade; it owns no clock of its own. */
export class RequestGeneration {
    next(): number { return scientificContext.patch({ origin: 'command' }); }
    isCurrent(generation: number): boolean { return scientificContext.isCurrent(generation); }
}
