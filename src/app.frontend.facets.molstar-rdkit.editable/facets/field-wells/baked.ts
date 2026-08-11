/**
 * Baked fields: the deployed site carries its own answers.
 *
 * ivan.icu is static. The panel talks to http://<hostname>:8901, which on a
 * visitor's machine is nothing — and the two daemons must NEVER be exposed
 * publicly, because they are unauthenticated services that run quantum
 * chemistry on whatever is posted to them. So either the fields are dead on
 * the deployed site, or the answers travel with the page.
 *
 * FORMAT, chosen by measurement (scripts/bake-fields.py has the table): float16
 * + gzip is 11.8x smaller than the ascii cube — 0.57 MB against 6.75 MB for an
 * 80³ orbital — at a cost of 6.09e-05 absolute error against a 1.805e-01 peak
 * on a field contoured at 0.04. Three orders of magnitude below the isovalue,
 * so it cannot move a surface anyone can see.
 *
 * The payload is REHYDRATED into an ascii cube and handed to the parser that
 * already exists. A bespoke binary loader would be a second cube reader, and
 * mol*'s is proven; ~200 ms of string building is a cheap price for not owning
 * a parser. float16 decoding is done by hand because the platform has no
 * Float16Array yet in every target browser.
 *
 * Keyed on sha256(molfile) — the SAME key the durable cache uses. A second
 * notion of identity is a second thing that can disagree with the first.
 */

interface BakedField {
    data?: string;
    header?: string;
    bytes?: number;
    refused?: boolean;
}

interface BakedMolecule {
    molecule: string;
    /** null when the structure carries no deposited small molecule. */
    molfile_sha256: string | null;
    no_ligand?: boolean;
    fields: Record<string, BakedField>;
}

let manifest: { molecules: BakedMolecule[] } | null | undefined;

async function loadManifest(): Promise<{ molecules: BakedMolecule[] } | null> {
    if (manifest !== undefined) return manifest;
    try {
        const resp = await fetch('fields/manifest.json');
        manifest = resp.ok ? await resp.json() as { molecules: BakedMolecule[] } : null;
    } catch {
        manifest = null;   // no bake in this deployment; the backend path stands
    }
    return manifest ?? null;
}

/** sha256 of the molfile, hex — the durable cache's key, computed in-browser. */
async function molfileKey(molfile: string): Promise<string> {
    const bytes = new TextEncoder().encode(molfile);
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest))
        .map(b => b.toString(16).padStart(2, '0')).join('');
}

/** IEEE 754 half → float. No Float16Array in every target browser yet. */
function decodeFloat16(u16: Uint16Array): Float32Array {
    const out = new Float32Array(u16.length);
    for (let i = 0; i < u16.length; i++) {
        const h = u16[i];
        const sign = (h & 0x8000) ? -1 : 1;
        const exp = (h & 0x7c00) >> 10;
        const frac = h & 0x03ff;
        if (exp === 0) out[i] = sign * Math.pow(2, -14) * (frac / 1024);
        else if (exp === 0x1f) out[i] = frac ? NaN : sign * Infinity;
        else out[i] = sign * Math.pow(2, exp - 15) * (1 + frac / 1024);
    }
    return out;
}

async function gunzip(buf: ArrayBuffer): Promise<ArrayBuffer> {
    const stream = new Response(buf).body!.pipeThrough(
        new DecompressionStream('gzip'));
    return await new Response(stream).arrayBuffer();
}

export interface BakedResult { cube: string; meta: Record<string, unknown> }

/**
 * The baked answer for this molfile and kind, or null if there is none.
 *
 * A baked REFUSAL is returned as a refusal, not as "no bake". The deployed
 * site must be able to say "Gasteiger cannot parameterize Fe" on its own —
 * falling through to "backend offline" would blame the network for a chemistry
 * answer, which is the fallback-hides-the-real-reason shape.
 */
/**
 * Whether the bake KNOWS this structure has no deposited ligand.
 *
 * Five of the ten bundled fixtures have none. Without this the panel cannot
 * tell "no small molecule in this crystal structure" from "the bake is
 * missing" from "the backend is down", and it would show the last of the three
 * — blaming the network for crystallography.
 */
export async function bakedNoLigand(name: string): Promise<boolean> {
    const m = await loadManifest();
    return !!m?.molecules.find(x => x.molecule === name && x.no_ligand);
}

export async function bakedField(molfile: string, kind: string, moleculeId?: string):
Promise<BakedResult | { refused: string } | null> {
    const m = await loadManifest();
    if (!m) return null;

    // TWO KEYS, and the order matters. sha256(molfile) is EXACT — it proves the
    // baked cube belongs to these coordinates — but it is also brittle: the
    // molfile is reconstructed by the app, so any change to that reconstruction
    // moves every hash and silently empties the bake. That is what happened
    // here: the deployed bundle rebuilds the molblock slightly differently from
    // the build the bake was made with, every lookup missed, and the panel
    // fell back to "backend offline" — blaming the network for a key mismatch.
    //
    // The structure ID survives a rebuild. So: hash first (exact, preferred),
    // molecule ID second (durable), and meta records WHICH matched so a
    // hash-miss is visible as a fact rather than as silence.
    const key = await molfileKey(molfile);
    let matchedBy = 'molfile-sha256';
    let entry = m.molecules.find(x => x.molfile_sha256 === key);
    if (!entry && moleculeId) {
        entry = m.molecules.find(x => x.molecule === moleculeId && !x.no_ligand);
        matchedBy = 'molecule-id (molfile hash did not match — the app rebuilds '
            + 'the molblock differently than when this was baked)';
    }
    if (!entry) return null;
    const field = entry.fields[kind];
    if (!field) return null;
    if (field.refused) {
        return { refused: 'This field was refused when the site was built, and '
            + 'the refusal is part of the bake: it is a fact about the molecule, '
            + 'not about the connection.' };
    }
    if (!field.data || !field.header) return null;

    const [headerResp, dataResp] = await Promise.all([
        fetch(field.header), fetch(field.data),
    ]);
    if (!headerResp.ok || !dataResp.ok) return null;
    const head = await headerResp.json() as {
        header: string[]; n_values: number; meta: Record<string, unknown>;
    };
    const raw = await gunzip(await dataResp.arrayBuffer());
    const values = decodeFloat16(new Uint16Array(raw));

    // Rehydrate the cube exactly as pyscf writes it: six values per line in
    // %13.5e. The parser downstream is whitespace-tolerant, but matching the
    // real format keeps a baked cube byte-comparable with a computed one,
    // which is what makes the two paths checkable against each other.
    const parts: string[] = head.header.slice();
    let line: string[] = [];
    for (let i = 0; i < values.length; i++) {
        line.push(values[i].toExponential(5).padStart(13));
        if (line.length === 6) { parts.push(line.join('')); line = []; }
    }
    if (line.length) parts.push(line.join(''));

    return {
        cube: parts.join('\n'),
        meta: { ...head.meta, cache: 'baked', baked_precision: 'float16',
                baked_matched_by: matchedBy },
    };
}
