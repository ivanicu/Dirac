'use strict';
/**
 * ops/ops.js — the operations console's only script. Vanilla JS, no build
 * step, no framework, no CDN: this page has to load on a LAN with nothing
 * but a static file server, and a CDN stall on an offline LAN has already
 * cost an hour once on this project (DESIGN.md §5).
 *
 * Data contract: GET http://<host>:8901/admin/snapshot — one JSON object
 * shaped like backend/admin_queries.py's snapshot(): { queue, cache, stale,
 * producers, methods, blob_health, toolkits }. That route is being written
 * by another agent while this page is being built, so it may 404, may not
 * exist, or may answer in a shape this file has to guess at defensively —
 * see extractSnapshot() below. Everything here is read-only: there is no
 * fetch() in this file that is not a GET, and no button anywhere that
 * deletes anything. Deletion is bin/dirac-sweep, run by hand, on the box.
 */

// ── config ───────────────────────────────────────────────────────────────

// window.location.hostname, never a hardcoded 127.0.0.1 — a hardcoded
// loopback address reads as "backend offline" from every machine but the
// one that has it bound to loopback, and that exact bug has already
// happened on this project (task brief, verbatim).
const ADMIN_PORT = 8901;
const SNAPSHOT_URL = `http://${window.location.hostname}:${ADMIN_PORT}/admin/snapshot`;
const START_CMD = 'backend/env/bin/python backend/field_server.py';
const REFRESH_MS = 5000;
const FETCH_TIMEOUT_MS = 4000;

// ── state ────────────────────────────────────────────────────────────────

/** @type {'loading'|'ok'|'degraded'|'not_found'|'offline'|'error'} */
let connState = 'loading';
let lastGoodSnapshot = null;   // the last snapshot that parsed successfully
let lastGoodAt = null;         // Date of that snapshot
let lastAttemptError = '';     // human text for the current non-ok state
let lastHttpStatus = null;
let paused = false;
let inFlight = false;
let refreshTimer = null;
let tickTimer = null;

// ── DOM refs ─────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);
const connPill = $('conn-pill');
const connBanner = $('conn-banner');
const ageText = $('age-text');
const btnRefresh = $('btn-refresh');
const btnPause = $('btn-pause');
const btnThemeNight = $('btn-theme-night');
const btnThemeChamber = $('btn-theme-chamber');

$('foot-url').textContent = `GET ${SNAPSHOT_URL}`;

// ── formatters — tabular numerals + units on every number ──────────────────

function fmtInt(n) {
    if (n === null || n === undefined) return '—';
    return Number(n).toLocaleString('en-US');
}

function fmtNum(n, digits) {
    if (n === null || n === undefined) return '—';
    return Number(n).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// Same rounding convention as admin_queries.py's own _fmt_bytes(), so the
// web page and the 3-a.m.-no-browser CLI never disagree about what "47 MB"
// means for the same row.
function fmtBytes(n) {
    if (n === null || n === undefined) return '—';
    let v = Math.abs(Number(n));
    const sign = Number(n) < 0 ? '-' : '';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    const digits = i === 0 ? 0 : 1;
    return `${sign}${fmtNum(v, digits)}<span class="unit">${units[i]}</span>`;
}

function fmtSeconds(n, digits) {
    if (n === null || n === undefined) return '—';
    return `${fmtNum(n, digits === undefined ? 2 : digits)}<span class="unit">s</span>`;
}

// seconds -> "36m 12s" / "1h 05m" / "42s" — the human-readable companion to
// the raw number, never a replacement for it.
function fmtDuration(seconds) {
    if (seconds === null || seconds === undefined) return '—';
    let s = Math.round(Math.abs(Number(seconds)));
    const sign = Number(seconds) < 0 ? '-' : '';
    if (s < 60) return `${sign}${s}s`;
    const m = Math.floor(s / 60);
    const rs = s % 60;
    if (m < 60) return `${sign}${m}m ${String(rs).padStart(2, '0')}s`;
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return `${sign}${h}h ${String(rm).padStart(2, '0')}m`;
}

// psycopg's isoformat() carries microseconds + a UTC offset (~32 chars);
// slice to second precision — the exact [:19] admin_queries.py's own
// _fmt_ts() uses for the CLI, so the two surfaces read identically.
function fmtTs(iso) {
    if (!iso) return '—';
    return String(iso).slice(0, 19).replace('T', ' ');
}

function fmtAgeSince(date) {
    if (!date) return '—';
    const s = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
    if (s < 2) return 'just now';
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s ago`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m ago`;
}

function esc(s) {
    return String(s === null || s === undefined ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── snapshot shape guessing ──────────────────────────────────────────────
//
// admin_routes.py is being written concurrently with this page — the exact
// wrapping around admin_queries.snapshot() is unknown at the time this file
// is written. Accept: the raw snapshot dict; {ok:true, ...snapshot fields
// merged at top level} (the do_GET /health convention already used in
// field_server.py); or {ok:true, data:{...snapshot}}. Anything else is
// reported honestly as an unrecognised shape, never silently coerced into
// empty panels that look like "0 of everything, healthy".

function extractSnapshot(json) {
    if (!json || typeof json !== 'object') return null;
    const looksLikeSnapshot = (o) => o && typeof o === 'object' &&
        ('queue' in o || 'cache' in o || 'stale' in o || 'producers' in o ||
         'methods' in o || 'blob_health' in o || 'toolkits' in o);
    if (looksLikeSnapshot(json)) return json;
    if (looksLikeSnapshot(json.data)) return json.data;
    if (looksLikeSnapshot(json.snapshot)) return json.snapshot;
    return null;
}

/** First key present on `obj` from `keys`, in order; undefined if none. */
function pick(obj, keys) {
    if (!obj) return undefined;
    for (const k of keys) if (obj[k] !== undefined) return obj[k];
    return undefined;
}

// ── fetch engine ─────────────────────────────────────────────────────────

async function fetchSnapshot() {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    try {
        const res = await fetch(SNAPSHOT_URL, { signal: ctrl.signal, headers: { Accept: 'application/json' } });
        lastHttpStatus = res.status;
        let body = null;
        try { body = await res.json(); } catch (e) { body = null; }

        if (res.status === 404) {
            setState('not_found', 'The backend is up, but /admin/snapshot returns 404 — the admin route ' +
                'has not been wired up on this backend yet.');
            return;
        }
        if (res.status === 503) {
            setState('degraded', (body && body.error) ||
                'The backend process answered, but the database is unreachable (503).');
            return;
        }
        if (!res.ok) {
            setState('error', `Backend answered with HTTP ${res.status}` +
                (body && body.error ? `: ${body.error}` : '.'));
            return;
        }
        const snap = extractSnapshot(body);
        if (!snap) {
            setState('error', 'Backend answered 200 but the body did not look like a snapshot ' +
                '(expected a "queue"/"cache"/... key somewhere).');
            return;
        }
        if (body && body.ok === false) {
            setState('degraded', body.error || 'Backend reported ok:false.');
            return;
        }
        lastGoodSnapshot = snap;
        lastGoodAt = new Date();
        setState('ok', '');
        renderAll(snap);
    } catch (e) {
        lastHttpStatus = null;
        // AbortError (our own timeout) and TypeError (connection refused /
        // DNS failure / CORS) both mean the same thing to an operator: no
        // response came back from the backend.
        setState('offline', e && e.name === 'AbortError'
            ? `No response from ${SNAPSHOT_URL} within ${FETCH_TIMEOUT_MS / 1000}s.`
            : `Could not reach ${SNAPSHOT_URL} (${(e && e.message) || 'network error'}).`);
    } finally {
        clearTimeout(timer);
    }
}

function setState(state, message) {
    const changed = state !== connState || message !== lastAttemptError;
    connState = state;
    lastAttemptError = message;
    if (changed) console.log(`[ops] connection state -> ${state}${message ? ': ' + message : ''}`);
    renderConnUI();
    if (state !== 'ok') renderStaleBodies();
}

// ── connection UI (the four+ honest states, each visually distinct) ────

function renderConnUI() {
    const variants = {
        loading:   { cls: 'busy',  text: 'checking backend…' },
        ok:        { cls: 'ok',    text: null }, // text computed from data below
        degraded:  { cls: 'warn',  text: 'DEGRADED — db unreachable, backend up' },
        not_found: { cls: 'route', text: 'admin route not found (404)' },
        offline:   { cls: 'error', text: 'backend offline' },
        error:     { cls: 'error', text: 'backend error' },
    };
    const v = variants[connState] || variants.error;
    let text = v.text;

    if (connState === 'ok' && lastGoodSnapshot) {
        const q = Array.isArray(lastGoodSnapshot.queue) ? lastGoodSnapshot.queue : [];
        const overdue = q.filter((j) => j && j.overdue).length;
        if (overdue > 0) {
            connPill.className = 'pill error';
            text = `${overdue} job${overdue === 1 ? '' : 's'} overdue`;
        } else if (q.length === 0) {
            connPill.className = 'pill ok';
            text = 'healthy — 0 jobs running';
        } else {
            connPill.className = 'pill ok';
            text = `healthy — ${q.length} running`;
        }
    } else {
        connPill.className = `pill ${v.cls}`;
    }
    connPill.innerHTML = `<span>${esc(text)}</span>`;

    // Banner: only shown for non-ok states, or for ok states that are
    // currently serving STALE data because the most recent poll failed.
    if (connState === 'ok') {
        connBanner.style.display = 'none';
    } else {
        connBanner.style.display = 'flex';
        connBanner.className = 'conn-banner ' + (v.cls === 'error' ? 'error' : v.cls === 'warn' ? 'warn' : 'route');
        let extra = '';
        if (connState === 'offline') {
            extra = `<span class="start-cmd">${esc(START_CMD)}</span>`;
        }
        const staleNote = lastGoodSnapshot
            ? `<div style="margin-top:4px;color:var(--text-3)">Showing the last snapshot that loaded successfully, `
              + `${fmtAgeSince(lastGoodAt)}. It is not being refreshed while this holds.</div>`
            : `<div style="margin-top:4px;color:var(--text-3)">No snapshot has loaded successfully yet.</div>`;
        connBanner.innerHTML =
            `<div><b>${esc(v.text || 'Backend problem')}</b>${esc(lastAttemptError)}${extra}${staleNote}</div>`;
    }
}

function renderStaleBodies() {
    // If we have never had a good snapshot, every panel shows the
    // connection-appropriate placeholder instead of a skeleton forever.
    if (lastGoodSnapshot) return;
    const placeholders = {
        loading:   '<div class="skeleton" style="width:80%"></div>',
        offline:   `<p class="empty-neutral">No data — backend unreachable. Start it with <code>${esc(START_CMD)}</code>.</p>`,
        degraded:  '<p class="empty-neutral">No data — backend is up but the database is unreachable (503).</p>',
        not_found: '<p class="empty-neutral">No data — the /admin/snapshot route does not exist on this backend yet.</p>',
        error:     `<p class="empty-neutral">No data — ${esc(lastAttemptError)}</p>`,
    };
    const html = placeholders[connState] || placeholders.error;
    for (const id of ['body-queue', 'body-cache', 'body-stale', 'body-blobs', 'body-methods', 'body-producers', 'body-toolkits']) {
        $(id).innerHTML = html;
    }
}

// ── panel renderers ──────────────────────────────────────────────────────

function renderAll(snap) {
    renderQueue(snap.queue);
    renderCache(snap.cache);
    renderStale(snap.stale);
    renderBlobs(snap.blob_health);
    renderMethods(snap.methods);
    renderProducers(snap.producers);
    renderToolkits(snap.toolkits);
}

function renderQueue(rows) {
    rows = Array.isArray(rows) ? rows : [];
    if (rows.length === 0) {
        $('body-queue').innerHTML = '<p class="empty-ok">0 jobs running. No news is good news — this is the '
            + 'empty state, not an error.</p>';
        return;
    }
    const trs = rows.map((r) => {
        const overdue = !!r.overdue;
        const idShort = r.id ? String(r.id).slice(0, 8) : '—';
        return `<tr class="${overdue ? 'overdue' : ''}">
            <td><code title="${esc(r.id)}">${esc(idShort)}</code></td>
            <td>${esc(r.method)} <span class="muted">${esc(r.method_version || '')}</span></td>
            <td>${esc(r.state)}</td>
            <td class="muted">${esc(r.compound_id || '—')}</td>
            <td class="num">${fmtDuration(r.age_seconds)}</td>
            <td class="num">${fmtDuration(r.budget_seconds)}</td>
            <td>${overdue ? '<span class="badge overdue">overdue</span>' : ''}</td>
            <td class="muted">${esc(r.worker || '—')}</td>
            <td class="muted num">${fmtTs(r.created_at)}</td>
        </tr>`;
    }).join('');
    $('body-queue').innerHTML = `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Job</th><th>Method</th><th>State</th><th>Compound</th>
        <th>Age</th><th>Budget</th><th></th><th>Worker</th><th>Created</th></tr></thead>
        <tbody>${trs}</tbody></table></div>`;
}

function renderCache(cache) {
    if (!cache) { $('body-cache').innerHTML = '<p class="empty-neutral">No cache data.</p>'; return; }

    const rowsTotal = pick(cache, ['rows_total', 'total_rows']);
    const rowsServable = pick(cache, ['rows_servable']);
    const rowsProducerCurrent = pick(cache, ['rows_producer_current', 'rows_on_current_producer']);
    const producerGenerations = pick(cache, ['producer_generations']);
    const maxGenerationsPerUnit = pick(cache, ['max_generations_per_unit']);
    const totalBytes = pick(cache, ['total_bytes']);
    const byKind = Array.isArray(cache.by_kind) ? cache.by_kind : [];

    const stat = (label, value, danger, ok) =>
        `<div class="stat${danger ? ' danger' : ''}${ok ? ' ok' : ''}"><div class="l">${esc(label)}</div>
         <div class="v num">${value === undefined ? '<span class="unit">not reported</span>' : value}</div></div>`;

    const rowsServableNum = Number(rowsServable);
    const servableIsZero = rowsServable !== undefined && rowsTotal > 0 && rowsServableNum === 0;

    let html = '<div class="stat-row">';
    html += stat('rows_total', rowsTotal === undefined ? undefined : fmtInt(rowsTotal));
    html += stat('rows_servable', rowsServable === undefined ? undefined : fmtInt(rowsServable), servableIsZero, rowsServable !== undefined && !servableIsZero);
    html += stat('rows_producer_current', rowsProducerCurrent === undefined ? undefined : fmtInt(rowsProducerCurrent),
                 rowsProducerCurrent !== undefined && rowsTotal > 0 && Number(rowsProducerCurrent) === 0);
    html += stat('total bytes', totalBytes === undefined ? undefined : fmtBytes(totalBytes));
    html += '</div>';

    // The pairing item 2 explicitly asks for: producer_generations next to
    // max_generations_per_unit, with a one-line explanation of the gap.
    html += '<div class="pair-row">';
    html += stat('producer_generations', producerGenerations === undefined ? undefined : fmtInt(producerGenerations));
    html += stat('max_generations_per_unit', maxGenerationsPerUnit === undefined ? undefined : fmtInt(maxGenerationsPerUnit));
    if (producerGenerations !== undefined && maxGenerationsPerUnit !== undefined) {
        const ratio = Number(maxGenerationsPerUnit) > 0 ? (Number(producerGenerations) / Number(maxGenerationsPerUnit)) : null;
        html += `<div class="pair-explain"><b>${ratio ? fmtNum(ratio, 1) + '×' : '—'} gap.</b>
            Producer identity used to be the hash of the whole service file, so an unrelated one-line edit
            (a docstring, a log line) superseded every cached row even though no physics changed. A compute
            unit's own version only moves when code that can change the returned number changes — that is
            <code>max_generations_per_unit</code>, the churn a cached row actually has to survive.
            If this ratio ever collapses toward 1×, the granularity fix has stopped working.</div>`;
    } else {
        html += `<div class="pair-explain"><span class="unavailable">producer_generations / max_generations_per_unit
            not exposed by this backend's /admin/cache yet — admin_queries.py's cache_summary() does not
            currently read app.v_cache_health (see migration 010_cache_health_metric.sql, which defines exactly
            these two columns). Shown as unavailable rather than as 0, so an absent metric is never mistaken for
            a measured zero.</span></div>`;
    }
    html += '</div>';

    if (byKind.length) {
        const trs = byKind.map((k) => `<tr>
            <td>${esc(k.kind)}</td>
            <td class="num">${fmtInt(k.rows)}</td>
            <td class="num">${fmtInt(k.distinct_molecules)}</td>
            <td class="num">${fmtBytes(k.bytes)}</td>
        </tr>`).join('');
        html += `<div class="tbl-wrap"><table class="tbl">
            <thead><tr><th>Kind</th><th>Rows</th><th>Distinct molecules</th><th>Bytes</th></tr></thead>
            <tbody>${trs}</tbody></table></div>`;
    }

    $('body-cache').innerHTML = html;
}

function renderStale(rows) {
    rows = Array.isArray(rows) ? rows : [];
    if (rows.length === 0) {
        $('body-stale').innerHTML = '<p class="empty-ok">No superseded generations — nothing to sweep.</p>'
            + boundaryNote();
        return;
    }
    let totRows = 0, totBytes = 0, totSecs = 0, totBlocked = 0;
    const trs = rows.map((r) => {
        totRows += Number(r.rows_to_sweep) || 0;
        totBytes += Number(r.reclaimable_bytes) || 0;
        totSecs += Number(r.compute_seconds_represented) || 0;
        totBlocked += Number(r.blocked_by_job) || 0;
        return `<tr>
            <td>${esc(r.service)}</td>
            <td><code>${esc(r.producer_version)}</code></td>
            <td class="muted num">${fmtTs(r.superseded_at)}</td>
            <td class="num">${fmtInt(r.rows_to_sweep)}</td>
            <td class="num">${fmtBytes(r.reclaimable_bytes)}</td>
            <td class="num">${fmtSeconds(r.compute_seconds_represented)} <span class="muted">(${fmtDuration(r.compute_seconds_represented)})</span></td>
            <td class="num">${r.blocked_by_job ? `<span class="badge overdue">${fmtInt(r.blocked_by_job)} blocked</span>` : '0'}</td>
        </tr>`;
    }).join('');

    const html = `
        <div class="total-banner">
            <span class="v num">${fmtInt(totRows)}<span class="unit">rows</span></span>
            &nbsp;&middot;&nbsp;
            <span class="v num">${fmtBytes(totBytes)}</span> reclaimable
            &nbsp;&middot;&nbsp;
            <span class="v num">${fmtSeconds(totSecs)}</span> compute represented
            (${fmtDuration(totSecs)})
            &nbsp;&middot;&nbsp;
            <span class="num">${fmtInt(totBlocked)}</span> blocked by a live job
        </div>
        <div class="tbl-wrap"><table class="tbl">
            <thead><tr><th>Service</th><th>Version</th><th>Superseded at</th><th>Rows</th>
            <th>Reclaimable</th><th>Compute represented</th><th>Blocked</th></tr></thead>
            <tbody>${trs}</tbody></table></div>
        ${boundaryNote()}`;
    $('body-stale').innerHTML = html;
}

function boundaryNote() {
    return `<div class="boundary-note">This page has no delete button, on purpose. Deletion runs as
        <code>bin/dirac-sweep --apply</code>, by hand, on the box — shell access is the auth boundary,
        not a button an unauthenticated LAN client can reach. This panel exists to make the number, not to
        push it.</div>`;
}

function renderBlobs(h) {
    if (!h) { $('body-blobs').innerHTML = '<p class="empty-neutral">No blob data.</p>'; return; }
    const orphanCount = Number(h.orphan_count) || 0;
    const html = `<div class="stat-row">
        <div class="stat"><div class="l">total_blobs</div><div class="v num">${fmtInt(h.total_blobs)}</div></div>
        <div class="stat"><div class="l">total_bytes</div><div class="v num">${fmtBytes(h.total_bytes)}</div></div>
        <div class="stat${orphanCount ? ' danger' : ' ok'}"><div class="l">orphan_count</div>
            <div class="v num">${fmtInt(h.orphan_count)}</div></div>
        <div class="stat${orphanCount ? ' danger' : ' ok'}"><div class="l">orphan_bytes</div>
            <div class="v num">${fmtBytes(h.orphan_bytes)}</div></div>
    </div>
    ${orphanCount
        ? `<p class="panel-sub" style="color:var(--danger);margin-top:0">${fmtInt(orphanCount)} orphan blob(s) —
             residue from a two-statement write, not a normal steady-state count. Reclaimable by
             <code>bin/dirac-sweep --apply</code>.</p>`
        : `<p class="empty-ok">0 orphans — the blob store is clean.</p>`}`;
    $('body-blobs').innerHTML = html;
}

function renderMethods(rows) {
    rows = Array.isArray(rows) ? rows : [];
    if (rows.length === 0) { $('body-methods').innerHTML = '<p class="empty-neutral">No methods registered.</p>'; return; }
    const trs = rows.map((r) => {
        const refuses = Array.isArray(r.refuses) ? r.refuses : [];
        const tags = refuses.length
            ? refuses.map((x) => `<span class="tag">${esc(x)}</span>`).join('')
            : '<span class="tag-none">(none declared)</span>';
        return `<tr>
            <td>${esc(r.method_id)}</td>
            <td><code>${esc(r.version)}</code></td>
            <td>${esc(r.exec_class)}</td>
            <td>${r.current ? '<span class="badge current">current</span>' : `<span class="badge superseded">superseded ${fmtTs(r.superseded_at)}</span>`}</td>
            <td>${tags}</td>
        </tr>`;
    }).join('');
    $('body-methods').innerHTML = `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Method</th><th>Version</th><th>Exec class</th><th>Status</th><th>Refuses</th></tr></thead>
        <tbody>${trs}</tbody></table></div>`;
}

function renderProducers(rows) {
    rows = Array.isArray(rows) ? rows : [];
    if (rows.length === 0) { $('body-producers').innerHTML = '<p class="empty-neutral">No producers registered.</p>'; return; }
    const trs = rows.map((r) => `<tr>
        <td>${esc(r.service)}</td>
        <td><code>${esc(r.version)}</code></td>
        <td class="muted num">${fmtTs(r.declared_at)}</td>
        <td class="muted num">${r.superseded_at ? fmtTs(r.superseded_at) : '—'}</td>
        <td>${r.current ? '<span class="badge current">current</span>' : '<span class="badge superseded">superseded</span>'}</td>
        <td class="muted">${esc(r.notes || '')}</td>
    </tr>`).join('');
    $('body-producers').innerHTML = `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Service</th><th>Version</th><th>Declared</th><th>Superseded</th><th></th><th>Notes</th></tr></thead>
        <tbody>${trs}</tbody></table></div>`;
}

function renderToolkits(rows) {
    rows = Array.isArray(rows) ? rows : [];
    if (rows.length === 0) { $('body-toolkits').innerHTML = '<p class="empty-neutral">No toolkits recorded.</p>'; return; }
    const trs = rows.map((r) => `<tr>
        <td>${esc(r.name)}</td>
        <td><code>${esc(r.version)}</code></td>
        <td class="muted">${esc(r.build_note || '')}</td>
        <td class="muted num">${fmtTs(r.verified_at)}</td>
    </tr>`).join('');
    $('body-toolkits').innerHTML = `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Name</th><th>Version</th><th>Build note</th><th>Verified at</th></tr></thead>
        <tbody>${trs}</tbody></table></div>`;
}

// ── polling / pause / age clock ──────────────────────────────────────────

async function tickFetch() {
    if (inFlight) return;
    inFlight = true;
    try { await fetchSnapshot(); } finally { inFlight = false; }
}

function scheduleRefresh() {
    clearTimeout(refreshTimer);
    if (paused) return;
    refreshTimer = setTimeout(async () => { await tickFetch(); scheduleRefresh(); }, REFRESH_MS);
}

function tickAge() {
    // Runs every second regardless of pause state, on purpose: the whole
    // point of showing data age is that a frozen page cannot masquerade as
    // a calm system, whether it froze because the backend died or because
    // an operator paused it and forgot.
    if (lastGoodAt) {
        ageText.textContent = `updated ${fmtAgeSince(lastGoodAt)}`;
    } else if (connState === 'loading') {
        ageText.textContent = 'no data yet';
    } else {
        ageText.textContent = 'never updated';
    }
}

btnRefresh.addEventListener('click', () => { tickFetch(); });

btnPause.addEventListener('click', () => {
    paused = !paused;
    btnPause.setAttribute('aria-pressed', String(paused));
    btnPause.textContent = paused ? 'Resume' : 'Pause';
    $('interval-text').textContent = paused ? 'paused' : `every ${REFRESH_MS / 1000}s`;
    if (!paused) scheduleRefresh();
    else clearTimeout(refreshTimer);
});

// ── theme toggle — a token swap, nothing else (DESIGN.md §2) ────────────

function applyTheme(name) {
    if (name === 'chamber') document.documentElement.dataset.theme = 'chamber';
    else delete document.documentElement.dataset.theme;
    btnThemeNight.setAttribute('aria-pressed', String(name !== 'chamber'));
    btnThemeChamber.setAttribute('aria-pressed', String(name === 'chamber'));
    try { localStorage.setItem('dirac-ops-theme', name); } catch (e) { /* ignore */ }
}
btnThemeNight.addEventListener('click', () => applyTheme('night'));
btnThemeChamber.addEventListener('click', () => applyTheme('chamber'));
applyTheme(document.documentElement.dataset.theme === 'chamber' ? 'chamber' : 'night');

// ── init ─────────────────────────────────────────────────────────────────

$('interval-text').textContent = `every ${REFRESH_MS / 1000}s`;
renderConnUI();
renderStaleBodies();
tickAge();
tickTimer = setInterval(tickAge, 1000);
tickFetch().then(scheduleRefresh);

// A read-only debug hook, not a second code path: an operator poking at
// this from devtools (or a verification pass) exercises the exact same
// functions the poll loop calls, never a reimplementation of them.
window.__ops = { setState, renderAll, renderConnUI, renderStaleBodies, extractSnapshot, fetchSnapshot, SNAPSHOT_URL };
