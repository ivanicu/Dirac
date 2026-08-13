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

    getRDKit,

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
import { ChemistryCache } from '../chemistry.backend.perception.rdkit-wasm.editable/chemistry-cache';
import { ligandLociToMolfile, lociFromFocusOptions } from '../chemistry.backend.perception.rdkit-wasm.editable/ligand-pipeline';
import { renderPropertiesPanel } from './facets/property-cockpit';
import { initFieldWellsPanel, updateFieldWellsLigand, autoRenderElectrostaticWell, setFieldFocusOptionsProvider, setFieldStructureId } from './facets/field-wells';
import { initLigandPhysicsPanel, updateLigandPhysics } from './facets/ligand-physics';
import { initPharmacophoreDesigner, updatePharmacophoreDesigner } from './facets/pharmacophore-designer';
import { initBondAtlas, updateBondAtlas } from './facets/bond-atlas';
import { updateHalogenAudit } from './facets/halogen-audit';
import { PresetStructureRepresentations } from '../mol-plugin-state/builder/structure/representation-preset';
import { StateTransforms } from '../mol-plugin-state/transforms';
import { Loci } from '../mol-model/loci';
import { ligandStore } from '../app/services/ligand-store';
import { appShell } from '../app/shell/app-shell';
import { sceneService } from '../app/shell/scene-service';
import { scientificContext } from '../app/context/scientific-context-store';
import { ModuleHost, type ModuleAdapter } from '../app/shell/module-host';
import { MODULES, navigableViews, VIEWS, WORKSPACES, type ModuleDefinition,
    type WorkspaceId } from '../app/shell/registries';
import { WorkspaceCanvas } from '../app/shell/workspace-canvas';
import { DiracClient } from '../app/services/dirac-client';
import { observedComputationRun, renderComputationRun } from '../app/services/computation-run';
// A READ-ONLY handle on the store, for driving the real code from a test rather
// than reasoning about it. Not a second code path: it exposes the same singleton
// the facets read. The store's generation advancing on a ligand change is the
// property that did NOT hold until index.ts wrote through it, and a property that
// cannot be observed from outside is a property nobody will notice losing.
(window as unknown as Record<string, unknown>).__diracStore = ligandStore;
(window as unknown as Record<string, unknown>).__diracShell = appShell;
import { QueryContext, Structure, StructureElement, StructureSelection, Unit } from '../mol-model/structure';
import { ShapeGroup } from '../mol-model/shape';
import { OrderedSet } from '../mol-data/int';
import { StructureSelectionQueries } from '../mol-plugin-state/helpers/structure-selection-query';
import { Mat4, Vec3 } from '../mol-math/linear-algebra';
import { StateSelection } from '../mol-state';
import { Color } from '../mol-util/color';

/**
 * The scene's clear colour, read from the theme's `--scene-bg` token.
 * Falls back to Dirac Night for a theme that does not declare one — a missing
 * token must not produce a transparent or black canvas.
 */
function sceneBackgroundColor(): number {
    const raw = getComputedStyle(document.documentElement)
        .getPropertyValue('--scene-bg').trim();
    const m = /^#?([0-9a-f]{6})$/i.exec(raw);
    return m ? parseInt(m[1], 16) : 0x0d141b;
}
import bDnaUrl from '../../examples/1bna_confal_pyramids.cif';
import crambinUrl from '../../examples/1crn.cif';
import retinoidUrl from '../../examples/1cbs_updated.cif';
import gramicidinUrl from '../../examples/1grm_updated.cif';
import mhcComplexUrl from '../../examples/7qpd.fw2.cif';
import gfpUrl from './assets/structures/1ema.cif';
import p53DnaUrl from './assets/structures/1tup.cif';
import porinUrl from './assets/structures/2por.cif';
import hemoglobinUrl from './assets/structures/4hhb.cif';
import lapatinibUrl from './assets/structures/1xkk.cif';
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
    // Added because the halogen audit could not fire on anything in this catalogue: across
    // all ten bundled structures the halogen count was zero, so a feature about what a
    // halogen points at had no data to point with. Lapatinib carries both a chloride and a
    // fluoride in a kinase pocket, which is the case the audit exists for.
    { id: '1XKK', label: '1XKK · EGFR + lapatinib', category: 'Protein–ligand complex · halogenated', stress: 'A chloro- and fluoro-substituted inhibitor in a kinase pocket; the only bundled structure on which a halogen-bond claim can be tested at all.', url: lapatinibUrl },
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
    readonly fixture?: '1CRN' | '1GRM' | '1CBS' | '1BNA' | '1EMA' | '1XKK' | '4HHB' | '1TUP' | '2POR' | '7QPD';
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

const applicationClient = new DiracClient({
    baseUrl: `http://${window.location.hostname || '127.0.0.1'}:8901`,
});

function setWorkspaceStateDimension(dimension: 'runtime' | 'evidence', value: string): void {
    const label = dimension === 'runtime' ? 'Runtime' : 'Evidence';
    for (const term of document.querySelectorAll<HTMLElement>('.workspace-state-strip dt')) {
        if (term.textContent !== label) continue;
        const group = term.parentElement;
        const description = group?.querySelector('dd');
        if (!group || !description) continue;
        group.dataset.state = value;
        description.textContent = value.replace('-', ' ');
    }
}

async function refreshRuns(): Promise<void> {
    const lists = [...document.querySelectorAll<HTMLElement>('#run-job-list, [data-run-list]')];
    const summaries = [...document.querySelectorAll<HTMLElement>('#run-job-summary, [data-run-summary]')];
    if (!lists.length || !summaries.length) return;
    for (const summary of summaries) {
        summary.textContent = 'Loading durable jobs…';
        summary.dataset.runtime = 'loading';
    }
    setWorkspaceStateDimension('runtime', 'loading');
    try {
        const env = await applicationClient.execute('job.list', { limit: 50 });
        if (!env.ok) throw new Error(env.error?.user_message || env.error?.message || 'job.list refused');
        const jobs = (env.data?.jobs || []) as Array<Record<string, any>>;
        for (const list of lists) list.replaceChildren();
        const stamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        for (const summary of summaries) {
            summary.textContent = `${jobs.length} recent jobs · updated ${stamp} · Mission / Run / Job remain distinct`;
            summary.dataset.runtime = jobs.length ? 'ready' : 'empty';
        }
        setWorkspaceStateDimension('runtime', jobs.length ? 'ready' : 'empty');
        setWorkspaceStateDimension('evidence', jobs.length ? 'provenance-backed' : 'none');
        if (!jobs.length) {
            const empty = document.createElement('p');
            empty.className = 'ledger-empty'; empty.textContent = 'No durable jobs yet.';
            for (const list of lists) list.appendChild(empty.cloneNode(true));
            return;
        }
        for (const job of jobs) {
            const row = document.createElement('article'); row.className = 'run-job-row';
            const title = document.createElement('strong');
            title.textContent = `${job.method_id || 'method'} · ${job.state}`;
            const detail = document.createElement('span');
            detail.textContent = `${String(job.id).slice(0, 12)} · ${job.seconds ?? '—'}s · ${job.durability || 'unknown durability'}`;
            row.append(title, detail);
            for (const list of lists) list.appendChild(row.cloneNode(true));
        }
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        for (const summary of summaries) {
            summary.textContent = `Could not refresh jobs · ${message}`;
            summary.dataset.runtime = 'error';
        }
        setWorkspaceStateDimension('runtime', 'error');
    }
}

function initGlobalContext(): void {
    const set = (id: string, value: string) => {
        const node = document.getElementById(id);
        if (node) node.textContent = value;
    };
    scientificContext.subscribe(context => {
        set('context-program', context.programRef?.id || 'unassigned');
        set('context-complex', context.complexRef?.id || 'none');
        set('context-focus', context.focusedObject
            ? `${context.focusedObject.kind}:${context.focusedObject.id}` : 'none');
    });
}

async function refreshAttention(): Promise<void> {
    const count = document.getElementById('attention-count');
    if (!count) return;
    count.textContent = '…';
    const env = await applicationClient.execute('attention.list', { limit: 100 });
    if (!env.ok) {
        count.textContent = '!';
        count.title = env.error?.user_message || env.error?.message || 'attention unavailable';
        return;
    }
    const items = (env.data?.items || []) as Array<Record<string, unknown>>;
    count.textContent = String(items.length);
    count.title = items.length ? items.map(item => `${item.priority}: ${item.reason}`).join('\n')
        : 'No actionable failures or approval waits';
    const actor = env.meta?.actor as { kind?: string; id?: string } | undefined;
    if (actor?.kind && actor.id) {
        const node = document.getElementById('agent-identity');
        if (node) node.textContent = `${actor.kind}:${actor.id}`;
    }
}

function initCommandPalette(): void {
    const dialog = document.getElementById('command-palette') as HTMLDialogElement | null;
    const open = document.getElementById('command-palette-open');
    const search = document.getElementById('command-palette-search') as HTMLInputElement | null;
    const list = document.getElementById('command-palette-list');
    const input = document.getElementById('command-palette-input') as HTMLTextAreaElement | null;
    const output = document.getElementById('command-palette-output');
    if (!dialog || !open || !search || !list || !input || !output) return;
    let commands: Array<Record<string, unknown>> = [];
    let selected = '';
    const render = () => {
        const query = search.value.toLowerCase();
        list.replaceChildren();
        for (const command of commands.filter(c => String(c.id).includes(query))) {
            const button = document.createElement('button');
            button.type = 'button'; button.textContent = String(command.id);
            button.dataset.selected = String(command.id === selected);
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', String(command.id === selected));
            button.addEventListener('click', () => { selected = String(command.id); render(); });
            list.appendChild(button);
        }
    };
    const show = async () => {
        dialog.showModal(); search.focus();
        if (!commands.length) {
            try {
                commands = await applicationClient.commands(); render();
            } catch (error) { output.textContent = String(error); }
        }
    };
    open.addEventListener('click', () => void show());
    search.addEventListener('input', render);
    document.addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
            event.preventDefault(); void show();
        }
    });
    document.getElementById('command-palette-close')?.addEventListener('click', () => dialog.close());
    document.getElementById('command-palette-run')?.addEventListener('click', async () => {
        if (!selected) { output.textContent = 'Select a semantic command.'; return; }
        try {
            const payload = JSON.parse(input.value || '{}') as Record<string, unknown>;
            output.textContent = 'Executing…';
            const env = await applicationClient.execute(selected, payload);
            output.textContent = JSON.stringify(env, null, 2);
            await refreshAttention();
        } catch (error) { output.textContent = error instanceof Error ? error.message : String(error); }
    });
}

function createDomModuleHost(): ModuleHost {
    const mounted = new Set<string>();
    const render = () => {
        const surfaces = new Set(MODULES.filter(module => mounted.has(module.id))
            .flatMap(module => module.surfaces));
        document.querySelectorAll<HTMLElement>('.master-tab[data-jump]').forEach(tab => {
            tab.hidden = !surfaces.has(tab.dataset.jump || '');
        });
        document.querySelectorAll<HTMLElement>('.detail-scroll > [data-section]').forEach(panel => {
            if (!surfaces.has(panel.dataset.section || '')) panel.dataset.active = 'false';
        });
        const selected = document.querySelector<HTMLElement>('.master-tab[aria-pressed="true"]');
        if (!selected || selected.hidden) {
            const preferred = MODULES.filter(module => mounted.has(module.id))
                .sort((a, b) => b.priority - a.priority).flatMap(module => module.surfaces)
                .find(surface => surfaces.has(surface));
            document.querySelectorAll<HTMLElement>('.master-tab').forEach(tab =>
                tab.setAttribute('aria-pressed', String(tab.dataset.jump === preferred)));
            document.querySelectorAll<HTMLElement>('.detail-scroll > [data-section]').forEach(panel => {
                panel.dataset.active = String(panel.dataset.section === preferred);
            });
        }
    };
    const adapters = new Map<string, ModuleAdapter>(MODULES.map(definition => {
        const adapter: ModuleAdapter = {
            mount: (module: ModuleDefinition) => { mounted.add(module.id); render(); },
            unmount: (module: ModuleDefinition) => { mounted.delete(module.id); render(); },
            update: () => render(),
        };
        return [definition.id, adapter];
    }));
    return new ModuleHost(adapters);
}

function initShellNavigation(): void {
    const host = document.getElementById('workspace-nav');
    const viewHost = document.getElementById('view-nav');
    const canvasHost = document.getElementById('workspace-canvas');
    const outlineHost = document.getElementById('workspace-outline');
    const breadcrumb = document.getElementById('shell-breadcrumb');
    if (!host || !viewHost || !canvasHost || !outlineHost || !breadcrumb) return;
    const moduleHost = createDomModuleHost();
    const navigate = (route: Parameters<typeof appShell.navigate>[0]) => {
        const target = VIEWS.find(view => view.id === route.view && view.workspace === route.workspace);
        appShell.navigate(route);
        // Preview Workspaces boot without WebGL. Entering a connected scientific
        // View performs one deliberate reload so the Mol* capability can attach.
        if (target?.requiresScene && !sceneService.current()) location.reload();
    };
    const workspaceCanvas = new WorkspaceCanvas(canvasHost, outlineHost, breadcrumb,
        navigate);
    const render = (active: WorkspaceId, activeView: string) => {
        host.replaceChildren();
        const workspaceSwitcher = document.createElement('label');
        workspaceSwitcher.className = 'workspace-switcher';
        const workspaceSwitcherLabel = document.createElement('span');
        workspaceSwitcherLabel.textContent = 'Workspace';
        const workspaceSelect = document.createElement('select');
        workspaceSelect.setAttribute('aria-label', 'Switch Workspace');
        for (const workspace of WORKSPACES.filter(w => w.shellReady)) {
            workspaceSelect.add(new Option(workspace.label, workspace.id, false, workspace.id === active));
        }
        workspaceSelect.addEventListener('change', () => {
            const workspace = WORKSPACES.find(item => item.id === workspaceSelect.value as WorkspaceId);
            const next = workspace && VIEWS.find(view => view.id === workspace.defaultView);
            if (workspace && next) navigate({ workspace: workspace.id, view: next.id,
                programId: appShell.current().programId || 'current' });
        });
        workspaceSwitcher.append(workspaceSwitcherLabel, workspaceSelect);
        host.append(workspaceSwitcher);
        for (const workspace of WORKSPACES.filter(w => w.shellReady)) {
            const button = document.createElement('a');
            const next = VIEWS.find(view => view.id === workspace.defaultView)!;
            const route = { workspace: workspace.id, view: next.id,
                programId: appShell.current().programId || 'current' };
            button.href = appShell.urlFor(route);
            const icon = document.createElement('span');
            icon.className = 'workspace-nav-icon';
            icon.textContent = workspace.icon; icon.setAttribute('aria-hidden', 'true');
            const navLabel = document.createElement('span');
            navLabel.className = 'workspace-nav-label';
            navLabel.textContent = workspace.id === 'runs' ? 'Compute' : workspace.label;
            button.append(icon, navLabel);
            button.setAttribute('aria-label', workspace.label);
            button.title = workspace.label;
            button.dataset.capability = workspace.availability;
            if (workspace.id === active) button.setAttribute('aria-current', 'page');
            button.addEventListener('click', event => {
                if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                event.preventDefault(); navigate(route);
            });
            host.appendChild(button);
        }
        viewHost.replaceChildren();
        const viewSwitcher = document.createElement('label');
        viewSwitcher.className = 'view-switcher';
        const viewSwitcherLabel = document.createElement('span');
        viewSwitcherLabel.textContent = 'View';
        const viewSelect = document.createElement('select');
        viewSelect.setAttribute('aria-label', 'Switch View');
        for (const view of navigableViews(active)) {
            viewSelect.add(new Option(view.label, view.id, false, view.id === activeView));
        }
        viewSelect.addEventListener('change', () => navigate({ workspace: active,
            view: viewSelect.value, programId: appShell.current().programId || 'current' }));
        viewSwitcher.append(viewSwitcherLabel, viewSelect);
        viewHost.append(viewSwitcher);
        for (const view of navigableViews(active)) {
            const button = document.createElement('a');
            const route = { workspace: active, view: view.id,
                programId: appShell.current().programId || 'current' };
            button.href = appShell.urlFor(route); button.textContent = view.label;
            button.dataset.capability = view.implemented ? 'implemented' : 'shell';
            if (view.id === activeView) button.setAttribute('aria-current', 'page');
            button.addEventListener('click', event => {
                if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                event.preventDefault(); navigate(route);
            });
            viewHost.appendChild(button);
        }
    };
    let projectedView = '';
    const project = (route: ReturnType<typeof appShell.current>) => {
        render(route.workspace, route.view);
        moduleHost.activate(route.view, scientificContext.current());
        workspaceCanvas.render(route);
        if (route.workspace === 'runs') void refreshRuns();
        const definition = VIEWS.find(view => view.id === route.view);
        document.title = definition ? `${definition.label} · Dirac` : 'Dirac';
        const announcer = document.getElementById('route-announcer');
        if (announcer && definition) announcer.textContent = `${definition.label} view loaded`;
        if (projectedView && projectedView !== route.view) {
            requestAnimationFrame(() => {
                const focusTarget = document.getElementById('workspace-view-title')
                    ?? document.getElementById('current-content');
                focusTarget?.focus();
            });
        }
        projectedView = route.view;
    };
    appShell.restore();
    appShell.subscribe(project);
    scientificContext.subscribe(() => moduleHost.activate(appShell.current().view, scientificContext.current()));
    window.addEventListener('popstate', () => project(appShell.restore()));
    document.getElementById('run-job-refresh')?.addEventListener('click', () => void refreshRuns());
    document.addEventListener('dirac:refresh-runs', () => void refreshRuns());
    initGlobalContext();
    initCommandPalette();
    void refreshAttention();
}

class MolecularVfxLab {
    private workbench?: Awaited<ReturnType<typeof createChemWorkbench>>;
    private baseline?: MolstarVisualSnapshot;
    // 1CBS, not 1EMA. The app booted on green fluorescent protein, whose chromophore is a
    // covalently modified polymer residue rather than a deposited ligand — so mol*'s ligand
    // query cannot see it, and Ligand, Properties, Fields, the SMARTS search and the bond
    // atlas all render "no ligand loaded" on the first screen a new user ever sees, under a
    // header that advertises "+ chromophore". 1CBS is the catalogue's own "Protein-ligand
    // complex" and is the structure the README's demo section already uses.
    private currentMolecule = MolecularControls.find(control => control.id === '1CBS')!;
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
    private readonly chemistryCache = new ChemistryCache();
    private smartsSearchTimer: ReturnType<typeof setTimeout> | null = null;
    /* The comment that used to live here described `importedMolfile`, a field
     * S0 deleted. An orphaned docstring is worse than none: it reads as a
     * description of code that exists. The role it described now belongs to
     * `smilesMolfile` above (see importMolecule). */
    private busy = false;


    async init() {
        // Product navigation and non-3D Workspaces must survive a missing or failed
        // WebGL context. The molecular workbench is one capability, not the boot gate
        // for Programs, Campaigns, Experiments, Knowledge, or Runs.
        initShellNavigation();
        const initialView = VIEWS.find(view => view.id === appShell.current().view);
        if (!initialView?.requiresScene) {
            byId('status').textContent = initialView?.delivery === 'connected'
                ? 'Connected · loading runtime data' : 'Shell available · no data selected';
            return;
        }
        const restored = scientificContext.current().complexRef;
        if (restored) {
            const id = restored.id.replace(/^pdb:/i, '').toUpperCase();
            const control = MolecularControls.find(item => item.id.toUpperCase() === id);
            if (!control) {
                this.renderSceneSourceRequired(`The requested complex "${restored.id}" is not available in this local catalog.`);
                return;
            }
            this.currentMolecule = control;
        } else {
            this.renderSceneSourceRequired('No complex is selected. Dirac will not substitute a demo structure.');
            return;
        }
        this.workbench = await createChemWorkbench({ target: byId('viewport') });
        // Scene ownership sits above every View. AppShell navigation may project this
        // host elsewhere, but it never constructs or disposes the plugin instance.
        sceneService.attach(this.workbench.plugin, byId('viewport'));
        this.workbench.plugin.selectionMode = true;
        this.workbench.plugin.managers.interactivity.setProps({ granularity: 'residue' });
        this.workbench.plugin.managers.structure.selection.events.changed.subscribe(() => {
            this.refreshMetrics();
            this.updateLigandDepictionSelectionHighlights();
        });
        this.workbench.plugin.behaviors.interaction.click.subscribe(({ current }) => this.handleInteractionClick(current.loci));
        // Other agents' facets — wrap in try/catch so their failures don't
        // crash the core lab init and make the UI disappear.
        try {
            initFieldWellsPanel(this.workbench.plugin);
            // The pocket field must use the SAME cutoff the semantic layers and
            // the 3-8 A slider already use, or the shell it charges is not the
            // shell the user can see on screen.
            setFieldFocusOptionsProvider(() => this.currentFocusOptions() as unknown as Record<string, unknown>);
        } catch (e) { console.error('[fields] init failed:', e); }
        try { initLigandPhysicsPanel(this.workbench.plugin); } catch (e) { console.error('[physics] init failed:', e); }
        try { initPharmacophoreDesigner(this.workbench.plugin); } catch (e) { console.error('[designer] init failed:', e); }
        try { initBondAtlas(); } catch (e) { console.error('[bond-atlas] init failed:', e); }
        // The canvas is part of the theme, so its clear colour comes from the
        // theme's own token. As a literal it was the one surface a theme could
        // not reach: the app booted to a near-black scene and the active theme
        // repainted it light on a 1.2 s poll, so every load flashed dark first
        // — and the field colours were still chosen for the colour that lost.
        await this.workbench.setBackground(Color(sceneBackgroundColor()));
        this.createControls();
        await this.loadMolecule(this.currentMolecule);
        this.baseline = captureMolstarVisualState(this.workbench.plugin);
        await this.applyRepresentationAndVisuals();
        this.refreshMetrics();
        byId('status').textContent = 'Ready';
    }

    private renderSceneSourceRequired(message: string): void {
        const viewport = byId('viewport');
        const card = document.createElement('section');
        card.className = 'scene-source-required';
        card.setAttribute('role', 'status');
        const title = document.createElement('h2');
        title.textContent = 'Select a structure source';
        const copy = document.createElement('p');
        copy.textContent = message;
        const example = document.createElement('button');
        example.type = 'button'; example.className = 'btn-primary';
        example.textContent = 'Open explicit example · PDB 1CBS';
        example.addEventListener('click', () => {
            scientificContext.patch({ complexRef: { kind: 'complex', id: 'pdb:1CBS' },
                origin: 'selection' });
            location.href = appShell.urlFor(appShell.current());
        });
        card.append(title, copy, example);
        viewport.replaceChildren(card);
        byId('status').textContent = 'Needs structure source';
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
            tab.tabIndex = groupIndex === 0 ? 0 : -1;
            tab.id = `${tabsId}-tab-${groupIndex}`;
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
            panel.id = `${upgradesId}-panel-${groupIndex}`;
            panel.setAttribute('aria-labelledby', tab.id);
            tab.setAttribute('aria-controls', panel.id);
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
            const expand = document.createElement('button');
            expand.type = 'button';
            expand.className = 'upgrade-expand';
            expand.textContent = 'Details';
            expand.setAttribute('aria-expanded', 'false');
            expand.addEventListener('click', () => {
                const expanded = row.classList.toggle('expanded');
                expand.setAttribute('aria-expanded', String(expanded));
                expand.textContent = expanded ? 'Hide details' : 'Details';
            });
            row.append(checkbox, copy, cost, expand);
            panel.appendChild(row);
            }

            tab.addEventListener('click', () => this.selectUpgradeGroup(group.id, scope));
            tab.addEventListener('keydown', event => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                const tabs = [...document.querySelectorAll<HTMLButtonElement>(
                    `[data-layer-scope="${scope}"][data-group]`)];
                const current = tabs.indexOf(tab);
                const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
                    : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
                tabs[next].click(); tabs[next].focus();
            });
            upgrades.appendChild(panel);
        }
    }

    private selectUpgradeGroup(group: UpgradeGroup['id'], scope: 'semantic' | 'vfx') {
        document.querySelectorAll<HTMLButtonElement>(`[data-layer-scope="${scope}"][data-group]`).forEach(tab => {
            const active = tab.dataset.group === group;
            tab.setAttribute('aria-selected', String(active));
            tab.tabIndex = active ? 0 : -1;
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

        if (anyEnabled) {
            renderComputationRun('interaction-run-record', observedComputationRun(
                'structure.interactions', {
                    executor: 'browser',
                    methodId: 'molstar.interactions',
                    version: 'workspace',
                    provenance: {
                        structure_id: this.currentMolecule.id,
                        ligand_target: this.currentLigandTarget()?.label || 'none',
                        cutoff_angstrom: this.ligandCutoff,
                        enabled_layers: INTERACTION_LAYERS.filter(layer =>
                            this.enabledUpgrades.has(layer.id as MolecularLayerId)).map(layer => layer.id),
                        interaction_count: allRecords.length,
                    },
                    note: 'Geometry-qualified browser observation; it is not a binding energy or experimental measurement. The service handler remains registered-unavailable.',
                }));
        } else {
            renderComputationRun('interaction-run-record', null);
        }

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
            // The SAME fan-out the deposited-ligand path uses. This line is the
            // fix for "import a molecule, click a field, nothing happens".
            this.fanOutLigand(this.smilesMolfile, summary.textContent || 'Imported molecule',
                this.workbench.plugin.managers.structure.component.pivotStructure?.cell.obj?.data,
                'import');
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
            // No ligand in this structure: CLEAR the store rather than leave it
            // holding the previous molecule. A stale "current" ligand makes
            // isCurrent() lie in the direction that matters — it would report a
            // molecule nobody is looking at as still live.
            ligandStore.clear();
            updateFieldWellsLigand(null, null);
            void updateBondAtlas(null);
            updateHalogenAudit(null, this.currentFocusOptions());
            void updatePharmacophoreDesigner(null, this.currentFocusOptions(), { structureId: this.currentMolecule.id, ligandLabel: null });
            // The properties cockpit was missing from this list, and missing from it in
            // the one direction that cannot be seen: every other facet here CLEARS, so
            // switching from haemoglobin to crambin emptied the wells, the atlas and the
            // designer while the cockpit went on displaying HEM · A:142, MW 616.5, LogP
            // 4.74 — a full, plausible, internally consistent readout of a molecule that
            // is not in the scene. Ivan: 我发现它这个永远就是它那个值是不变的.
            //
            // The reason it survived a test is worth more than the fix. I checked this
            // panel on 1CBS, 1XKK and 4HHB, watched every number change, and concluded it
            // recomputed — but all three HAVE a deposited ligand, so all three take the
            // path above this branch. I picked the sample points and therefore picked the
            // answer. The molecules that expose it are the ones with nothing to show.
            void renderPropertiesPanel(null, null);
            return;
        }

        // S0 item 2+3: Use ChemistryCache instead of independent prepareLigandAnalysis.
        // The cache was populated at the top of applySemanticLayers — read from it.
        const cached = this.chemistryCache.current();
        const molfile = cached?.molfile ?? this.smilesMolfile;
        const atomCount = cached?.atomCount ?? 0;
        const chemistry = cached?.chemistry ?? null;

        if (!molfile) {
            target.innerHTML = '<p class="ledger-empty">RDKit cannot parse this ligand (ComponentBond / CCD data unavailable).</p>';
            summary.textContent = 'RDKit parse failed';
            stats.textContent = '';
            // Same omission, same direction: a ligand RDKit cannot read is not a reason to
            // keep showing the last one it could.
            void renderPropertiesPanel(null, null);
            updateFieldWellsLigand(null, null);
            void updateBondAtlas(null);
            updateHalogenAudit(null, this.currentFocusOptions());
            void updatePharmacophoreDesigner(null, this.currentFocusOptions(), { structureId: this.currentMolecule.id, ligandLabel: null });
            return;
        }

        const highlights: AtomHighlight[] = [];
        if (chemistry) {
            if (this.enabledUpgrades.has('aromaticity-rdkit')) {
                for (let i = 0; i < atomCount; i++) {
                    if (chemistry.aromaticAtoms[i]) highlights.push({ atomIndex: i, color: '#c792ea', alpha: 0.55 });
                }
            }
            if (this.enabledUpgrades.has('donor-acceptor-rdkit')) {
                for (let i = 0; i < atomCount; i++) {
                    if (chemistry.donors[i]) highlights.push({ atomIndex: i, color: '#5fd0c8', alpha: 0.55 });
                    if (chemistry.acceptors[i]) highlights.push({ atomIndex: i, color: '#e1a14e', alpha: 0.55 });
                }
            }
        }

        const showAtomIndices = byId<HTMLInputElement>('show-atom-indices')?.checked ?? false;
        const result = await LigandDepiction.depict(molfile, {
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
        const aromCount = chemistry ? countSetBits8(chemistry.aromaticAtoms) : 0;
        const donorCount = chemistry ? countSetBits8(chemistry.donors) : 0;
        const acceptorCount = chemistry ? countSetBits8(chemistry.acceptors) : 0;
        stats.textContent = `${atomCount} atoms · ${aromCount} aromatic · ${donorCount} HBD · ${acceptorCount} HBA`;

        this.fanOutLigand(molfile, ligandTarget?.label ?? null, structure);
    }

    /**
     * ONE home for "a new active molecule exists" — every facet that must hear
     * about it, in one place.
     *
     * It exists because there were TWO cascades and they had drifted. The PDB
     * path told seven consumers; the SMILES/import path told two (the depiction
     * and the properties panel) and returned. So an imported molecule reached
     * the screen with a 2D picture, a Lipinski table — and a Fields facet whose
     * `molfile` was still null, which makes every field button a silent no-op
     * *under a status line that says "rendering electrostatic well…"*. Also
     * silently absent on that path: ligand physics, the bond atlas, the halogen
     * audit and the pharmacophore designer.
     *
     * Adding the five missing calls to the second branch would have been the
     * third copy of this list. The list is the thing that must not be
     * duplicated: a new facet added by anyone gets wired here once, and cannot
     * be wired into one entry path and not the other.
     *
     * `structure` is optional because the two paths differ in what they have —
     * an import has no deposited-ligand loci, and the two audits that take a
     * mol* Structure are skipped rather than fed a substitute. Skipped-and-said
     * is a scope statement; fed-a-substitute is a wrong answer.
     */
    /**
     * ONE writer for "which molecule is the pasted/imported one".
     *
     * `smilesMolfile` decides a BRANCH: renderLigandDepiction() checks it first
     * and, when set, never looks at the loaded structure. So a stale value does
     * not merely linger — it wins. Measured symptom (found by an outside audit,
     * not by me): paste a SMILES, then pick a PDB structure from the dropdown,
     * and five facets keep showing the pasted molecule while the 3D scene shows
     * the protein. loadMolecule() reset three other pieces of ligand state and
     * not this one, which is the same one-path-of-two shape as the import bug
     * this pairs with — I fixed the writing half this morning and left the
     * clearing half, and that is exactly why the writers now live in one place.
     *
     * `smartsSearchMolfile` travels with it because it answers the same
     * question; two fields updated at four sites was the drift risk.
     */
    private setPastedMolecule(molfile: string | null) {
        this.smilesMolfile = molfile;
        this.smartsSearchMolfile = molfile;
    }

    private fanOutLigand(molfile: string, label: string | null, structure?: Structure,
                         origin: 'loci' | 'import' = 'loci') {
        // The write enters ScientificContextStore here, so every facet observes the
        // same generation token and late async results are rejected centrally.
        //
        // It goes HERE and nowhere else because fanOutLigand is already the one
        // home for "a new active molecule exists": every entry path funnels
        // through it, so the store cannot be updated on one path and missed on
        // the other — the exact defect that had the import flow rendering a stale
        // molecule across five facets this morning.
        //
        // `origin` is PASSED, not inferred from whether `structure` is present —
        // and the first version inferred it, which recorded every imported
        // molecule as a LociLigand. An import IS loaded into the scene as a
        // structure, so "structure exists" does not distinguish "a deposited
        // ligand inside a protein" from "the whole structure is the molecule the
        // user pasted". Caught by reading the store back in a real browser: kind
        // was 'loci' for an import. Both call sites know which they are; a type
        // that lies is worse than no type, because a reader trusts it.
        // Both are coordSpace 'scene' — a 2D-only molfile must never arrive here,
        // which is what requireScene() enforces on the read side.
        if (origin === 'loci' && structure) {
            ligandStore.setFromLoci({
                molfile, label: label ?? 'Ligand',
                structureRef: structure,
                bundleRef: this.currentLigandTarget() ?? null,
                cutoffA: 0,
            });
        } else {
            ligandStore.setFromImport({
                molfile, label: label ?? 'Imported molecule',
                inchikey: this.currentMolecule.id ?? '', seed: 0,
            });
        }
        void renderPropertiesPanel(molfile, label);
        // The structure id is the bake's DURABLE key. sha256(molfile) is exact
        // but brittle: the app reconstructs the molblock, so any change to that
        // writer moves every hash and silently empties the baked cache — which
        // is exactly what the deployed site was showing, as "backend offline".
        setFieldStructureId(this.currentMolecule?.id ?? null);
        updateFieldWellsLigand(molfile, label);
        updateLigandPhysics(molfile, label);
        // The atlas gets the SAME molfile the 2D depiction was built from, so the
        // two views cannot end up describing different molecules.
        void updateBondAtlas(molfile);
        if (structure) {
            // Geometry only until the QM field has been run for this ligand; the
            // panel says so rather than guessing a V_S,max.
            updateHalogenAudit(structure, this.currentFocusOptions());
            void updatePharmacophoreDesigner(structure, this.currentFocusOptions(),
                { structureId: this.currentMolecule.id, ligandLabel: label ?? 'Ligand' });
        }
        this.smartsSearchMolfile = molfile;
        const smartsInput = byId<HTMLInputElement>('smarts-input');
        if (smartsInput?.value) void this.runSmartsSearch(smartsInput.value);
        void this.refreshLigandIdentifiers(molfile);
    }

    private async refreshLigandIdentifiers(molfile: string) {
        // S0: read from ChemistryCache if available (descriptors computed once).
        const cached = this.chemistryCache.current();
        const ids = cached?.identifiers ?? await computeLigandIdentifiers(molfile);
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
            this.setPastedMolecule(null);
            void updateBondAtlas(null);
            updateHalogenAudit(null, this.currentFocusOptions());
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
            this.setPastedMolecule(molfile);
            // "ALL chemistry features" has to include the atlas. Driving the real UI showed the
            // depiction rendering here while the atlas stayed empty, because the atlas was wired
            // only into the deposited-ligand path.
            void updateBondAtlas(molfile);
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

    private async runSmartsSearch(smarts: string) {
        const input = byId<HTMLInputElement>('smarts-input');
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
        let payload: { ok: boolean; molfile?: string; error?: string;
            meta?: { smiles_canonical: string; inchikey: string; natoms_heavy: number; mmff_optimized: boolean } };
        try {
            const env = await applicationClient.execute('conformer.generate', { smiles });
            const molecule = env.data?.molecule as Record<string, unknown> | undefined;
            const provenance = (env.meta?.provenance || {}) as Record<string, unknown>;
            payload = env.ok && molecule?.content ? {
                ok: true,
                molfile: String(molecule.content),
                meta: {
                    smiles_canonical: String(provenance.smiles_canonical || smiles),
                    inchikey: String(provenance.inchikey || 'unknown'),
                    natoms_heavy: Number(provenance.n_atoms_heavy || 0),
                    mmff_optimized: true,
                },
            } : { ok: false, error: env.error?.user_message || env.error?.message || 'conformer.generate refused' };
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
        // THE AUTHORITATIVE MOLFILE FOR AN IMPORT, and its absence was a
        // regression I introduced. S0 (the ligand-pipeline dedup) removed the
        // `importedMolfile` field and left only the comment that described it,
        // so nothing on this path set an active molfile at all: with no CCD
        // data for a bare small molecule, renderLigandDepiction() found no
        // ligand loci, field-wells' `molfile` stayed null, and every field
        // button below was a silent no-op — under a status line that says
        // "rendering electrostatic well…". Confident wrong status over a
        // no-op is the exact failure this whole app is built to refuse, and it
        // sat in the one flow that was requested by name (导入一个分子自动生成).
        // Reproduced 3x in an isolated browser before this fix; the mechanism
        // is then visible in two lines of source.
        //
        // NAMING DEBT, stated rather than silently tolerated: this field is
        // called smilesMolfile because it began as the paste-SMILES path, and
        // it is now what every facet reads as "the active molecule". Renaming
        // it touches ~10 call sites in a file two other sessions are editing
        // today, so it waits — but a reader must not conclude that an /embed
        // import is a SMILES paste.
        this.setPastedMolecule(payload.molfile);
        scientificContext.patch({ complexRef: undefined, origin: 'import' });
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

        this.framedLigandMolecule = undefined;
        this.ligandTargets = [];
        this.selectedLigandTargetId = undefined;
        // THE MISSING RESET. Without this a pasted molecule survives loading a
        // deposited structure and keeps winning the depiction branch.
        this.setPastedMolecule(null);
        scientificContext.patch({ complexRef: { kind: 'complex', id: `pdb:${control.id}` },
            origin: 'navigation' });
        byId('status').textContent = `Loading ${control.id}…`;
        // A route such as /p/KRAS-G12D/structures/complex must still resolve
        // bundled structures from the application root. window.location.href
        // incorrectly turns ./assets/x.cif into /p/.../assets/x.cif; document.baseURI
        // honours the shell's <base href="/"> and makes reload truly deep-linkable.
        const response = await fetch(new URL(control.url, document.baseURI));
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
        const canvas3d = this.workbench.plugin.canvas3d;
        // Hold the last good frame for the whole transaction. Measured before this line
        // existed: switching representation presented a 409 ms window in which the canvas
        // was #0d141b — mol*'s own scene background, restored by the theme's 1.5 s poll
        // rather than by anything that knew the switch had happened — and inside that
        // window the molecule appeared in mol*'s default colours before the app's overpaint
        // landed. Both are intermediate states that were never meant to be seen, so the fix
        // is not to make them shorter but to not present them: pause(true) cancels the
        // animation frame and blocks draws, and the canvas keeps showing the last complete
        // frame until resume().
        canvas3d?.pause(true);
        try {
            await this.applyRepresentationAndVisualsInner(manager, resetCamera);
        } finally {
            // Repaint the background from the theme in the SAME path that disturbed it,
            // before the first frame after resume. The poll in theme-chamber.js stays as a
            // safety net for presets this path does not own, but nothing now waits on it.
            try { await this.workbench.setBackground(Color(sceneBackgroundColor())); } catch { /* theme is best-effort */ }
            canvas3d?.resume();
        }
    }

    private async applyRepresentationAndVisualsInner(
        manager: NonNullable<typeof this.workbench>['plugin']['managers']['structure']['component'],
        resetCamera: boolean,
    ) {
        if (!this.workbench) return;
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

        // S0 item 2+3: Compute molfile + ALL RDKit results ONCE.
        // The apply functions below still call RDKit for Overpaint (will be
        // fixed in Phase 3), but the availability badges + panel renders
        // at the bottom now read from this cache instead of independently.
        await this.updateChemistryCache();

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

    /**
     * S0 item 2+3: Compute the focused ligand's molfile ONCE, run ALL RDKit
     * computations ONCE, cache the results. Every consumer reads from cache.
     *
     * In SMILES mode, uses the SMILES-derived molfile directly.
     * In PDB mode, extracts from the loaded structure's ligand loci.
     */
    private async updateChemistryCache(): Promise<void> {
        if (this.smilesMolfile) {
            const atomCount = parseInt(this.smilesMolfile.split('\n')[3]?.slice(0, 3).trim() || '0', 10) || 0;
            await this.chemistryCache.update(this.smilesMolfile, atomCount);
            return;
        }
        const structure = this.workbench!.plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
        if (!structure) { this.chemistryCache.clear(); return; }
        const loci = lociFromFocusOptions(structure, this.currentFocusOptions());
        if (StructureElement.Loci.isEmpty(loci)) { this.chemistryCache.clear(); return; }
        const build = ligandLociToMolfile(loci);
        if (!build) { this.chemistryCache.clear(); return; }
        await this.chemistryCache.update(build.molfile, build.atomCount);
    }

    private pendingAction: (() => Promise<void>) | null = null;

    /**
     * S0 item 4: PerformQueue — last-write-wins.
     * If busy, stores the latest action and executes after current finishes.
     * No silent drops (fixes F13).
     */
    private async perform(action: () => Promise<void>) {
        this.pendingAction = action;
        if (this.busy) return;
        this.busy = true;
        while (this.pendingAction) {
            const current = this.pendingAction;
            this.pendingAction = null;
            this.setControlsDisabled(true);
            byId('status').textContent = 'Applying…';
            try {
                await current();
                this.refreshMetrics();
                byId('status').textContent = 'Ready';
            } catch (error) {
                console.error(error);
                const message = error instanceof Error ? error.message : String(error);
                const status = byId('status');
                status.textContent = message;
                status.title = message;
            }
        }
        this.setControlsDisabled(false);
        this.busy = false;
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
        byId('viewport-accessible-summary').textContent = [
            `Structure ${this.currentMolecule.id}: ${this.currentMolecule.category}.`,
            `${structure?.elementCount.toLocaleString() ?? 'Unknown'} atoms and ${structure?.polymerResidueCount.toLocaleString() ?? 'unknown'} polymer residues.`,
            `${this.workbench.plugin.managers.structure.selection.elementCount().toLocaleString()} selected elements.`,
            `Representation ${[...representationTypes].join(' plus ') || 'none'}.`,
            'Use the synchronized ligand, contact ledger, properties, fields, and semantic layer controls for text and keyboard-accessible inspection.',
        ].join(' ');
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
    status.setAttribute('role', 'alert');
    status.title = message; // the pill ellipsizes; the full error must stay reachable
    const viewport = document.getElementById('viewport');
    if (viewport && !viewport.hidden) {
        const boundary = document.createElement('section');
        boundary.className = 'scene-source-required';
        boundary.setAttribute('role', 'alert');
        const heading = document.createElement('h2');
        heading.textContent = '3D workbench could not start';
        const explanation = document.createElement('p');
        explanation.textContent = `${message}. Your selected source remains in the URL and scientific context.`;
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.textContent = 'Retry 3D workbench';
        retry.addEventListener('click', () => window.location.reload());
        const continueLink = document.createElement('a');
        continueLink.href = appShell.urlFor({
            workspace: 'runs',
            view: 'runs.active',
            programId: appShell.current().programId,
        });
        continueLink.textContent = 'Continue in Compute & Automation';
        boundary.append(heading, explanation, retry, continueLink);
        viewport.replaceChildren(boundary);
    }
});
