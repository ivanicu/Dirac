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
import { Volume } from '../../../mol-model/volume';
import { Color } from '../../../mol-util/color';

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
const SHELLS = [
    { fraction: 1.0, alpha: 0.55, emissive: 0.55, visuals: ['solid'] as string[] },
    // The cage is a WHISPER around the core, not a net over the scene: close
    // to the solid (0.62x) and faint, or the mesh buries both the molecule
    // and the surface it is supposed to be annotating.
    { fraction: 0.62, alpha: 0.14, emissive: 0.22, visuals: ['wireframe'] as string[] },
] as const;
const shellRef = (sign: 'pos' | 'neg', i: number) => `field-wells-repr-${sign}-${i}`;

export type FieldKind = 'mep' | 'mep_qm' | 'homo' | 'lumo' | 'density' | 'mlp';

interface KindSpec {
    label: string;
    iso: number;
    diverging: boolean;
    unit: string;
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
    mep: { label: 'Electrostatic well', iso: 8, diverging: true, unit: 'kcal/mol', posColor: 0x6788bc, negColor: 0xbd777b, quantum: false },
    mep_qm: { label: 'QM potential well', iso: 0.05, diverging: true, unit: 'Ha/e', posColor: 0x6788bc, negColor: 0xbd777b, quantum: true },
    homo: { label: 'HOMO', iso: 0.04, diverging: true, unit: 'amp', posColor: 0x7fc7a5, negColor: 0xa397d3, quantum: true },
    lumo: { label: 'LUMO', iso: 0.04, diverging: true, unit: 'amp', posColor: 0x7fc7a5, negColor: 0xa397d3, quantum: true },
    density: { label: 'e⁻ density', iso: 0.05, diverging: false, unit: 'e/Bohr³', posColor: 0xd8aa75, negColor: 0xd8aa75, quantum: true },
    // Default iso must sit BELOW the hydrophilic side's typical |min| (~0.06
    // on aspirin) or the cyan lobes never exist and the field looks all-grease.
    mlp: { label: 'Lipophilicity', iso: 0.05, diverging: true, unit: 'MLP', posColor: 0xd5b979, negColor: 0x74ccdd, quantum: false },
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
}

let plugin: PluginContext | null = null;
let molfile: string | null = null;
let ligandLabel: string | null = null;
let activeKind: FieldKind | null = null;
let activeVolume: Volume | null = null;
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

function molfileHeavyAtoms(mf: string): number {
    const counts = mf.split('\n')[3] ?? '';
    return parseInt(counts.slice(0, 3), 10) || 0;
}

/** Fetch one field into the browser cache (deduplicated). Throws on backend
 * refusal; returns null if the focused ligand changed while in flight. */
async function fetchField(kind: FieldKind, store = false): Promise<{ cube: string, meta: FieldMeta } | null> {
    const key = cacheKey(kind);
    if (!store) {
        const hit = cubeCache.get(key);
        if (hit) return hit;
        const pending = pendingFetch.get(key);
        if (pending) return pending;
    }
    const requestMolfile = molfile;
    const basis = currentBasis();
    const p = (async () => {
        try {
            const resp = await fetch(`${BACKEND}/field`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ molfile: requestMolfile, kind, basis, store }),
            });
            const payload = await resp.json();
            if (molfile !== requestMolfile) return null;
            if (!payload.ok) throw new Error(payload.error);
            const entry = { cube: payload.cube as string, meta: payload.meta as FieldMeta };
            cubeCache.set(key, entry);
            return entry;
        } finally {
            pendingFetch.delete(key);
        }
    })();
    pendingFetch.set(key, p);
    return p;
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
    const failed: string[] = [];
    for (const kind of kinds) {
        if (molfile !== startedFor) return;   // ligand changed mid-prefetch
        setPrefetchNote(`Precomputing fields ${done}/${kinds.length} — ${Kinds[kind].label}…`);
        try {
            await fetchField(kind);
        } catch {
            failed.push(Kinds[kind].label);   // honest per-kind refusals (e.g. Gasteiger NaN)
        }
        done++;
    }
    if (molfile !== startedFor) return;
    const skipNote = heavy > PREFETCH_QM_MAX_HEAVY ? ` (quantum on demand — ${heavy} heavy atoms)` : '';
    setPrefetchNote(failed.length
        ? `Fields cached in browser + auto-saved to DB; unavailable: ${failed.join(', ')}${skipNote}`
        : `All ${kinds.length} fields cached in browser + auto-saved to DB${skipNote}.`);
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

function currentIso(): number {
    return activeKind ? Kinds[activeKind].iso * isoMultiplier() : 0;
}

function updateIsoReadout() {
    const el = byId('field-iso-readout');
    if (!el) return;
    if (!activeKind) { el.textContent = ''; return; }
    const sign = Kinds[activeKind].diverging ? '±' : '';
    el.textContent = `${sign}${currentIso().toPrecision(3)} ${Kinds[activeKind].unit}`;
}

function setButtonsEnabled() {
    document.querySelectorAll<HTMLButtonElement>('.field-btn').forEach(btn => {
        btn.disabled = busy || !molfile;
        btn.dataset.active = String(btn.dataset.field === activeKind);
    });
}

function renderMeta(meta: FieldMeta | null) {
    const el = byId('field-meta');
    if (!el) return;
    if (!meta) { el.innerHTML = ''; return; }
    const rows: [string, string][] = [];
    if (meta.method) rows.push(['Method', meta.basis ? `${meta.method}/${meta.basis}` : meta.method]);
    if (meta.units) rows.push(['Units', meta.units]);
    if ((meta as { total_logp?: number }).total_logp !== undefined) {
        rows.push(['Crippen logP', (meta as { total_logp?: number }).total_logp!.toFixed(2)]);
    }
    if (meta.scf_energy_ha !== undefined) rows.push(['SCF energy', `${meta.scf_energy_ha.toFixed(4)} Ha`]);
    // One decimal, deliberately: Koopmans + minimal-basis errors are ~0.5-1 eV,
    // and a second decimal would put false precision in front of a chemist
    // (the physics session's absolute_uncertainty_pct lesson, applied here).
    if (meta.homo_ev !== undefined) rows.push(['HOMO', `≈${meta.homo_ev.toFixed(1)} eV`]);
    if (meta.lumo_ev !== undefined && meta.lumo_ev !== null) rows.push(['LUMO', `≈${meta.lumo_ev.toFixed(1)} eV`]);
    if (meta.net_charge !== undefined) rows.push(['Net charge', String(meta.net_charge)]);
    if (meta.natoms !== undefined) rows.push(['Atoms (with H)', String(meta.natoms)]);
    if (meta.nbasis !== undefined) rows.push(['Basis functions', String(meta.nbasis)]);
    if (meta.total_seconds !== undefined) rows.push(['Compute time', `${meta.total_seconds} s`]);
    el.innerHTML = rows.map(([k, v]) =>
        `<div class="field-meta-row"><span>${k}</span><span>${v}</span></div>`).join('');
}

function reprParams(kind: FieldKind, sign: 1 | -1, shell: number) {
    const spec = Kinds[kind];
    const s = SHELLS[shell];
    return createVolumeRepresentationParams(plugin!, activeVolume ?? undefined, {
        type: 'isosurface',
        typeParams: {
            isoValue: Volume.IsoValue.absolute(sign * currentIso() * s.fraction),
            visuals: s.visuals as ('solid' | 'wireframe')[],
            alpha: s.alpha,
            xrayShaded: s.visuals.includes('solid'),
            emissive: s.emissive,
        },
        color: 'uniform',
        colorParams: { value: Color(sign > 0 ? spec.posColor : spec.negColor) },
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

async function renderCube(cubeText: string, kind: FieldKind) {
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
    for (let i = 0; i < SHELLS.length; i++) {
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
    updateIsoReadout();
}

async function updateIsoSurfaces() {
    if (!plugin || !activeKind || !activeVolume) return;
    const update = plugin.build();
    for (let i = 0; i < SHELLS.length; i++) {
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

async function requestField(kind: FieldKind) {
    if (!plugin || !molfile || busy) return;
    const spec = Kinds[kind];
    const requestMolfile = molfile;

    // Browser-cache path: solved on import, swapping fields costs no network.
    const cached = cubeCache.get(cacheKey(kind));
    if (cached) {
        busy = true;
        setButtonsEnabled();
        try {
            await renderCube(cached.cube, kind);
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
        ? `Solving ${spec.label} — pyscf SCF on ${ligandLabel ?? 'ligand'}…`
        : `Computing ${spec.label}…`, 'busy');
    try {
        const entry = await fetchField(kind);
        if (entry === null || molfile !== requestMolfile) {
            setStatus('Ligand changed while computing — stale field discarded.', 'idle');
            return;
        }
        await renderCube(entry.cube, kind);
        renderMeta(entry.meta);
        setStatus(`${spec.label} rendered for ${ligandLabel ?? 'ligand'}.`, 'ok');
    } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        if (msg.includes('fetch')) {
            setStatus(`Backend unreachable — start it with: backend/env/bin/python backend/field_server.py (${msg})`, 'error');
        } else {
            setStatus(`Backend refused: ${msg}`, 'error');
            renderMeta(null);
        }
    } finally {
        busy = false;
        setButtonsEnabled();
    }
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
        setStatus('Field cleared.', 'idle');
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
        cubeCache.clear();
        setPrefetchNote('');
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
