/**
 * Structural and biological semantic layers for an existing Mol* scene.
 *
 * These layers deliberately do not add components, representations, meshes, or
 * labels. They only overpaint loci already owned by the current Mol* structure
 * representations, so atom/residue/chain picking remains canonical.
 */

import { QueryContext, Structure, StructureElement, StructureSelection } from '../mol-model/structure';
import { PluginContext } from '../mol-plugin/context';
import { StateTransforms } from '../mol-plugin-state/transforms';
import { StructureSelectionQueries } from '../mol-plugin-state/helpers/structure-selection-query';
import { StateSelection } from '../mol-state';
import { Overpaint } from '../mol-theme/overpaint';
import { Color } from '../mol-util/color';
import { SecondaryStructureProvider } from '../mol-model-props/computed/secondary-structure';
import { SecondaryStructureType } from '../mol-model/structure/model/types';
import { RuntimeContext } from '../mol-task';
import { MolScriptBuilder as MS } from '../mol-script/language/builder';
import { compile } from '../mol-script/runtime/query/compiler';

export type StructuralSemanticLayerCost = 'low' | 'medium';

/**
 * Every entry is an independently enabled semantic statement, rather than a
 * replacement representation. The ordering in `buildSemanticBundles`
 * makes more specific statements (secondary structure and disulfides) win over
 * the broad molecule-role colors where loci overlap.
 */
export const StructuralSemanticLayers = [
    { id: 'protein-role', label: 'Protein body', group: 'Molecular roles', cost: 'low', description: 'Colors protein polymer loci without changing the active cartoon, surface, or atomic representation.' },
    { id: 'nucleic-role', label: 'DNA / RNA', group: 'Molecular roles', cost: 'low', description: 'Colors native nucleic-acid loci while retaining backbone, base, atom and residue picking.' },
    { id: 'ligand-role', label: 'Ligand / cofactor', group: 'Molecular roles', cost: 'low', description: 'Colors non-polymer ligand loci only; it does not infer affinity or binding importance.' },
    { id: 'glycan-role', label: 'Branched carbohydrate', group: 'Molecular roles', cost: 'low', description: 'Colors branched carbohydrate loci without adding a glycan representation.' },
    { id: 'ion-role', label: 'Ions', group: 'Molecular roles', cost: 'low', description: 'Colors ions present in the loaded structure. It does not claim a coordination geometry.' },
    { id: 'binding-site-neighborhood', label: 'Ligand neighborhood', group: 'Local relationships', cost: 'medium', description: 'Colors whole residues within 5 Å of ligand loci, excluding the ligand itself. This is a structural proximity calculation, not an affinity claim.' },
    { id: 'secondary-structure-identity', label: 'Secondary-structure identity', group: 'Structural identity', cost: 'medium', description: 'Fine-grained DSSP secondary-structure identity: α-helix, 3₁₀-helix, π-helix, β-strand/sheet, turn, bend, coil. Seven orthogonal colors from Mol* secondary-structure data or its computed fallback.' },
    { id: 'disulfide-bridges', label: 'Disulfide-linked cysteines', group: 'Covalent structure', cost: 'low', description: 'Marks cysteine residues participating in explicit or inferred Mol* disulfide bonds. Bond cylinders themselves are not replaced.' },
] as const satisfies readonly {
    id: string,
    label: string,
    group: string,
    cost: StructuralSemanticLayerCost,
    description: string,
}[];

export type StructuralSemanticLayerId = typeof StructuralSemanticLayers[number]['id'];

/** The color vocabulary is exported so a UI or legend can describe the scene truthfully. */
export const StructuralSemanticLayerColors = {
    protein: Color(0x7294a5),
    nucleic: Color(0x7bc7d8),
    ligand: Color(0xffad58),
    glycan: Color(0xcfa66f),
    ion: Color(0xc9a5f5),
    coil: Color(0x7f9aa6),
    helix: Color(0xdd7195),
    helixAlpha: Color(0xdd5e8a),
    helix310: Color(0x5eb8c9),
    helixPi: Color(0xa06ec9),
    beta: Color(0xe8c45c),
    turn: Color(0xe89c5c),
    bend: Color(0x9db5bf),
    disulfide: Color(0xffdc6e),
    bindingSite: Color(0xffd166),
} as const;

const StructuralSemanticOverpaintTag = 'mol-plugin-chem-structural-semantic-layers';

// Fine-grained secondary-structure queries — compiled MolScript expressions that
// check specific DSSP-derived SecondaryStructureType flags. These enable the
// 7-color subdivision of the secondary-structure-identity layer.
function ssQuery(flag: number): (ctx: QueryContext) => StructureSelection {
    return compile<StructureSelection>(MS.struct.modifier.union([
        MS.struct.generator.atomGroups({
            'residue-test': MS.core.flags.hasAny([
                MS.ammp('secondaryStructureFlags'),
                MS.core.type.bitflags([flag])
            ])
        })
    ]));
}

const AlphaHelixQuery = ssQuery(SecondaryStructureType.Flag.HelixAlpha);
const Helix310Query = ssQuery(SecondaryStructureType.Flag.Helix3Ten);
const PiHelixQuery = ssQuery(SecondaryStructureType.Flag.HelixPi);
const TurnQuery = ssQuery(SecondaryStructureType.Flag.Turn);
const BendQuery = ssQuery(SecondaryStructureType.Flag.Bend);

type BundleLayer = {
    bundle: StructureElement.Bundle,
    color: Color,
    clear: boolean,
};

type QueryLayer = {
    color: Color,
    query: (ctx: QueryContext) => StructureSelection,
};

const LigandNeighborhoodQuery = compile<StructureSelection>(MS.struct.modifier.union([
    MS.struct.modifier.exceptBy({
        0: MS.struct.modifier.includeSurroundings({
            0: StructureSelectionQueries.ligandPlusConnected.expression,
            radius: 5,
            'as-whole-residues': true,
        }),
        by: StructureSelectionQueries.ligandPlusConnected.expression,
    }),
]));

/**
 * Apply all requested semantic layers as a single Mol* representation state.
 * A single state is intentional: Mol* currently has no general composition of
 * several independent `Representation3DState` transforms for one representation.
 */
export async function applyStructuralSemanticLayers(plugin: PluginContext, enabled: Iterable<StructuralSemanticLayerId>) {
    const active = new Set(enabled);
    const needsSecondaryStructure = active.has('secondary-structure-identity');

    if (needsSecondaryStructure) {
        await Promise.all(plugin.managers.structure.component.currentStructures.map(async entry => {
            await SecondaryStructureProvider.attach({
                runtime: RuntimeContext.Synchronous,
                assetManager: plugin.managers.asset,
            }, entry.cell.obj!.data, void 0, true);
        }));
    }

    const update = plugin.state.data.build();
    const state = plugin.state.data;
    for (const entry of plugin.managers.structure.component.currentStructures) {
        for (const component of entry.components) {
            for (const representation of component.representations) {
                const repr = representation.cell;
                const previous = state.select(StateSelection.Generators
                    .ofTransformer(StateTransforms.Representation.OverpaintStructureRepresentation3DFromBundle, repr.transform.ref)
                    .withTag(StructuralSemanticOverpaintTag))[0];
                const source = repr.obj?.data.sourceData;
                const layers = source ? buildSemanticBundles(source, active) : [];

                if (layers.length === 0) {
                    if (previous) update.delete(previous.transform.ref);
                    continue;
                }

                const overpaint = filterBundles(layers, source!);
                if (previous) {
                    update.to(previous).update(Overpaint.toBundle(overpaint));
                } else {
                    update.to(repr.transform.ref).apply(
                        StateTransforms.Representation.OverpaintStructureRepresentation3DFromBundle,
                        Overpaint.toBundle(overpaint),
                        { tags: StructuralSemanticOverpaintTag }
                    );
                }
            }
        }
    }
    await update.commit({ doNotUpdateCurrent: true });
}

function buildSemanticBundles(structure: Structure, active: ReadonlySet<StructuralSemanticLayerId>): BundleLayer[] {
    const layers: QueryLayer[] = [];

    // Broad biological roles come first. More specific structural facts below
    // intentionally overpaint them where the two categories intersect.
    if (active.has('protein-role')) layers.push({ color: StructuralSemanticLayerColors.protein, query: StructureSelectionQueries.protein.query });
    if (active.has('nucleic-role')) layers.push({ color: StructuralSemanticLayerColors.nucleic, query: StructureSelectionQueries.nucleic.query });
    if (active.has('ligand-role')) layers.push({ color: StructuralSemanticLayerColors.ligand, query: StructureSelectionQueries.ligand.query });
    if (active.has('glycan-role')) layers.push({ color: StructuralSemanticLayerColors.glycan, query: StructureSelectionQueries.branched.query });
    if (active.has('ion-role')) layers.push({ color: StructuralSemanticLayerColors.ion, query: StructureSelectionQueries.ion.query });
    if (active.has('binding-site-neighborhood')) layers.push({ color: StructuralSemanticLayerColors.bindingSite, query: LigandNeighborhoodQuery });

    if (active.has('secondary-structure-identity')) {
        // 7-color subdivision: coil base, then α/3₁₀/π helix + β + turn + bend overlay.
        // Order matters: more specific types applied AFTER broader ones so they win.
        layers.push({ color: StructuralSemanticLayerColors.coil, query: StructureSelectionQueries.protein.query });
        layers.push({ color: StructuralSemanticLayerColors.beta, query: StructureSelectionQueries.beta.query });
        layers.push({ color: StructuralSemanticLayerColors.turn, query: TurnQuery });
        layers.push({ color: StructuralSemanticLayerColors.bend, query: BendQuery });
        layers.push({ color: StructuralSemanticLayerColors.helixAlpha, query: AlphaHelixQuery });
        layers.push({ color: StructuralSemanticLayerColors.helix310, query: Helix310Query });
        layers.push({ color: StructuralSemanticLayerColors.helixPi, query: PiHelixQuery });
    }
    if (active.has('disulfide-bridges')) layers.push({ color: StructuralSemanticLayerColors.disulfide, query: StructureSelectionQueries.disulfideBridges.query });

    return layers.flatMap(({ color, query }) => {
        const selection = query(new QueryContext(structure.root));
        const loci = StructureSelection.toLociWithCurrentUnits(selection);
        return StructureElement.Loci.isEmpty(loci)
            ? []
            : [{ bundle: StructureElement.Bundle.fromLoci(loci), color, clear: false }];
    });
}

/** Match the helper used by Mol*'s structure component manager. */
function filterBundles(layers: BundleLayer[], structure: Structure) {
    const overpaint = Overpaint.ofBundle(layers, structure.root);
    const merged = Overpaint.merge(overpaint);
    return Overpaint.filter(merged, structure) as Overpaint<StructureElement.Loci>;
}
