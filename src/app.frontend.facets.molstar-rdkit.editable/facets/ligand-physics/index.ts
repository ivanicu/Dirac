/**
 * Ligand physics facet — the two quantities that change a synthesis decision.
 *
 * Both were already computed by `backend/physics` (:8902) and had ZERO
 * consumers: a grep for `8902`, `/surface/mep` and `/torsion/strain` across
 * `src/` returned nothing but coincidental digit matches in vendored example
 * data. A clean-context medicinal chemist ranked them 9/10 and 9/10 — the only
 * two numbers in this repo that would change what goes into a synthesis queue
 * tomorrow — while the six volumetric fields that DO have a UI were ranked
 * 2/10 to 7/10. The useful half was built and never wired up.
 *
 *   σ-hole   V_S,max on the 0.001 a.u. isodensity surface, with the C–X···max
 *            angle that decides whether a positive cap is a σ-hole at all.
 *            Answers: is Cl→Br worth a synthesis?
 *   strain   MMFF94s relaxed bidirectional torsion scan, per rotor, with the
 *            observed conformer marked on its own curve.
 *            Answers: is this docked pose fiction?
 *
 * Separate daemon from the fields backend on purpose (see backend/physics/
 * server.py). This facet is a pure consumer and says so when the daemon is
 * down rather than pretending.
 */

import { PluginContext } from '../../../mol-plugin/context';
import { torsionPanel, type TorsionRow } from './plot';

const PHYSICS = `http://${window.location.hostname || '127.0.0.1'}:8902`;

/** The physics daemon's own budget, sent explicitly so the two cannot
 * disagree — the failure that held 22 cores for 36 minutes. */
const BUDGET_SECONDS = 90;
const clientTimeoutMs = (budget: number) => (budget * 2 + 20) * 1000;

interface SigmaHole {
    angle_deg: number;
    bonded_to: string;
    bonded_to_index: number;
    is_sigma_hole: boolean;
    belt_value_kcal_per_mol: number | null;
    anisotropy_kcal_per_mol?: number;
    positive_cap: boolean;
}

interface Extremum {
    kind: 'maximum' | 'minimum';
    value_kcal_per_mol: number;
    position: [number, number, number];
    atom_index: number;
    element: string;
    distance_to_atom_a: number;
    sigma_hole?: SigmaHole;
}

interface SurfaceMeta {
    basis: string;
    method: string;
    v_s_max_kcal_per_mol: number;
    v_s_min_kcal_per_mol: number;
    sigma_holes_found: number;
    absolute_uncertainty_pct: number;
    uncertainty_note?: string;
    n_surface_points: number;
    n_atoms: number;
    n_basis: number;
    scf_seconds: number;
    total_seconds: number;
    ran_on?: string;
    gpu_unavailable_reason?: string;
    ecp?: string[];
    charge: number;
    spin: number;
}

let plugin: PluginContext | null = null;
let molfile: string | null = null;
let ligandLabel: string | null = null;
let inFlight: AbortController | null = null;
let busy = false;
/**
 * Monotonic ligand generation. A multi-second SCF that lands after the user
 * has moved on must be DISCARDED, not rendered: it would put a confident
 * number from molecule A into molecule B's scene, which is the worst kind of
 * stale result because nothing about it looks wrong.
 *
 * field-wells has carried this guard since this morning; this facet, written
 * hours later, did not — the same "fix lands on one path of two" that bit the
 * iodine ECP and the SCF deadline today. Found by a peer's state audit.
 *
 * Exported so the physics-contract gate can mechanise ONE invariant — every
 * SCF-reaching path is bounded AND its result is checked before it lands —
 * instead of two, only one of which is currently enforced.
 */
let ligandGeneration = 0;
export function isCurrentLigandGeneration(g: number): boolean {
    return g === ligandGeneration;
}
/**
 * The extrema markers currently in the scene, so a re-run replaces them
 * instead of stacking. Typed loosely on purpose: the concrete
 * ShapeRepresentation type comes from a dynamic import inside markExtrema
 * (the mol-geo/mol-repr modules are heavy and this facet must not pull them
 * into the initial bundle), so it is not nameable at module scope.
 */
let lastMarkers: Parameters<NonNullable<PluginContext['canvas3d']>['remove']>[0] | null = null;

function byId<T extends HTMLElement>(id: string): T | null {
    return document.getElementById(id) as T | null;
}

function esc(s: string): string {
    return String(s).replace(/[&<>"]/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
}

/**
 * A value printed to its own uncertainty, never past it.
 *
 * `physics/README.md` measures the absolute uncertainty on V_S,max at ~25%
 * (basis 4–10%, method 3–22%) and says in its own words that printing
 * "+21.1 kcal/mol" to one decimal is false precision. The panel is the last
 * place that discipline can be enforced, so it is enforced here: round the
 * value to the size of its interval and show the interval.
 */
function withUncertainty(value: number, pct: number): string {
    const sigma = Math.abs(value) * pct / 100;
    const step = sigma >= 10 ? 10 : sigma >= 1 ? 1 : 0.1;
    const round = (x: number) => (Math.round(x / step) * step);
    const digits = step < 1 ? 1 : 0;
    const sign = value > 0 ? '+' : '';
    return `${sign}${round(value).toFixed(digits)} ± ${round(sigma).toFixed(digits)}`;
}

function setStatus(id: string, text: string, tone: 'idle' | 'busy' | 'ok' | 'error' = 'idle') {
    const el = byId(id);
    if (!el) return;
    el.textContent = text;
    el.dataset.tone = tone;
}

function setBusy(next: boolean) {
    busy = next;
    document.querySelectorAll<HTMLButtonElement>('.phys-btn').forEach(b => {
        b.disabled = busy || !molfile;
    });
    const cancel = byId<HTMLButtonElement>('phys-cancel');
    if (cancel) { cancel.hidden = !busy; cancel.disabled = false; }
}

async function post(path: string, body: Record<string, unknown>, budget: number) {
    const controller = new AbortController();
    inFlight = controller;
    const timer = setTimeout(() => controller.abort(), clientTimeoutMs(budget));
    try {
        const resp = await fetch(`${PHYSICS}${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...body, max_seconds: budget }),
            signal: controller.signal,
        });
        return await resp.json();
    } finally {
        clearTimeout(timer);
        if (inFlight === controller) inFlight = null;
    }
}

// ── σ-hole ──────────────────────────────────────────────────────────────────

/**
 * The extrema table.
 *
 * Ordered by |V|, halogens and chalcogens first regardless of magnitude: on
 * 4-bromobenzonitrile an N–H maximum outranks the bromine's σ-hole, and a
 * table sorted purely by value buries the one row the chemist opened this
 * panel to read. (The backend learned the same lesson — top-N truncation once
 * deleted every iodine σ-hole.)
 */
function extremaTable(extrema: Extremum[], pct: number): string {
    const scored = extrema.map(e => ({
        e,
        priority: e.sigma_hole ? 0 : 1,
        mag: Math.abs(e.value_kcal_per_mol),
    }));
    scored.sort((a, b) => a.priority - b.priority || b.mag - a.mag);

    const rows = scored.slice(0, 12).map(({ e }) => {
        const s = e.sigma_hole;
        const isHole = !!s?.is_sigma_hole;
        const angle = s ? `${s.angle_deg.toFixed(0)}°` : '—';
        const aniso = s?.anisotropy_kcal_per_mol !== undefined
            ? s.anisotropy_kcal_per_mol.toFixed(1) : '—';
        return `<tr data-sigma="${isHole}">
            <td>${esc(e.element)}${e.atom_index}</td>
            <td>${e.kind === 'maximum' ? 'V<sub>S,max</sub>' : 'V<sub>S,min</sub>'}</td>
            <td class="num">${withUncertainty(e.value_kcal_per_mol, pct)}</td>
            <td class="num">${angle}</td>
            <td class="num">${aniso}</td>
            <td>${isHole ? 'σ-hole' : (s ? 'no — angle too low' : '')}</td>
        </tr>`;
    }).join('');

    return `<table class="phys-table">
        <thead><tr>
          <th>atom</th><th>kind</th><th class="num">kcal/mol</th>
          <th class="num" title="C–X···max angle. Below 150° a positive cap is not a σ-hole.">C–X···max</th>
          <th class="num" title="Cap minus the same atom's perpendicular belt. Sign cannot classify an ion; anisotropy can.">anisotropy</th>
          <th></th>
        </tr></thead>
        <tbody>${rows}</tbody></table>`;
}

/** The last completed surface run, for consumers that need the numbers rather than the table. */
let lastSurface: { extrema: Extremum[], meta: SurfaceMeta } | null = null;
const surfaceListeners = new Set<(r: { extrema: Extremum[], meta: SurfaceMeta }) => void>();

export function getLastSurfaceResult() { return lastSurface; }
export function onSurfaceResult(cb: (r: { extrema: Extremum[], meta: SurfaceMeta }) => void) {
    surfaceListeners.add(cb);
    return () => surfaceListeners.delete(cb);
}
function publishSurfaceResult(extrema: Extremum[], meta: SurfaceMeta) {
    lastSurface = { extrema, meta };
    for (const cb of surfaceListeners) { try { cb(lastSurface); } catch (e) { console.error(e); } }
}

function surfacePanel(extrema: Extremum[], meta: SurfaceMeta): string {
    const pct = meta.absolute_uncertainty_pct ?? 25;
    const holes = meta.sigma_holes_found;
    const headline = holes
        ? `${holes} σ-hole${holes > 1 ? 's' : ''} found`
        : 'No σ-hole on this molecule';
    // A halogen with no σ-hole cannot make a halogen bond however good the
    // geometry looks — that absence IS the answer, so it is stated as a result
    // rather than shown as an empty table.
    return `
      <div class="phys-headline">
        <span class="phys-big">${withUncertainty(meta.v_s_max_kcal_per_mol, pct)}</span>
        <span class="phys-unit">kcal/mol V<sub>S,max</sub></span>
        <span class="phys-verdict" data-verdict="${holes ? 'notable' : 'negligible'}">${esc(headline)}</span>
      </div>
      <div class="phys-caveat">${esc(meta.uncertainty_note
        ?? `Absolute values carry ~${pct}% uncertainty; orderings are robust, magnitudes are not.`)}</div>
      ${extremaTable(extrema, pct)}
      <div class="phys-method">${esc(meta.method)}/${esc(meta.basis)}
        · 0.001 a.u. isodensity surface
        · ${esc(String(meta.n_surface_points))} points
        · charge ${esc(String(meta.charge))}, spin ${esc(String(meta.spin))}
        ${meta.ecp && meta.ecp.length ? `· ECP on ${esc(meta.ecp.join('/'))}` : ''}
        · ran on ${esc(meta.ran_on ?? 'cpu')}${meta.gpu_unavailable_reason
            ? ` (${esc(meta.gpu_unavailable_reason)})` : ''}
        · ${esc(String(meta.total_seconds))} s</div>`;
}

/**
 * Drop a marker at each extremum, in the molfile's own frame.
 *
 * The molfile carries scene coordinates, so a position from the backend is
 * already registered with the mol* scene and needs no alignment step. Markers
 * rather than a second molecular surface: mol* builds a better surface than
 * this facet should duplicate, and colouring ITS surface via /surface/mep_at
 * is the right next move — but a number in a table with no place in space is
 * the thing a chemist cannot act on, and a marker fixes that today.
 */
async function markExtrema(extrema: Extremum[]) {
    if (!plugin) return;
    const { Shape } = await import('../../../mol-model/shape');
    const { ShapeRepresentation } = await import('../../../mol-repr/shape/representation');
    const { Mesh } = await import('../../../mol-geo/geometry/mesh/mesh');
    const { MeshBuilder } = await import('../../../mol-geo/geometry/mesh/mesh-builder');
    const { addSphere } = await import('../../../mol-geo/geometry/mesh/builder/sphere');
    const { Color } = await import('../../../mol-util/color');
    const { Vec3 } = await import('../../../mol-math/linear-algebra');

    const shown = extrema.filter(e => e.sigma_hole || Math.abs(e.value_kcal_per_mol) > 5);
    const state = MeshBuilder.createState(512, 256);
    const groups: { color: number, label: string }[] = [];

    shown.forEach((e, i) => {
        state.currentGroup = i;
        // Radius carries |V| so the eye ranks them without reading the table,
        // floored so a weak-but-real σ-hole is still clickable.
        const r = Math.max(0.22, Math.min(0.55, Math.abs(e.value_kcal_per_mol) / 60));
        addSphere(state, Vec3.create(...e.position), r, 2);
        const positive = e.value_kcal_per_mol > 0;
        groups.push({
            color: positive ? 0x6a8cc0 : 0xbd777b,
            label: `${e.element}${e.atom_index} ${positive ? 'V_S,max' : 'V_S,min'} `
                + `${e.value_kcal_per_mol.toFixed(1)} kcal/mol`
                + (e.sigma_hole?.is_sigma_hole ? ` · σ-hole at ${e.sigma_hole.angle_deg}°` : ''),
        });
    });

    const mesh = MeshBuilder.getMesh(state);
    const repr = ShapeRepresentation(
        () => Shape.create('sigma-hole extrema', {}, mesh,
            (g: number) => Color(groups[g]?.color ?? 0x888888),
            () => 1,
            (g: number) => groups[g]?.label ?? ''),
        Mesh.Utils);
    // Alpha must clear the renderer's pickingAlphaThreshold (0.5) or the
    // markers render and refuse to be picked — the exact trap the
    // pharmacophore facet hit and documented.
    await repr.createOrUpdate({ alpha: 0.95 }).run();
    // Canvas3D.add, not a state-tree transformer. These markers are a
    // read-only annotation on a computed result — they are not part of the
    // molecule's state, must not survive a state snapshot, and are replaced
    // wholesale on the next run. The pharmacophore facet uses the state tree
    // because its features are USER DATA that has to persist and be picked
    // into an editing model; this is the other case, and using its machinery
    // would put a derived quantity into the document.
    if (lastMarkers) plugin.canvas3d?.remove(lastMarkers);
    plugin.canvas3d?.add(repr);
    lastMarkers = repr;
}

/**
 * The basis the refusal already prescribes.
 *
 * The cost gate declines a large ligand with a specific, correct remedy — "use a smaller
 * basis (sto-3g is ~31 s here)" — and until this control existed the panel offered no way
 * to take it. An honest refusal that names an action the interface cannot perform is a
 * dead end wearing the manners of a good error message; measured on lapatinib, which is
 * 66 atoms and 698 basis functions against a 90 s budget, that dead end was the only
 * outcome available.
 */
function selectedBasis(): string {
    const el = document.getElementById('phys-basis') as HTMLSelectElement | null;
    return el?.value || 'def2-svp';
}

async function runSurface() {
    if (!molfile || busy) return;
    setBusy(true);
    const basis = selectedBasis();
    setStatus('phys-surface-status',
        `Solving surface electrostatics on ${ligandLabel ?? 'ligand'} in ${basis}… `
        + `(gives up at ${BUDGET_SECONDS} s · Cancel to stop now)`, 'busy');
    const generation = ligandGeneration;
    try {
        const out = await post('/surface/mep', { molfile, basis }, BUDGET_SECONDS);
        if (!isCurrentLigandGeneration(generation)) {
            setStatus('phys-surface-status',
                'Ligand changed while solving — result discarded rather than '
                + 'rendered into the wrong molecule.', 'idle');
            return;
        }
        if (!out.ok) {
            setStatus('phys-surface-status', out.error, 'error');
            return;
        }
        const host = byId('phys-surface-body');
        if (host) host.innerHTML = surfacePanel(out.extrema, out.meta);
        // Publish, so the halogen audit can ask what the σ-hole POINTS AT without issuing a
        // second SCF for numbers that already exist. This panel answers "is there a hole and
        // how deep"; that one answers "is anything on the axis" — two halves of one question
        // that have historically lived in two different products.
        publishSurfaceResult(out.extrema, out.meta);
        setStatus('phys-surface-status',
            `Surface electrostatics for ${ligandLabel ?? 'ligand'}.`, 'ok');
        try { await markExtrema(out.extrema); } catch { /* table still stands */ }
    } catch (e) {
        const aborted = e instanceof Error && e.name === 'AbortError';
        setStatus('phys-surface-status', aborted
            ? 'Cancelled.'
            : `Physics daemon unreachable — backend/env/bin/python backend/physics/server.py`,
        aborted ? 'idle' : 'error');
    } finally {
        setBusy(false);
    }
}

// ── torsional strain ────────────────────────────────────────────────────────

async function runTorsion() {
    if (!molfile || busy) return;
    setBusy(true);
    setStatus('phys-torsion-status', `Scanning rotatable bonds on ${ligandLabel ?? 'ligand'}…`, 'busy');
    const generation = ligandGeneration;
    try {
        const out = await post('/torsion/strain', { molfile }, BUDGET_SECONDS);
        if (!isCurrentLigandGeneration(generation)) {
            setStatus('phys-torsion-status',
                'Ligand changed while scanning — result discarded rather than '
                + 'rendered into the wrong molecule.', 'idle');
            return;
        }
        if (!out.ok) {
            setStatus('phys-torsion-status', out.error, 'error');
            return;
        }
        const host = byId('phys-torsion-body');
        if (host) {
            host.innerHTML = torsionPanel(
                out.torsions as TorsionRow[], out.total_strain_kcal,
                out.total_verdict, out.meta);
        }
        setStatus('phys-torsion-status',
            `${out.meta.n_scanned} rotors scanned for ${ligandLabel ?? 'ligand'}.`, 'ok');
    } catch (e) {
        const aborted = e instanceof Error && e.name === 'AbortError';
        setStatus('phys-torsion-status', aborted
            ? 'Cancelled.'
            : 'Physics daemon unreachable — backend/env/bin/python backend/physics/server.py',
        aborted ? 'idle' : 'error');
    } finally {
        setBusy(false);
    }
}

// ── lifecycle ───────────────────────────────────────────────────────────────

async function checkHealth() {
    const el = byId('phys-backend');
    if (!el) return;
    try {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 1500);
        const resp = await fetch(`${PHYSICS}/health`, { signal: ctrl.signal });
        clearTimeout(timer);
        const h = await resp.json();
        el.textContent = `physics online · rdkit ${h.rdkit} · pyscf ${h.pyscf}`;
        el.dataset.online = 'true';
    } catch {
        el.textContent = 'physics offline — backend/env/bin/python backend/physics/server.py';
        el.dataset.online = 'false';
    }
}

export function initLigandPhysicsPanel(p: PluginContext) {
    plugin = p;
    byId('phys-run-surface')?.addEventListener('click', () => void runSurface());
    byId('phys-run-torsion')?.addEventListener('click', () => void runTorsion());
    byId('phys-cancel')?.addEventListener('click', () => {
        inFlight?.abort();
        inFlight = null;
        setBusy(false);
        setStatus('phys-surface-status', 'Cancelled. The daemon stops at its own deadline.', 'idle');
    });
    setBusy(false);
    void checkHealth();
}

/** Same contract as the other facets: the lab hands over the focused ligand's
 * molfile, already in scene coordinates. */
export function updateLigandPhysics(nextMolfile: string | null, label: string | null) {
    // A result belongs to the molecule it was computed on; carrying it across a ligand
    // switch is how a number ends up describing something it never saw.
    lastSurface = null;
    const changed = nextMolfile !== molfile;
    molfile = nextMolfile;
    ligandLabel = label;
    const summary = byId('phys-summary');
    if (summary) summary.textContent = molfile ? (label ?? 'Ligand') : 'No ligand loaded';
    if (changed) {
        ligandGeneration++;   // invalidates every in-flight result
        inFlight?.abort();
        inFlight = null;
        for (const id of ['phys-surface-body', 'phys-torsion-body']) {
            const host = byId(id);
            if (host) host.innerHTML = '';
        }
        setStatus('phys-surface-status', molfile
            ? 'Not computed — this is a QM run, so it is on demand.' : 'Load a ligand first.');
        setStatus('phys-torsion-status', molfile
            ? 'Not computed — force field, about a second.' : 'Load a ligand first.');
    }
    setBusy(false);
}
