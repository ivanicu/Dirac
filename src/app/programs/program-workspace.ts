import { OBJECT_KINDS, type ObjectRef } from '../generated/commands';
import { scientificContext } from '../context/scientific-context-store';
import { DiracClient } from '../services/dirac-client';
import { renderLaneLoadChart, renderProgramGantt, renderScientificGraph,
    toWorkVisualItems, workGraphModel, type WorkLane,
    type WorkVisualItem } from '../visualization/scientific-visuals';

type Program = Record<string, any> & { ref: ObjectRef<'program'>; version: number };
type FieldOption = string | { value: string; label: string };
type Field = { name: string; label: string; value?: string; required?: boolean;
    multiline?: boolean; options?: readonly FieldOption[]; placeholder?: string;
    multiple?: boolean; readonly?: boolean; type?: 'text' | 'date' | 'number' };

class ProgramPrerequisiteError extends Error {}

const text = (tag: keyof HTMLElementTagNameMap, value: string, className = '') => {
    const node = document.createElement(tag);
    node.textContent = value; if (className) node.className = className;
    return node;
};

const humanize = (value: unknown) => String(value ?? '—').replace(/_/g, ' ');
const WORKFLOW_LANES = [
    ['understand', 'Understand'], ['design', 'Design'], ['decide', 'Decide'],
    ['make', 'Make'], ['test_learn', 'Test & Learn'],
] as const;
const CLOSED_WORK = new Set(['done', 'cancelled']);

const REFERENCE_FAMILIES: Record<string, readonly string[]> = {
    identity: ['target_disease', 'substance_registration', 'batch', 'sample', 'sample_transfer'],
    delivery: ['work_comment', 'work_attachment', 'gate_criterion'],
    data: ['protocol_version', 'experiment', 'dataset_version'],
    structure: ['structure_observation', 'annotation', 'review', 'analysis_snapshot'],
    evidence: ['evidence_release', 'external_evidence'],
};

export class ProgramWorkspaceController {
    private current?: Program;
    private installed = false;
    private ganttWrites: Promise<void> = Promise.resolve();

    constructor(private readonly client: DiracClient,
        private readonly selectProgram: (programId: string) => void) {}

    install(): void {
        if (this.installed) return;
        this.installed = true;
        document.addEventListener('dirac:refresh-program', () => void this.refresh());
        document.addEventListener('click', event => {
            const button = (event.target as Element | null)?.closest<HTMLButtonElement>('[data-program-action]');
            if (!button) return;
            void this.action(button.dataset.programAction || '').catch(error => this.status(
                error instanceof Error ? error.message : String(error),
                error instanceof ProgramPrerequisiteError ? 'needs-context' : 'error'));
        });
        document.addEventListener('change', event => {
            const workItem = (event.target as Element | null)?.closest<HTMLSelectElement>('#context-work-item');
            if (workItem?.value) {
                const ref = { kind: 'work_item' as const, id: workItem.value };
                scientificContext.patch({ workItemRef: ref, origin: 'selection' });
                return;
            }
            const select = (event.target as Element | null)?.closest<HTMLSelectElement>('[data-program-select]');
            if (!select?.value) return;
            this.selectProgram(select.value);
        });
    }

    async refresh(): Promise<void> {
        const root = document.querySelector<HTMLElement>('.program-workspace');
        const workflowRoots = document.querySelectorAll<HTMLElement>('[data-workflow-lane]');
        const globalWork = document.querySelector<HTMLSelectElement>('#context-work-item');
        if (!root && !workflowRoots.length && !globalWork) return;
        if (root) this.status('Loading durable Program state…', 'loading');
        try {
            const listEnvelope = await this.client.execute('program.list', { limit: 200 });
            if (!listEnvelope.ok) throw new Error(listEnvelope.error?.user_message
                || listEnvelope.error?.message || 'program.list refused');
            const programs = (listEnvelope.data?.programs || []) as Program[];
            const selector = root?.querySelector<HTMLSelectElement>('[data-program-select]');
            if (selector) {
                selector.replaceChildren(new Option(programs.length ? 'Select a Program' : 'No Programs yet', ''));
                for (const program of programs) selector.add(new Option(
                    `${program.code} · ${program.name}`, program.ref.id));
            }
            const routeId = document.querySelector<HTMLElement>('.program-page')?.dataset.programId;
            const contextId = scientificContext.current().programRef?.id;
            const requested = routeId && routeId !== 'current' ? routeId : contextId;
            const activeId = requested && programs.some(item => item.ref.id === requested)
                ? requested : programs[0]?.ref.id;
            if (!activeId) {
                this.current = undefined;
                if (root) {
                    this.showEmpty(true);
                    this.status('No Program exists yet. Create the project fact root before entering Design.', 'empty');
                }
                this.renderWorkflow(undefined);
                this.renderGlobalWork(undefined);
                this.publish(undefined);
                return;
            }
            if (selector) selector.value = activeId;
            const envelope = await this.client.execute('program.get', {
                program_ref: { kind: 'program', id: activeId },
            });
            if (!envelope.ok) throw new Error(envelope.error?.user_message
                || envelope.error?.message || 'program.get refused');
            this.current = envelope.data?.program as Program;
            scientificContext.patch({ programRef: this.current.ref, origin: 'restore' });
            const globalProgram = document.getElementById('context-program');
            if (globalProgram) {
                globalProgram.dataset.ref = this.current.ref.id;
                globalProgram.textContent = this.current.code || this.current.ref.id;
                globalProgram.title = `${this.current.name || this.current.code} · ${this.current.ref.id}`;
            }
            if (root) { this.render(this.current); this.showEmpty(false); }
            this.status(`Version ${this.current.version} · durable PostgreSQL aggregate · ${this.current.events?.length || 0} recent events`, 'ready');
            this.renderWorkflow(this.current);
            this.renderGlobalWork(this.current);
            this.publish(this.current);
        } catch (error) {
            this.current = undefined; this.showEmpty(true);
            this.status(error instanceof Error ? error.message : String(error), 'error');
            this.renderWorkflow(undefined, error instanceof Error ? error.message : String(error));
            this.renderGlobalWork(undefined);
            this.publish(undefined, error instanceof Error ? error.message : String(error));
        }
    }

    private publish(program?: Program, error?: string): void {
        document.dispatchEvent(new CustomEvent('dirac:program-state', {
            detail: { program, error },
        }));
    }

    private renderGlobalWork(program?: Program): void {
        const select = document.querySelector<HTMLSelectElement>('#context-work-item');
        if (!select) return;
        const workspace = document.getElementById('app')?.dataset.workspace || '';
        const lane = ({ structures: 'understand', design: 'design', campaigns: 'decide',
            synthesis: 'make', experiments: 'test_learn' } as Record<string, string>)[workspace];
        const items = ((program?.work_items || []) as Array<Record<string, any>>);
        const previous = scientificContext.current().workItemRef?.id || select.value;
        select.replaceChildren(new Option('No Work Item selected', ''));
        for (const item of items) select.add(new Option(
            `${humanize(item.lane)} · ${item.key} · ${item.title}`, item.ref.id));
        const preferred = items.find(item => item.ref.id === previous)
            || items.find(item => item.lane === lane) || items[0];
        if (preferred) {
            select.value = preferred.ref.id;
            if (scientificContext.current().workItemRef?.id !== preferred.ref.id) {
                scientificContext.patch({ workItemRef: preferred.ref, origin: 'selection' });
            }
        }
        select.title = lane
            ? `Current unique Program Work Item; ${humanize(lane)} tasks are suggested but context is never replaced`
            : 'Current unique Program Work Item across all stages';
    }

    private renderWorkflow(program?: Program, failure?: string): void {
        for (const root of document.querySelectorAll<HTMLElement>('[data-workflow-lane]')) {
            const lane = root.dataset.workflowLane;
            const status = root.querySelector<HTMLElement>('.workspace-workflow-status');
            const list = root.querySelector<HTMLElement>('[data-workflow-items]');
            if (!list || !status) continue;
            list.replaceChildren();
            if (failure) { status.textContent = failure; continue; }
            const allItems = (program?.work_items || []) as Array<Record<string, any>>;
            const selectedId = scientificContext.current().workItemRef?.id;
            const selected = allItems.find(item => item.ref.id === selectedId);
            const items = selected ? [selected] : allItems.filter(item => item.lane === lane);
            status.textContent = program && selected
                ? `The same unique Work Item is carried from ${humanize(selected.lane)} into ${humanize(lane)} · Program ${program.code}`
                : program
                ? `${items.length} unique Work Item${items.length === 1 ? '' : 's'} in this stage · Program ${program.code}`
                : 'Select or create a Program to route work through this stage.';
            for (const item of items) {
                const card = document.createElement('article'); card.className = 'workspace-workflow-item';
                card.append(text('strong', item.title), text('span', `${humanize(item.lane)} → ${humanize(lane)} · ${item.ref.id} · ${humanize(item.status)}`),
                    text('span', `${item.executions?.length || 0} execution job(s) · canonical`));
                list.append(card);
            }
            if (program && !items.length) list.append(text('p', 'No Work Item is currently in this stage.', 'program-atom-empty'));
        }
    }

    private render(program: Program): void {
        const title = document.querySelector<HTMLElement>('[data-program-title]');
        const summary = document.querySelector<HTMLElement>('[data-program-summary]');
        const badges = document.querySelector<HTMLElement>('[data-program-badges]');
        if (title) title.textContent = `${program.code} · ${program.name}`;
        if (summary) summary.textContent = program.summary || 'No Program summary recorded yet.';
        if (badges) {
            badges.replaceChildren();
            for (const value of [program.lifecycle, program.stage, `v${program.version}`,
                program.portfolio_ref ? `portfolio · ${program.portfolio_ref.id}` : 'portfolio · unassigned',
                program.target_ref ? `target · ${program.target_ref.id}` : 'target · unassigned']) {
                badges.append(text('span', humanize(value)));
            }
        }
        const metrics = document.querySelector<HTMLElement>('[data-program-metrics]');
        if (metrics) {
            metrics.replaceChildren();
            const labels: Array<[string, string]> = [
                ['Objectives', 'objectives'], ['Hypotheses', 'hypotheses'],
                ['Decisions', 'decisions'], ['Milestones', 'milestones'], ['Team', 'members'],
                ['Stage gates', 'stage_gates'], ['Work items', 'work_items'], ['Executions', 'work_executions'], ['Evidence', 'evidence_bindings'],
                ['Lineage', 'lineage'], ['Linked objects', 'links'], ['Native jobs', 'reference_jobs'],
            ];
            for (const [label, key] of labels) {
                const item = document.createElement('div');
                item.append(text('dt', label), text('dd', String(program.counts?.[key] ?? 0)));
                metrics.append(item);
            }
        }
        const referenceRecords = (program.reference_jobs || []) as Array<Record<string, any>>;
        const referenceCount = document.querySelector<HTMLElement>('[data-program-reference-count]');
        if (referenceCount) referenceCount.textContent = `${referenceRecords.length} governed record${referenceRecords.length === 1 ? '' : 's'}`;
        for (const [family, kinds] of Object.entries(REFERENCE_FAMILIES)) {
            const list = document.querySelector<HTMLElement>(`[data-reference-list="${family}"]`);
            if (!list) continue;
            list.replaceChildren();
            const records = referenceRecords.filter(record => kinds.includes(record.job_kind));
            for (const record of records.slice(0, 6)) {
                const item = document.createElement('article'); item.className = 'program-reference-record';
                const identity = record.ref ? `${record.ref.kind} · ${record.ref.id}` : 'canonical record';
                const detail = record.name || record.title || record.sample_code || record.protocol_key
                    || record.experiment_key || record.dataset_key || record.observation_key
                    || record.source_record_id || record.release_name || record.label || record.body
                    || record.explanation || record.reason || identity;
                item.append(text('strong', humanize(record.job_kind)), text('span', String(detail)),
                    text('small', identity));
                list.append(item);
            }
            if (!records.length) list.append(text('p', 'No governed records yet.', 'program-atom-empty'));
        }
        for (const collection of ['objectives', 'hypotheses', 'milestones', 'decisions', 'members',
            'stage_gates', 'work_items', 'work_packages', 'work_executions', 'evidence_bindings', 'lineage']) {
            const list = document.querySelector<HTMLElement>(`[data-program-collection="${collection}"]`);
            if (!list) continue;
            list.replaceChildren();
            const atoms = (program[collection] || []) as Array<Record<string, any>>;
            if (!atoms.length) {
                const empty = text('p', 'Nothing recorded yet.', 'program-atom-empty');
                list.append(empty); continue;
            }
            for (const atom of atoms.slice(0, 8)) {
                const card = document.createElement('article'); card.className = 'program-atom';
                const heading = document.createElement('div');
                const edge = atom.source_ref ? `${atom.source_ref.kind} → ${atom.target_ref.kind}`
                    : atom.subject_ref ? `${atom.subject_ref.kind} ${atom.relation} ${atom.evidence_ref.kind}` : '';
                const principal = atom.principal ? `${atom.principal.id} · ${humanize(atom.role)}` : '';
                heading.append(text('strong', atom.title || atom.action || principal || edge || atom.key || atom.claim || 'Record'),
                    text('span', atom.lane ? `${humanize(atom.lane)} · ${humanize(atom.status)}`
                        : atom.revision ? `${atom.key || 'record'} · r${atom.revision}` : humanize(atom.status || atom.relation || 'current')));
                const detail = atom.statement || atom.rationale || atom.description || atom.outcome
                    || atom.responsibility || atom.evidence_summary || atom.claim || edge || '';
                const actor = atom.created_by?.id || atom.assigned_by?.id || atom.attached_by?.id || 'system';
                const at = atom.created_at || atom.assigned_at || atom.attached_at || '';
                card.append(heading, text('p', detail), text('small', `${humanize(atom.status || 'current')} · ${humanize(actor)} · ${String(at).slice(0, 10)}`));
                if (collection === 'work_items') {
                    const actions = document.createElement('div'); actions.className = 'program-atom-actions';
                    const move = text('button', 'Move stage') as HTMLButtonElement; move.type = 'button';
                    move.dataset.programAction = `move-work:${atom.ref.id}`;
                    const attach = text('button', 'Attach execution') as HTMLButtonElement; attach.type = 'button';
                    attach.dataset.programAction = `attach-job:${atom.ref.id}`;
                    actions.append(move, attach); card.append(actions);
                }
                list.append(card);
            }
        }
        const health = document.querySelector<HTMLElement>('[data-program-health]');
        if (health) {
            const state = program.health || { score: 0, status: 'at_risk', risks: [] };
            health.dataset.status = state.status; health.replaceChildren();
            const score = document.createElement('div'); score.className = 'program-health-score';
            score.append(text('strong', `${state.score}/100`), text('span', `Operational health · ${humanize(state.status)}`));
            const risks = document.createElement('div'); risks.className = 'program-health-risks';
            for (const risk of state.risks || []) risks.append(text('span', risk.action));
            if (!risks.children.length) risks.append(text('span', 'All declared readiness checks are satisfied.'));
            health.append(score, risks);
        }
        this.renderWorkManagement(program);
        const events = document.querySelector<HTMLOListElement>('[data-program-events]');
        if (events) {
            events.replaceChildren();
            for (const event of (program.events || []) as Array<Record<string, any>>) {
                const item = document.createElement('li');
                item.append(text('span', `v${event.program_version}`),
                    text('strong', humanize(event.kind)),
                    text('small', `${humanize(event.actor?.id)} · ${new Date(event.occurred_at).toLocaleString()}`));
                events.append(item);
            }
            if (!events.children.length) events.append(text('li', 'No events recorded.'));
        }
    }

    private renderWorkManagement(program: Program): void {
        const items = [...((program.work_items || []) as Array<Record<string, any>>)]
            .sort((a, b) => (a.priority || 9) - (b.priority || 9)
                || String(a.due_on || '9999').localeCompare(String(b.due_on || '9999')));
        const today = new Date(); today.setHours(0, 0, 0, 0);
        const isOverdue = (item: Record<string, any>) => item.due_on && !CLOSED_WORK.has(item.status)
            && new Date(`${item.due_on}T00:00:00`) < today;
        const summary = document.querySelector<HTMLElement>('[data-program-work-summary]');
        if (summary) {
            summary.replaceChildren();
            for (const [label, value, state] of [
                ['All work', items.length, 'all'],
                ['In progress', items.filter(item => item.status === 'active').length, 'active'],
                ['Blocked', items.filter(item => item.status === 'blocked').length, 'blocked'],
                ['Overdue', items.filter(isOverdue).length, 'overdue'],
                ['Complete', items.filter(item => item.status === 'done').length, 'done'],
            ] as Array<[string, number, string]>) {
                const metric = document.createElement('div'); metric.dataset.state = state;
                metric.append(text('dt', label), text('dd', String(value))); summary.append(metric);
            }
        }
        const keyById = new Map(items.map(item => [item.ref.id, item.key]));
        for (const [lane, label] of WORKFLOW_LANES) {
            const list = document.querySelector<HTMLElement>(`[data-program-work-lane="${lane}"]`);
            if (!list) continue;
            list.replaceChildren();
            const laneItems = items.filter(item => item.lane === lane);
            const count = list.closest('.program-workflow-lane')?.querySelector<HTMLElement>('.program-workflow-count');
            if (count) count.textContent = String(laneItems.length);
            for (const item of laneItems) {
                const card = document.createElement('article'); card.className = 'program-task-card';
                card.dataset.workItemId = item.ref.id;
                card.dataset.status = item.status; if (isOverdue(item)) card.dataset.overdue = 'true';
                const top = document.createElement('div'); top.className = 'program-task-card-top';
                top.append(text('span', item.key, 'program-task-key'), text('span', `P${item.priority || 3}`, 'program-task-priority'));
                const dependencies = (item.depends_on_refs || []) as ObjectRef[];
                const dates = item.start_on && item.due_on ? `${item.start_on} → ${item.due_on}`
                    : item.due_on ? `Finish ${item.due_on} · start not planned` : 'Not scheduled';
                const owner = item.owner?.id || 'Unassigned';
                const meta = document.createElement('div'); meta.className = 'program-task-meta';
                meta.append(text('span', humanize(item.status)), text('span', owner), text('span', dates));
                const dependencyLine = text('p', dependencies.length
                    ? `After ${dependencies.map(ref => keyById.get(ref.id) || ref.id).join(', ')}`
                    : `No dependencies · starts in ${label}`, 'program-task-dependencies');
                const actions = document.createElement('div'); actions.className = 'program-task-actions';
                const edit = text('button', 'Edit plan') as HTMLButtonElement; edit.type = 'button';
                edit.dataset.programAction = `edit-work:${item.ref.id}`;
                const move = text('button', 'Move stage') as HTMLButtonElement; move.type = 'button';
                move.dataset.programAction = `move-work:${item.ref.id}`;
                actions.append(edit, move);
                card.append(top, text('h5', item.title), meta, dependencyLine, actions); list.append(card);
            }
            if (!laneItems.length) list.append(text('p', 'No work in this stage.', 'program-workflow-empty'));
        }
        const visualItems = toWorkVisualItems(items, (program.work_packages || []) as Array<Record<string, any>>);
        const graph = document.querySelector<HTMLElement>('[data-program-work-graph]');
        if (graph) renderScientificGraph(graph, workGraphModel(visualItems), {
            ariaLabel: 'Program work dependency graph, arranged by discovery stage',
            onSelect: node => this.selectWorkItem(node.id),
            storageKey: `${program.ref.id}:work`,
        });
        const chart = document.querySelector<HTMLElement>('[data-program-work-chart]');
        if (chart) renderLaneLoadChart(chart, visualItems, lane => {
            this.scrollToLane(lane);
            graph?.dispatchEvent(new CustomEvent('dirac:graph-filter', { detail: { kind: lane } }));
        }, `${program.ref.id}:work`);
        this.renderGantt(visualItems);
    }

    private renderGantt(items: readonly WorkVisualItem[]): void {
        const root = document.querySelector<HTMLElement>('[data-program-gantt]');
        if (!root) return;
        renderProgramGantt(root, items, {
            onSelect: id => this.selectWorkItem(id), onEdit: id => this.workPackage(id),
            onScheduleChange: (id, start, end) => this.queueGanttWrite(id, { start_on: start, due_on: end }),
            onProgressChange: (id, progress) => this.queueGanttWrite(id, { progress_percent: progress }),
            storageKey: this.current?.ref.id,
        });
    }

    private queueGanttWrite(id: string, patch: Record<string, unknown>): void {
        this.ganttWrites = this.ganttWrites.then(async () => {
            const item = (this.current?.work_items || []).find((candidate: any) => candidate.ref.id === id);
            if (!item || !this.current) throw new Error('The selected Program Work Item is no longer current.');
            const spec = item.current_package || item;
            this.status('Saving the direct schedule edit…', 'loading');
            await this.execute('program.work_package.record', {
                program_ref: this.current.ref, expected_version: this.current.version,
                work_package: {
                    key: item.key, title: item.title, description: spec.description,
                    lane: item.lane, status: item.status, priority: item.priority,
                    owner: item.owner, start_on: item.start_on, due_on: item.due_on,
                    progress_percent: item.progress_percent || 0,
                    depends_on_refs: item.depends_on_refs || [], deliverable_refs: spec.deliverable_refs || [],
                    ...patch,
                },
            }, 'Schedule edit saved as a new Program Work Package revision.');
        }).catch(async error => {
            this.status(error instanceof Error ? error.message : String(error), 'error');
            await this.refresh();
        });
    }

    private selectWorkItem(id: string): void {
        scientificContext.patch({ workItemRef: { kind: 'work_item', id }, origin: 'selection' });
        for (const card of document.querySelectorAll<HTMLElement>('[data-work-item-id]')) {
            card.toggleAttribute('data-selected', card.dataset.workItemId === id);
        }
        document.querySelector<HTMLElement>(`[data-work-item-id="${CSS.escape(id)}"]`)
            ?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
    }

    private scrollToLane(lane: WorkLane): void {
        document.querySelector<HTMLElement>(`.program-workflow-lane[data-lane="${lane}"]`)
            ?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    }

    private async action(action: string): Promise<void> {
        if (action === 'refresh') return this.refresh();
        if (action === 'create') return this.create();
        if (action === 'create-portfolio') return this.createPortfolio();
        if (!this.current) return;
        if (action === 'snapshot') {
            await this.execute('program.snapshot.create', {
                program_ref: this.current.ref, expected_version: this.current.version,
            }, 'Program snapshot frozen with a content digest.');
            return;
        }
        if (action === 'edit') return this.edit();
        if (action === 'objective') return this.objective();
        if (action === 'hypothesis') return this.hypothesis();
        if (action === 'decision') return this.decision();
        if (action === 'milestone') return this.milestone();
        if (action === 'assign-portfolio') return this.assignPortfolio();
        if (action === 'member') return this.member();
        if (action === 'gate') return this.gate();
        if (action === 'work') return this.workPackage();
        if (action.startsWith('edit-work:')) return this.workPackage(action.slice('edit-work:'.length));
        if (action.startsWith('reference:')) return this.referenceJob(action.slice('reference:'.length));
        if (action.startsWith('move-work:')) return this.moveWork(action.slice('move-work:'.length));
        if (action.startsWith('attach-job:')) return this.attachJob(action.slice('attach-job:'.length));
        if (action === 'evidence') return this.evidence();
        if (action === 'lineage') return this.lineage();
        if (action === 'link') return this.link();
    }

    private create(): void {
        this.form('Create Program', [
            { name: 'code', label: 'Program code', required: true, placeholder: 'MOR-PAM' },
            { name: 'name', label: 'Program name', required: true },
            { name: 'summary', label: 'Scientific intent', multiline: true },
            { name: 'indication', label: 'Indication' }, { name: 'modality', label: 'Modality' },
            { name: 'stage', label: 'Discovery stage', options: ['discovery', 'target_validation', 'hit_discovery', 'hit_to_lead', 'lead_optimization', 'candidate_selection', 'preclinical'], value: 'discovery' },
        ], async values => {
            const envelope = await this.command('program.create', { program: values });
            const id = (envelope.data?.program as Program).ref.id;
            this.selectProgram(id);
        });
    }

    private createPortfolio(): void {
        this.form('Create Portfolio', [
            { name: 'code', label: 'Portfolio code', required: true, placeholder: 'NEURO' },
            { name: 'name', label: 'Portfolio name', required: true },
            { name: 'mandate', label: 'Investment mandate', multiline: true },
        ], async values => {
            await this.command('portfolio.create', { portfolio: values });
            this.status('Portfolio created. It can now govern one or more Programs.', 'ready');
        });
    }

    private async assignPortfolio(): Promise<void> {
        const envelope = await this.command('portfolio.list', { limit: 200 });
        const portfolios = (envelope.data?.portfolios || []) as Array<Record<string, any>>;
        if (!portfolios.length) throw new Error('Create a Portfolio before assigning this Program.');
        this.form('Assign Portfolio', [{ name: 'portfolio_id', label: 'Portfolio', required: true,
            options: portfolios.map(item => item.ref.id) }], values => this.execute('program.portfolio.assign', {
            program_ref: this.current!.ref, expected_version: this.current!.version,
            portfolio_ref: { kind: 'portfolio', id: values.portfolio_id },
        }, 'Program assigned to its canonical Portfolio.'));
    }

    private edit(): void {
        const p = this.current!;
        this.form('Edit Program', [
            { name: 'name', label: 'Program name', required: true, value: p.name },
            { name: 'summary', label: 'Scientific intent', multiline: true, value: p.summary || '' },
            { name: 'indication', label: 'Indication', value: p.indication || '' },
            { name: 'modality', label: 'Modality', value: p.modality || '' },
            { name: 'owner_id', label: 'Owner', value: p.owner_id || '' },
            { name: 'lifecycle', label: 'Lifecycle', options: ['draft', 'active', 'paused', 'completed', 'archived'], value: p.lifecycle },
            { name: 'stage', label: 'Discovery stage', options: ['discovery', 'target_validation', 'hit_discovery', 'hit_to_lead', 'lead_optimization', 'candidate_selection', 'preclinical'], value: p.stage },
        ], values => this.execute('program.update', {
            program_ref: p.ref, expected_version: p.version, patch: values,
        }, 'Program context updated.'));
    }

    private objective(): void {
        this.form('Record Objective', [
            { name: 'key', label: 'Stable key', required: true, placeholder: 'cell-potency' },
            { name: 'title', label: 'Objective', required: true },
            { name: 'rationale', label: 'Why this matters', required: true, multiline: true },
            { name: 'category', label: 'Category', options: ['efficacy', 'selectivity', 'developability', 'safety', 'synthesis', 'evidence'] },
            { name: 'metric', label: 'Metric', required: true, placeholder: 'EC50' },
            { name: 'direction', label: 'Direction', options: ['at_most', 'at_least', 'maximize', 'minimize', 'within', 'qualitative'] },
            { name: 'value', label: 'Threshold value', required: true }, { name: 'unit', label: 'Unit', required: true },
            { name: 'hardness', label: 'Constraint', options: ['hard', 'soft'] },
        ], values => {
            const { value, unit, ...objective } = values;
            return this.atom('objective', { ...objective, threshold: { value, unit } });
        });
    }

    private hypothesis(): void {
        this.form('Record Hypothesis', [
            { name: 'key', label: 'Stable key', required: true }, { name: 'title', label: 'Hypothesis title', required: true },
            { name: 'statement', label: 'Testable statement', required: true, multiline: true },
            { name: 'falsification_criterion', label: 'What would falsify it?', required: true, multiline: true },
            { name: 'confidence', label: 'Confidence 0–1', required: true, value: '0.5' },
        ], values => this.atom('hypothesis', { ...values, confidence: Number(values.confidence) }));
    }

    private decision(): void {
        this.form('Record Decision', [
            { name: 'key', label: 'Stable key', required: true },
            { name: 'type', label: 'Decision type', options: ['scientific', 'portfolio', 'stage_gate', 'scope', 'resource', 'risk'] },
            { name: 'action', label: 'Action', required: true }, { name: 'outcome', label: 'Outcome', required: true },
            { name: 'rationale', label: 'Rationale and evidence boundary', required: true, multiline: true },
            { name: 'alternatives', label: 'Alternatives considered (one per line)', multiline: true },
        ], values => this.atom('decision', { ...values,
            alternatives: values.alternatives.split('\n').map(value => value.trim()).filter(Boolean) }));
    }

    private milestone(): void {
        this.form('Record Milestone', [
            { name: 'key', label: 'Stable key', required: true }, { name: 'title', label: 'Milestone', required: true },
            { name: 'description', label: 'Purpose', multiline: true },
            { name: 'target_date', label: 'Target date', placeholder: 'YYYY-MM-DD' },
            { name: 'criteria', label: 'Acceptance criteria (one per line)', required: true, multiline: true },
        ], values => this.atom('milestone', { ...values,
            target_date: values.target_date || undefined,
            criteria: values.criteria.split('\n').map(value => value.trim()).filter(Boolean) }));
    }

    private member(): void {
        this.form('Assign Program Member', [
            { name: 'principal_id', label: 'Person or agent ID', required: true },
            { name: 'principal_kind', label: 'Principal kind', options: ['human', 'agent', 'service'] },
            { name: 'role', label: 'Program role', options: ['program_lead', 'medicinal_chemistry', 'computational_chemistry', 'biology', 'dmpk', 'toxicology', 'synthesis', 'data_science', 'operations', 'reviewer', 'observer'] },
            { name: 'responsibility', label: 'Explicit responsibility', multiline: true },
        ], values => this.execute('program.member.assign', {
            program_ref: this.current!.ref, expected_version: this.current!.version,
            member: { principal: { kind: values.principal_kind, id: values.principal_id },
                role: values.role, responsibility: values.responsibility || undefined },
        }, 'Program responsibility assigned.'));
    }

    private gate(): void {
        const stage = this.current!.stage;
        this.form('Record Stage Gate', [
            { name: 'key', label: 'Stable gate key', required: true, value: stage },
            { name: 'stage', label: 'Discovery stage', options: ['discovery', 'target_validation', 'hit_discovery', 'hit_to_lead', 'lead_optimization', 'candidate_selection', 'preclinical'], value: stage },
            { name: 'title', label: 'Gate title', required: true },
            { name: 'criteria', label: 'Readiness criteria (one per line)', required: true, multiline: true },
            { name: 'status', label: 'Assessment state', options: ['planned', 'ready'] },
            { name: 'target_date', label: 'Target date', placeholder: 'YYYY-MM-DD' },
            { name: 'evidence_summary', label: 'Evidence boundary', multiline: true },
        ], values => this.execute('program.stage_gate.record', {
            program_ref: this.current!.ref, expected_version: this.current!.version,
            stage_gate: { ...values, target_date: values.target_date || undefined,
                criteria: values.criteria.split('\n').map(value => value.trim()).filter(Boolean) },
        }, 'Stage gate recorded as a versioned readiness contract.'));
    }

    private workPackage(workItemId?: string): void {
        const item = workItemId
            ? (this.current!.work_items || []).find((candidate: any) => candidate.ref.id === workItemId)
            : undefined;
        const spec = item?.current_package || item || {};
        const dependencyOptions = ((this.current!.work_items || []) as Array<Record<string, any>>)
            .filter(candidate => candidate.ref.id !== workItemId)
            .map(candidate => ({ value: candidate.ref.id, label: `${candidate.key} · ${candidate.title}` }));
        const selectedDependencies = (item?.depends_on_refs || []).map((ref: ObjectRef) => ref.id).join('\n');
        this.form(item ? 'Edit planned task' : 'Plan a task', [
            { name: 'title', label: 'Task outcome', required: true, value: item?.title || '',
                placeholder: 'e.g. Confirm cellular potency' },
            { name: 'key', label: item ? 'Stable task key' : 'Task key (generated if blank)',
                value: item?.key || '', readonly: !!item, placeholder: 'confirm-cell-potency' },
            { name: 'description', label: 'Definition of done', required: true, multiline: true,
                value: spec.description || '', placeholder: 'What evidence or deliverable makes this complete?' },
            { name: 'lane', label: 'Discovery stage', options: WORKFLOW_LANES.map(([value, label]) => ({ value, label })),
                value: item?.lane || 'understand', readonly: !!item },
            { name: 'status', label: 'Work status', options: ['backlog', 'ready', 'active', 'blocked', 'done', 'cancelled'],
                value: item?.status || 'backlog' },
            { name: 'priority', label: 'Priority', type: 'number', value: String(item?.priority || 3), required: true },
            { name: 'progress_percent', label: 'Completion (%)', type: 'number',
                value: String(item?.progress_percent || 0), required: true },
            { name: 'owner_id', label: 'Owner', value: item?.owner?.id || '', placeholder: 'person or agent ID' },
            { name: 'start_on', label: 'Planned start', type: 'date', value: item?.start_on || '' },
            { name: 'due_on', label: 'Planned finish', type: 'date', value: item?.due_on || '' },
            { name: 'depends_on', label: 'Dependencies', options: dependencyOptions, multiple: true,
                value: selectedDependencies },
        ], values => {
            const key = values.key || this.taskKey(values.title);
            if (values.start_on && values.due_on && values.start_on > values.due_on) {
                throw new Error('Planned finish must be on or after planned start.');
            }
            return this.execute('program.work_package.record', {
                program_ref: this.current!.ref, expected_version: this.current!.version,
                work_package: { key, title: values.title, description: values.description,
                    lane: values.lane, status: values.status, priority: Number(values.priority),
                    progress_percent: Math.max(0, Math.min(100, Number(values.progress_percent))),
                    start_on: values.start_on || undefined, due_on: values.due_on || undefined,
                    depends_on_refs: this.lines(values.depends_on || '').map(id => ({ kind: 'work_item', id })),
                    owner: values.owner_id ? { kind: 'human', id: values.owner_id } : undefined },
            }, item ? 'Task plan revised without changing its identity.' : 'Task planned as one canonical Work Item.');
        });
    }

    private taskKey(title: string): string {
        const key = title.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 64);
        const suffix = crypto.randomUUID().slice(0, 6);
        return `${key || 'task'}-${suffix}`.slice(0, 72);
    }

    private moveWork(workItemId: string): void {
        const workItem = (this.current!.work_items || []).find((item: any) => item.ref.id === workItemId);
        this.form('Move Work Item', [
            { name: 'to_lane', label: 'Destination', options: WORKFLOW_LANES
                .filter(([value]) => value !== workItem.lane).map(([value, label]) => ({ value, label })) },
            { name: 'reason', label: 'Why it is ready to move', required: true, multiline: true },
        ], values => this.execute('program.work_item.transition', {
            program_ref: this.current!.ref, expected_version: this.current!.version,
            transition: { work_item_ref: workItem.ref, to_lane: values.to_lane, reason: values.reason },
        }, 'The same Work Item moved to its next workflow lane.'));
    }

    private attachJob(workItemId: string): void {
        const workItem = (this.current!.work_items || []).find((item: any) => item.ref.id === workItemId);
        this.form('Attach Runtime Job', [
            { name: 'job_id', label: 'Existing Job ID', required: true },
            { name: 'purpose', label: 'Purpose of this execution', multiline: true },
        ], values => this.execute('program.work_execution.attach', {
            program_ref: this.current!.ref, expected_version: this.current!.version,
            execution: { work_item_ref: workItem.ref, job_ref: { kind: 'job', id: values.job_id },
                purpose: values.purpose || undefined },
        }, 'Runtime Job attached to this unique Work Item.'));
    }

    private evidence(): void {
        const subjects = ['program', 'objective', 'hypothesis', 'decision', 'milestone', 'stage_gate', 'work_item', 'work_package'];
        this.form('Attach Canonical Evidence', [
            { name: 'subject_kind', label: 'Subject kind', options: subjects },
            { name: 'subject_id', label: 'Subject canonical ID', required: true, value: this.current!.ref.id },
            { name: 'relation', label: 'Relationship', options: ['supports', 'contradicts', 'tests', 'explains'] },
            { name: 'evidence_kind', label: 'Evidence kind', options: ['evidence', 'measurement', 'dataset', 'artifact', 'literature_reference', 'prediction', 'complex', 'pose', 'field', 'batch', 'sample'] },
            { name: 'evidence_id', label: 'Evidence canonical ID', required: true },
            { name: 'claim', label: 'Claim supported or challenged', required: true, multiline: true },
            { name: 'strength', label: 'Strength 0–1', value: '0.5' },
        ], values => this.execute('program.evidence.attach', {
            program_ref: this.current!.ref, expected_version: this.current!.version,
            binding: { subject_ref: { kind: values.subject_kind, id: values.subject_id }, relation: values.relation,
                evidence_ref: { kind: values.evidence_kind, id: values.evidence_id }, claim: values.claim,
                strength: Number(values.strength) },
        }, 'Evidence linked to the canonical subject.'));
    }

    private lineage(): void {
        this.form('Record Canonical Entity Lineage', [
            { name: 'shape', label: 'Relationship', options: ['compound|has_form|compound_form', 'compound_form|produced_as|batch', 'sample|sampled_from|batch', 'sample|formulated_as|formulation', 'batch|released_by|quality_release', 'sample|assayed_under|protocol', 'sample|has_measurement|measurement'] },
            { name: 'source_id', label: 'Source canonical ID', required: true },
            { name: 'target_id', label: 'Target canonical ID', required: true },
        ], values => {
            const [sourceKind, relation, targetKind] = values.shape.split('|');
            return this.execute('program.lineage.record', {
                program_ref: this.current!.ref, expected_version: this.current!.version,
                lineage: { source_ref: { kind: sourceKind, id: values.source_id }, relation,
                    target_ref: { kind: targetKind, id: values.target_id } },
            }, 'Canonical entity lineage recorded without duplicating either object.');
        });
    }

    private link(): void {
        this.form('Link canonical object', [
            { name: 'kind', label: 'Object kind', options: OBJECT_KINDS.filter(kind => kind !== 'program') },
            { name: 'id', label: 'Existing canonical entity ID', required: true }, { name: 'role', label: 'Role', required: true, placeholder: 'primary-campaign' },
            { name: 'rationale', label: 'Why it belongs to this Program', multiline: true },
        ], values => this.execute('program.link', {
            program_ref: this.current!.ref, expected_version: this.current!.version,
            object_ref: { kind: values.kind, id: values.id }, role: values.role,
            rationale: values.rationale || undefined,
        }, 'Object linked to Program context.'));
    }

    private referenceJob(action: string): void {
        const program = this.current!;
        const run = async (command: string, record: Record<string, unknown>, success: string) => {
            const envelope = await this.command(command, {
                program_ref: program.ref, expected_version: program.version, record,
            });
            const ref = envelope.data?.record?.ref as ObjectRef | undefined;
            if (ref) scientificContext.patch({
                focusedObject: ref, selectedObjects: [ref], origin: 'command',
                ...(ref.kind === 'compound' ? { compoundRef: ref as ObjectRef<'compound'> } : {}),
                ...(ref.kind === 'sample' ? { sampleRef: ref as ObjectRef<'sample'> } : {}),
                ...(ref.kind === 'experiment' ? { experimentRef: ref as ObjectRef<'experiment'> } : {}),
                ...(ref.kind === 'dataset_version'
                    ? { datasetVersionRef: ref as ObjectRef<'dataset_version'> } : {}),
            });
            this.status(success, 'ready'); await this.refresh();
        };
        const workItems = (program.work_items || []) as Array<Record<string, any>>;
        const gates = (program.stage_gates || []) as Array<Record<string, any>>;
        const references = (program.reference_jobs || []) as Array<Record<string, any>>;
        const refs = (kind: string) => references.filter(item => item.job_kind === kind)
            .map(item => String(item.ref?.id || '')).filter(Boolean);
        const requiredOptions = (values: string[], what: string) => {
            if (!values.length) throw new ProgramPrerequisiteError(
                `Record ${what} first; this operation will not invent a source object.`);
            return values;
        };
        if (action === 'target-disease') {
            if (!program.target_ref) throw new ProgramPrerequisiteError(
                'Assign the Program target before linking a disease.');
            return this.form('Link Target–Disease Scope', [
                { name: 'disease_key', label: 'Stable disease key', required: true },
                { name: 'name', label: 'Disease name', required: true },
                { name: 'ontology_namespace', label: 'Ontology namespace', placeholder: 'EFO' },
                { name: 'ontology_id', label: 'Ontology ID', placeholder: 'EFO_0000001' },
                { name: 'role', label: 'Role', options: ['primary', 'secondary', 'safety', 'biomarker', 'exploratory'] },
                { name: 'rationale', label: 'Why this target–disease pair is in scope', required: true, multiline: true },
                { name: 'description', label: 'Disease scope note', multiline: true },
            ], values => run('program.target_disease.link', {
                disease_key: values.disease_key, name: values.name, description: values.description || undefined,
                ontology: values.ontology_namespace && values.ontology_id
                    ? { namespace: values.ontology_namespace, id: values.ontology_id } : undefined,
                target_ref: program.target_ref, role: values.role, rationale: values.rationale,
            }, 'Target–disease scope linked to this Program.'));
        }
        if (action === 'substance') return this.form('Register Substance Identity', [
            { name: 'compound_id', label: 'Canonical compound ID', required: true,
                value: scientificContext.current().compoundRef?.id || '' },
            { name: 'status', label: 'Review state', options: ['draft', 'candidate_match', 'conflict', 'validated', 'approved', 'rejected'] },
            { name: 'definition', label: 'Registration definition (JSON)', required: true, multiline: true, value: '{\n  "parent": ""\n}' },
            { name: 'validation', label: 'Validation findings (JSON)', multiline: true, value: '{}' },
            { name: 'decision', label: 'Human identity decision', multiline: true },
        ], values => run('identity.substance_registration.record', {
            compound_ref: { kind: 'compound', id: values.compound_id }, status: values.status,
            definition: this.parseJson(values.definition, 'Registration definition'),
            validation: this.parseJson(values.validation || '{}', 'Validation findings'), decision: values.decision || undefined,
        }, 'Substance identity revision recorded; the compound itself remains canonical.'));
        if (action === 'batch') return this.form('Register Material Batch', [
            { name: 'compound_id', label: 'Canonical compound', required: true,
                value: scientificContext.current().compoundRef?.id || '' },
            { name: 'batch_code', label: 'Batch code', required: true },
            { name: 'form_kind', label: 'Chemical form', options: ['neutral', 'free_base', 'free_acid', 'salt', 'hydrate', 'solvate', 'cocrystal', 'mixture'] },
            { name: 'provenance', label: 'Provenance', options: ['internal_synthesis', 'purchase', 'gift', 'literature_only', 'virtual'] },
            { name: 'purity_pct', label: 'Purity %' },
            { name: 'purity_method', label: 'Purity method', options: ['', 'hplc_uv', 'lcms', 'nmr', 'elemental', 'qnmr', 'supplier_coa'] },
            { name: 'amount_mg', label: 'Amount (mg)' }, { name: 'supplier', label: 'Supplier' },
            { name: 'synthesized_on', label: 'Synthesized on', type: 'date' },
            { name: 'label', label: 'Form label' },
        ], values => run('material.batch.register', {
            compound_ref: { kind: 'compound', id: values.compound_id }, batch_code: values.batch_code,
            form_kind: values.form_kind, provenance: values.provenance,
            purity_pct: values.purity_pct ? Number(values.purity_pct) : undefined,
            purity_method: values.purity_method || undefined,
            amount_mg: values.amount_mg ? Number(values.amount_mg) : undefined,
            supplier: values.supplier || undefined, synthesized_on: values.synthesized_on || undefined,
            label: values.label || undefined,
        }, 'Canonical form and material batch registered.'));
        if (action === 'sample') return this.form('Create Physical Sample', [
            { name: 'sample_code', label: 'Sample code', required: true },
            { name: 'batch_id', label: 'Source batch ID', required: true,
                value: scientificContext.current().focusedObject?.kind === 'batch'
                    ? scientificContext.current().focusedObject?.id : '' },
            { name: 'parent_sample_id', label: 'Parent sample ID (aliquot only)' },
            { name: 'amount_value', label: 'Amount', required: true },
            { name: 'amount_unit', label: 'Unit', options: ['mg', 'g', 'ug', 'mmol', 'umol', 'mol'] },
            { name: 'container', label: 'Container' }, { name: 'location', label: 'Initial location' },
        ], values => run('sample.create', {
            sample_code: values.sample_code, batch_ref: { kind: 'batch', id: values.batch_id },
            parent_sample_ref: values.parent_sample_id ? { kind: 'sample', id: values.parent_sample_id } : undefined,
            amount_value: Number(values.amount_value), amount_unit: values.amount_unit,
            container: values.container || undefined, location: values.location || undefined,
        }, 'Canonical batch-derived sample created with its first custody event.'));
        if (action === 'sample-transfer') return this.form('Transfer Sample Custody', [
            { name: 'sample_id', label: 'Sample', required: true, options: requiredOptions(refs('sample'), 'a sample') },
            { name: 'to_location', label: 'Destination', required: true },
            { name: 'reason', label: 'Transfer reason', required: true, multiline: true },
        ], values => run('sample.transfer', { sample_ref: { kind: 'sample', id: values.sample_id },
            to_location: values.to_location, reason: values.reason }, 'Sample custody moved without changing sample identity.'));
        if (action === 'work-comment' || action === 'work-attachment') {
            const options = requiredOptions(workItems.map(item => item.ref.id), 'a Work Item');
            if (action === 'work-comment') return this.form('Add Work Item Comment', [
                { name: 'work_item_id', label: 'Work Item', options, required: true },
                { name: 'body', label: 'Decision-relevant comment', required: true, multiline: true },
            ], values => run('program.work_comment.record', { work_item_ref: { kind: 'work_item', id: values.work_item_id },
                body: values.body }, 'Comment attached to the stable Work Item.'));
            return this.form('Attach Evidence to Work Item', [
                { name: 'work_item_id', label: 'Work Item', options, required: true },
                { name: 'artifact_id', label: 'Existing artifact ID', required: true },
                { name: 'role', label: 'Attachment role', required: true, placeholder: 'source-evidence' },
            ], values => run('program.work_attachment.record', { work_item_ref: { kind: 'work_item', id: values.work_item_id },
                artifact_ref: { kind: 'artifact', id: values.artifact_id }, role: values.role },
            'Artifact attached to the stable Work Item.'));
        }
        if (action === 'gate-criterion') return this.form('Assess One Gate Criterion', [
            { name: 'gate_id', label: 'Stage Gate', required: true, options: requiredOptions(gates.map(item => item.ref.id), 'a Stage Gate') },
            { name: 'criterion_key', label: 'Declared criterion text/key', required: true },
            { name: 'status', label: 'Assessment', options: ['unknown', 'not_met', 'met', 'waived'] },
            { name: 'evidence_kind', label: 'Evidence kind', options: ['artifact', 'evidence', 'measurement', 'dataset_version', 'sample'] },
            { name: 'evidence_id', label: 'Evidence ID (required when met)' },
            { name: 'explanation', label: 'Evidence boundary or waiver reason', required: true, multiline: true },
        ], values => run('program.gate_criterion.assess', {
            stage_gate_ref: { kind: 'stage_gate', id: values.gate_id }, criterion_key: values.criterion_key,
            status: values.status, evidence_ref: values.evidence_id ? { kind: values.evidence_kind, id: values.evidence_id } : undefined,
            explanation: values.explanation,
        }, 'Criterion assessed independently; the gate remains an explicit decision.'));
        if (action === 'protocol') return this.form('Version Experimental Protocol', [
            { name: 'protocol_key', label: 'Stable protocol key', required: true },
            { name: 'title', label: 'Protocol title', required: true }, { name: 'assay_id', label: 'Canonical assay ID' },
            { name: 'specification', label: 'Immutable specification (JSON)', required: true, multiline: true, value: '{}' },
        ], values => run('protocol.version.record', { protocol_key: values.protocol_key, title: values.title,
            assay_ref: values.assay_id ? { kind: 'assay', id: values.assay_id } : undefined,
            specification: this.parseJson(values.specification, 'Protocol specification') },
        'Immutable protocol version recorded.'));
        if (action === 'experiment') return this.form('Record Experiment', [
            { name: 'experiment_key', label: 'Stable experiment key', required: true },
            { name: 'title', label: 'Experiment title', required: true },
            { name: 'work_item_id', label: 'Work Item', required: true,
                options: requiredOptions(workItems.map(item => item.ref.id), 'a Work Item'),
                value: scientificContext.current().workItemRef?.id || '' },
            { name: 'protocol_id', label: 'Protocol version', required: true, options: requiredOptions(refs('protocol_version'), 'a protocol version') },
            { name: 'sample_ids', label: 'Sample IDs (one per line)', multiline: true },
            { name: 'status', label: 'Status', options: ['planned', 'running', 'completed', 'failed', 'cancelled'] },
            { name: 'started_at', label: 'Started at (ISO-8601)' }, { name: 'completed_at', label: 'Completed at (ISO-8601)' },
        ], values => run('experiment.record', {
            experiment_key: values.experiment_key, title: values.title,
            work_item_ref: { kind: 'work_item', id: values.work_item_id },
            protocol_version_ref: { kind: 'protocol_version', id: values.protocol_id }, status: values.status,
            started_at: values.started_at || undefined, completed_at: values.completed_at || undefined,
            samples: this.lines(values.sample_ids).map(id => ({ sample_ref: { kind: 'sample', id }, role: 'test' })),
        }, 'Experiment linked to its Work Item, protocol version, and physical samples.'));
        if (action === 'dataset') return this.form('Commit Dataset Version', [
            { name: 'dataset_key', label: 'Stable dataset key', required: true },
            { name: 'manifest_artifact_id', label: 'Manifest artifact ID', required: true },
            { name: 'manifest', label: 'Manifest (JSON)', required: true, multiline: true, value: '{}' },
            { name: 'schema_version', label: 'Schema version', required: true, value: '1' },
            { name: 'access_scope', label: 'Access scope', options: ['program', 'internal', 'public', 'partner_confidential', 'restricted', 'regulated'] },
            { name: 'experiment_id', label: 'Experiment ID' },
            { name: 'parent_ids', label: 'Parent dataset version IDs (one per line)', multiline: true },
            { name: 'derivation', label: 'Derivation note', multiline: true },
        ], values => run('dataset.version.commit', {
            dataset_key: values.dataset_key, manifest_artifact_ref: { kind: 'artifact', id: values.manifest_artifact_id },
            manifest: this.parseJson(values.manifest, 'Dataset manifest'), schema_version: values.schema_version,
            access_scope: values.access_scope, experiment_ref: values.experiment_id ? { kind: 'experiment', id: values.experiment_id } : undefined,
            parent_refs: this.lines(values.parent_ids).map(id => ({ kind: 'dataset_version', id })), derivation: values.derivation || undefined,
        }, 'Dataset version committed with manifest, access scope, and lineage.'));
        if (action === 'observation') return this.form('Register Structure Observation', [
            { name: 'observation_key', label: 'Stable observation key', required: true },
            { name: 'structure_id', label: 'Protein structure ID', required: true },
            { name: 'dataset_id', label: 'Source dataset version', required: true, options: requiredOptions(refs('dataset_version'), 'a dataset version') },
            { name: 'compound_id', label: 'Canonical compound ID' }, { name: 'experiment_id', label: 'Experiment ID' },
            { name: 'canonical_site', label: 'Canonical binding-site label' },
        ], values => run('structure.observation.register', { observation_key: values.observation_key,
            structure_ref: { kind: 'protein_structure', id: values.structure_id },
            dataset_version_ref: { kind: 'dataset_version', id: values.dataset_id },
            compound_ref: values.compound_id ? { kind: 'compound', id: values.compound_id } : undefined,
            experiment_ref: values.experiment_id ? { kind: 'experiment', id: values.experiment_id } : undefined,
            canonical_site: values.canonical_site || undefined }, 'Experimental structure observation registered.'));
        if (action === 'annotation' || action === 'review') {
            const observationIds = requiredOptions(refs('structure_observation'), 'a structure observation');
            if (action === 'annotation') return this.form('Annotate Structure Observation', [
                { name: 'subject_id', label: 'Structure observation', required: true, options: observationIds },
                { name: 'annotation_kind', label: 'Annotation kind', options: ['site', 'tag', 'merge_hypothesis', 'note', 'quality'] },
                { name: 'label', label: 'Label', required: true },
                { name: 'value', label: 'Structured value (JSON)', multiline: true, value: '{}' },
            ], values => run('structure.annotation.record', { subject_ref: { kind: 'structure_observation', id: values.subject_id },
                annotation_kind: values.annotation_kind, label: values.label, value: this.parseJson(values.value, 'Annotation value') },
            'Annotation attached to the canonical observation.'));
            return this.form('Review Structure Observation', [
                { name: 'subject_id', label: 'Structure observation', required: true, options: observationIds },
                { name: 'review_role', label: 'Review authority', options: ['main', 'peer'] },
                { name: 'status', label: 'Review outcome', options: ['accepted', 'questionable', 'rejected'] },
                { name: 'comment', label: 'Review rationale', required: true, multiline: true },
            ], values => run('structure.review.record', { subject_ref: { kind: 'structure_observation', id: values.subject_id },
                review_role: values.review_role, status: values.status, comment: values.comment },
            'Scientific review recorded with explicit authority.'));
        }
        if (action === 'analysis-snapshot') return this.form('Preserve Analysis State', [
            { name: 'title', label: 'Snapshot title', required: true },
            { name: 'work_item_id', label: 'Work Item', options: workItems.map(item => item.ref.id) },
            { name: 'dataset_ids', label: 'Dataset versions (one per line)', required: true, multiline: true,
                value: refs('dataset_version').join('\n') },
            { name: 'state', label: 'Analysis state (JSON)', required: true, multiline: true, value: '{}' },
        ], values => run('structure.analysis_snapshot.create', { title: values.title, snapshot_mode: 'preserved',
            work_item_ref: values.work_item_id ? { kind: 'work_item', id: values.work_item_id } : undefined,
            dataset_version_refs: this.lines(values.dataset_ids).map(id => ({ kind: 'dataset_version', id })),
            state: this.parseJson(values.state, 'Analysis state') }, 'Preserved, reproducible analysis snapshot created.'));
        if (action === 'evidence-release') return this.form('Import External Evidence Release', [
            { name: 'source_name', label: 'Source', required: true, value: 'Open Targets' },
            { name: 'release_name', label: 'Pinned release name', required: true },
            { name: 'source_url', label: 'Source URL' }, { name: 'retrieved_at', label: 'Retrieved at (ISO-8601)', required: true },
            { name: 'artifact_id', label: 'Payload artifact ID', required: true },
        ], values => run('evidence.release.import', { source_name: values.source_name, release_name: values.release_name,
            source_url: values.source_url || undefined, retrieved_at: values.retrieved_at,
            payload_artifact_ref: { kind: 'artifact', id: values.artifact_id } }, 'External evidence release pinned to a verified artifact.'));
        if (action === 'external-evidence') {
            if (!program.target_ref) throw new ProgramPrerequisiteError(
                'Assign the Program target before importing target–disease evidence.');
            return this.form('Record Explainable External Evidence', [
                { name: 'release_id', label: 'Evidence release', required: true, options: requiredOptions(refs('evidence_release'), 'an evidence release') },
                { name: 'disease_id', label: 'Program disease', required: true, options: requiredOptions(refs('target_disease'), 'a target–disease link') },
                { name: 'source_record_id', label: 'Source record ID', required: true },
                { name: 'data_type', label: 'Data type', required: true, placeholder: 'genetic_association' },
                { name: 'evidence_source', label: 'Evidence source', required: true },
                { name: 'score', label: 'Source score 0–1 (optional)' },
                { name: 'payload', label: 'Source payload (JSON)', required: true, multiline: true, value: '{}' },
            ], values => run('evidence.external.record', {
                release_ref: { kind: 'external_evidence_release', id: values.release_id }, source_record_id: values.source_record_id,
                target_ref: program.target_ref, disease_ref: { kind: 'disease', id: values.disease_id },
                data_type: values.data_type, evidence_source: values.evidence_source,
                score: values.score === '' ? undefined : Number(values.score), is_direct: true,
                payload: this.parseJson(values.payload, 'Evidence payload'),
            }, 'Release-pinned evidence recorded; the UI can show counts and score range without inventing a composite truth score.'));
        }
        throw new Error(`Unknown native reference operation: ${action}`);
    }

    private parseJson(value: string, label: string): Record<string, unknown> {
        try {
            const parsed = JSON.parse(value);
            if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error();
            return parsed as Record<string, unknown>;
        } catch {
            throw new Error(`${label} must be a JSON object.`);
        }
    }

    private lines(value: string): string[] {
        return value.split('\n').map(item => item.trim()).filter(Boolean);
    }

    private atom(kind: 'objective' | 'hypothesis' | 'decision' | 'milestone', value: Record<string, unknown>) {
        return this.execute(`program.${kind}.record`, {
            program_ref: this.current!.ref, expected_version: this.current!.version, [kind]: value,
        }, `${humanize(kind)} recorded as a new immutable revision.`);
    }

    private async execute(command: string, input: Record<string, unknown>, success: string): Promise<void> {
        await this.command(command, input); this.status(success, 'ready'); await this.refresh();
    }

    private async command(command: string, input: Record<string, unknown>): Promise<Record<string, any>> {
        const envelope = await this.client.execute(command, input, { requestId: crypto.randomUUID() });
        if (!envelope.ok) throw new Error(envelope.error?.user_message || envelope.error?.message || `${command} refused`);
        return envelope as Record<string, any>;
    }

    private form(title: string, fields: Field[], submit: (values: Record<string, string>) => Promise<void>): void {
        const dialog = document.createElement('dialog'); dialog.className = 'program-dialog workspace-source-dialog';
        const form = document.createElement('form'); form.method = 'dialog';
        form.append(text('h2', title));
        const controls = document.createElement('div'); controls.className = 'program-dialog-fields';
        for (const field of fields) {
            const label = document.createElement('label'); label.append(text('span', field.label));
            let control: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
            if (field.options) {
                control = document.createElement('select');
                control.multiple = !!field.multiple;
                for (const option of field.options) {
                    const value = typeof option === 'string' ? option : option.value;
                    const label = typeof option === 'string' ? humanize(option) : option.label;
                    control.add(new Option(label, value));
                }
            } else if (field.multiline) control = document.createElement('textarea');
            else control = document.createElement('input');
            control.name = field.name; control.required = !!field.required;
            if (control instanceof HTMLSelectElement && control.multiple) {
                const selected = new Set((field.value || '').split('\n').filter(Boolean));
                for (const option of Array.from(control.options)) option.selected = selected.has(option.value);
            } else control.value = field.value || '';
            if (control instanceof HTMLInputElement) control.type = field.type || 'text';
            control.disabled = !!field.readonly;
            if (!(control instanceof HTMLSelectElement)) control.placeholder = field.placeholder || '';
            label.append(control); controls.append(label);
        }
        const error = text('p', '', 'program-dialog-error'); error.setAttribute('role', 'alert');
        const actions = document.createElement('div'); actions.className = 'workspace-source-actions';
        const cancel = text('button', 'Cancel', 'workspace-visual-related') as HTMLButtonElement;
        cancel.type = 'button'; cancel.addEventListener('click', () => dialog.close());
        const save = text('button', 'Save', 'workspace-visual-action') as HTMLButtonElement; save.type = 'submit';
        actions.append(cancel, save); form.append(controls, error, actions); dialog.append(form); document.body.append(dialog);
        form.addEventListener('submit', async event => {
            event.preventDefault(); save.disabled = true; error.textContent = '';
            try {
                const values: Record<string, string> = {};
                for (const field of fields) {
                    const control = form.elements.namedItem(field.name) as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement | null;
                    if (!control) continue;
                    if (control instanceof HTMLSelectElement && control.multiple) {
                        values[field.name] = Array.from(control.selectedOptions).map(option => option.value).join('\n');
                    } else values[field.name] = control.value;
                }
                await submit(values); dialog.close();
            } catch (caught) {
                error.textContent = caught instanceof Error ? caught.message : String(caught); save.disabled = false;
            }
        });
        dialog.addEventListener('close', () => dialog.remove(), { once: true });
        dialog.showModal(); dialog.querySelector<HTMLElement>('input,textarea,select')?.focus();
    }

    private showEmpty(empty: boolean): void {
        const emptyState = document.querySelector<HTMLElement>('[data-program-empty]');
        const dashboard = document.querySelector<HTMLElement>('[data-program-dashboard]');
        if (emptyState) emptyState.hidden = !empty;
        if (dashboard) dashboard.hidden = empty;
    }

    private status(message: string, state: string): void {
        const node = document.querySelector<HTMLElement>('[data-program-status]');
        if (node) { node.textContent = message; node.dataset.state = state; }
        const global = document.getElementById('status');
        if (global) global.textContent = state === 'ready'
            ? `Connected · Program v${this.current?.version ?? '—'} ready`
            : state === 'error' ? `Connected · Program error · ${message}`
                : state === 'needs-context' ? 'Connected · Program action needs context'
                : state === 'empty' ? 'Connected · Program context required'
                    : 'Connected · loading Program data';
        const runtime = state === 'ready' ? 'ready' : state === 'error' ? 'error'
            : state === 'empty' || state === 'needs-context' ? 'needs-context' : 'loading';
        const evidence = state === 'ready' ? 'provenance-backed' : 'none';
        for (const term of document.querySelectorAll<HTMLElement>('.workspace-state-strip dt')) {
            const description = term.parentElement?.querySelector<HTMLElement>('dd');
            if (!description) continue;
            if (term.textContent === 'Runtime') {
                term.parentElement!.dataset.state = runtime;
                description.textContent = humanize(runtime);
            }
            if (term.textContent === 'Evidence') {
                term.parentElement!.dataset.state = evidence;
                description.textContent = humanize(evidence);
            }
        }
    }
}
