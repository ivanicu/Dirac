import cytoscape, { type Core, type ElementDefinition } from 'cytoscape';
import Gantt, { type GanttTask } from 'frappe-gantt';
import { use, init, getInstanceByDom, type EChartsType } from 'echarts/core';
import { BarChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent, AriaComponent } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
import { graphKindSeries, laneLoadSeries, WORK_LANES,
    type ScientificGraphModel, type ScientificGraphNode,
    type WorkLane, type WorkVisualItem } from './visual-models';
export { graphKindSeries, laneLoadSeries, programRelationGraph, toWorkVisualItems,
    workGraphModel, WORK_LANES } from './visual-models';
export type { ScientificGraphEdge, ScientificGraphModel, ScientificGraphNode,
    WorkLane, WorkVisualItem } from './visual-models';

use([BarChart, GridComponent, LegendComponent, TooltipComponent, AriaComponent, SVGRenderer]);

const graphInstances = new WeakMap<HTMLElement, Core>();
const graphObservers = new WeakMap<HTMLElement, ResizeObserver>();
const chartObservers = new WeakMap<HTMLElement, ResizeObserver>();

const humanize = (value: string): string => value.replace(/_/g, ' ');

function graphElements(model: ScientificGraphModel): ElementDefinition[] {
    return [
        ...model.nodes.map((node, index) => ({
            data: { id: node.id, label: node.label, kind: node.kind,
                status: node.status || '', ref: node.ref },
            position: {
                x: node.x ?? (index % 5) * 210,
                y: node.y ?? Math.floor(index / 5) * 96,
            },
        })),
        ...model.edges.map(edge => ({ data: { id: edge.id, source: edge.source,
            target: edge.target, label: edge.label } })),
    ];
}

function replaceObserver(map: WeakMap<HTMLElement, ResizeObserver>, root: HTMLElement,
                         callback: () => void): void {
    map.get(root)?.disconnect();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(callback); observer.observe(root); map.set(root, observer);
}

function accessibleGraphList(model: ScientificGraphModel,
    onSelect?: (node: ScientificGraphNode) => void): HTMLElement {
    const details = document.createElement('details'); details.className = 'scientific-visual-fallback';
    const summary = document.createElement('summary'); summary.textContent = 'Accessible graph list';
    const list = document.createElement('ul');
    for (const node of model.nodes) {
        const item = document.createElement('li');
        const incoming = model.edges.filter(edge => edge.target === node.id);
        const outgoing = model.edges.filter(edge => edge.source === node.id);
        const action = document.createElement('button'); action.type = 'button';
        action.textContent = `${node.label} · ${humanize(node.kind)}`;
        action.disabled = !onSelect;
        if (onSelect) action.addEventListener('click', () => onSelect(node));
        const relation = document.createElement('span');
        relation.textContent = `${incoming.length} incoming · ${outgoing.length} outgoing`;
        item.append(action, relation); list.append(item);
    }
    details.append(summary, list); return details;
}

export function renderScientificGraph(root: HTMLElement, model: ScientificGraphModel,
    options: { readonly ariaLabel: string; readonly onSelect?: (node: ScientificGraphNode) => void;
        readonly preset?: boolean }): void {
    graphInstances.get(root)?.destroy(); graphObservers.get(root)?.disconnect();
    root.replaceChildren();
    if (!model.nodes.length) {
        const empty = document.createElement('p'); empty.className = 'scientific-visual-empty';
        empty.textContent = 'No traceable nodes are available for this view.'; root.append(empty); return;
    }
    const canvas = document.createElement('div'); canvas.className = 'scientific-graph-canvas';
    canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', options.ariaLabel);
    root.append(canvas, accessibleGraphList(model, options.onSelect));
    const cy = cytoscape({
        container: canvas, elements: graphElements(model),
        layout: options.preset === false ? { name: 'cose', animate: false, fit: true, padding: 28 }
            : { name: 'preset', fit: true, padding: 32 },
        minZoom: .25, maxZoom: 2.2,
        style: [
            { selector: 'node', style: {
                'shape': 'round-rectangle', 'width': 176, 'height': 48,
                'background-color': '#f7f6f2', 'border-color': '#292925', 'border-width': 1,
                'label': 'data(label)', 'font-family': 'IBM Plex Sans, sans-serif',
                'font-size': '10px', 'text-wrap': 'ellipsis', 'text-max-width': '154px',
                'color': '#292925', 'text-valign': 'center', 'text-halign': 'center',
            } },
            { selector: 'node[status = "blocked"]', style: { 'border-color': '#8f1f25', 'border-width': 3 } },
            { selector: 'node[status = "done"]', style: { 'border-color': '#36724b', 'opacity': .72 } },
            { selector: 'node:selected', style: { 'background-color': '#292925', 'color': '#f7f6f2', 'border-width': 3 } },
            { selector: 'edge', style: {
                'width': 1.2, 'line-color': '#aaa9a2', 'target-arrow-color': '#77766f',
                'target-arrow-shape': 'triangle', 'curve-style': 'bezier',
                'label': 'data(label)', 'font-size': 7, 'color': '#77766f',
                'text-background-color': '#f7f6f2', 'text-background-opacity': .92,
                'text-background-padding': '2px',
            } },
        ],
    });
    cy.on('tap', 'node', event => {
        const node = model.nodes.find(candidate => candidate.id === event.target.id());
        if (node && options.onSelect) options.onSelect(node);
    });
    graphInstances.set(root, cy);
    replaceObserver(graphObservers, root, () => { cy.resize(); cy.fit(undefined, 28); });
}

export function renderProgramGantt(root: HTMLElement, items: readonly WorkVisualItem[],
    onSelect: (id: string) => void): void {
    root.replaceChildren();
    const scheduled = items.filter(item => item.start && item.end);
    if (!scheduled.length) {
        const empty = document.createElement('p'); empty.className = 'program-gantt-empty';
        empty.textContent = items.length
            ? 'No invented bars: add a planned start and finish to place work on the schedule.'
            : 'Plan the first task to create the Program schedule.';
        root.append(empty); return;
    }
    const chart = document.createElement('div'); chart.className = 'program-frappe-gantt';
    const tasks: GanttTask[] = scheduled.map(item => ({
        id: item.id, name: `${item.key} · ${item.title}`, start: item.start!, end: item.end!,
        progress: item.status === 'done' ? 100 : 0,
        dependencies: item.dependencyIds.join(','),
        custom_class: `dirac-gantt-${item.status.replace(/[^a-z0-9_-]/gi, '-')}`,
        description: `${item.owner} · ${humanize(item.lane)} · ${humanize(item.status)}`,
    }));
    root.append(chart);
    new Gantt(chart, tasks, {
        view_modes: ['Day', 'Week', 'Month'], view_mode: 'Week', view_mode_select: true,
        readonly: true, popup: false, infinite_padding: false, scroll_to: 'today',
        on_click: task => onSelect(task.id),
    });
    const svg = chart.querySelector('svg');
    svg?.setAttribute('role', 'img');
    svg?.setAttribute('aria-label', `Program schedule with ${tasks.length} dated tasks and dependency arrows`);
    const details = document.createElement('details'); details.className = 'scientific-visual-fallback';
    const summary = document.createElement('summary'); summary.textContent = 'Accessible schedule table';
    const table = document.createElement('table');
    const head = table.createTHead().insertRow();
    for (const label of ['Task', 'Start', 'Finish', 'Owner', 'Depends on']) {
        const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = label; head.append(cell);
    }
    const body = table.createTBody();
    for (const item of scheduled) {
        const row = body.insertRow();
        const action = document.createElement('button'); action.type = 'button';
        action.textContent = `${item.key} · ${item.title}`; action.addEventListener('click', () => onSelect(item.id));
        const taskCell = row.insertCell(); taskCell.append(action);
        for (const value of [item.start!, item.end!, item.owner,
            item.dependencyIds.length ? item.dependencyIds.join(', ') : '—']) row.insertCell().textContent = value;
    }
    details.append(summary, table); root.append(details);
    const unscheduled = items.filter(item => !item.start || !item.end);
    if (unscheduled.length) {
        const queue = document.createElement('section'); queue.className = 'program-unscheduled';
        const heading = document.createElement('strong'); heading.textContent = `Unscheduled · ${unscheduled.length}`;
        const list = document.createElement('div');
        for (const item of unscheduled) {
            const action = document.createElement('button'); action.type = 'button';
            action.textContent = `${item.key} · ${item.title}`;
            action.addEventListener('click', () => onSelect(item.id)); list.append(action);
        }
        queue.append(heading, list); root.append(queue);
    }
}

export function renderLaneLoadChart(root: HTMLElement, items: readonly WorkVisualItem[],
    onLaneSelect?: (lane: WorkLane) => void): void {
    chartObservers.get(root)?.disconnect(); getInstanceByDom(root)?.dispose(); root.replaceChildren();
    if (!items.length) {
        const empty = document.createElement('p'); empty.className = 'scientific-visual-empty';
        empty.textContent = 'No work exists to summarize.'; root.append(empty); return;
    }
    const series = laneLoadSeries(items);
    const chart: EChartsType = init(root, undefined, { renderer: 'svg' });
    chart.setOption({
        animation: false, aria: { enabled: true, decal: { show: true } },
        grid: { top: 18, right: 12, bottom: 48, left: 38 },
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        legend: { bottom: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 9 } },
        xAxis: { type: 'category', data: series.lanes,
            axisLabel: { color: '#5f5e58', fontSize: 9, interval: 0 } },
        yAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#5f5e58', fontSize: 9 },
            splitLine: { lineStyle: { color: '#deddd7', type: 'dotted' } } },
        series: series.statuses.map(status => ({
            name: humanize(status), type: 'bar', stack: 'work', barMaxWidth: 34,
            data: series.values[status], emphasis: { focus: 'series' },
            itemStyle: { color: ({ active: '#292925', ready: '#557287', backlog: '#8b887e',
                blocked: '#8f1f25', done: '#36724b', planned: '#b8b6ae' } as Record<string, string>)[status]
                || '#716a80' },
        })),
    });
    chart.on('click', params => {
        const index = typeof params.dataIndex === 'number' ? params.dataIndex : -1;
        const lane = WORK_LANES[index]; if (lane && onLaneSelect) onLaneSelect(lane);
    });
    replaceObserver(chartObservers, root, () => chart.resize());
}

export function renderKindDistribution(root: HTMLElement, model: ScientificGraphModel): void {
    chartObservers.get(root)?.disconnect(); getInstanceByDom(root)?.dispose(); root.replaceChildren();
    const values = graphKindSeries(model);
    if (!values.length) {
        const empty = document.createElement('p'); empty.className = 'scientific-visual-empty';
        empty.textContent = 'No canonical objects are available to summarize.'; root.append(empty); return;
    }
    const chart = init(root, undefined, { renderer: 'svg' });
    chart.setOption({
        animation: false, aria: { enabled: true, decal: { show: true } },
        grid: { top: 8, left: 104, right: 16, bottom: 18 }, tooltip: { trigger: 'item' },
        xAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#deddd7', type: 'dotted' } } },
        yAxis: { type: 'category', data: values.map(item => item.name).reverse(),
            axisLabel: { fontSize: 9, color: '#5f5e58', width: 92, overflow: 'truncate' } },
        series: [{ type: 'bar', data: values.map(item => item.value).reverse(), barMaxWidth: 18,
            itemStyle: { color: '#292925' }, label: { show: true, position: 'right', fontSize: 9 } }],
    });
    replaceObserver(chartObservers, root, () => chart.resize());
}
