/**
 * Opt-in molecular-context layers for a Mol* structure scene.
 *
 * These are deliberately separate from the active Native representation: a
 * cartoon-only protein view normally omits waters, ions, glycans and lipids.
 * When a layer is enabled, this module creates a small, pickable representation
 * from entities that are actually present in the loaded structure. It never
 * invents a membrane, hydration shell, glycan, or ion that is absent from the
 * source model.
 */

import type { PluginContext } from '../mol-plugin/context';
import { StateSelection } from '../mol-state';
import { StateTransforms } from '../mol-plugin-state/transforms';
import { StructureSelectionQueries, type StructureSelectionQuery } from '../mol-plugin-state/helpers/structure-selection-query';
import { Color } from '../mol-util/color';

export type ContextSemanticLayerCost = 'low' | 'medium';

export const ContextSemanticLayers = [
    {
        id: 'crystallographic-water',
        label: 'Observed water molecules',
        group: 'Molecular context',
        cost: 'medium',
        representation: 'spacefill',
        description: 'Shows only waters encoded in the input structure. It is not a simulated hydration shell or solvent density.'
    },
    {
        id: 'native-membrane-lipids',
        label: 'Native membrane lipids',
        group: 'Molecular context',
        cost: 'medium',
        representation: 'ball-and-stick',
        description: 'Shows only entities annotated as lipids in the input structure. No membrane plane or missing lipids are inferred.'
    },
    {
        id: 'branched-glycans',
        label: 'Branched glycans',
        group: 'Molecular context',
        cost: 'medium',
        representation: 'ball-and-stick',
        description: 'Shows only source-model branched carbohydrate entities. It does not predict glycosylation or complete unresolved glycans.'
    },
    {
        id: 'resolved-ions',
        label: 'Resolved ions',
        group: 'Molecular context',
        cost: 'low',
        representation: 'spacefill',
        description: 'Shows only ion entities classified by the loaded structure. Coordination claims remain a separate interaction layer.'
    },
] as const satisfies readonly {
    id: string,
    label: string,
    group: string,
    cost: ContextSemanticLayerCost,
    representation: 'spacefill' | 'ball-and-stick',
    description: string,
}[];

export type ContextSemanticLayerId = typeof ContextSemanticLayers[number]['id'];

type ContextSemanticLayer = typeof ContextSemanticLayers[number];

/** Exported palette so legends can identify the contextual entity class without guessing. */
export const ContextSemanticLayerColors = {
    water: Color(0x74cce8),
    lipid: Color(0xd89d63),
    glycan: Color(0xd4bc70),
    ion: Color(0xc99bff),
} as const;

const ContextSemanticTag = 'mol-plugin-chem-context-semantic-layer';

const LayerQueries: Record<ContextSemanticLayerId, StructureSelectionQuery> = {
    'crystallographic-water': StructureSelectionQueries.water,
    'native-membrane-lipids': StructureSelectionQueries.lipid,
    'branched-glycans': StructureSelectionQueries.branched,
    'resolved-ions': StructureSelectionQueries.ion,
};

const LayerColors: Record<ContextSemanticLayerId, Color> = {
    'crystallographic-water': ContextSemanticLayerColors.water,
    'native-membrane-lipids': ContextSemanticLayerColors.lipid,
    'branched-glycans': ContextSemanticLayerColors.glycan,
    'resolved-ions': ContextSemanticLayerColors.ion,
};

/**
 * Reconcile just the standalone context components owned by this module.
 *
 * Every generated component is a selection over the original structure, so it
 * preserves Mol* atom/residue/chain loci and picking. Empty source selections
 * are silently omitted by `tryCreateComponentFromSelection`; that is the
 * intentional "no context present" result rather than an invented surrogate.
 */
export async function applyContextSemanticLayers(plugin: PluginContext, enabled: Iterable<ContextSemanticLayerId>) {
    const active = new Set(enabled);
    const state = plugin.state.data;
    const update = state.build();
    const existing = state.select(
        StateSelection.Generators.ofTransformer(StateTransforms.Model.StructureComponent).withTag(ContextSemanticTag)
    );

    for (const cell of existing) update.delete(cell);
    await update.commit({ doNotUpdateCurrent: true });

    const activeLayers = ContextSemanticLayers.filter(layer => active.has(layer.id));
    for (const structure of plugin.managers.structure.component.currentStructures) {
        for (const layer of activeLayers) {
            await addContextRepresentation(plugin, structure.cell, layer);
        }
    }
}

async function addContextRepresentation(
    plugin: PluginContext,
    structure: Parameters<PluginContext['builders']['structure']['tryCreateComponentFromSelection']>[0],
    layer: ContextSemanticLayer,
) {
    const component = await plugin.builders.structure.tryCreateComponentFromSelection(
        structure,
        LayerQueries[layer.id],
        `semantic-context-${layer.id}`,
        { label: layer.label, tags: [ContextSemanticTag, `${ContextSemanticTag}:${layer.id}`] },
    );
    if (!component) return;

    await plugin.builders.structure.representation.addRepresentation(component, {
        type: layer.representation,
        color: 'uniform',
        colorParams: { value: LayerColors[layer.id] },
        size: 'uniform',
        sizeParams: { value: layer.representation === 'spacefill' ? 0.42 : 0.28 },
    });
}
