import cytoscape, { type Core, type ElementDefinition, type LayoutOptions } from 'cytoscape';
import Gantt, { type GanttTask } from 'frappe-gantt';
import { use, init, connect, type EChartsType } from 'echarts/core';
import { BarChart, HeatmapChart, PieChart, TreemapChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent, AriaComponent, ToolboxComponent,
    DataZoomComponent, VisualMapComponent } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
import { graphKindSeries, laneLoadSeries, WORK_LANES,
    criticalPathIds, scheduleConflicts,
    type ScientificGraphModel, type ScientificGraphNode,
    type WorkLane, type WorkVisualItem } from './visual-models';
export { graphKindSeries, laneLoadSeries, programRelationGraph, toWorkVisualItems,
    workGraphModel, WORK_LANES } from './visual-models';
export type { ScientificGraphEdge, ScientificGraphModel, ScientificGraphNode,
    WorkLane, WorkVisualItem } from './visual-models';

use([BarChart, HeatmapChart, PieChart, TreemapChart, GridComponent, LegendComponent,
    TooltipComponent, AriaComponent, ToolboxComponent, DataZoomComponent,
    VisualMapComponent, SVGRenderer]);

const graphInstances = new WeakMap<HTMLElement, Core>();
const graphObservers = new WeakMap<HTMLElement, ResizeObserver>();
const chartObservers = new WeakMap<HTMLElement, ResizeObserver>();
const chartInstances = new WeakMap<HTMLElement, EChartsType>();
const graphFilterListeners = new WeakMap<HTMLElement, EventListener>();

const humanize = (value: string): string => value.replace(/_/g, ' ');
const localDateKey = (value: Date): string => [value.getFullYear(),
    String(value.getMonth() + 1).padStart(2, '0'), String(value.getDate()).padStart(2, '0')].join('-');
const safeStorage = {
    read(key: string): string | undefined { try { return localStorage.getItem(key) || undefined; } catch { return undefined; } },
    write(key: string, value: string): void { try { localStorage.setItem(key, value); } catch { /* storage is optional */ } },
};

function download(name: string, content: string, type: string): void {
    const anchor = document.createElement('a'); anchor.download = name;
    anchor.href = content.startsWith('data:') ? content : URL.createObjectURL(new Blob([content], { type }));
    anchor.click(); if (!content.startsWith('data:')) URL.revokeObjectURL(anchor.href);
}

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
        readonly preset?: boolean; readonly storageKey?: string }): void {
    graphInstances.get(root)?.destroy(); graphObservers.get(root)?.disconnect();
    const previousFilter = graphFilterListeners.get(root);
    if (previousFilter) root.removeEventListener('dirac:graph-filter', previousFilter);
    root.replaceChildren();
    if (!model.nodes.length) {
        const empty = document.createElement('p'); empty.className = 'scientific-visual-empty';
        empty.textContent = 'No traceable nodes are available for this view.'; root.append(empty); return;
    }
    const controls = document.createElement('div'); controls.className = 'scientific-visual-controls scientific-graph-controls';
    const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Find object';
    search.setAttribute('aria-label', 'Find a graph object');
    const kind = document.createElement('select'); kind.setAttribute('aria-label', 'Filter object kind');
    kind.add(new Option('All object kinds', ''));
    for (const value of [...new Set(model.nodes.map(node => node.kind))].sort()) kind.add(new Option(humanize(value), value));
    const pathFrom = document.createElement('select'); pathFrom.setAttribute('aria-label', 'Path start object');
    const pathTo = document.createElement('select'); pathTo.setAttribute('aria-label', 'Path end object');
    pathFrom.add(new Option('Path from…', '')); pathTo.add(new Option('Path to…', ''));
    for (const node of model.nodes) {
        pathFrom.add(new Option(node.label, node.id)); pathTo.add(new Option(node.label, node.id));
    }
    const layout = document.createElement('select'); layout.setAttribute('aria-label', 'Graph layout');
    for (const [value, label] of [['preset', 'Stage'], ['cose', 'Force'], ['breadthfirst', 'Hierarchy'],
        ['concentric', 'Concentric'], ['circle', 'Circle'], ['grid', 'Grid']]) layout.add(new Option(label, value));
    layout.value = options.preset === false ? 'cose' : 'preset';
    const action = (label: string, command: string) => {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
        button.dataset.graphCommand = command; return button;
    };
    const fit = action('Fit', 'fit'); const neighbors = action('Neighbors', 'neighbors');
    const path = action('Trace path', 'path'); const rank = action('Rank', 'rank');
    const reset = action('Show all', 'reset'); const save = action('Save view', 'save');
    const png = action('PNG', 'png'); const json = action('JSON', 'json');
    const status = document.createElement('span'); status.className = 'scientific-control-status'; status.setAttribute('role', 'status');
    controls.append(search, kind, pathFrom, pathTo, layout, fit, neighbors, path, rank, reset, save, png, json, status);
    const canvas = document.createElement('div'); canvas.className = 'scientific-graph-canvas';
    canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', options.ariaLabel);
    root.append(controls, canvas, accessibleGraphList(model, options.onSelect));
    const cy = cytoscape({
        container: canvas, elements: graphElements(model),
        layout: options.preset === false ? { name: 'cose', animate: false, fit: true, padding: 28 }
            : { name: 'preset', fit: true, padding: 32 },
        minZoom: .25, maxZoom: 2.2, selectionType: 'additive', boxSelectionEnabled: true,
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
        if (!pathFrom.value) pathFrom.value = event.target.id();
        else if (!pathTo.value && pathFrom.value !== event.target.id()) pathTo.value = event.target.id();
        if (node && options.onSelect) options.onSelect(node);
    });
    const saved = options.storageKey ? safeStorage.read(`dirac:graph:${options.storageKey}`) : undefined;
    if (saved) try {
        const state = JSON.parse(saved) as { layout?: string; positions?: Record<string, { x: number; y: number }> };
        for (const [id, position] of Object.entries(state.positions || {})) cy.$id(id).position(position);
        if (state.layout) layout.value = state.layout;
    } catch { /* an invalid personal view never blocks the scientific graph */ }
    const applyVisibility = () => {
        const query = search.value.trim().toLowerCase(); const selectedKind = kind.value;
        cy.nodes().forEach(node => {
            const visible = (!selectedKind || node.data('kind') === selectedKind)
                && (!query || String(node.data('label')).toLowerCase().includes(query));
            node.style('display', visible ? 'element' : 'none');
        });
        cy.edges().forEach(edge => {
            edge.style('display', edge.source().visible() && edge.target().visible() ? 'element' : 'none');
        });
        const visible = cy.nodes(':visible').length; status.textContent = `${visible}/${model.nodes.length} objects`;
    };
    search.addEventListener('input', applyVisibility); kind.addEventListener('change', applyVisibility);
    const externalFilter: EventListener = event => {
        const value = (event as CustomEvent<{ kind?: string }>).detail?.kind || '';
        kind.value = value; applyVisibility(); cy.fit(cy.elements(':visible'), 28);
        status.textContent = value ? `Cross-filtered to ${humanize(value)}` : 'Cross-filter cleared';
    };
    root.addEventListener('dirac:graph-filter', externalFilter); graphFilterListeners.set(root, externalFilter);
    layout.addEventListener('change', () => {
        cy.elements().style('display', 'element'); search.value = ''; kind.value = '';
        cy.layout({ name: layout.value, animate: false, fit: true, padding: 28 } as LayoutOptions).run();
        status.textContent = `${layout.selectedOptions[0].text} layout`;
    });
    controls.addEventListener('click', event => {
        const command = (event.target as HTMLElement).closest<HTMLButtonElement>('[data-graph-command]')?.dataset.graphCommand;
        if (!command) return;
        if (command === 'fit') cy.fit(cy.elements(':visible'), 28);
        if (command === 'reset') {
            cy.elements().style('display', 'element'); cy.elements().removeClass('graph-emphasis');
            search.value = ''; kind.value = ''; pathFrom.value = ''; pathTo.value = '';
            cy.fit(undefined, 28); status.textContent = 'Full graph restored';
        }
        if (command === 'neighbors') {
            const selected = cy.nodes(':selected');
            if (!selected.length) { status.textContent = 'Select an object first'; return; }
            const visible = selected.closedNeighborhood(); cy.elements().style('display', 'none'); visible.style('display', 'element');
            cy.fit(visible, 36); status.textContent = `${visible.nodes().length} neighboring objects`;
        }
        if (command === 'path') {
            const selected = cy.nodes(':selected'); const from = pathFrom.value ? cy.$id(pathFrom.value) : selected[0];
            const to = pathTo.value ? cy.$id(pathTo.value) : selected[1];
            if (!from?.length || !to?.length || from.id() === to.id()) {
                status.textContent = 'Choose two different path endpoints'; return;
            }
            const result = cy.elements().dijkstra({ root: from, directed: false }).pathTo(to);
            cy.elements().removeClass('graph-emphasis'); result.addClass('graph-emphasis'); cy.fit(result, 48);
            status.textContent = result.length ? `${result.nodes().length} objects on shortest path` : 'No connecting path';
        }
        if (command === 'rank') {
            const ranks = cy.elements().pageRank({});
            const top = cy.nodes().sort((a, b) => ranks.rank(b) - ranks.rank(a)).slice(0, 5);
            cy.elements().removeClass('graph-emphasis'); top.addClass('graph-emphasis'); cy.fit(top, 48);
            status.textContent = `Top connected: ${top.map(node => node.data('label')).join(' · ')}`;
        }
        if (command === 'save' && options.storageKey) {
            const positions = Object.fromEntries(cy.nodes().map(node => [node.id(), node.position()]));
            safeStorage.write(`dirac:graph:${options.storageKey}`, JSON.stringify({ layout: layout.value, positions }));
            status.textContent = 'Personal graph view saved';
        }
        if (command === 'png') download('dirac-program-graph.png', cy.png({ full: true, scale: 2 }), 'image/png');
        if (command === 'json') download('dirac-program-graph.json', JSON.stringify({ model, view: cy.json() }, null, 2), 'application/json');
    });
    cy.style().selector('.graph-emphasis').style({ 'border-color': '#c24842', 'border-width': 4,
        'line-color': '#c24842', 'target-arrow-color': '#c24842', 'z-index': 20 }).update();
    applyVisibility();
    graphInstances.set(root, cy);
    replaceObserver(graphObservers, root, () => { cy.resize(); cy.fit(undefined, 28); });
}

export function renderProgramGantt(root: HTMLElement, items: readonly WorkVisualItem[],
    options: { readonly onSelect: (id: string) => void; readonly onEdit: (id: string) => void;
        readonly onScheduleChange: (id: string, start: string, end: string) => void;
        readonly onProgressChange: (id: string, progress: number) => void;
        readonly storageKey?: string }): void {
    root.replaceChildren();
    const scheduled = items.filter(item => item.start && item.end);
    if (!scheduled.length) {
        const empty = document.createElement('p'); empty.className = 'program-gantt-empty';
        empty.textContent = items.length
            ? 'No invented bars: add a planned start and finish to place work on the schedule.'
            : 'Plan the first task to create the Program schedule.';
        root.append(empty); return;
    }
    const controls = document.createElement('div'); controls.className = 'scientific-visual-controls program-gantt-controls';
    const action = (label: string, command: string) => {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
        button.dataset.ganttCommand = command; return button;
    };
    const critical = action('Critical chain', 'critical'); const conflicts = action('Conflicts', 'conflicts');
    const baselines = action('Baselines', 'baselines'); const edit = action('Edit selected', 'edit');
    const svgExport = action('SVG', 'svg'); const jsonExport = action('JSON', 'json');
    const status = document.createElement('span'); status.className = 'scientific-control-status'; status.setAttribute('role', 'status');
    controls.append(critical, conflicts, baselines, edit, svgExport, jsonExport, status);
    const chart = document.createElement('div'); chart.className = 'program-frappe-gantt';
    const tasks: GanttTask[] = scheduled.map(item => ({
        id: item.id, name: `${item.key} · ${item.title}`, start: item.start!, end: item.end!,
        progress: item.progress,
        dependencies: item.dependencyIds.join(','),
        custom_class: `dirac-gantt-${item.status.replace(/[^a-z0-9_-]/gi, '-')}`,
        description: `${item.owner} · ${humanize(item.lane)} · ${humanize(item.status)}`,
    }));
    const persistedProgress = new Map(scheduled.map(item => [item.id, Math.round(item.progress)]));
    const pendingSchedules = new Map<string, { start: string; end: string }>();
    let scheduleTimer: ReturnType<typeof setTimeout> | undefined;
    const flushSchedules = () => {
        scheduleTimer = undefined;
        for (const [id, value] of pendingSchedules) options.onScheduleChange(id, value.start, value.end);
        pendingSchedules.clear();
    };
    const queueSchedule = (id: string, start: string, end: string) => {
        pendingSchedules.set(id, { start, end });
        if (scheduleTimer) clearTimeout(scheduleTimer);
        scheduleTimer = setTimeout(flushSchedules, 180);
    };
    const emitProgress = (id: string, progress: number) => {
        const normalized = Math.max(0, Math.min(100, Math.round(progress)));
        if (persistedProgress.get(id) === normalized) return;
        persistedProgress.set(id, normalized); options.onProgressChange(id, normalized);
    };
    const savedMode = options.storageKey ? safeStorage.read(`dirac:gantt:${options.storageKey}:mode`) : undefined;
    root.append(controls, chart);
    new Gantt(chart, tasks, {
        view_mode: savedMode || 'Week', view_mode_select: true,
        readonly: false, readonly_dates: false, readonly_progress: false,
        // Native expected-progress bars produce negative SVG widths for future work;
        // baseline variance below is evidence-backed and remains valid for future plans.
        show_expected_progress: false, auto_move_label: true, move_dependencies: false,
        today_button: true, popup_on: 'hover', infinite_padding: false, scroll_to: 'today',
        on_click: task => options.onSelect(task.id), on_double_click: task => options.onEdit(task.id),
        on_date_change: (task, start, end) => queueSchedule(task.id, localDateKey(start), localDateKey(end)),
        on_progress_change: (task, progress) => emitProgress(task.id, progress),
        on_view_change: mode => {
            const name = typeof mode === 'string' ? mode : (mode as { name?: string })?.name;
            if (name && options.storageKey) safeStorage.write(`dirac:gantt:${options.storageKey}:mode`, name);
            chart.dispatchEvent(new CustomEvent('dirac:gantt-view-change'));
        },
        popup: context => {
            context.set_title(context.task.name);
            context.set_subtitle(context.task.description || 'Canonical Program Work Item');
            context.set_details(`${context.task.start} → ${context.task.end} · ${Math.round(context.task.progress || 0)}% complete`);
            context.add_action('Open task', () => options.onEdit(context.task.id));
        },
    });
    const svg = chart.querySelector('svg');
    svg?.setAttribute('role', 'img');
    svg?.setAttribute('aria-label', `Program schedule with ${tasks.length} dated tasks and dependency arrows`);
    // Reconcile the rendered handle after mouseup. Frappe only emits progress_change
    // when its own SVG listener receives the release; overlays and edge releases can
    // otherwise leave a visible edit that was never persisted.
    const reconcileProgress = () => queueMicrotask(() => {
        for (const wrapper of chart.querySelectorAll<SVGGElement>('.bar-wrapper[data-id]')) {
            const bar = wrapper.querySelector<SVGRectElement>('.bar');
            const progress = wrapper.querySelector<SVGRectElement>('.bar-progress');
            if (!bar || !progress) continue;
            const width = Number(bar.getAttribute('width')); const done = Number(progress.getAttribute('width'));
            if (width > 0 && Number.isFinite(done)) emitProgress(wrapper.dataset.id!, Math.floor(done / width * 100));
        }
    });
    chart.addEventListener('mousedown', event => {
        if ((event.target as Element).closest('.handle.progress')) {
            document.addEventListener('mouseup', reconcileProgress, { once: true });
        }
    });
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
        action.textContent = `${item.key} · ${item.title}`; action.addEventListener('click', () => options.onSelect(item.id));
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
            action.addEventListener('click', () => options.onSelect(item.id)); list.append(action);
        }
        queue.append(heading, list); root.append(queue);
    }
    const criticalIds = new Set(criticalPathIds(scheduled)); const collisionList = scheduleConflicts(scheduled);
    const conflictIds = new Set(collisionList.flatMap(item => [item.first, item.second]));
    const baselineRows = scheduled.filter(item => item.baselineStart && item.baselineEnd);
    const baseline = document.createElement('section'); baseline.className = 'program-gantt-analysis'; baseline.hidden = true;
    baseline.append(document.createElement('strong'));
    baseline.firstElementChild!.textContent = `Baseline variance · ${baselineRows.length}`;
    const baselineList = document.createElement('div');
    for (const item of baselineRows) {
        const variance = Math.round((Date.parse(`${item.end}T00:00:00Z`) - Date.parse(`${item.baselineEnd}T00:00:00Z`)) / 86_400_000);
        const row = document.createElement('button'); row.type = 'button'; row.addEventListener('click', () => options.onSelect(item.id));
        row.textContent = `${item.key} · ${item.baselineStart} → ${item.baselineEnd} · ${variance > 0 ? '+' : ''}${variance}d finish variance`;
        baselineList.append(row);
    }
    if (!baselineRows.length) baselineList.textContent = 'No superseded dated plan exists; Dirac will not invent a baseline.';
    baseline.append(baselineList); root.append(baseline);
    let selectedId = '';
    chart.addEventListener('click', event => {
        selectedId = (event.target as Element).closest<SVGGElement>('.bar-wrapper')?.dataset.id || selectedId;
    });
    const mark = (ids: ReadonlySet<string>, className: string, enabled: boolean) => {
        for (const id of ids) chart.querySelector(`.bar-wrapper[data-id="${CSS.escape(id)}"]`)
            ?.classList.toggle(className, enabled);
    };
    let criticalOn = false; let conflictsOn = false;
    chart.addEventListener('dirac:gantt-view-change', () => queueMicrotask(() => {
        mark(criticalIds, 'dirac-gantt-critical', criticalOn);
        mark(conflictIds, 'dirac-gantt-conflict', conflictsOn);
    }));
    controls.addEventListener('click', event => {
        const command = (event.target as Element).closest<HTMLButtonElement>('[data-gantt-command]')?.dataset.ganttCommand;
        if (!command) return;
        if (command === 'critical') {
            criticalOn = !criticalOn; mark(criticalIds, 'dirac-gantt-critical', criticalOn);
            status.textContent = criticalOn ? `${criticalIds.size} tasks on the longest dated dependency chain` : 'Critical chain hidden';
        }
        if (command === 'conflicts') {
            conflictsOn = !conflictsOn; mark(conflictIds, 'dirac-gantt-conflict', conflictsOn);
            status.textContent = collisionList.length ? `${collisionList.length} owner schedule collisions` : 'No owner schedule collisions';
        }
        if (command === 'baselines') {
            baseline.hidden = !baseline.hidden;
            status.textContent = baseline.hidden ? 'Baselines hidden' : `${baselineRows.length} baselines shown`;
        }
        if (command === 'edit') selectedId ? options.onEdit(selectedId) : status.textContent = 'Select a task first';
        if (command === 'svg') {
            const svg = chart.querySelector('svg'); if (svg) download('dirac-program-schedule.svg',
                new XMLSerializer().serializeToString(svg), 'image/svg+xml');
        }
        if (command === 'json') download('dirac-program-schedule.json', JSON.stringify({ tasks: items,
            criticalPath: [...criticalIds], conflicts: collisionList }, null, 2), 'application/json');
    });
}

export function renderLaneLoadChart(root: HTMLElement, items: readonly WorkVisualItem[],
    onLaneSelect?: (lane: WorkLane) => void, storageKey = 'program'): void {
    chartObservers.get(root)?.disconnect(); chartInstances.get(root)?.dispose(); root.replaceChildren();
    if (!items.length) {
        const empty = document.createElement('p'); empty.className = 'scientific-visual-empty';
        empty.textContent = 'No work exists to summarize.'; root.append(empty); return;
    }
    const series = laneLoadSeries(items); const controls = document.createElement('div');
    controls.className = 'scientific-visual-controls scientific-chart-controls';
    const mode = document.createElement('select'); mode.setAttribute('aria-label', 'Work distribution chart type');
    mode.add(new Option('Stacked bars', 'bar')); mode.add(new Option('Status heatmap', 'heatmap'));
    mode.value = safeStorage.read(`dirac:chart:${storageKey}:work-mode`) || 'bar';
    const save = document.createElement('button'); save.type = 'button'; save.textContent = 'Save view';
    const json = document.createElement('button'); json.type = 'button'; json.textContent = 'JSON';
    const status = document.createElement('span'); status.className = 'scientific-control-status'; status.setAttribute('role', 'status');
    controls.append(mode, save, json, status);
    const canvas = document.createElement('div'); canvas.className = 'scientific-chart-canvas'; root.append(controls, canvas);
    const chart: EChartsType = init(canvas, undefined, { renderer: 'svg' }); chartInstances.set(root, chart);
    const colors: Record<string, string> = { active: '#292925', ready: '#557287', backlog: '#8b887e',
        blocked: '#8f1f25', done: '#36724b', planned: '#b8b6ae' };
    const render = () => {
        chart.clear(); const common = { animation: false, aria: { enabled: true, decal: { show: true } },
            toolbox: { show: true, right: 4, top: 0, feature: { saveAsImage: { name: 'dirac-work-distribution' },
                dataView: { readOnly: true }, dataZoom: {}, restore: {} } } };
        if (mode.value === 'heatmap') chart.setOption({ ...common,
            grid: { top: 42, right: 20, bottom: 40, left: 70 }, tooltip: {},
            xAxis: { type: 'category', data: series.lanes }, yAxis: { type: 'category', data: series.statuses.map(humanize) },
            visualMap: { min: 0, max: Math.max(1, ...Object.values(series.values).flat()), calculable: true,
                orient: 'horizontal', left: 'center', bottom: 0, inRange: { color: ['#f2f1ec', '#292925'] } },
            series: [{ type: 'heatmap', data: series.statuses.flatMap((state, y) =>
                series.values[state].map((value, x) => [x, y, value])), label: { show: true }, emphasis: { focus: 'self' } }],
        }); else chart.setOption({ ...common,
            grid: { top: 42, right: 12, bottom: 58, left: 38 },
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            legend: { bottom: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 9 } },
            dataZoom: [{ type: 'inside', xAxisIndex: 0 }, { type: 'slider', xAxisIndex: 0, height: 12, bottom: 28 }],
            xAxis: { type: 'category', data: series.lanes, axisLabel: { color: '#5f5e58', fontSize: 9, interval: 0 } },
            yAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#5f5e58', fontSize: 9 },
                splitLine: { lineStyle: { color: '#deddd7', type: 'dotted' } } },
            series: series.statuses.map(state => ({ name: humanize(state), type: 'bar', stack: 'work', barMaxWidth: 34,
                data: series.values[state], emphasis: { focus: 'series' }, itemStyle: { color: colors[state] || '#716a80' } })),
        });
        const savedOption = safeStorage.read(`dirac:chart:${storageKey}:work-option`);
        if (savedOption) try { chart.setOption(JSON.parse(savedOption)); } catch { /* personal view is optional */ }
        status.textContent = `${items.length} work items · ${series.statuses.length} observed states`;
    };
    render(); mode.addEventListener('change', () => { safeStorage.write(`dirac:chart:${storageKey}:work-mode`, mode.value); render(); });
    chart.on('click', params => {
        const tuple = Array.isArray(params.data) ? params.data : undefined;
        const index = tuple ? Number(tuple[0]) : typeof params.dataIndex === 'number' ? params.dataIndex : -1;
        const lane = WORK_LANES[index]; if (lane && onLaneSelect) onLaneSelect(lane);
    });
    save.addEventListener('click', () => {
        const option = chart.getOption() as { legend?: unknown; dataZoom?: unknown };
        safeStorage.write(`dirac:chart:${storageKey}:work-option`, JSON.stringify({ legend: option.legend, dataZoom: option.dataZoom }));
        status.textContent = 'Personal chart view saved';
    });
    json.addEventListener('click', () => download('dirac-work-distribution.json', JSON.stringify(series, null, 2), 'application/json'));
    chart.group = `dirac-${storageKey}`; connect(chart.group);
    replaceObserver(chartObservers, root, () => chart.resize());
}

export function renderKindDistribution(root: HTMLElement, model: ScientificGraphModel,
    storageKey = 'program', onKindSelect?: (kind: string) => void): void {
    chartObservers.get(root)?.disconnect(); chartInstances.get(root)?.dispose(); root.replaceChildren();
    const values = graphKindSeries(model);
    if (!values.length) {
        const empty = document.createElement('p'); empty.className = 'scientific-visual-empty';
        empty.textContent = 'No canonical objects are available to summarize.'; root.append(empty); return;
    }
    const controls = document.createElement('div'); controls.className = 'scientific-visual-controls scientific-chart-controls';
    const mode = document.createElement('select'); mode.setAttribute('aria-label', 'Object distribution chart type');
    for (const [value, label] of [['bar', 'Ranked bars'], ['pie', 'Composition'], ['treemap', 'Treemap']]) mode.add(new Option(label, value));
    mode.value = safeStorage.read(`dirac:chart:${storageKey}:kind-mode`) || 'bar';
    const json = document.createElement('button'); json.type = 'button'; json.textContent = 'JSON';
    const status = document.createElement('span'); status.className = 'scientific-control-status';
    controls.append(mode, json, status); const canvas = document.createElement('div'); canvas.className = 'scientific-chart-canvas';
    root.append(controls, canvas); const chart = init(canvas, undefined, { renderer: 'svg' }); chartInstances.set(root, chart);
    const render = () => {
        chart.clear(); const common = { animation: false, aria: { enabled: true, decal: { show: true } },
            tooltip: { trigger: 'item' }, toolbox: { right: 4, top: 0, feature: {
                saveAsImage: { name: 'dirac-object-distribution' }, dataView: { readOnly: true }, restore: {} } } };
        if (mode.value === 'pie') chart.setOption({ ...common, legend: { type: 'scroll', bottom: 0 },
            series: [{ type: 'pie', radius: ['34%', '67%'], data: values, label: { formatter: '{b} · {c}' },
                emphasis: { focus: 'self', scale: true } }] });
        else if (mode.value === 'treemap') chart.setOption({ ...common,
            series: [{ type: 'treemap', roam: true, nodeClick: 'zoomToNode', breadcrumb: { show: true },
                data: values, label: { show: true, formatter: '{b}\n{c}' } }] });
        else chart.setOption({ ...common, grid: { top: 42, left: 104, right: 22, bottom: 36 },
            dataZoom: [{ type: 'inside', yAxisIndex: 0 }],
            xAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#deddd7', type: 'dotted' } } },
            yAxis: { type: 'category', data: values.map(item => item.name).reverse(),
                axisLabel: { fontSize: 9, color: '#5f5e58', width: 92, overflow: 'truncate' } },
            series: [{ type: 'bar', data: values.map(item => item.value).reverse(), barMaxWidth: 18,
                itemStyle: { color: '#292925' }, label: { show: true, position: 'right', fontSize: 9 },
                emphasis: { focus: 'self' } }] });
        status.textContent = `${model.nodes.length} objects · ${values.length} kinds`;
    };
    render(); mode.addEventListener('change', () => { safeStorage.write(`dirac:chart:${storageKey}:kind-mode`, mode.value); render(); });
    chart.on('click', params => {
        const selected = String(params.name || ''); if (selected && onKindSelect) onKindSelect(selected.replace(/ /g, '_'));
    });
    json.addEventListener('click', () => download('dirac-object-distribution.json', JSON.stringify(values, null, 2), 'application/json'));
    chart.group = `dirac-${storageKey}`; connect(chart.group);
    replaceObserver(chartObservers, root, () => chart.resize());
}
