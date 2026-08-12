import type { ShellRoute } from './app-shell';
import { navigableViews, VIEWS, WORKSPACES, type ViewDefinition } from './registries';
import { VIEW_EXPERIENCES, WORKSPACE_NARRATIVES, type ExperienceModule } from './workspace-catalog';
import { renderWorkspaceVisual } from './workspace-visuals';

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

function writeLocal(key: string, value: string): void {
    try {
        if (value) localStorage.setItem(key, value);
        else localStorage.removeItem(key);
    } catch { /* A private browser may refuse storage; the shell remains fully navigable. */ }
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
                this.search?.focus();
            }
        });
    }

    render(route: ShellRoute): void {
        const definition = VIEWS.find(view => view.id === route.view);
        const workspace = WORKSPACES.find(item => item.id === route.workspace);
        if (!definition || !workspace) return;
        const scaffold = !definition.implemented;
        const app = document.getElementById('app');
        app?.classList.toggle('shell-scaffold', scaffold);
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
        this.renderCanvas(definition, route.programId);
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
            const button = element('button', 'workspace-outline-view');
            button.type = 'button';
            button.dataset.active = String(view.id === active.id);
            if (view.id === active.id) button.setAttribute('aria-current', 'page');
            const name = element('span', '', view.label);
            const status = element('small', view.implemented ? 'is-live' : 'is-preview',
                view.implemented ? 'Ready' : 'Preview');
            button.append(name, status);
            button.addEventListener('click', () => this.navigate({
                workspace: view.workspace, view: view.id, programId: programId || 'current',
            }));
            list.append(button);
        }
        this.outline.replaceChildren(heading, narrative, label, list);
    }

    private renderCanvas(definition: ViewDefinition, programId?: string): void {
        const experience = VIEW_EXPERIENCES[definition.id];
        if (!experience) throw new Error(`${definition.id}: missing View experience`);
        const workspace = WORKSPACES.find(item => item.id === definition.workspace)!;

        const page = element('article', 'workspace-page');
        page.dataset.workspace = workspace.id;
        const header = element('header', 'workspace-page-header');
        const titleBlock = element('div', 'workspace-page-title');
        const eyebrow = element('div', 'workspace-page-eyebrow');
        eyebrow.append(element('span', 'workspace-page-icon', workspace.icon),
            element('span', '', `${workspace.label} workspace`),
            element('span', 'workspace-page-separator', '·'),
            element('span', 'workspace-page-state', 'Preview · no data selected'));
        titleBlock.append(eyebrow, element('h1', '', definition.label),
            element('p', '', experience.summary));

        const target = experience.liveTarget ? VIEWS.find(view => view.id === experience.liveTarget) : undefined;
        const targetWorkspace = target ? WORKSPACES.find(item => item.id === target.workspace) : undefined;
        const openTarget = target ? () => this.navigate({
            workspace: target.workspace, view: target.id, programId: programId || 'current',
        }) : undefined;
        const actionLabel = target && targetWorkspace ? `Continue in ${targetWorkspace.label} · ${target.label}` : undefined;
        header.append(titleBlock);

        const question = element('section', 'workspace-question');
        question.append(element('span', '', 'The human question'),
            element('blockquote', '', experience.question));
        const visual = renderWorkspaceVisual(definition, experience, { actionLabel, onAction: openTarget });

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
        moduleGrid.setAttribute('aria-live', 'polite');
        const renderModules = () => {
            if (definition.id !== this.activeViewId) return;
            const query = search.value.trim().toLowerCase();
            const modules = experience.modules.filter(module =>
                `${module.title} ${module.purpose}`.toLowerCase().includes(query));
            moduleGrid.replaceChildren(...modules.map((module, index) =>
                this.moduleCard(module, index + 1)));
            if (!modules.length) moduleGrid.append(element('p', 'workspace-no-results',
                'No module matches this filter.'));
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
        textarea.placeholder = 'Capture the first workflow, decision, or data source to connect…';
        textarea.value = readLocal(storageKey('note', definition.id));
        const saved = element('span', 'workspace-note-saved', textarea.value ? 'Saved locally' : 'Not saved');
        let saveTimer: ReturnType<typeof setTimeout> | undefined;
        textarea.addEventListener('input', () => {
            saved.textContent = 'Saving…';
            if (saveTimer) clearTimeout(saveTimer);
            saveTimer = setTimeout(() => {
                writeLocal(storageKey('note', definition.id), textarea.value.trim());
                saved.textContent = textarea.value.trim() ? 'Saved locally' : 'Not saved';
            }, 250);
        });
        notebook.append(textarea, saved);
        lower.append(milestone, notebook);

        const readiness = element('details', 'workspace-readiness');
        const readinessSummary = element('summary');
        readinessSummary.append(element('strong', '', 'Product readiness'),
            element('span', '', 'Planned modules, connection sequence, and private note'));
        readiness.append(readinessSummary, toolbar, moduleGrid, lower);

        page.append(header, question, visual, readiness);
        this.host.replaceChildren(page);
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
