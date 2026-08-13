import type { ObjectKind } from '../domain/object-ref';
import { COMMANDS } from '../generated/commands';

export type WorkspaceId = 'programs' | 'design' | 'structures' | 'campaigns'
    | 'synthesis' | 'experiments' | 'knowledge' | 'runs';
export type Placement = 'main' | 'left' | 'right' | 'bottom' | 'overlay';

export interface WorkspaceDefinition {
    id: WorkspaceId; label: string; icon: string; defaultView: string;
    availability: 'implemented' | 'gated'; shellReady: boolean;
}
export interface ViewDefinition {
    id: string; workspace: WorkspaceId; label: string; route: string;
    implemented: boolean; shellReady: boolean; acceptedContext: ObjectKind[];
    delivery: 'shell' | 'connected'; requiresScene: boolean;
    modules: string[]; primaryObjectKinds: ObjectKind[]; actions: string[];
}
export interface ModuleDefinition {
    id: string; version: number; supportedViews: string[];
    requiresContext: ObjectKind[]; consumesObjects: ObjectKind[];
    providesCommands: string[]; surfaces: string[]; placement: Placement; priority: number;
}

/**
 * Every operational surface from the original molecular workbench must have a
 * registry-owned home in the Workspace architecture. This list is the migration
 * contract: removing a module cannot silently strand an old capability.
 */
export const WORKBENCH_SURFACES = [
    'focus', 'semantic', 'ligand', 'properties', 'fields',
    'physics', 'designer', 'vfx', 'ledger', 'runs',
] as const;

export const WORKSPACES: readonly WorkspaceDefinition[] = [
    { id: 'programs', label: 'Programs', icon: '◉', defaultView: 'programs.overview', availability: 'implemented', shellReady: true },
    { id: 'design', label: 'Design', icon: '◇', defaultView: 'design.builder', availability: 'implemented', shellReady: true },
    { id: 'structures', label: 'Structures', icon: '◈', defaultView: 'structures.complex', availability: 'implemented', shellReady: true },
    { id: 'campaigns', label: 'Campaigns', icon: '⊞', defaultView: 'campaigns.compounds', availability: 'gated', shellReady: true },
    { id: 'synthesis', label: 'Synthesis', icon: '⇝', defaultView: 'synthesis.routes', availability: 'gated', shellReady: true },
    { id: 'experiments', label: 'Experiments', icon: '◫', defaultView: 'experiments.design', availability: 'gated', shellReady: true },
    { id: 'knowledge', label: 'Knowledge', icon: '▦', defaultView: 'knowledge.search', availability: 'gated', shellReady: true },
    { id: 'runs', label: 'Compute & Automation', icon: '▷', defaultView: 'runs.active', availability: 'implemented', shellReady: true },
] as const;

const SCENE_VIEWS = new Set([
    'design.builder', 'design.objectives', 'structures.complex',
    'structures.site', 'structures.dynamics',
]);

const view = (id: string, workspace: WorkspaceId, label: string, route: string,
    implemented = false, modules: string[] = [], primaryObjectKinds: ObjectKind[] = [],
    actions: string[] = []): ViewDefinition => ({ id, workspace, label, route, implemented,
    delivery: implemented ? 'connected' : 'shell', requiresScene: SCENE_VIEWS.has(id),
    shellReady: true, modules, primaryObjectKinds, actions, acceptedContext: primaryObjectKinds });

export const VIEWS: readonly ViewDefinition[] = [
    view('programs.overview', 'programs', 'Overview', '/p/:programId', true,
        ['program.overview'], ['program', 'portfolio', 'target', 'compound', 'series', 'evidence'],
        ['program.list', 'program.get', 'program.create', 'program.update',
            'portfolio.create', 'portfolio.list', 'program.portfolio.assign',
            'program.member.assign', 'program.objective.record', 'program.hypothesis.record',
            'program.decision.record', 'program.milestone.record', 'program.stage_gate.record',
            'program.work_package.record', 'program.evidence.attach', 'program.lineage.record',
            'program.health.get', 'program.link', 'program.snapshot.create']),
    view('programs.hypotheses', 'programs', 'Hypotheses & Goals', '/p/:programId/hypotheses'),
    view('programs.progress', 'programs', 'Progress & Decisions', '/p/:programId/progress'),
    view('design.builder', 'design', 'Builder', '/p/:programId/design/builder', true,
        ['chem.builder', 'chem.property-summary'], ['molecule'], ['conformer.generate', 'molecule.properties']),
    view('design.analogs', 'design', 'Analog & Series Design', '/p/:programId/design/analogs'),
    view('design.generate', 'design', 'Generative Design', '/p/:programId/design/generate'),
    view('design.objectives', 'design', 'Constraints & Objectives', '/p/:programId/design/objectives', true,
        ['design.pharmacophore', 'chem.property-summary'], ['molecule', 'program'], ['molecule.properties']),
    view('structures.complex', 'structures', 'Complex', '/p/:programId/structures/complex', true,
        ['scene.viewport', 'structure.interaction-map', 'structure.field-overlay'],
        ['complex', 'molecule'], ['structure.field.compute']),
    view('structures.site', 'structures', 'Binding Site', '/p/:programId/structures/site', true,
        ['scene.viewport', 'structure.field-overlay', 'design.pharmacophore'],
        ['complex'], ['structure.surface.compute']),
    view('structures.compare', 'structures', 'Compare', '/p/:programId/structures/compare'),
    view('structures.dynamics', 'structures', 'Dynamics', '/p/:programId/structures/dynamics', true,
        ['scene.viewport', 'structure.torsion-strain'], ['conformer', 'pose'], ['structure.torsion.analyze']),
    view('campaigns.compounds', 'campaigns', 'Compounds', '/p/:programId/campaigns/compounds'),
    view('campaigns.sar', 'campaigns', 'SAR', '/p/:programId/campaigns/sar'),
    view('campaigns.landscape', 'campaigns', 'Landscape', '/p/:programId/campaigns/landscape'),
    view('campaigns.optimize', 'campaigns', 'Optimization', '/p/:programId/campaigns/optimize'),
    view('synthesis.routes', 'synthesis', 'Routes', '/p/:programId/synthesis/routes'),
    view('synthesis.building-blocks', 'synthesis', 'Building Blocks', '/p/:programId/synthesis/building-blocks'),
    view('synthesis.make', 'synthesis', 'Make Queue', '/p/:programId/synthesis/make'),
    view('experiments.design', 'experiments', 'Design', '/p/:programId/experiments/design'),
    view('experiments.assays', 'experiments', 'Assays', '/p/:programId/experiments/assays'),
    view('experiments.runs', 'experiments', 'Execution', '/p/:programId/experiments/runs'),
    view('experiments.results', 'experiments', 'Results', '/p/:programId/experiments/results'),
    view('knowledge.search', 'knowledge', 'Search', '/knowledge/search'),
    view('knowledge.entities', 'knowledge', 'Entities', '/knowledge/entities'),
    view('knowledge.evidence', 'knowledge', 'Evidence', '/knowledge/evidence'),
    view('knowledge.datasets', 'knowledge', 'Datasets', '/knowledge/datasets'),
    view('runs.missions', 'runs', 'Missions', '/runs/missions'),
    view('runs.active', 'runs', 'Active', '/runs/active', true,
        ['run.job-status', 'run.attention'], ['mission', 'run', 'job'], ['job.list', 'job.cancel']),
    view('runs.review', 'runs', 'Review', '/runs/review'),
    view('runs.history', 'runs', 'History', '/runs/history', true,
        ['run.job-status', 'evidence.provenance'], ['run', 'job', 'artifact'], ['job.list', 'job.get']),
] as const;

export const MODULES: readonly ModuleDefinition[] = [
    { id: 'program.overview', version: 2, supportedViews: ['programs.overview'], requiresContext: [], consumesObjects: ['program', 'portfolio', 'target', 'objective', 'hypothesis', 'decision', 'milestone', 'stage_gate', 'work_package', 'compound', 'compound_form', 'batch', 'sample', 'evidence', 'measurement'], providesCommands: ['program.list', 'program.get', 'program.create', 'program.update', 'portfolio.create', 'portfolio.list', 'program.portfolio.assign', 'program.member.assign', 'program.objective.record', 'program.hypothesis.record', 'program.decision.record', 'program.milestone.record', 'program.stage_gate.record', 'program.work_package.record', 'program.evidence.attach', 'program.lineage.record', 'program.health.get', 'program.link', 'program.snapshot.create'], surfaces: ['ledger'], placement: 'main', priority: 100 },
    { id: 'scene.viewport', version: 1, supportedViews: ['structures.complex', 'structures.site', 'structures.dynamics'], requiresContext: [], consumesObjects: ['complex', 'molecule'], providesCommands: [], surfaces: ['focus', 'semantic', 'vfx'], placement: 'main', priority: 100 },
    { id: 'structure.interaction-map', version: 1, supportedViews: ['structures.complex'], requiresContext: ['complex'], consumesObjects: ['complex'], providesCommands: ['structure.interactions'], surfaces: ['ledger'], placement: 'right', priority: 80 },
    { id: 'structure.field-overlay', version: 1, supportedViews: ['structures.complex', 'structures.site'], requiresContext: ['molecule'], consumesObjects: ['field', 'artifact'], providesCommands: ['structure.field.compute', 'structure.surface.compute'], surfaces: ['fields'], placement: 'right', priority: 90 },
    { id: 'structure.torsion-strain', version: 1, supportedViews: ['structures.dynamics'], requiresContext: ['molecule'], consumesObjects: ['pose', 'conformer'], providesCommands: ['structure.torsion.analyze'], surfaces: ['physics'], placement: 'right', priority: 70 },
    { id: 'chem.builder', version: 1, supportedViews: ['design.builder'], requiresContext: [], consumesObjects: ['molecule'], providesCommands: ['conformer.generate'], surfaces: ['focus', 'ligand'], placement: 'main', priority: 100 },
    { id: 'chem.property-summary', version: 1, supportedViews: ['design.builder', 'design.objectives'], requiresContext: ['molecule'], consumesObjects: ['molecule', 'prediction'], providesCommands: ['molecule.properties'], surfaces: ['properties'], placement: 'right', priority: 60 },
    { id: 'design.pharmacophore', version: 1, supportedViews: ['design.objectives', 'structures.site'], requiresContext: ['molecule'], consumesObjects: ['molecule', 'complex'], providesCommands: [], surfaces: ['designer'], placement: 'right', priority: 75 },
    { id: 'run.job-status', version: 1, supportedViews: ['runs.active', 'runs.history'], requiresContext: [], consumesObjects: ['job', 'run'], providesCommands: ['job.list', 'job.get', 'job.cancel'], surfaces: ['runs'], placement: 'main', priority: 100 },
    { id: 'run.attention', version: 1, supportedViews: ['runs.active'], requiresContext: [], consumesObjects: ['mission', 'run', 'job'], providesCommands: ['attention.list'], surfaces: ['runs'], placement: 'right', priority: 90 },
    { id: 'evidence.provenance', version: 1, supportedViews: ['runs.history'], requiresContext: [], consumesObjects: ['artifact', 'job', 'evidence'], providesCommands: ['job.get'], surfaces: ['ledger', 'runs'], placement: 'right', priority: 80 },
] as const;

export function availableViews(workspace: WorkspaceId): readonly ViewDefinition[] {
    return VIEWS.filter(v => v.workspace === workspace && v.implemented);
}

/** Views whose durable product route and honest UI shell exist, independent of capability depth. */
export function navigableViews(workspace: WorkspaceId): readonly ViewDefinition[] {
    return VIEWS.filter(v => v.workspace === workspace && v.shellReady);
}

export function modulesForView(viewId: string): readonly ModuleDefinition[] {
    const definition = VIEWS.find(v => v.id === viewId && v.implemented);
    if (!definition) return [];
    return definition.modules.map(id => MODULES.find(module => module.id === id)!)
        .sort((a, b) => b.priority - a.priority);
}

export function assertRegistryIntegrity(
    workspaces: readonly WorkspaceDefinition[] = WORKSPACES,
    views: readonly ViewDefinition[] = VIEWS,
    modules: readonly ModuleDefinition[] = MODULES,
    commands: readonly { id: string }[] = COMMANDS,
): void {
    const workspaceIds: ReadonlySet<string> = new Set(workspaces.map(w => w.id));
    const viewIds: ReadonlySet<string> = new Set(views.map(v => v.id));
    const moduleIds: ReadonlySet<string> = new Set(modules.map(m => m.id));
    const commandIds: ReadonlySet<string> = new Set(commands.map(c => c.id));
    if (workspaceIds.size !== 8) throw new Error(`expected 8 workspaces, got ${workspaceIds.size}`);
    if (viewIds.size !== 30) throw new Error(`expected 30 views, got ${viewIds.size}`);
    for (const workspace of workspaces) {
        if (!viewIds.has(workspace.defaultView)) throw new Error(`${workspace.id}: missing default view`);
        if (!workspace.shellReady) throw new Error(`${workspace.id}: product shell is not ready`);
    }
    for (const v of views) {
        if (!workspaceIds.has(v.workspace)) throw new Error(`${v.id}: missing workspace`);
        if (!v.shellReady) throw new Error(`${v.id}: product shell is not ready`);
        for (const id of v.modules) if (!moduleIds.has(id)) throw new Error(`${v.id}: missing module ${id}`);
    }
    for (const module of modules) {
        if (!module.surfaces.length) throw new Error(`${module.id}: no render surfaces`);
        for (const id of module.supportedViews) if (!viewIds.has(id)) throw new Error(`${module.id}: missing view ${id}`);
        for (const id of module.providesCommands) if (!commandIds.has(id)) throw new Error(`${module.id}: missing command ${id}`);
    }
    const absorbedSurfaces = new Set(modules.flatMap(module => module.surfaces));
    for (const surface of WORKBENCH_SURFACES) {
        if (!absorbedSurfaces.has(surface)) throw new Error(`unabsorbed workbench surface ${surface}`);
    }
}

assertRegistryIntegrity();
