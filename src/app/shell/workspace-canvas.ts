import type { ShellRoute } from './app-shell';
import { VIEWS, WORKSPACES, type ViewDefinition } from './registries';
import { VIEW_EXPERIENCES, type ExperienceModule } from './workspace-catalog';
import { renderWorkspaceVisual } from './workspace-visuals';
import { OBJECT_KINDS, type ObjectKind, type ObjectRef } from '../generated/commands';
import { scientificContext } from '../context/scientific-context-store';
import { VIEW_PLANS } from './workspace-plans';
import { deriveViewState } from './view-state';

type Navigate = (route: ShellRoute) => void;

const element = <K extends keyof HTMLElementTagNameMap>(tag: K, className?: string,
    text?: string): HTMLElementTagNameMap[K] => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
};

const storageKey = (kind: 'note', viewId: string) => `dirac:shell:${kind}:${viewId}`;

function readLocal(key: string): string {
    try { return localStorage.getItem(key) || ''; } catch { return ''; }
}

function writeLocal(key: string, value: string): boolean {
    try {
        if (value) localStorage.setItem(key, value);
        else localStorage.removeItem(key);
        return true;
    } catch { return false; }
}

/** Human-readable canvas for Views whose route exists before their scientific modules do. */
export class WorkspaceCanvas {
    private activeViewId = '';
    private search?: HTMLInputElement;

    private workflowLane(workspace: string): string | undefined {
        return ({ structures: 'understand', design: 'design', campaigns: 'decide',
            synthesis: 'make', experiments: 'test_learn' } as Record<string, string>)[workspace];
    }

    private workflowContext(workspace: string): HTMLElement | undefined {
        const lane = this.workflowLane(workspace);
        if (!lane) return undefined;
        const section = element('section', 'workspace-workflow-context');
        section.dataset.workflowLane = lane;
        section.append(element('span', 'workspace-section-kicker', 'Program workflow'),
            element('h2', '', `${lane.replace('_', ' & ')} work`),
            element('p', 'workspace-workflow-status', 'Loading unique Program Work Items…'));
        const list = element('div', 'workspace-workflow-items'); list.dataset.workflowItems = '';
        section.append(list); return section;
    }

    constructor(private readonly host: HTMLElement, private readonly breadcrumb: HTMLElement,
                private readonly navigate: Navigate) {
        document.addEventListener('keydown', event => {
            if (event.key === '/' && document.getElementById('app')?.classList.contains('shell-scaffold')
                && event.target instanceof HTMLElement
                && !['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) {
                event.preventDefault();
                this.host.querySelector<HTMLDetailsElement>('.workspace-readiness')?.setAttribute('open', '');
                this.search?.focus();
            }
        });
    }

    render(route: ShellRoute): void {
        const definition = VIEWS.find(view => view.id === route.view);
        const workspace = WORKSPACES.find(item => item.id === route.workspace);
        if (!definition || !workspace) return;
        const scaffold = !definition.requiresScene;
        const connected = definition.delivery === 'connected';
        const app = document.getElementById('app');
        app?.classList.add('workspace-shell');
        app?.classList.toggle('shell-scaffold', scaffold);
        app?.classList.toggle('shell-connected', scaffold && connected);
        if (app) app.dataset.workspace = route.workspace;
        this.breadcrumb.hidden = !scaffold;
        this.breadcrumb.textContent = scaffold ? `${workspace.label}  /  ${definition.label}` : '';
        this.host.hidden = !scaffold;
        this.activeViewId = definition.id;
        if (!scaffold) {
            this.host.replaceChildren();
            this.search = undefined;
            return;
        }
        if (connected) this.renderConnectedCanvas(definition, route.programId);
        else this.renderCanvas(definition, route.programId);
    }

    private renderCanvas(definition: ViewDefinition, programId?: string): void {
        const experience = VIEW_EXPERIENCES[definition.id];
        if (!experience) throw new Error(`${definition.id}: missing View experience`);
        const workspace = WORKSPACES.find(item => item.id === definition.workspace)!;
        const state = deriveViewState(definition, scientificContext.current());

        const page = element('article', 'workspace-page');
        page.dataset.workspace = workspace.id;
        const header = element('header', 'workspace-page-header');
        const titleBlock = element('div', 'workspace-page-title');
        const eyebrow = element('div', 'workspace-page-eyebrow');
        eyebrow.append(element('span', 'workspace-page-icon', workspace.icon),
            element('span', '', `${workspace.label} workspace`),
            element('span', 'workspace-page-separator', '·'),
            element('span', 'workspace-page-state', 'Preview'));
        const title = element('h1', '', definition.label);
        title.id = 'workspace-view-title';
        title.tabIndex = -1;
        titleBlock.append(eyebrow, title,
            element('p', '', experience.summary));

        const target = experience.liveTarget ? VIEWS.find(view => view.id === experience.liveTarget) : undefined;
        const targetWorkspace = target ? WORKSPACES.find(item => item.id === target.workspace) : undefined;
        const openTarget = target ? () => this.navigate({
            workspace: target.workspace, view: target.id, programId: programId || 'current',
        }) : undefined;
        const relatedLabel = target && targetWorkspace
            ? `Open related ${targetWorkspace.label} · ${target.label}${target.delivery === 'shell' ? ' preview' : ''}`
            : undefined;
        const mobileSourceAction = element('button', 'workspace-mobile-source-action',
            VIEW_PLANS[definition.id]?.sourceLabel || 'Select source object');
        mobileSourceAction.type = 'button';
        mobileSourceAction.addEventListener('click', () => this.openSourceDialog(definition, programId));
        header.append(titleBlock, this.stateStrip(state), mobileSourceAction);

        const question = element('section', 'workspace-question');
        question.append(element('span', '', 'The human question'),
            element('blockquote', '', experience.question));
        const selected = scientificContext.current().focusedObject
            || scientificContext.current().selectedObjects[0];
        const visual = renderWorkspaceVisual(definition, experience, {
            actionLabel: VIEW_PLANS[definition.id]?.sourceLabel || 'Select source object',
            onAction: () => this.openSourceDialog(definition, programId),
            relatedLabel, onRelated: openTarget,
            selectedSource: selected ? `${selected.kind}:${selected.id}` : undefined,
        });

        const toolbar = element('div', 'workspace-module-toolbar');
        const moduleHeading = element('div');
        moduleHeading.append(element('span', 'workspace-section-kicker', 'Product readiness'),
            element('h2', '', 'Planned data and interactions'));
        const searchWrap = element('label', 'workspace-module-search');
        searchWrap.append(element('span', '', 'Filter modules'));
        const search = element('input') as HTMLInputElement;
        search.type = 'search';
        search.placeholder = 'Type / to focus';
        searchWrap.append(search);
        toolbar.append(moduleHeading, searchWrap);
        this.search = search;

        const moduleGrid = element('section', 'workspace-module-grid');
        const resultStatus = element('p', 'workspace-filter-status');
        resultStatus.setAttribute('role', 'status');
        const renderModules = () => {
            if (definition.id !== this.activeViewId) return;
            const query = search.value.trim().toLowerCase();
            const modules = experience.modules.filter(module =>
                `${module.title} ${module.purpose}`.toLowerCase().includes(query));
            moduleGrid.replaceChildren(...modules.map((module, index) =>
                this.moduleCard(module, index + 1)));
            if (!modules.length) moduleGrid.append(element('p', 'workspace-no-results',
                'No module matches this filter.'));
            resultStatus.textContent = `${modules.length} module${modules.length === 1 ? '' : 's'} shown`;
        };
        search.addEventListener('input', renderModules);
        renderModules();

        const lower = element('section', 'workspace-lower-grid');
        const milestone = element('div', 'workspace-milestone');
        milestone.append(element('span', 'workspace-section-kicker', 'Connection plan'),
            element('h2', '', 'What must be connected next'),
            element('p', '', experience.nextMilestone));
        const ladder = element('ol', 'workspace-delivery-ladder');
        const stages = [
            ['01', 'Select a source object', 'required'],
            ['02', 'Load traceable observations', experience.modules.some(m => m.readiness === 'foundation') ? 'partly ready' : 'required'],
            ['03', 'Review quality and provenance', 'required'],
            ['04', 'Enable decisions and actions', 'required'],
        ] as const;
        for (const [index, label, status] of stages) {
            const row = element('li');
            row.dataset.status = status;
            row.append(element('span', '', index), element('strong', '', label),
                element('small', '', status));
            ladder.append(row);
        }
        milestone.append(ladder);

        const notebook = element('div', 'workspace-notebook');
        notebook.append(element('span', 'workspace-section-kicker', 'Private note'),
            element('h2', '', 'Capture working context'),
            element('p', '', 'This note stays in this browser. It is not shared, durable, or scientific evidence.'));
        const textarea = element('textarea') as HTMLTextAreaElement;
        textarea.setAttribute('aria-label', `Private working note for ${definition.label}`);
        textarea.placeholder = 'Capture the first workflow, decision, or data source to connect…';
        textarea.value = readLocal(storageKey('note', definition.id));
        const saved = element('span', 'workspace-note-saved', textarea.value ? 'Saved locally' : 'Not saved');
        let saveTimer: ReturnType<typeof setTimeout> | undefined;
        textarea.addEventListener('input', () => {
            saved.textContent = 'Saving…';
            if (saveTimer) clearTimeout(saveTimer);
            saveTimer = setTimeout(() => {
                const stored = writeLocal(storageKey('note', definition.id), textarea.value.trim());
                saved.textContent = stored
                    ? (textarea.value.trim() ? 'Saved locally' : 'Not saved')
                    : 'Could not save in this browser';
            }, 250);
        });
        saved.setAttribute('role', 'status');
        notebook.append(textarea, saved);
        lower.append(milestone, notebook);

        const readiness = element('details', 'workspace-readiness');
        const readinessSummary = element('summary');
        readinessSummary.append(element('strong', '', 'Product readiness'),
            element('span', '', 'Planned modules, connection sequence, and private note'));
        readiness.append(readinessSummary, toolbar, resultStatus, moduleGrid, lower);

        const workflow = this.workflowContext(definition.workspace);
        page.append(header, question, ...(workflow ? [workflow] : []), visual, readiness);
        this.host.replaceChildren(page);
        if (workflow) queueMicrotask(() => document.dispatchEvent(new CustomEvent('dirac:refresh-program')));
    }

    private renderConnectedCanvas(definition: ViewDefinition, programId?: string): void {
        if (definition.id === 'programs.overview') {
            this.renderProgramCanvas(definition, programId);
            return;
        }
        const experience = VIEW_EXPERIENCES[definition.id];
        const state = deriveViewState(definition, scientificContext.current());
        const page = element('article', 'workspace-page workspace-page--connected');
        const header = element('header', 'workspace-page-header');
        const titleBlock = element('div', 'workspace-page-title');
        const eyebrow = element('div', 'workspace-page-eyebrow');
        eyebrow.append(element('span', 'workspace-page-state', 'Connected capability'),
            element('span', 'workspace-page-separator', '·'),
            element('span', '', 'No 3D scene required'));
        const title = element('h1', '', definition.label);
        title.id = 'workspace-view-title'; title.tabIndex = -1;
        titleBlock.append(eyebrow, title, element('p', '', experience.summary));
        header.append(titleBlock, this.stateStrip(state));
        const question = element('section', 'workspace-question');
        question.append(element('span', '', 'The human question'),
            element('blockquote', '', experience.question));
        const runs = element('section', 'workspace-native-runs');
        runs.setAttribute('aria-labelledby', 'workspace-runs-heading');
        const runsHeading = element('div', 'workspace-native-runs-heading');
        const copy = element('div');
        const h2 = element('h2', '', definition.id === 'runs.history' ? 'Execution history' : 'Active execution');
        h2.id = 'workspace-runs-heading';
        copy.append(h2, element('p', '', 'Mission, Run, and Job remain distinct. Runtime and evidence state are reported separately.'));
        const refresh = element('button', 'workspace-visual-action', 'Refresh jobs');
        refresh.type = 'button';
        refresh.addEventListener('click', () => document.dispatchEvent(new CustomEvent('dirac:refresh-runs')));
        runsHeading.append(copy, refresh);
        const status = element('p', 'workspace-run-summary', 'Loading durable jobs…');
        status.dataset.runSummary = '';
        status.setAttribute('role', 'status');
        const list = element('div', 'run-job-list');
        list.dataset.runList = '';
        list.setAttribute('aria-live', 'polite');
        runs.append(runsHeading, status, list);
        const workflow = this.workflowContext(definition.workspace);
        page.append(header, question, ...(workflow ? [workflow] : []), runs);
        this.host.replaceChildren(page);
        if (workflow) queueMicrotask(() => document.dispatchEvent(new CustomEvent('dirac:refresh-program')));
    }

    private renderProgramCanvas(definition: ViewDefinition, programId?: string): void {
        const experience = VIEW_EXPERIENCES[definition.id];
        const state = deriveViewState(definition, scientificContext.current());
        const page = element('article', 'workspace-page workspace-page--connected program-page');
        page.dataset.programId = programId || 'current';
        const header = element('header', 'workspace-page-header');
        const titleBlock = element('div', 'workspace-page-title');
        const eyebrow = element('div', 'workspace-page-eyebrow');
        eyebrow.append(element('span', 'workspace-page-state', 'Program fact root'),
            element('span', 'workspace-page-separator', '·'),
            element('span', '', 'Versioned and provenance backed'));
        const title = element('h1', '', definition.label);
        title.id = 'workspace-view-title'; title.tabIndex = -1;
        titleBlock.append(eyebrow, title, element('p', '', experience.summary));
        header.append(titleBlock, this.stateStrip(state));

        const question = element('section', 'workspace-question');
        question.append(element('span', '', 'The human question'),
            element('blockquote', '', experience.question));

        const workbench = element('section', 'program-workspace');
        workbench.setAttribute('aria-label', 'Program workspace');
        const toolbar = element('div', 'program-toolbar');
        const selectLabel = element('label', 'program-selector');
        selectLabel.append(element('span', '', 'Current Program'));
        const select = element('select') as HTMLSelectElement;
        select.dataset.programSelect = ''; select.setAttribute('aria-label', 'Current Program');
        select.add(new Option('Loading Programs…', ''));
        selectLabel.append(select);
        const toolbarActions = element('div', 'program-toolbar-actions');
        const button = (label: string, action: string, secondary = false) => {
            const node = element('button', secondary ? 'workspace-visual-related' : 'workspace-visual-action', label);
            node.type = 'button'; node.dataset.programAction = action; return node;
        };
        toolbarActions.append(button('New Program', 'create'), button('New Portfolio', 'create-portfolio', true),
            button('Refresh', 'refresh', true));
        toolbar.append(selectLabel, toolbarActions);

        const status = element('p', 'program-load-status', 'Loading durable Program state…');
        status.dataset.programStatus = ''; status.setAttribute('role', 'status');
        const empty = element('section', 'program-empty-state');
        empty.dataset.programEmpty = '';
        empty.append(element('span', 'workspace-section-kicker', 'Start here'),
            element('h2', '', 'Create or select a Program'),
            element('p', '', 'A Program owns objectives, hypotheses, decisions, milestones, and the frozen context handed to Design.'),
            button('Create the first Program', 'create'));

        const dashboard = element('div', 'program-dashboard');
        dashboard.dataset.programDashboard = ''; dashboard.hidden = true;
        const identity = element('section', 'program-identity');
        const identityCopy = element('div');
        identityCopy.append(element('span', 'workspace-section-kicker', 'Active aggregate'),
            element('h2', '', '—'), element('p', '', ''));
        identityCopy.querySelector('h2')!.dataset.programTitle = '';
        identityCopy.querySelector('p')!.dataset.programSummary = '';
        const badges = element('div', 'program-badges'); badges.dataset.programBadges = '';
        const identityActions = element('div', 'program-identity-actions');
        identityActions.append(button('Assign Portfolio', 'assign-portfolio', true),
            button('Edit Program', 'edit', true), button('Freeze snapshot', 'snapshot'));
        identity.append(identityCopy, badges, identityActions);

        const metrics = element('dl', 'program-metrics'); metrics.dataset.programMetrics = '';
        const health = element('section', 'program-health'); health.dataset.programHealth = '';
        const delivery = element('section', 'program-delivery-workbench');
        const deliveryHeader = element('header');
        const deliveryCopy = element('div');
        deliveryCopy.append(element('span', 'workspace-section-kicker', 'Program delivery'),
            element('h3', '', 'From scientific intent to completed work'),
            element('p', '', 'Plan once, then move the same canonical Work Item through Understand, Design, Decide, Make, and Test & Learn.'));
        deliveryHeader.append(deliveryCopy, button('Plan a task', 'work'));
        const workSummary = element('dl', 'program-work-summary'); workSummary.dataset.programWorkSummary = '';
        const workflowBoard = element('div', 'program-workflow-board'); workflowBoard.dataset.programWorkflowBoard = '';
        for (const [lane, label, intent] of [
            ['understand', 'Understand', 'Target, evidence, structure'],
            ['design', 'Design', 'Ideas and molecular proposals'],
            ['decide', 'Decide', 'Portfolio and priority'],
            ['make', 'Make', 'Routes, batches, samples'],
            ['test_learn', 'Test & Learn', 'Experiments and decisions'],
        ]) {
            const column = element('section', 'program-workflow-lane'); column.dataset.lane = lane;
            const laneHeader = element('header');
            laneHeader.append(element('div', '', ''), element('strong', 'program-workflow-count', '0'));
            laneHeader.firstElementChild!.append(element('h4', '', label), element('p', '', intent));
            const tasks = element('div', 'program-workflow-tasks'); tasks.dataset.programWorkLane = lane;
            column.append(laneHeader, tasks); workflowBoard.append(column);
        }
        const gantt = element('section', 'program-gantt');
        const ganttHeader = element('header');
        ganttHeader.append(element('div', '', ''), element('div', 'program-gantt-legend', ''));
        ganttHeader.firstElementChild!.append(element('span', 'workspace-section-kicker', 'Schedule'),
            element('h4', '', 'Program Gantt'),
            element('p', '', 'Only explicitly planned dates are drawn. Dependencies and overdue work stay visible.'));
        ganttHeader.lastElementChild!.append(element('span', '', 'Active'), element('span', '', 'Blocked'), element('span', '', 'Done'));
        const ganttBody = element('div', 'program-gantt-body'); ganttBody.dataset.programGantt = '';
        gantt.append(ganttHeader, ganttBody);
        delivery.append(deliveryHeader, workSummary, workflowBoard, gantt);
        const referenceJobs = element('section', 'program-reference-jobs');
        const referenceHeader = element('header');
        const referenceCopy = element('div');
        referenceCopy.append(element('span', 'workspace-section-kicker', 'Native reference jobs'),
            element('h3', '', 'One Program · one identity chain · one delivery spine'),
            element('p', '', 'Each operation writes a canonical object, provenance-bearing event, or governed relationship. Records remain the same objects in every workspace.'));
        const referenceCount = element('strong', 'program-reference-count', '0 records');
        referenceCount.dataset.programReferenceCount = '';
        referenceHeader.append(referenceCopy, referenceCount);
        const referenceGrid = element('div', 'program-reference-grid');
        const referenceFamily = (id: string, label: string, purpose: string,
            actions: Array<[string, string]>) => {
            const section = element('article', 'program-reference-family');
            section.dataset.referenceFamily = id;
            const heading = element('header');
            const copy = element('div'); copy.append(element('h4', '', label), element('p', '', purpose));
            const controls = element('div', 'program-reference-actions');
            for (const [action, actionLabel] of actions) controls.append(button(actionLabel, `reference:${action}`, true));
            heading.append(copy, controls);
            const list = element('div', 'program-reference-list'); list.dataset.referenceList = id;
            section.append(heading, list); return section;
        };
        referenceGrid.append(
            referenceFamily('identity', '1 · Scope & identity', 'Target–disease scope, reviewed substance identity, batch-derived samples, and custody.', [
                ['target-disease', 'Link disease'], ['substance', 'Register substance'],
                ['sample', 'Create sample'], ['sample-transfer', 'Transfer sample'],
            ]),
            referenceFamily('delivery', '2 · Team delivery', 'The same Work Item carries discussion, files, execution, and criterion-level readiness.', [
                ['work-comment', 'Add comment'], ['work-attachment', 'Attach file'], ['gate-criterion', 'Assess criterion'],
            ]),
            referenceFamily('data', '3 · Experimental data', 'Immutable protocols, physical samples, experiments, and lineage-bearing dataset versions.', [
                ['protocol', 'Version protocol'], ['experiment', 'Record experiment'], ['dataset', 'Commit dataset'],
            ]),
            referenceFamily('structure', '4 · Structure collaboration', 'Experimental observations, annotations, review authority, and preserved analysis state.', [
                ['observation', 'Register observation'], ['annotation', 'Annotate'],
                ['review', 'Review'], ['analysis-snapshot', 'Preserve analysis'],
            ]),
            referenceFamily('evidence', '5 · External evidence', 'Release-pinned imports and explainable target–disease evidence records.', [
                ['evidence-release', 'Import release'], ['external-evidence', 'Record evidence'],
            ]),
        );
        referenceJobs.append(referenceHeader, referenceGrid);
        const grid = element('div', 'program-atom-grid');
        const panel = (kind: string, label: string, singular: string, collection: string, help: string) => {
            const section = element('section', 'program-atom-panel');
            const heading = element('header');
            const copy = element('div'); copy.append(element('h3', '', label), element('p', '', help));
            heading.append(copy, button(`Record ${singular}`, kind, true));
            const list = element('div', 'program-atom-list'); list.dataset.programCollection = collection;
            section.append(heading, list); return section;
        };
        grid.append(panel('objective', 'Objectives', 'Objective', 'objectives', 'Explicit success conditions and thresholds.'),
            panel('hypothesis', 'Hypotheses', 'Hypothesis', 'hypotheses', 'Testable beliefs with falsification criteria.'),
            panel('milestone', 'Milestones', 'Milestone', 'milestones', 'Evidence-bearing stage gates and delivery criteria.'),
            panel('decision', 'Decisions', 'Decision', 'decisions', 'Actor-attributed choices, outcomes, and alternatives.'));
        const operating = element('div', 'program-operating-grid');
        operating.append(panel('member', 'Team & roles', 'Member', 'members', 'Explicit scientific responsibility and review authority.'),
            panel('gate', 'Stage gates', 'Gate', 'stage_gates', 'Evidence-backed readiness criteria and approval decisions.'),
            panel('evidence', 'Evidence graph', 'Evidence edge', 'evidence_bindings', 'Claims linked to canonical evidence without copying it.'),
            panel('lineage', 'Entity lineage', 'Lineage edge', 'lineage', 'One compound identity across form, batch, sample and result.'));
        const timeline = element('section', 'program-timeline');
        const timelineHeader = element('header');
        timelineHeader.append(element('div', '', ''), button('Link object', 'link', true));
        timelineHeader.firstElementChild!.append(element('h3', '', 'Program timeline'),
            element('p', '', 'Ordered aggregate events; newest first.'));
        const events = element('ol'); events.dataset.programEvents = '';
        timeline.append(timelineHeader, events);
        dashboard.append(identity, health, metrics, delivery, referenceJobs, operating, grid, timeline);
        workbench.append(toolbar, status, empty, dashboard);
        page.append(header, question, workbench);
        this.host.replaceChildren(page);
        queueMicrotask(() => document.dispatchEvent(new CustomEvent('dirac:refresh-program')));
    }

    private stateStrip(state: ReturnType<typeof deriveViewState>): HTMLElement {
        const strip = element('dl', 'workspace-state-strip');
        const item = (label: string, value: string) => {
            const group = element('div');
            group.append(element('dt', '', label), element('dd', '', value.replace('-', ' ')));
            group.dataset.state = value;
            return group;
        };
        strip.append(item('Delivery', state.delivery), item('Runtime', state.runtime),
            item('Evidence', state.evidence));
        return strip;
    }

    private openSourceDialog(definition: ViewDefinition, programId?: string): void {
        const dialog = element('dialog', 'workspace-source-dialog') as HTMLDialogElement;
        const form = element('form') as HTMLFormElement;
        form.method = 'dialog';
        const title = element('h2', '', 'Select a canonical source object');
        const help = element('p', '', 'This adds an object reference to scientific context. It does not claim that observations are loaded.');
        const kindLabel = element('label');
        kindLabel.append(element('span', '', 'Object kind'));
        const kind = element('select') as HTMLSelectElement;
        for (const item of OBJECT_KINDS) kind.add(new Option(item.replace(/_/g, ' '), item));
        const preferred = definition.acceptedContext[0] || VIEW_PLANS[definition.id]?.plannedInputs[0] || 'program';
        kind.value = preferred;
        kindLabel.append(kind);
        const idLabel = element('label');
        idLabel.append(element('span', '', 'Canonical ID'));
        const id = element('input') as HTMLInputElement;
        id.required = true; id.autocomplete = 'off'; id.placeholder = 'e.g. CMP-1042';
        idLabel.append(id);
        const actions = element('div', 'workspace-source-actions');
        const cancel = element('button', 'workspace-visual-related', 'Cancel');
        cancel.type = 'button'; cancel.addEventListener('click', () => dialog.close());
        const select = element('button', 'workspace-visual-action', 'Use this object');
        select.type = 'submit';
        actions.append(cancel, select);
        form.append(title, help, kindLabel, idLabel, actions);
        form.addEventListener('submit', event => {
            event.preventDefault();
            const value = id.value.trim();
            if (!value) return;
            const ref: ObjectRef = { kind: kind.value as ObjectKind, id: value };
            const patch: Parameters<typeof scientificContext.patch>[0] = {
                focusedObject: ref, selectedObjects: [ref], origin: 'selection',
                ...(ref.kind === 'program' ? { programRef: ref as ObjectRef<'program'> } : {}),
                ...(ref.kind === 'complex' ? { complexRef: ref as ObjectRef<'complex'> } : {}),
                ...(ref.kind === 'target' ? { targetRef: ref as ObjectRef<'target'> } : {}),
                ...(ref.kind === 'campaign' ? { campaignRef: ref as ObjectRef<'campaign'> } : {}),
                ...(ref.kind === 'series' ? { seriesRef: ref as ObjectRef<'series'> } : {}),
                ...(ref.kind === 'hypothesis'
                    ? { activeHypotheses: [ref as ObjectRef<'hypothesis'>] } : {}),
            };
            scientificContext.patch(patch);
            dialog.close();
            dialog.remove();
            this.navigate({ workspace: definition.workspace, view: definition.id,
                programId: programId || scientificContext.current().programRef?.id || 'current' });
        });
        dialog.append(form);
        document.body.append(dialog);
        dialog.addEventListener('close', () => dialog.remove(), { once: true });
        dialog.showModal(); id.focus();
    }

    private moduleCard(module: ExperienceModule, index: number): HTMLElement {
        const card = element('article', 'workspace-module-card');
        card.dataset.readiness = module.readiness;
        const top = element('div', 'workspace-module-card-top');
        top.append(element('span', 'workspace-module-index', String(index).padStart(2, '0')),
            element('span', 'workspace-module-readiness', module.readiness));
        card.append(top, element('h3', '', module.title), element('p', '', module.purpose));
        const boundary = element('div', 'workspace-module-boundary');
        boundary.append(element('span', '', module.readiness === 'available' ? 'Connected now'
            : module.readiness === 'foundation' ? 'Data contract ready' : 'Not connected'));
        card.append(boundary);
        return card;
    }
}
