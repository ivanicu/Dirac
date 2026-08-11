/**
 * Bond Atlas facet — the second depiction in the Ligand tab.
 *
 * The panel already renders RDKit's own SVG, which spends its channels the
 * conventional way: element on the label, bond order on the line count, and nothing
 * else anywhere. This draws the same molecule from scratch so the BOND can carry the
 * rest — ring context, torsional freedom, swing mass, connectivity dependence, and
 * the molecule's most balanced disconnection — which is the orthogonal-channel rule
 * in Dirac's own NORTHSTAR applied to the half of the diagram that never got one.
 *
 * Every colour is a Dirac CSS variable rather than a literal, so the atlas follows
 * whatever theme is mounted instead of carrying its own.
 */

import { computeBondAtlas, BondAtlas, BondAtlasBond } from '../../../chemistry.backend.perception.rdkit-wasm.editable/bond-graph';

const NS = 'http://www.w3.org/2000/svg';
/** A structure diagram has a comfortable bond length. Shrink to fit; never magnify past it. */
const NATURAL_BOND_PX = 46;

/* design/tokens.css is the single source of visual truth (DESIGN.md §2: a theme is a token
   swap and nothing else), and ring context is a CATEGORICAL data-viz channel, so it takes the
   sanctioned categorical ramp rather than semantic UI colours. --high and --unavail, which the
   first version of this file used, belong to the app's pre-tokens inline block. */
/* design/tokens.css is canonical (DESIGN.md §2) and carries the sanctioned categorical ramp,
   but the app still runs on its pre-tokens inline :root block — the migration is architecture
   phase P2. Naming the canonical token FIRST with the app's current token as the fallback means
   the atlas is correct today and becomes correct-by-the-spec the day tokens.css is mounted,
   with no second edit. Referencing --viz-cat-* alone rendered the bonds nearly invisible in the
   Chamber theme, because an unresolved var() is not a colour. */
const RING_COLOR = (b: BondAtlasBond) =>
    b.aromatic ? 'var(--viz-cat-1, var(--accent))'
        : b.ringSize === null ? 'var(--text-3)'
            : b.ringSize <= 5 ? 'var(--viz-cat-6, var(--warn))'
                : b.ringSize === 6 ? 'var(--viz-cat-3, var(--text-2))'
                    : 'var(--viz-cat-2, var(--unavail))';

const SYM_COLORS = [
    'var(--viz-cat-3, var(--warn))', 'var(--viz-cat-2, var(--accent))',
    'var(--viz-cat-4, var(--unavail))', 'var(--viz-cat-5, var(--high))',
];

let state: { atlas: BondAtlas | null, channels: Record<string, boolean> } = {
    atlas: null,
    channels: { order: true, ring: true, swing: true, rot: true, bridge: true, waist: true, sym: true, hb: true },
};

const el = (tag: string, attrs: Record<string, string | number> = {}) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
    return n;
};

function draw(host: HTMLElement) {
    const m = state.atlas;
    host.textContent = '';
    if (!m || !m.atoms.length) {
        const p = document.createElement('p');
        p.className = 'ledger-empty';
        p.textContent = 'No ligand loaded — the atlas needs a molecule.';
        host.appendChild(p);
        return;
    }

    const PAD = 24;
    const xs = m.atoms.map(a => a.x), ys = m.atoms.map(a => a.y);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const molBond = (() => {
        const ls = m.bonds.map(b => Math.hypot(m.atoms[b.a].x - m.atoms[b.b].x, m.atoms[b.a].y - m.atoms[b.b].y))
            .filter(v => v > 1e-6).sort((p, q) => p - q);
        return ls.length ? ls[Math.floor(ls.length / 2)] : 1.5;
    })();

    // The frame follows the molecule, and the scale is capped at the natural bond length:
    // a fixed canvas letterboxes a wide molecule, and filling it magnifies a small one until
    // six atoms fill the panel with girder-thick bonds.
    const MAX_W = 340, MAX_H = 300;
    let sc = Math.min((MAX_W - 2 * PAD) / Math.max(0.5, x1 - x0), (MAX_H - 2 * PAD) / Math.max(0.5, y1 - y0));
    sc = Math.min(sc, NATURAL_BOND_PX / molBond);
    const W = Math.max(120, Math.round((x1 - x0) * sc + 2 * PAD));
    const H = Math.max(110, Math.round((y1 - y0) * sc + 2 * PAD));
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const X = (v: number) => W / 2 + (v - cx) * sc;
    const Y = (v: number) => H / 2 + (v - cy) * sc;

    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H });
    (svg as SVGElement).setAttribute('class', 'bond-atlas-svg');

    // Widths are a fraction of this molecule's own on-screen bond length. A stroke in fixed
    // pixels reads as hairline on a small molecule and heavy on a large one, which destroys
    // exactly the cross-molecule comparison an absolute swing scale is for.
    const wUnit = Math.max(0.45, (molBond * sc) / 40);
    const maxSwing = Math.max(1, m.nHeavy / 2);
    const on = (k: string) => state.channels[k];

    for (const b of m.bonds) {
        const A = m.atoms[b.a], B = m.atoms[b.b];
        const [ax, ay, bx, by] = [X(A.x), Y(A.y), X(B.x), Y(B.y)];
        const L = Math.hypot(bx - ax, by - ay) || 1;
        const nx = -(by - ay) / L, ny = (bx - ax) / L;
        const w = (on('swing') ? 1.4 + 4.4 * (b.swing / maxSwing) : 2.1) * wUnit;

        if (on('bridge') && b.bridge) {
            svg.appendChild(el('line', {
                x1: ax, y1: ay, x2: bx, y2: by, stroke: 'var(--danger, var(--high))',
                'stroke-width': w + 6 * wUnit, 'stroke-opacity': 0.18, 'stroke-linecap': 'round',
            }));
        }
        const nLines = on('order') ? Math.min(3, b.order) : 1;
        for (let k = 0; k < nLines; k++) {
            const off = (k - (nLines - 1) / 2) * 4.2 * wUnit;
            const ln = el('line', {
                x1: ax + nx * off, y1: ay + ny * off, x2: bx + nx * off, y2: by + ny * off,
                stroke: on('ring') ? RING_COLOR(b) : 'var(--text-2)',
                'stroke-width': w, 'stroke-linecap': 'round',
            });
            if (on('rot') && b.rotatable) ln.setAttribute('stroke-dasharray', `${5 * wUnit} ${3 * wUnit}`);
            ln.appendChild(titleFor(b, m));
            svg.appendChild(ln);
        }
        if (on('waist') && b.k === m.waist && b.split) {
            svg.appendChild(el('circle', {
                cx: (ax + bx) / 2, cy: (ay + by) / 2, r: 9 * wUnit, fill: 'none',
                stroke: 'var(--danger, var(--high))', 'stroke-width': 1.2, 'stroke-dasharray': '3 2',
            }));
        }
    }

    for (const a of m.atoms) {
        const [x, y] = [X(a.x), Y(a.y)];
        // Equivalence is drawn as the EXCEPTION. Marking all of them turns a structure into a
        // graph diagram and buries the thing worth acting on.
        const twinned = on('sym') && a.symSize > 1;
        const show = a.el !== 'C' || twinned;
        const r = (a.el === 'C' ? 6 : 8.5) * Math.min(1.6, Math.max(0.8, wUnit));
        if (on('hb') && (a.donor || a.acceptor)) {
            svg.appendChild(el('circle', {
                cx: x, cy: y, r: r + 4, fill: 'none', 'stroke-width': 1.4,
                stroke: a.donor && a.acceptor ? 'var(--viz-cat-4, var(--unavail))' : a.donor ? 'var(--info, var(--accent))' : 'var(--danger, var(--high))',
            }));
        }
        if (show) {
            svg.appendChild(el('circle', {
                cx: x, cy: y, r, fill: 'var(--surface)',
                stroke: twinned ? SYM_COLORS[a.symClass % SYM_COLORS.length] : 'none',
                'stroke-width': twinned ? 2 : 0,
            }));
            const t = el('text', {
                x, y: y + 3.4, 'text-anchor': 'middle', 'font-size': 9.5,
                fill: twinned ? SYM_COLORS[a.symClass % SYM_COLORS.length] : 'var(--text)',
            });
            t.textContent = a.el;
            svg.appendChild(t);
        }
        if (a.charge) {
            const t = el('text', { x: x + r + 1, y: y - r + 3, 'font-size': 8, fill: 'var(--danger, var(--high))' });
            t.textContent = a.charge > 0 ? `+${a.charge}` : `${a.charge}`;
            svg.appendChild(t);
        }
    }
    host.appendChild(svg);
}

function titleFor(b: BondAtlasBond, m: BondAtlas) {
    const t = document.createElementNS(NS, 'title');
    const A = m.atoms[b.a], B = m.atoms[b.b];
    t.textContent = [
        `${A.el}${b.a}–${B.el}${b.b}`,
        `order ${b.order}${b.aromatic ? ' (aromatic)' : ''}`,
        b.ringSize === null ? 'not in a ring' : `smallest ring of ${b.ringSize}`,
        b.rotatable ? 'rotatable' : 'rigid',
        b.bridge && b.split ? `load-bearing: cutting gives ${b.split[0]} + ${b.split[1]} atoms` : 'not load-bearing',
        b.swing ? `${b.swing} of ${m.nHeavy} heavy atoms swing` : 'nothing swings',
    ].join(' · ');
    return t;
}

function renderReadout(host: HTMLElement) {
    const m = state.atlas;
    if (!m) { host.textContent = ''; return; }
    const c = m.counts;
    host.innerHTML = [
        [m.nHeavy, 'heavy atoms'], [m.bonds.length, 'bonds'], [c.bridges, 'load-bearing'],
        [c.rotatable, 'free torsions'], [c.symClasses, 'distinct positions'],
    ].map(([v, k]) => `<span class="bond-atlas-stat"><b>${v}</b>${k}</span>`).join('');
}

/**
 * Each row draws the ink it controls.
 *
 * The prototype kept a colour key in a band under the drawing while the toggles lived in a
 * list beside it, so reading the encoding and operating it were two different places on
 * screen. DESIGN.md §1 already asks for the opposite — "the decoder ships with the
 * encoding" — and the fix costs a 46px inline SVG per row: the menu becomes the legend and
 * the band it needed becomes drawing area.
 */
const CHANNEL_LABELS: [string, string, string][] = [
    ['ring', 'ring context', `<svg width="42" height="12">${[['var(--text-3)', 0], ['var(--viz-cat-6, var(--warn))', 1], ['var(--viz-cat-3, var(--text-2))', 2], ['var(--viz-cat-1, var(--accent))', 3]].map(([c, i]) => `<rect x="${(i as number) * 11}" y="3" width="9" height="6" fill="${c}"/>`).join('')}</svg>`],
    ['swing', 'swing mass', '<svg width="42" height="12"><line x1="1" y1="6" x2="18" y2="6" stroke="var(--text-2)" stroke-width="1.2"/><line x1="24" y1="6" x2="41" y2="6" stroke="var(--text-2)" stroke-width="4.5"/></svg>'],
    ['rot', 'rotatable', '<svg width="42" height="12"><line x1="1" y1="6" x2="41" y2="6" stroke="var(--text-2)" stroke-width="2.2" stroke-dasharray="5 3"/></svg>'],
    ['bridge', 'load-bearing', '<svg width="42" height="12"><line x1="1" y1="6" x2="41" y2="6" stroke="var(--danger, var(--high))" stroke-width="8" stroke-opacity=".22"/><line x1="1" y1="6" x2="41" y2="6" stroke="var(--text-2)" stroke-width="2.2"/></svg>'],
    ['waist', 'waist', '<svg width="42" height="12"><line x1="1" y1="6" x2="41" y2="6" stroke="var(--text-2)" stroke-width="2"/><circle cx="21" cy="6" r="5" fill="none" stroke="var(--danger, var(--high))" stroke-dasharray="2 2"/></svg>'],
    ['sym', 'equivalence', `<svg width="42" height="12">${['var(--viz-cat-3, var(--warn))', 'var(--viz-cat-2, var(--accent))', 'var(--viz-cat-4, var(--unavail))'].map((c, i) => `<circle cx="${7 + i * 13}" cy="6" r="4.6" fill="var(--surface)" stroke="${c}" stroke-width="2"/>`).join('')}</svg>`],
    ['hb', 'donor/acceptor', '<svg width="42" height="12"><circle cx="12" cy="6" r="4.6" fill="none" stroke="var(--info, var(--accent))" stroke-width="1.5"/><circle cx="29" cy="6" r="4.6" fill="none" stroke="var(--danger, var(--high))" stroke-width="1.5"/></svg>'],
];

/** Called once, after the Ligand panel exists. */
export function initBondAtlas() {
    const controls = document.getElementById('bond-atlas-channels');
    if (!controls || controls.dataset.ready) return;
    controls.dataset.ready = 'true';
    controls.innerHTML = CHANNEL_LABELS.map(([k, label, sample]) =>
        `<label class="bond-atlas-channel"><input type="checkbox" data-channel="${k}" checked>` +
        `<span class="bond-atlas-swatch">${sample}</span><span>${label}</span></label>`).join('');
    controls.querySelectorAll<HTMLInputElement>('input[data-channel]').forEach(input => {
        input.addEventListener('change', () => {
            state.channels[input.dataset.channel!] = input.checked;
            const host = document.getElementById('bond-atlas');
            if (host) draw(host);
        });
    });
}

/**
 * Called from the same lifecycle point that refreshes the 2D depiction. `molfile` is the
 * molfile the depiction already built, so the atlas and the depiction always describe the
 * same molecule — no second reconstruction, no second chance to disagree.
 */
export async function updateBondAtlas(molfile: string | null) {
    const host = document.getElementById('bond-atlas');
    const readout = document.getElementById('bond-atlas-readout');
    if (!host) return;
    if (!molfile) {
        state.atlas = null;
        draw(host);
        if (readout) renderReadout(readout);
        return;
    }
    try {
        state.atlas = await computeBondAtlas(molfile);
    } catch (error) {
        console.error('bond atlas failed', error);
        state.atlas = null;
    }
    draw(host);
    if (readout) renderReadout(readout);
}
