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
import { buildSurroundingsRequest } from './surroundings';
import { bakedField } from './baked';
import { fieldOverlay } from './volume-overlay';
// THE SDK, and the frontend eating it is the point of PR-13. Every URL, every envelope
// shape, every artifact digest check and the v1/v2 choice live in ONE module now, shared
// with the CLI's and the MCP adapter's semantics. Before this, the URL and the refusal
// shape were rebuilt at five call sites and the sixth would have differed.
import { DiracClient, DiracError, fetchField as sdkFetchField } from '../../../app/services/dirac-client';

// A renderer that fails silently is indistinguishable from one that works but
// has nothing to draw. The handle costs nothing and every harness can read it.
(globalThis as unknown as Record<string, unknown>).__diracFieldOverlay = fieldOverlay;

// Follow the page's host: the daemon runs beside whatever served the app, so
// a hardcoded 127.0.0.1 would point a Mac's browser at the Mac itself and
// read as "backend offline" from every machine but this one.
const BACKEND = `http://${window.location.hostname || '127.0.0.1'}:8901`;
/**
 * One client per module. Holds the v2-availability memo, so a v1-only daemon costs one
 * 404 for the whole session rather than one per interaction — on the click the user is
 * waiting for.
 */
const dirac = new DiracClient({ baseUrl: BACKEND });
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

/** Non-null when the overlay could not run and the old shells drew instead.
 *  Surfaced in the panel: a fallback that renders identically to the primary
 *  is indistinguishable from the primary working. */
let overlayFallback: string | null = null;

/**
 * Which renderer draws the field.
 *
 * `both` is not a debug toggle, it is THE ALIGNMENT INSTRUMENT. The overlay
 * raymarches in its own WebGL context off mol*'s camera state, and a placement
 * that is wrong by one convention — a transposed basis, a flipped handedness,
 * an off-by-one on the texel centre — renders a plausible field in the wrong
 * place. That is the worst failure available here, because it looks like
 * chemistry and nothing in the picture contradicts it.
 *
 * Drawn together, the two renderers must agree: the overlay's rim traces the
 * silhouette of mol*'s own isosurface at the same isovalue, because they are
 * the same level set of the same volume. Any drift is visible immediately and
 * needs no instrumentation. Keeping the legacy path reachable also means it
 * stays alive rather than rotting as dead code behind a fallback nobody hits.
 */
export type FieldRenderer = 'composite' | 'shells' | 'both';
// DEFAULT IS THE LEGACY SHELLS, deliberately, and this is not the plan being
// abandoned — it is a measured fact about the composite's current state. The
// compositing path is now proven correct end to end (a flat probe fills
// 985,504 of 985,504 viewport pixels), but the FIELD it composites is still
// wrong: it renders as an unstructured mass filling the volume's footprint,
// the sign channel never produces a positive lobe, and all four styles come
// out indistinguishable. Shipping that as the default would replace a working
// renderer with a broken one. Composite stays one dropdown away so the work
// is visible and testable; it becomes the default when it draws a field a
// chemist would recognise.
let renderer: FieldRenderer = 'shells';
export function setFieldRenderer(next: FieldRenderer) { renderer = next; }
export function getFieldRenderer(): FieldRenderer { return renderer; }

/** Value that saturates a display channel, in the cube's own units. The rim
 *  sits at the isovalue, so the channels are scaled against a few times it —
 *  a fixed constant here would be a per-molecule clamp by another name. */
function isoScaleFor(kind: FieldKind): number {
    return Math.abs(currentIso() * Kinds[kind].cubeScale) * 3 || 1;
}

/** The scene's ground decides additive vs subtractive, and the field colours
 *  come from the theme, not from literals — same rule as the shells. */
function syncOverlayTheme() {
    if (!activeKind) return;
    const spec = Kinds[activeKind];
    const rgb = (n: number): [number, number, number] =>
        [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
    fieldOverlay.setColors(rgb(tokenColor(spec.negToken, spec.negColor)),
                           rgb(tokenColor(spec.posToken, spec.posColor)));
    fieldOverlay.setSceneLuminance(hexLuminance(cssToken('--scene-bg')));
}

/**
 * The four channels, as switches.
 *
 * They are switches and not a style preset because each one carries a DIFFERENT
 * QUANTITY, and the only way to know what a channel was contributing is to turn
 * it off. Shape states a boundary, steepness is |grad V| — where a partner feels
 * a force, which is not where V is large — direction is the grain of grad V, and
 * sign is the one the other three structurally cannot carry.
 */
const CHANNELS: { key: keyof import('./volume-overlay').OverlayChannels; label: string; hint: string }[] = [
    { key: 'shape', label: 'Shape', hint: 'rim of the contour — the only channel that states a boundary' },
    { key: 'steepness', label: 'Steepness', hint: '|∇V| outside the contour — where a partner feels a force' },
    { key: 'direction', label: 'Direction', hint: 'grain along ∇V — which way it pulls' },
    { key: 'sign', label: 'Sign', hint: 'donor vs acceptor — the channel the other three cannot carry' },
];

function renderOverlayControls() {
    const sel = byId<HTMLSelectElement>('field-renderer');
    if (sel && sel.value !== renderer) sel.value = renderer;
    const host = byId('field-channels');
    if (!host) return;
    const on = fieldOverlay.active && !overlayFallback && renderer !== 'shells';
    host.hidden = !on;
    if (!on) {
        const note = byId('field-overlay-note');
        if (note) {
            note.hidden = !overlayFallback;
            note.textContent = overlayFallback
                ? `Drawn with the legacy isosurface shells — ${overlayFallback}.`
                : '';
        }
        return;
    }
    const note = byId('field-overlay-note');
    if (note) {
        const dsFrom = fieldOverlay.downsampledFrom;
        note.hidden = !dsFrom;
        note.textContent = dsFrom
            ? `Volume downsampled from ${dsFrom.join('×')} for display; values are unchanged, resolution is not.`
            : '';
    }
    const st = fieldOverlay.getChannels();
    host.innerHTML = CHANNELS.map(c =>
        `<button type="button" class="field-chan" data-chan="${c.key}" `
        + `aria-pressed="${st[c.key]}" title="${escapeHtml(c.hint)}">${c.label}</button>`).join('');
    for (const b of Array.from(host.querySelectorAll<HTMLButtonElement>('.field-chan'))) {
        b.addEventListener('click', () => {
            const k = b.dataset.chan as keyof import('./volume-overlay').OverlayChannels;
            const next = b.getAttribute('aria-pressed') !== 'true';
            b.setAttribute('aria-pressed', String(next));
            fieldOverlay.setChannels({ [k]: next });
        });
    }
}

export type FieldKind = 'mep' | 'mep_qm' | 'homo' | 'lumo' | 'density' | 'mlp'
    /** The REVERSE field: the pocket as source, read in the ligand's box. */
    | 'pocket_mep';

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
    // Same units, same fixed contour and same colours as `mep` on purpose: the
    // whole value of the reverse field is comparing it against the ligand's
    // own, and a different scale would make that comparison a lie.
    pocket_mep: { label: 'Pocket map (point charges, screened)', iso: 10, cubeScale: 1, diverging: true, unit: 'kcal/mol', posToken: '--viz-mep-pos', negToken: '--viz-mep-neg', posColor: 0x6788bc, negColor: 0xbd777b, quantum: false },
    mlp: { label: 'Lipophilicity', iso: 0.25, cubeScale: 1, diverging: true, unit: 'MLP', posToken: '--viz-mlp-pos', negToken: '--viz-mlp-neg', posColor: 0xd5b979, negColor: 0x74ccdd, quantum: false },
};


/**
 * What the panel and the renderer read: the CANONICAL output tree plus the envelope's own
 * metadata, and nothing hand-mirrored.
 *
 * The 26-field `FieldMeta` interface that used to live here is gone. It was a second home
 * for facts the backend already declares, kept in step by a gate that compared it key by
 * key — and it drifted twice in one day regardless, because policing two homes is not the
 * same as having one. `data` is typed by the generated
 * contracts/generated/typescript/methods.ts, so its shape is the descriptor's shape by
 * construction and the only staleness check needed is whether the generated file is
 * current.
 *
 * `regionExtras` is the ONE remaining legacy pocket, and it is named so it cannot hide:
 * /field/region has not been folded into the kernel yet, so its two facts (waters_excluded,
 * waters_note) still arrive outside the contract. It is deleted when that route joins the
 * catalog, and its presence here is the reminder that it has not.
 */
export type FieldView = {
    data: Record<string, any>;
    warnings: Array<{ code: string; message: string; affects?: string[] }>;
    meta: Record<string, any> | null;
    digestVerified?: boolean;
    digestUnverifiedReason?: string;
    regionExtras?: { waters_excluded?: number | null; waters_note?: string | null };
};

let plugin: PluginContext | null = null;
let molfile: string | null = null;
let ligandLabel: string | null = null;
let activeKind: FieldKind | null = null;
let activeVolume: Volume | null = null;
let lastView: FieldView | null = null;
let busy = false;

/**
 * Browser-side field cache — Ivan's architecture: a molecule's fields are
 * solved on arrival and live HERE; switching fields is a cache swap, not a
 * network roundtrip. The database is an EXPORT the user asks for (store=true),
 * not a write-through. Keyed kind|basis, cleared when the molfile changes.
 */
const cubeCache = new Map<string, { cube: string, view: FieldView }>();

/**
 * The baked fallback's legacy flat meta, lifted into the canonical view.
 *
 * ONE translator, and it lives here rather than in the SDK because the legacy shape is a
 * property of the BAKED FILES on disk — they were generated before the output contract
 * existed and they are static assets, so nothing can migrate them in place. Putting the
 * adapter in the SDK would have implied the wire still speaks this shape, which it does
 * not.
 *
 * Deleted when the bake is regenerated from /v2/invoke. Until then a baked field is a
 * genuinely poorer answer than a live one, and `bakedFallback: true` in the view says so
 * rather than letting it pass as equivalent.
 */
function viewFromBaked(m: any, kind: FieldKind): FieldView {
    const meta = m || {};
    const warnings: Array<{ code: string; message: string }> = [];
    for (const [key, code] of [['frontier_caveat', 'BASIS_NOT_QUOTABLE'],
                               ['model_caveat', 'MODEL_CAVEAT'],
                               ['physics_caveat', 'MODEL_CAVEAT']] as const) {
        if (meta[key]) warnings.push({ code, message: String(meta[key]) });
    }
    return {
        data: {
            field: {
                kind: meta.kind ?? kind,
                native_units: meta.units,
                grid: { dimensions: meta.dims, spacing_angstrom: meta.spacing },
                extrema: { min: meta.vmin, max: meta.vmax },
                single_signed: meta.single_signed ?? null,
                box: {
                    iso_fixed: meta.iso_fixed ?? null,
                    iso_sized_for: meta.iso_sized_for ?? null,
                    contour_closes_in_box: meta.contour_closes_in_box ?? null,
                    wall_seconds: meta.wall_max ?? null,
                },
            },
            ...(meta.converged !== undefined ? {
                wavefunction: {
                    converged: meta.converged, method: meta.method, basis: meta.basis,
                    n_basis_functions: meta.nbasis,
                    scf_energy_hartree: meta.scf_energy_ha,
                    homo_ev: meta.homo_ev, lumo_ev: meta.lumo_ev,
                },
            } : {}),
            model: {
                charge_model: meta.charges ?? meta.charge_model ?? null,
                net_charge: meta.net_charge ?? null,
                total_logp: meta.total_logp ?? null,
                sigma_hole_representable: meta.sigma_hole_representable ?? null,
            },
        },
        warnings,
        meta: { version: meta.method_version ?? null, cache: 'baked',
                seconds: meta.total_seconds ?? null, bakedFallback: true,
                provenance: { n_atoms: meta.natoms ?? null } },
        digestVerified: false,
        digestUnverifiedReason: 'this field came from the pre-baked static assets, which '
            + 'carry no digest — it is a fallback for an offline backend, not a computed '
            + 'result',
    };
}
const pendingFetch = new Map<string, Promise<{ cube: string, view: FieldView } | null>>();
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
/**
 * Whether the compute backend answered its health check. On the deployed
 * static site it never will, and that is the normal case there rather than an
 * error — the answers ship with the page instead.
 */
let backendOnline = false;
/** The loaded structure's id (e.g. "1CBS"). Durable across rebuilds, unlike the
 * reconstructed molfile's hash, so the bake can still be found when the app's
 * molblock writer changes. */
let structureId: string | null = null;
export function setFieldStructureId(id: string | null) { structureId = id; }

/** Fetch one field into the browser cache (deduplicated). Throws FieldRefusal;
 * returns null if the focused ligand changed while in flight. */
async function fetchField(kind: FieldKind, store = false,
    budget = QM_BUDGET_SECONDS): Promise<{ cube: string, view: FieldView } | null> {
    const key = cacheKey(kind);
    if (!store) {
        const hit = cubeCache.get(key);
        if (hit) return hit;
        const pending = pendingFetch.get(key);
        if (pending) return pending;
    }
    const requestMolfile = molfile;

    // BAKED FIRST when there is no backend to ask. Backend-first while one is
    // reachable, so a locally recomputed field is never shadowed by a bake
    // that predates a physics change — that staleness is exactly what the
    // producer-identity guard exists to prevent, and a bake is a cache too.
    if (!backendOnline && requestMolfile) {
        const baked = await bakedField(requestMolfile, kind, structureId ?? undefined);
        if (baked && 'refused' in baked) throw new FieldRefusal(baked.refused, 'unsupported');
        if (baked) {
            const entry = { cube: baked.cube, view: viewFromBaked(baked.meta, kind) };
            cubeCache.set(key, entry);
            return entry;
        }
    }

    // ONLY the quantum kinds take a basis, and the Kinds table already says which. Sent
    // unconditionally, this produced a REAL refusal the browser surfaced today:
    // `fields.mlp: /parameters Additional properties are not allowed ('basis' was
    // unexpected)`. The contract was right and the client was wrong — and the client had
    // the answer in its own table the whole time. Sending a parameter a method does not
    // declare is not harmless politeness; the schema is closed, and closed is the point.
    const basis = Kinds[kind].quantum ? currentBasis() : undefined;
    const controller = new AbortController();
    inFlight = controller;
    const timer = setTimeout(() => controller.abort(), clientTimeoutMs(budget));
    const p = (async () => {
        try {
            const got = await sdkFetchField(dirac, kind, {
                molfile: requestMolfile!, basis, maxSeconds: budget,
                signal: controller.signal,
            });
            if (molfile !== requestMolfile) return null;
            // The digest check is REPORTED, not assumed. On this origin (plain http on a
            // LAN address) crypto.subtle does not exist, so the browser cannot verify —
            // and that is recorded in the meta rather than passed off as verified. A
            // check that is quietly absent looks exactly like one that passed.
            const view: FieldView = {
                data: got.data, warnings: got.warnings, meta: got.envelope.meta ?? null,
                digestVerified: got.digestVerified,
                digestUnverifiedReason: got.digestUnverifiedReason,
            };
            const entry = { cube: got.cube, view };
            cubeCache.set(key, entry);
            return entry;
        } catch (e) {
            if (e instanceof FieldRefusal) throw e;
            if (e instanceof DiracError) {
                // Mapped by CODE. The panel's four reason buckets are coarser than the
                // twelve-code vocabulary, so the mapping is explicit and lossy in a
                // stated direction: anything the panel has no bucket for reads as
                // 'unsupported', which shows the message and does not offer a retry.
                const bucket = ({
                    BUDGET: 'budget', TOO_LARGE: 'unsupported', PARSE: 'unsupported',
                    UNSUPPORTED: 'unsupported', UNPARAMETERIZED: 'unsupported',
                    OPEN_SHELL_SPIN_REQUIRED: 'unsupported', UNCONVERGED: 'unsupported',
                    DB_UNAVAILABLE: 'network', NOT_FOUND: 'network',
                    DIGEST_MISMATCH: 'network', INTERNAL: 'internal',
                } as Record<string, 'budget' | 'unsupported' | 'internal' | 'network'>)[e.code]
                    ?? 'unsupported';
                throw new FieldRefusal(
                    e.callerAction ? `${e.message} — ${e.callerAction}` : e.message,
                    bucket);
            }
            if (controller.signal.aborted) {
                throw new FieldRefusal(
                    `cancelled after ${budget * 2 + 20} s — the daemon should have `
                    + `stopped itself at ${budget} s, so it is over its own bound`,
                    'budget');
            }
            // A backend that was up at health-check time and is not now: the
            // bake is a better answer than an error, and it says it is baked.
            if (requestMolfile) {
                const baked = await bakedField(requestMolfile, kind, structureId ?? undefined);
                if (baked && !('refused' in baked)) {
                    const entry = { cube: baked.cube, view: viewFromBaked(baked.meta, kind) };
                    cubeCache.set(key, entry);
                    return entry;
                }
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
    // Recomputed against the CURRENT slider position, not the value the
    // backend sized the box for. The slider multiplies the contour by
    // 10^[-1,1], so a box that closes at the default can still be exited at
    // the low end — that is where the cut-off lobes came from.
    const wall = lastView?.data?.field?.box?.wall_seconds;
    const open = typeof wall === 'number' && currentIso() <= wall;
    if (open) {
        note = ` · OPEN SURFACE — field reaches ${wall} at the box edge, `
             + `so the lobes are cut off. Raise the isovalue.`;
    } else if (lastView?.data?.field?.single_signed) {
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
export function present<T>(v: T): v is NonNullable<T> {
    return v !== undefined && v !== null && (v as unknown) !== '';
}

/** True when two already-rounded numbers are meaningfully different — plain
 * `!==` would also fire on float noise the backend never intended as signal. */
function numbersDiffer(a: number, b: number): boolean {
    return Math.abs(a - b) > 1e-9;
}

export interface MetaRow { label: string; value: string; }

/**
 * Pure: meta -> what the panel should show. Kept apart from `renderMeta`
 * (which owns only the DOM write) so the caveat-gating rules — the entire
 * reason this file exists — can be exercised from a test with no browser.
 *
 * Every row and caveat is presence-checked with `present()`, so an absent OR
 * explicitly-null key is omitted, never printed as the literal string
 * "null" (that shipped once, and was caught only by a screenshot).
 */
/**
 * The panel, reading the CANONICAL output tree.
 *
 * WHAT THIS REPLACES: a 26-field hand-written `FieldMeta` interface and thirty reads out
 * of a flat dict that no schema governed. A gate compared that interface to the backend
 * key by key — and it still drifted twice in one day, because two homes for one fact drift
 * whatever polices them. The type now comes from
 * contracts/generated/typescript/methods.ts, generated from the descriptors, so the drift
 * check is "is the generated file stale" rather than one comparison per key.
 *
 * AND THE CAVEATS NOW SWITCH ON A CODE. They used to be four differently-named prose keys
 * (`frontier_caveat`, `model_caveat`, `physics_caveat`, `waters_note`) whose presence was
 * the signal — so a backend that renamed one silently stopped warning anybody, and nothing
 * would have gone red. `warnings[]` is typed: the panel matches `code`, and an unfamiliar
 * code is still SHOWN (as a caveat with its message) rather than dropped, because a
 * warning this client does not recognise is exactly the one worth reading.
 */
export function buildMetaDisplay(view: FieldView | null): { rows: MetaRow[], caveats: string[] } {
    const rows: MetaRow[] = [];
    const caveats: string[] = [];
    if (!view) return { rows, caveats };
    const f = view.data.field ?? {};
    const wf = view.data.wavefunction ?? {};
    const model = view.data.model ?? {};
    const box = f.box ?? {};
    const prov = (view.meta?.provenance ?? {}) as Record<string, any>;
    const units = f.native_units;
    const codes = new Set(view.warnings.map(w => w.code));

    const methodName = wf.method ?? model.charge_model ?? model.logp_model;
    if (present(methodName)) {
        rows.push({ label: 'Method',
                    value: wf.basis ? `${methodName}/${wf.basis}` : String(methodName) });
    }
    if (present(units)) rows.push({ label: 'Units', value: String(units) });
    if (present(model.total_logp)) rows.push({ label: 'Crippen logP', value: model.total_logp.toFixed(2) });
    if (present(wf.scf_energy_hartree)) rows.push({ label: 'SCF energy', value: `${wf.scf_energy_hartree.toFixed(4)} Ha` });
    // One decimal, deliberately: Koopmans + minimal-basis errors are ~0.5-1 eV, and a
    // second decimal would put false precision in front of a chemist. An orbital energy is
    // printed ONLY when the level of theory can carry it — at STO-3G the HOMO ordering of
    // a substituted-benzene series inverts and the LUMO moves ~12 eV to def2-SVP, so the
    // decimal is itself a claim. The signal is now the WARNING CODE, not the presence of a
    // prose string.
    if (codes.has('BASIS_NOT_QUOTABLE')) {
        rows.push({ label: 'HOMO / LUMO', value: 'not quotable at this level' });
    } else {
        if (present(wf.homo_ev)) rows.push({ label: 'HOMO', value: `≈${wf.homo_ev.toFixed(1)} eV` });
        if (present(wf.lumo_ev)) rows.push({ label: 'LUMO', value: `≈${wf.lumo_ev.toFixed(1)} eV` });
    }
    if (present(model.net_charge)) rows.push({ label: 'Net charge', value: String(model.net_charge) });
    if (present(prov.n_atoms)) rows.push({ label: 'Atoms (with H)', value: String(prov.n_atoms) });
    if (present(wf.n_basis_functions)) rows.push({ label: 'Basis functions', value: String(wf.n_basis_functions) });
    if (present(view.meta?.seconds)) rows.push({ label: 'Compute time', value: `${view.meta!.seconds} s` });
    // WHICH SOURCE RAN. New here, and it is the one row a chemist comparing two maps
    // actually needs: two fields with different method versions came from different code
    // and are not comparable however similar they look.
    if (present(view.meta?.version)) rows.push({ label: 'Method version', value: String(view.meta!.version) });
    // Region route: WHERE the charges came from, and how much of the source was
    // deliberately left out. A group field is additive but the charge model is not, so a
    // residue-template source and a caller-supplied one are different claims.
    if (present(model.charge_model) && !present(wf.method)) {
        rows.push({ label: 'Charge model', value: String(model.charge_model) });
    }
    if (present(view.regionExtras?.waters_excluded)) {
        rows.push({ label: 'Waters excluded', value: String(view.regionExtras!.waters_excluded) });
    }

    // EVERY warning is shown, recognised or not. A code this build has never seen is the
    // most interesting one there is, and dropping it would make a new caveat invisible
    // until somebody remembered to add a branch.
    for (const w of view.warnings) caveats.push(w.message);
    if (model.sigma_hole_representable === false && !codes.has('SIGMA_HOLE_NOT_REPRESENTABLE')) {
        caveats.push('This molecule has a halogen or chalcogen. A point-charge model is '
            + 'spherical, so a σ-hole is structurally impossible in it — measured, it '
            + 'reports the opposite sign. Use the Physics tab, which computes the '
            + 'potential on the isodensity surface.');
    }
    if (present(view.regionExtras?.waters_note)) caveats.push(view.regionExtras!.waters_note);
    // iso_sized_for is the isovalue the BOX was grown to keep closed at the slider FLOOR;
    // iso_fixed is the contour actually drawn at the slider's default. The two differ by
    // design on every freshly-computed grid field, and the difference is what tells a
    // chemist how much slider room the box has. Both are now DECLARED in the output
    // contract (field.box) instead of arriving in an ungoverned dict.
    if (present(box.iso_sized_for) && present(box.iso_fixed)
        && numbersDiffer(box.iso_sized_for, box.iso_fixed)) {
        const unit = present(units) ? ` ${units}` : '';
        caveats.push(`Box grown to keep the surface closed down to `
            + `${box.iso_sized_for}${unit} on the isovalue slider; the contour drawn at `
            + `the default position is ${box.iso_fixed}${unit} — lowering the slider past `
            + `the smaller number can reopen it.`);
    }
    if (box.contour_closes_in_box === false) {
        caveats.push('The isosurface is CLIPPED by the grid box: it does not close inside '
            + 'the volume, so a lobe that looks small here may simply be cut off.');
    }
    // The digest check, surfaced. Not a caveat about the chemistry — a caveat about how
    // much this client was able to verify, which is a different kind of fact and belongs
    // on screen rather than only in a console.
    if (view.digestVerified === false && view.digestUnverifiedReason) {
        caveats.push(`Artifact digest not verified: ${view.digestUnverifiedReason}`);
    }
    return { rows, caveats };
}

function renderMeta(view: FieldView | null) {
    const el = byId('field-meta');
    if (!el) return;
    if (!view) { el.innerHTML = ''; return; }
    const { rows, caveats } = buildMetaDisplay(view);
    el.innerHTML = rows.map(({ label, value }) =>
        `<div class="field-meta-row"><span>${label}</span><span>${value}</span></div>`).join('')
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
    fieldOverlay.clear();
    if (plugin && plugin.state.data.cells.has(REF_DATA)) {
        await plugin.build().delete(REF_DATA).commit();
    }
    renderMeta(null);
    updateIsoReadout();
    setButtonsEnabled();
}

async function renderCube(cubeText: string, kind: FieldKind, view?: FieldView) {
    // Adopt the backend's measured isovalue when it sent one; fall back to the
    // kind's constant otherwise. Set BEFORE the representations are built, or
    // the first frame is drawn at the old scale and corrected a tick later.
    lastView = view ?? null;
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

    // THE FIELD IS DRAWN AS LIGHT OVER THE SCENE, NOT AS SHELLS INSIDE IT.
    // What used to happen here: two or three VolumeRepresentation3D isosurfaces
    // per sign. They are the incumbent everywhere and they have one defect no
    // tuning fixes — they OCCLUDE THE LIGAND, and the only escape is to lower
    // alpha until the field stops being readable. The overlay is additive and
    // non-occluding by construction, so it composites over the finished frame
    // instead of competing for depth. See volume-overlay.ts for why that is the
    // correct model here and would not be for a representation that occludes.
    const wantOverlay = renderer !== 'shells';
    const mounted = wantOverlay && (fieldOverlay.active || fieldOverlay.mount(plugin));
    if (wantOverlay && !mounted) {
        // A dead overlay must not silently render nothing. Fall back to the
        // shells so the field still appears, and SAY which path drew it — a
        // fallback that hides the primary's death is how two dead cycles went
        // unnoticed the last time this repo built one.
        overlayFallback = fieldOverlay.lastError ?? 'overlay unavailable';
    } else {
        overlayFallback = null;
    }
    if (mounted) {
        syncOverlayTheme();
        if (!fieldOverlay.setVolume(activeVolume, currentIsoCube(), isoScaleFor(kind))) {
            overlayFallback = fieldOverlay.lastError ?? 'volume upload failed';
        }
    } else {
        fieldOverlay.clear();
    }
    if (!mounted || overlayFallback || renderer === 'both') {
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
    }
    frameFieldOnce();
    updateIsoReadout();
    renderOverlayControls();
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
    if (fieldOverlay.active && !overlayFallback) {
        fieldOverlay.setIso(currentIsoCube(), isoScaleFor(activeKind));
        // In `both` the shells must move WITH the overlay or the alignment
        // instrument stops measuring alignment and starts measuring the lag
        // between two isovalues — which would look exactly like misalignment.
        if (renderer !== 'both') { updateIsoReadout(); return; }
    }
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

/**
 * The reverse field. Goes to /field/region rather than /field, because it is
 * not a field OF the ligand: the ligand only supplies the box.
 */
async function requestPocketField() {
    if (!plugin || busy) return;
    clearRetry();
    const built = buildSurroundingsRequest(plugin, currentFocusOptions?.() ?? {});
    if ('error' in built) { setStatus(built.error, 'error'); return; }
    busy = true;
    setButtonsEnabled();
    setStatus(`Pocket field — ${built.sources.length} shell atoms as source, `
        + `sampled in the ligand's box…`, 'busy');
    try {
        // GROW THE FRAME UNTIL THE CONTOUR CLOSES. The ligand path does this
        // in the backend; here it cannot, because the frame belongs to the
        // caller by design — that separation is what keeps the grid
        // ligand-sized while the source is the whole pocket. So the caller
        // owns the growing too. Measured: a ligand box + 3 Å does not close a
        // pocket field, and the panel drew a flat cut face down one side.
        let payload: Record<string, unknown> = {};
        let framePad = 3;
        for (const pad of [3, 6, 9, 12]) {
            framePad = pad;
            const grown = buildSurroundingsRequest(
                plugin, currentFocusOptions?.() ?? {}, pad);
            if ('error' in grown) break;
            const resp = await fetch(`${BACKEND}/field/region`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sources: grown.sources, frame: grown.frame,
                                       kind: 'mep' }),
            });
            payload = await resp.json();
            if (!payload.ok) break;
            const meta = payload.meta as { contour_closes_in_box?: boolean };
            if (meta?.contour_closes_in_box !== false) break;
        }
        if (!payload.ok) { setStatus(String(payload.error), 'error'); return; }
        // /field/region is the LAST route still outside the kernel: it is not an
        // executable method in the catalog, so it answers in the flat v1 shape and is
        // lifted into the canonical view here. `regionExtras` carries the two facts the
        // output contract has no home for yet (waters), named so they cannot be mistaken
        // for contract-declared data. This block is deleted when the region route becomes
        // a method — and the fact that it is still here is the reminder that it has not.
        const pmeta = payload.meta as Record<string, any>;
        const pview: FieldView = {
            data: {
                field: {
                    kind: 'pocket_mep', native_units: pmeta.units,
                    grid: { dimensions: pmeta.dims, spacing_angstrom: pmeta.spacing },
                    extrema: { min: pmeta.vmin, max: pmeta.vmax },
                    single_signed: pmeta.single_signed ?? null,
                    box: {
                        iso_fixed: pmeta.iso_fixed ?? null,
                        iso_sized_for: pmeta.iso_sized_for ?? null,
                        contour_closes_in_box: pmeta.contour_closes_in_box ?? null,
                        pad_angstrom: pmeta.pad_used_angstrom ?? null,
                        wall_seconds: pmeta.wall_max ?? null,
                    },
                },
                model: {
                    charge_model: pmeta.charge_model ?? null,
                    net_charge: pmeta.net_charge ?? null,
                },
            },
            warnings: [pmeta.physics_caveat, pmeta.model_caveat]
                .filter(Boolean)
                .map((m: string) => ({ code: 'MODEL_CAVEAT', message: String(m) })),
            meta: { cache: pmeta.cache ?? null, seconds: pmeta.total_seconds ?? null,
                    version: pmeta.method_version ?? null,
                    provenance: { n_atoms: pmeta.natoms ?? null } },
            regionExtras: { waters_excluded: pmeta.waters_excluded ?? null,
                            waters_note: pmeta.waters_note ?? null },
            digestVerified: false,
            digestUnverifiedReason: '/field/region is still a v1 route: it returns the '
                + 'bytes inline with no artifact row, so there is no digest to check',
        };
        await renderCube(payload.cube as string, 'pocket_mep', pview);
        renderMeta(pview);
        // present() before interpolation — the same class of bug as the
        // meta-panel rows: a status line built with a bare template literal
        // cannot tell an absent/null charge_model from the literal word.
        const chargeModelNote = present(pmeta.charge_model)
            ? pmeta.charge_model : 'charge model not recorded';
        // The region route owns no padding — the FRAME is the caller's, which
        // is the whole point of the split — so it has no pad to report and the
        // status line printed "frame grown to ? Å". A question mark on screen
        // is the same defect as the `null` rows: a field the renderer had no
        // business asking for. Report the pad the CALLER used, which it knows.
        setStatus(`Pocket map from ${pmeta.n_sources_used} residue-shell atoms, `
            + `net charge ${pmeta.net_charge} · ${chargeModelNote} · frame `
            + `+${framePad} Å around the ligand.`, 'ok');
    } catch (e) {
        setStatus(`Backend unreachable — ${e instanceof Error ? e.message : String(e)}`, 'error');
    } finally {
        busy = false;
        setButtonsEnabled();
    }
}

/** Supplied by the lab so the shell obeys the SAME cutoff slider the semantic
 * layers use, rather than this facet inventing a second notion of "nearby". */
let currentFocusOptions: (() => Record<string, unknown>) | null = null;
export function setFieldFocusOptionsProvider(fn: () => Record<string, unknown>) {
    currentFocusOptions = fn;
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
            await renderCube(cached.cube, kind, cached.view);
            renderMeta(cached.view);
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
        await renderCube(entry.cube, kind, entry.view);
        renderMeta(entry.view);
        if (spec.quantum && typeof entry.view.meta?.seconds === 'number') {
            lastQuantumSeconds = Math.round(entry.view.meta.seconds);
        }
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
        backendOnline = true;
    } catch {
        backendOnline = false;
        // On the deployed site this is the NORMAL state, not a fault: the
        // daemons are unauthenticated quantum-chemistry servers and must not
        // be public. Saying "offline" alone would read as breakage.
        const baked = molfile ? await bakedField(molfile, 'mep', structureId ?? undefined) : null;
        el.textContent = baked
            ? 'precomputed fields — no backend needed'
            : 'backend offline — backend/env/bin/python backend/field_server.py';
        el.dataset.online = baked ? 'true' : 'false';
    }
}

/** Called once after the workbench (and its plugin) exists. */
export function initFieldWellsPanel(p: PluginContext) {
    plugin = p;
    document.querySelectorAll<HTMLButtonElement>('.field-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const kind = btn.dataset.field as FieldKind;
            if (kind === 'pocket_mep') { void requestPocketField(); return; }
            if (Kinds[kind]) void requestField(kind);
        });
    });
    byId<HTMLInputElement>('field-iso')?.addEventListener('input', () => void updateIsoSurfaces());
    byId<HTMLSelectElement>('field-style')?.addEventListener('change', (e) => {
        fieldOverlay.setStyle(+(e.target as HTMLSelectElement).value);
    });
    byId<HTMLSelectElement>('field-renderer')?.addEventListener('change', (e) => {
        renderer = (e.target as HTMLSelectElement).value as FieldRenderer;
        // Re-render rather than mutate: switching renderer changes which state
        // tree nodes must exist, and the cheapest correct way to get there is
        // the path that already builds them.
        if (activeKind) void requestField(activeKind);
    });
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
/**
 * The geometry moved — an atom was dragged, a pose was edited.
 *
 * A field is a function OF THE COORDINATES. The moment they change, the
 * surface on screen belongs to a molecule that no longer exists, and it is
 * still drawn in exactly the right place, registered to atoms that have moved.
 * That is the worst failure this codebase has a name for: not an error, a
 * picture that looks right.
 *
 * What can follow a drag and what cannot is a measured fact, not a preference:
 *
 *     mep      0.1 s     mlp   1.5 s        -> can recompute live
 *     homo    15 s       mep_qm  226 s      -> cannot, at any frame rate
 *
 * (226 s is lapatinib at 234 basis functions, measured today.) So the classical
 * pair recomputes on a debounce and the quantum four go STALE AND SAY SO. The
 * one thing not on offer is leaving a quantum surface up as if it still
 * described the molecule.
 */
export function invalidateFieldsForGeometry(nextMolfile: string | null) {
    molfile = nextMolfile;
    abortInFlight();
    cubeCache.clear();          // every entry was keyed to the old coordinates

    if (!activeKind) return;
    const spec = Kinds[activeKind];
    if (spec.quantum) {
        // Clear it. A quantum field cannot be re-solved at drag speed, and
        // dimming it or captioning it "stale" leaves a wrong surface on screen
        // for a reader who is looking at the molecule, not at the caption.
        const stale = activeKind;
        void clearField();
        setStatus(`Geometry changed — ${spec.label} cleared. It took `
            + `${lastQuantumSeconds ?? '15+'} s to solve and cannot follow a drag; `
            + `click it again when the pose is where you want it.`, 'idle');
        offerRetry(stale, budgetFor(stale));
        return;
    }
    // Classical: cheap enough to follow the geometry.
    if (geometryTimer !== null) window.clearTimeout(geometryTimer);
    geometryTimer = window.setTimeout(() => {
        geometryTimer = null;
        if (activeKind && !Kinds[activeKind].quantum) void requestField(activeKind);
    }, GEOMETRY_DEBOUNCE_MS);
}

/** Long enough that a drag does not queue a request per frame, short enough
 * that the field feels attached to the atom rather than chasing it. */
const GEOMETRY_DEBOUNCE_MS = 250;
let geometryTimer: number | null = null;
let lastQuantumSeconds: number | null = null;

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
    // Debug/warm hook: exposes the EXACT molfile the panel would send. The
    // durable cache is keyed on sha256(molfile), so a warmer that generates its
    // own molfile writes rows the app can never hit. Reading this one makes the
    // warm exact, and lets it use a budget no interactive click should have.
    (window as unknown as { diracFields: unknown }).diracFields =
        { molfile, label: ligandLabel, backend: BACKEND };
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
        // Re-ask the backend label now that a ligand exists. checkHealth runs at
        // init, when molfile is still null, so it cannot know whether this
        // structure has a bake — and it printed "backend offline, start it
        // with..." on a deployed site where that is the NORMAL state and there
        // is no shell to type it into. Looking broken and being broken are
        // different failures, and only one of them was real.
        void checkHealth();
        if (molfile) void prefetchAll();
    }
    if (!activeKind && !changed) {
        setStatus(molfile ? 'Pick a field to render its 3D well.' : 'Load a structure with a ligand first.', 'idle');
    }
    setButtonsEnabled();
}
