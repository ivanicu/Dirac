/**
 * Chemical-semantic visual layers for an already displayed Mol* structure.
 *
 * These are deliberately overlays, not representations: they never create
 * atoms, bonds, surfaces, or labels, and therefore preserve the loci used for
 * atom/residue/chain picking. Every layer is derived only from information
 * available in the loaded structure or its Mol* bond graph.
 */

import { Structure, StructureElement, StructureSelection } from '../mol-model/structure';
import { PluginContext } from '../mol-plugin/context';
import { StateTransforms } from '../mol-plugin-state/transforms';
import { StateSelection } from '../mol-state';
import { Overpaint } from '../mol-theme/overpaint';
import { MolScriptBuilder as MS } from '../mol-script/language/builder';
import { compile } from '../mol-script/runtime/query/compiler';
import { Color } from '../mol-util/color';
import { Loci } from '../mol-model/loci';
import { QueryContext } from '../mol-model/structure/query/context';

export type ChemicalSemanticLayerId =
    | 'aromatic-rings'
    | 'formal-positive-charge'
    | 'formal-negative-charge'
    | 'metal-centres';

export type ChemicalSemanticLayerCost = 'low';

export interface ChemicalSemanticLayerDefinition {
    readonly id: ChemicalSemanticLayerId;
    readonly label: string;
    readonly color: Color;
    readonly cost: ChemicalSemanticLayerCost;
    /** The exact source of truth; not an inference or a chemistry guess. */
    readonly source: string;
    readonly description: string;
}

/**
 * The display order is intentional: more specific coordination information is
 * applied after broad atom categories when the same atom belongs to both.
 */
export const ChemicalSemanticLayers: readonly ChemicalSemanticLayerDefinition[] = Object.freeze([
    {
        id: 'aromatic-rings',
        label: 'Aromatic rings',
        color: Color(0xc792ea),
        cost: 'low',
        source: 'Mol* aromatic ring perception',
        description: 'Colors atoms and bonds belonging to rings Mol* identifies as aromatic.',
    },
    {
        id: 'formal-positive-charge',
        label: 'Formal positive charge',
        color: Color(0x58b6ff),
        cost: 'low',
        source: 'mmCIF atom_site.pdbx_formal_charge > 0',
        description: 'Colors only atoms whose input model explicitly declares a positive formal charge.',
    },
    {
        id: 'formal-negative-charge',
        label: 'Formal negative charge',
        color: Color(0xff6b7a),
        cost: 'low',
        source: 'mmCIF atom_site.pdbx_formal_charge < 0',
        description: 'Colors only atoms whose input model explicitly declares a negative formal charge.',
    },
    {
        id: 'metal-centres',
        label: 'Metal centres',
        color: Color(0x5fe0b8),
        cost: 'low',
        source: 'atom element symbol',
        description: 'Colors atoms whose element belongs to Mol*\'s explicit metal-element set.',
    },
]);

const ChemicalSemanticLayerTag = 'chemical-semantic-layers';

const AromaticRingsQuery = compile<StructureSelection>(MS.struct.modifier.union([
    MS.struct.generator.rings({ 'only-aromatic': true }),
]));

const FormalPositiveChargeQuery = compile<StructureSelection>(MS.struct.modifier.union([
    MS.struct.generator.atomGroups({
        'atom-test': MS.core.rel.gr([MS.ammp('pdbx_formal_charge'), 0]),
    }),
]));

const FormalNegativeChargeQuery = compile<StructureSelection>(MS.struct.modifier.union([
    MS.struct.generator.atomGroups({
        'atom-test': MS.core.rel.lt([MS.ammp('pdbx_formal_charge'), 0]),
    }),
]));

// Kept local rather than importing an implementation detail from bond-compute.
// The values are the metal symbols Mol* treats as metals when computing bonds.
const MetalElements = [
    'LI', 'NA', 'K', 'RB', 'CS', 'FR', 'BE', 'MG', 'CA', 'SR', 'BA', 'RA',
    'AL', 'GA', 'IN', 'SN', 'TL', 'PB', 'BI', 'SC', 'TI', 'V', 'CR', 'MN',
    'FE', 'CO', 'NI', 'CU', 'ZN', 'Y', 'ZR', 'NB', 'MO', 'TC', 'RU', 'RH',
    'PD', 'AG', 'CD', 'LA', 'HF', 'TA', 'W', 'RE', 'OS', 'IR', 'PT', 'AU',
    'HG', 'AC', 'RF', 'DB', 'SG', 'BH', 'HS', 'MT', 'CE', 'PR', 'ND', 'PM',
    'SM', 'EU', 'GD', 'TB', 'DY', 'HO', 'ER', 'TM', 'YB', 'LU', 'TH', 'PA',
    'U', 'NP', 'PU', 'AM', 'CM', 'BK', 'CF', 'ES', 'FM', 'MD', 'NO', 'LR',
] as const;

const MetalCentresQuery = compile<StructureSelection>(MS.struct.modifier.union([
    MS.struct.generator.atomGroups({
        'atom-test': MS.core.set.has([MS.set(...MetalElements), MS.acp('elementSymbol')]),
    }),
]));

const LayerQueries: Readonly<Record<ChemicalSemanticLayerId, ReturnType<typeof compile<StructureSelection>>>> = {
    'aromatic-rings': AromaticRingsQuery,
    'formal-positive-charge': FormalPositiveChargeQuery,
    'formal-negative-charge': FormalNegativeChargeQuery,
    'metal-centres': MetalCentresQuery,
};

function layerLoci(structure: Structure, id: ChemicalSemanticLayerId) {
    return StructureSelection.toLociWithCurrentUnits(LayerQueries[id](new QueryContext(structure)));
}

/**
 * Applies exactly the requested chemical overlays to every current structure
 * representation. Calling it again replaces only this module's overlays; it
 * leaves selections, picking, and overpaint layers owned by other modules
 * intact. Passing an empty iterable removes this module's overlays.
 */
export async function applyChemicalSemanticLayers(plugin: PluginContext, enabled: Iterable<ChemicalSemanticLayerId>) {
    const enabledIds = new Set(enabled);
    const enabledLayers = ChemicalSemanticLayers.filter(layer => enabledIds.has(layer.id));
    const state = plugin.state.data;
    const update = state.build();

    for (const structureRef of plugin.managers.structure.component.currentStructures) {
        for (const component of structureRef.components) {
            for (const representation of component.representations) {
                const repr = representation.cell;
                const source = repr.obj?.data.sourceData;
                if (!source) continue;

                const layers: Overpaint.BundleLayer[] = [];
                for (const definition of enabledLayers) {
                    const loci = layerLoci(source.root, definition.id);
                    if (Loci.isEmpty(loci)) continue;
                    layers.push({
                        bundle: StructureElement.Bundle.fromLoci(loci),
                        color: definition.color,
                        clear: false,
                    });
                }

                const existing = state.select(
                    StateSelection.Generators.ofTransformer(
                        StateTransforms.Representation.OverpaintStructureRepresentation3DFromBundle,
                        repr.transform.ref,
                    ).withTag(ChemicalSemanticLayerTag),
                )[0];

                const overpaint = Overpaint.filter(Overpaint.ofBundle(layers, source.root), source) as Overpaint<StructureElement.Loci>;
                if (existing) {
                    if (layers.length) update.to(existing.transform.ref).update(Overpaint.toBundle(overpaint));
                    else update.delete(existing.transform.ref);
                } else if (layers.length) {
                    update.to(repr.transform.ref).apply(
                        StateTransforms.Representation.OverpaintStructureRepresentation3DFromBundle,
                        Overpaint.toBundle(overpaint),
                        { tags: ChemicalSemanticLayerTag },
                    );
                }
            }
        }
    }

    await update.commit({ doNotUpdateCurrent: true });
}
