/**
 * 3D pharmacophore feature overlay for a deposited ligand in an existing
 * Mol* scene. Computes H-bond acceptor (HBA), H-bond donor (HBD), aromatic
 * ring, and hydrophobic features from RDKit chemistry perception and renders
 * them as primitive geometry in the same 3D space as the ligand.
 *
 * Visual channel allocation (orthogonal to atom color):
 *   HBA  = small red cone in lone-pair direction (radiusTop=0, ~1 Å long)
 *   HBD  = small blue stick in H-bond direction (~1 Å long)
 *   ARO  = thin amber disk through ring center, perpendicular to ring plane
 *   HYD  = hazy grey sphere around the hydrophobic atom
 *
 * Each feature is its own mesh group, so groupId === feature index and the
 * picker returns the originating feature. The Shape is attached as a tagged
 * state node so it can be removed cleanly when the layer is disabled.
 */

import { PluginContext } from '../mol-plugin/context';
import { StateTransforms } from '../mol-plugin-state/transforms';
import { StateSelection, StateTransformer } from '../mol-state';
import { Color } from '../mol-util/color';
import { ParamDefinition as PD } from '../mol-util/param-definition';
import { Task } from '../mol-task';
import { Mat4, Vec3 } from '../mol-math/linear-algebra';
import { Mesh } from '../mol-geo/geometry/mesh/mesh';
import { MeshBuilder } from '../mol-geo/geometry/mesh/mesh-builder';
import { addSimpleCylinder, BasicCylinderProps } from '../mol-geo/geometry/mesh/builder/cylinder';
import { addSphere } from '../mol-geo/geometry/mesh/builder/sphere';
import { Circle } from '../mol-geo/primitive/circle';
import { Shape } from '../mol-model/shape';
import { PluginStateObject as SO } from '../mol-plugin-state/objects';
import { Structure, StructureElement } from '../mol-model/structure';
import { computeLigandChemistry } from './semantic-chemistry-rdkit';
import { ligandLociToMolfile, lociFromFocusOptions, extractLigandAtomData } from './ligand-pipeline';
import type { LigandFocusOptions } from './semantic-focus';

export type PharmacophoreLayerId = 'pharmacophore-features-rdkit';

export interface PharmacophoreLayerDefinition {
    readonly id: PharmacophoreLayerId;
    readonly label: string;
    readonly cost: 'medium';
    readonly source: string;
    readonly description: string;
}

export const PharmacophoreLayers: readonly PharmacophoreLayerDefinition[] = Object.freeze([
    {
        id: 'pharmacophore-features-rdkit',
        label: 'Pharmacophore features · 3D',
        cost: 'medium',
        source: 'RDKit Lipinski + aromatic perception, rendered as mol* Shape primitives',
        description: 'Draws directional H-bond acceptor cones (red), donor sticks (blue), aromatic ring disks (amber), and hydrophobic halos (grey) computed from the focused ligand. Orthogonal to atom color; visualizes the ligand\'s recognition profile.',
    },
]);

const PharmacophoreTag = 'mol-plugin-chem-pharmacophore';

type FeatureKind = 'hba' | 'hbd' | 'aromatic' | 'hydrophobic';

interface Feature {
    kind: FeatureKind;
    position: Vec3;
    direction?: Vec3;
    radius?: number;
    label: string;
}

const COLOR_HBA = Color(0xe15555);
const COLOR_HBD = Color(0x4dabf7);
const COLOR_ARO = Color(0xfab005);
const COLOR_HYD = Color(0x868e96);

const TMP_UP = Vec3.create(0, 1, 0);
const TMP_TARGET = Vec3();
const TMP_MAT = Mat4();
const TMP_BASE = Vec3();
const TMP_APEX = Vec3();

/**
 * Compute pharmacophore features from the focused ligand. The atom-index
 * contract from semantic-chemistry-rdkit is preserved: features reference
 * the same indices used by the molfile and LigandChemistry arrays.
 */
export async function computePharmacophoreFeatures(structure: Structure, options: LigandFocusOptions): Promise<Feature[]> {
    const loci = lociFromFocusOptions(structure, options);
    if (StructureElement.Loci.isEmpty(loci)) return [];

    const data = extractLigandAtomData(loci);
    if (!data) return [];
    const { atomPositions, atomElements, bonds } = data;

    const molfile = ligandLociToMolfile(loci);
    if (!molfile) return [];
    const chemistry = await computeLigandChemistry(molfile.molfile, atomPositions.length);
    if (!chemistry) return [];

    const features: Feature[] = [];
    const neighborMap = buildNeighborMap(bonds);

    // HBA: cone in the lone-pair direction.
    // Direction heuristic: outward from the average of bonded neighbors.
    for (let i = 0; i < atomPositions.length; i++) {
        if (!chemistry.acceptors[i]) continue;
        const dir = outwardDirection(i, neighborMap, atomPositions);
        if (!dir) continue;
        features.push({
            kind: 'hba',
            position: Vec3.clone(atomPositions[i]),
            direction: dir,
            label: 'H-bond acceptor',
        });
    }

    // HBD: small stick in the donor's H-bond direction (same outward heuristic).
    for (let i = 0; i < atomPositions.length; i++) {
        if (!chemistry.donors[i]) continue;
        const dir = outwardDirection(i, neighborMap, atomPositions);
        if (!dir) continue;
        features.push({
            kind: 'hbd',
            position: Vec3.clone(atomPositions[i]),
            direction: dir,
            label: 'H-bond donor',
        });
    }

    // Aromatic: one disk per SSSR ring reported by RDKit. This correctly
    // handles fused ring systems (naphthalene, indole, purine) where the
    // connected-component approach overgroups atoms into a single blob.
    if (chemistry.aromaticRings && chemistry.aromaticRings.length > 0) {
        for (const ringAtomIds of chemistry.aromaticRings) {
            const center = centroid(ringAtomIds, atomPositions);
            if (!center) continue;
            const normal = ringNormal(ringAtomIds, atomPositions, center);
            if (!normal) continue;
            const radius = ringRadius(ringAtomIds, atomPositions, center);
            features.push({
                kind: 'aromatic',
                position: center,
                direction: normal,
                radius: Math.max(radius * 0.9, 0.8),
                label: 'Aromatic ring',
            });
        }
    } else {
        // Fallback: if RDKit didn't report rings (shouldn't happen), group
        // aromatic atoms by connected component as a degraded approximation.
        const rings = findAromaticRings(chemistry.aromaticAtoms, neighborMap);
        for (const ringAtomIds of rings) {
            const center = centroid(ringAtomIds, atomPositions);
            if (!center) continue;
            const normal = ringNormal(ringAtomIds, atomPositions, center);
            if (!normal) continue;
            const radius = ringRadius(ringAtomIds, atomPositions, center);
            features.push({
                kind: 'aromatic',
                position: center,
                direction: normal,
                radius: Math.max(radius * 0.9, 0.8),
                label: 'Aromatic ring (fallback)',
            });
        }
    }

    // Hydrophobic: halo around carbons with no N/O neighbors.
    for (let i = 0; i < atomPositions.length; i++) {
        if (atomElements[i] !== 'C') continue;
        const neighbors = neighborMap.get(i) ?? [];
        if (neighbors.some(j => atomElements[j] === 'N' || atomElements[j] === 'O')) continue;
        features.push({
            kind: 'hydrophobic',
            position: Vec3.clone(atomPositions[i]),
            radius: 1.4,
            label: 'Hydrophobic',
        });
    }

    return features;
}

// === Loci → atoms + bonds ===




// === Geometry helpers ===

function buildNeighborMap(bonds: Array<{ a1: number; a2: number }>): Map<number, number[]> {
    const map = new Map<number, number[]>();
    for (const b of bonds) {
        if (!map.has(b.a1)) map.set(b.a1, []);
        if (!map.has(b.a2)) map.set(b.a2, []);
        map.get(b.a1)!.push(b.a2);
        map.get(b.a2)!.push(b.a1);
    }
    return map;
}

function outwardDirection(atomIdx: number, neighborMap: Map<number, number[]>, positions: Vec3[]): Vec3 | null {
    const neighbors = neighborMap.get(atomIdx);
    if (!neighbors || neighbors.length === 0) return null;
    const center = Vec3.clone(positions[atomIdx]);
    const avg = Vec3.create(0, 0, 0);
    for (const n of neighbors) Vec3.add(avg, avg, positions[n]);
    Vec3.scale(avg, avg, 1 / neighbors.length);
    Vec3.sub(avg, avg, center);
    const len = Vec3.magnitude(avg);
    if (len < 1e-4) return null;
    Vec3.scale(avg, avg, 1 / len);
    return avg;
}

function findAromaticRings(aromaticFlags: Uint8Array, neighborMap: Map<number, number[]>): number[][] {
    const visited = new Set<number>();
    const rings: number[][] = [];
    for (let i = 0; i < aromaticFlags.length; i++) {
        if (!aromaticFlags[i] || visited.has(i)) continue;
        // BFS to find connected aromatic atoms
        const ring: number[] = [];
        const queue = [i];
        visited.add(i);
        while (queue.length) {
            const cur = queue.shift()!;
            ring.push(cur);
            for (const n of neighborMap.get(cur) ?? []) {
                if (aromaticFlags[n] && !visited.has(n)) {
                    visited.add(n);
                    queue.push(n);
                }
            }
        }
        if (ring.length >= 5 && ring.length <= 7) rings.push(ring);
    }
    return rings;
}

function centroid(indices: number[], positions: Vec3[]): Vec3 | null {
    if (indices.length === 0) return null;
    const c = Vec3.create(0, 0, 0);
    for (const i of indices) Vec3.add(c, c, positions[i]);
    Vec3.scale(c, c, 1 / indices.length);
    return c;
}

function ringNormal(indices: number[], positions: Vec3[], center: Vec3): Vec3 | null {
    if (indices.length < 3) return null;
    // Best-fit normal via cross product of two edges from the centroid.
    const v1 = Vec3.sub(Vec3(), positions[indices[0]], center);
    const v2 = Vec3.sub(Vec3(), positions[indices[1]], center);
    const n = Vec3.cross(Vec3(), v1, v2);
    const len = Vec3.magnitude(n);
    if (len < 1e-4) return null;
    Vec3.scale(n, n, 1 / len);
    return n;
}

function ringRadius(indices: number[], positions: Vec3[], center: Vec3): number {
    let sum = 0;
    for (const i of indices) sum += Vec3.distance(positions[i], center);
    return sum / indices.length;
}

// === Shape builder ===

function buildPharmacophoreShape(features: readonly Feature[], prev?: Mesh): Shape<Mesh> {
    const state = MeshBuilder.createState(Math.max(features.length * 256, 512), 1024, prev);
    state.currentGroup = -1;
    const colorByGroup = new Map<number, Color>();
    const labelByGroup = new Map<number, string>();

    for (let i = 0; i < features.length; i++) {
        const f = features[i];
        state.currentGroup = i;
        switch (f.kind) {
            case 'hba': {
                if (!f.direction) break;
                Vec3.copy(TMP_BASE, f.direction);
                Vec3.scaleAndAdd(TMP_APEX, f.position, TMP_BASE, 1.0);
                const props: BasicCylinderProps = {
                    radiusTop: 0,
                    radiusBottom: 0.18,
                    radialSegments: 16,
                    topCap: false,
                    bottomCap: true,
                };
                addSimpleCylinder(state, f.position, TMP_APEX, props);
                colorByGroup.set(i, COLOR_HBA);
                labelByGroup.set(i, f.label);
                break;
            }
            case 'hbd': {
                if (!f.direction) break;
                Vec3.copy(TMP_BASE, f.direction);
                Vec3.scaleAndAdd(TMP_APEX, f.position, TMP_BASE, 1.0);
                addSimpleCylinder(state, f.position, TMP_APEX, {
                    radiusTop: 0.08,
                    radiusBottom: 0.08,
                    radialSegments: 12,
                    topCap: true,
                    bottomCap: true,
                });
                colorByGroup.set(i, COLOR_HBD);
                labelByGroup.set(i, f.label);
                break;
            }
            case 'aromatic': {
                const radius = f.radius ?? 1.4;
                const normal = f.direction ? Vec3.normalize(Vec3(), f.direction) : Vec3.copy(Vec3(), TMP_UP);
                Vec3.scaleAndAdd(TMP_TARGET, f.position, normal, 1);
                Mat4.targetTo(TMP_MAT, f.position, TMP_TARGET, TMP_UP);
                Mat4.scale(TMP_MAT, TMP_MAT, Vec3.set(Vec3(), radius, radius, radius));
                const circlePrim = Circle({ radius: 1, segments: 48 });
                MeshBuilder.addPrimitive(state, TMP_MAT, circlePrim);
                // Slight offset along normal so the second face doesn't z-fight.
                Vec3.scaleAndAdd(TMP_TARGET, f.position, normal, 0.03);
                Mat4.targetTo(TMP_MAT, TMP_TARGET, Vec3.scaleAndAdd(Vec3(), TMP_TARGET, normal, 1), TMP_UP);
                Mat4.scale(TMP_MAT, TMP_MAT, Vec3.set(Vec3(), radius, radius, radius));
                MeshBuilder.addPrimitive(state, TMP_MAT, circlePrim);
                colorByGroup.set(i, COLOR_ARO);
                labelByGroup.set(i, f.label);
                break;
            }
            case 'hydrophobic': {
                const radius = f.radius ?? 1.4;
                addSphere(state, f.position, radius, 1);
                colorByGroup.set(i, COLOR_HYD);
                labelByGroup.set(i, f.label);
                break;
            }
        }
    }

    return Shape.create(
        'Pharmacophore',
        features,
        MeshBuilder.getMesh(state),
        g => colorByGroup.get(g) ?? Color(0xffffff),
        () => 1,
        g => labelByGroup.get(g) ?? '',
    );
}

// === State node transformer (mirrors extensions/interactions/transforms.ts) ===

const PharmacophoreShapeProviderFactory = StateTransformer.builderFactory('mol-plugin-chem-pharmacophore');

const PharmacophoreShapeProvider = PharmacophoreShapeProviderFactory({
    name: 'pharmacophore-shape-provider',
    display: { name: 'Pharmacophore Shape Provider' },
    from: SO.Molecule.Structure,
    to: SO.Shape.Provider,
    params: { features: PD.Value<Feature[]>([], { isHidden: true }) },
})({
    canAutoUpdate: () => true,
    apply({ a, params }) {
        const collection = params.features;
        return Task.create('Build Pharmacophore Shape', async () => {
            return new SO.Shape.Provider({
                label: 'Pharmacophore',
                data: collection,
                params: PD.withDefaults(Mesh.Params, { alpha: 0.55, doubleSided: true }),
                getShape: (_ctx, data, _props, prev) => buildPharmacophoreShape(data as readonly Feature[], prev?.geometry),
                geometryUtils: Mesh.Utils,
            }, { label: 'Pharmacophore' });
        });
    },
});

// === Loci helper ===


// === Public attach/detach ===

export async function applyPharmacophoreFeatures(
    plugin: PluginContext,
    enabled: boolean,
    options: LigandFocusOptions,
): Promise<void> {
    const state = plugin.state.data;
    const update = state.build();

    // Always remove this module's owned nodes first.
    for (const cell of state.select(
        StateSelection.Generators.ofTransformer(PharmacophoreShapeProvider).withTag(PharmacophoreTag),
    )) {
        update.delete(cell);
    }

    if (!enabled) {
        await update.commit({ doNotUpdateCurrent: true });
        return;
    }

    const structure = plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
    if (!structure) {
        await update.commit({ doNotUpdateCurrent: true });
        return;
    }

    const features = await computePharmacophoreFeatures(structure, options);
    if (features.length === 0) {
        await update.commit({ doNotUpdateCurrent: true });
        return;
    }

    for (const structureRef of plugin.managers.structure.component.currentStructures) {
        update.to(structureRef.cell)
            .apply(PharmacophoreShapeProvider, { features }, { tags: [PharmacophoreTag] })
            .apply(StateTransforms.Representation.ShapeRepresentation3D, {}, { tags: [`${PharmacophoreTag}:visual`] });
    }

    await update.commit({ doNotUpdateCurrent: true });
}

/** Count of features per kind for the lab's availability badge. */
export async function getPharmacophoreFeatureCounts(structure: Structure, options: LigandFocusOptions): Promise<{
    hasLigand: boolean;
    hba: number;
    hbd: number;
    aromatic: number;
    hydrophobic: number;
}> {
    const features = await computePharmacophoreFeatures(structure, options);
    if (features.length === 0) {
        const loci = lociFromFocusOptions(structure, options);
        return { hasLigand: !StructureElement.Loci.isEmpty(loci), hba: 0, hbd: 0, aromatic: 0, hydrophobic: 0 };
    }
    const count = (k: FeatureKind) => features.filter(f => f.kind === k).length;
    return {
        hasLigand: true,
        hba: count('hba'),
        hbd: count('hbd'),
        aromatic: count('aromatic'),
        hydrophobic: count('hydrophobic'),
    };
}
