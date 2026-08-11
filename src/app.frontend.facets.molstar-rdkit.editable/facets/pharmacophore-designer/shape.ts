/**
 * Pharmacophore Designer — editable 3D shape.
 *
 * Renders the model's features as pickable mol* Shape primitives in the same
 * scene as the ligand: a translucent tolerance sphere per enabled feature
 * (radius = the feature's tolerance in Å) plus the kind glyph the read-only
 * layer established — HBA cone, HBD stick, aromatic disk. Disabled features
 * collapse to a small muted core so they stay findable without implying they
 * are part of the query.
 *
 * groupId === feature INDEX in the rendered array, so the picker (hover /
 * drag) maps a hit straight back to the model. One state node, updated in
 * place on every model change — cheap enough to run per animation frame
 * during a drag.
 */

import { PluginContext } from '../../../mol-plugin/context';
import { StateTransforms } from '../../../mol-plugin-state/transforms';
import { StateSelection, StateTransformer } from '../../../mol-state';
import { Color } from '../../../mol-util/color';
import { ParamDefinition as PD } from '../../../mol-util/param-definition';
import { Task } from '../../../mol-task';
import { Mat4, Vec3 } from '../../../mol-math/linear-algebra';
import { Mesh } from '../../../mol-geo/geometry/mesh/mesh';
import { MeshBuilder } from '../../../mol-geo/geometry/mesh/mesh-builder';
import { addSimpleCylinder } from '../../../mol-geo/geometry/mesh/builder/cylinder';
import { addSphere } from '../../../mol-geo/geometry/mesh/builder/sphere';
import { Circle } from '../../../mol-geo/primitive/circle';
import { Shape, ShapeGroup } from '../../../mol-model/shape';
import { PluginStateObject as SO } from '../../../mol-plugin-state/objects';
import { Loci } from '../../../mol-model/loci';
import { OrderedSet } from '../../../mol-data/int';
import { FeatureKindLabel, type DesignerFeature } from './model';

export const DesignerShapeName = 'Pharmacophore model';
const DesignerTag = 'dirac-pharmacophore-designer';

const KIND_COLOR = {
    hba: Color(0xe15555),
    hbd: Color(0x4dabf7),
    aromatic: Color(0xfab005),
    hydrophobic: Color(0x868e96),
} as const;
const COLOR_DISABLED = Color(0x6b7689);

const TMP_UP = Vec3.create(0, 1, 0);
const TMP_DIR = Vec3();
const TMP_END = Vec3();
const TMP_MAT = Mat4();
const TMP_TARGET = Vec3();

function buildDesignerShape(features: readonly DesignerFeature[], prev?: Mesh): Shape<Mesh> {
    const state = MeshBuilder.createState(Math.max(features.length * 512, 1024), 2048, prev);
    const colorByGroup = new Map<number, Color>();
    const labelByGroup = new Map<number, string>();

    for (let i = 0; i < features.length; i++) {
        const f = features[i];
        state.currentGroup = i;
        const color = f.enabled ? KIND_COLOR[f.kind] : COLOR_DISABLED;
        colorByGroup.set(i, color);
        labelByGroup.set(i, `${FeatureKindLabel[f.kind]} · F${f.id} · r ${f.radius.toFixed(1)} Å · drag to move`);

        if (!f.enabled) {
            // Muted core only: findable and draggable, visibly not in the query.
            addSphere(state, f.position, 0.35, 2);
            continue;
        }

        // Tolerance sphere.
        addSphere(state, f.position, f.radius, 2);

        // Kind glyph, following the read-only layer's visual language.
        if ((f.kind === 'hba' || f.kind === 'hbd') && f.direction) {
            Vec3.normalize(TMP_DIR, f.direction);
            Vec3.scaleAndAdd(TMP_END, f.position, TMP_DIR, f.radius + 0.7);
            if (f.kind === 'hba') {
                addSimpleCylinder(state, f.position, TMP_END, {
                    radiusTop: 0, radiusBottom: 0.22, radialSegments: 16, topCap: false, bottomCap: true,
                });
            } else {
                addSimpleCylinder(state, f.position, TMP_END, {
                    radiusTop: 0.1, radiusBottom: 0.1, radialSegments: 12, topCap: true, bottomCap: true,
                });
            }
        } else if (f.kind === 'aromatic' && f.direction) {
            const normal = Vec3.normalize(Vec3(), f.direction);
            const diskRadius = Math.max(f.radius * 0.85, 0.6);
            Vec3.scaleAndAdd(TMP_TARGET, f.position, normal, 1);
            Mat4.targetTo(TMP_MAT, f.position, TMP_TARGET, TMP_UP);
            Mat4.scale(TMP_MAT, TMP_MAT, Vec3.set(Vec3(), diskRadius, diskRadius, diskRadius));
            MeshBuilder.addPrimitive(state, TMP_MAT, Circle({ radius: 1, segments: 48 }));
            // Normal stick so the plane orientation reads at a glance.
            Vec3.scaleAndAdd(TMP_END, f.position, normal, f.radius + 0.5);
            addSimpleCylinder(state, f.position, TMP_END, {
                radiusTop: 0.06, radiusBottom: 0.06, radialSegments: 8, topCap: true, bottomCap: true,
            });
        }
    }

    return Shape.create(
        DesignerShapeName,
        features,
        MeshBuilder.getMesh(state),
        g => colorByGroup.get(g) ?? Color(0xffffff),
        () => 1,
        g => labelByGroup.get(g) ?? '',
    );
}

const Factory = StateTransformer.builderFactory('dirac-pharmacophore-designer');

const DesignerShapeProvider = Factory({
    name: 'pharmacophore-designer-shape-provider',
    display: { name: 'Pharmacophore Model Provider' },
    from: SO.Molecule.Structure,
    to: SO.Shape.Provider,
    params: { features: PD.Value<DesignerFeature[]>([], { isHidden: true }) },
})({
    canAutoUpdate: () => true,
    apply({ params }) {
        return Task.create('Pharmacophore Model Shape', async () => {
            return new SO.Shape.Provider({
                label: DesignerShapeName,
                data: params.features,
                // Alpha must clear the renderer's pickingAlphaThreshold (0.5) or
                // the features cannot be hovered / dragged at all.
                params: PD.withDefaults(Mesh.Params, { alpha: 0.55, doubleSided: true }),
                getShape: (_ctx, data, _props, prev) => buildDesignerShape(data as readonly DesignerFeature[], prev?.geometry),
                geometryUtils: Mesh.Utils,
            }, { label: DesignerShapeName });
        });
    },
});

/** Snapshot features so the rendered params stay stable while the model mutates. */
function snapshot(features: readonly DesignerFeature[]): DesignerFeature[] {
    return features.map(f => ({
        ...f,
        position: Vec3.clone(f.position),
        direction: f.direction ? Vec3.clone(f.direction) : null,
    }));
}

/**
 * Create / update / remove the single designer shape node so the 3D scene
 * mirrors `features`. Safe to call per animation frame during a drag: an
 * existing node gets a param update (mesh rebuild reusing the previous
 * geometry buffers), not a delete + re-create.
 */
export async function syncDesignerShape(
    plugin: PluginContext,
    features: readonly DesignerFeature[],
    visible: boolean,
): Promise<void> {
    const state = plugin.state.data;
    const existing = state.select(
        StateSelection.Generators.ofTransformer(DesignerShapeProvider).withTag(DesignerTag),
    );

    if (!visible || features.length === 0) {
        if (existing.length === 0) return;
        const update = state.build();
        for (const cell of existing) update.delete(cell);
        await update.commit({ doNotUpdateCurrent: true });
        return;
    }

    const update = state.build();
    if (existing.length > 0) {
        update.to(existing[0].transform.ref).update({ features: snapshot(features) });
    } else {
        const structureCell = plugin.managers.structure.component.pivotStructure?.cell;
        if (!structureCell) return;
        update.to(structureCell)
            .apply(DesignerShapeProvider, { features: snapshot(features) }, { tags: [DesignerTag] })
            .apply(StateTransforms.Representation.ShapeRepresentation3D, {}, { tags: [`${DesignerTag}:visual`] });
    }
    await update.commit({ doNotUpdateCurrent: true });
}

/**
 * Map a picked loci to the index of the designer feature it belongs to, or
 * null when the pick is anything else.
 */
export function designerFeatureIndexFromLoci(loci: Loci): number | null {
    if (!ShapeGroup.isLoci(loci)) return null;
    if (loci.shape.name !== DesignerShapeName) return null;
    if (loci.groups.length === 0) return null;
    const ids = loci.groups[0].ids;
    if (OrderedSet.size(ids) === 0) return null;
    return OrderedSet.getAt(ids, 0);
}
