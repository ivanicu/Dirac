import { VIEWS } from './registries';

export type WorkspaceVisualKind = 'flow' | 'timeline' | 'matrix' | 'scatter' | 'network'
    | 'funnel' | 'kanban' | 'curve' | 'plate' | 'table' | 'lineage' | 'compare';

export interface WorkspaceVisualSpec {
    kind: WorkspaceVisualKind;
    title: string;
    caption: string;
    primary: readonly string[];
    secondary?: readonly string[];
    xLabel?: string;
    yLabel?: string;
}

/**
 * The primary information shape for every product View.
 *
 * These are semantic chart contracts, not fixture datasets. Until a read model is
 * connected the renderer draws the axes, stages, columns, or relationships and marks
 * the observation layer as empty. This keeps the intended UX inspectable without
 * presenting invented scientific measurements as product truth.
 */
export const WORKSPACE_VISUALS: Readonly<Record<string, WorkspaceVisualSpec>> = {
    'programs.overview': {
        kind: 'flow', title: 'Program decision pulse',
        caption: 'Goals → evidence → bottlenecks → decisions, on one causal reading path.',
        primary: ['Goals', 'Evidence', 'Bottlenecks', 'Decisions'], secondary: ['Current state', 'Next review'],
    },
    'programs.hypotheses': {
        kind: 'network', title: 'Hypothesis and evidence map',
        caption: 'Competing claims remain separate; support and contradiction converge on decisions.',
        primary: ['Primary hypothesis', 'Alternative', 'Null'], secondary: ['Supporting evidence', 'Contradicting evidence', 'Open test'],
    },
    'programs.progress': {
        kind: 'timeline', title: 'Program history',
        caption: 'Milestones, evidence changes, promotions, and decisions in chronological order.',
        primary: ['Program start', 'Milestone', 'Evidence change', 'Decision', 'Current state'],
    },

    'design.builder': {
        kind: 'flow', title: 'Molecule design loop', caption: 'Intent → structure → properties → review.',
        primary: ['Intent', 'Build', 'Evaluate', 'Review'],
    },
    'design.analogs': {
        kind: 'network', title: 'Analog neighborhood',
        caption: 'Parentage and transformations stay visible while nearby chemical options branch.',
        primary: ['Lead', 'R-group series', 'Scaffold series'], secondary: ['Matched pair', 'Bioisostere', 'Novel branch'],
    },
    'design.generate': {
        kind: 'funnel', title: 'Governed proposal funnel',
        caption: 'A proposal must survive constraints, diversity review, and human judgment before promotion.',
        primary: ['Generated', 'Constraint pass', 'Diversity review', 'Human review', 'Promoted'],
    },
    'design.objectives': {
        kind: 'matrix', title: 'Objective and constraint matrix', caption: 'Hard bounds and soft preferences remain distinguishable.',
        primary: ['Potency', 'Selectivity', 'ADME', 'Synthesis'], secondary: ['Hard bound', 'Soft preference', 'Unknown'],
    },

    'structures.complex': {
        kind: 'network', title: 'Complex interaction map', caption: 'Ligand, residues, waters, and field evidence share one structural context.',
        primary: ['Ligand', 'Pocket residues', 'Waters'], secondary: ['Contacts', 'Fields', 'Annotations'],
    },
    'structures.site': {
        kind: 'matrix', title: 'Binding-site opportunity map', caption: 'Spatial opportunities are organized by interaction type and confidence.',
        primary: ['Hydrogen bond', 'Hydrophobic', 'Electrostatic', 'Water'], secondary: ['Required', 'Optional', 'Blocked'],
    },
    'structures.compare': {
        kind: 'compare', title: 'Structure comparison',
        caption: 'Two structures keep synchronized selections; only interpretable differences enter the center rail.',
        primary: ['Reference structure', 'Comparison structure'], secondary: ['Geometry', 'Interactions', 'Fields', 'Confidence'],
    },
    'structures.dynamics': {
        kind: 'curve', title: 'Dynamics observables', caption: 'Trajectory observables share time and uncertainty rather than isolated snapshots.',
        primary: ['RMSD', 'RMSF', 'Contacts'], xLabel: 'Trajectory time', yLabel: 'Observable',
    },

    'campaigns.compounds': {
        kind: 'table', title: 'Compound portfolio', caption: 'Identity, series, evidence freshness, owner, and promotion state in one governed table.',
        primary: ['Compound', 'Series', 'Evidence', 'Owner', 'State'],
    },
    'campaigns.sar': {
        kind: 'matrix', title: 'SAR criterion matrix',
        caption: 'Compound × endpoint cells distinguish pass, exceeded bound, and not measured.',
        primary: ['Series member', 'R-group', 'Transformation'], secondary: ['Potency', 'Selectivity', 'Solubility', 'Stability'],
    },
    'campaigns.landscape': {
        kind: 'scatter', title: 'Chemical-space landscape', caption: 'Series coverage and unexplored regions share an explicit embedding method.',
        primary: ['Series', 'Candidates', 'Coverage gaps'], xLabel: 'Embedding dimension 1', yLabel: 'Embedding dimension 2',
    },
    'campaigns.optimize': {
        kind: 'scatter', title: 'Multi-objective frontier', caption: 'Trade-offs stay visible; uncertainty is not collapsed into one opaque score.',
        primary: ['Candidates', 'Pareto frontier', 'Uncertainty'], xLabel: 'Benefit objective', yLabel: 'Cost / risk objective',
    },

    'synthesis.routes': {
        kind: 'lineage', title: 'Synthesis route tree', caption: 'Alternative disconnections converge on the requested molecule with provenance.',
        primary: ['Target', 'Route A', 'Route B'], secondary: ['Starting materials', 'Intermediates', 'Conditions'],
    },
    'synthesis.building-blocks': {
        kind: 'matrix', title: 'Material coverage matrix', caption: 'Route demand is compared with inventory, suppliers, lead time, and substitutes.',
        primary: ['Required material', 'Route demand', 'Alternative'], secondary: ['In stock', 'Supplier', 'Unknown'],
    },
    'synthesis.make': {
        kind: 'kanban', title: 'Executable make queue', caption: 'Every request carries an owner, route, decision purpose, and explicit blocked state.',
        primary: ['Approved', 'Ordered', 'In progress', 'Quality review', 'Completed'],
    },

    'experiments.design': {
        kind: 'matrix', title: 'Experiment design matrix', caption: 'Factors, controls, replicates, and acceptance criteria stay visible before execution.',
        primary: ['Hypothesis', 'Factor', 'Control', 'Replicate'], secondary: ['Protocol', 'Sample', 'Acceptance'],
    },
    'experiments.assays': {
        kind: 'table', title: 'Assay registry', caption: 'Endpoint semantics, protocol versions, units, and quality history are first-class.',
        primary: ['Assay', 'Endpoint', 'Protocol', 'Units', 'Quality'],
    },
    'experiments.runs': {
        kind: 'plate', title: 'Experiment execution board', caption: 'Plate position, sample lineage, controls, and incidents remain linked.',
        primary: ['Samples', 'Controls', 'Replicates'], secondary: ['Queued', 'Running', 'Incident'],
    },
    'experiments.results': {
        kind: 'curve', title: 'Measurement response', caption: 'Replicates, fit, uncertainty, censoring, and exclusions share one evidence surface.',
        primary: ['Replicates', 'Model fit', 'Confidence band'], xLabel: 'Dose / condition', yLabel: 'Measured response',
    },

    'knowledge.search': {
        kind: 'funnel', title: 'Search and review path', caption: 'A query narrows by object kind, provenance, method, time, and confidence.',
        primary: ['All objects', 'Typed results', 'Evidence filter', 'Reviewed set'],
    },
    'knowledge.entities': {
        kind: 'network', title: 'Canonical entity graph', caption: 'Objects, relations, provenance, and activity remain separately inspectable.',
        primary: ['Entity', 'Related objects', 'Activity'], secondary: ['Relations', 'Provenance', 'Artifacts'],
    },
    'knowledge.evidence': {
        kind: 'network', title: 'Claim and evidence graph', caption: 'Support, contradiction, dependency, and supersession converge on one claim.',
        primary: ['Claim', 'Support', 'Contradiction'], secondary: ['Method', 'Artifact', 'Confidence'],
    },
    'knowledge.datasets': {
        kind: 'lineage', title: 'Dataset lineage', caption: 'Source observations flow through versioned transforms to governed consumers.',
        primary: ['Sources', 'Dataset version', 'Consumers'], secondary: ['Transform', 'Quality profile', 'Known limitations'],
    },

    'runs.missions': {
        kind: 'kanban', title: 'Mission control', caption: 'Durable intent sits above attempts, jobs, dependencies, and review outcomes.',
        primary: ['Requested', 'Ready', 'Running', 'Review', 'Resolved'],
    },
    'runs.active': {
        kind: 'timeline', title: 'Active execution', caption: 'Jobs, attention events, artifacts, and cancellation share one execution clock.',
        primary: ['Queued', 'Started', 'Artifact', 'Review'],
    },
    'runs.review': {
        kind: 'table', title: 'Scientific review queue', caption: 'Result, provenance, caveats, and review decision are adjacent.',
        primary: ['Outcome', 'Method', 'Caveat', 'Reviewer', 'Decision'],
    },
    'runs.history': {
        kind: 'timeline', title: 'Execution history', caption: 'Completed and failed work remains reproducible from inputs through artifacts.',
        primary: ['Invocation', 'Job', 'Artifact', 'Outcome'],
    },
};

export function assertWorkspaceVisualCatalog(viewIds: readonly string[] = VIEWS.map(view => view.id)): void {
    const missing = viewIds.filter(id => !WORKSPACE_VISUALS[id]);
    const extra = Object.keys(WORKSPACE_VISUALS).filter(id => !viewIds.includes(id));
    if (missing.length || extra.length) {
        throw new Error(`workspace visual catalog mismatch: missing=${missing.join(',')} extra=${extra.join(',')}`);
    }
}

assertWorkspaceVisualCatalog();
