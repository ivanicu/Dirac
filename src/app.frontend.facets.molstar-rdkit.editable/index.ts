import {
    applyMolstarVisualUpgrades,
    captureMolstarVisualState,
    createChemWorkbench,
    MolstarVisualUpgrades,
    RecommendedMolstarVisualUpgrades,
    restoreMolstarVisualState,
    type MolstarVisualSnapshot,
    type MolstarVisualUpgradeId,
} from '../chemistry.backend.perception.rdkit-wasm.editable';
import {
    applyChemicalSemanticLayers,
    ChemicalSemanticLayers,
    type ChemicalSemanticLayerId,
} from '../chemistry.backend.perception.rdkit-wasm.editable/semantic-chemistry';
import {
    applyStructuralSemanticLayers,
    StructuralSemanticLayers,
    type StructuralSemanticLayerId,
} from '../chemistry.backend.perception.rdkit-wasm.editable/semantic-structural';
import {
    applyInteractionSemanticLayers,
    getInteractionSemanticLayerCounts,
    getInteractionSemanticLayerRecords,
    SemanticInteractionLayers,
    type SemanticInteractionRecord,
    type SemanticInteractionLayerId,
} from '../chemistry.backend.perception.rdkit-wasm.editable/semantic-interactions';
import {
    applyContextSemanticLayers,
    ContextSemanticLayers,
    type ContextSemanticLayerId,
} from '../chemistry.backend.perception.rdkit-wasm.editable/semantic-context';
import {
    applyFocusSemanticLayers,
    FocusSemanticLayers,
    getFocusSemanticLayerCounts,
    getLigandFocusLoci,
    getLigandFocusTargets,
    type LigandFocusTarget,
    type FocusSemanticLayerId,
} from '../chemistry.backend.perception.rdkit-wasm.editable/semantic-focus';
import {
    applyEvidenceSemanticLayers,
    EvidenceSemanticLayers,
    type EvidenceSemanticLayerId,
} from '../chemistry.backend.perception.rdkit-wasm.editable/semantic-evidence';
import {
    applyRdkitChemicalLayers,
    getRdkitChemicalLayerCounts,
    prepareLigandAnalysis,
    getRDKit,
    computeLigandChemistry,
    searchLigandSmarts,
    applySmartsSearchOverlay,
    computeLigandIdentifiers,
    RdkitChemicalLayers,
    type RdkitChemicalLayerId,
} from '../chemistry.backend.perception.rdkit-wasm.editable/semantic-chemistry-rdkit';
import {
    applyPharmacophoreFeatures,
    getPharmacophoreFeatureCounts,
    PharmacophoreLayers,
    type PharmacophoreLayerId,
} from '../chemistry.backend.perception.rdkit-wasm.editable/pharmacophore-features';
import {
    applyBondOrder3D,

    BondOrder3DLayers,
    type BondOrder3DLayerId,
} from '../chemistry.backend.perception.rdkit-wasm.editable/bond-order-3d';
import { LigandDepiction, type AtomHighlight, type AtomPosition } from '../chemistry.backend.perception.rdkit-wasm.editable/ligand-depiction';
import { renderPropertiesPanel } from './facets/property-cockpit';
import { initFieldWellsPanel, updateFieldWellsLigand, autoRenderElectrostaticWell } from './facets/field-wells';
import { initPharmacophoreDesigner, updatePharmacophoreDesigner } from './facets/pharmacophore-designer';
import { PresetStructureRepresentations } from '../mol-plugin-state/builder/structure/representation-preset';
import { StateTransforms } from '../mol-plugin-state/transforms';
import { Loci } from '../mol-model/loci';
import { QueryContext, StructureElement, StructureSelection, Unit } from '../mol-model/structure';
import { ShapeGroup } from '../mol-model/shape';
import { OrderedSet } from '../mol-data/int';
import { StructureSelectionQueries } from '../mol-plugin-state/helpers/structure-selection-query';
import { Mat4, Vec3 } from '../mol-math/linear-algebra';
import { StateSelection } from '../mol-state';
import { Color } from '../mol-util/color';
import bDnaUrl from '../../examples/1bna_confal_pyramids.cif';
import crambinUrl from '../../examples/1crn.cif';
import retinoidUrl from '../../examples/1cbs_updated.cif';
import gramicidinUrl from '../../examples/1grm_updated.cif';
import mhcComplexUrl from '../../examples/7qpd.fw2.cif';
import gfpUrl from './assets/structures/1ema.cif';
import p53DnaUrl from './assets/structures/1tup.cif';
import porinUrl from './assets/structures/2por.cif';
import hemoglobinUrl from './assets/structures/4hhb.cif';
import { focusLociKeepingSlab, restoreSceneSlab } from './camera-slab';
import './index.html';

interface MolecularControl {
    readonly id: string;
    readonly label: string;
    readonly category: string;
    readonly stress: string;
    readonly url: string;
}

const MolecularControls: readonly MolecularControl[] = [
    { id: '1CRN', label: '1CRN · Crambin', category: 'Small mixed-fold protein', stress: 'Compact α/β fold; exposes ribbon curvature and cap quality.', url: crambinUrl },
    { id: '1GRM', label: '1GRM · Gramicidin A', category: 'Membrane peptide channel', stress: 'Paired transmembrane helices; exposes tube roundness and specular continuity.', url: gramicidinUrl },
    { id: '1CBS', label: '1CBS · Retinoid-binding protein', category: 'Protein–ligand complex', stress: 'β-rich binding pocket plus a small-molecule ligand; tests semantic focus and occlusion.', url: retinoidUrl },
    { id: '1BNA', label: '1BNA · B-DNA dodecamer', category: 'Pure nucleic acid', stress: 'Nucleotide rings, bonds and helical depth without any protein cartoon.', url: bDnaUrl },
    { id: '1EMA', label: '1EMA · Green fluorescent protein', category: 'β-barrel protein + chromophore', stress: 'Dense curved β-sheets around an internal chromophore.', url: gfpUrl },
    { id: '4HHB', label: '4HHB · Hemoglobin', category: 'Multimeric protein + heme', stress: 'Four-chain assembly with cofactors; tests chain separation and internal depth.', url: hemoglobinUrl },
    { id: '1TUP', label: '1TUP · p53–DNA complex', category: 'Protein–DNA complex', stress: 'Two polymer classes in contact; tests representation and selection semantics.', url: p53DnaUrl },
    { id: '2POR', label: '2POR · Porin', category: 'Membrane β-barrel', stress: 'Long β-strands and a central pore; exposes sheet edges and depth contours.', url: porinUrl },
    { id: '7QPD', label: '7QPD · MHC-I loading complex', category: 'Large macromolecular assembly', stress: 'Large multi-chain assembly; tests scaling, clutter control and performance.', url: mhcComplexUrl },
];

type RepresentationId = keyof typeof PresetStructureRepresentations;

const RepresentationControls: readonly { id: RepresentationId, label: string }[] = [
    { id: 'polymer-and-ligand', label: 'Native · Polymer + ligand' },
    { id: 'polymer-cartoon', label: 'Native · Polymer cartoon' },
    { id: 'atomic-detail', label: 'Native · Atomic detail' },
    { id: 'molecular-surface', label: 'Native · Molecular surface' },
    { id: 'protein-and-nucleic', label: 'Native · Protein + nucleic' },
    { id: 'illustrative', label: 'Native · Illustrative' },
    { id: 'auto', label: 'Native · Automatic' },
];

const UpgradeGroups = [
    { id: 'Molecular roles', label: 'Roles', description: 'Protein, nucleic acid, ligand, carbohydrate and ion identities already present in the source structure.' },
    { id: 'Structural identity', label: 'Structure', description: 'Computed or deposited secondary-structure identity for the existing polymer.' },
    { id: 'Local relationships', label: 'Local', description: 'Spatial relationships calculated from the current structure, such as the residue neighborhood around a ligand.' },
    { id: 'Covalent structure', label: 'Covalent', description: 'Explicit or Mol*-inferred covalent structural facts such as disulfide-linked cysteines.' },
    { id: 'Chemistry', label: 'Chemistry', description: 'Input-declared atom chemistry and ring/metal features. These layers preserve the existing Native geometry.' },
    { id: 'RDKit chemistry', label: 'RDKit', description: 'Computed chemistry perception from an in-browser RDKit-JS instance running over the focused ligand. Adds Gasteiger partial charge, Lipinski donor/acceptor, and accurate aromaticity on top of the existing Native geometry.' },
    { id: 'Pharmacophore', label: 'Pharmacophore', description: '3D pharmacophore features rendered as mol* Shape primitives: H-bond acceptor cones, donor sticks, aromatic ring disks, hydrophobic halos. Orthogonal to atom color; surfaces the ligand recognition profile.' },
    { id: 'Input evidence', label: 'Evidence', description: 'Deposited atom-site evidence fields only. A layer remains empty when the loaded structure does not contain a meaningful source signal.' },
    { id: 'Interactions', label: 'Interactions', description: 'Ligand-local, pickable hydrogen-bond geometry computed by Mol*, with a live contact count.' },
    { id: 'Molecular context', label: 'Context', description: 'Observed water, lipids, glycans and ions present in the source model. These are explicit semantic components, not inferred scenery.' },
    { id: 'Ligand focus', label: 'Ligand focus', description: 'Ligand atoms, nearby residue side chains and a local pocket surface. The Native context is dimmed instead of recoloured.' },
    { id: 'Geometry', label: 'Geometry', description: 'Quality and shape of existing curves and primitives.' },
    { id: 'Sphere only', label: 'Sphere', description: 'Conditional controls for existing spacefill spheres only.' },
    { id: 'Material', label: 'Material', description: 'Surface response without changing molecular geometry.' },
    { id: 'Surface only', label: 'Surface', description: 'Conditional smoothing controls for an existing molecular or gaussian surface only.' },
    { id: 'Lighting', label: 'Lighting', description: 'Independent key, fill, rim and shadow controls.' },
    { id: 'Environment', label: 'Environment', description: 'Background, ambient fill and frame composition behind the existing Native view.' },
    { id: 'Depth', label: 'Depth', description: 'Contact separation and depth-derived contours.' },
    { id: 'Image', label: 'Image', description: 'Final-frame antialiasing, sharpening and tone response.' },
    { id: 'Focus', label: 'Camera', description: 'Depth cue, depth of field and highlight bloom.' },
    { id: 'Composition', label: 'Composition', description: 'Explicit single-copy scene slots. Each switch adds exactly one sibling of the current Native representation.' },
    { id: 'Interaction', label: 'Interaction', description: 'Semantic hover and selection feedback.' },
    { id: 'Experimental', label: 'Experimental', description: 'Non-realtime capabilities are listed honestly and remain disabled until a real backend exists.' },
] as const;

const StructuralLayerControls = StructuralSemanticLayers.map(layer => ({
    ...layer,
    recommended: false,
}));

const InteractionLayerControls = SemanticInteractionLayers.map(layer => ({
    ...layer,
    group: 'Interactions',
    cost: 'medium' as const,
    recommended: false,
}));

const RdkitLayerControls = RdkitChemicalLayers.map(layer => ({
    ...layer,
    group: 'RDKit chemistry',
    recommended: false,
}));

const PharmacophoreLayerControls = PharmacophoreLayers.map(layer => ({
    ...layer,
    group: 'Pharmacophore',
    recommended: false,
}));

const BondOrder3DLayerControls = BondOrder3DLayers.map(layer => ({
    ...layer,
    group: 'Pharmacophore',
    recommended: false,
}));

const FocusLayerControls = FocusSemanticLayers.map(layer => ({
    ...layer,
    recommended: false,
    description: `${layer.description} Source: ${layer.source}.`,
}));

// Expose ALL semantic layers — no artificial filter. The UpgradeGroups
// categorization provides the UI grouping; every layer that the chemistry
// substrate can compute should be reachable from the lab.
const AllStructuralLayerControls = StructuralLayerControls;
const AllChemicalLayerControls = ChemicalSemanticLayers.map(layer => ({ ...layer, group: 'Chemistry', recommended: false }));
const AllContextLayerControls = ContextSemanticLayers.map(layer => ({ ...layer, group: 'Molecular context', recommended: false }));
const AllEvidenceLayerControls = EvidenceSemanticLayers.map(layer => ({ ...layer, group: 'Input evidence', recommended: false }));
const SemanticLayerControls = [
    ...AllStructuralLayerControls,
    ...FocusLayerControls,
    ...InteractionLayerControls,
    ...AllChemicalLayerControls,
    ...AllContextLayerControls,
    ...AllEvidenceLayerControls,
    ...RdkitLayerControls,
    ...PharmacophoreLayerControls,
    ...BondOrder3DLayerControls,
] as const;
const VfxLayerControls = MolstarVisualUpgrades;
const VisualLayerControls = [...SemanticLayerControls, ...VfxLayerControls] as const;
const SemanticUpgradeGroups = UpgradeGroups.filter(group =>
    group.id !== 'Geometry' && group.id !== 'Sphere only' && group.id !== 'Material' &&
    group.id !== 'Surface only' && group.id !== 'Lighting' && group.id !== 'Environment' &&
    group.id !== 'Depth' && group.id !== 'Image' && group.id !== 'Focus' &&
    group.id !== 'Composition' && group.id !== 'Interaction' && group.id !== 'Experimental'
);
const VfxUpgradeGroups = UpgradeGroups.slice(9);
type UpgradeGroup = typeof UpgradeGroups[number];
 type MolecularLayerId = MolstarVisualUpgradeId | ChemicalSemanticLayerId | StructuralSemanticLayerId | SemanticInteractionLayerId | ContextSemanticLayerId | FocusSemanticLayerId | EvidenceSemanticLayerId | RdkitChemicalLayerId | PharmacophoreLayerId | BondOrder3DLayerId;

/**
 * The semantic controls are deliberately not presentation presets. This
 * compact guide makes their intended question, visual channel and absence
 * condition explicit at the point where a user turns them on.
 */
type SemanticLayerGuide = {
    readonly channel: 'recolour native geometry' | 'add contact geometry' | 'add source component geometry' | 'add ligand-local geometry';
    readonly expected: string;
    readonly use: string;
    readonly empty: string;
    /** A checked fixture from this lab's control suite; absent means no honest fixture exists yet. */
    readonly fixture?: '1CRN' | '1GRM' | '1CBS' | '1BNA' | '1EMA' | '4HHB' | '1TUP' | '2POR' | '7QPD';
    readonly representation: RepresentationId;
};

const SemanticLayerGuides: Readonly<Record<Exclude<MolecularLayerId, MolstarVisualUpgradeId>, SemanticLayerGuide>> = {
    'protein-role': { channel: 'recolour native geometry', expected: 'one protein-body colour across the existing polymer representation.', use: 'Use as the coarse class map; turn it off when secondary structure or a local focus should carry the colour channel.', empty: 'Empty when the model has no protein polymer.', fixture: '1EMA', representation: 'polymer-cartoon' },
    'nucleic-role': { channel: 'recolour native geometry', expected: 'one DNA/RNA colour on the existing backbone and bases.', use: 'Use to separate nucleic acid from protein in mixed complexes.', empty: 'Empty when the model has no nucleic acid.', fixture: '1BNA', representation: 'protein-and-nucleic' },
    'ligand-role': { channel: 'recolour native geometry', expected: 'a local ligand/cofactor accent inside the Native representation.', use: 'Use to locate a deposited small molecule before opening the ligand-focus geometry.', empty: 'Empty when no non-polymer ligand is present.', fixture: '1CBS', representation: 'polymer-and-ligand' },
    'glycan-role': { channel: 'recolour native geometry', expected: 'a carbohydrate-class accent, without adding an invented glycan model.', use: 'Use to distinguish observed glycans from protein or ligand.', empty: 'Empty when no branched carbohydrate is deposited.', fixture: '7QPD', representation: 'polymer-and-ligand' },
    'ion-role': { channel: 'recolour native geometry', expected: 'a compact ion accent on already-resolved ions.', use: 'Use to locate ions; use metal coordination separately to inspect contacts.', empty: 'Empty when no ion entity is present.', fixture: '1TUP', representation: 'atomic-detail' },
    'secondary-structure-identity': { channel: 'recolour native geometry', expected: 'helix, beta-strand and coil colours on the same Native polymer.', use: 'Use for fold reading; it intentionally competes with the broad protein-body colour.', empty: 'Empty when no protein secondary structure is available.', fixture: '2POR', representation: 'polymer-cartoon' },
    'binding-site-neighborhood': { channel: 'recolour native geometry', expected: 'a 5 Å residue-zone accent around a deposited ligand.', use: 'Use to ask which full residues form the local pocket, not which contacts are energetically strongest.', empty: 'Empty when no ligand is present.', fixture: '1CBS', representation: 'polymer-and-ligand' },
    'disulfide-bridges': { channel: 'recolour native geometry', expected: 'small cysteine-residue accents at explicit or Mol*-inferred disulfide links.', use: 'Use to inspect covalent stabilization; it does not draw a second artificial bond.', empty: 'Empty when no disulfide is found.', fixture: '1CRN', representation: 'atomic-detail' },
    'aromatic-rings': { channel: 'recolour native geometry', expected: 'local colour on atoms and bonds belonging to aromatic rings.', use: 'Use alongside ligand detail or pi contacts to read ring chemistry.', empty: 'Empty when Mol* finds no aromatic ring.', fixture: '1CBS', representation: 'atomic-detail' },
    'formal-positive-charge': { channel: 'recolour native geometry', expected: 'a local accent only on atoms whose input explicitly declares formal positive charge.', use: 'Use for deposited formal-charge annotation, not estimated electrostatics.', empty: 'Usually empty for files that omit pdbx_formal_charge.', representation: 'atomic-detail' },
    'formal-negative-charge': { channel: 'recolour native geometry', expected: 'a local accent only on atoms whose input explicitly declares formal negative charge.', use: 'Use for deposited formal-charge annotation, not estimated electrostatics.', empty: 'Usually empty for files that omit pdbx_formal_charge.', representation: 'atomic-detail' },
    'metal-centres': { channel: 'recolour native geometry', expected: 'a local metal-element accent.', use: 'Use to locate a metal centre before enabling its computed coordination contacts.', empty: 'Empty when no metal element is present.', fixture: '4HHB', representation: 'atomic-detail' },
    'partial-occupancy': { channel: 'recolour native geometry', expected: 'a warning accent for atom sites with occupancy below 1.', use: 'Use to inspect deposited disorder/partial population, never as a general confidence scale.', empty: 'Empty when occupancy is absent or every site is fully occupied.', fixture: '2POR', representation: 'atomic-detail' },
    'atomic-displacement': { channel: 'recolour native geometry', expected: 'an accent on the upper quintile of this structure’s B_iso_or_equiv values.', use: 'Use for relative atomic displacement within this file, not predicted confidence or resolution.', empty: 'Empty when B_iso_or_equiv is absent or uninformative.', fixture: '1EMA', representation: 'atomic-detail' },
    'alternate-locations': { channel: 'recolour native geometry', expected: 'an accent on atom sites carrying a non-empty alternate-location identifier.', use: 'Use to find deposited alternate conformers; it does not choose one for you.', empty: 'Empty when the input has no alternate locations.', fixture: '2POR', representation: 'atomic-detail' },
    'model-identity': { channel: 'recolour native geometry', expected: 'a per-input-model palette on already-loaded atoms.', use: 'Use to distinguish deposited model identities, not to imply a trajectory.', empty: 'Empty for a single-model structure.', fixture: '1GRM', representation: 'atomic-detail' },
    'hydrogen-bonds': { channel: 'add contact geometry', expected: 'thin cyan dashed links between geometry-qualified ligand-local endpoints.', use: 'Use with ligand focus to inspect a pocket contact and its measured endpoint distance.', empty: 'Empty when Mol* finds no geometry-qualified hydrogen bond.', fixture: '1CBS', representation: 'polymer-and-ligand' },
    'ionic-contacts': { channel: 'add contact geometry', expected: 'thin pale-blue dashed charged-group links.', use: 'Use to inspect candidate salt-bridge-like contacts, not a force-field energy.', empty: 'Empty when no qualifying ionic contact is computed.', fixture: '1CBS', representation: 'atomic-detail' },
    'pi-interactions': { channel: 'add contact geometry', expected: 'purple and magenta solid links for stacking and cation–pi contacts.', use: 'Use with aromatic-rings or ligand-detail to explain local recognition.', empty: 'Empty when no qualifying pi interaction is computed.', fixture: '1CBS', representation: 'atomic-detail' },
    'metal-coordination-contacts': { channel: 'add contact geometry', expected: 'gold solid links between Mol*-computed metal and ligand features.', use: 'Use after locating a metal centre; this is the coordination graph, not just a metal colour.', empty: 'Empty when no coordination is computed.', fixture: '4HHB', representation: 'atomic-detail' },
    'crystallographic-water': { channel: 'add source component geometry', expected: 'small source-water spheres only where water is actually deposited.', use: 'Use for observed structural waters, not hydration prediction.', empty: 'Empty when the input contains no water entity.', fixture: '1CBS', representation: 'polymer-and-ligand' },
    'native-membrane-lipids': { channel: 'add source component geometry', expected: 'ball-and-stick lipid components that exist in the input.', use: 'Use for resolved lipid context; it never fills in a membrane plane.', empty: 'Empty when no lipid entity is deposited.', representation: 'polymer-and-ligand' },
    'branched-glycans': { channel: 'add source component geometry', expected: 'a separate observed-glycan representation.', use: 'Use when glycan geometry would be hidden by the chosen Native representation.', empty: 'Empty when no branched carbohydrate is deposited.', fixture: '7QPD', representation: 'polymer-and-ligand' },
    'resolved-ions': { channel: 'add source component geometry', expected: 'a separate source-ion representation.', use: 'Use when ions are too small or hidden in the Native representation.', empty: 'Empty when no ion entity is deposited.', fixture: '1TUP', representation: 'atomic-detail' },
    'ligand-detail': { channel: 'add ligand-local geometry', expected: 'an element-coloured ball-and-stick view of the deposited ligand.', use: 'Use to inspect ligand chemistry while retaining the Native cartoon underneath.', empty: 'Empty when no deposited ligand is present.', fixture: '1CBS', representation: 'polymer-and-ligand' },
    'ligand-interface-residues': { channel: 'add ligand-local geometry', expected: 'a restrained ball-and-stick shell of whole residues inside the configured ligand cutoff.', use: 'Use for pocket composition; interaction layers answer the more specific contact question.', empty: 'Empty when no deposited ligand is present.', fixture: '1CBS', representation: 'polymer-and-ligand' },
    'ligand-pocket-surface': { channel: 'add ligand-local geometry', expected: 'a translucent local molecular surface around the configured ligand neighbourhood.', use: 'Use for enclosure and shape; it is intentionally a local surface, never a whole-protein surface.', empty: 'Empty when no deposited ligand is present.', fixture: '1CBS', representation: 'polymer-and-ligand' },
    'aromaticity-rdkit': { channel: 'recolour native geometry', expected: 'a purple accent on ligand atoms RDKit perceives as aromatic.', use: 'Use for accurate aromaticity on drug fragments where mol* perception misses fused heterocycles.', empty: 'Empty when RDKit perceives no aromatic atoms (no ligand, or fully saturated ligand).', fixture: '1CBS', representation: 'atomic-detail' },
    'donor-acceptor-rdkit': { channel: 'recolour native geometry', expected: 'cyan accent on Lipinski H-bond donor atoms, orange accent on acceptor atoms.', use: 'Use to read pharmacokinetic potential of the ligand; donor/acceptor counts feed Lipinski/Veber rules.', empty: 'Empty when RDKit finds no Lipinski donor or acceptor on the ligand.', fixture: '1CBS', representation: 'atomic-detail' },
    'partial-charge-rdkit': { channel: 'recolour native geometry', expected: 'a red-white-blue gradient on ligand atoms coloured by Gasteiger partial charge.', use: 'Use to estimate electrostatic character of the ligand; this is computed, not experimental.', empty: 'Empty when RDKit fails to compute Gasteiger charges (no ligand, or parsing failure).', fixture: '1CBS', representation: 'atomic-detail' },
    'stereo-rdkit': { channel: 'recolour native geometry', expected: 'blue accent on R chiral atoms, red on S, yellow on undefined (? potential centers).', use: 'Use to verify deposited stereochemistry and spot ambiguous chiral centers in drug fragments.', empty: 'Empty when the ligand has no chiral centers (fully planar or symmetric).', fixture: '1CBS', representation: 'atomic-detail' },
    'ring-atoms-rdkit': { channel: 'recolour native geometry', expected: 'a uniform accent on every atom in any SSSR ring (aliphatic or aromatic).', use: 'Use to read the molecular scaffold at a glance — distinguishes the ring backbone from chain substituents.', empty: 'Empty when the ligand is acyclic (no rings at all).', fixture: '1CBS', representation: 'atomic-detail' },
    'sp3-carbons-rdkit': { channel: 'recolour native geometry', expected: 'an accent on every saturated sp³-hybridized carbon (4 single bonds, tetrahedral).', use: 'Use to read 3D-character of the scaffold — high sp³ fraction correlates with drug-likeness and lead-likeness (see Property Cockpit).', empty: 'Empty when the ligand has no sp³ carbons (fully aromatic / planar / unsaturated).', representation: 'atomic-detail' },
    'reactive-groups-rdkit': { channel: 'recolour native geometry', expected: 'red accents on atoms in aldehyde, Michael acceptor, epoxide, acyl halide, alkyl halide, nitro, disulfide, peroxide, azide, or isocyanate groups.', use: 'Use to flag compounds with potential assay interference or covalent reactivity before progressing to screening.', empty: 'Empty when no known reactive group is detected — a clean result.', representation: 'atomic-detail' },
    'bond-order-rdkit': { channel: 'recolour native geometry', expected: 'blue accents on atoms in double bonds, purple on triple bonds.', use: 'Use to see WHERE unsaturation is without switching to 2D.', empty: 'Empty when the ligand has no double or triple bonds.', fixture: '1CBS', representation: 'atomic-detail' },
    'pains-rdkit': { channel: 'recolour native geometry', expected: 'bright magenta accents on atoms matching known PAINS substructures (rhodanines, catechols, quinones, Michael acceptors, etc.).', use: 'Use to flag compounds with potential assay interference before progressing to screening.', empty: 'Empty when no PAINS pattern matches — a clean result.', representation: 'atomic-detail' },
    'pharmacophore-features-rdkit': { channel: 'add ligand-local geometry', expected: 'H-bond acceptor cones (red), donor sticks (blue), aromatic ring disks (amber), and hydrophobic halos (grey) drawn over the ligand.', use: 'Use to read the ligand recognition profile at a glance; this is the chemist\'s shorthand for "what does this molecule bind with".', empty: 'Empty when no ligand is present or RDKit perception yields no features.', fixture: '1CBS', representation: 'atomic-detail' },
    'bond-order-3d-rdkit': { channel: 'add ligand-local geometry', expected: 'extra parallel cylinders next to double bonds (1 extra) and triple bonds (2 extra), ChemDraw-style.', use: 'Use to visually distinguish C=C, C≡C in 3D without switching to 2D depiction.', empty: 'Empty when the ligand has no double or triple bonds.', fixture: '1CBS', representation: 'atomic-detail' },
};

const CoreSemanticLegends: Partial<Record<Exclude<MolecularLayerId, MolstarVisualUpgradeId>, string>> = {
    'secondary-structure-identity': 'Legend · α-helix pink · 3₁₀ teal · π violet · β gold · turn amber · bend grey · coil slate',
    'ligand-detail': 'Legend · element colours · carbon grey · nitrogen blue · oxygen red · sulfur gold',
    'ligand-interface-residues': 'Legend · mint side-chain sticks · whole residues inside the current cutoff',
    'ligand-pocket-surface': 'Legend · translucent cyan · molecular surface of the current residue shell',
    'hydrogen-bonds': 'Legend · cyan dashed contact + endpoint arrow · ligand-local geometry-qualified H-bond',
};

function isChemicalLayer(id: MolecularLayerId): id is ChemicalSemanticLayerId {
    return ChemicalSemanticLayers.some(layer => layer.id === id);
}

function isVfxLayer(id: MolecularLayerId): id is MolstarVisualUpgradeId {
    return MolstarVisualUpgrades.some(layer => layer.id === id);
}

function isStructuralLayer(id: MolecularLayerId): id is StructuralSemanticLayerId {
    return StructuralSemanticLayers.some(layer => layer.id === id);
}

function isInteractionLayer(id: MolecularLayerId): id is SemanticInteractionLayerId {
    return SemanticInteractionLayers.some(layer => layer.id === id);
}

function isContextLayer(id: MolecularLayerId): id is ContextSemanticLayerId {
    return ContextSemanticLayers.some(layer => layer.id === id);
}

function isFocusLayer(id: MolecularLayerId): id is FocusSemanticLayerId {
    return FocusSemanticLayers.some(layer => layer.id === id);
}

function isEvidenceLayer(id: MolecularLayerId): id is EvidenceSemanticLayerId {
    return EvidenceSemanticLayers.some(layer => layer.id === id);
}

function isRdkitLayer(id: MolecularLayerId): id is RdkitChemicalLayerId {
    return RdkitChemicalLayers.some(layer => layer.id === id);
}

function isPharmacophoreLayer(id: MolecularLayerId): id is PharmacophoreLayerId {
    return PharmacophoreLayers.some(layer => layer.id === id);
}

function isBondOrder3DLayer(id: MolecularLayerId): id is BondOrder3DLayerId {
    return BondOrder3DLayers.some(layer => layer.id === id);
}

const MesoscaleCopyTag = 'mn-vfx-mesoscale-copy';
const MesoscaleCopySlots = [
    { id: 'mesoscale-copy-upper-left', offset: [-1, 0.16, -0.22] },
    { id: 'mesoscale-copy-right', offset: [1, -0.08, -0.35] },
    { id: 'mesoscale-copy-lower', offset: [0.12, -0.85, 0.18] },
] as const satisfies readonly { id: MolstarVisualUpgradeId, offset: readonly [number, number, number] }[];

function byId<T extends HTMLElement>(id: string): T {
    const element = document.getElementById(id);
    if (!element) throw new Error(`Missing element #${id}`);
    return element as T;
}

class MolecularVfxLab {
    private workbench?: Awaited<ReturnType<typeof createChemWorkbench>>;
    private baseline?: MolstarVisualSnapshot;
    private currentMolecule = MolecularControls.find(control => control.id === '1EMA')!;
    private currentRepresentation: RepresentationId = 'polymer-and-ligand';
    private enabledUpgrades = new Set<MolecularLayerId>(RecommendedMolstarVisualUpgrades);
    private framedLigandMolecule?: string;
    private ligandTargets: readonly LigandFocusTarget[] = [];
    private selectedLigandTargetId?: string;
    private ligandCutoff = 5;
    // interactionRecords removed — renderContactLedger now fetches on-demand from all interaction types
    private ligandDepictionAtomPositions: AtomPosition[] = [];
    private smartsSearchMolfile: string | null = null;
    private smilesMolfile: string | null = null;
    private smartsSearchTimer: ReturnType<typeof setTimeout> | null = null;
    /** Set when the current scene came from /embed — the authoritative molfile
     * for the whole facet cascade (no CCD data exists for imports). */
    private importedMolfile: string | null = null;
    private busy = false;

    async init() {
        this.workbench = await createChemWorkbench({ target: byId('viewport') });
        this.workbench.plugin.selectionMode = true;
        this.workbench.plugin.managers.interactivity.setProps({ granularity: 'residue' });
        this.workbench.plugin.managers.structure.selection.events.changed.subscribe(() => {
            this.refreshMetrics();
            this.updateLigandDepictionSelectionHighlights();
        });
        this.workbench.plugin.behaviors.interaction.click.subscribe(({ current }) => this.handleInteractionClick(current.loci));
        // Other agents' facets — wrap in try/catch so their failures don't
        // crash the core lab init and make the UI disappear.
        try { initFieldWellsPanel(this.workbench.plugin); } catch (e) { console.error('[fields] init failed:', e); }
        try { initPharmacophoreDesigner(this.workbench.plugin); } catch (e) { console.error('[designer] init failed:', e); }
        await this.workbench.setBackground(Color(0x0d141b));
        this.createControls();
        await this.loadMolecule(this.currentMolecule);
        this.baseline = captureMolstarVisualState(this.workbench.plugin);
        await this.applyRepresentationAndVisuals();
        this.refreshMetrics();
        byId('status').textContent = 'Ready';
    }

    private createControls() {
        const molecule = byId<HTMLSelectElement>('molecule');
        for (const control of MolecularControls) molecule.add(new Option(control.label, control.id));
        molecule.value = this.currentMolecule.id;
        molecule.addEventListener('change', () => {
            const next = MolecularControls.find(control => control.id === molecule.value);
            if (next) void this.perform(async () => {
                this.currentMolecule = next;
                await this.loadMolecule(next);
                await this.applyRepresentationAndVisuals();
            });
        });

        const importRun = byId<HTMLButtonElement>('import-run');
        const importSmiles = byId<HTMLInputElement>('import-smiles');
        const runImport = () => {
            const smiles = importSmiles?.value.trim();
            if (!smiles) return;
            void this.perform(() => this.importMolecule(smiles));
        };
        importRun?.addEventListener('click', runImport);
        importSmiles?.addEventListener('keydown', e => { if (e.key === 'Enter') runImport(); });

        const representation = byId<HTMLSelectElement>('representation');
        for (const control of RepresentationControls) representation.add(new Option(control.label, control.id));
        representation.value = this.currentRepresentation;
        representation.addEventListener('change', () => {
            this.currentRepresentation = representation.value as RepresentationId;
            void this.perform(() => this.applyRepresentationAndVisuals());
        });

        const granularity = byId<HTMLSelectElement>('granularity');
        granularity.addEventListener('change', () => {
            this.workbench?.plugin.managers.interactivity.setProps({ granularity: granularity.value as Loci.Granularity });
        });
        byId('clear-selection').addEventListener('click', () => {
            this.workbench?.plugin.managers.interactivity.lociSelects.deselectAll();
            this.refreshMetrics();
        });

        const ligandTarget = byId<HTMLSelectElement>('ligand-target');
        ligandTarget.addEventListener('change', () => {
            this.selectedLigandTargetId = ligandTarget.value || undefined;
            this.framedLigandMolecule = undefined;
            void this.perform(async () => {
                await this.applySemanticLayers();
                this.focusLigandOnce(true);
            });
        });
        const cutoff = byId<HTMLInputElement>('ligand-cutoff');
        const cutoffValue = byId<HTMLOutputElement>('ligand-cutoff-value');
        cutoff.addEventListener('input', () => {
            cutoffValue.value = `${Number(cutoff.value).toFixed(1)} Å`;
        });
        cutoff.addEventListener('change', () => {
            this.ligandCutoff = Number(cutoff.value);
            this.framedLigandMolecule = undefined;
            void this.perform(async () => {
                await this.applySemanticLayers();
                this.focusLigandOnce(true);
            });
        });

        this.createLayerSurface('semantic-layer-tabs', 'semantic-upgrades', SemanticUpgradeGroups, 'semantic');
        this.createLayerSurface('vfx-layer-tabs', 'vfx-upgrades', VfxUpgradeGroups, 'vfx');

        byId('restore-pareto-1').addEventListener('click', () => {
            this.enabledUpgrades = new Set(RecommendedMolstarVisualUpgrades);
            document.querySelectorAll<HTMLInputElement>('[data-upgrade]').forEach(checkbox => {
                checkbox.checked = this.enabledUpgrades.has(checkbox.dataset.upgrade as MolecularLayerId);
            });
            this.refreshLayerNavigation();
            void this.perform(() => this.applyVisuals());
        });
        this.refreshLayerNavigation();

        // SMARTS substructure search — debounced, validates on input, applies Overpaint.
        const smartsInput = byId<HTMLInputElement>('smarts-input');
        smartsInput.addEventListener('input', () => {
            if (this.smartsSearchTimer) clearTimeout(this.smartsSearchTimer);
            this.smartsSearchTimer = setTimeout(() => void this.runSmartsSearch(smartsInput.value), 350);
        });

        // SMILES input — loads ANY molecule without needing a PDB structure.
        // Bypasses ComponentBond entirely; feeds SMILES directly to RDKit.
        const smilesInput = byId<HTMLInputElement>('smiles-input');
        let smilesTimer: ReturnType<typeof setTimeout> | null = null;
        smilesInput.addEventListener('input', () => {
            if (smilesTimer) clearTimeout(smilesTimer);
            smilesTimer = setTimeout(() => void this.loadFromSmiles(smilesInput.value), 400);
        });

        // Atom indices toggle on 2D SVG (debug + selection aid)
        const atomIdxToggle = byId<HTMLInputElement>('show-atom-indices');
        if (atomIdxToggle) {
            atomIdxToggle.addEventListener('change', () => {
                void this.perform(() => this.renderLigandDepiction());
            });
        }

        // Descriptor click→highlight: clicking a Property Cockpit gauge that
        // has data-toggle-layer toggles the corresponding RDKit layer in 3D.
        document.addEventListener('click', (e) => {
            const cell = (e.target as HTMLElement).closest('[data-toggle-layer]');
            if (!cell) return;
            const layerId = cell.getAttribute('data-toggle-layer') as MolecularLayerId;
            if (!layerId) return;
            const cb = document.querySelector<HTMLInputElement>(`[data-upgrade="${layerId}"]`);
            if (!cb || cb.disabled) return;
            cb.checked = !cb.checked;
            if (cb.checked) this.enabledUpgrades.add(layerId);
            else this.enabledUpgrades.delete(layerId);
            this.refreshLayerNavigation();
            void this.perform(async () => {
                await this.applySemanticLayers();
            });
        });

        // Copy-to-clipboard for canonical identifiers.
        document.querySelectorAll<HTMLButtonElement>('.btn-copy[data-copy]').forEach(btn => {
            btn.addEventListener('click', () => {
                const target = document.getElementById(btn.dataset.copy || '');
                if (!target?.textContent || target.textContent === '—') return;
                navigator.clipboard?.writeText(target.textContent).then(() => {
                    btn.dataset.copied = 'true';
                    const originalText = btn.textContent;
                    btn.textContent = 'copied';
                    setTimeout(() => { btn.dataset.copied = 'false'; btn.textContent = originalText; }, 1200);
                }).catch(() => { /* ignore */ });
            });
        });
    }

    private createLayerSurface(
        tabsId: string,
        upgradesId: string,
        groups: readonly UpgradeGroup[],
        scope: 'semantic' | 'vfx',
    ) {
        const tabs = byId(tabsId);
        const upgrades = byId(upgradesId);
        for (const [groupIndex, group] of groups.entries()) {
            const groupUpgrades = VisualLayerControls.filter(upgrade => upgrade.group === group.id);
            const tab = document.createElement('button');
            tab.type = 'button';
            tab.className = 'layer-tab';
            tab.dataset.group = group.id;
            tab.dataset.layerScope = scope;
            tab.setAttribute('role', 'tab');
            tab.setAttribute('aria-selected', String(groupIndex === 0));
            const tabLabel = document.createElement('span');
            tabLabel.textContent = group.label;
            const tabCount = document.createElement('small');
            tabCount.dataset.groupCount = group.id;
            tab.append(tabLabel, tabCount);
            tabs.appendChild(tab);

            const panel = document.createElement('section');
            panel.className = 'layer-panel';
            panel.dataset.groupPanel = group.id;
            panel.dataset.layerScope = scope;
            panel.hidden = groupIndex !== 0;
            panel.setAttribute('role', 'tabpanel');
            const panelHeader = document.createElement('header');
            const panelTitle = document.createElement('h3');
            panelTitle.textContent = group.label;
            const panelDescription = document.createElement('p');
            panelDescription.textContent = group.description;
            panelHeader.append(panelTitle, panelDescription);
            panel.appendChild(panelHeader);

            for (const upgrade of groupUpgrades) {
            const row = document.createElement('div');
            row.className = 'upgrade';
            row.title = upgrade.description;
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'upgrade-toggle';
            checkbox.dataset.upgrade = upgrade.id;
            checkbox.setAttribute('aria-label', upgrade.label);
            checkbox.checked = this.enabledUpgrades.has(upgrade.id);
            const available = !('available' in upgrade) || upgrade.available !== false;
            if (!available) {
                checkbox.disabled = true;
                checkbox.dataset.unavailable = 'true';
                row.classList.add('upgrade-unavailable');
            }
            checkbox.addEventListener('change', () => {
                if (checkbox.checked) this.enabledUpgrades.add(upgrade.id);
                else this.enabledUpgrades.delete(upgrade.id);
                this.refreshLayerNavigation();
                void this.perform(async () => {
                    if (scope === 'semantic') {
                        await this.applySemanticLayers();
                        if (checkbox.checked && (isFocusLayer(upgrade.id) || upgrade.id === 'hydrogen-bonds')) this.focusLigandOnce();
                    } else {
                        await this.applyVisuals();
                    }
                });
            });
            const copy = document.createElement('span');
            copy.className = 'upgrade-copy';
            const title = document.createElement('strong');
            title.textContent = upgrade.label;
            if (upgrade.recommended) {
                const mark = document.createElement('sup');
                mark.className = 'pareto-mark';
                mark.textContent = '1';
                mark.title = 'Included in the confirmed Pareto 1 default';
                title.append(' ', mark);
            }
            const description = document.createElement('span');
            description.textContent = upgrade.description;
            copy.append(title, description);
            const guide = SemanticLayerGuides[upgrade.id as Exclude<MolecularLayerId, MolstarVisualUpgradeId>];
            if (guide) {
                const availability = document.createElement('span');
                availability.className = 'semantic-availability';
                availability.dataset.semanticAvailability = upgrade.id;
                availability.textContent = 'Checking current structure…';
                const guidance = document.createElement('span');
                guidance.className = 'semantic-guidance';
                const representation = RepresentationControls.find(control => control.id === guide.representation);
                guidance.textContent = `Expected: ${guide.expected} Use: ${guide.use} Empty: ${guide.empty} Best Native view: ${representation?.label ?? guide.representation}.`;
                const channel = document.createElement('span');
                channel.className = 'semantic-channel';
                channel.textContent = guide.channel;
                const legend = document.createElement('span');
                legend.className = 'semantic-legend';
                legend.textContent = CoreSemanticLegends[upgrade.id as Exclude<MolecularLayerId, MolstarVisualUpgradeId>] ?? '';
                const debug = document.createElement('button');
                debug.type = 'button';
                debug.className = 'semantic-debug';
                debug.dataset.semanticDebug = upgrade.id;
                if (guide.fixture) {
                    debug.textContent = `Open checked view · ${guide.fixture}`;
                    debug.title = `Loads ${guide.fixture} in ${representation?.label ?? guide.representation}, restores Pareto 1 VFX, and enables only this semantic layer.`;
                    debug.addEventListener('click', () => {
                        void this.perform(() => this.openSemanticDebug(upgrade.id as Exclude<MolecularLayerId, MolstarVisualUpgradeId>, guide));
                    });
                } else {
                    debug.textContent = 'No positive fixture in this suite';
                    debug.title = 'The current nine structures do not contain a positive source example for this layer. It remains available for a structure that does.';
                    debug.disabled = true;
                }
                copy.append(availability, guidance, legend, channel, debug);
            }
            const cost = document.createElement('em');
            cost.className = available ? `cost cost-${upgrade.cost}` : 'cost cost-unavailable';
            cost.textContent = available ? upgrade.cost : 'not realtime';
            row.append(checkbox, copy, cost);
            panel.appendChild(row);
            }

            tab.addEventListener('click', () => this.selectUpgradeGroup(group.id, scope));
            upgrades.appendChild(panel);
        }
    }

    private selectUpgradeGroup(group: UpgradeGroup['id'], scope: 'semantic' | 'vfx') {
        document.querySelectorAll<HTMLButtonElement>(`[data-layer-scope="${scope}"][data-group]`).forEach(tab => {
            const active = tab.dataset.group === group;
            tab.setAttribute('aria-selected', String(active));
        });
        document.querySelectorAll<HTMLElement>(`[data-layer-scope="${scope}"][data-group-panel]`).forEach(panel => {
            panel.hidden = panel.dataset.groupPanel !== group;
        });
    }

    private refreshLayerNavigation() {
        for (const group of UpgradeGroups) {
            const controls = VisualLayerControls.filter(upgrade => upgrade.group === group.id);
            const enabled = controls.filter(upgrade => this.enabledUpgrades.has(upgrade.id)).length;
            const count = document.querySelector<HTMLElement>(`[data-group-count="${group.id}"]`);
            if (count) count.textContent = `${enabled}/${controls.length}`;
        }
        const semanticEnabled = SemanticLayerControls.filter(layer => this.enabledUpgrades.has(layer.id)).length;
        byId('semantic-enabled-summary').textContent = `${semanticEnabled} enabled · ${SemanticLayerControls.length} semantic controls`;
        const vfxEnabled = VfxLayerControls.filter(layer => this.enabledUpgrades.has(layer.id)).length;
        byId('layer-enabled-summary').textContent = `${vfxEnabled} enabled · ${VfxLayerControls.length} VFX controls`;
    }

    private refreshSemanticAvailability() {
        if (!this.workbench) return;
        const structure = this.workbench.plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
        if (!structure) return;

        const proteinSelection = StructureSelectionQueries.protein.query(new QueryContext(structure.root));
        const proteinLoci = StructureSelection.toLociWithCurrentUnits(proteinSelection);
        const proteinElements = StructureElement.Loci.size(proteinLoci);
        const focus = getFocusSemanticLayerCounts(structure, this.currentFocusOptions());
        const interactions = getInteractionSemanticLayerCounts(this.workbench.plugin);
        const ligandAvailable = focus['ligand-detail'].elements > 0;
        const hydrogenBondsEnabled = this.enabledUpgrades.has('hydrogen-bonds');

        const state: Record<string, { available: boolean, text: string, signal: 'ready' | 'empty' | 'active' }> = {
            'secondary-structure-identity': {
                available: proteinElements > 0,
                text: proteinElements > 0 ? `${structure.polymerResidueCount} protein polymer residues` : 'Not present · no protein polymer',
                signal: proteinElements > 0 ? 'ready' : 'empty',
            },
            'ligand-detail': {
                available: ligandAvailable,
                text: ligandAvailable ? `${focus['ligand-detail'].elements} ligand atoms` : 'Not present · no deposited ligand',
                signal: ligandAvailable ? 'ready' : 'empty',
            },
            'ligand-interface-residues': {
                available: ligandAvailable,
                text: ligandAvailable ? `${focus['ligand-interface-residues'].residues} nearby residues · ${this.ligandCutoff.toFixed(1)} Å cutoff` : 'Not present · no deposited ligand',
                signal: ligandAvailable ? 'ready' : 'empty',
            },
            'ligand-pocket-surface': {
                available: ligandAvailable,
                text: ligandAvailable ? `${focus['ligand-pocket-surface'].residues} residues define the local surface` : 'Not present · no deposited ligand',
                signal: ligandAvailable ? 'ready' : 'empty',
            },
            'hydrogen-bonds': {
                available: ligandAvailable,
                text: !ligandAvailable
                    ? 'Not present · no ligand to scope contacts'
                    : hydrogenBondsEnabled
                        ? `${interactions['hydrogen-bonds']} ligand-local hydrogen bonds computed`
                        : 'Ready to compute · ligand-local only',
                signal: !ligandAvailable ? 'empty' : hydrogenBondsEnabled ? 'active' : 'ready',
            },
        };

        for (const layer of SemanticLayerControls) {
            const status = state[layer.id];
            if (!status) continue;
            const live = document.querySelector<HTMLElement>(`[data-semantic-availability="${layer.id}"]`);
            if (live) {
                live.textContent = status.text;
                live.dataset.signal = status.signal;
            }
            const checkbox = document.querySelector<HTMLInputElement>(`[data-upgrade="${layer.id}"]`);
            if (!checkbox) continue;
            const row = checkbox.closest('.upgrade');
            if (status.available) {
                delete checkbox.dataset.unavailable;
                if (!this.busy) checkbox.disabled = false;
                row?.classList.remove('upgrade-unavailable');
            } else {
                this.enabledUpgrades.delete(layer.id);
                checkbox.checked = false;
                checkbox.dataset.unavailable = 'true';
                checkbox.disabled = true;
                row?.classList.add('upgrade-unavailable');
            }
        }
        this.refreshLayerNavigation();
        void this.refreshRdkitAvailability(ligandAvailable);
    }

    /**
     * RDKit counts require async WASM calls. Populated separately from the
     * synchronous layer availability so the rest of the UI is not blocked on
     * RDKit init. Falls back to a 'computing…' state if RDKit is still loading
     * or fails to parse the ligand.
     */
    private async refreshRdkitAvailability(ligandAvailable: boolean) {
        if (!this.workbench) return;
        const structure = this.workbench.plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
        if (!structure) return;

        const rdkitLayerIds: RdkitChemicalLayerId[] = ['aromaticity-rdkit', 'donor-acceptor-rdkit', 'partial-charge-rdkit', 'stereo-rdkit', 'ring-atoms-rdkit', 'sp3-carbons-rdkit', 'reactive-groups-rdkit', 'bond-order-rdkit', 'pains-rdkit'];

        if (!ligandAvailable) {
            for (const id of rdkitLayerIds) this.setRdkitAvailability(id, { available: false, text: 'Not present · no deposited ligand', signal: 'empty' });
            this.setRdkitAvailability('pharmacophore-features-rdkit', { available: false, text: 'Not present · no deposited ligand', signal: 'empty' });
            return;
        }

        for (const id of rdkitLayerIds) this.setRdkitAvailability(id, { available: true, text: 'Computing via RDKit-WASM…', signal: 'ready' });

        try {
            const counts = await getRdkitChemicalLayerCounts(structure, this.currentFocusOptions());
            if (!counts.hasLigand) {
                for (const id of rdkitLayerIds) this.setRdkitAvailability(id, { available: false, text: 'Not present · no ligand molfile', signal: 'empty' });
                return;
            }
            this.setRdkitAvailability('aromaticity-rdkit', {
                available: counts.aromatic > 0,
                text: counts.aromatic > 0 ? `${counts.aromatic} aromatic atoms (RDKit perception)` : 'No aromatic atoms (RDKit)',
                signal: counts.aromatic > 0 ? 'ready' : 'empty',
            });
            this.setRdkitAvailability('donor-acceptor-rdkit', {
                available: counts.donors + counts.acceptors > 0,
                text: `${counts.donors} donor${counts.donors === 1 ? '' : 's'} · ${counts.acceptors} acceptor${counts.acceptors === 1 ? '' : 's'} (Lipinski SMARTS)`,
                signal: counts.donors + counts.acceptors > 0 ? 'ready' : 'empty',
            });
            this.setRdkitAvailability('partial-charge-rdkit', {
                available: counts.partialChargeRange !== null,
                text: counts.partialChargeRange
                    ? `Approx range ${counts.partialChargeRange[0].toFixed(2)} .. ${counts.partialChargeRange[1].toFixed(2)} (Allred-Rochow)`
                    : 'Partial charge unavailable',
                signal: counts.partialChargeRange ? 'ready' : 'empty',
            });
            this.setRdkitAvailability('stereo-rdkit', {
                available: counts.chiralCentersR + counts.chiralCentersS + counts.chiralCentersUndefined > 0,
                text: `${counts.chiralCentersR}R · ${counts.chiralCentersS}S` + (counts.chiralCentersUndefined > 0 ? ` · ${counts.chiralCentersUndefined} undefined` : '') + ' (CIP)',
                signal: counts.chiralCentersR + counts.chiralCentersS + counts.chiralCentersUndefined > 0 ? 'ready' : 'empty',
            });
            this.setRdkitAvailability('ring-atoms-rdkit', {
                available: counts.ringAtoms > 0,
                text: counts.ringAtoms > 0 ? `${counts.ringAtoms} ring atoms (SSSR)` : 'No rings (acyclic ligand)',
                signal: counts.ringAtoms > 0 ? 'ready' : 'empty',
            });
            this.setRdkitAvailability('sp3-carbons-rdkit', {
                available: counts.sp3Carbons > 0,
                text: counts.sp3Carbons > 0 ? `${counts.sp3Carbons} sp³ carbons` : 'No sp³ carbons (fully aromatic / unsaturated)',
                signal: counts.sp3Carbons > 0 ? 'ready' : 'empty',
            });
            this.setRdkitAvailability('reactive-groups-rdkit', {
                available: counts.reactiveGroups.length > 0,
                text: counts.reactiveGroups.length > 0
                    ? `⚠ ${counts.reactiveGroups.join(', ')}`
                    : 'No reactive groups detected',
                signal: counts.reactiveGroups.length > 0 ? 'active' : 'empty',
            });
            this.setRdkitAvailability('bond-order-rdkit', {
                available: counts.doubleBondAtoms + counts.tripleBondAtoms > 0,
                text: counts.doubleBondAtoms + counts.tripleBondAtoms > 0
                    ? `${counts.doubleBondAtoms} double · ${counts.tripleBondAtoms} triple bond atoms`
                    : 'No double or triple bonds (fully saturated)',
                signal: counts.doubleBondAtoms + counts.tripleBondAtoms > 0 ? 'ready' : 'empty',
            });
            this.setRdkitAvailability('pains-rdkit', {
                available: counts.painsLabels.length > 0,
                text: counts.painsLabels.length > 0
                    ? `⚠ PAINS: ${counts.painsLabels.slice(0,3).join(', ')}${counts.painsLabels.length > 3 ? ` +${counts.painsLabels.length - 3}` : ''}`
                    : 'No PAINS matches — clean',
                signal: counts.painsLabels.length > 0 ? 'active' : 'empty',
            });
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            for (const id of rdkitLayerIds) this.setRdkitAvailability(id, { available: false, text: `RDKit error · ${message}`, signal: 'empty' });
        }
        // Pharmacophore feature counts (parallel RDKit computation; kept separate for clear copy).
        void this.refreshPharmacophoreAvailability(ligandAvailable);
    }

    private async refreshPharmacophoreAvailability(ligandAvailable: boolean) {
        if (!this.workbench) return;
        const structure = this.workbench.plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
        if (!structure) return;
        const id = 'pharmacophore-features-rdkit' as PharmacophoreLayerId;
        if (!ligandAvailable) {
            this.setRdkitAvailability(id, { available: false, text: 'Not present · no deposited ligand', signal: 'empty' });
            return;
        }
        this.setRdkitAvailability(id, { available: true, text: 'Computing pharmacophore features…', signal: 'ready' });
        try {
            const counts = await getPharmacophoreFeatureCounts(structure, this.currentFocusOptions());
            if (!counts.hasLigand) {
                this.setRdkitAvailability(id, { available: false, text: 'Not present · no ligand molfile', signal: 'empty' });
                return;
            }
            const total = counts.hba + counts.hbd + counts.aromatic + counts.hydrophobic;
            this.setRdkitAvailability(id, {
                available: total > 0,
                text: total > 0
                    ? `${counts.hba} HBA · ${counts.hbd} HBD · ${counts.aromatic} rings · ${counts.hydrophobic} hydrophobic`
                    : 'No pharmacophore features detected',
                signal: total > 0 ? 'ready' : 'empty',
            });
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            this.setRdkitAvailability(id, { available: false, text: `Pharmacophore error · ${message}`, signal: 'empty' });
        }
    }

    private setRdkitAvailability(id: RdkitChemicalLayerId | PharmacophoreLayerId, status: { available: boolean, text: string, signal: 'ready' | 'empty' | 'active' }) {
        const live = document.querySelector<HTMLElement>(`[data-semantic-availability="${id}"]`);
        if (live) {
            live.textContent = status.text;
            live.dataset.signal = status.signal;
        }
        const checkbox = document.querySelector<HTMLInputElement>(`[data-upgrade="${id}"]`);
        if (!checkbox) return;
        const row = checkbox.closest('.upgrade');
        if (status.available) {
            delete checkbox.dataset.unavailable;
            if (!this.busy) checkbox.disabled = false;
            row?.classList.remove('upgrade-unavailable');
        } else {
            this.enabledUpgrades.delete(id);
            checkbox.checked = false;
            checkbox.dataset.unavailable = 'true';
            checkbox.disabled = true;
            row?.classList.add('upgrade-unavailable');
        }
    }

    private currentLigandTarget() {
        return this.ligandTargets.find(target => target.id === this.selectedLigandTargetId);
    }

    private currentFocusOptions() {
        return { target: this.currentLigandTarget()?.bundle, cutoff: this.ligandCutoff };
    }

    private refreshLigandTargets() {
        if (!this.workbench) return;
        const structure = this.workbench.plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
        if (!structure) return;
        this.ligandTargets = getLigandFocusTargets(structure);
        if (!this.ligandTargets.some(target => target.id === this.selectedLigandTargetId)) {
            this.selectedLigandTargetId = this.ligandTargets[0]?.id;
        }

        const select = byId<HTMLSelectElement>('ligand-target');
        select.replaceChildren(...this.ligandTargets.map(target => new Option(`${target.label} · ${target.elementCount} atoms`, target.id)));
        select.value = this.selectedLigandTargetId ?? '';
        const available = this.ligandTargets.length > 0;
        select.disabled = !available;
        if (available) delete select.dataset.unavailable;
        else select.dataset.unavailable = 'true';

        const cutoff = byId<HTMLInputElement>('ligand-cutoff');
        cutoff.disabled = !available;
        if (available) delete cutoff.dataset.unavailable;
        else cutoff.dataset.unavailable = 'true';
        byId('ligand-target-summary').textContent = available
            ? `${this.ligandTargets.length} deposited ligand target${this.ligandTargets.length === 1 ? '' : 's'}`
            : 'No deposited ligand in this structure';
    }

    private renderContactLedger() {
        const ledger = byId('contact-ledger');
        ledger.replaceChildren();

        // Collect records from ALL enabled interaction types, not just H-bonds.
        const INTERACTION_LAYERS: Array<{ id: string; prefix: string; color: string }> = [
            { id: 'hydrogen-bonds', prefix: 'H', color: '#5fd0c8' },
            { id: 'ionic-contacts', prefix: 'I', color: '#4dabf7' },
            { id: 'pi-interactions', prefix: 'π', color: '#c792ea' },
            { id: 'metal-coordination-contacts', prefix: 'M', color: '#e8c45c' },
        ];
        const allRecords: Array<{ record: SemanticInteractionRecord; prefix: string; color: string }> = [];
        let anyEnabled = false;
        for (const { id, prefix, color } of INTERACTION_LAYERS) {
            if (!this.enabledUpgrades.has(id as MolecularLayerId)) continue;
            anyEnabled = true;
            const recs = getInteractionSemanticLayerRecords(this.workbench!.plugin, id as SemanticInteractionLayerId);
            for (const r of recs) allRecords.push({ record: r, prefix, color });
        }

        byId('contact-ledger-summary').textContent = anyEnabled
            ? `${allRecords.length} ligand-local interactions (${[...new Set(allRecords.map(r => r.prefix))].join('/')})`
            : 'Enable an interaction layer to compute the ledger';

        if (!anyEnabled || allRecords.length === 0) {
            const empty = document.createElement('p');
            empty.className = 'ledger-empty';
            empty.textContent = anyEnabled
                ? 'No geometry-qualified ligand interactions were found for this target.'
                : 'The ledger and 3D contact geometry share the same computed records.';
            ledger.appendChild(empty);
            return;
        }

        for (const { record, prefix, color } of allRecords) {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'contact-row';
            row.dataset.contactId = String(record.id);
            row.style.borderLeft = `3px solid ${color}`;
            const index = document.createElement('span');
            index.className = 'contact-index';
            index.textContent = `${prefix}${String(record.id + 1).padStart(2, '0')}`;
            index.style.color = color;
            const labels = document.createElement('strong');
            labels.textContent = `${record.aLabel} — ${record.bLabel}`;
            const distance = document.createElement('span');
            distance.className = 'contact-distance';
            distance.textContent = `${record.distance.toFixed(2)} Å`;
            row.append(index, labels, distance);
            row.addEventListener('click', () => this.focusContact(record));
            ledger.appendChild(row);
        }
    }

    private focusContact(record: SemanticInteractionRecord) {
        if (!this.workbench) return;
        const plugin = this.workbench.plugin;
        plugin.managers.interactivity.lociSelects.deselectAll();
        for (const loci of record.loci) plugin.managers.interactivity.lociSelects.select({ loci }, false);
        focusLociKeepingSlab(plugin, [...record.loci], { minRadius: 4, extraRadius: 3, durationMs: 250 });
        this.setActiveContact(record.id);
    }

    /**
     * 2D ligand depiction via RDKit-JS. Re-renders whenever the focused
     * ligand or any RDKit chemistry layer changes. Highlights mirror the
     * Semantics · RDKit toggles so the 2D and 3D views never disagree.
     */
    private async renderLigandDepiction() {
        if (!this.workbench) return;
        const target = byId('ligand-depiction');
        const summary = byId('ligand-depiction-summary');
        const stats = byId('ligand-depiction-stats');
        if (!target || !summary || !summary) return;

        // SMILES mode: use the SMILES-derived molfile directly, bypass PDB.
        if (this.smilesMolfile) {
            summary.textContent = 'Depicting SMILES…';
            const highlights: AtomHighlight[] = [];
            // Compute chemistry for highlights (same as PDB path)
            const { computeLigandChemistry } = await import('../chemistry.backend.perception.rdkit-wasm.editable/semantic-chemistry-rdkit');
            const countsLine = this.smilesMolfile.split('\n')[3] || '';
            const atomCount = parseInt(countsLine.slice(0, 3).trim(), 10) || 0;
            const chem = await computeLigandChemistry(this.smilesMolfile, atomCount);
            if (chem) {
                if (this.enabledUpgrades.has('aromaticity-rdkit')) {
                    for (let i = 0; i < atomCount; i++) if (chem.aromaticAtoms[i]) highlights.push({ atomIndex: i, color: '#c792ea', alpha: 0.55 });
                }
                if (this.enabledUpgrades.has('donor-acceptor-rdkit')) {
                    for (let i = 0; i < atomCount; i++) {
                        if (chem.donors[i]) highlights.push({ atomIndex: i, color: '#5fd0c8', alpha: 0.55 });
                        if (chem.acceptors[i]) highlights.push({ atomIndex: i, color: '#e1a14e', alpha: 0.55 });
                    }
                }
            }
            const showAtomIndices = byId<HTMLInputElement>('show-atom-indices')?.checked ?? false;
            const result = await LigandDepiction.depict(this.smilesMolfile, { atomHighlights: highlights, width: 760, height: 500, showAtomIndices });
            if (!result) { target.innerHTML = '<p class="ledger-empty">RDKit depiction failed.</p>'; summary.textContent = 'Depiction failed'; return; }
            target.innerHTML = result.svgString;
            this.ligandDepictionAtomPositions = result.atomPositions;
            const svg = target.querySelector('svg');
            if (svg) svg.addEventListener('click', (e: Event) => this.handleLigandDepictionClick(e as MouseEvent));
            summary.textContent = 'SMILES molecule';
            const aromCount = chem ? countSetBits8(chem.aromaticAtoms) : 0;
            const donorCount = chem ? countSetBits8(chem.donors) : 0;
            const acceptorCount = chem ? countSetBits8(chem.acceptors) : 0;
            stats.textContent = `${atomCount} atoms · ${aromCount} aromatic · ${donorCount} HBD · ${acceptorCount} HBA`;
            void renderPropertiesPanel(this.smilesMolfile, 'SMILES molecule');
            void this.refreshLigandIdentifiers(this.smilesMolfile);
            return;
        }

        // PDB mode: extract ligand from structure as before.
        const structure = this.workbench.plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
        if (!structure) {
            target.innerHTML = '<p class="ledger-empty">No structure loaded.</p>';
            summary.textContent = 'No structure';
            stats.textContent = '';
            return;
        }

        const ligandTarget = this.currentLigandTarget();
        let loci: StructureElement.Loci;
        if (ligandTarget) {
            loci = StructureElement.Bundle.toLoci(ligandTarget.bundle, structure);
        } else {
            const sel = StructureSelectionQueries.ligand.query(new QueryContext(structure.root));
            loci = StructureSelection.toLociWithCurrentUnits(sel);
        }

        if (StructureElement.Loci.isEmpty(loci)) {
            target.innerHTML = '<p class="ledger-empty">No deposited ligand in this structure.</p>';
            summary.textContent = 'No deposited ligand';
            stats.textContent = '';
            this.ligandDepictionAtomPositions = [];
            updateFieldWellsLigand(null, null);
            void updatePharmacophoreDesigner(null, this.currentFocusOptions(), { structureId: this.currentMolecule.id, ligandLabel: null });
            return;
        }

        // Imported molecules bypass loci→molfile reconstruction: that path
        // needs the CCD ComponentBond property, which a MOL-format import
        // does not carry — and the backend-embedded molfile IS the model the
        // scene was built from, so the atom-index contract holds by identity.
        let analysis: Awaited<ReturnType<typeof prepareLigandAnalysis>>;
        if (this.importedMolfile) {
            const atomCount = parseInt(this.importedMolfile.split('\n')[3]?.slice(0, 3) ?? '0', 10);
            const chemistry = atomCount > 0 ? await computeLigandChemistry(this.importedMolfile, atomCount) : null;
            analysis = atomCount > 0 ? { molfile: this.importedMolfile, atomCount, chemistry } : null;
        } else {
            analysis = await prepareLigandAnalysis(loci);
        }
        if (!analysis) {
            target.innerHTML = '<p class="ledger-empty">RDKit cannot parse this ligand (ComponentBond / CCD data unavailable).</p>';
            summary.textContent = 'RDKit parse failed';
            stats.textContent = '';
            updateFieldWellsLigand(null, null);
            void updatePharmacophoreDesigner(null, this.currentFocusOptions(), { structureId: this.currentMolecule.id, ligandLabel: null });
            return;
        }

        const highlights: AtomHighlight[] = [];
        if (analysis.chemistry) {
            if (this.enabledUpgrades.has('aromaticity-rdkit')) {
                for (let i = 0; i < analysis.atomCount; i++) {
                    if (analysis.chemistry.aromaticAtoms[i]) highlights.push({ atomIndex: i, color: '#c792ea', alpha: 0.55 });
                }
            }
            if (this.enabledUpgrades.has('donor-acceptor-rdkit')) {
                for (let i = 0; i < analysis.atomCount; i++) {
                    if (analysis.chemistry.donors[i]) highlights.push({ atomIndex: i, color: '#5fd0c8', alpha: 0.55 });
                    if (analysis.chemistry.acceptors[i]) highlights.push({ atomIndex: i, color: '#e1a14e', alpha: 0.55 });
                }
            }
        }

        const showAtomIndices = byId<HTMLInputElement>('show-atom-indices')?.checked ?? false;
        const result = await LigandDepiction.depict(analysis.molfile, {
            atomHighlights: highlights,
            // 2× density — SVG viewBox keeps it crisp, CSS scales for the panel.
            // Verified necessary to avoid atom overlap on macrocycles (HEM, C8E).
            width: 760,
            height: 500,
            showAtomIndices,
        });
        if (!result) {
            target.innerHTML = '<p class="ledger-empty">RDKit depiction failed.</p>';
            return;
        }
        target.innerHTML = result.svgString;
        this.ligandDepictionAtomPositions = result.atomPositions;

        const svg = target.querySelector('svg');
        if (svg) svg.addEventListener('click', (e: Event) => this.handleLigandDepictionClick(e as MouseEvent));

        summary.textContent = ligandTarget?.label ?? 'Ligand';
        const aromCount = analysis.chemistry ? countSetBits8(analysis.chemistry.aromaticAtoms) : 0;
        const donorCount = analysis.chemistry ? countSetBits8(analysis.chemistry.donors) : 0;
        const acceptorCount = analysis.chemistry ? countSetBits8(analysis.chemistry.acceptors) : 0;
        stats.textContent = `${analysis.atomCount} atoms · ${aromCount} aromatic · ${donorCount} HBD · ${acceptorCount} HBA`;

        // Property Optimization Cockpit facet reuses the same molfile
        // (computed once here) rather than re-running ligandLociToMolfile.
        void renderPropertiesPanel(analysis.molfile, ligandTarget?.label ?? null);
        // Field Wells facet: same molfile carries scene coordinates, so backend
        // cubes land aligned. A ligand change clears any displayed field.
        updateFieldWellsLigand(analysis.molfile, ligandTarget?.label ?? null);
        // Pharmacophore Designer facet: seeds its editable model from the same
        // focused ligand; keeps user edits while the source is unchanged.
        void updatePharmacophoreDesigner(structure, this.currentFocusOptions(), { structureId: this.currentMolecule.id, ligandLabel: ligandTarget?.label ?? 'Ligand' });
        // SMARTS search uses the same molfile.
        this.smartsSearchMolfile = analysis.molfile;
        // Re-run the SMARTS search against the new ligand (if input is non-empty).
        const smartsInput = byId<HTMLInputElement>('smarts-input');
        if (smartsInput?.value) void this.runSmartsSearch(smartsInput.value);
        // Compute and display canonical identifiers.
        void this.refreshLigandIdentifiers(analysis.molfile);
    }

    private async refreshLigandIdentifiers(molfile: string) {
        const ids = await computeLigandIdentifiers(molfile);
        const set = (id: string, value: string) => {
            const el = document.getElementById(id);
            if (el) el.textContent = value || '—';
        };
        if (!ids) {
            set('ligand-smiles', '');
            set('ligand-inchi', '');
            set('ligand-inchikey', '');
            return;
        }
        set('ligand-smiles', ids.smiles);
        set('ligand-inchi', ids.inchi);
        set('ligand-inchikey', ids.inchiKey);
    }

    /**
     * Load ANY molecule from a SMILES string. Bypasses the entire PDB/ComponentBond
     * pipeline — feeds SMILES directly to RDKit, gets a molfile, and runs all
     * chemistry + depiction + property computations on it. Also loads the molfile
     * into mol* so 3D Overpaint layers can color atoms.
     */
    private async loadFromSmiles(smiles: string) {
        const input = byId<HTMLInputElement>('smiles-input');
        const status = byId<HTMLElement>('smiles-status');
        if (!input || !status) return;

        if (!smiles.trim()) {
            input.dataset.error = 'false';
            status.textContent = 'Type a SMILES to analyze any molecule — no PDB structure needed.';
            this.smilesMolfile = null;
            return;
        }

        if (!this.workbench) return;
        status.textContent = 'Parsing SMILES…';
        try {
            const RDKit = await getRDKit();
            const mol = RDKit.get_mol(smiles.trim());
            if (!mol || !mol.is_valid()) {
                input.dataset.error = 'true';
                status.dataset.ok = 'false';
                status.textContent = 'Invalid SMILES — RDKit could not parse.';
                return;
            }
            input.dataset.error = 'false';

            // Generate 2D coords for depiction, then get molblock for all computations.
            try { mol.set_new_coords(true); } catch { /* keep whatever coords exist */ }
            const molfile = mol.get_molblock();
            const smilesCanonical = mol.get_smiles();
            mol.delete();

            if (!molfile || molfile.length < 50) {
                status.textContent = 'SMILES parsed but molfile generation failed.';
                return;
            }

            // Store as the active molfile for ALL chemistry features.
            this.smilesMolfile = molfile;
            this.smartsSearchMolfile = molfile;
            status.dataset.ok = 'true';
            status.textContent = `Parsed: ${smilesCanonical.slice(0, 60)}${smilesCanonical.length > 60 ? '…' : ''}`;

            // Render 2D depiction + properties + identifiers directly from molfile.
            // Do NOT load into mol* — small-molecule-only structures cause
            // StructureSelectionQueries to throw. 3D Overpaint is skipped in
            // SMILES mode; the 2D depiction carries the visual load.
            await this.renderLigandDepiction();
            void this.refreshLigandIdentifiers(molfile);
        } catch (error) {
            input.dataset.error = 'true';
            status.dataset.ok = 'false';
            status.textContent = `Error: ${error instanceof Error ? error.message : String(error)}`;
        }
    }

    private async runSmartsSearch(smarts: string) {        const input = byId<HTMLInputElement>('smarts-input');
        const status = byId<HTMLElement>('smarts-status');
        if (!input || !status) return;
        if (!this.workbench) return;

        // Empty input → clear overlay.
        if (!smarts.trim()) {
            input.dataset.error = 'false';
            status.dataset.ok = '';
            status.textContent = 'Type a SMARTS pattern to highlight matches on the ligand.';
            await applySmartsSearchOverlay(this.workbench.plugin, this.currentFocusOptions(), null);
            return;
        }
        if (!this.smartsSearchMolfile) {
            status.dataset.ok = 'false';
            status.textContent = 'No ligand loaded.';
            return;
        }

        status.textContent = 'Searching…';
        const result = await searchLigandSmarts(this.smartsSearchMolfile, smarts);
        if (!result) {
            input.dataset.error = 'true';
            status.dataset.ok = 'false';
            status.textContent = 'RDKit failed to parse the ligand molfile.';
            return;
        }
        if (!result.valid) {
            input.dataset.error = 'true';
            status.dataset.ok = 'false';
            status.textContent = `Invalid SMARTS · ${result.error ?? 'syntax error'}`;
            await applySmartsSearchOverlay(this.workbench.plugin, this.currentFocusOptions(), null);
            return;
        }
        input.dataset.error = 'false';
        if (result.matchCount === 0) {
            status.dataset.ok = '';
            status.textContent = `0 matches.`;
        } else {
            const atomHits = result.matchAtomIndices.reduce((s, f) => s + (f ? 1 : 0), 0);
            status.dataset.ok = 'true';
            status.textContent = `${result.matchCount} match${result.matchCount === 1 ? '' : 'es'} · ${atomHits} atom${atomHits === 1 ? '' : 's'} highlighted.`;
        }
        await applySmartsSearchOverlay(this.workbench.plugin, this.currentFocusOptions(), result);
    }

    private handleLigandDepictionClick(event: MouseEvent) {
        if (!this.workbench || this.ligandDepictionAtomPositions.length === 0) return;
        const target = byId('ligand-depiction');
        const svg = target.querySelector('svg');
        if (!svg) return;
        const atomIdx = LigandDepiction.getAtomIndexFromClick(
            svg as SVGSVGElement,
            this.ligandDepictionAtomPositions,
            event.clientX,
            event.clientY,
        );
        if (atomIdx < 0) return;
        this.selectLigandAtomByIndex(atomIdx);
    }

    /**
     * Build a single-atom StructureElement.Loci by walking the focused ligand
     * loci in the same iteration order used to build the molfile, then select
     * it in mol*. This is the 2D-click → 3D-select half of the sync.
     */
    private selectLigandAtomByIndex(atomIdx: number) {
        if (!this.workbench) return;
        const plugin = this.workbench.plugin;
        const structure = plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
        if (!structure) return;
        const ligandTarget = this.currentLigandTarget();
        if (!ligandTarget) return;
        const loci = StructureElement.Bundle.toLoci(ligandTarget.bundle, structure);
        let counter = 0;
        for (const e of loci.elements) {
            if (!Unit.isAtomic(e.unit)) continue;
            const count = OrderedSet.size(e.indices);
            for (let i = 0; i < count; i++) {
                if (counter === atomIdx) {
                    const unitIndex = OrderedSet.getAt(e.indices, i);
                    const atomLoci = StructureElement.Loci(structure, [{
                        unit: e.unit,
                        indices: OrderedSet.ofSingleton(unitIndex),
                    }]);
                    plugin.managers.interactivity.lociSelects.deselectAll();
                    plugin.managers.interactivity.lociSelects.select({ loci: atomLoci }, false);
                    focusLociKeepingSlab(plugin, atomLoci, { minRadius: 4, extraRadius: 2, durationMs: 250 });
                    return;
                }
                counter++;
            }
        }
    }

    private handleInteractionClick(loci: Loci) {
        // Clicking empty space is the user saying they are done with the detail they
        // focused. Focusing anything narrows the clipping slab to that object's radius
        // (see camera-slab.ts), and zooming out cannot widen it again, so the background
        // click is the way back — no control to discover, and it matches what the gesture
        // already means everywhere else.
        if (Loci.isEmpty(loci)) {
            if (this.workbench) restoreSceneSlab(this.workbench.plugin);
            return;
        }
        if (!ShapeGroup.isLoci(loci) || loci.shape.name !== 'Interactions' || loci.groups.length === 0) return;
        const id = OrderedSet.getAt(loci.groups[0].ids, 0);
        this.setActiveContact(id);
    }

    private setActiveContact(id: number) {
        document.querySelectorAll<HTMLElement>('[data-contact-id]').forEach(row => {
            row.dataset.active = String(Number(row.dataset.contactId) === id);
        });
    }

    /**
     * 3D→2D selection sync. When the user picks atoms in the 3D viewport,
     * draw highlight rings on the 2D SVG at the corresponding positions.
     * Uses the same atom-index walker as ligandLociToMolfile + selectLigandAtomByIndex.
     */
    private updateLigandDepictionSelectionHighlights() {
        const container = document.getElementById('ligand-depiction');
        const svg = container?.querySelector('svg');
        if (!svg || !this.workbench) {
            this.clearLigandDepictionHighlights();
            return;
        }
        if (this.ligandDepictionAtomPositions.length === 0) {
            this.clearLigandDepictionHighlights();
            return;
        }

        const plugin = this.workbench.plugin;
        const structure = plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
        if (!structure) { this.clearLigandDepictionHighlights(); return; }
        const ligandTarget = this.currentLigandTarget();
        if (!ligandTarget) { this.clearLigandDepictionHighlights(); return; }

        const ligandLoci = StructureElement.Bundle.toLoci(ligandTarget.bundle, structure);
        // Walk ligand loci in the same order as ligandLociToMolfile → atom index.
        // For each ligand atom, check whether the equivalent 3D element is selected.
        const selected = new Set<number>();
        let counter = 0;
        for (const e of ligandLoci.elements) {
            if (!Unit.isAtomic(e.unit)) continue;
            const count = OrderedSet.size(e.indices);
            for (let i = 0; i < count; i++) {
                const unitIndex = OrderedSet.getAt(e.indices, i);
                const atomLoci = StructureElement.Loci(structure, [{
                    unit: e.unit,
                    indices: OrderedSet.ofSingleton(unitIndex),
                }]);
                // intersects returns 0 if not selected, >0 if selected
                const intersection = StructureElement.Loci.intersect(plugin.managers.structure.selection.getLoci(structure) as StructureElement.Loci, atomLoci);
                if (!StructureElement.Loci.isEmpty(intersection)) {
                    selected.add(counter);
                }
                counter++;
            }
        }

        this.renderLigandDepictionHighlights(svg as unknown as SVGSVGElement, selected);
    }

    private clearLigandDepictionHighlights() {
        document.querySelectorAll('.selection-highlight-ring').forEach(el => el.remove());
    }

    private renderLigandDepictionHighlights(svg: SVGSVGElement, selectedIndices: Set<number>) {
        // Remove old rings
        svg.querySelectorAll('.selection-highlight-ring').forEach(el => el.remove());
        if (selectedIndices.size === 0) return;

        const ns = 'http://www.w3.org/2000/svg';
        // Find or create the highlight group
        let group = svg.querySelector<SVGGElement>('#selection-highlights');
        if (!group) {
            group = document.createElementNS(ns, 'g');
            group.id = 'selection-highlights';
            svg.appendChild(group);
        }

        for (const idx of selectedIndices) {
            const pos = this.ligandDepictionAtomPositions.find(p => p.idx === idx);
            if (!pos) continue;
            const ring = document.createElementNS(ns, 'circle');
            ring.setAttribute('cx', String(pos.x));
            ring.setAttribute('cy', String(pos.y));
            ring.setAttribute('r', '14');
            ring.setAttribute('fill', 'none');
            ring.setAttribute('stroke', '#ff6b35');
            ring.setAttribute('stroke-width', '2.5');
            ring.setAttribute('stroke-dasharray', '4 3');
            ring.classList.add('selection-highlight-ring');
            group.appendChild(ring);
        }
    }

    private async openSemanticDebug(id: Exclude<MolecularLayerId, MolstarVisualUpgradeId>, guide: SemanticLayerGuide) {
        if (!guide.fixture) return;
        const fixture = MolecularControls.find(control => control.id === guide.fixture);
        if (!fixture) throw new Error(`Missing checked semantic fixture ${guide.fixture}`);

        // A checked view is intentionally diagnostic: one semantic layer over a
        // known Native representation plus the established P1 imaging baseline.
        this.enabledUpgrades = new Set<MolecularLayerId>([...RecommendedMolstarVisualUpgrades, id]);
        this.currentRepresentation = guide.representation;
        byId<HTMLSelectElement>('representation').value = guide.representation;
        document.querySelectorAll<HTMLInputElement>('[data-upgrade]').forEach(checkbox => {
            checkbox.checked = this.enabledUpgrades.has(checkbox.dataset.upgrade as MolecularLayerId);
        });

        if (this.currentMolecule.id !== fixture.id) {
            this.currentMolecule = fixture;
            byId<HTMLSelectElement>('molecule').value = fixture.id;
            await this.loadMolecule(fixture);
        }
        this.refreshLayerNavigation();
        await this.applyRepresentationAndVisuals();
    }

    /**
     * SMILES → backend ETKDG/MMFF 3D → mol* scene. The imported molecule
     * becomes the focused ligand, so the entire facet cascade (2D depiction,
     * properties, field wells, designer seed) runs off the same lifecycle
     * as a deposited ligand — then the electrostatic well renders itself.
     */
    private async importMolecule(smiles: string) {
        if (!this.workbench) return;
        const status = byId('import-status');
        const setImportStatus = (text: string, tone: 'idle' | 'ok' | 'error') => {
            if (status) { status.textContent = text; status.dataset.tone = tone; }
        };
        setImportStatus('Embedding 3D structure (ETKDG + MMFF)…', 'idle');
        byId('status').textContent = 'Embedding molecule…';
        let payload: { ok: boolean, molfile?: string, error?: string, meta?: { smiles_canonical: string, inchikey: string, natoms_heavy: number, mmff_optimized: boolean } };
        try {
            const resp = await fetch(`http://${window.location.hostname || '127.0.0.1'}:8901/embed`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ smiles }),
            });
            payload = await resp.json();
        } catch (e) {
            setImportStatus(`Backend unreachable — start it with: backend/env/bin/python backend/field_server.py (${e instanceof Error ? e.message : e})`, 'error');
            byId('status').textContent = 'Import failed';
            return;
        }
        if (!payload.ok || !payload.molfile || !payload.meta) {
            setImportStatus(`Backend refused: ${payload.error}`, 'error');
            byId('status').textContent = 'Import failed';
            return;
        }
        const meta = payload.meta;
        this.framedLigandMolecule = undefined;
        this.ligandTargets = [];
        this.selectedLigandTargetId = undefined;
        this.importedMolfile = payload.molfile;
        this.currentMolecule = {
            id: meta.inchikey,
            label: `Imported · ${meta.smiles_canonical}`,
            category: 'Imported molecule',
            stress: `Generated in-backend: ETKDGv3 embed${meta.mmff_optimized ? ' + MMFF94 optimization' : ''}, ${meta.natoms_heavy} heavy atoms.`,
            url: '',
        };
        await this.workbench.loadStructureFromData(payload.molfile, {
            format: 'mol',
            label: `${meta.inchikey}.mol`,
        });
        await this.applyRepresentationAndVisuals();
        this.refreshMetrics();
        // applyRepresentationAndVisuals kicks renderLigandDepiction as
        // fire-and-forget; the Fields facet only learns the molfile inside it.
        // Await it explicitly so the auto-rendered well cannot race the
        // lifecycle that arms it.
        await this.renderLigandDepiction();
        setImportStatus(`Imported ${meta.smiles_canonical} (${meta.inchikey}) — facets live, rendering electrostatic well…`, 'ok');
        byId('status').textContent = 'Ready';
        autoRenderElectrostaticWell();
    }

    private async loadMolecule(control: MolecularControl) {
        if (!this.workbench) return;
        this.importedMolfile = null;
        this.framedLigandMolecule = undefined;
        this.ligandTargets = [];
        this.selectedLigandTargetId = undefined;
        byId('status').textContent = `Loading ${control.id}…`;
        const response = await fetch(new URL(control.url, window.location.href));
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        await this.workbench.loadStructureFromData(await response.text(), {
            format: 'mmcif',
            label: `${control.id}.cif`,
        });
    }

    private focusLigandOnce(force = false) {
        if (!this.workbench || (!force && this.framedLigandMolecule === this.currentMolecule.id)) return;
        const structure = this.workbench.plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
        if (!structure) return;
        const loci = getLigandFocusLoci(structure, this.currentFocusOptions());
        if (StructureElement.Loci.isEmpty(loci)) return;
        this.framedLigandMolecule = this.currentMolecule.id;
        focusLociKeepingSlab(this.workbench.plugin, loci, { minRadius: 8, extraRadius: 5, durationMs: 250 });
    }

    private async applyRepresentationAndVisuals(resetCamera = true) {
        if (!this.workbench) return;
        const manager = this.workbench.plugin.managers.structure.component;
        await this.removeMesoscaleCopies();
        const structure = manager.pivotStructure;
        if (!structure) throw new Error('Loaded structure is unavailable');
        await manager.applyPreset(
            [structure],
            PresetStructureRepresentations[this.currentRepresentation],
            { quality: this.enabledUpgrades.has('curve-quality-high') ? 'high' : 'auto' }
        );
        if (this.currentRepresentation === 'polymer-and-ligand') {
            const water = manager.pivotStructure?.components.filter(component => component.cell.obj?.label === 'Water') ?? [];
            if (water.length) await this.workbench.plugin.managers.structure.hierarchy.remove(water, true);
        }
        this.refreshLigandTargets();
        this.refreshSemanticAvailability();
        if (MesoscaleCopySlots.some(slot => this.enabledUpgrades.has(slot.id))) await this.createMesoscaleCopies();
        await this.applyVisuals();
        if (resetCamera) await this.workbench.resetCamera(0);
    }

    private async removeMesoscaleCopies() {
        if (!this.workbench) return;
        const copies = this.workbench.plugin.state.data.select(
            StateSelection.Generators.ofTransformer(StateTransforms.Model.StructureFromModel).withTag(MesoscaleCopyTag)
        );
        if (copies.length === 0) return;
        const update = this.workbench.plugin.state.data.build();
        for (const copy of copies) update.delete(copy);
        await update.commit({ doNotUpdateCurrent: true });
    }

    private async createMesoscaleCopies() {
        if (!this.workbench) return;
        const plugin = this.workbench.plugin;
        const source = plugin.managers.structure.component.pivotStructure;
        const sphere = source?.cell.obj?.data.boundary.sphere;
        if (!source?.model || !sphere) return;

        const spacing = Math.max(sphere.radius * 1.75, 24);
        const activeSlots = MesoscaleCopySlots.filter(slot => this.enabledUpgrades.has(slot.id));
        const update = plugin.state.data.build();
        const sourceParams = source.cell.transform.params;
        const copyRefs = activeSlots.map(slot => {
            const offset = Vec3.create(
                spacing * slot.offset[0],
                spacing * slot.offset[1],
                spacing * slot.offset[2],
            );
            return update.to(source.model!.cell)
            // A new StructureFromModel is a real sibling structure. Only the following
            // transform is a decorator, so no original representation tree is moved.
            .apply(StateTransforms.Model.StructureFromModel, sourceParams, { tags: [MesoscaleCopyTag, slot.id] })
            .apply(
                StateTransforms.Model.TransformStructureConformation,
                { transform: { name: 'matrix', params: { data: Mat4.fromTranslation(Mat4(), offset), transpose: false } } }
            ).ref;
        });
        await update.commit({ doNotUpdateCurrent: true });

        for (const copyRef of copyRefs) {
            await plugin.builders.structure.representation.applyPreset(
                copyRef,
                PresetStructureRepresentations[this.currentRepresentation],
                { quality: this.enabledUpgrades.has('curve-quality-high') ? 'high' : 'auto' }
            );
        }
    }

    private async applyVisuals() {
        if (!this.workbench) return;
        if (this.baseline) await restoreMolstarVisualState(this.workbench.plugin, this.baseline);
        await this.applySemanticLayers();
        const vfxLayers = [...this.enabledUpgrades].filter(isVfxLayer);
        await applyMolstarVisualUpgrades(this.workbench.plugin, vfxLayers);
    }

    private async applySemanticLayers() {
        if (!this.workbench) return;
        const chemicalLayers = [...this.enabledUpgrades].filter(isChemicalLayer);
        const structuralLayers = [...this.enabledUpgrades].filter(isStructuralLayer);
        const interactionLayers = new Set([...this.enabledUpgrades].filter(isInteractionLayer));
        const contextLayers = [...this.enabledUpgrades].filter(isContextLayer);
        const focusLayers = [...this.enabledUpgrades].filter(isFocusLayer);
        const evidenceLayers = [...this.enabledUpgrades].filter(isEvidenceLayer);
        const rdkitLayers = [...this.enabledUpgrades].filter(isRdkitLayer);
        const pharmacophoreEnabled = [...this.enabledUpgrades].some(isPharmacophoreLayer);
        const bondOrder3DEnabled = [...this.enabledUpgrades].some(isBondOrder3DLayer);
        await applyStructuralSemanticLayers(this.workbench.plugin, structuralLayers);
        await applyChemicalSemanticLayers(this.workbench.plugin, chemicalLayers);
        await applyEvidenceSemanticLayers(this.workbench.plugin, evidenceLayers);
        await applyInteractionSemanticLayers(this.workbench.plugin, interactionLayers, this.currentLigandTarget()?.bundle);
        await applyContextSemanticLayers(this.workbench.plugin, contextLayers);
        await applyFocusSemanticLayers(this.workbench.plugin, focusLayers, this.currentFocusOptions());
        await applyRdkitChemicalLayers(this.workbench.plugin, rdkitLayers, this.currentFocusOptions());
        await applyPharmacophoreFeatures(this.workbench.plugin, pharmacophoreEnabled, this.currentFocusOptions());
        await applyBondOrder3D(this.workbench.plugin, bondOrder3DEnabled, this.currentFocusOptions());
        this.renderContactLedger();
        this.refreshSemanticAvailability();
        void this.renderLigandDepiction();
    }

    private async perform(action: () => Promise<void>) {
        if (this.busy) return;
        this.busy = true;
        this.setControlsDisabled(true);
        byId('status').textContent = 'Applying…';
        try {
            await action();
            this.refreshMetrics();
            byId('status').textContent = 'Ready';
        } catch (error) {
            console.error(error);
            const message = error instanceof Error ? error.message : String(error);
            const status = byId('status');
            status.textContent = message;
            status.title = message; // the pill ellipsizes; the full error must stay reachable
        } finally {
            this.setControlsDisabled(false);
            this.busy = false;
        }
    }

    private setControlsDisabled(disabled: boolean) {
        document.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLButtonElement>('input, select, button').forEach(control => {
            control.disabled = disabled || control.dataset.unavailable === 'true';
        });
    }

    private refreshMetrics() {
        if (!this.workbench) return;
        const structure = this.workbench.plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
        const representationTypes = new Set(
            this.workbench.plugin.managers.structure.component.pivotStructure?.components.flatMap(component =>
                component.representations.map(representation => representation.cell.transform.params?.type?.name as string)
            ) ?? []
        );
        const selected = VisualLayerControls.filter(upgrade => this.enabledUpgrades.has(upgrade.id));
        const high = selected.filter(upgrade => upgrade.cost === 'high').length;
        const medium = selected.filter(upgrade => upgrade.cost === 'medium').length;
        const cost = high ? 'heavy' : medium > 2 ? 'balanced+' : medium ? 'balanced' : 'low';
        byId('molecule-note').textContent = `${this.currentMolecule.category}. ${this.currentMolecule.stress}`;
        byId('structure-id').textContent = this.currentMolecule.id;
        byId('elements').textContent = structure?.elementCount.toLocaleString() ?? '—';
        byId('residues').textContent = structure?.polymerResidueCount.toLocaleString() ?? '—';
        byId('scene-instances').textContent = this.workbench.plugin.managers.structure.hierarchy.current.structures.length.toLocaleString();
        byId('representation-metric').textContent = [...representationTypes].join(' + ') || 'none';
        byId('selection-count').textContent = this.workbench.plugin.managers.structure.selection.elementCount().toLocaleString();
        byId('layer').textContent = 'Pareto 1 · editable stack';
        byId('upgrade-count').textContent = `${selected.length} / ${VisualLayerControls.length}`;
        byId('cost').textContent = cost;
        byId('semantics').textContent = 'preserved';
        byId('backend').textContent = 'Mol* WebGL';
        byId('badge').textContent = `${this.currentMolecule.id} · ${this.currentMolecule.category}`;
        byId('note').textContent = `Native ${this.currentRepresentation} + independent layers. Active: ${selected.map(upgrade => upgrade.label).join(' · ')}`;
    }
}

function countSetBits8(flags: Uint8Array): number {
    let n = 0;
    for (let i = 0; i < flags.length; i++) if (flags[i]) n++;
    return n;
}

const lab = new MolecularVfxLab();
(window as unknown as { molecularVfxLab: MolecularVfxLab }).molecularVfxLab = lab;
void lab.init().catch(error => {
    console.error(error);
    const message = error instanceof Error ? error.message : String(error);
    const status = byId('status');
    status.textContent = message;
    status.title = message; // the pill ellipsizes; the full error must stay reachable
});
