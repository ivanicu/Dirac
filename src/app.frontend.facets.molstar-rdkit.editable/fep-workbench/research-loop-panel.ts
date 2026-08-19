import { ResearchLoopClient, type ResearchContext, type ResearchLoopSnapshot,
    type ResearchProgram, type ResearchProposal, type ResearchProvider } from './research-loop-client';

type CampaignBinding = { campaignId: string; label: string };
type MountOptions = {
    client: ResearchLoopClient;
    campaign: () => CampaignBinding | null;
};

const CLASS_LABEL: Record<string, string> = {
    typed_evidence: 'TYPED EVIDENCE',
    method_result: 'METHOD RESULT',
    human_attestation: 'HUMAN ATTESTATION',
    system_state: 'SYSTEM STATE',
    unverified_external: 'UNVERIFIED EXTERNAL',
};

function node<K extends keyof HTMLElementTagNameMap>(
    tag: K, className = '', text = '',
): HTMLElementTagNameMap[K] {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text) result.textContent = text;
    return result;
}

function section(title: string, kicker = ''): HTMLElement {
    const root = node('section', 'research-loop-section');
    const header = node('header');
    header.append(node('b', '', title), node('span', '', kicker));
    root.append(header);
    return root;
}

function stringify(value: unknown): string {
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'string') return value;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    return JSON.stringify(value);
}

function id(): string {
    return globalThis.crypto?.randomUUID?.()
        || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

class ResearchLoopPanel {
    private loop: ResearchLoopSnapshot | null = null;
    private context: ResearchContext | null = null;
    private proposal: ResearchProposal | null = null;
    private profiles: ResearchProvider[] = [];
    private programs: ResearchProgram[] = [];
    private polling = 0;
    private busy = false;
    private returnFocus: HTMLElement | null = null;

    constructor(private host: HTMLElement, private toggle: HTMLButtonElement,
                private options: MountOptions) {
        this.host.hidden = true;
        this.host.setAttribute('aria-hidden', 'true');
        this.toggle.addEventListener('click', () => this.open());
        this.host.addEventListener('keydown', event => {
            if (event.key === 'Escape') this.close();
        });
    }

    private storageKey(campaignId: string): string {
        return `dirac.research-loop.${campaignId}`;
    }

    private receipt(campaignId: string): { runId?: string; requestKey?: string; requestPayload?: string } {
        try { return JSON.parse(localStorage.getItem(this.storageKey(campaignId)) || '{}'); } catch { return {}; }
    }

    private store(campaignId: string, value: {
        runId?: string; requestKey?: string; requestPayload?: string;
    }): void {
        localStorage.setItem(this.storageKey(campaignId), JSON.stringify(value));
    }

    private async open(): Promise<void> {
        this.returnFocus = document.activeElement instanceof HTMLElement
            ? document.activeElement : this.toggle;
        this.host.hidden = false;
        this.host.setAttribute('aria-hidden', 'false');
        this.toggle.setAttribute('aria-expanded', 'true');
        await this.refresh();
        this.host.querySelector<HTMLElement>('button,select,input,textarea')?.focus();
    }

    private close(): void {
        window.clearTimeout(this.polling);
        this.host.hidden = true;
        this.host.setAttribute('aria-hidden', 'true');
        this.toggle.setAttribute('aria-expanded', 'false');
        this.returnFocus?.focus();
    }

    private async refresh(): Promise<void> {
        const campaign = this.options.campaign();
        if (!campaign) {
            this.renderNotice('NO GOVERNED CAMPAIGN',
                'Build or restore a planned FEP Campaign before starting an AI research loop.');
            return;
        }
        try {
            const receipt = this.receipt(campaign.campaignId);
            if (!this.profiles.length) {
                [this.profiles, this.programs] = await Promise.all([
                    this.options.client.providers(), this.options.client.programs(),
                ]);
            }
            if (receipt.runId) {
                this.loop = await this.options.client.get(receipt.runId);
                [this.context, this.proposal] = await Promise.all([
                    this.options.client.context(this.loop.context_ref),
                    this.options.client.proposal(this.loop.proposal_ref),
                ]);
            }
            this.render(campaign);
            if (this.loop && ['active', 'waiting_approval'].includes(this.loop.state)) {
                this.polling = window.setTimeout(() => void this.refresh(), 2000);
            }
        } catch (error) {
            this.renderNotice('RESEARCH LOOP UNAVAILABLE',
                error instanceof Error ? error.message : String(error), true);
        }
    }

    private frame(title: string): HTMLElement {
        this.host.replaceChildren();
        const top = node('header', 'research-loop-top');
        const copy = node('div');
        copy.append(node('small', '', 'AI RESEARCH LOOP'), node('b', '', title));
        const close = node('button', 'research-loop-close', 'CLOSE');
        close.type = 'button'; close.setAttribute('aria-label', 'Close AI research loop');
        close.addEventListener('click', () => this.close());
        top.append(copy, close);
        const live = node('div', 'research-loop-live');
        live.id = 'research-loop-live'; live.setAttribute('role', 'status');
        live.setAttribute('aria-live', 'polite');
        this.host.append(top, live);
        return live;
    }

    private renderNotice(title: string, detail: string, retry = false): void {
        const live = this.frame(title);
        const block = section('ATTENTION', 'FAIL CLOSED');
        block.append(node('p', 'research-loop-attention', detail));
        const review = node('button', 'research-loop-secondary', 'OPEN FEP REVIEW');
        review.addEventListener('click', () => this.openReview());
        block.append(review);
        if (retry) {
            const button = node('button', 'research-loop-primary', 'RETRY CAPABILITY CHECK');
            button.addEventListener('click', () => { this.profiles = []; void this.refresh(); });
            block.append(button);
        }
        live.append(block);
    }

    private render(campaign: CampaignBinding): void {
        if (!this.loop) { this.renderCreate(campaign); return; }
        const live = this.frame(`${this.loop.state.toUpperCase()} · ${this.loop.stage.toUpperCase()}`);
        const status = node('div', 'research-loop-status');
        status.append(
            node('span', '', `STATE ${this.loop.state.toUpperCase()}`),
            node('span', '', `STAGE ${this.loop.stage.toUpperCase()}`),
            node('span', '', `ITERATION ${this.loop.iteration}`),
            node('span', '', `PROVIDER ${this.loop.provider.profile_id}`),
        );
        live.append(status);
        this.renderGoal(live);
        this.renderFacts(live);
        this.renderHypotheses(live);
        this.renderAction(live);
        this.renderBudget(live);
        this.renderAttention(live);
        this.renderTimeline(live);
        this.renderControls(live);
    }

    private renderCreate(campaign: CampaignBinding): void {
        const live = this.frame('NEW LOOP');
        const available = this.profiles.filter(row => row.configured);
        if (!available.length) {
            const block = section('ATTENTION', 'PROVIDER UNCONFIGURED');
            block.append(node('p', 'research-loop-attention',
                'No admitted provider is configured. Existing FEP planning, preparation and RunSets remain available.'));
            const reasons = node('ul');
            this.profiles.forEach(profile => reasons.append(
                node('li', '', `${profile.label} · ${profile.reason || 'not configured'}`)));
            block.append(reasons);
            const review = node('button', 'research-loop-secondary', 'OPEN FEP REVIEW');
            review.addEventListener('click', () => this.openReview()); block.append(review);
            live.append(block); return;
        }
        const goal = section('GOAL', campaign.label.toUpperCase());
        const intent = node('textarea') as HTMLTextAreaElement;
        intent.id = 'research-loop-intent'; intent.rows = 4;
        intent.value = 'Prioritize FEP evidence that could change the current lead ranking.';
        intent.setAttribute('aria-label', 'Human-authored research-loop goal');
        const program = node('select') as HTMLSelectElement;
        program.id = 'research-loop-program'; program.setAttribute('aria-label', 'Program');
        this.programs.forEach(row => {
            const option = node('option') as HTMLOptionElement;
            option.value = row.ref.id; option.textContent = `${row.code || 'PROGRAM'} · ${row.name}`;
            program.append(option);
        });
        const provider = node('select') as HTMLSelectElement;
        provider.id = 'research-loop-provider'; provider.setAttribute('aria-label', 'AI provider');
        available.forEach(row => {
            const option = node('option') as HTMLOptionElement;
            option.value = row.profile_id;
            option.textContent = `${row.label} · ${row.configured_model} · ${row.locality}`;
            provider.append(option);
        });
        const disclosure = node('p', 'research-loop-egress');
        const updateDisclosure = () => {
            const selected = available.find(row => row.profile_id === provider.value);
            disclosure.textContent = selected?.external_egress
                ? 'This provider receives a bounded summary of structures, Campaign state, selected scientific results, and this intent. Raw trajectories, PDB files, checkpoints, credentials, and hidden reasoning are not sent.'
                : 'This isolated provider receives the same bounded summary. Raw trajectories, PDB files, checkpoints, credentials, and hidden reasoning are not sent.';
        };
        provider.addEventListener('change', updateDisclosure); updateDisclosure();
        const fields = node('div', 'research-loop-create-fields');
        fields.append(this.label('PROGRAM', program), this.label('PROVIDER', provider));
        goal.append(intent, fields, disclosure);
        const create = node('button', 'research-loop-primary', 'START BOUNDED RESEARCH LOOP');
        create.disabled = !this.programs.length;
        create.addEventListener('click', () => void this.create(campaign, program.value,
            provider.value, intent.value));
        goal.append(create);
        live.append(goal);
    }

    private label(text: string, control: HTMLElement): HTMLElement {
        const label = node('label'); label.append(node('span', '', text), control); return label;
    }

    private async create(campaign: CampaignBinding, programId: string,
                         providerId: string, intent: string): Promise<void> {
        if (this.busy || !programId || !providerId || !intent.trim()) return;
        this.busy = true;
        const provider = this.profiles.find(row => row.profile_id === providerId)!;
        const request = {
            program_ref: { kind: 'program' as const, id: programId },
            campaign_ref: { kind: 'campaign' as const, id: campaign.campaignId },
            intent: intent.trim(), autonomy_class: 'A2' as const,
            provider_profile_id: providerId, data_classification: 'internal',
            budget: { max_reasoner_calls: 8, max_iterations: 8,
                max_fep_runsets: 3, max_gpu_hours: 12, max_external_cost: 10 },
            policy: { auto_risk_classes: ['R0', 'R1', 'R2'],
                per_action_risk_classes: ['R3'], human_only_risk_classes: ['R4'],
                stop_on_campaign_stale: true as const, stop_on_open_identity_conflict: true as const,
                max_same_subject_actions: 3,
                cloud_egress_approved: provider.external_egress },
        };
        const requestPayload = JSON.stringify(request);
        const existing = this.receipt(campaign.campaignId);
        const requestKey = existing.requestKey && existing.requestPayload === requestPayload
            ? existing.requestKey : `research-loop:${campaign.campaignId}:${id()}`;
        this.store(campaign.campaignId, { requestKey, requestPayload });
        try {
            const result = await this.options.client.create({
                request_key: requestKey, ...request,
            });
            const runId = String(result.run_ref?.id || '');
            if (!runId) throw new Error('research.loop.create returned no durable Run');
            this.store(campaign.campaignId, { requestKey, requestPayload, runId });
            await this.refresh();
        } catch (error) {
            this.renderNotice('LOOP CREATION REFUSED',
                error instanceof Error ? error.message : String(error), true);
        } finally { this.busy = false; }
    }

    private renderGoal(root: HTMLElement): void {
        const item = section('GOAL', 'HUMAN AUTHORED');
        item.append(node('p', '', this.loop!.goal.intent)); root.append(item);
    }

    private renderFacts(root: HTMLElement): void {
        const item = section('WHAT CHANGED', 'CURRENT CONTEXT');
        const facts = (this.context?.facts || []).slice(-8).reverse();
        if (!facts.length) item.append(node('p', 'research-loop-empty', 'No context Artifact yet.'));
        facts.forEach(fact => {
            const row = node('article', 'research-loop-fact');
            const stale = Boolean(fact.freshness?.stale);
            const sourceLabel = fact.source_class === 'method_result'
                ? 'METHOD RESULT · COMPLETED UNVALIDATED'
                : (CLASS_LABEL[String(fact.source_class)] || 'SYSTEM STATE');
            const badge = node('b', `research-loop-badge ${fact.source_class || ''}`,
                stale ? 'STALE' : sourceLabel);
            row.append(badge, node('span', '', String(fact.category || fact.fact_id || 'fact')),
                node('p', '', stringify(fact.structured_value || fact.untrusted_content)));
            const boundary = fact.claim_boundary || {};
            row.append(node('small', '', `${String(boundary.status || 'unclassified').toUpperCase()} · ${boundary.eligible_as_scientific_evidence ? 'ELIGIBLE EVIDENCE' : 'NOT ELIGIBLE AS EVIDENCE'}`));
            item.append(row);
        }); root.append(item);
    }

    private renderHypotheses(root: HTMLElement): void {
        const item = section('CURRENT DRAFT HYPOTHESES', 'AI DRAFT');
        const rows = this.proposal?.hypothesis_drafts || [];
        if (!rows.length) item.append(node('p', 'research-loop-empty', 'No validated proposal Artifact yet.'));
        rows.forEach(hypothesis => {
            const card = node('article', 'research-loop-hypothesis');
            card.append(node('b', 'research-loop-badge ai-draft', 'AI DRAFT'),
                node('h4', '', stringify(hypothesis.statement)),
                node('p', '', `Prediction · ${stringify(hypothesis.testable_prediction)}`),
                node('p', '', `Falsifier · ${stringify(hypothesis.falsifier)}`),
                node('small', '', `Supports ${stringify(hypothesis.supporting_fact_ids)} · Contradicts ${stringify(hypothesis.contradicting_fact_ids)} · Assumptions ${stringify(hypothesis.assumptions)}`));
            item.append(card);
        }); root.append(item);
    }

    private renderAction(root: HTMLElement): void {
        const pending = this.loop!.pending_action || {};
        const preview = pending.preview as Record<string, any> | undefined;
        const item = section('RECOMMENDED NEXT ACTION', preview ? 'SERVER COMPILED' : 'NO PREVIEW');
        if (!preview) {
 item.append(node('p', 'research-loop-empty',
            this.proposal?.summary || 'The controller is deriving the next bounded action.')); root.append(item); return;
}
        item.append(node('b', 'research-loop-action-title', String(preview.template_id)),
            node('p', '', `${String(preview.subject_ref?.kind)} · ${String(preview.subject_ref?.id)}`),
            node('p', '', String(preview.scientific_question)),
            node('small', '', `Risk ${preview.consequence?.risk_class} · ${preview.consequence?.summary}`),
            node('div', 'research-loop-boundary', 'MODEL PROPOSAL ≠ SCIENTIFIC EVIDENCE · COMPLETED FEP REMAINS UNVALIDATED'));
        const estimate = preview.estimate || {};
        item.append(node('p', '', `Estimate · GPU ≤ ${stringify(estimate.gpu_hours_upper_bound)} h · external cost ≤ ${stringify(estimate.external_cost_upper_bound)}`));
        if (this.loop!.state === 'waiting_approval') this.renderApproval(item, preview);
        root.append(item);
    }

    private renderApproval(root: HTMLElement, preview: Record<string, any>): void {
        const approval = node('div', 'research-loop-approval');
        approval.append(node('b', '', 'APPROVAL · EXACT CONSEQUENCES'));
        const acknowledgements = (preview.required_acknowledgements || []) as string[];
        const checks: HTMLInputElement[] = [];
        acknowledgements.forEach(value => {
            const input = node('input') as HTMLInputElement;
            input.type = 'checkbox'; input.value = value;
            checks.push(input);
            const label = node('label'); label.append(input, node('span', '', value.replace(/_/g, ' ').toUpperCase()));
            approval.append(label);
        });
        const rationale = node('textarea') as HTMLTextAreaElement;
        rationale.rows = 3; rationale.placeholder = 'Why this exact action resolves the decision gap';
        rationale.setAttribute('aria-label', 'Approval or rejection rationale'); approval.append(rationale);
        const actions = node('div', 'research-loop-approval-actions');
        const approve = node('button', 'research-loop-primary', 'APPROVE EXACT ACTION');
        const reject = node('button', 'research-loop-danger', 'REJECT');
        approve.addEventListener('click', () => void this.decide(true, preview, rationale.value,
            checks.filter(check => check.checked).map(check => check.value)));
        reject.addEventListener('click', () => void this.decide(false, preview, rationale.value, []));
        actions.append(approve, reject); approval.append(actions); root.append(approval);
    }

    private async decide(approved: boolean, preview: Record<string, any>, rationale: string,
                         acknowledgements: string[]): Promise<void> {
        if (this.busy || !this.loop || !rationale.trim()) return;
        this.busy = true;
        try {
            if (approved) await this.options.client.approve(
                this.loop, String(preview.action_fingerprint), acknowledgements, rationale.trim());
            else await this.options.client.reject(
                this.loop, String(preview.action_fingerprint), rationale.trim());
            await this.refresh();
        } catch (error) {
 this.renderNotice('DECISION REFUSED',
            error instanceof Error ? error.message : String(error), true);
} finally { this.busy = false; }
    }

    private renderBudget(root: HTMLElement): void {
        const item = section('BUDGET', 'REMAINING / SPENT');
        const grid = node('div', 'research-loop-budget');
        for (const key of ['reasoner_calls', 'fep_runsets', 'gpu_hours', 'external_cost', 'iterations']) {
            const cell = node('span'); cell.append(node('small', '', key.replace(/_/g, ' ').toUpperCase()),
                node('b', '', `${stringify(this.loop!.budget.remaining[key])} / ${stringify(this.loop!.budget.spent[key])}`));
            grid.append(cell);
        }
        item.append(grid); root.append(item);
    }

    private renderAttention(root: HTMLElement): void {
        const attention = this.loop!.attention || {};
        if (!Object.keys(attention).length) return;
        const item = section('ATTENTION', String(attention.reason_code || attention.code || 'BLOCKED'));
        item.append(node('p', 'research-loop-attention',
            String(attention.summary || attention.message || stringify(attention)))); root.append(item);
    }

    private renderTimeline(root: HTMLElement): void {
        const item = section('TIMELINE', `${this.loop!.events.length} RECENT EVENTS`);
        const list = node('ol', 'research-loop-timeline');
        [...this.loop!.events].reverse().forEach(event => {
            const row = node('li');
            const actor = `${String(event.actor?.kind || '')}:${String(event.actor?.id || '')}`;
            const authority = event.actor?.kind === 'service'
                ? ` · Executed automatically under Ivan's loop grant` : '';
            row.append(node('b', '', String(event.event_type || 'event').replace(/_/g, ' ').toUpperCase()),
                node('span', '', `${String(event.stage || '')} · ${actor}${authority}`),
                node('small', '', String(event.occurred_at || ''))); list.append(row);
        }); item.append(list); root.append(item);
    }

    private renderControls(root: HTMLElement): void {
        const item = section('LOOP CONTROL', 'HUMAN AUTHORITY');
        const rationale = node('input') as HTMLInputElement;
        rationale.placeholder = 'Control rationale'; rationale.setAttribute('aria-label', 'Control rationale');
        const revised = node('textarea') as HTMLTextAreaElement;
        revised.rows = 2; revised.value = this.loop!.goal.intent;
        revised.setAttribute('aria-label', 'Revised research goal');
        const actions = node('div', 'research-loop-control-actions');
        const choices = this.loop!.state === 'paused' ? ['resume', 'cancel']
            : this.loop!.state === 'blocked' ? ['retry', 'pause', 'cancel']
                : ['pause', 'cancel'];
        choices.forEach(action => {
            const button = node('button', action === 'cancel' ? 'research-loop-danger' : 'research-loop-secondary', action.toUpperCase());
            button.addEventListener('click', () => void this.control(action, rationale.value)); actions.append(button);
        });
        const revise = node('button', 'research-loop-secondary', 'REVISE GOAL');
        revise.addEventListener('click', () => void this.control('revise_intent', rationale.value,
            { revised_intent: revised.value }));
        const provider = node('select') as HTMLSelectElement;
        provider.setAttribute('aria-label', 'Replacement AI provider');
        this.profiles.filter(row => row.configured).forEach(row => {
            const option = node('option') as HTMLOptionElement;
            option.value = row.profile_id; option.textContent = `${row.label} · ${row.locality}`;
            option.selected = row.profile_id === this.loop!.provider.profile_id;
            provider.append(option);
        });
        const changeProvider = node('button', 'research-loop-secondary', 'CHANGE PROVIDER');
        changeProvider.disabled = provider.value === this.loop!.provider.profile_id;
        provider.addEventListener('change', () => {
            changeProvider.disabled = provider.value === this.loop!.provider.profile_id;
        });
        changeProvider.addEventListener('click', () => void this.control(
            'change_provider', rationale.value, { provider_profile_id: provider.value }));
        const review = node('button', 'research-loop-secondary', 'OPEN FEP REVIEW');
        review.addEventListener('click', () => this.openReview());
        actions.append(revise, changeProvider, review);
        item.append(revised, provider, rationale, actions); root.append(item);
    }

    private async control(action: string, rationale: string,
                          extra: Record<string, unknown> = {}): Promise<void> {
        if (this.busy || !this.loop || !rationale.trim()) return;
        this.busy = true;
        try { await this.options.client.control(this.loop, action, rationale.trim(), extra); await this.refresh(); } catch (error) {
 this.renderNotice('CONTROL REFUSED',
            error instanceof Error ? error.message : String(error), true);
} finally { this.busy = false; }
    }

    private openReview(): void {
        this.close();
        const button = document.getElementById('main-review') as HTMLButtonElement | null;
        button?.click(); button?.focus();
    }
}

export function mountResearchLoopPanel(options: MountOptions): void {
    const host = document.getElementById('research-loop-drawer');
    const toggle = document.getElementById('research-loop-toggle');
    if (!(host instanceof HTMLElement) || !(toggle instanceof HTMLButtonElement)) {
        throw new Error('Research Loop Drawer mount points are missing');
    }
    new ResearchLoopPanel(host, toggle, options);
}
