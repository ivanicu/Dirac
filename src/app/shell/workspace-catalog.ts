import type { WorkspaceId } from './registries';

export type ModuleReadiness = 'available' | 'foundation' | 'planned';

export interface ExperienceModule {
    title: string;
    purpose: string;
    readiness: ModuleReadiness;
}

export interface ViewExperience {
    summary: string;
    question: string;
    modules: readonly ExperienceModule[];
    nextMilestone: string;
    liveTarget?: string;
}

export const WORKSPACE_NARRATIVES: Readonly<Record<WorkspaceId, string>> = {
    programs: 'Frame the scientific mission, its evidence, and the decisions that move it forward.',
    design: 'Turn program objectives into molecules and inspect nearby chemical possibilities.',
    structures: 'Understand molecular geometry, interactions, fields, and structural alternatives.',
    campaigns: 'Move compounds through a traceable optimization portfolio instead of isolated files.',
    synthesis: 'Connect design intent to routes, materials, and an executable make queue.',
    experiments: 'Plan, run, and interpret measurements as durable scientific evidence.',
    knowledge: 'Find entities, claims, datasets, and provenance across the whole scientific system.',
    runs: 'Observe delegated work, intervene where needed, and preserve execution history.',
};

const m = (title: string, purpose: string,
    readiness: ModuleReadiness = 'planned'): ExperienceModule => ({ title, purpose, readiness });

const view = (summary: string, question: string, modules: readonly ExperienceModule[],
    nextMilestone: string, liveTarget?: string): ViewExperience => ({
    summary, question, modules, nextMilestone, liveTarget,
});

/**
 * Human-facing product contract for every registered View.
 *
 * This is deliberately separate from scientific capability registration: a View can have a
 * stable route, information hierarchy, notebook, and implementation map before its domain
 * commands exist. `readiness` describes the real backing layer and is never sample result data.
 */
export const VIEW_EXPERIENCES: Readonly<Record<string, ViewExperience>> = {
    'programs.overview': view(
        'A single reading surface for the current state of a drug program.',
        'What are we trying to achieve, and what matters now?', [
            m('Target summary', 'Identity, mechanism, structural coverage, and unresolved target risks.', 'foundation'),
            m('Program objectives', 'The measurable potency, selectivity, ADME, and delivery goals.', 'foundation'),
            m('Current bottlenecks', 'The few constraints presently limiting program progress.'),
            m('Candidate & series summary', 'The active chemical series and their promotion state.'),
            m('Recent evidence', 'The newest observations that changed confidence.', 'foundation'),
            m('Pending decisions', 'Questions requiring a human or agent decision.', 'foundation'),
        ], 'Connect Program, Evidence, and Decision queries to a real overview read model.',
        'structures.complex'),
    'programs.hypotheses': view(
        'A falsifiable map of what the program believes and why.',
        'What do we currently believe, and what would change our mind?', [
            m('Hypothesis tree', 'Parent, competing, and dependent hypotheses.', 'foundation'),
            m('Goal & constraint editor', 'Translate intent into explicit success conditions.'),
            m('Evidence balance', 'Supporting, contradicting, and inconclusive evidence.', 'foundation'),
            m('Open questions', 'Knowledge gaps ordered by decision value.'),
            m('Falsification suggestions', 'Experiments or computations most able to reject a claim.'),
        ], 'Add hypothesis list/create commands and evidence-link editing.', 'design.objectives'),
    'programs.progress': view(
        'A durable narrative of milestones, decisions, and scientific state changes.',
        'How did the program get here?', [
            m('Program timeline', 'Events from inception to the current scientific state.'),
            m('Decision log', 'Actor-attributed decisions with evidence and rationale.', 'foundation'),
            m('Milestones', 'Planned and achieved program checkpoints.'),
            m('Promotions & rejections', 'Why molecules or series advanced or stopped.'),
            m('State changes', 'Material changes to goals, confidence, or program direction.'),
        ], 'Create a chronological Program event projection from audit and domain relations.',
        'runs.history'),

    'design.builder': view(
        'The operational molecular design workbench.',
        'What molecule do I want to make?', [
            m('3D molecular workbench', 'Persistent structure scene and molecule focus.', 'available'),
            m('Property preview', 'Live RDKit descriptors for the focused ligand.', 'available'),
            m('Pharmacophore designer', 'Editable interaction intent and screening.', 'available'),
            m('Constraint status', 'Program-objective fit for the current design.', 'foundation'),
        ], 'Add a durable Molecule save command and program-linked design history.'),
    'design.analogs': view(
        'Explore a chemically local neighborhood around a lead or series.',
        'What nearby chemical possibilities are worth exploring?', [
            m('R-group enumeration', 'Enumerate substituent choices at selected attachment points.'),
            m('Scaffold transformations', 'Apply traceable scaffold and bioisostere transformations.'),
            m('Matched-pair suggestions', 'Rank changes supported by historical property shifts.'),
            m('Analog neighborhood', 'Map similarity, novelty, and series membership.'),
            m('Transformation history', 'Preserve the parent and intent of every proposed analog.'),
        ], 'Define Molecule parentage and transformation commands, then render the first series graph.',
        'design.builder'),
    'design.generate': view(
        'A governed proposal stream for broader machine-assisted design-space search.',
        'Search a much larger design space for me.', [
            m('Generation objective', 'Choose the program objective and proposal intent.'),
            m('Generation constraints', 'Hard filters and soft preferences inherited from the program.'),
            m('Proposal stream', 'Review, accept, reject, and explain generated molecules.'),
            m('Diversity control', 'Balance exploitation, novelty, and scaffold coverage.'),
            m('Family clustering', 'Organize proposals into interpretable chemical families.'),
        ], 'Create proposal and review objects before connecting any generative model.',
        'design.objectives'),
    'design.objectives': view(
        'The operational surface for molecular intent and pharmacophore constraints.',
        'What does better mean for this program?', [
            m('Pharmacophore objective', 'Editable spatial interaction requirements.', 'available'),
            m('Property summary', 'Current molecule descriptors and warnings.', 'available'),
            m('Objective weighting', 'Hard versus soft constraint control.', 'foundation'),
            m('Synthetic constraints', 'Chemistry and route feasibility requirements.', 'planned'),
        ], 'Persist program-scoped objectives and evaluate molecules against them.'),

    'structures.complex': view(
        'The operational source of truth for a protein–ligand complex.',
        'What is physically happening in this complex?', [
            m('Persistent 3D scene', 'One Mol* scene shared across structural Views.', 'available'),
            m('Interaction map', 'Residue and atom-level contacts around the ligand.', 'available'),
            m('Field overlay', 'Computed fields and surfaces with provenance.', 'available'),
            m('Structure annotations', 'Actor-attributed structural observations.', 'foundation'),
        ], 'Add durable annotations and structure comparison selections.'),
    'structures.site': view(
        'A focused reading of the binding site as opportunities and constraints.',
        'What opportunities and constraints does this binding site create?', [
            m('Persistent site scene', 'Pocket-focused 3D context.', 'available'),
            m('Field wells', 'Electrostatic, frontier-orbital, and hydrophobic fields.', 'available'),
            m('Pharmacophore', 'Editable interaction features in the shared scene.', 'available'),
            m('Waters & hotspots', 'Water networks and residue opportunity map.', 'foundation'),
        ], 'Persist a BindingSite object and site-specific annotations.'),
    'structures.compare': view(
        'Compare structural states without losing correspondence or provenance.',
        'What changed between these structures, poses, or conformers?', [
            m('Comparison set', 'Choose aligned complexes, poses, or conformers.'),
            m('Alignment controls', 'Define the stable frame and matched atoms.'),
            m('Interaction differences', 'Show gained, lost, and conserved contacts.'),
            m('Field differences', 'Compare homologous scalar fields and surfaces.'),
            m('Difference ledger', 'Record consequential differences as evidence.'),
        ], 'Add a comparison-set context object and two-structure scene projection.',
        'structures.complex'),
    'structures.dynamics': view(
        'The operational entry point for conformational and torsional behavior.',
        'How does this molecular system move?', [
            m('Persistent 3D scene', 'Shared structural context for motion.', 'available'),
            m('Torsion strain', 'Durable torsion scan through the Job system.', 'available'),
            m('Conformer ensemble', 'Rank and compare conformational alternatives.', 'foundation'),
            m('Trajectory analysis', 'Time-dependent structural observables.', 'planned'),
        ], 'Promote conformer ensembles to durable objects, then add trajectory artifacts.'),

    'campaigns.compounds': view(
        'The governed compound portfolio for a program.',
        'Which compounds exist, and where are they in the campaign?', [
            m('Compound table', 'Identity, series, status, owner, and latest evidence.', 'foundation'),
            m('Saved filters', 'Reusable scientific and operational slices.'),
            m('Bulk actions', 'Assign, tag, advance, or request computation.'),
            m('Compound drawer', 'One molecule with provenance and linked results.'),
        ], 'Add campaign membership commands and the first server-backed compound table.',
        'design.builder'),
    'campaigns.sar': view(
        'Connect chemical transformations to measured and predicted property changes.',
        'What structure–activity relationships are supported by the evidence?', [
            m('Series matrix', 'Compounds organized by scaffold and substitution pattern.'),
            m('Matched molecular pairs', 'Comparable transformations and effect sizes.'),
            m('Property selectors', 'Choose endpoint, assay, method, and confidence.'),
            m('SAR annotations', 'Capture interpretable claims and exceptions.'),
        ], 'Define measurement objects and series membership before computing SAR summaries.',
        'campaigns.compounds'),
    'campaigns.landscape': view(
        'A multi-property map of chemical space and campaign coverage.',
        'Where are the promising, crowded, and unexplored regions?', [
            m('Chemical-space map', 'Embeddings with explicit method and version.'),
            m('Property overlays', 'Color and filter by selected evidence-backed endpoints.'),
            m('Series boundaries', 'Interpret clusters as governed campaign entities.'),
            m('Coverage gaps', 'Areas compatible with goals but weakly explored.'),
        ], 'Register a versioned embedding method and artifact-backed landscape projection.',
        'campaigns.compounds'),
    'campaigns.optimize': view(
        'Prioritize the next compounds under multiple objectives and uncertainty.',
        'What should the campaign do next, and why?', [
            m('Objective scorecard', 'One comparable view of potency, selectivity, ADME, and feasibility.'),
            m('Pareto frontier', 'Trade-offs without collapsing everything into one opaque score.'),
            m('Uncertainty', 'Confidence and missing evidence for each candidate.'),
            m('Promotion queue', 'Reviewable recommendations with rationale.'),
        ], 'Connect persisted objectives to a transparent, versioned ranking command.',
        'design.objectives'),

    'synthesis.routes': view(
        'Traceable synthesis options for designed compounds.',
        'How could we make this molecule?', [
            m('Route tree', 'Steps, intermediates, conditions, and alternatives.'),
            m('Feasibility evidence', 'Precedent, confidence, and known route risks.'),
            m('Route comparison', 'Cost, time, yield, safety, and material availability.'),
            m('Human review', 'Chemist decisions preserved with rationale.'),
        ], 'Define Route and Step objects before integrating a retrosynthesis provider.',
        'design.builder'),
    'synthesis.building-blocks': view(
        'A material inventory and sourcing surface connected to proposed routes.',
        'Do we have or can we obtain what the route needs?', [
            m('Building-block catalog', 'Identity, structure, quantity, and source.'),
            m('Availability', 'Inventory and vendor availability with observation time.'),
            m('Route demand', 'Which active routes require each material.'),
            m('Alternatives', 'Compatible substitutes and their route consequences.'),
        ], 'Create material and supplier contracts with time-stamped observations.',
        'synthesis.routes'),
    'synthesis.make': view(
        'An executable, prioritized queue of compounds approved for synthesis.',
        'What should be made next, by whom, and for what decision?', [
            m('Make queue', 'Priority, owner, route, amount, and requested date.'),
            m('Approval state', 'Scientific and operational approvals.'),
            m('Progress', 'Ordered, in progress, blocked, completed, or cancelled.'),
            m('Decision linkage', 'The hypothesis or decision each compound serves.'),
        ], 'Model make requests as Missions with synthesis-specific state and approvals.',
        'runs.missions'),

    'experiments.design': view(
        'Design measurements around hypotheses rather than disconnected assay requests.',
        'What experiment would most reduce the current uncertainty?', [
            m('Hypothesis linkage', 'The claim and decision the experiment addresses.', 'foundation'),
            m('Protocol design', 'Factors, controls, replicates, and acceptance criteria.'),
            m('Sample plan', 'Compounds, concentrations, plates, and allocation.'),
            m('Power & quality', 'Expected resolution and validity checks.'),
        ], 'Create ExperimentDesign and Protocol contracts linked to Hypothesis.',
        'programs.hypotheses'),
    'experiments.assays': view(
        'The governed catalog of assay definitions and measurement semantics.',
        'What exactly does each assay measure?', [
            m('Assay registry', 'Endpoint, protocol version, units, and biological system.'),
            m('Quality history', 'Controls, drift, failure modes, and confidence.'),
            m('Compatibility', 'Compounds, sample requirements, and limitations.'),
            m('Data contract', 'Machine-readable result and missingness semantics.'),
        ], 'Define versioned Assay and Protocol objects before result ingestion.',
        'knowledge.datasets'),
    'experiments.runs': view(
        'Operational execution of planned experiments.',
        'Which experiments are running, blocked, or ready for review?', [
            m('Experiment queue', 'Scheduled and active experimental work.'),
            m('Plate & sample state', 'Concrete execution state and deviations.'),
            m('Quality controls', 'Live control validity and protocol conformance.'),
            m('Run incidents', 'Failures requiring attention and resolution.'),
        ], 'Extend Run with protocol execution and sample lineage.', 'runs.active'),
    'experiments.results': view(
        'Interpret measurement results with units, uncertainty, and provenance intact.',
        'What did the experiment show, and how much should we trust it?', [
            m('Result table', 'Measurements with units, censoring, and replicate structure.'),
            m('Quality assessment', 'Control validity and exclusion rationale.'),
            m('Visualization', 'Endpoint-appropriate plots with uncertainty.'),
            m('Evidence promotion', 'Turn reviewed results into linked Evidence objects.', 'foundation'),
        ], 'Implement a typed Measurement contract and result-ingestion command.',
        'programs.hypotheses'),

    'knowledge.search': view(
        'One search surface across scientific objects, claims, and artifacts.',
        'What does Dirac already know about this question?', [
            m('Unified search', 'Search canonical objects and selected metadata.'),
            m('Filters', 'Kind, program, actor, method, time, and confidence.'),
            m('Result context', 'Why a result matched and where it belongs.'),
            m('Saved investigations', 'Reusable queries and reviewed result sets.'),
        ], 'Build a read-only cross-kind search projection over canonical ObjectRefs.',
        'runs.history'),
    'knowledge.entities': view(
        'Browse the canonical object graph behind the product.',
        'What entities exist, and how are they connected?', [
            m('Entity browser', 'Inspect all registered ObjectKinds.', 'foundation'),
            m('Relationship graph', 'Controlled, actor-attributed object relations.', 'foundation'),
            m('Provenance panel', 'Creation, method, source, and supersession.'),
            m('Related activity', 'Commands, Runs, Jobs, and Decisions touching the entity.'),
        ], 'Expose safe object and relation read commands, then render one entity graph.',
        'runs.history'),
    'knowledge.evidence': view(
        'Audit scientific claims through supporting and contradicting evidence.',
        'Why do we believe this claim?', [
            m('Evidence ledger', 'Evidence with source, confidence, and observation time.', 'foundation'),
            m('Claim relationships', 'Supports, contradicts, supersedes, and depends-on.', 'foundation'),
            m('Provenance trace', 'From conclusion back to method, artifact, or instrument.'),
            m('Conflict review', 'Surface unresolved contradictions for judgment.'),
        ], 'Add evidence-list and relation traversal commands.', 'programs.hypotheses'),
    'knowledge.datasets': view(
        'Govern reusable scientific datasets and their lineage.',
        'Which data can be reused, under what assumptions?', [
            m('Dataset catalog', 'Purpose, schema, owner, version, and access context.'),
            m('Lineage', 'Source observations and transformations.'),
            m('Quality profile', 'Coverage, missingness, drift, and known limitations.'),
            m('Consumers', 'Methods, models, analyses, and decisions using the dataset.'),
        ], 'Define Dataset and DatasetVersion objects with artifact-backed manifests.',
        'knowledge.entities'),

    'runs.missions': view(
        'The durable queue of delegated scientific intent above individual Jobs.',
        'What work has been requested, and what outcome is expected?', [
            m('Mission queue', 'Intent, owner, priority, status, and expected result.', 'foundation'),
            m('Run attempts', 'Every attempt made to fulfill the mission.', 'foundation'),
            m('Dependencies', 'Prerequisite Missions and blocking decisions.'),
            m('Outcome contract', 'Machine-readable completion and review criteria.'),
        ], 'Add Mission list/create commands and link existing Runs in the UI.', 'runs.active'),
    'runs.active': view(
        'The operational monitor for currently executing scientific work.',
        'What is running, failing, or waiting for attention?', [
            m('Job status', 'Durable Job state and cancellation.', 'available'),
            m('Attention queue', 'Actionable failures and approval-waiting Runs.', 'available'),
            m('Execution metadata', 'Method, actor, cache, timing, and artifacts.', 'available'),
            m('Resource utilization', 'Worker and queue capacity.', 'foundation'),
        ], 'Add worker/queue observations when the distributed execution plane lands.'),
    'runs.review': view(
        'A human checkpoint for scientific outputs requiring judgment.',
        'Which completed work needs review or approval?', [
            m('Review queue', 'Outputs awaiting scientific or operational judgment.'),
            m('Result preview', 'Method-appropriate result and caveats.'),
            m('Provenance', 'Inputs, method version, actor, and artifacts.', 'foundation'),
            m('Decision actions', 'Approve, reject, request revision, or escalate.'),
        ], 'Define review state and approval commands on Run outcomes.', 'runs.history'),
    'runs.history': view(
        'The operational ledger of completed and failed work.',
        'What happened, with which inputs and implementation?', [
            m('Job history', 'Durable status, method, timing, and error semantics.', 'available'),
            m('Artifact provenance', 'Content-addressed outputs and lineage.', 'available'),
            m('Command observations', 'Actor, latency, cache, and linked Job evidence.', 'available'),
            m('Re-run & compare', 'Controlled reproduction against a newer method version.', 'foundation'),
        ], 'Add saved filters and explicit reproducibility comparison.'),
};

export function assertExperienceCatalog(viewIds: readonly string[]): void {
    const expected = new Set(viewIds);
    const actual = new Set(Object.keys(VIEW_EXPERIENCES));
    for (const id of expected) if (!actual.has(id)) throw new Error(`${id}: missing View experience`);
    for (const id of actual) if (!expected.has(id)) throw new Error(`${id}: orphan View experience`);
    for (const [id, experience] of Object.entries(VIEW_EXPERIENCES)) {
        if (!experience.modules.length) throw new Error(`${id}: no experience modules`);
        if (!experience.question || !experience.nextMilestone) throw new Error(`${id}: incomplete experience copy`);
    }
}
