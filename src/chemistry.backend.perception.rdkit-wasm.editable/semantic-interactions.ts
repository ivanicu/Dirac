/**
 * Opt-in semantic interaction layers for a Mol* structure scene.
 *
 * Each layer is backed by Mol*'s computed interaction model. The layer does
 * not change an existing molecular representation: it adds its own pickable
 * contact geometry only while that particular semantic layer is enabled.
 */

import { AssetManager } from '../mol-util/assets';
import { Loci } from '../mol-model/loci';
import { Vec3 } from '../mol-math/linear-algebra';
import { Color } from '../mol-util/color';
import { ParamDefinition as PD } from '../mol-util/param-definition';
import { Task } from '../mol-task';
import { StateSelection, StateTransformer } from '../mol-state';
import { PluginStateObject as SO } from '../mol-plugin-state/objects';
import { StateTransforms } from '../mol-plugin-state/transforms';
import type { PluginContext } from '../mol-plugin/context';
import { QueryContext, Structure, StructureElement, StructureProperties, StructureSelection, Unit } from '../mol-model/structure';
import { InteractionFlag, InteractionType } from '../mol-model-props/computed/interactions/common';
import { computeInteractions, InteractionsParams, type InteractionsProps } from '../mol-model-props/computed/interactions/interactions';
import { HydrogenBondsProvider } from '../mol-model-props/computed/interactions/hydrogen-bonds';
import { IonicProvider, PiStackingProvider, CationPiProvider } from '../mol-model-props/computed/interactions/charged';
import { MetalCoordinationProvider } from '../mol-model-props/computed/interactions/metal';
import { InteractionData, InteractionsShape } from '../extensions/interactions/transforms';
import { InteractionTypeToKind, type InteractionKind, type StructureInteractionElement, type StructureInteractions } from '../extensions/interactions/model';
import { StructureSelectionQueries } from '../mol-plugin-state/helpers/structure-selection-query';

export const SemanticInteractionLayers = [
    {
        id: 'hydrogen-bonds',
        label: 'Hydrogen bonds',
        kinds: ['hydrogen-bond'] as const,
        providers: ['hydrogen-bonds'] as const,
        description: 'Geometry-qualified donor–acceptor hydrogen bonds computed by Mol*.'
    },
    {
        id: 'ionic-contacts',
        label: 'Ionic contacts',
        kinds: ['ionic'] as const,
        providers: ['ionic'] as const,
        description: 'Charged-group contacts computed from Mol* valence and interaction features.'
    },
    {
        id: 'pi-interactions',
        label: 'Pi interactions',
        kinds: ['pi-stacking', 'cation-pi'] as const,
        providers: ['pi-stacking', 'cation-pi'] as const,
        description: 'Aromatic stacking and cation–pi contacts computed from Mol* aromatic-ring features.'
    },
    {
        id: 'metal-coordination-contacts',
        label: 'Metal coordination contacts',
        kinds: ['metal-coordination'] as const,
        providers: ['metal-coordination'] as const,
        description: 'Metal–ligand coordination contacts computed by Mol*.'
    },
] as const;

export type SemanticInteractionLayerId = typeof SemanticInteractionLayers[number]['id'];

type SemanticInteractionLayer = typeof SemanticInteractionLayers[number];
type ComputedInteractions = Awaited<ReturnType<typeof computeInteractions>>;

const SemanticInteractionTag = 'mol-plugin-chem-semantic-interaction';
const Factory = StateTransformer.builderFactory('mol-plugin-chem-semantic-interaction');

const SemanticInteractionData = Factory({
    name: 'semantic-interaction-data',
    display: { name: 'Semantic Interaction Layer' },
    from: SO.Molecule.Structure,
    to: InteractionData,
    params: {
        layer: PD.Select<SemanticInteractionLayerId>('hydrogen-bonds', SemanticInteractionLayers.map(layer => [layer.id, layer.label])),
        target: PD.Value<StructureElement.Bundle>(StructureElement.Bundle.Empty, { isHidden: true }),
    },
})({
    apply({ a, params }) {
        const layer = getLayer(params.layer);
        return Task.create(`Compute ${layer.label}`, async ctx => {
            const interactions = await computeInteractions(
                { runtime: ctx, assetManager: new AssetManager() },
                a.data,
                interactionPropsFor(layer),
            );
            const ligandLoci = params.target.elements.length > 0 && params.target.hash === a.data.hashCode
                ? StructureElement.Bundle.toLoci(params.target, a.data)
                : StructureSelection.toLociWithCurrentUnits(
                    StructureSelectionQueries.ligandPlusConnected.query(new QueryContext(a.data.root))
                );
            const data = toStructureInteractions(a.data, interactions, new Set<InteractionKind>(layer.kinds), ligandLoci);
            return new InteractionData({ interactions: data }, { label: layer.label, description: layer.description });
        });
    },
});

/**
 * Add exactly the requested semantic contact layers and remove only layers
 * previously owned by this module. Existing molecular representations,
 * selections, overpaint, and VFX state are left untouched.
 */
export async function applyInteractionSemanticLayers(
    plugin: PluginContext,
    enabled: ReadonlySet<SemanticInteractionLayerId>,
    target: StructureElement.Bundle = StructureElement.Bundle.Empty,
) {
    const state = plugin.state.data;
    const update = state.build();
    const existing = state.select(
        StateSelection.Generators.ofTransformer(SemanticInteractionData).withTag(SemanticInteractionTag)
    );
    for (const cell of existing) update.delete(cell);

    const activeLayers = SemanticInteractionLayers.filter(layer => enabled.has(layer.id));
    if (activeLayers.length > 0) {
        for (const structure of plugin.managers.structure.component.currentStructures) {
            for (const layer of activeLayers) {
                update.to(structure.cell)
                    .apply(SemanticInteractionData, { layer: layer.id, target }, { tags: [SemanticInteractionTag, `${SemanticInteractionTag}:${layer.id}`] })
                    .apply(InteractionsShape, {
                        kinds: [...layer.kinds],
                        styles: interactionStylesFor(layer),
                    })
                    .apply(StateTransforms.Representation.ShapeRepresentation3D, {}, { tags: [`${SemanticInteractionTag}:visual`, `${SemanticInteractionTag}:${layer.id}`] });
            }
        }
    }

    await update.commit({ doNotUpdateCurrent: true });
}

export interface SemanticInteractionRecord {
    readonly id: number;
    readonly kind: InteractionKind;
    readonly aLabel: string;
    readonly bLabel: string;
    readonly distance: number;
    readonly loci: readonly [StructureElement.Loci, StructureElement.Loci];
}

/** Inspectable records backing the rendered contact geometry. */
export function getInteractionSemanticLayerRecords(plugin: PluginContext, id: SemanticInteractionLayerId): readonly SemanticInteractionRecord[] {
    const records: SemanticInteractionRecord[] = [];
    for (const cell of plugin.state.data.select(
        StateSelection.Generators.ofTransformer(SemanticInteractionData).withTag(`${SemanticInteractionTag}:${id}`),
    )) {
        for (const [index, interaction] of (cell.obj?.data.interactions.elements ?? []).entries()) {
            const a = StructureElement.Loci.getFirstLocation(interaction.a);
            const b = StructureElement.Loci.getFirstLocation(interaction.b);
            const sphereA = Loci.getBoundingSphere(interaction.a);
            const sphereB = Loci.getBoundingSphere(interaction.b);
            if (!a || !b || !sphereA || !sphereB) continue;
            records.push({
                id: index,
                kind: interaction.info.kind,
                aLabel: endpointLabel(a),
                bLabel: endpointLabel(b),
                distance: Vec3.distance(sphereA.center, sphereB.center),
                loci: [interaction.a, interaction.b],
            });
        }
    }
    return records;
}

function endpointLabel(location: StructureElement.Location) {
    if (!Unit.isAtomic(location.unit)) return 'coarse site';
    const comp = StructureProperties.atom.label_comp_id(location);
    const chain = StructureProperties.chain.auth_asym_id(location) || StructureProperties.chain.label_asym_id(location);
    const sequence = StructureProperties.residue.auth_seq_id(location);
    const atom = StructureProperties.atom.label_atom_id(location);
    return `${comp} ${chain || '—'}:${sequence} ${atom}`;
}

/** Counts the currently rendered contact primitives for each owned layer. */
export function getInteractionSemanticLayerCounts(plugin: PluginContext): Readonly<Record<SemanticInteractionLayerId, number>> {
    const counts: Record<SemanticInteractionLayerId, number> = {
        'hydrogen-bonds': 0,
        'ionic-contacts': 0,
        'pi-interactions': 0,
        'metal-coordination-contacts': 0,
    };
    for (const cell of plugin.state.data.select(
        StateSelection.Generators.ofTransformer(SemanticInteractionData).withTag(SemanticInteractionTag),
    )) {
        const id = cell.params?.values.layer as SemanticInteractionLayerId | undefined;
        if (!id) continue;
        counts[id] = cell.obj?.data.interactions.elements.length ?? 0;
    }
    return counts;
}

function getLayer(id: SemanticInteractionLayerId): SemanticInteractionLayer {
    const layer = SemanticInteractionLayers.find(candidate => candidate.id === id);
    if (!layer) throw new Error(`Unknown semantic interaction layer '${id}'`);
    return layer;
}

function interactionPropsFor(layer: SemanticInteractionLayer): Partial<InteractionsProps> {
    const props = PD.getDefaultValues(InteractionsParams) as InteractionsProps;
    const providers: Record<string, { name: 'on', params: unknown }> = {
        'hydrogen-bonds': { name: 'on', params: PD.getDefaultValues(HydrogenBondsProvider.params) },
        'ionic': { name: 'on', params: PD.getDefaultValues(IonicProvider.params) },
        'pi-stacking': { name: 'on', params: PD.getDefaultValues(PiStackingProvider.params) },
        'cation-pi': { name: 'on', params: PD.getDefaultValues(CationPiProvider.params) },
        'metal-coordination': { name: 'on', params: PD.getDefaultValues(MetalCoordinationProvider.params) },
    };
    for (const provider of layer.providers) {
        (props.providers as Record<string, unknown>)[provider] = providers[provider];
    }
    return props;
}

function interactionStylesFor(layer: SemanticInteractionLayer) {
    // These are visual encodings only. Contact membership remains the Mol*
    // computed result passed to InteractionsShape above.
    const styles = {
        'unknown': { color: Color(0x000000), style: 'dashed' as const, radius: 0.04 },
        'ionic': { color: Color(0x8cd9ff), style: 'dashed' as const, radius: 0.055 },
        'pi-stacking': { color: Color(0x9674d8), style: 'solid' as const, radius: 0.05 },
        'cation-pi': { color: Color(0xd278b8), style: 'solid' as const, radius: 0.05 },
        'halogen-bond': { color: Color(0xffde21), style: 'dashed' as const, radius: 0.04 },
        'hydrogen-bond': { color: Color(0x74d6d0), style: 'dashed' as const, radius: 0.045, showArrow: true, arrowOffset: 0.18 },
        'weak-hydrogen-bond': { color: Color(0x74d6d0), style: 'dashed' as const, radius: 0.04, showArrow: true, arrowOffset: 0.18 },
        'hydrophobic': { color: Color(0x777777), style: 'dashed' as const, radius: 0.04 },
        'metal-coordination': { color: Color(0xffc857), style: 'solid' as const, radius: 0.06 },
        'water-bridge': { color: Color(0x55d5ff), style: 'dashed' as const, radius: 0.04 },
        'covalent': { color: Color(0x999999), radius: 0.1 },
    };
    // Keep the returned object complete because InteractionsShape validates all
    // style groups even when this particular layer exposes only one or two.
    return styles;
}

function toStructureInteractions(
    structure: Structure,
    interactions: ComputedInteractions,
    visibleKinds: ReadonlySet<InteractionKind>,
    ligandLoci: StructureElement.Loci,
): StructureInteractions {
    const elements: StructureInteractionElement[] = [];

    for (const unit of structure.units) {
        const contacts = interactions.unitsContacts.get(unit.id);
        if (!contacts) continue;

        for (let index = 0, count = contacts.a.length; index < count; index++) {
            // Intra-unit contacts occupy two directed adjacency slots. Retain
            // one slot so a physical contact is represented once.
            if (contacts.a[index] > contacts.b[index] || contacts.edgeProps.flag[index] === InteractionFlag.Filtered) continue;
            const kind = InteractionTypeToKind[contacts.edgeProps.type[index] as InteractionType];
            if (!kind || !visibleKinds.has(kind)) continue;
            const a = lociForFeature(structure, interactions, unit.id, contacts.a[index]);
            const b = lociForFeature(structure, interactions, unit.id, contacts.b[index]);
            if (!StructureElement.Loci.areIntersecting(a, ligandLoci) && !StructureElement.Loci.areIntersecting(b, ligandLoci)) continue;
            elements.push({ info: { kind } as StructureInteractionElement['info'], a, b });
        }
    }

    for (const contact of interactions.contacts.edges) {
        if (contact.unitA > contact.unitB || contact.props.flag === InteractionFlag.Filtered) continue;
        const kind = InteractionTypeToKind[contact.props.type];
        if (!kind || !visibleKinds.has(kind)) continue;
        const a = lociForFeature(structure, interactions, contact.unitA, contact.indexA);
        const b = lociForFeature(structure, interactions, contact.unitB, contact.indexB);
        if (!StructureElement.Loci.areIntersecting(a, ligandLoci) && !StructureElement.Loci.areIntersecting(b, ligandLoci)) continue;
        elements.push({ info: { kind } as StructureInteractionElement['info'], a, b });
    }

    return { kind: 'structure-interactions', elements };
}

function lociForFeature(structure: Structure, interactions: ComputedInteractions, unitId: number, featureIndex: number): StructureElement.Loci {
    const unit = structure.unitMap.get(unitId);
    const features = interactions.unitsFeatures.get(unitId);
    if (!unit || !features) throw new Error(`Interaction feature references missing unit ${unitId}`);

    const builder = structure.subsetBuilder(false);
    builder.beginUnit(unit.id);
    for (let offset = features.offsets[featureIndex], end = features.offsets[featureIndex + 1]; offset < end; offset++) {
        builder.addElement(unit.elements[features.members[offset]]);
    }
    builder.commitUnit();
    return Structure.toStructureElementLoci(builder.getStructure());
}
