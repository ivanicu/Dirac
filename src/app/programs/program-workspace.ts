import { OBJECT_KINDS, type ObjectRef } from '../generated/commands';
import { scientificContext } from '../context/scientific-context-store';
import { DiracClient } from '../services/dirac-client';

type Program = Record<string, any> & { ref: ObjectRef<'program'>; version: number };
type Field = { name: string; label: string; value?: string; required?: boolean;
    multiline?: boolean; options?: readonly string[]; placeholder?: string };

const text = (tag: keyof HTMLElementTagNameMap, value: string, className = '') => {
    const node = document.createElement(tag);
    node.textContent = value; if (className) node.className = className;
    return node;
};

const humanize = (value: unknown) => String(value ?? '—').replace(/_/g, ' ');

export class ProgramWorkspaceController {
    private current?: Program;
    private installed = false;

    constructor(private readonly client: DiracClient,
        private readonly selectProgram: (programId: string) => void) {}

    install(): void {
        if (this.installed) return;
        this.installed = true;
        document.addEventListener('dirac:refresh-program', () => void this.refresh());
        document.addEventListener('click', event => {
            const button = (event.target as Element | null)?.closest<HTMLButtonElement>('[data-program-action]');
            if (!button || !document.querySelector('.program-workspace')) return;
            void this.action(button.dataset.programAction || '');
        });
        document.addEventListener('change', event => {
            const select = (event.target as Element | null)?.closest<HTMLSelectElement>('[data-program-select]');
            if (!select?.value) return;
            this.selectProgram(select.value);
        });
    }

    async refresh(): Promise<void> {
        const root = document.querySelector<HTMLElement>('.program-workspace');
        if (!root) return;
        this.status('Loading durable Program state…', 'loading');
        try {
            const listEnvelope = await this.client.execute('program.list', { limit: 200 });
            if (!listEnvelope.ok) throw new Error(listEnvelope.error?.user_message
                || listEnvelope.error?.message || 'program.list refused');
            const programs = (listEnvelope.data?.programs || []) as Program[];
            const selector = root.querySelector<HTMLSelectElement>('[data-program-select]')!;
            selector.replaceChildren(new Option(programs.length ? 'Select a Program' : 'No Programs yet', ''));
            for (const program of programs) selector.add(new Option(
                `${program.code} · ${program.name}`, program.ref.id));
            const routeId = document.querySelector<HTMLElement>('.program-page')?.dataset.programId;
            const contextId = scientificContext.current().programRef?.id;
            const requested = routeId && routeId !== 'current' ? routeId : contextId;
            const activeId = requested && programs.some(item => item.ref.id === requested)
                ? requested : programs[0]?.ref.id;
            if (!activeId) {
                this.current = undefined; this.showEmpty(true);
                this.status('No Program exists yet. Create the project fact root before entering Design.', 'empty');
                return;
            }
            selector.value = activeId;
            const envelope = await this.client.execute('program.get', {
                program_ref: { kind: 'program', id: activeId },
            });
            if (!envelope.ok) throw new Error(envelope.error?.user_message
                || envelope.error?.message || 'program.get refused');
            this.current = envelope.data?.program as Program;
            scientificContext.patch({ programRef: this.current.ref,
                focusedObject: this.current.ref, origin: 'restore' });
            this.render(this.current); this.showEmpty(false);
            this.status(`Version ${this.current.version} · durable PostgreSQL aggregate · ${this.current.events?.length || 0} recent events`, 'ready');
        } catch (error) {
            this.current = undefined; this.showEmpty(true);
            this.status(error instanceof Error ? error.message : String(error), 'error');
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
                program.target_ref ? `target · ${program.target_ref.id}` : 'target · unassigned']) {
                badges.append(text('span', humanize(value)));
            }
        }
        const metrics = document.querySelector<HTMLElement>('[data-program-metrics]');
        if (metrics) {
            metrics.replaceChildren();
            const labels: Array<[string, string]> = [
                ['Objectives', 'objectives'], ['Hypotheses', 'hypotheses'],
                ['Decisions', 'decisions'], ['Milestones', 'milestones'], ['Linked objects', 'links'],
            ];
            for (const [label, key] of labels) {
                const item = document.createElement('div');
                item.append(text('dt', label), text('dd', String(program.counts?.[key] ?? 0)));
                metrics.append(item);
            }
        }
        for (const collection of ['objectives', 'hypotheses', 'milestones', 'decisions']) {
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
                heading.append(text('strong', atom.title || atom.action || atom.key || 'Untitled'),
                    text('span', `${atom.key || 'record'} · r${atom.revision || 1}`));
                const detail = atom.statement || atom.rationale || atom.description || atom.outcome || '';
                card.append(heading, text('p', detail), text('small', `${humanize(atom.status)} · ${humanize(atom.created_by?.id)} · ${String(atom.created_at || '').slice(0, 10)}`));
                list.append(card);
            }
        }
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

    private async action(action: string): Promise<void> {
        if (action === 'refresh') return this.refresh();
        if (action === 'create') return this.create();
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

    private link(): void {
        this.form('Link canonical object', [
            { name: 'kind', label: 'Object kind', options: OBJECT_KINDS.filter(kind => kind !== 'program') },
            { name: 'id', label: 'Canonical ID', required: true }, { name: 'role', label: 'Role', required: true, placeholder: 'primary-campaign' },
            { name: 'rationale', label: 'Why it belongs to this Program', multiline: true },
        ], values => this.execute('program.link', {
            program_ref: this.current!.ref, expected_version: this.current!.version,
            object_ref: { kind: values.kind, id: values.id }, role: values.role,
            rationale: values.rationale || undefined,
        }, 'Object linked to Program context.'));
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
                for (const option of field.options) control.add(new Option(humanize(option), option));
            } else if (field.multiline) control = document.createElement('textarea');
            else control = document.createElement('input');
            control.name = field.name; control.required = !!field.required;
            control.value = field.value || '';
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
                const values = Object.fromEntries(new FormData(form).entries()) as Record<string, string>;
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
        if (!node) return; node.textContent = message; node.dataset.state = state;
        const runtime = state === 'ready' ? 'ready' : state === 'error' ? 'error'
            : state === 'empty' ? 'needs-context' : 'loading';
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
