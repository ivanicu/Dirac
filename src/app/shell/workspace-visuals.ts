import type { ViewDefinition } from './registries';
import type { ViewExperience } from './workspace-catalog';
import { WORKSPACE_VISUALS, type WorkspaceVisualSpec } from './workspace-visual-catalog';

const HTML_NS = 'http://www.w3.org/1999/xhtml';
const SVG_NS = 'http://www.w3.org/2000/svg';

const html = <K extends keyof HTMLElementTagNameMap>(tag: K, className?: string,
    text?: string): HTMLElementTagNameMap[K] => {
    const node = document.createElementNS(HTML_NS, tag) as HTMLElementTagNameMap[K];
    if (className) node.setAttribute('class', className);
    if (text !== undefined) node.textContent = text;
    return node;
};

const svg = <K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string> = {}) => {
    const node = document.createElementNS(SVG_NS, tag) as SVGElementTagNameMap[K];
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
    return node;
};

export interface WorkspaceVisualOptions {
    actionLabel?: string;
    onAction?: () => void;
}

function emptyMessage(label = 'No records yet'): HTMLElement {
    const empty = html('div', 'workspace-visual-empty');
    empty.append(html('span', 'workspace-visual-empty-mark', '—'),
        html('strong', '', label),
        html('small', '', 'No scientific marks are drawn until a provenance-bearing source is selected'));
    return empty;
}

function flow(spec: WorkspaceVisualSpec): HTMLElement {
    const body = html('div', 'workspace-flow');
    spec.primary.forEach((label, index) => {
        const stage = html('div', 'workspace-flow-stage');
        stage.append(html('span', '', String(index + 1).padStart(2, '0')), html('strong', '', label), html('small', '', 'Awaiting source'));
        body.append(stage);
        if (index < spec.primary.length - 1) body.append(html('i', 'workspace-flow-arrow', '→'));
    });
    return body;
}

function timeline(spec: WorkspaceVisualSpec): HTMLElement {
    const body = html('div', 'workspace-timeline');
    body.setAttribute('role', 'img');
    body.setAttribute('aria-label', `${spec.title}: expected event types; not a time scale`);
    const track = html('div', 'workspace-timeline-track');
    spec.primary.forEach((label, index) => {
        const event = html('div', 'workspace-timeline-event');
        event.style.setProperty('--position', `${(index / Math.max(spec.primary.length - 1, 1)) * 100}%`);
        event.append(html('i'), html('strong', '', label), html('small', '', 'Expected event type'));
        track.append(event);
    });
    body.append(track);
    return body;
}

function matrix(spec: WorkspaceVisualSpec): HTMLElement {
    const body = html('div', 'workspace-matrix-wrap');
    const table = html('table', 'workspace-matrix');
    const head = html('thead');
    const headRow = html('tr');
    headRow.append(html('th', '', 'Object / dimension'));
    for (const column of spec.secondary || ['Status', 'Evidence', 'Confidence']) headRow.append(html('th', '', column));
    head.append(headRow);
    const tbody = html('tbody');
    for (const row of spec.primary) {
        const tr = html('tr');
        tr.append(html('th', '', row));
        for (const _column of spec.secondary || ['Status', 'Evidence', 'Confidence']) {
            const cell = html('td');
            cell.append(html('span', 'workspace-matrix-empty', '—'));
            tr.append(cell);
        }
        tbody.append(tr);
    }
    table.append(head, tbody);
    body.append(table);
    return body;
}

function axes(spec: WorkspaceVisualSpec, kind: 'scatter' | 'curve'): HTMLElement {
    const wrap = html('div', `workspace-axes workspace-axes--${kind}`);
    const chart = svg('svg', { viewBox: '0 0 720 280', role: 'img', 'aria-label': `${spec.title}: awaiting data` });
    chart.append(svg('line', { x1: '64', y1: '226', x2: '690', y2: '226', class: 'axis' }),
        svg('line', { x1: '64', y1: '28', x2: '64', y2: '226', class: 'axis' }));
    for (let index = 1; index < 5; index++) {
        const y = 226 - index * 40;
        chart.append(svg('line', { x1: '64', y1: String(y), x2: '690', y2: String(y), class: 'grid' }));
    }
    const xLabel = svg('text', { x: '690', y: '258', 'text-anchor': 'end', class: 'label' });
    xLabel.textContent = spec.xLabel || 'Input';
    const yLabel = svg('text', { x: '18', y: '30', transform: 'rotate(-90 18 30)', 'text-anchor': 'end', class: 'label' });
    yLabel.textContent = spec.yLabel || 'Output';
    const empty = svg('text', { x: '377', y: '130', 'text-anchor': 'middle', class: 'empty-label' });
    empty.textContent = 'AWAITING CONNECTED OBSERVATIONS';
    chart.append(xLabel, yLabel, empty);
    wrap.append(chart);
    return wrap;
}

function network(spec: WorkspaceVisualSpec): HTMLElement {
    const wrap = html('div', 'workspace-network');
    const graph = svg('svg', {
        viewBox: '0 0 720 300',
        role: 'img',
        'aria-label': `${spec.title}: expected entity types; relationships are not loaded`,
    });
    const primary = spec.primary;
    const secondary = spec.secondary || [];
    const positions = [
        [150, 84], [360, 54], [570, 84], [230, 222], [490, 222], [360, 154],
    ];
    const labels = [...primary, ...secondary].slice(0, positions.length);
    labels.forEach((label, index) => {
        const [x, y] = positions[index];
        const group = svg('g', { class: index === 0 ? 'node node--primary' : 'node' });
        group.append(svg('rect', { x: String(x - 74), y: String(y - 22), width: '148', height: '44', rx: '3' }));
        const text = svg('text', { x: String(x), y: String(y + 4), 'text-anchor': 'middle' });
        text.textContent = label;
        group.append(text);
        graph.append(group);
    });
    wrap.append(graph);
    return wrap;
}

function funnel(spec: WorkspaceVisualSpec): HTMLElement {
    const body = html('div', 'workspace-funnel');
    body.setAttribute('role', 'img');
    body.setAttribute('aria-label', `${spec.title}: ordered review stages without counts or attrition`);
    spec.primary.forEach((label, index) => {
        const row = html('div', 'workspace-funnel-stage');
        row.append(html('span', '', String(index + 1).padStart(2, '0')), html('strong', '', label), html('small', '', 'No count'));
        body.append(row);
    });
    return body;
}

function kanban(spec: WorkspaceVisualSpec): HTMLElement {
    const body = html('div', 'workspace-kanban');
    for (const column of spec.primary) {
        const lane = html('section', 'workspace-kanban-lane');
        const header = html('header');
        header.append(html('strong', '', column), html('span', '', '0'));
        lane.append(header, emptyMessage('No items in this state'));
        body.append(lane);
    }
    return body;
}

function plate(spec: WorkspaceVisualSpec): HTMLElement {
    const body = html('div', 'workspace-plate-map');
    const map = html('div', 'workspace-plate-grid');
    map.setAttribute('role', 'img');
    map.setAttribute('aria-label', `${spec.title}: empty 8 by 12 plate schema; no samples or controls assigned`);
    map.append(html('span', 'workspace-plate-corner'));
    for (let column = 0; column < 12; column++) map.append(html('span', 'workspace-plate-axis', String(column + 1)));
    for (let row = 0; row < 8; row++) {
        map.append(html('span', 'workspace-plate-axis', String.fromCharCode(65 + row)));
        for (let column = 0; column < 12; column++) {
            const well = html('i');
            well.setAttribute('aria-hidden', 'true');
            map.append(well);
        }
    }
    const legend = html('div', 'workspace-plate-legend');
    legend.append(html('strong', '', '96-well execution map'), html('span', '', 'All wells awaiting sample and control assignments'));
    body.append(map, legend);
    return body;
}

function table(spec: WorkspaceVisualSpec): HTMLElement {
    const body = html('div', 'workspace-table-wrap');
    const table = html('table', 'workspace-visual-table');
    const head = html('thead');
    const tr = html('tr');
    for (const column of spec.primary) tr.append(html('th', '', column));
    head.append(tr);
    const tbody = html('tbody');
    const row = html('tr');
    const cell = html('td');
    cell.colSpan = spec.primary.length;
    cell.append(emptyMessage('No connected records'));
    row.append(cell); tbody.append(row); table.append(head, tbody); body.append(table);
    return body;
}

function lineage(spec: WorkspaceVisualSpec): HTMLElement {
    const body = html('div', 'workspace-lineage');
    const sources = html('div', 'workspace-lineage-column');
    for (const label of spec.secondary || ['Source A', 'Source B']) sources.append(html('div', '', label));
    const center = html('div', 'workspace-lineage-center');
    center.append(html('i', '', '→'), html('strong', '', spec.primary[1] || 'Versioned object'), html('i', '', '→'));
    const consumers = html('div', 'workspace-lineage-column');
    consumers.append(html('div', '', spec.primary[0]), html('div', '', spec.primary[2] || 'Consumers'));
    body.append(sources, center, consumers);
    return body;
}

function compare(spec: WorkspaceVisualSpec): HTMLElement {
    const body = html('div', 'workspace-compare');
    const panel = (label: string) => {
        const node = html('section', 'workspace-compare-panel');
        node.append(html('header', '', label), html('div', 'workspace-compare-viewport'), html('small', '', 'Awaiting structure selection'));
        return node;
    };
    const delta = html('div', 'workspace-compare-delta');
    delta.append(html('strong', '', 'Δ'), ...(spec.secondary || ['Geometry', 'Interactions', 'Fields']).map(label => {
        const row = html('span'); row.append(html('b', '', label), html('i', '', '—')); return row;
    }));
    body.append(panel(spec.primary[0]), delta, panel(spec.primary[1]));
    return body;
}

function visualBody(spec: WorkspaceVisualSpec): HTMLElement {
    if (spec.kind === 'flow') return flow(spec);
    if (spec.kind === 'timeline') return timeline(spec);
    if (spec.kind === 'matrix') return matrix(spec);
    if (spec.kind === 'scatter' || spec.kind === 'curve') return axes(spec, spec.kind);
    if (spec.kind === 'network') return network(spec);
    if (spec.kind === 'funnel') return funnel(spec);
    if (spec.kind === 'kanban') return kanban(spec);
    if (spec.kind === 'plate') return plate(spec);
    if (spec.kind === 'table') return table(spec);
    if (spec.kind === 'lineage') return lineage(spec);
    return compare(spec);
}

export function renderWorkspaceVisual(definition: ViewDefinition, experience: ViewExperience,
    options: WorkspaceVisualOptions = {}): HTMLElement {
    const spec = WORKSPACE_VISUALS[definition.id];
    const section = html('section', 'workspace-visual');
    section.dataset.kind = spec.kind;

    const heading = html('header', 'workspace-visual-heading');
    const copy = html('div');
    copy.append(html('span', 'workspace-section-kicker', `${spec.kind} view`), html('h2', '', spec.title), html('p', '', spec.caption));
    const truth = html('span', 'workspace-visual-truth', 'NO DATA SELECTED');
    heading.append(copy, truth);

    const body = html('div', 'workspace-visual-layout');
    const stage = html('div', 'workspace-visual-stage');
    stage.append(visualBody(spec));
    const rail = html('aside', 'workspace-visual-rail');
    rail.append(html('span', 'workspace-visual-guide-label', 'Start here'),
        html('h3', '', 'Choose a connected source'),
        html('p', '', 'This View stays empty until an object or read model provides traceable observations.'));
    if (options.actionLabel && options.onAction) {
        const action = html('button', 'workspace-visual-action', options.actionLabel);
        action.type = 'button';
        action.addEventListener('click', options.onAction);
        rail.append(action);
    }
    const boundary = html('div', 'workspace-visual-boundary');
    boundary.append(html('span', '', 'Evidence boundary'), html('p', '',
        'Axes and stages describe the reading contract only. No trend, relationship, target region, or event timing is inferred.'));
    rail.append(boundary);
    body.append(stage, rail);
    section.append(heading, body);
    return section;
}
