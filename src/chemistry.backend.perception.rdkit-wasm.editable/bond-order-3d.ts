/**
 * 3D bond order rendering: draws extra parallel cylinders next to double
 * and triple bonds so the 3D scene shows molecular unsaturation visually
 * (ChemDraw-style) instead of all-single-line cylinders.
 *
 * For a double bond C=C:
 *   main cylinder (from mol* Ball & Stick) + 1 extra offset cylinder
 * For a triple bond C≡C:
 *   main cylinder + 2 extra offset cylinders
 *
 * The extra cylinders are drawn as a mol* Shape (MeshBuilder), attached as
 * a tagged state node — same ownership pattern as pharmacophore features.
 */

import { PluginContext } from '../mol-plugin/context';
import { StateTransforms } from '../mol-plugin-state/transforms';
import { StateSelection, StateTransformer } from '../mol-state';
import { Color } from '../mol-util/color';
import { ParamDefinition as PD } from '../mol-util/param-definition';
import { Task } from '../mol-task';
import { Vec3 } from '../mol-math/linear-algebra';
import { Mesh } from '../mol-geo/geometry/mesh/mesh';
import { MeshBuilder } from '../mol-geo/geometry/mesh/mesh-builder';
import { addSimpleCylinder, BasicCylinderProps } from '../mol-geo/geometry/mesh/builder/cylinder';
import { Shape } from '../mol-model/shape';
import { PluginStateObject as SO } from '../mol-plugin-state/objects';
import { OrderedSet } from '../mol-data/int';
import { ComponentBond } from '../mol-model-formats/structure/property/bonds/chem_comp';
import type { LigandFocusOptions } from './semantic-focus';
import { lociFromFocusOptions } from './ligand-pipeline';
import { Structure, StructureElement, Unit, StructureProperties } from '../mol-model/structure';

export type BondOrder3DLayerId = 'bond-order-3d-rdkit';

export interface BondOrder3DLayerDefinition {
    readonly id: BondOrder3DLayerId;
    readonly label: string;
    readonly cost: 'low';
    readonly source: string;
    readonly description: string;
}

export const BondOrder3DLayers: readonly BondOrder3DLayerDefinition[] = Object.freeze([
    {
        id: 'bond-order-3d-rdkit',
        label: 'Bond order · 3D double/triple lines',
        cost: 'low',
        source: 'molfile bond block → mol* Shape parallel cylinders',
        description: 'Draws extra parallel cylinders next to double bonds (1 extra) and triple bonds (2 extra) so the 3D scene shows bond order visually. The main bond cylinder stays from mol* Ball & Stick; this layer adds the second/third line.',
    },
]);

const BondOrder3DTag = 'mol-plugin-chem-bond-order-3d';

interface BondInfo {
    a1: Vec3;
    a2: Vec3;
    order: number;
}

function collectBondOrderGeometry(structure: Structure, options: LigandFocusOptions): BondInfo[] {
    const loci = lociFromFocusOptions(structure, options);
    if (StructureElement.Loci.isEmpty(loci)) return [];

    const model = structure.models[0];
    const bondData = ComponentBond.Provider.get(model);
    if (!bondData) return [];

    // Collect atom positions + names
    const positions: Vec3[] = [];
    const names: string[] = [];
    const pos = Vec3();
    const location = StructureElement.Location.create(structure);
    let compId = '';
    for (const e of loci.elements) {
        if (!Unit.isAtomic(e.unit)) continue;
        const count = OrderedSet.size(e.indices);
        for (let i = 0; i < count; i++) {
            const idx = OrderedSet.getAt(e.indices, i);
            location.unit = e.unit;
            location.element = e.unit.elements[idx];
            if (!compId) compId = StructureProperties.residue.label_comp_id(location);
            names.push(StructureProperties.atom.label_atom_id(location));
            e.unit.conformation.position(location.element, pos);
            positions.push(Vec3.clone(pos));
        }
    }
    if (positions.length === 0) return [];

    const nameToIdx = new Map<string, number>();
    for (let i = 0; i < names.length; i++) nameToIdx.set(names[i], i);

    const bonds: BondInfo[] = [];
    const compBonds = bondData.entries.get(compId);
    if (compBonds?.map) {
        for (const [name1, pairs] of compBonds.map) {
            const a1 = nameToIdx.get(name1);
            if (a1 === undefined) continue;
            for (const [name2, bond] of pairs.map) {
                const a2 = nameToIdx.get(name2);
                if (a2 === undefined) continue;
                if (a1 < a2 && bond.order > 1) {
                    bonds.push({ a1: positions[a1], a2: positions[a2], order: bond.order });
                }
            }
        }
    }
    return bonds;
}

const TMP_DIR = Vec3();
const TMP_PERP = Vec3();
const TMP_OFFSET_A = Vec3();
const TMP_OFFSET_B = Vec3();
const UP = Vec3.create(0, 1, 0);

function buildBondOrderShape(bonds: readonly BondInfo[], prev?: Mesh): Shape<Mesh> {
    const state = MeshBuilder.createState(Math.max(bonds.length * 64, 256), 256, prev);
    state.currentGroup = -1;
    const colorByGroup = new Map<number, Color>();

    const OFFSET = 0.22;  // Å, perpendicular offset for parallel lines
    const RADIUS = 0.045; // Å, thinner than main bond

    for (let i = 0; i < bonds.length; i++) {
        const b = bonds[i];
        Vec3.sub(TMP_DIR, b.a2, b.a1);
        const len = Vec3.magnitude(TMP_DIR);
        if (len < 0.01) continue;
        Vec3.scale(TMP_DIR, TMP_DIR, 1 / len);

        // Perpendicular: cross(direction, up). If parallel to up, use (1,0,0).
        Vec3.cross(TMP_PERP, TMP_DIR, UP);
        let perpLen = Vec3.magnitude(TMP_PERP);
        if (perpLen < 0.01) {
            TMP_PERP[0] = 1; TMP_PERP[1] = 0; TMP_PERP[2] = 0;
            perpLen = 1;
        }
        Vec3.scale(TMP_PERP, TMP_PERP, OFFSET / perpLen);

        const props: BasicCylinderProps = {
            radiusTop: RADIUS, radiusBottom: RADIUS,
            radialSegments: 8, topCap: true, bottomCap: true,
        };

        if (b.order === 2) {
            // 1 extra cylinder offset to one side
            state.currentGroup = i;
            Vec3.add(TMP_OFFSET_A, b.a1, TMP_PERP);
            Vec3.add(TMP_OFFSET_B, b.a2, TMP_PERP);
            addSimpleCylinder(state, TMP_OFFSET_A, TMP_OFFSET_B, props);
            colorByGroup.set(i, Color(0xaaaaaa));
        } else if (b.order >= 3) {
            // 2 extra cylinders: both sides
            state.currentGroup = i;
            Vec3.add(TMP_OFFSET_A, b.a1, TMP_PERP);
            Vec3.add(TMP_OFFSET_B, b.a2, TMP_PERP);
            addSimpleCylinder(state, TMP_OFFSET_A, TMP_OFFSET_B, props);
            Vec3.sub(TMP_OFFSET_A, b.a1, TMP_PERP);
            Vec3.sub(TMP_OFFSET_B, b.a2, TMP_PERP);
            addSimpleCylinder(state, TMP_OFFSET_A, TMP_OFFSET_B, props);
            colorByGroup.set(i, Color(0xaaaaaa));
        }
    }

    return Shape.create(
        'BondOrder3D', bonds, MeshBuilder.getMesh(state),
        g => colorByGroup.get(g) ?? Color(0xaaaaaa),
        () => 1, g => '',
    );
}

const BondOrder3DFactory = StateTransformer.builderFactory('mol-plugin-chem-bond-order-3d');

const BondOrder3DProvider = BondOrder3DFactory({
    name: 'bond-order-3d-shape-provider',
    display: { name: 'Bond Order 3D Shape Provider' },
    from: SO.Molecule.Structure,
    to: SO.Shape.Provider,
    params: { bonds: PD.Value<BondInfo[]>([], { isHidden: true }) },
})({
    canAutoUpdate: () => true,
    apply({ a, params }) {
        const bonds = params.bonds;
        return Task.create('Build Bond Order 3D Shape', async () => {
            return new SO.Shape.Provider({
                label: 'Bond Order 3D',
                data: bonds,
                params: PD.withDefaults(Mesh.Params, {}),
                getShape: (_ctx, data, _props, prev) => buildBondOrderShape(data as readonly BondInfo[], prev?.geometry),
                geometryUtils: Mesh.Utils,
            }, { label: 'Bond Order 3D' });
        });
    },
});

export async function applyBondOrder3D(plugin: PluginContext, enabled: boolean, options: LigandFocusOptions): Promise<void> {
    const state = plugin.state.data;
    const update = state.build();

    for (const cell of state.select(
        StateSelection.Generators.ofTransformer(BondOrder3DProvider).withTag(BondOrder3DTag)
    )) {
        update.delete(cell);
    }

    if (!enabled) { await update.commit({ doNotUpdateCurrent: true }); return; }

    const structure = plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
    if (!structure) { await update.commit({ doNotUpdateCurrent: true }); return; }

    const bonds = collectBondOrderGeometry(structure, options);
    if (bonds.length === 0) { await update.commit({ doNotUpdateCurrent: true }); return; }

    for (const structureRef of plugin.managers.structure.component.currentStructures) {
        update.to(structureRef.cell)
            .apply(BondOrder3DProvider, { bonds }, { tags: [BondOrder3DTag] })
            .apply(StateTransforms.Representation.ShapeRepresentation3D, {}, { tags: [`${BondOrder3DTag}:visual`] });
    }
    await update.commit({ doNotUpdateCurrent: true });
}

export function getBondOrder3DCounts(structure: Structure, options: LigandFocusOptions): { doubleBonds: number; tripleBonds: number } {
    const bonds = collectBondOrderGeometry(structure, options);
    return {
        doubleBonds: bonds.filter(b => b.order === 2).length,
        tripleBonds: bonds.filter(b => b.order >= 3).length,
    };
}
