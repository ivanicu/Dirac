/**
 * The host's front door.
 *
 * THE GENERATION IS DERIVED, NEVER DECLARED. A page that prints "V2" because a
 * constant somewhere says "V2" tells you what someone typed. This one probes the
 * service for the capabilities that DEFINE the generation and reports which are
 * present — so if a service is rolled back, or an older build is running on a
 * port nobody remembered, the page says V1 without anyone editing it. A version
 * string can lie; the presence of `qm_slots` in a health payload cannot.
 *
 * Same reasoning the field panel uses for chemistry: report the measurement and
 * the instrument, not a label.
 */
'use strict';

const HOST = window.location.hostname || '127.0.0.1';
const FIELDS = `http://${HOST}:8901`;
const PHYSICS = `http://${HOST}:8902`;
const WEB = `http://${HOST}:1338`;
const OPS = `http://${HOST}:1355`;

/** Each capability names the probe that decides it — the probe IS the definition. */
const CAPABILITIES = [
    { key: 'job ledger', probe: 'health.jobs.opened is a number',
      test: h => h && h.jobs && typeof h.jobs.opened === 'number' },
    { key: 'dedup / join', probe: 'health.jobs.joined exists',
      test: h => h && h.jobs && typeof h.jobs.joined === 'number' },
    { key: 'bounded concurrency', probe: 'health.qm_slots > 0',
      test: h => h && typeof h.qm_slots === 'number' && h.qm_slots > 0 },
    { key: 'persist accounting', probe: 'health.persist.{queued,ok,failed}',
      test: h => h && h.persist && typeof h.persist.failed === 'number' },
    { key: 'ops read surface', probe: 'GET /admin/snapshot returns ok:true',
      test: (h, s) => !!(s && s.ok) },
    { key: 'method-currency cache', probe: 'snapshot.cache.rows_servable exists',
      test: (h, s) => !!(s && s.data && s.data.cache
                         && typeof s.data.cache.rows_servable === 'number') },
];

const $ = id => document.getElementById(id);
const cell = (v, cls) => `<td class="${cls || 'v'}">${v}</td>`;

async function fetchJSON(url, ms = 6000) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), ms);
    try {
        const r = await fetch(url, { signal: ctl.signal });
        if (!r.ok) return { __error: `http ${r.status}` };
        return await r.json();
    } catch (e) {
        return { __error: String(e && e.message || e) };
    } finally { clearTimeout(t); }
}

/** Is something listening at all? A CORS-blocked response still proves a server
 *  answered, so a fetch that REJECTS is the only evidence of "down" — and the two
 *  must not be conflated, which is the whole reason the ops console exists. */
async function reachable(url) {
    try { await fetch(url, { mode: 'no-cors', cache: 'no-store' }); return true; }
    catch { return false; }
}

function renderGeneration(health, snap) {
    const rows = CAPABILITIES.map(c => ({ ...c, ok: !!c.test(health, snap) }));
    const have = rows.filter(r => r.ok).length;
    // V2 is not a name for "the newest thing"; it is the set of capabilities
    // below. Partial is reported as partial rather than rounded up, because a
    // host with half of them is exactly the state that produces a confident
    // wrong answer.
    const gen = have === rows.length ? 'V2' : (have === 0 ? 'V1' : `V1.${have}`);
    $('gen').textContent = gen;
    $('genwhy').innerHTML = have === rows.length
        ? `all ${rows.length} defining capabilities answered their probe. `
          + `This is derived from the service, not from a version string.`
        : `<span class="wait">${have} of ${rows.length}</span> capabilities present — `
          + `reported as partial rather than rounded up to V2, because a host with `
          + `half of them is the state that produces a confident wrong answer.`;
    $('caps').innerHTML = rows.map(r =>
        `<i class="${r.ok ? 'up' : 'down'}">${r.ok ? '✔' : '✕'}</i>`
        + `<span><b>${r.key}</b> <span class="p">— ${r.probe}</span></span>`).join('');
}

function renderServices(health, snap, physicsUp, webUp) {
    const fieldsUp = health && !health.__error;
    const rows = [
        ['dirac-fields', ':8901', fieldsUp ? 'up' : 'down',
         fieldsUp ? `rdkit ${health.rdkit} · pyscf ${health.pyscf} · rss ${health.rss_mb} MB`
                  : (health.__error || 'no answer')],
        ['dirac-web', ':1338', webUp ? 'up' : 'down',
         webUp ? 'the app bundle' : 'no answer'],
        ['dirac-ops', ':1355', 'up', 'this page and the console'],
        ['dirac-physics', ':8902', physicsUp ? 'up' : 'down',
         physicsUp ? 'σ-hole surface MEP · torsion · region fields'
                   : 'installed, not started (unit exists; port may be hand-run)'],
    ];
    $('svc').querySelector('tbody').innerHTML = rows.map(([n, p, st, note]) =>
        `<tr>${cell(n, 'k')}${cell(p, 'n')}`
        + `<td class="${st === 'up' ? 'up' : 'down'}">${st}</td>${cell(note, 'n')}</tr>`
    ).join('');
}

function renderLive(health, snap) {
    const j = (health && health.jobs) || {};
    const q = (health && health.qm_waiting) || {};
    const c = (snap && snap.data && snap.data.cache) || {};
    const rows = [
        ['jobs opened', j.opened, 'rows written since this process started'],
        ['done / failed', `${j.done ?? '—'} / ${j.failed ?? '—'}`, 'terminal outcomes'],
        ['joined', j.joined, 'duplicate requests that waited instead of recomputing'],
        ['join timeouts', j.join_timeout, 'waited, gave up, computed it after all'],
        ['ledger write failures', j.write_failed,
         'a job row may never cost a result — this is how often that happened'],
        ['SCF slots', health && health.qm_slots, 'concurrent quantum computations allowed'],
        ['peak at the gate', q.peak, 'most requests ever waiting or running at once'],
        ['peak running', q.running_peak,
         'measured AT THE SEMAPHORE; the job ledger over-counts this because its interval includes work after the slot is released'],
        ['queue refusals', q.refused, 'short-budget callers refused with the depth'],
        ['SCF cache', health && `${health.scf_cached} / ${health.scf_cache_max}`,
         'in-memory wavefunctions, bounded'],
        ['persisted / failed', health && health.persist
            ? `${health.persist.ok} / ${health.persist.failed}` : '—',
         'background cube writes; a failure here is counted because the response already said stored:true'],
        ['cube rows servable', c.rows_servable, 'readable under the CURRENT method versions'],
        ['rows total', c.rows_total, 'including rows from superseded methods'],
    ];
    $('live').querySelector('tbody').innerHTML = rows.map(([k, v, note]) =>
        `<tr>${cell(k, 'k')}${cell(v === undefined || v === null ? '—' : v)}${cell(note, 'n')}</tr>`
    ).join('');
}

function renderUrls() {
    const rows = [
        ['app', `${WEB}/`, 'the full architecture, this box'],
        ['ops console', `${OPS}/index.html`, 'queue · cache · stale · producers'],
        ['this page', `${OPS}/host.html`, 'what the host is'],
        ['fields health', `${FIELDS}/health`, 'machine-readable capability set'],
        ['admin snapshot', `${FIELDS}/admin/snapshot`, 'read-only; no route here writes'],
    ];
    $('urls').querySelector('tbody').innerHTML = rows.map(([k, u, note]) =>
        `<tr>${cell(k, 'k')}<td class="v"><a href="${u}">${u}</a></td>${cell(note, 'n')}</tr>`
    ).join('');
}

async function tick() {
    const [health, snap] = await Promise.all([
        fetchJSON(`${FIELDS}/health`),
        fetchJSON(`${FIELDS}/admin/snapshot`, 9000),
    ]);
    const [physicsUp, webUp] = await Promise.all([
        reachable(`${PHYSICS}/health`), reachable(`${WEB}/`),
    ]);
    renderGeneration(health.__error ? null : health, snap.__error ? null : snap);
    renderServices(health, snap, physicsUp, webUp);
    renderLive(health.__error ? null : health, snap.__error ? null : snap);
    renderUrls();
    const stamp = new Date().toTimeString().slice(0, 8);
    $('foot').insertAdjacentHTML('afterbegin',
        `<div style="margin-bottom:8px">read at ${stamp} from <code>${HOST}</code>`
        + (health.__error ? ` · <span class="down">fields: ${health.__error}</span>` : '')
        + `</div>`);
}

tick();
setInterval(() => { $('foot').querySelectorAll('div').forEach(d => d.remove()); tick(); }, 10000);
