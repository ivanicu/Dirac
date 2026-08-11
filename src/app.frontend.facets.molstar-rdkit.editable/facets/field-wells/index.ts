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

const BACKEND = 'http://127.0.0.1:8901';
const REF_DATA = 'field-wells-data';
const REF_VOLUME = 'field-wells-volume';
const REF_POS = 'field-wells-repr-pos';
const REF_NEG = 'field-wells-repr-neg';

export type FieldKind = 'mep' | 'mep_qm' | 'homo' | 'lumo' | 'density';

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
const Kinds: Record<FieldKind, KindSpec> = {
    mep: { label: 'Electrostatic well', iso: 8, diverging: true, unit: 'kcal/mol', posColor: 0x4f9dff, negColor: 0xff5f56, quantum: false },
    mep_qm: { label: 'QM potential well', iso: 0.05, diverging: true, unit: 'Ha/e', posColor: 0x4f9dff, negColor: 0xff5f56, quantum: true },
    homo: { label: 'HOMO', iso: 0.04, diverging: true, unit: 'amp', posColor: 0x59d0a5, negColor: 0xc792ea, quantum: true },
    lumo: { label: 'LUMO', iso: 0.04, diverging: true, unit: 'amp', posColor: 0x59d0a5, negColor: 0xc792ea, quantum: true },
    density: { label: 'e⁻ density', iso: 0.05, diverging: false, unit: 'e/Bohr³', posColor: 0xe8b45a, negColor: 0xe8b45a, quantum: true },
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
    if (meta.method) rows.push(['Method', `${meta.method}/${meta.basis}`]);
    if (meta.units) rows.push(['Units', meta.units]);
    if (meta.scf_energy_ha !== undefined) rows.push(['SCF energy', `${meta.scf_energy_ha.toFixed(4)} Ha`]);
    if (meta.homo_ev !== undefined) rows.push(['HOMO', `${meta.homo_ev.toFixed(2)} eV`]);
    if (meta.lumo_ev !== undefined && meta.lumo_ev !== null) rows.push(['LUMO', `${meta.lumo_ev.toFixed(2)} eV`]);
    if (meta.net_charge !== undefined) rows.push(['Net charge', String(meta.net_charge)]);
    if (meta.natoms !== undefined) rows.push(['Atoms (with H)', String(meta.natoms)]);
    if (meta.nbasis !== undefined) rows.push(['Basis functions', String(meta.nbasis)]);
    if (meta.total_seconds !== undefined) rows.push(['Compute time', `${meta.total_seconds} s`]);
    el.innerHTML = rows.map(([k, v]) =>
        `<div class="field-meta-row"><span>${k}</span><span>${v}</span></div>`).join('');
}

function reprParams(kind: FieldKind, sign: 1 | -1) {
    const spec = Kinds[kind];
    return createVolumeRepresentationParams(plugin!, activeVolume ?? undefined, {
        type: 'isosurface',
        typeParams: {
            isoValue: Volume.IsoValue.absolute(sign * currentIso()),
            alpha: 0.38,
            xrayShaded: true,
            emissive: 0.25,
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
    reprs.to(REF_VOLUME).apply(
        StateTransforms.Representation.VolumeRepresentation3D,
        reprParams(kind, 1), { ref: REF_POS });
    if (spec.diverging) {
        reprs.to(REF_VOLUME).apply(
            StateTransforms.Representation.VolumeRepresentation3D,
            reprParams(kind, -1), { ref: REF_NEG });
    }
    await reprs.commit();
    updateIsoReadout();
}

async function updateIsoSurfaces() {
    if (!plugin || !activeKind || !activeVolume) return;
    const spec = Kinds[activeKind];
    const update = plugin.build();
    update.to(REF_POS).update(
        StateTransforms.Representation.VolumeRepresentation3D, () => reprParams(activeKind!, 1));
    if (spec.diverging && plugin.state.data.cells.has(REF_NEG)) {
        update.to(REF_NEG).update(
            StateTransforms.Representation.VolumeRepresentation3D, () => reprParams(activeKind!, -1));
    }
    await update.commit();
    updateIsoReadout();
}

async function requestField(kind: FieldKind) {
    if (!plugin || !molfile || busy) return;
    const spec = Kinds[kind];
    // Captured at request time: if the focused ligand changes while the
    // backend is computing, the stale response must be discarded — rendering
    // it would place the PREVIOUS molecule's field into the new scene.
    const requestMolfile = molfile;
    busy = true;
    setButtonsEnabled();
    setStatus(spec.quantum
        ? `Solving ${spec.label} — pyscf SCF on ${ligandLabel ?? 'ligand'}…`
        : `Computing ${spec.label}…`, 'busy');
    try {
        const basis = byId<HTMLSelectElement>('field-basis')?.value ?? 'sto-3g';
        const resp = await fetch(`${BACKEND}/field`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ molfile: requestMolfile, kind, basis }),
        });
        const payload = await resp.json();
        if (molfile !== requestMolfile) {
            setStatus('Ligand changed while computing — stale field discarded.', 'idle');
            return;
        }
        if (!payload.ok) {
            setStatus(`Backend refused: ${payload.error}`, 'error');
            renderMeta(null);
            return;
        }
        await renderCube(payload.cube, kind);
        renderMeta(payload.meta);
        setStatus(`${spec.label} rendered for ${ligandLabel ?? 'ligand'}.`, 'ok');
    } catch (e) {
        setStatus(`Backend unreachable — start it with: backend/env/bin/python backend/field_server.py (${e instanceof Error ? e.message : e})`, 'error');
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
    if (changed && activeKind) {
        void clearField();
        setStatus('Ligand changed — previous field cleared.', 'idle');
    } else if (!activeKind) {
        setStatus(molfile ? 'Pick a field to render its 3D well.' : 'Load a structure with a ligand first.', 'idle');
    }
    setButtonsEnabled();
}
