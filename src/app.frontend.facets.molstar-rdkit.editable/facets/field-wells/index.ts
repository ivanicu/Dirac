/**
 * Field Wells facet — 3D electromagnetic & quantum energy wells for the
 * focused ligand.
 *
 * Integration contract (same as property-cockpit): the lab computes the
 * focused ligand's molfile once and passes it to `updateFieldWellsLigand`.
 * The molfile carries scene coordinates, so every cube the backend returns
 * is already registered with the mol* scene — no alignment step exists.
 *
 * The heavy physics lives in backend/field_server.py (RDKit Gasteiger MEP,
 * pyscf HF for HOMO/LUMO/density/QM-MEP). This facet is a pure consumer:
 * fetch cube → ParseCube → VolumeFromCube → two signed isosurfaces with
 * x-ray shading. When the backend is down it says so instead of pretending.
 */

import { PluginContext } from '../../../mol-plugin/context';
import { StateTransforms } from '../../../mol-plugin-state/transforms';
import { createVolumeRepresentationParams } from '../../../mol-plugin-state/helpers/volume-representation-params';
import { Grid, Volume } from '../../../mol-model/volume';
import { Color } from '../../../mol-util/color';
import { focusSphereKeepingSlab } from '../../camera-slab';

// Follow the page's host: the daemon runs beside whatever served the app, so
// a hardcoded 127.0.0.1 would point a Mac's browser at the Mac itself and
// read as "backend offline" from every machine but this one.
const BACKEND = `http://${window.location.hostname || '127.0.0.1'}:8901`;
const REF_DATA = 'field-wells-data';
const REF_VOLUME = 'field-wells-volume';

/**
 * Two shells per sign: a vivid x-ray-shaded SOLID core at the full isovalue,
 * and a WIREFRAME cage at a lower one. Three stacked translucent skins read
 * as fog; a mesh cage over a glowing core reads as a force field — the lines
 * give the eye structure where alpha stacking only gives it milk.
 */
const SHELLS_DARK = [
    { fraction: 1.0, alpha: 0.55, emissive: 0.55, visuals: ['solid'] as string[] },
    // The cage is a WHISPER around the core, not a net over the scene: close
    // to the solid (0.62x) and faint, or the mesh buries both the molecule
    // and the surface it is supposed to be annotating.
    { fraction: 0.62, alpha: 0.14, emissive: 0.22, visuals: ['wireframe'] as string[] },
] as const;

/**
 * The same two shells against a LIGHT scene, which is a different optical
 * problem rather than the same one with new colours.
 *
 * `emissive` ADDS light. On a near-black well that reads as a glowing field;
 * on a #f1f0eb panel it drives every lobe toward the background, so the
 * brighter the field the less of it you see. It goes to zero here.
 *
 * `xrayShaded` makes facets facing the camera transparent and grazing ones
 * opaque. Against black that is a rim-lit x-ray; against white the face-on
 * middle shows the panel through it and the lobe reads as an empty outline —
 * the shape is exactly what an isosurface exists to communicate. Off, with
 * plain diffuse shading and enough alpha to still see the molecule inside.
 *
 * The cage gains weight instead of losing it: a thin dark line on paper is a
 * quieter mark than a thin bright line on black.
 */
const SHELLS_LIGHT = [
    { fraction: 1.0, alpha: 0.52, emissive: 0.0, visuals: ['solid'] as string[] },
    { fraction: 0.62, alpha: 0.26, emissive: 0.0, visuals: ['wireframe'] as string[] },
] as const;

/** sRGB relative luminance of a `#rrggbb` string; -1 when it cannot be read. */
function hexLuminance(hex: string): number {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) return -1;
    const v = parseInt(m[1], 16);
    const ch = [(v >> 16) & 255, (v >> 8) & 255, v & 255]
        .map(c => c / 255)
        .map(c => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}

function cssToken(name: string): string {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * Which optical regime the scene is in — MEASURED off the theme's own
 * background token rather than switched on a theme name. A theme nobody has
 * written yet gets the right treatment for free, and the app never has to keep
 * a list of which themes are dark.
 */
function shells() {
    const lum = hexLuminance(cssToken('--scene-bg'));
    return lum > 0.18 ? SHELLS_LIGHT : SHELLS_DARK;
}

/**
 * A field colour belongs to the THEME. It lived here as a hex literal, which
 * meant the one part of the interface where colour carries the physics was the
 * one part no theme could reach: the panel went to a machined light face and
 * the lobes stayed tuned for a near-black well, four of the seven sitting
 * under 2:1 against the new ground (measured, design/derive_field_colors.py).
 * The literal survives only as a fallback for a theme that forgets a token.
 */
function tokenColor(name: string, fallback: number): number {
    const m = /^#?([0-9a-f]{6})$/i.exec(cssToken(name));
    return m ? parseInt(m[1], 16) : fallback;
}
const shellRef = (sign: 'pos' | 'neg', i: number) => `field-wells-repr-${sign}-${i}`;

export type FieldKind = 'mep' | 'mep_qm' | 'homo' | 'lumo' | 'density' | 'mlp';

interface KindSpec {
    label: string;
    /** The FIXED contour, in `unit`. Identical across molecules by design. */
    iso: number;
    /**
     * display unit -> cube unit. The QM potential's cube is in Ha/e while the
     * classical one is in kcal/mol, and both were being contoured with their
     * own bare number — so toggling between two renderings of the SAME
     * physical quantity silently moved the contour from 18 to 31.4 kcal/mol.
     * One unit on screen, converted here.
     */
    cubeScale: number;
    diverging: boolean;
    unit: string;
    /** Theme token names; the numbers beside them are the fallback only. */
    posToken: string;
    negToken: string;
    posColor: number;
    negColor: number;
    quantum: boolean;
}

/** MEP convention: red = negative potential (electron-rich), blue = positive. */
// Colors are the design system's --viz-* tokens (design/tokens.css, Night),
// mid-saturation per Ivan's ruling: OKLCH chroma capped at the chroma of his
// settled #e0af68 gold. Hue = meaning (blue +, red −), lightness = legibility;
// only chroma was cut. Divergent pairs verified ≥0.10 ΔE apart by
// design/check_palette.py — run it after touching any value here.
const Kinds: Record<FieldKind, KindSpec> = {
    mep: { label: 'Electrostatic well', iso: 10, cubeScale: 1, diverging: true, unit: 'kcal/mol', posToken: '--viz-mep-pos', negToken: '--viz-mep-neg', posColor: 0x6788bc, negColor: 0xbd777b, quantum: false },
    mep_qm: { label: 'QM potential well', iso: 10, cubeScale: 1 / 627.5094740631, diverging: true, unit: 'kcal/mol', posToken: '--viz-mep-pos', negToken: '--viz-mep-neg', posColor: 0x6788bc, negColor: 0xbd777b, quantum: true },
    homo: { label: 'HOMO', iso: 0.04, cubeScale: 1, diverging: true, unit: 'amp', posToken: '--viz-orb-pos', negToken: '--viz-orb-neg', posColor: 0x7fc7a5, negColor: 0xa397d3, quantum: true },
    lumo: { label: 'LUMO', iso: 0.04, cubeScale: 1, diverging: true, unit: 'amp', posToken: '--viz-orb-pos', negToken: '--viz-orb-neg', posColor: 0x7fc7a5, negColor: 0xa397d3, quantum: true },
    density: { label: 'e⁻ density', iso: 0.05, cubeScale: 1, diverging: false, unit: 'e/Bohr³', posToken: '--viz-density', negToken: '--viz-density', posColor: 0xd8aa75, negColor: 0xd8aa75, quantum: true },
    // Default iso must sit BELOW the hydrophilic side's typical |min| (~0.06
    // on aspirin) or the cyan lobes never exist and the field looks all-grease.
    mlp: { label: 'Lipophilicity', iso: 0.25, cubeScale: 1, diverging: true, unit: 'MLP', posToken: '--viz-mlp-pos', negToken: '--viz-mlp-neg', posColor: 0xd5b979, negColor: 0x74ccdd, quantum: false },
};

interface FieldMeta {
    kind: string;
    basis?: string;
    method?: string;
    scf_energy_ha?: number;
    homo_ev?: number;
    lumo_ev?: number;
    scf_seconds?: number;
    total_seconds?: number;
    converged?: boolean;
    net_charge?: number;
    units?: string;
    natoms?: number;
    nbasis?: number;
    /** The fixed contour the backend grew the box around. */
    iso_fixed?: number;
    pad_used_angstrom?: number;
    wall_max?: number;
    /** False = the surface runs off the grid and is drawn as a flat face. */
    contour_closes_in_box?: boolean;
    /** MLP is one-signed for most drug-like molecules; measured, not assumed. */
    single_signed?: boolean;
    /** Why the frontier orbital energies must not be quoted at this level. */
    frontier_caveat?: string | null;
    /** False when the molecule has a halogen/chalcogen a point charge cannot represent. */
    sigma_hole_representable?: boolean;
    model_caveat?: string;
}

let plugin: PluginContext | null = null;
let molfile: string | null = null;
let ligandLabel: string | null = null;
let activeKind: FieldKind | null = null;
let activeVolume: Volume | null = null;
let lastMeta: FieldMeta | null = null;
let busy = false;

/**
 * Browser-side field cache — Ivan's architecture: a molecule's fields are
 * solved on arrival and live HERE; switching fields is a cache swap, not a
 * network roundtrip. The database is an EXPORT the user asks for (store=true),
 * not a write-through. Keyed kind|basis, cleared when the molfile changes.
 */
const cubeCache = new Map<string, { cube: string, meta: FieldMeta }>();
const pendingFetch = new Map<string, Promise<{ cube: string, meta: FieldMeta } | null>>();
const PREFETCH_CLASSICAL: FieldKind[] = ['mep', 'mlp'];
const PREFETCH_QUANTUM: FieldKind[] = ['homo', 'lumo', 'density'];
/** Quantum prefetch only below this heavy-atom count — a background prefetch
 * must never quietly start a six-minute Fe-heme SCF. */
const PREFETCH_QM_MAX_HEAVY = 40;

function currentBasis(): string {
    return byId<HTMLSelectElement>('field-basis')?.value ?? 'sto-3g';
}

function cacheKey(kind: FieldKind): string {
    return `${kind}|${Kinds[kind].quantum ? currentBasis() : 'none'}`;
}

/**
 * Heavy atoms — counted, not read off the counts line.
 *
 * The old body took columns 0-3 of line 4, which is the TOTAL atom count and
 * includes hydrogens, so the number driving the "is quantum affordable" gate
 * was wrong by a factor of about two on anything with explicit H. It also
 * returned 0 for a V3000 molfile, whose counts line reads "0 0 0 0 0 999
 * V3000" — and 0 heavy atoms passes every affordability check there is, so the
 * one format that most needs the gate was the one that disabled it.
 */
function molfileHeavyAtoms(mf: string): number {
    const lines = mf.split('\n');
    const counts = lines[3] ?? '';
    if (counts.includes('V3000')) {
        // Element symbol is the 4th whitespace field of each ATOM line.
        return lines.filter(l => /^M {2}V30 \d+ [A-Z]/.test(l))
            .filter(l => (l.trim().split(/\s+/)[3] ?? '') !== 'H').length;
    }
    const total = parseInt(counts.slice(0, 3), 10);
    if (!Number.isFinite(total) || total <= 0) return 0;
    let heavy = 0;
    for (let i = 4; i < 4 + total && i < lines.length; i++) {
        // V2000 atom line: x, y, z in 3x10 columns, then the element symbol.
        const sym = lines[i].slice(31, 34).trim();
        if (sym && sym !== 'H' && sym !== 'D') heavy++;
    }
    return heavy || total;
}

/**
 * A refusal that carries WHY. The backend distinguishes 'budget' (too slow, and
 * retryable with more time) from 'unsupported' (chemically impossible, and no
 * amount of time will help) — a difference the chemist has to see, because the
 * two demand opposite next moves.
 */
class FieldRefusal extends Error {
    constructor(message: string, readonly reason: 'budget' | 'unsupported' | 'internal' | 'network') {
        super(message);
    }
}

/**
 * The clock the panel is willing to wait, and the SAME number is sent to the
 * daemon as its own budget so the two cannot disagree. When they did, the panel
 * simply waited: one click on a heme held 22 cores for 36 minutes with the UI
 * showing "Solving…" the whole time and every button disabled.
 */
const QM_BUDGET_SECONDS = 60;
/** The socket must outlive the daemon's own deadline, or a timeout here would
 * be indistinguishable from the daemon's bound failing to fire. */
const clientTimeoutMs = (budget: number) => (budget * 2 + 20) * 1000;

/** The request the user can cancel — one at a time, by construction. */
let inFlight: AbortController | null = null;

/** Fetch one field into the browser cache (deduplicated). Throws FieldRefusal;
 * returns null if the focused ligand changed while in flight. */
async function fetchField(kind: FieldKind, store = false,
    budget = QM_BUDGET_SECONDS): Promise<{ cube: string, meta: FieldMeta } | null> {
    const key = cacheKey(kind);
    if (!store) {
        const hit = cubeCache.get(key);
        if (hit) return hit;
        const pending = pendingFetch.get(key);
        if (pending) return pending;
    }
    const requestMolfile = molfile;
    const basis = currentBasis();
    const controller = new AbortController();
    inFlight = controller;
    const timer = setTimeout(() => controller.abort(), clientTimeoutMs(budget));
    const p = (async () => {
        try {
            const resp = await fetch(`${BACKEND}/field`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    molfile: requestMolfile, kind, basis, store,
                    max_seconds: budget,
                }),
                signal: controller.signal,
            });
            const payload = await resp.json();
            if (molfile !== requestMolfile) return null;
            if (!payload.ok) throw new FieldRefusal(payload.error, payload.reason ?? 'internal');
            const entry = { cube: payload.cube as string, meta: payload.meta as FieldMeta };
            cubeCache.set(key, entry);
            return entry;
        } catch (e) {
            if (e instanceof FieldRefusal) throw e;
            if (controller.signal.aborted) {
                throw new FieldRefusal(
                    `cancelled after ${budget * 2 + 20} s — the daemon should have `
                    + `stopped itself at ${budget} s, so it is over its own bound`,
                    'budget');
            }
            throw new FieldRefusal(
                e instanceof Error ? e.message : String(e), 'network');
        } finally {
            clearTimeout(timer);
            if (inFlight === controller) inFlight = null;
            pendingFetch.delete(key);
        }
    })();
    pendingFetch.set(key, p);
    return p;
}

/** Abandon whatever is in flight. Used by the Cancel control and by any change
 * that makes the answer worthless before it arrives. */
function abortInFlight() {
    inFlight?.abort();
    inFlight = null;
    // A promise from the OLD molecule left in this map is not merely stale: a
    // request for the NEW molecule with the same kind|basis key joins it,
    // reads `molfile !== requestMolfile`, resolves null, and the panel reports
    // "ligand changed" for a click the user just made. The first field click
    // after switching molecules silently did nothing.
    pendingFetch.clear();
}

function setPrefetchNote(text: string) {
    const el = byId('field-prefetch');
    if (!el) return;
    el.hidden = !text;
    el.textContent = text;
}

/** Solve the molecule's fields into the browser cache, cheap ones first.
 * Quantum fields are skipped above PREFETCH_QM_MAX_HEAVY heavy atoms. */
async function prefetchAll() {
    if (!molfile) return;
    const startedFor = molfile;
    const heavy = molfileHeavyAtoms(molfile);
    const kinds = heavy <= PREFETCH_QM_MAX_HEAVY
        ? [...PREFETCH_CLASSICAL, ...PREFETCH_QUANTUM]
        : PREFETCH_CLASSICAL;
    let done = 0;
    // The REASON, not just the label. `catch {}` discarded the exception and
    // kept only the field's name; the note then appended an unrelated
    // heavy-atom clause, so Ivan read "unavailable: Electrostatic well
    // (quantum on demand — 43 heavy atoms)" for a CLASSICAL field that had
    // failed for a completely different reason. The backend had said exactly
    // what was wrong — "Gasteiger cannot parameterize Fe" — and this line
    // deleted it and substituted a falsehood.
    const failed: { label: string, why: string }[] = [];
    for (const kind of kinds) {
        if (molfile !== startedFor) return;   // ligand changed mid-prefetch
        setPrefetchNote(`Precomputing fields ${done}/${kinds.length} — ${Kinds[kind].label}…`);
        try {
            await fetchField(kind);
        } catch (e) {
            failed.push({
                label: Kinds[kind].label,
                why: e instanceof Error ? e.message : String(e),
            });
        }
        done++;
    }
    if (molfile !== startedFor) return;
    const parts: string[] = [];
    parts.push(`${done - failed.length}/${kinds.length} fields cached in browser`);
    if (heavy > PREFETCH_QM_MAX_HEAVY) {
        // Its own clause: this is a statement about what was NOT ATTEMPTED,
        // and gluing it onto the list of things that were attempted and failed
        // is what manufactured the wrong explanation.
        parts.push(`quantum fields left on demand — ${heavy} heavy atoms`);
    }
    setPrefetchNote(parts.join(' · '));
    const reasons = byId('field-prefetch-reasons');
    if (reasons) {
        reasons.hidden = failed.length === 0;
        reasons.innerHTML = failed.map(f =>
            `<div class="field-refusal"><span>${escapeHtml(f.label)}</span>`
            + `<span>${escapeHtml(f.why)}</span></div>`).join('');
    }
}

function escapeHtml(s: string): string {
    return s.replace(/[&<>"]/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
}


function byId<T extends HTMLElement>(id: string): T | null {
    return document.getElementById(id) as T | null;
}

function setStatus(text: string, tone: 'idle' | 'busy' | 'ok' | 'error' = 'idle') {
    const el = byId('field-status');
    if (!el) return;
    el.textContent = text;
    el.dataset.tone = tone;
}

function isoMultiplier(): number {
    const slider = byId<HTMLInputElement>('field-iso');
    return slider ? Math.pow(10, parseFloat(slider.value)) : 1;
}

/**
 * The isovalue the slider's 1.0x position means.
 *
 * MEP and MLP are sums of atomic contributions, so their scale belongs to the
 * molecule. Both earlier answers were wrong in opposite directions: a
 * hand-tuned constant clipped the grid wall on every molecule tried, and the
 * per-molecule percentile that replaced it was a function of the PADDING —
 * 7.6x swing on aspirin from pad alone. The contour is fixed now and the
 * BACKEND grows the box until it closes.
 */
/**
 * The contour, in the field's own display units.
 *
 * It is a CONSTANT, and that is the point. Two earlier attempts moved it:
 * a hand-tuned value that clipped the grid wall on every molecule tried, then
 * a per-molecule percentile that never clipped and was a function of the
 * PADDING — measured, 7.6x swing on aspirin from pad alone, with the physics
 * identical. A ruler derived from the box is a measurement of the box.
 *
 * The crate was a BOX problem, so the backend grows the box until the contour
 * closes inside it and reports whether it succeeded. The ruler holds still, so
 * the same colour means the same kcal/mol on every molecule in a series.
 */
function currentIso(): number {
    if (!activeKind) return 0;
    return Kinds[activeKind].iso * isoMultiplier();
}

/** The same contour expressed in the cube's own units. */
function currentIsoCube(): number {
    if (!activeKind) return 0;
    return currentIso() * Kinds[activeKind].cubeScale;
}

function updateIsoReadout() {
    const el = byId('field-iso-readout');
    if (!el) return;
    if (!activeKind) { el.textContent = ''; return; }
    const sign = Kinds[activeKind].diverging ? '±' : '';
    let note = '';
    // Whether the surface actually closes is a fact about this picture, and it
    // is the one the crate bug turned on. Stated, not inferred from the shape.
    if (lastMeta && lastMeta.contour_closes_in_box === false) {
        note = ` · open surface (field is ${lastMeta.wall_max ?? '?'} at the box edge)`;
    } else if (lastMeta && lastMeta.single_signed) {
        note = ' · field is single-signed — no negative lobe exists';
    }
    el.textContent =
        `${sign}${currentIso().toPrecision(3)} ${Kinds[activeKind].unit}${note}`;
}

function setButtonsEnabled() {
    document.querySelectorAll<HTMLButtonElement>('.field-btn').forEach(btn => {
        btn.disabled = busy || !molfile;
        btn.dataset.active = String(btn.dataset.field === activeKind);
    });
    // Cancel is the one control that must be LIVE precisely when everything
    // else is dead — `.field-btn` disables the whole row while busy, so it
    // deliberately does not carry that class.
    const cancel = byId<HTMLButtonElement>('field-cancel');
    if (cancel) {
        cancel.hidden = !busy;
        cancel.disabled = false;
    }
}

/**
 * `undefined` and `null` are the same thing to a reader, and the two response
 * paths disagree about which they use: the compute path omits a field it does
 * not have, the DB cache path returns it as null. The panel printed
 * "Net charge null · Compute time null s" on any cache hit — visible only in a
 * screenshot, because the code reads correctly and the shapes differ.
 */
function present<T>(v: T): v is NonNullable<T> {
    return v !== undefined && v !== null && (v as unknown) !== '';
}

function renderMeta(meta: FieldMeta | null) {
    const el = byId('field-meta');
    if (!el) return;
    if (!meta) { el.innerHTML = ''; return; }
    const rows: [string, string][] = [];
    if (present(meta.method)) rows.push(['Method', meta.basis ? `${meta.method}/${meta.basis}` : meta.method]);
    if (present(meta.units)) rows.push(['Units', meta.units]);
    if ((meta as { total_logp?: number }).total_logp !== undefined) {
        rows.push(['Crippen logP', (meta as { total_logp?: number }).total_logp!.toFixed(2)]);
    }
    if (present(meta.scf_energy_ha)) rows.push(['SCF energy', `${meta.scf_energy_ha.toFixed(4)} Ha`]);
    // One decimal, deliberately: Koopmans + minimal-basis errors are ~0.5-1 eV,
    // and a second decimal would put false precision in front of a chemist
    // (the physics session's absolute_uncertainty_pct lesson, applied here).
    // An orbital energy is printed ONLY when the level of theory can carry it.
    // At STO-3G the HOMO ordering of a substituted-benzene series inverts —
    // nitrobenzene reads as more electron-rich than benzene — and the LUMO
    // moves ~12 eV to def2-SVP. A number with no referent, printed to one
    // decimal, is worse than no number: the decimal is itself a claim.
    if (meta.frontier_caveat) {
        rows.push(['HOMO / LUMO', 'not quotable at this level']);
    } else {
        if (present(meta.homo_ev)) rows.push(['HOMO', `≈${meta.homo_ev.toFixed(1)} eV`]);
        if (present(meta.lumo_ev)) rows.push(['LUMO', `≈${meta.lumo_ev.toFixed(1)} eV`]);
    }
    if (present(meta.net_charge)) rows.push(['Net charge', String(meta.net_charge)]);
    if (present(meta.natoms)) rows.push(['Atoms (with H)', String(meta.natoms)]);
    if (present(meta.nbasis)) rows.push(['Basis functions', String(meta.nbasis)]);
    if (present(meta.total_seconds)) rows.push(['Compute time', `${meta.total_seconds} s`]);
    const caveats: string[] = [];
    if (meta.frontier_caveat) caveats.push(meta.frontier_caveat);
    if (meta.model_caveat) caveats.push(meta.model_caveat);
    if (meta.sigma_hole_representable === false) {
        caveats.push('This molecule has a halogen or chalcogen. A point-charge '
            + 'model is spherical, so a σ-hole is structurally impossible in it '
            + '— measured, it reports the opposite sign. Use the Physics tab, '
            + 'which computes the potential on the isodensity surface.');
    }
    el.innerHTML = rows.map(([k, v]) =>
        `<div class="field-meta-row"><span>${k}</span><span>${v}</span></div>`).join('')
        + caveats.map(c => `<div class="field-caveat">${escapeHtml(c)}</div>`).join('');
}

function reprParams(kind: FieldKind, sign: 1 | -1, shell: number) {
    const spec = Kinds[kind];
    const s = shells()[shell];
    return createVolumeRepresentationParams(plugin!, activeVolume ?? undefined, {
        type: 'isosurface',
        typeParams: {
            isoValue: Volume.IsoValue.absolute(sign * currentIsoCube() * s.fraction),
            visuals: s.visuals as ('solid' | 'wireframe')[],
            alpha: s.alpha,
            xrayShaded: s.visuals.includes('solid'),
            emissive: s.emissive,
        },
        color: 'uniform',
        colorParams: {
            value: Color(sign > 0
                ? tokenColor(spec.posToken, spec.posColor)
                : tokenColor(spec.negToken, spec.negColor)),
        },
    });
}

async function clearField() {
    activeKind = null;
    activeVolume = null;
    if (plugin && plugin.state.data.cells.has(REF_DATA)) {
        await plugin.build().delete(REF_DATA).commit();
    }
    renderMeta(null);
    updateIsoReadout();
    setButtonsEnabled();
}

async function renderCube(cubeText: string, kind: FieldKind, meta?: FieldMeta) {
    // Adopt the backend's measured isovalue when it sent one; fall back to the
    // kind's constant otherwise. Set BEFORE the representations are built, or
    // the first frame is drawn at the old scale and corrected a tick later.
    lastMeta = meta ?? null;
    if (!plugin) return;
    if (plugin.state.data.cells.has(REF_DATA)) {
        await plugin.build().delete(REF_DATA).commit();
    }

    const spec = Kinds[kind];
    activeKind = kind;

    const update = plugin.build();
    update.toRoot()
        .apply(StateTransforms.Data.RawData,
            { data: cubeText, label: `Field · ${spec.label}` },
            { ref: REF_DATA, state: { isGhost: true } })
        .apply(StateTransforms.Data.ParseCube, {}, { state: { isGhost: true } })
        .apply(StateTransforms.Volume.VolumeFromCube, {}, { ref: REF_VOLUME });
    await update.commit();

    const cell = plugin.state.data.cells.get(REF_VOLUME);
    activeVolume = (cell?.obj?.data as Volume) ?? null;
    if (!activeVolume) throw new Error('volume creation failed');

    const reprs = plugin.build();
    for (let i = 0; i < shells().length; i++) {
        reprs.to(REF_VOLUME).apply(
            StateTransforms.Representation.VolumeRepresentation3D,
            reprParams(kind, 1, i), { ref: shellRef('pos', i) });
        if (spec.diverging) {
            reprs.to(REF_VOLUME).apply(
                StateTransforms.Representation.VolumeRepresentation3D,
                reprParams(kind, -1, i), { ref: shellRef('neg', i) });
        }
    }
    await reprs.commit();
    frameFieldOnce();
    updateIsoReadout();
}

/** The molecule the camera has already been taken to for a field. */
let framedForMolfile: string | null = null;

/**
 * Fly to the field the first time one is rendered for a ligand.
 *
 * Without this the picture is honest and useless: the well is a few Ångström
 * across, the camera is framing a whole protein, and the field renders as a
 * speck behind an opaque cartoon — visible in a screenshot only if you already
 * know where to look. Computing a field IS the request to look at it.
 *
 * Once per ligand, not once per field: re-framing on every swap would fight a
 * user who has just positioned the camera to compare two of them.
 */
function frameFieldOnce() {
    if (!plugin || !activeVolume || framedForMolfile === molfile) return;
    try {
        const sphere = Grid.getBoundingSphere(activeVolume.grid);
        if (!sphere || !(sphere.radius > 0)) return;
        framedForMolfile = molfile;
        // Keeps the clipping slab wide enough for the rest of the scene, so
        // flying in does not slice the protein away around the field.
        focusSphereKeepingSlab(plugin, sphere, { extraRadius: 1.5, durationMs: 320 });
    } catch {
        // A camera that will not move is not a reason to withhold the field.
    }
}

async function updateIsoSurfaces() {
    if (!plugin || !activeKind || !activeVolume) return;
    const update = plugin.build();
    for (let i = 0; i < shells().length; i++) {
        for (const sign of ['pos', 'neg'] as const) {
            const ref = shellRef(sign, i);
            if (!plugin.state.data.cells.has(ref)) continue;
            const idx = i;
            update.to(ref).update(
                StateTransforms.Representation.VolumeRepresentation3D,
                () => reprParams(activeKind!, sign === 'pos' ? 1 : -1, idx));
        }
    }
    await update.commit();
    updateIsoReadout();
}

async function requestField(kind: FieldKind, budget = budgetFor(kind)) {
    if (!plugin || !molfile || busy) return;
    const spec = Kinds[kind];
    const requestMolfile = molfile;
    clearRetry();

    // Browser-cache path: solved on import, swapping fields costs no network.
    const cached = cubeCache.get(cacheKey(kind));
    if (cached) {
        busy = true;
        setButtonsEnabled();
        try {
            await renderCube(cached.cube, kind, cached.meta);
            renderMeta(cached.meta);
            setStatus(`${spec.label} rendered (browser cache).`, 'ok');
        } finally {
            busy = false;
            setButtonsEnabled();
        }
        return;
    }

    busy = true;
    setButtonsEnabled();
    setStatus(spec.quantum
        ? `Solving ${spec.label} — pyscf SCF on ${ligandLabel ?? 'ligand'}… `
          + `(gives up at ${budget} s · Cancel to stop now)`
        : `Computing ${spec.label}…`, 'busy');
    try {
        const entry = await fetchField(kind, false, budget);
        if (entry === null || molfile !== requestMolfile) {
            setStatus('Ligand changed while computing — stale field discarded.', 'idle');
            return;
        }
        await renderCube(entry.cube, kind, entry.meta);
        renderMeta(entry.meta);
        setStatus(`${spec.label} rendered for ${ligandLabel ?? 'ligand'}.`, 'ok');
    } catch (e) {
        const reason = e instanceof FieldRefusal ? e.reason : 'internal';
        const msg = e instanceof Error ? e.message : String(e);
        renderMeta(null);
        if (reason === 'network') {
            setStatus(`Backend unreachable — start it with: `
                + `backend/env/bin/python backend/field_server.py (${msg})`, 'error');
        } else if (reason === 'budget') {
            // Not a failure of the molecule, and showing it as one tells a
            // chemist their compound is broken when the calculation was merely
            // slow. The retry is offered with the budget it would actually need.
            setStatus(msg, 'busy');
            offerRetry(kind, budgetFor(kind) * 4);
        } else {
            // 'unsupported' — the backend named a cause and the cause is the
            // most useful thing on the screen. It is shown verbatim.
            setStatus(msg, 'error');
        }
    } finally {
        busy = false;
        setButtonsEnabled();
    }
}

/** The budget a kind is launched with. Classical fields are sub-second and
 * never need one; the quantum path is the only thing that has ever run away. */
function budgetFor(kind: FieldKind): number {
    return Kinds[kind].quantum ? QM_BUDGET_SECONDS : 15;
}

/** Put a real control on the screen instead of telling the user to edit JSON. */
function offerRetry(kind: FieldKind, budget: number) {
    const host = byId('field-retry');
    if (!host) return;
    host.hidden = false;
    host.innerHTML = '';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'field-btn';
    btn.textContent = `Run anyway · ${Math.round(budget)} s budget`;
    btn.addEventListener('click', () => {
        host.hidden = true;
        void requestField(kind, budget);
    });
    host.appendChild(btn);
}

function clearRetry() {
    const host = byId('field-retry');
    if (host) { host.hidden = true; host.innerHTML = ''; }
}

async function checkHealth() {
    const el = byId('fields-backend');
    if (!el) return;
    try {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 1500);
        const resp = await fetch(`${BACKEND}/health`, { signal: ctrl.signal });
        clearTimeout(timer);
        const h = await resp.json();
        el.textContent = `backend online · rdkit ${h.rdkit} · pyscf ${h.pyscf}`;
        el.dataset.online = 'true';
    } catch {
        el.textContent = 'backend offline — backend/env/bin/python backend/field_server.py';
        el.dataset.online = 'false';
    }
}

/** Called once after the workbench (and its plugin) exists. */
export function initFieldWellsPanel(p: PluginContext) {
    plugin = p;
    document.querySelectorAll<HTMLButtonElement>('.field-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const kind = btn.dataset.field as FieldKind;
            if (Kinds[kind]) void requestField(kind);
        });
    });
    byId<HTMLInputElement>('field-iso')?.addEventListener('input', () => void updateIsoSurfaces());
    byId('field-clear')?.addEventListener('click', () => {
        void clearField();
        clearRetry();
        setStatus('Field cleared.', 'idle');
    });
    // A running calculation the user cannot stop is the whole complaint: the
    // panel said "Solving…" for 36 minutes with every control disabled. The
    // daemon now stops itself too, but a bound the user cannot reach is not a
    // control, it is a promise.
    byId('field-cancel')?.addEventListener('click', () => {
        abortInFlight();
        busy = false;
        setButtonsEnabled();
        setStatus('Cancelled. The daemon stops at its own deadline; '
            + 'nothing is left running.', 'idle');
    });
    // Changing basis invalidates the quantum entries only; classical fields
    // have no basis. Cheapest correct move: refetch on demand.
    byId<HTMLSelectElement>('field-basis')?.addEventListener('change', () => {
        for (const key of [...cubeCache.keys()]) {
            if (!key.endsWith('|none')) cubeCache.delete(key);
        }
    });
    setButtonsEnabled();
    void checkHealth();
}

/**
 * Fire the cheap default field automatically — used by molecule import so a
 * pasted SMILES ends with a rendered well without another click. Skips
 * silently when a field is already up or a request is in flight.
 */
export function autoRenderElectrostaticWell() {
    if (molfile && !busy && !activeKind) void requestField('mep');
}

/**
 * Called from the lab's ligand lifecycle with the already-computed molfile.
 * A change of ligand invalidates any displayed field (it belongs to the
 * previous molecule), so the field is cleared, never silently kept.
 */
export function updateFieldWellsLigand(nextMolfile: string | null, label: string | null) {
    const changed = nextMolfile !== molfile;
    molfile = nextMolfile;
    ligandLabel = label;
    const summary = byId('fields-summary');
    if (summary) summary.textContent = molfile ? (label ?? 'Ligand') : 'No ligand loaded';
    if (changed) {
        // Everything in flight belongs to the PREVIOUS molecule and its answer
        // is worthless now. Without this the pending map kept the old
        // promises, and the first field click on the new molecule joined one,
        // resolved null, and reported "ligand changed" for a click that had
        // just been made.
        abortInFlight();
        cubeCache.clear();
        clearRetry();
        setPrefetchNote('');
        const reasons = byId('field-prefetch-reasons');
        if (reasons) { reasons.hidden = true; reasons.innerHTML = ''; }
        if (activeKind) {
            void clearField();
            setStatus('Ligand changed — previous field cleared.', 'idle');
        }
        // Solve the new molecule's fields into the browser cache right away —
        // by the time a button is clicked, the answer is usually local.
        if (molfile) void prefetchAll();
    }
    if (!activeKind && !changed) {
        setStatus(molfile ? 'Pick a field to render its 3D well.' : 'Load a structure with a ligand first.', 'idle');
    }
    setButtonsEnabled();
}
