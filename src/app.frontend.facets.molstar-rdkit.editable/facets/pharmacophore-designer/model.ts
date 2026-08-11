/**
 * Pharmacophore Designer — editable model state.
 *
 * The model is a set of pharmacophore features (HBA / HBD / aromatic /
 * hydrophobic) seeded from the focused ligand's RDKit perception (the same
 * `computePharmacophoreFeatures` the read-only 3D layer uses) and then owned
 * by the user: features can be moved (3D drag), resized (tolerance radius),
 * toggled, deleted, or added free-standing.
 *
 * Pure state + events. No DOM, no mol* imports beyond linear algebra, so the
 * model is trivially serializable and testable.
 */

import { Vec3 } from '../../../mol-math/linear-algebra';

export type DesignerFeatureKind = 'hba' | 'hbd' | 'aromatic' | 'hydrophobic';

export interface DesignerFeature {
    /** Stable id within one model; survives reordering and deletion. */
    readonly id: number;
    readonly kind: DesignerFeatureKind;
    readonly position: Vec3;
    /** Lone-pair / H direction for hba+hbd, ring normal for aromatic, null for hydrophobic. */
    readonly direction: Vec3 | null;
    /** Tolerance sphere radius in Å. */
    radius: number;
    /** Included in the screening query and rendered with its tolerance sphere. */
    enabled: boolean;
    readonly origin: 'ligand' | 'user';
}

export interface DesignerModelSource {
    readonly structureId: string | null;
    readonly ligandLabel: string | null;
}

/** Default tolerance radii (Å) per feature kind, LigandScout-style. */
export const DefaultFeatureRadius: Record<DesignerFeatureKind, number> = {
    hba: 1.0,
    hbd: 1.0,
    aromatic: 1.2,
    hydrophobic: 1.4,
};

export const FeatureKindLabel: Record<DesignerFeatureKind, string> = {
    hba: 'H-bond acceptor',
    hbd: 'H-bond donor',
    aromatic: 'Aromatic ring',
    hydrophobic: 'Hydrophobic',
};

export const FeatureKindShort: Record<DesignerFeatureKind, string> = {
    hba: 'HBA',
    hbd: 'HBD',
    aromatic: 'ARO',
    hydrophobic: 'HYD',
};

/** Same hues as the read-only pharmacophore layer — one color, one meaning (STYLE.md). */
export const FeatureKindColorHex: Record<DesignerFeatureKind, string> = {
    hba: '#e15555',
    hbd: '#4dabf7',
    aromatic: '#fab005',
    hydrophobic: '#868e96',
};

/**
 * Shape of the features returned by the shared substrate's
 * `computePharmacophoreFeatures` (its Feature interface is not exported;
 * this is its structural type).
 */
export interface SubstrateFeature {
    kind: DesignerFeatureKind;
    position: Vec3;
    direction?: Vec3;
    radius?: number;
    label: string;
}

/** 'composition' changes re-run screening; 'geometry' changes only move 3D shapes. */
export type ModelChangeKind = 'composition' | 'geometry';

export class PharmacophoreDesignerModel {
    private features: DesignerFeature[] = [];
    private nextId = 1;
    private listeners: Array<(change: ModelChangeKind) => void> = [];
    /** True once the user has edited; guards against auto-reseeding over user work. */
    dirty = false;
    source: DesignerModelSource = { structureId: null, ligandLabel: null };

    onChange(listener: (change: ModelChangeKind) => void): void {
        this.listeners.push(listener);
    }

    private emit(change: ModelChangeKind) {
        for (const l of this.listeners) l(change);
    }

    all(): readonly DesignerFeature[] {
        return this.features;
    }

    get(id: number): DesignerFeature | undefined {
        return this.features.find(f => f.id === id);
    }

    byIndex(index: number): DesignerFeature | undefined {
        return this.features[index];
    }

    indexOf(id: number): number {
        return this.features.findIndex(f => f.id === id);
    }

    isEmpty(): boolean {
        return this.features.length === 0;
    }

    enabledCountByKind(): Record<DesignerFeatureKind, number> {
        const counts: Record<DesignerFeatureKind, number> = { hba: 0, hbd: 0, aromatic: 0, hydrophobic: 0 };
        for (const f of this.features) if (f.enabled) counts[f.kind]++;
        return counts;
    }

    /** Replace the whole model from substrate perception. Clears the dirty flag. */
    seedFromLigand(features: readonly SubstrateFeature[], source: DesignerModelSource): void {
        this.features = features.map((f, i) => ({
            id: i + 1,
            kind: f.kind,
            position: Vec3.clone(f.position),
            direction: f.direction ? Vec3.clone(f.direction) : null,
            radius: f.kind === 'aromatic' && f.radius ? f.radius : DefaultFeatureRadius[f.kind],
            enabled: true,
            origin: 'ligand' as const,
        }));
        this.nextId = this.features.length + 1;
        this.source = source;
        this.dirty = false;
        this.emit('composition');
    }

    clear(): void {
        this.features = [];
        this.dirty = true;
        this.emit('composition');
    }

    add(kind: DesignerFeatureKind, position: Vec3): DesignerFeature {
        const feature: DesignerFeature = {
            id: this.nextId++,
            kind,
            position: Vec3.clone(position),
            direction: kind === 'hydrophobic' ? null : Vec3.create(0, 0, 1),
            radius: DefaultFeatureRadius[kind],
            enabled: true,
            origin: 'user',
        };
        this.features.push(feature);
        this.dirty = true;
        this.emit('composition');
        return feature;
    }

    remove(id: number): void {
        const index = this.indexOf(id);
        if (index < 0) return;
        this.features.splice(index, 1);
        this.dirty = true;
        this.emit('composition');
    }

    setEnabled(id: number, enabled: boolean): void {
        const f = this.get(id);
        if (!f || f.enabled === enabled) return;
        f.enabled = enabled;
        this.dirty = true;
        this.emit('composition');
    }

    setRadius(id: number, radius: number): void {
        const f = this.get(id);
        if (!f) return;
        f.radius = Math.min(3, Math.max(0.5, radius));
        this.dirty = true;
        this.emit('geometry');
    }

    moveTo(id: number, position: Vec3): void {
        const f = this.get(id);
        if (!f) return;
        Vec3.copy(f.position, position);
        this.dirty = true;
        this.emit('geometry');
    }

    /** Pairwise distances (Å) between enabled features, for the live matrix. */
    distanceMatrix(): { features: DesignerFeature[]; distances: number[][] } {
        const enabled = this.features.filter(f => f.enabled);
        const distances = enabled.map(a => enabled.map(b => Vec3.distance(a.position, b.position)));
        return { features: enabled, distances };
    }

    // === Serialization ===

    toJSON(): string {
        return JSON.stringify({
            format: 'dirac-pharmacophore-model',
            version: 1,
            source: this.source,
            features: this.features.map(f => ({
                id: f.id,
                kind: f.kind,
                position: [f.position[0], f.position[1], f.position[2]],
                direction: f.direction ? [f.direction[0], f.direction[1], f.direction[2]] : null,
                radius: f.radius,
                enabled: f.enabled,
                origin: f.origin,
            })),
        }, null, 2);
    }

    /** Returns an error string on malformed input, null on success. */
    fromJSON(text: string): string | null {
        let parsed: unknown;
        try {
            parsed = JSON.parse(text);
        } catch (e) {
            return `Not valid JSON: ${e instanceof Error ? e.message : String(e)}`;
        }
        const doc = parsed as { format?: string; version?: number; source?: DesignerModelSource; features?: unknown[] };
        if (doc.format !== 'dirac-pharmacophore-model') return 'Not a dirac-pharmacophore-model file.';
        if (doc.version !== 1) return `Unsupported model version ${doc.version}.`;
        if (!Array.isArray(doc.features)) return 'Missing features array.';

        const kinds: DesignerFeatureKind[] = ['hba', 'hbd', 'aromatic', 'hydrophobic'];
        const features: DesignerFeature[] = [];
        let maxId = 0;
        for (const raw of doc.features as Array<{ id?: number; kind?: string; position?: number[]; direction?: number[] | null; radius?: number; enabled?: boolean; origin?: string }>) {
            if (!raw || !kinds.includes(raw.kind as DesignerFeatureKind)) return `Unknown feature kind: ${raw?.kind}`;
            const p = raw.position;
            if (!Array.isArray(p) || p.length !== 3 || !p.every(v => Number.isFinite(v))) return 'Feature with malformed position.';
            const d = raw.direction;
            if (d !== null && d !== undefined && (!Array.isArray(d) || d.length !== 3 || !d.every(v => Number.isFinite(v)))) return 'Feature with malformed direction.';
            const id = typeof raw.id === 'number' && raw.id > 0 ? raw.id : features.length + 1;
            maxId = Math.max(maxId, id);
            features.push({
                id,
                kind: raw.kind as DesignerFeatureKind,
                position: Vec3.create(p[0], p[1], p[2]),
                direction: d ? Vec3.create(d[0], d[1], d[2]) : null,
                radius: typeof raw.radius === 'number' ? Math.min(3, Math.max(0.5, raw.radius)) : DefaultFeatureRadius[raw.kind as DesignerFeatureKind],
                enabled: raw.enabled !== false,
                origin: raw.origin === 'user' ? 'user' : 'ligand',
            });
        }
        this.features = features;
        this.nextId = maxId + 1;
        if (doc.source && typeof doc.source === 'object') {
            this.source = { structureId: doc.source.structureId ?? null, ligandLabel: doc.source.ligandLabel ?? null };
        }
        this.dirty = true;
        this.emit('composition');
        return null;
    }
}
