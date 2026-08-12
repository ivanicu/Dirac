import type { ShellRoute } from './app-shell';
import { navigableViews, VIEWS, WORKSPACES, type ViewDefinition } from './registries';
import { VIEW_EXPERIENCES, WORKSPACE_NARRATIVES, type ExperienceModule } from './workspace-catalog';
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

    constructor(private readonly host: HTMLElement, private readonly outline: HTMLElement,
                private readonly breadcrumb: HTMLElement, private readonly navigate: Navigate) {
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
        app?.classList.toggle('shell-scaffold', scaffold);
        app?.classList.toggle('shell-connected', scaffold && connected);
        // A scene View is the established Dirac scientific workbench. Workspace
        // routing may select its modules, but must not insert a second product
        // shell above the workbench or restyle its chrome.
        app?.classList.toggle('scene-workbench', !scaffold);
        if (app) app.dataset.workspace = route.workspace;
        this.breadcrumb.hidden = !scaffold;
        this.breadcrumb.textContent = scaffold ? `${workspace.label}  /  ${definition.label}` : '';
        this.host.hidden = !scaffold;
        this.outline.hidden = !scaffold;
        if (!scaffold) {
            this.host.replaceChildren();
            this.outline.replaceChildren();
            this.activeViewId = '';
            return;
        }
        this.activeViewId = definition.id;
        this.renderOutline(definition, route.programId);
        if (connected) this.renderConnectedCanvas(definition, route.programId);
        else this.renderCanvas(definition, route.programId);
    }

    private renderOutline(active: ViewDefinition, programId?: string): void {
        const workspace = WORKSPACES.find(item => item.id === active.workspace)!;
        const views = navigableViews(active.workspace);
        const heading = element('div', 'workspace-outline-heading');
        const icon = element('span', 'workspace-outline-icon', workspace.icon);
        const copy = element('div');
        copy.append(element('span', 'workspace-outline-kicker', 'Workspace'),
            element('h2', '', workspace.label));
        heading.append(icon, copy);
        const narrative = element('p', 'workspace-outline-copy', WORKSPACE_NARRATIVES[workspace.id]);
        const label = element('span', 'workspace-outline-label', 'Views');
        const list = element('nav', 'workspace-outline-list');
        list.setAttribute('aria-label', `${workspace.label} view map`);
        for (const view of views) {
            const button = element('a', 'workspace-outline-view');
            button.href = view.route.replace(':programId', encodeURIComponent(programId || 'current'));
            button.dataset.active = String(view.id === active.id);
            if (view.id === active.id) button.setAttribute('aria-current', 'page');
            const name = element('span', '', view.label);
            const status = element('small', view.implemented ? 'is-live' : 'is-preview',
                view.implemented ? 'Connected' : 'Preview');
            button.append(name, status);
            button.addEventListener('click', event => {
                if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                event.preventDefault();
                this.navigate({
                workspace: view.workspace, view: view.id, programId: programId || 'current',
                });
            });
            list.append(button);
        }
        this.outline.replaceChildren(heading, narrative, label, list);
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

        page.append(header, question, visual, readiness);
        this.host.replaceChildren(page);
    }

    private renderConnectedCanvas(definition: ViewDefinition, programId?: string): void {
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
        page.append(header, question, runs);
        this.host.replaceChildren(page);
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
