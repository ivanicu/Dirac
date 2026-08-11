/**
 * Pharmacophore Designer facet.
 *
 * Drag-editable pharmacophore model over the focused ligand, with live SMARTS
 * screening of a shipped compound library.
 *
 * Integration contract (same shape as the Property Cockpit facet): the lab
 * calls `initPharmacophoreDesigner(plugin)` once after the workbench exists,
 * and `updatePharmacophoreDesigner(structure, focusOptions, source)` from the
 * same lifecycle point that refreshes the 2D depiction. Everything else —
 * model edits, 3D drag, screening, export — is owned here.
 *
 * Substrate reuse, not reimplementation: features are seeded by the shared
 * `computePharmacophoreFeatures`, library perception goes through the shared
 * `computeLigandChemistry`, and hit previews render through the shared
 * `LigandDepiction`. This facet adds only the editable model, the drag
 * controller, the screening loop, and the panel.
 */

import { PluginContext } from '../../../mol-plugin/context';
import { Sphere3D } from '../../../mol-math/geometry';
import { focusSphereKeepingSlab } from '../../camera-slab';
import { Structure } from '../../../mol-model/structure';
import { Vec3 } from '../../../mol-math/linear-algebra';
import { computePharmacophoreFeatures } from '../../../chemistry.backend.perception.rdkit-wasm.editable/pharmacophore-features';
import type { LigandFocusOptions } from '../../../chemistry.backend.perception.rdkit-wasm.editable/semantic-focus';
import { LigandDepiction, type AtomHighlight } from '../../../chemistry.backend.perception.rdkit-wasm.editable/ligand-depiction';
import {
    PharmacophoreDesignerModel,
    FeatureKindShort,
    FeatureKindLabel,
    FeatureKindColorHex,
    type DesignerFeature,
    type DesignerFeatureKind,
} from './model';
import { ScreeningEngine, type ScreeningVerdict } from './screening';
import { syncDesignerShape } from './shape';
import { installDesignerDrag } from './drag';

/** 2D highlight colors — the Ligand tab's established atom-channel hues. */
const Depiction2DColor: Record<DesignerFeatureKind, string> = {
    hba: '#e1a14e',
    hbd: '#5fd0c8',
    aromatic: '#c792ea',
    hydrophobic: '#868e96',
};

const KindOrder: readonly DesignerFeatureKind[] = ['hba', 'hbd', 'aromatic', 'hydrophobic'];

function byId<T extends HTMLElement>(id: string): T | null {
    return document.getElementById(id) as T | null;
}

function esc(text: string): string {
    return text.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));
}

class PharmacophoreDesigner {
    readonly model = new PharmacophoreDesignerModel();
    private readonly engine = new ScreeningEngine();
    private plugin: PluginContext | null = null;
    private dragHandle: { dispose(): void } | null = null;
    private show3d = true;
    private customSmarts = '';
    private smartsDebounce = 0;
    private selectedHitId: string | null = null;
    private screeningToken = 0;
    private hasLigand = false;

    init(plugin: PluginContext): void {
        this.plugin = plugin;
        this.dragHandle?.dispose();
        this.dragHandle = installDesignerDrag(plugin, {
            getFeature: index => this.model.byIndex(index),
            onDragMove: (index, position) => {
                const f = this.model.byIndex(index);
                if (f) this.model.moveTo(f.id, position);
            },
            onDragEnd: () => this.renderSummary(),
        });

        this.model.onChange(change => {
            void this.syncShape();
            this.renderDistances();
            this.renderSummary();
            if (change === 'composition') {
                this.renderFeatureList();
                void this.runScreening();
            }
        });

        this.wireControls();
    }

    /** Lab lifecycle hook: called whenever the focused ligand may have changed. */
    async update(structure: Structure | null, options: LigandFocusOptions, source: { structureId: string | null; ligandLabel: string | null }): Promise<void> {
        if (!structure || !source.ligandLabel) {
            this.hasLigand = false;
            if (!this.model.isEmpty()) this.model.clear();
            this.model.source = source;
            this.model.dirty = false;
            this.renderSummary();
            this.renderFeatureList();
            void this.runScreening();
            return;
        }

        this.hasLigand = true;
        const sameSource = this.model.source.structureId === source.structureId
            && this.model.source.ligandLabel === source.ligandLabel;
        if (sameSource && this.model.dirty) {
            // User owns the model; never overwrite edits from a lifecycle refresh.
            await this.syncShape();
            return;
        }

        const features = await computePharmacophoreFeatures(structure, options);
        this.model.seedFromLigand(features, source);
    }

    // === 3D ===

    private async syncShape(): Promise<void> {
        if (!this.plugin) return;
        try {
            await syncDesignerShape(this.plugin, this.model.all(), this.show3d && this.hasLigand);
        } catch (error) {
            console.error('pharmacophore-designer shape sync failed', error);
        }
    }

    private focusFeature(f: DesignerFeature): void {
        if (!this.plugin) return;
        focusSphereKeepingSlab(this.plugin, { center: f.position, radius: Math.max(f.radius * 3, 6) } as Sphere3D, { durationMs: 250 });
    }

    // === Panel rendering ===

    private renderSummary(): void {
        const summary = byId('designer-summary');
        const stats = byId('designer-summary-stats');
        if (!summary || !stats) return;
        if (!this.hasLigand) {
            summary.textContent = 'No deposited ligand';
            stats.textContent = '';
            return;
        }
        const src = this.model.source;
        summary.textContent = `${src.structureId ?? '?'} · ${src.ligandLabel ?? '?'}${this.model.dirty ? ' · edited' : ' · as perceived'}`;
        const counts = this.model.enabledCountByKind();
        stats.textContent = KindOrder.map(k => `${counts[k]} ${FeatureKindShort[k]}`).join(' · ');
    }

    private renderFeatureList(): void {
        const list = byId('designer-feature-list');
        if (!list) return;
        list.replaceChildren();
        if (this.model.isEmpty()) {
            const empty = document.createElement('p');
            empty.className = 'ledger-empty';
            empty.textContent = this.hasLigand
                ? 'Model is empty. Reset from ligand or add features below.'
                : 'Switch to a structure with a deposited ligand to seed a pharmacophore model.';
            list.appendChild(empty);
            return;
        }

        for (const f of this.model.all()) {
            const row = document.createElement('div');
            row.className = 'designer-feature';
            row.dataset.enabled = String(f.enabled);

            const toggle = document.createElement('input');
            toggle.type = 'checkbox';
            toggle.checked = f.enabled;
            toggle.setAttribute('aria-label', `Include ${FeatureKindLabel[f.kind]} F${f.id} in query`);
            toggle.addEventListener('change', () => this.model.setEnabled(f.id, toggle.checked));

            const chip = document.createElement('i');
            chip.className = 'designer-chip';
            chip.style.background = FeatureKindColorHex[f.kind];

            const label = document.createElement('button');
            label.type = 'button';
            label.className = 'designer-feature-label';
            label.title = 'Focus camera on this feature';
            label.innerHTML = `<strong>${FeatureKindShort[f.kind]}</strong> F${f.id} <small>${f.origin === 'ligand' ? 'from ligand' : 'user'}</small>`;
            label.addEventListener('click', () => this.focusFeature(f));

            const radius = document.createElement('input');
            radius.type = 'range';
            radius.min = '0.5';
            radius.max = '3';
            radius.step = '0.1';
            radius.value = String(f.radius);
            radius.setAttribute('aria-label', `Tolerance radius for F${f.id}`);
            const radiusOut = document.createElement('output');
            radiusOut.textContent = `${f.radius.toFixed(1)} Å`;
            radius.addEventListener('input', () => {
                this.model.setRadius(f.id, Number(radius.value));
                radiusOut.textContent = `${Number(radius.value).toFixed(1)} Å`;
            });

            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'designer-remove';
            remove.textContent = '×';
            remove.title = `Delete F${f.id}`;
            remove.addEventListener('click', () => this.model.remove(f.id));

            row.append(toggle, chip, label, radius, radiusOut, remove);
            list.appendChild(row);
        }
    }

    private renderDistances(): void {
        const target = byId('designer-distances');
        if (!target) return;
        const { features, distances } = this.model.distanceMatrix();
        if (features.length < 2) {
            target.innerHTML = '<p class="ledger-empty">Enable at least two features to measure inter-feature distances.</p>';
            return;
        }
        let html = '<table class="designer-matrix"><thead><tr><th></th>';
        for (const f of features) html += `<th style="color:${FeatureKindColorHex[f.kind]}">F${f.id}</th>`;
        html += '</tr></thead><tbody>';
        for (let i = 0; i < features.length; i++) {
            html += `<tr><th style="color:${FeatureKindColorHex[features[i].kind]}">F${features[i].id}</th>`;
            for (let j = 0; j < features.length; j++) {
                html += i < j ? `<td>${distances[i][j].toFixed(1)}</td>` : '<td class="designer-matrix-void">·</td>';
            }
            html += '</tr>';
        }
        html += '</tbody></table>';
        target.innerHTML = html;
    }

    // === Screening ===

    private async runScreening(): Promise<void> {
        const summary = byId('designer-screen-summary');
        const hits = byId('designer-hits');
        const smartsError = byId('designer-smarts-error');
        if (!summary || !hits) return;

        const required = this.model.enabledCountByKind();
        const anyRequirement = KindOrder.some(k => required[k] > 0) || this.customSmarts.trim().length > 0;
        if (!anyRequirement) {
            summary.textContent = 'Empty query — enable features or add a SMARTS constraint.';
            hits.replaceChildren();
            return;
        }

        const token = ++this.screeningToken;
        summary.textContent = 'Screening library…';
        try {
            const result = await this.engine.screen({ required, smarts: this.customSmarts || null });
            if (token !== this.screeningToken) return; // superseded by a newer query
            summary.textContent = `${result.matchCount} / ${result.screenedCount} library molecules match`
                + (result.invalidCount > 0 ? ` · ${result.invalidCount} unparseable excluded` : '');
            if (smartsError) smartsError.textContent = result.smartsError ?? '';
            this.renderHits(result.verdicts, required);
        } catch (error) {
            summary.textContent = `Screening failed · ${error instanceof Error ? error.message : String(error)}`;
        }
    }

    private renderHits(verdicts: ScreeningVerdict[], required: Record<DesignerFeatureKind, number>): void {
        const hits = byId('designer-hits');
        if (!hits) return;
        hits.replaceChildren();

        for (const v of verdicts) {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'designer-hit';
            row.dataset.match = String(v.matches);
            row.dataset.active = String(v.entry.id === this.selectedHitId);

            const name = document.createElement('strong');
            name.textContent = v.entry.name;
            const category = document.createElement('small');
            category.textContent = v.entry.category;

            const chips = document.createElement('span');
            chips.className = 'designer-hit-chips';
            for (const kind of KindOrder) {
                if (required[kind] === 0) continue;
                const chip = document.createElement('em');
                chip.dataset.pass = String(v.satisfied[kind]);
                chip.textContent = `${FeatureKindShort[kind]} ${v.counts[kind]}/${required[kind]}`;
                chips.appendChild(chip);
            }
            if (v.smartsMatched !== null) {
                const chip = document.createElement('em');
                chip.dataset.pass = String(v.smartsMatched);
                chip.textContent = 'SMARTS';
                chips.appendChild(chip);
            }

            row.append(name, category, chips);
            row.addEventListener('click', () => {
                this.selectedHitId = v.entry.id;
                hits.querySelectorAll<HTMLElement>('.designer-hit').forEach(el => { el.dataset.active = 'false'; });
                row.dataset.active = 'true';
                void this.renderHitPreview(v.entry.id);
            });
            hits.appendChild(row);
        }
    }

    private async renderHitPreview(entryId: string): Promise<void> {
        const target = byId('designer-hit-preview');
        const caption = byId('designer-hit-preview-caption');
        if (!target || !caption) return;
        const screened = this.engine.getEntry(entryId);
        if (!screened || !screened.valid || !screened.molblock || !screened.chemistry) {
            target.innerHTML = '<p class="ledger-empty">No depiction available.</p>';
            caption.textContent = '';
            return;
        }

        // Priority when one atom carries several channels: HYD < ARO < HBD < HBA
        // (later writes win in the highlight map).
        const highlights: AtomHighlight[] = [];
        const pushFlags = (flags: Uint8Array | null, kind: DesignerFeatureKind) => {
            if (!flags) return;
            for (let i = 0; i < flags.length; i++) {
                if (flags[i]) highlights.push({ atomIndex: i, color: Depiction2DColor[kind], alpha: 0.5 });
            }
        };
        pushFlags(screened.hydrophobicFlags, 'hydrophobic');
        pushFlags(screened.chemistry.aromaticAtoms, 'aromatic');
        pushFlags(screened.chemistry.donors, 'hbd');
        pushFlags(screened.chemistry.acceptors, 'hba');

        const result = await LigandDepiction.depict(screened.molblock, { atomHighlights: highlights, width: 340, height: 200 });
        if (!result) {
            target.innerHTML = '<p class="ledger-empty">RDKit depiction failed for this molecule.</p>';
            caption.textContent = '';
            return;
        }
        target.innerHTML = result.svgString;
        const c = screened.counts!;
        caption.textContent = `${screened.entry.name} · ${esc(screened.entry.smiles)} · ${c.hba} HBA · ${c.hbd} HBD · ${c.aromatic} rings · ${c.hydrophobic} hydrophobic C`;
    }

    // === Controls ===

    private wireControls(): void {
        for (const kind of KindOrder) {
            byId(`designer-add-${kind}`)?.addEventListener('click', () => {
                const position = this.newFeaturePosition();
                const f = this.model.add(kind, position);
                this.focusFeature(f);
            });
        }

        byId('designer-reset')?.addEventListener('click', () => {
            this.model.dirty = false;
            // Re-seeding runs through the lab's next lifecycle tick; trigger it
            // directly from the last known structure.
            void this.reseedFromCurrentStructure();
        });

        byId('designer-clear')?.addEventListener('click', () => this.model.clear());

        byId('designer-export')?.addEventListener('click', () => this.exportModel());

        const importInput = byId<HTMLInputElement>('designer-import-file');
        byId('designer-import')?.addEventListener('click', () => importInput?.click());
        importInput?.addEventListener('change', async () => {
            const file = importInput.files?.[0];
            importInput.value = '';
            if (!file) return;
            const error = this.model.fromJSON(await file.text());
            const summary = byId('designer-summary');
            if (error && summary) summary.textContent = `Import failed · ${error}`;
        });

        const show3d = byId<HTMLInputElement>('designer-show-3d');
        show3d?.addEventListener('change', () => {
            this.show3d = show3d.checked;
            void this.syncShape();
        });

        const smarts = byId<HTMLInputElement>('designer-smarts');
        smarts?.addEventListener('input', () => {
            this.customSmarts = smarts.value;
            window.clearTimeout(this.smartsDebounce);
            this.smartsDebounce = window.setTimeout(() => void this.runScreening(), 300);
        });
    }

    private newFeaturePosition(): Vec3 {
        // Place new features at the model centroid (or camera target) plus a
        // small offset so consecutive adds do not stack invisibly.
        const features = this.model.all();
        const p = Vec3();
        if (features.length > 0) {
            for (const f of features) Vec3.add(p, p, f.position);
            Vec3.scale(p, p, 1 / features.length);
            Vec3.add(p, p, Vec3.create(2, 0.5 * (features.length % 4), 0));
        } else if (this.plugin?.canvas3d) {
            Vec3.copy(p, this.plugin.canvas3d.camera.target);
        }
        return p;
    }

    private async reseedFromCurrentStructure(): Promise<void> {
        const structure = this.plugin?.managers.structure.component.pivotStructure?.cell.obj?.data;
        if (!structure) return;
        const features = await computePharmacophoreFeatures(structure, this.lastFocusOptions);
        this.model.seedFromLigand(features, this.model.source);
    }

    /** Kept in sync by update() so Reset can re-derive without lab involvement. */
    lastFocusOptions: LigandFocusOptions = {};

    private exportModel(): void {
        const blob = new Blob([this.model.toJSON()], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const src = this.model.source;
        a.href = url;
        a.download = `pharmacophore-model-${src.structureId ?? 'model'}-${src.ligandLabel ?? 'ligand'}.json`;
        a.click();
        URL.revokeObjectURL(url);
    }
}

const designer = new PharmacophoreDesigner();
// Debug hook for e2e tests (same convention as window.molecularVfxLab).
(window as unknown as { pharmacophoreDesigner: PharmacophoreDesigner }).pharmacophoreDesigner = designer;

export function initPharmacophoreDesigner(plugin: PluginContext): void {
    designer.init(plugin);
}

export async function updatePharmacophoreDesigner(
    structure: Structure | null,
    options: LigandFocusOptions,
    source: { structureId: string | null; ligandLabel: string | null },
): Promise<void> {
    designer.lastFocusOptions = options;
    await designer.update(structure, options, source);
}
