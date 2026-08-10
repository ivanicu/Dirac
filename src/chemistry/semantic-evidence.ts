/**
 * Input-evidence semantic layers for an existing Mol* scene.
 *
 * These layers expose values already carried by the loaded model. They do not
 * interpret a B factor as a confidence score, infer an ensemble from a single
 * model, or manufacture a visual result when the corresponding input field is
 * absent or uninformative. They only overpaint existing molecular geometry, so
 * atom/residue/chain loci and the active representation remain canonical.
 */

import { OrderedSet } from '../mol-data/int';
import { Structure, StructureElement, Unit } from '../mol-model/structure';
import type { PluginContext } from '../mol-plugin/context';
import { StateTransforms } from '../mol-plugin-state/transforms';
import { StateSelection } from '../mol-state';
import { Overpaint } from '../mol-theme/overpaint';
import { Color } from '../mol-util/color';

export type EvidenceSemanticLayerId =
    | 'partial-occupancy'
    | 'atomic-displacement'
    | 'alternate-locations'
    | 'model-identity';

export type EvidenceSemanticLayerCost = 'low';

export interface EvidenceSemanticLayerDefinition {
    readonly id: EvidenceSemanticLayerId;
    readonly label: string;
    readonly group: 'Input evidence';
    readonly cost: EvidenceSemanticLayerCost;
    /** Exact input field(s); this must remain visible in a future legend. */
    readonly source: string;
    /** States what the visual means and, importantly, what it does not mean. */
    readonly description: string;
}

export const EvidenceSemanticLayers: readonly EvidenceSemanticLayerDefinition[] = Object.freeze([
    {
        id: 'partial-occupancy',
        label: 'Partial occupancy',
        group: 'Input evidence',
        cost: 'low',
        source: 'input atom_site.occupancy',
        description: 'Marks input atoms with occupancy below 1.0. This is an atom-site population field, not a general confidence score.',
    },
    {
        id: 'atomic-displacement',
        label: 'High atomic displacement',
        group: 'Input evidence',
        cost: 'low',
        source: 'input atom_site.B_iso_or_equiv',
        description: 'Marks the upper quintile of the loaded B_iso_or_equiv field. It expresses a relative atomic-displacement value, not predicted confidence or experimental resolution.',
    },
    {
        id: 'alternate-locations',
        label: 'Alternate locations',
        group: 'Input evidence',
        cost: 'low',
        source: 'input atom_site.label_alt_id',
        description: 'Marks atoms with a non-empty alternate-location identifier in the input. It does not choose or invent an alternate conformer.',
    },
    {
        id: 'model-identity',
        label: 'Input model identity',
        group: 'Input evidence',
        cost: 'low',
        source: 'input atom_site.pdbx_PDB_model_num',
        description: 'Colors already-loaded atoms by their input model number when more than one model is present. It does not imply that the models form an equilibrated trajectory.',
    },
]);

export interface EvidenceSemanticLayerAvailability {
    readonly id: EvidenceSemanticLayerId;
    /** True only when the loaded scene contains a visually meaningful signal. */
    readonly available: boolean;
    readonly totalAtomCount: number;
    readonly affectedAtomCount: number;
    readonly coverage: number;
    /** Present for numeric evidence layers only. */
    readonly valueRange?: readonly [number, number];
    /** Present only for the multi-model layer. */
    readonly distinctModelCount?: number;
    readonly reason: string;
}

export const EvidenceSemanticLayerColors = {
    partialOccupancy: Color(0xffb45c),
    lowOccupancy: Color(0xfb7185),
    atomicDisplacement: Color(0xdd71c6),
    alternateLocation: Color(0x4fd7d2),
    models: [Color(0x77b7e5), Color(0xe7a65b), Color(0x95ce8b), Color(0xca91e7), Color(0xea869f), Color(0x63c9bd)] as const,
} as const;

const EvidenceSemanticLayerTag = 'mol-plugin-chem-evidence-semantic-layers';
const PartialOccupancyEpsilon = 1e-3;

type IndexedUnit = { unit: Unit.Atomic, indices: number[] };

/**
 * Inspect a structure before exposing its controls. Default occupancy of 1 and
 * a constant B field are deliberately treated as unavailable: rendering them
 * would add color without adding information.
 */
export function getEvidenceSemanticLayerAvailability(structure: Structure): readonly EvidenceSemanticLayerAvailability[] {
    const atomicUnits = structure.root.units.filter(Unit.isAtomic);
    const totalAtomCount = atomicUnits.reduce((count, unit) => count + unit.elements.length, 0);
    const occupancy = collectOccupancy(atomicUnits);
    const displacement = collectAtomicDisplacement(atomicUnits);
    const alternateLocations = collectAlternateLocations(atomicUnits);
    const models = collectModels(atomicUnits);

    return [
        {
            id: 'partial-occupancy',
            available: occupancy.defined && occupancy.partialCount > 0,
            totalAtomCount,
            affectedAtomCount: occupancy.partialCount,
            coverage: ratio(occupancy.partialCount, totalAtomCount),
            valueRange: occupancy.defined ? [occupancy.min, occupancy.max] : undefined,
            reason: occupancy.defined
                ? occupancy.partialCount > 0 ? 'Input contains atom sites with occupancy below 1.0.' : 'Input occupancy is uniformly 1.0.'
                : 'Input does not define atom-site occupancy.',
        },
        {
            id: 'atomic-displacement',
            available: displacement.defined && displacement.max > displacement.min,
            totalAtomCount,
            affectedAtomCount: displacement.highCount,
            coverage: ratio(displacement.highCount, totalAtomCount),
            valueRange: displacement.defined ? [displacement.min, displacement.max] : undefined,
            reason: displacement.defined
                ? displacement.max > displacement.min ? 'Input has a varying B_iso_or_equiv field.' : 'Input B_iso_or_equiv field is constant.'
                : 'Input does not define B_iso_or_equiv.',
        },
        {
            id: 'alternate-locations',
            available: alternateLocations.count > 0,
            totalAtomCount,
            affectedAtomCount: alternateLocations.count,
            coverage: ratio(alternateLocations.count, totalAtomCount),
            reason: alternateLocations.count > 0 ? 'Input contains non-empty alternate-location identifiers.' : 'Input contains no alternate-location identifiers.',
        },
        {
            id: 'model-identity',
            available: models.numbers.length > 1,
            totalAtomCount,
            affectedAtomCount: models.numbers.length > 1 ? totalAtomCount : 0,
            coverage: models.numbers.length > 1 ? 1 : 0,
            distinctModelCount: models.numbers.length,
            reason: models.numbers.length > 1 ? `Input contains ${models.numbers.length} model numbers.` : 'Input exposes one model number in this scene.',
        },
    ];
}

/** Aggregate availability over everything that Mol* currently displays. */
export function getPluginEvidenceSemanticLayerAvailability(plugin: PluginContext): readonly EvidenceSemanticLayerAvailability[] {
    const structures = plugin.managers.structure.component.currentStructures
        .map(entry => entry.cell.obj?.data)
        .filter((structure): structure is Structure => !!structure);
    if (structures.length === 0) return EvidenceSemanticLayers.map(layer => ({
        id: layer.id,
        available: false,
        totalAtomCount: 0,
        affectedAtomCount: 0,
        coverage: 0,
        reason: 'No loaded Mol* structure.',
    }));

    const byId = new Map<EvidenceSemanticLayerId, EvidenceSemanticLayerAvailability>();
    for (const structure of structures) {
        for (const availability of getEvidenceSemanticLayerAvailability(structure)) {
            const previous = byId.get(availability.id);
            if (!previous) {
                byId.set(availability.id, availability);
                continue;
            }
            const totalAtomCount = previous.totalAtomCount + availability.totalAtomCount;
            const affectedAtomCount = previous.affectedAtomCount + availability.affectedAtomCount;
            byId.set(availability.id, {
                ...availability,
                available: previous.available || availability.available,
                totalAtomCount,
                affectedAtomCount,
                coverage: ratio(affectedAtomCount, totalAtomCount),
                distinctModelCount: availability.id === 'model-identity'
                    ? Math.max(previous.distinctModelCount ?? 0, availability.distinctModelCount ?? 0)
                    : undefined,
            });
        }
    }
    return EvidenceSemanticLayers.map(layer => byId.get(layer.id)!);
}

/**
 * Apply only the requested evidence layers. The function replaces overlays
 * carrying this module's tag, leaving geometry, selection, VFX, and semantic
 * overlays owned by other modules untouched. Unsupported / empty data simply
 * produces no overlay.
 */
export async function applyEvidenceSemanticLayers(plugin: PluginContext, enabled: Iterable<EvidenceSemanticLayerId>) {
    const active = new Set(enabled);
    const state = plugin.state.data;
    const update = state.build();

    for (const structureRef of plugin.managers.structure.component.currentStructures) {
        for (const component of structureRef.components) {
            for (const representation of component.representations) {
                const repr = representation.cell;
                const source = repr.obj?.data.sourceData;
                if (!source) continue;

                const bundles = buildEvidenceBundles(source.root, active);
                const existing = state.select(StateSelection.Generators
                    .ofTransformer(StateTransforms.Representation.OverpaintStructureRepresentation3DFromBundle, repr.transform.ref)
                    .withTag(EvidenceSemanticLayerTag))[0];
                if (bundles.length === 0) {
                    if (existing) update.delete(existing.transform.ref);
                    continue;
                }

                const overpaint = Overpaint.filter(Overpaint.ofBundle(bundles, source.root), source) as Overpaint<StructureElement.Loci>;
                if (existing) {
                    update.to(existing.transform.ref).update(Overpaint.toBundle(overpaint));
                } else {
                    update.to(repr.transform.ref).apply(
                        StateTransforms.Representation.OverpaintStructureRepresentation3DFromBundle,
                        Overpaint.toBundle(overpaint),
                        { tags: EvidenceSemanticLayerTag },
                    );
                }
            }
        }
    }
    await update.commit({ doNotUpdateCurrent: true });
}

function buildEvidenceBundles(structure: Structure, active: ReadonlySet<EvidenceSemanticLayerId>): Overpaint.BundleLayer[] {
    const units = structure.units.filter(Unit.isAtomic);
    const availability = new Map<EvidenceSemanticLayerId, EvidenceSemanticLayerAvailability>(
        getEvidenceSemanticLayerAvailability(structure).map(entry => [entry.id, entry])
    );
    const layers: Overpaint.BundleLayer[] = [];

    if (active.has('partial-occupancy') && availability.get('partial-occupancy')!.available) {
        // Medium partial occupancy is a warm warning; very low occupancy becomes
        // pink. Both colors identify an input fact rather than a confidence rank.
        addIndexedLayer(layers, structure, collectIndices(units, (unit, element) => {
            const occupancy = unit.model.atomicConformation.occupancy.value(element);
            return occupancy >= 0.5 && occupancy < 1 - PartialOccupancyEpsilon;
        }), EvidenceSemanticLayerColors.partialOccupancy);
        addIndexedLayer(layers, structure, collectIndices(units, (unit, element) => {
            return unit.model.atomicConformation.occupancy.value(element) < 0.5;
        }), EvidenceSemanticLayerColors.lowOccupancy);
    }

    if (active.has('atomic-displacement') && availability.get('atomic-displacement')!.available) {
        const threshold = atomicDisplacementThreshold(units);
        addIndexedLayer(layers, structure, collectIndices(units, (unit, element) => {
            return unit.model.atomicConformation.B_iso_or_equiv.value(element) >= threshold;
        }), EvidenceSemanticLayerColors.atomicDisplacement);
    }

    if (active.has('alternate-locations') && availability.get('alternate-locations')!.available) {
        addIndexedLayer(layers, structure, collectIndices(units, (unit, element) => {
            return unit.model.atomicHierarchy.atoms.label_alt_id.value(element).trim().length > 0;
        }), EvidenceSemanticLayerColors.alternateLocation);
    }

    if (active.has('model-identity') && availability.get('model-identity')!.available) {
        const models = collectModels(units).numbers;
        for (let index = 0; index < models.length; index++) {
            const modelNumber = models[index];
            addIndexedLayer(layers, structure, collectIndices(units, unit => unit.model.modelNum === modelNumber), EvidenceSemanticLayerColors.models[index % EvidenceSemanticLayerColors.models.length]);
        }
    }

    return layers;
}

function addIndexedLayer(layers: Overpaint.BundleLayer[], structure: Structure, indexedUnits: readonly IndexedUnit[], color: Color) {
    const loci = lociFromIndexedUnits(structure, indexedUnits);
    if (!StructureElement.Loci.isEmpty(loci)) layers.push({ bundle: StructureElement.Bundle.fromLoci(loci), color, clear: false });
}

function collectIndices(units: readonly Unit.Atomic[], predicate: (unit: Unit.Atomic, element: number) => boolean): IndexedUnit[] {
    const result: IndexedUnit[] = [];
    for (const unit of units) {
        const indices: number[] = [];
        for (let index = 0; index < unit.elements.length; index++) {
            if (predicate(unit, unit.elements[index])) indices.push(index);
        }
        if (indices.length) result.push({ unit, indices });
    }
    return result;
}

function lociFromIndexedUnits(structure: Structure, indexedUnits: readonly IndexedUnit[]): StructureElement.Loci {
    return StructureElement.Loci(structure, indexedUnits.map(({ unit, indices }) => ({
        unit,
        indices: OrderedSet.ofSortedArray(new Int32Array(indices) as any),
    })));
}

function collectOccupancy(units: readonly Unit.Atomic[]) {
    let defined = false;
    let partialCount = 0;
    let min = Infinity;
    let max = -Infinity;
    for (const unit of units) {
        const column = unit.model.atomicConformation.occupancy;
        if (!column.isDefined) continue;
        defined = true;
        for (let index = 0; index < unit.elements.length; index++) {
            const value = column.value(unit.elements[index]);
            min = Math.min(min, value);
            max = Math.max(max, value);
            if (value < 1 - PartialOccupancyEpsilon) partialCount++;
        }
    }
    return { defined, partialCount, min: defined ? min : 0, max: defined ? max : 0 };
}

function collectAtomicDisplacement(units: readonly Unit.Atomic[]) {
    let defined = false;
    let min = Infinity;
    let max = -Infinity;
    const values: number[] = [];
    for (const unit of units) {
        const column = unit.model.atomicConformation.B_iso_or_equiv;
        if (!column.isDefined) continue;
        defined = true;
        for (let index = 0; index < unit.elements.length; index++) {
            const value = column.value(unit.elements[index]);
            min = Math.min(min, value);
            max = Math.max(max, value);
            values.push(value);
        }
    }
    const threshold = percentile(values, 0.8);
    return {
        defined,
        min: defined ? min : 0,
        max: defined ? max : 0,
        highCount: defined && max > min ? values.filter(value => value >= threshold).length : 0,
    };
}

function collectAlternateLocations(units: readonly Unit.Atomic[]) {
    let count = 0;
    for (const unit of units) {
        const column = unit.model.atomicHierarchy.atoms.label_alt_id;
        if (!column.isDefined) continue;
        for (let index = 0; index < unit.elements.length; index++) {
            if (column.value(unit.elements[index]).trim().length > 0) count++;
        }
    }
    return { count };
}

function collectModels(units: readonly Unit.Atomic[]) {
    return { numbers: Array.from(new Set(units.map(unit => unit.model.modelNum))).sort((a, b) => a - b) };
}

function atomicDisplacementThreshold(units: readonly Unit.Atomic[]) {
    const values: number[] = [];
    for (const unit of units) {
        const column = unit.model.atomicConformation.B_iso_or_equiv;
        if (!column.isDefined) continue;
        for (let index = 0; index < unit.elements.length; index++) values.push(column.value(unit.elements[index]));
    }
    return percentile(values, 0.8);
}

function percentile(values: readonly number[], quantile: number) {
    if (values.length === 0) return Infinity;
    const sorted = Array.from(values).sort((a, b) => a - b);
    return sorted[Math.min(sorted.length - 1, Math.max(0, Math.floor((sorted.length - 1) * quantile)))];
}

function ratio(numerator: number, denominator: number) {
    return denominator > 0 ? numerator / denominator : 0;
}
