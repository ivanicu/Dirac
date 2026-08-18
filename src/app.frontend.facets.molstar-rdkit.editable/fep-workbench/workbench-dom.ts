import type { ExactOperationBinding } from './workbench-state';
import type { PreparationPolicyView, RunHistoryView, RunJobsView } from './workbench-view-model';

export type SafeTextTarget = { textContent: string | null };

export function setSafeText(target: SafeTextTarget | null, value: unknown): void {
    if (target) target.textContent = String(value ?? '');
}

export function escapeHtml(value: unknown): string {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[character]!));
}

export function bindDialogEscape(dialog: HTMLDialogElement | null, close: () => void): void {
    if (!dialog) return;
    const handler = (event: Event) => {
        if (event.type === 'keydown' && (event as KeyboardEvent).key !== 'Escape') return;
        event.preventDefault();
        close();
    };
    dialog.addEventListener('cancel', handler);
    dialog.addEventListener('keydown', handler);
}

export function dialogReturnTarget(documentLike: Document, fallbackId: string): HTMLElement | null {
    const active = documentLike.activeElement;
    return active instanceof HTMLElement && active !== documentLike.body
        ? active : documentLike.getElementById(fallbackId);
}

type SafeAttribute = 'aria-label' | 'role' | 'title';
type SafeElementOptions = Readonly<{
    className?: string;
    text?: unknown;
    attributes?: Partial<Record<SafeAttribute, string>>;
}>;

/**
 * Create UI-owned markup without an HTML parser. Only inert attributes needed
 * by the workbench are admitted; all dynamic content is assigned as text.
 * The document is injected so the helper remains testable without a DOM.
 */
export function safeElement<K extends keyof HTMLElementTagNameMap>(
    documentLike: Pick<Document, 'createElement'>,
    tag: K,
    options: SafeElementOptions = {},
): HTMLElementTagNameMap[K] {
    const element = documentLike.createElement(tag);
    if (options.className) element.className = options.className;
    if (Object.prototype.hasOwnProperty.call(options, 'text')) {
        element.textContent = String(options.text ?? '');
    }
    for (const [name, value] of Object.entries(options.attributes || {})) {
        if (value !== undefined) element.setAttribute(name, value);
    }
    return element;
}

export function renderRunHistoryDom(
    documentLike: Pick<Document, 'createElement'>,
    host: HTMLElement,
    view: RunHistoryView,
): void {
    const section = safeElement(documentLike, 'section', {
        className: 'run-history', attributes: { 'aria-label': 'Durable RunSet history' },
    });
    for (const row of view.rows) {
        const article = safeElement(documentLike, 'article', {
            className: `run-history-row${row.active ? ' active' : ''}`,
        });
        article.append(
            safeElement(documentLike, 'b', { text: row.heading }),
            safeElement(documentLike, 'code', { text: row.runIdentifier }),
            safeElement(documentLike, 'span', { text: row.context }),
        );
        if (row.aggregate !== null) {
            article.append(safeElement(documentLike, 'em', { text: row.aggregate }));
        }
        section.append(article);
    }
    if (!view.rows.length) {
        const empty = safeElement(documentLike, 'div', { className: 'job-empty' });
        empty.append(
            safeElement(documentLike, 'b', { text: 'NO DURABLE RUNSET HISTORY' }),
            safeElement(documentLike, 'span', {
                text: 'Terminal RunSets will remain inspectable here after reload.',
            }),
        );
        section.append(empty);
    }
    host.replaceChildren(section);
}

export function renderRunJobsDom(
    documentLike: Pick<Document, 'createElement'>,
    host: HTMLElement,
    view: RunJobsView,
): void {
    if (view.empty) {
        const empty = safeElement(documentLike, 'div', {
            className: `job-empty${view.ready ? ' ready' : ''}`,
        });
        empty.append(
            safeElement(documentLike, 'b', { text: view.emptyHeading }),
            safeElement(documentLike, 'span', { text: view.emptyDetail }),
        );
        host.replaceChildren(empty);
        return;
    }
    const header = safeElement(documentLike, 'div', { className: 'job-head' });
    for (const label of ['LEG', 'REP', 'FULL JOB ID', 'STATE']) {
        header.append(safeElement(documentLike, 'span', { text: label }));
    }
    const rows = view.rows.map(row => {
        const element = safeElement(documentLike, 'div', {
            className: `job-row ${row.stateClass}`,
        });
        element.append(
            safeElement(documentLike, 'b', { text: row.leg }),
            safeElement(documentLike, 'span', { text: row.repeat }),
            safeElement(documentLike, 'code', {
                text: row.jobId, attributes: { title: row.jobId },
            }),
            safeElement(documentLike, 'em', { text: row.state }),
        );
        return element;
    });
    host.replaceChildren(header, ...rows);
}

export function markPipelineReadyDom(
    documentLike: Pick<Document, 'querySelector'>,
    name: string,
    label: string,
): void {
    const row = documentLike.querySelector(`[data-pipeline="${name}"]`) as HTMLElement | null;
    row?.classList.add('ready');
    const state = row?.querySelector('em');
    if (state) state.textContent = label;
}

export function renderPreparationControlsDom(
    documentLike: Pick<Document, 'getElementById'>,
    visible: boolean,
    fullJobIdAvailable: boolean,
    cancelState: string,
): void {
    const cancel = documentLike.getElementById('cancel-preparation') as HTMLButtonElement | null;
    const detach = documentLike.getElementById('detach-preparation') as HTMLButtonElement | null;
    if (cancel) {
        cancel.hidden = !visible;
        cancel.disabled = !visible || !fullJobIdAvailable
            || cancelState === 'requested' || cancelState === 'confirmed';
    }
    if (detach) {
        detach.hidden = !visible;
        detach.disabled = !visible || cancelState === 'detached';
    }
}

export function renderPreparationPolicyDom(
    documentLike: Pick<Document, 'createElement' | 'getElementById' | 'querySelector'>,
    view: PreparationPolicyView,
): void {
    setSafeText(documentLike.getElementById('preparation-policy-summary'), view.summary);
    const host = documentLike.getElementById('preparation-policy-rows');
    if (host) {
        if (!view.generated) {
            host.replaceChildren(safeElement(documentLike, 'p', {
                text: 'Backend evidence appears here after receptor and pose preparation.',
            }));
        } else {
            host.replaceChildren(...view.rows.map(row => {
                const item = safeElement(documentLike, 'div', {
                    className: `policy-execution-row ${row.verdict.toLowerCase()}`,
                });
                item.append(
                    safeElement(documentLike, 'b', {
                        text: row.axis.replace(/_/g, ' ').toUpperCase(),
                    }),
                    safeElement(documentLike, 'em', { text: row.verdict }),
                    safeElement(documentLike, 'span', { text: row.witness }),
                );
                return item;
            }));
        }
    }
    documentLike.querySelector('.preparation-policy-audit')
        ?.classList.toggle('blocked', view.blocked);
}

function appendReceiptRef(
    documentLike: Pick<Document, 'createElement'>,
    host: HTMLElement,
    label: string,
    value: { kind: string; id: string; sha256: string },
): void {
    const row = safeElement(documentLike, 'div');
    row.append(
        safeElement(documentLike, 'small', { text: label }),
        safeElement(documentLike, 'code', {
            text: `${value.kind} · ${value.id} · ${value.sha256}`,
        }),
    );
    host.append(row);
}

export function renderOperationConfirmationDom(
    documentLike: Pick<Document, 'createElement'>,
    host: HTMLElement,
    binding: ExactOperationBinding | null,
    receiptPresent: boolean,
    mainCopy: boolean,
    exact: boolean,
): void {
    host.dataset.exact = String(exact);
    if (!binding) {
        host.replaceChildren(
            safeElement(documentLike, 'b', {
                text: `EXACT OPERATION CONFIRMATION · ${receiptPresent ? 'LEGACY / INCOMPLETE RECEIPT' : 'INCOMPLETE'}`,
            }),
            safeElement(documentLike, 'span', {
                text: receiptPresent
                    ? 'This receipt may be reconciled or cancelled, but it cannot START or RETRY physical work because plan/system/pose provenance is incomplete.'
                    : 'START remains locked until plan, system, endpoint poses and edge artifacts are all visible here.',
            }),
        );
        return;
    }
    host.replaceChildren(safeElement(documentLike, 'b', {
        text: mainCopy
            ? 'EXACT OPERATION CONFIRMATION'
            : 'AUDIT COPY · EXACT RECEIPT · PHYSICAL START DISABLED',
    }));
    const campaign = safeElement(documentLike, 'div');
    campaign.append(
        safeElement(documentLike, 'small', { text: 'CAMPAIGN' }),
        safeElement(documentLike, 'code', {
            text: `${binding.campaign.id} · generation ${binding.campaign.version} · ${binding.campaign.sha256}`,
        }),
    );
    const plan = safeElement(documentLike, 'div');
    plan.append(
        safeElement(documentLike, 'small', { text: 'PLAN JOB / EDGE' }),
        safeElement(documentLike, 'code', {
            text: `${binding.planNetworkJobId} · ${binding.edgeId}`,
        }),
    );
    host.append(campaign, plan);
    appendReceiptRef(documentLike, host, 'PLAN NETWORK', binding.planNetworkRef);
    appendReceiptRef(documentLike, host, 'PREPARED SYSTEM', binding.preparedSystemRef);
    appendReceiptRef(documentLike, host, 'PARENT POSE', binding.parentPoseRef);
    appendReceiptRef(documentLike, host, 'PROPOSAL POSE', binding.proposalPoseRef);
    appendReceiptRef(documentLike, host, 'EDGE SPEC', binding.edgeSpecRef);
    appendReceiptRef(documentLike, host, 'POSED EDGE NETWORK', binding.edgeNetworkRef);
    appendReceiptRef(documentLike, host, 'COMPLEX TRANSFORMATION', binding.complexTransformationRef);
    appendReceiptRef(documentLike, host, 'SOLVENT TRANSFORMATION', binding.solventTransformationRef);
    const request = safeElement(documentLike, 'div');
    request.append(
        safeElement(documentLike, 'small', { text: 'REQUEST / PREFLIGHT SPEC DIGEST' }),
        safeElement(documentLike, 'code', {
            text: `${binding.requestKey} · ${binding.specDigest}`,
        }),
    );
    host.append(request);
}
