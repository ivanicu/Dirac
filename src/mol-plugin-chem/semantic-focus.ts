/**
 * Ligand-centred semantic focus layers for an existing Mol* scene.
 *
 * This module owns only the selection/representation nodes it creates. It
 * never changes the native components or representations, so their picking
 * loci remain untouched. The visual scope is deliberately local: all layers
 * derive from a deposited ligand and its configured surroundings, rather
 * than showing the structure-wide interaction graph.
 */

import { PluginContext } from '../mol-plugin/context';
import { QueryContext, Structure, StructureElement, StructureProperties, StructureSelection, Unit } from '../mol-model/structure';
import { StructureUniqueSubsetBuilder } from '../mol-model/structure/structure/util/unique-subset-builder';
import { StateTransforms } from '../mol-plugin-state/transforms';
import { StateSelection } from '../mol-state';
import { createStructureRepresentationParams } from '../mol-plugin-state/helpers/structure-representation-params';
import { StructureSelectionQueries } from '../mol-plugin-state/helpers/structure-selection-query';
import { Color } from '../mol-util/color';
import { Transparency } from '../mol-theme/transparency';
import { OrderedSet, SortedArray } from '../mol-data/int';
import { Vec3 } from '../mol-math/linear-algebra';

export type FocusSemanticLayerId =
    | 'ligand-detail'
    | 'ligand-interface-residues'
    | 'ligand-pocket-surface';

export type FocusSemanticLayerCost = 'low' | 'medium' | 'high';

export interface FocusSemanticLayerDefinition {
    readonly id: FocusSemanticLayerId;
    readonly label: string;
    readonly group: 'Ligand focus';
    readonly cost: FocusSemanticLayerCost;
    /** What the layer is derived from; this is intentionally not a functional claim. */
    readonly source: string;
    readonly description: string;
}

/**
 * All entries below are independent. For example, the local molecular surface
 * can be enabled without creating a duplicate ligand ball-and-stick object.
 */
export const FocusSemanticLayers: readonly FocusSemanticLayerDefinition[] = Object.freeze([
    {
        id: 'ligand-detail',
        label: 'Ligand detail',
        group: 'Ligand focus',
        cost: 'low',
        source: 'Non-polymer ligand plus Mol* connected atoms',
        description: 'Adds an atomically detailed view of deposited ligands only. It does not infer a binding pose, affinity, or pharmacophore.',
    },
    {
        id: 'ligand-interface-residues',
        label: 'Ligand interface residues',
        group: 'Ligand focus',
        cost: 'medium',
        source: 'Whole residues with at least one atom inside the configured ligand cutoff',
        description: 'Shows the ligand-local macromolecular neighbourhood. This is a distance-defined interface, not an interaction-energy calculation.',
    },
    {
        id: 'ligand-pocket-surface',
        label: 'Ligand pocket surface',
        group: 'Ligand focus',
        cost: 'high',
        source: 'Molecular surface of the configured ligand neighbourhood',
        description: 'Adds a translucent local molecular surface around nearby residues. It never creates a whole-protein surface.',
    },
]);

const FocusSemanticTag = 'mol-plugin-chem-ligand-focus';
const FocusTransparencyTag = 'mol-plugin-chem-ligand-focus-dim';

export interface LigandFocusTarget {
    readonly id: string;
    readonly label: string;
    readonly bundle: StructureElement.Bundle;
    readonly elementCount: number;
}

export interface LigandFocusOptions {
    readonly target?: StructureElement.Bundle;
    readonly cutoff?: number;
}

type ResolvedFocus = {
    ligand: StructureElement.Loci;
    neighbourhood: StructureElement.Loci;
};

export interface FocusSemanticLayerStats {
    readonly elements: number;
    readonly residues: number;
}

/** Availability stats used by the lab to disable empty controls before rendering. */
export function getFocusSemanticLayerCounts(structure: Structure, options: LigandFocusOptions = {}): Readonly<Record<FocusSemanticLayerId, FocusSemanticLayerStats>> {
    const focus = resolveFocus(structure, options);
    return {
        'ligand-detail': lociStats(focus.ligand),
        'ligand-interface-residues': lociStats(focus.neighbourhood),
        'ligand-pocket-surface': lociStats(focus.neighbourhood),
    };
}

/** Canonical focus target shared by the geometry layers and camera framing. */
export function getLigandFocusLoci(structure: Structure, options: LigandFocusOptions = {}) {
    const focus = resolveFocus(structure, options);
    return StructureElement.Loci.union(focus.ligand, focus.neighbourhood);
}

/** Individual deposited ligand residues available as focus targets. */
export function getLigandFocusTargets(structure: Structure): readonly LigandFocusTarget[] {
    // Target identity must come from deposited ligand residues only. The
    // ligandPlusConnected query also contains covalently/metal-coordinated
    // protein residues, which are useful rendering context but are not valid
    // ligand choices (for example the proximal His around a haem group).
    const ligand = depositedLigandLoci(structure);
    const groups = new Map<string, { label: string, units: Map<number, { unit: Unit, indices: number[] }> }>();
    const location = StructureElement.Location.create(structure);

    for (const entry of ligand.elements) {
        for (let i = 0, count = OrderedSet.size(entry.indices); i < count; i++) {
            const unitIndex = OrderedSet.getAt(entry.indices, i);
            location.unit = entry.unit;
            location.element = entry.unit.elements[unitIndex];
            const residueKey = StructureProperties.residue.key(location);
            const id = `${entry.unit.model.id}:${residueKey}`;
            let group = groups.get(id);
            if (!group) {
                const comp = StructureProperties.atom.label_comp_id(location);
                const chain = StructureProperties.chain.auth_asym_id(location) || StructureProperties.chain.label_asym_id(location);
                const seq = StructureProperties.residue.auth_seq_id(location);
                group = { label: `${comp} · ${chain || '—'}:${seq}`, units: new Map() };
                groups.set(id, group);
            }
            let unit = group.units.get(entry.unit.id);
            if (!unit) {
                unit = { unit: entry.unit, indices: [] };
                group.units.set(entry.unit.id, unit);
            }
            unit.indices.push(unitIndex);
        }
    }

    return [...groups.entries()].map(([id, group]) => {
        const loci = StructureElement.Loci(structure, [...group.units.values()].map(({ unit, indices }) => ({
            unit,
            indices: OrderedSet.ofSortedArray(SortedArray.ofSortedArray<StructureElement.UnitIndex>(indices.sort((a, b) => a - b))),
        })));
        return { id, label: group.label, bundle: StructureElement.Bundle.fromLoci(loci), elementCount: StructureElement.Loci.size(loci) };
    }).sort((a, b) => a.label.localeCompare(b.label));
}

function defaultLigandLoci(structure: Structure) {
    const selection = StructureSelectionQueries.ligandPlusConnected.query(new QueryContext(structure.root));
    return StructureSelection.toLociWithCurrentUnits(selection);
}

function depositedLigandLoci(structure: Structure) {
    const selection = StructureSelectionQueries.ligand.query(new QueryContext(structure.root));
    return StructureSelection.toLociWithCurrentUnits(selection);
}

function resolveFocus(structure: Structure, options: LigandFocusOptions): ResolvedFocus {
    const cutoff = Math.min(8, Math.max(3, options.cutoff ?? 5));
    const ligand = options.target && options.target.hash === structure.hashCode
        ? StructureElement.Bundle.toLoci(options.target, structure)
        : defaultLigandLoci(structure);
    if (StructureElement.Loci.isEmpty(ligand)) return { ligand, neighbourhood: ligand };

    const builder = new StructureUniqueSubsetBuilder(structure);
    const position = Vec3();
    const location = StructureElement.Location.create(structure);
    StructureElement.Loci.forEachLocation(ligand, loc => {
        loc.unit.conformation.position(loc.element, position);
        structure.lookup3d.findIntoBuilder(position[0], position[1], position[2], cutoff, builder);
    }, location);
    const nearby = Structure.toStructureElementLoci(builder.getStructure());
    const wholeResidues = StructureElement.Loci.extendToWholeResidues(nearby, true);
    return { ligand, neighbourhood: StructureElement.Loci.subtract(wholeResidues, ligand) };
}

function lociStats(loci: StructureElement.Loci) {
    const structure = StructureElement.Bundle.toStructure(StructureElement.Bundle.fromLoci(loci), loci.structure);
    const residues = structure.units.reduce((count, unit) => count + (Unit.isAtomic(unit) ? unit.residueCount : unit.elements.length), 0);
    return { elements: StructureElement.Loci.size(loci), residues };
}

/**
 * Replace this module's owned geometry with exactly the requested focus
 * layers. Native representations and layers owned by other semantic modules
 * are left alone. Passing an empty iterable deletes only this module's nodes.
 */
export async function applyFocusSemanticLayers(plugin: PluginContext, enabled: Iterable<FocusSemanticLayerId>, options: LigandFocusOptions = {}) {
    const active = new Set(enabled);
    const state = plugin.state.data;
    const update = state.build();

    // A selection node is the root of every owned layer, so deleting it also
    // removes its representation without touching sibling native components.
    for (const existing of state.select(
        StateSelection.Generators.ofTransformer(StateTransforms.Model.StructureSelectionFromExpression)
            .withTag(FocusSemanticTag),
    )) {
        update.delete(existing.transform.ref);
    }
    for (const existing of state.select(
        StateSelection.Generators.ofTransformer(StateTransforms.Model.StructureSelectionFromBundle)
            .withTag(FocusSemanticTag),
    )) {
        update.delete(existing.transform.ref);
    }
    for (const existing of state.select(
        StateSelection.Generators.ofTransformer(StateTransforms.Representation.TransparencyStructureRepresentation3DFromBundle)
            .withTag(FocusTransparencyTag),
    )) {
        update.delete(existing.transform.ref);
    }

    for (const structureRef of plugin.managers.structure.component.currentStructures) {
        const structureCell = structureRef.cell;
        const structure = structureCell.obj?.data;
        if (!structure) continue;
        const focus = resolveFocus(structure, options);

        if (active.size > 0) {
            const wholeStructure = StructureElement.Loci.all(structure.root);
            const transparency = Transparency.toBundle(Transparency.ofBundle([{
                bundle: StructureElement.Bundle.fromLoci(wholeStructure),
                value: 0.58,
            }], structure.root));
            for (const component of structureRef.components) {
                for (const representation of component.representations) {
                    if (representation.cell.transform.tags?.includes(FocusSemanticTag)) continue;
                    update.to(representation.cell.transform.ref).apply(
                        StateTransforms.Representation.TransparencyStructureRepresentation3DFromBundle,
                        transparency,
                        { tags: [FocusTransparencyTag] },
                    );
                }
            }
        }

        for (const definition of FocusSemanticLayers) {
            if (!active.has(definition.id)) continue;

            const tag = `${FocusSemanticTag}:${definition.id}`;
            const loci = definition.id === 'ligand-detail' ? focus.ligand : focus.neighbourhood;
            if (StructureElement.Loci.isEmpty(loci)) continue;
            const selection = update.to(structureCell).apply(
                StateTransforms.Model.StructureSelectionFromBundle,
                {
                    label: `[Ligand focus] ${definition.label}`,
                    bundle: StructureElement.Bundle.fromLoci(loci),
                },
                { tags: [FocusSemanticTag, tag] },
            );

            selection.apply(
                StateTransforms.Representation.StructureRepresentation3D,
                createFocusRepresentationParams(plugin, structure, definition.id),
                { tags: [FocusSemanticTag, tag] },
            );
        }
    }

    await update.commit({ doNotUpdateCurrent: true });
}

function createFocusRepresentationParams(plugin: PluginContext, structure: Structure, id: FocusSemanticLayerId) {
    switch (id) {
        case 'ligand-detail':
            return createStructureRepresentationParams(plugin, structure, {
                type: 'ball-and-stick',
                color: 'element-symbol',
                typeParams: {
                    sizeFactor: 0.28,
                    sizeAspectRatio: 0.74,
                    adjustCylinderLength: true,
                    aromaticBonds: true,
                    multipleBonds: 'off',
                    excludeTypes: ['hydrogen-bond', 'metal-coordination'],
                },
            });
        case 'ligand-interface-residues':
            return createStructureRepresentationParams(plugin, structure, {
                type: 'ball-and-stick',
                color: 'uniform',
                colorParams: { value: Color(0x6ed7c2) },
                typeParams: {
                    alpha: 0.72,
                    sizeFactor: 0.16,
                    sizeAspectRatio: 0.65,
                    excludeTypes: ['hydrogen-bond', 'metal-coordination'],
                },
            });
        case 'ligand-pocket-surface':
            return createStructureRepresentationParams(plugin, structure, {
                type: 'molecular-surface',
                color: 'uniform',
                colorParams: { value: Color(0x5ab4c9) },
                typeParams: {
                    alpha: 0.26,
                    resolution: 0.8,
                    probeRadius: 1.4,
                    smoothColors: { name: 'on', params: { resolutionFactor: 1, sampleStride: 3 } },
                },
            });
    }
}
