#!/usr/bin/env node
/**
 * scripts/perf_probe.mjs — reproducible performance probe for Dirac.
 *
 * WHY THIS EXISTS: SPEC.md and prior sessions quoted performance numbers
 * (bundle size, cold load, field-swap latency, backend timings, RSS) that
 * were measured by hand once, with no command attached. A number without a
 * command is a rumour. This script re-measures every one of those numbers
 * against the LIVE system and prints the exact command/expression that
 * produced each row, so any session can re-run it and get a real answer
 * instead of a remembered one.
 *
 * HARD RULES this script follows (do not relax these when editing it):
 *   - Never print a number it could not measure. A failed precondition
 *     (backend down, no browser, bundle not built) prints SKIPPED + why —
 *     never a placeholder, never a number from memory.
 *   - Read-only against the repo and the Postgres DB. The only local writes
 *     are a throwaway Chrome profile under os.tmpdir(), deleted when the
 *     run ends. Hitting the backend's own /embed and /field endpoints is
 *     the system doing its normal job (which persists computed cubes to
 *     Postgres as PART OF SERVING THE REQUEST, exactly as any real browser
 *     click would) — this script issues no direct SQL, no migration, no
 *     admin/delete route, no schema change.
 *   - Latency rows run >=3 repetitions and report median + min/max + n,
 *     never a single sample dressed up as "the" number.
 *
 * USAGE:
 *   node scripts/perf_probe.mjs                  # everything, human table
 *   node scripts/perf_probe.mjs --json            # everything, machine JSON
 *   node scripts/perf_probe.mjs --only bundle      # one section
 *   node scripts/perf_probe.mjs --only load,swap   # multiple sections
 * Sections: bundle | load | swap | backend | rss
 */

import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';
import { execSync, spawn, spawnSync } from 'node:child_process';
import os from 'node:os';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..');

const WEB_URL = 'http://127.0.0.1:1360';
const BACKEND_URL = 'http://127.0.0.1:8901';
const BUNDLE_PATH = path.join(REPO_ROOT, 'build/dirac/dirac.js');

// Aspirin — the fixed molecule named in the task this script was written for.
const ASPIRIN_SMILES = 'CC(=O)Oc1ccccc1C(=O)O';

// contracts/iface.pyi FieldKind, as named in SPEC.md §4.4 (J1). This probe
// does not itself re-derive that list from contracts/iface.pyi (that file is
// owned by another concurrent session) — if the wire vocabulary changes,
// this list is the first thing to check against SPEC.md before trusting the
// per-kind rows below.
const FIELD_KINDS = ['mep', 'mep_qm', 'homo', 'lumo', 'density', 'mlp'];

// Six small, structurally distinct molecules for the RSS-batch section.
// Small on purpose: the point is to force six distinct SCF cache entries
// quickly, not to stress pyscf's SCF convergence.
const RSS_BATCH_SMILES = ['C', 'CCO', 'c1ccccc1', 'c1ccncc1', 'CC(=O)C', 'C=O'];

// ---------------------------------------------------------------- helpers --

function nowIso() {
    return new Date().toISOString();
}

function median(nums) {
    const s = [...nums].sort((a, b) => a - b);
    const mid = Math.floor(s.length / 2);
    return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

function row(section, metric, fields) {
    return {
        section, metric,
        timestamp: nowIso(),
        skipped: false,
        ...fields,
    };
}

function skip(section, metric, cmd, reason) {
    return {
        section, metric, cmd,
        timestamp: nowIso(),
        skipped: true,
        reason,
    };
}

async function fetchWithTimeout(url, opts = {}, timeoutMs = 5000) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    try {
        return await fetch(url, { ...opts, signal: ctl.signal });
    } finally {
        clearTimeout(t);
    }
}

async function checkBackendUp() {
    try {
        const r = await fetchWithTimeout(`${BACKEND_URL}/health`, {}, 3000);
        if (!r.ok) return { up: false, reason: `GET ${BACKEND_URL}/health -> HTTP ${r.status}` };
        const body = await r.json();
        return { up: true, health: body };
    } catch (e) {
        return { up: false, reason: `GET ${BACKEND_URL}/health failed: ${e.message}` };
    }
}

async function checkWebUp() {
    try {
        const r = await fetchWithTimeout(WEB_URL, {}, 3000);
        return { up: r.ok, reason: r.ok ? undefined : `GET ${WEB_URL} -> HTTP ${r.status}` };
    } catch (e) {
        return { up: false, reason: `GET ${WEB_URL} failed: ${e.message}` };
    }
}

// -------------------------------------------------------------- bundle --

function measureBundle(rows) {
    const cmdRaw = `ls -l build/dirac/dirac.js`;
    if (!fs.existsSync(BUNDLE_PATH)) {
        rows.push(skip('bundle', 'raw bytes', cmdRaw, `${BUNDLE_PATH} does not exist — run 'npm run build:dirac' first`));
        rows.push(skip('bundle', 'gzip bytes', `gzip -c build/dirac/dirac.js | wc -c`, 'bundle not built'));
        return;
    }
    const stat = fs.statSync(BUNDLE_PATH);
    rows.push(row('bundle', 'raw bytes', {
        value: stat.size, unit: 'bytes',
        cmd: cmdRaw,
        note: `= ${(stat.size / (1024 * 1024)).toFixed(2)} MiB (1024-based)`,
    }));

    let gzipBytes;
    try {
        gzipBytes = parseInt(
            execSync(`gzip -c ${JSON.stringify(BUNDLE_PATH)} | wc -c`, { encoding: 'utf8' }).trim(),
            10,
        );
    } catch (e) {
        rows.push(skip('bundle', 'gzip bytes', `gzip -c build/dirac/dirac.js | wc -c`, `gzip failed: ${e.message}`));
        return;
    }
    rows.push(row('bundle', 'gzip bytes', {
        value: gzipBytes, unit: 'bytes',
        cmd: `gzip -c build/dirac/dirac.js | wc -c`,
        note: `= ${(gzipBytes / (1024 * 1024)).toFixed(2)} MiB (1024-based). This is a THEORETICAL `
            + `transport budget, not necessarily what the dev web server sends today — see docs/PERF.md.`,
    }));
}

// ------------------------------------------------------------ chrome/CDP --

const CHROME_CANDIDATES = ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', '/snap/bin/chromium'];

function findChromeBinary() {
    for (const bin of CHROME_CANDIDATES) {
        try {
            const r = spawnSync(bin, ['--version'], { stdio: 'ignore', timeout: 5000 });
            if (r.status === 0) return bin;
        } catch {
            // not found / not runnable — try next candidate
        }
    }
    return null;
}

class CDPSession {
    constructor(wsUrl) {
        this.ws = new WebSocket(wsUrl);
        this.nextId = 1;
        this.pending = new Map();
        this.listeners = new Map();
        this.ready = new Promise((resolve, reject) => {
            this.ws.addEventListener('open', () => resolve());
            this.ws.addEventListener('error', (e) => reject(new Error('CDP websocket error: ' + e.message)));
        });
        this.ws.addEventListener('message', (ev) => {
            const msg = JSON.parse(ev.data);
            if (msg.id && this.pending.has(msg.id)) {
                const { resolve, reject } = this.pending.get(msg.id);
                this.pending.delete(msg.id);
                if (msg.error) reject(new Error(JSON.stringify(msg.error)));
                else resolve(msg.result);
            } else if (msg.method) {
                const cbs = this.listeners.get(msg.method);
                if (cbs) for (const cb of [...cbs]) cb(msg.params);
            }
        });
    }
    async send(method, params = {}) {
        await this.ready;
        const id = this.nextId++;
        return new Promise((resolve, reject) => {
            this.pending.set(id, { resolve, reject });
            this.ws.send(JSON.stringify({ id, method, params }));
        });
    }
    on(method, cb) {
        if (!this.listeners.has(method)) this.listeners.set(method, []);
        this.listeners.get(method).push(cb);
    }
    once(method) {
        return new Promise((resolve) => {
            const cb = (params) => { this.listeners.get(method).splice(this.listeners.get(method).indexOf(cb), 1); resolve(params); };
            this.on(method, cb);
        });
    }
    close() { try { this.ws.close(); } catch { /* already closed */ } }
    async evalJS(expression) {
        const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
        if (r.exceptionDetails) {
            throw new Error(r.exceptionDetails.exception?.description || JSON.stringify(r.exceptionDetails));
        }
        return r.result.value;
    }
}

async function withChromePage(chromeBin, fn) {
    const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dirac-perf-probe-chrome-'));
    const proc = spawn(chromeBin, [
        '--headless=new', '--disable-gpu', '--no-sandbox',
        '--remote-debugging-port=0', `--user-data-dir=${userDataDir}`, 'about:blank',
    ], { stdio: ['ignore', 'ignore', 'pipe'] });

    let port;
    try {
        port = await new Promise((resolve, reject) => {
            const t = setTimeout(() => reject(new Error('chrome did not print a DevTools port within 10s')), 10000);
            proc.stderr.on('data', (chunk) => {
                const m = chunk.toString().match(/DevTools listening on ws:\/\/127\.0\.0\.1:(\d+)\//);
                if (m) { clearTimeout(t); resolve(Number(m[1])); }
            });
            proc.on('exit', (code) => { clearTimeout(t); reject(new Error(`chrome exited early (code ${code})`)); });
        });

        const pageInfo = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
        const session = new CDPSession(pageInfo.webSocketDebuggerUrl);
        await session.send('Page.enable');
        await session.send('Network.enable');
        await session.send('Runtime.enable');
        try {
            return await fn(session);
        } finally {
            session.close();
        }
    } finally {
        proc.kill();
        // Chrome writes lock files asynchronously on exit; give it a beat
        // before trying to remove the profile, and never fail the run over
        // a leftover temp dir.
        await new Promise((r) => setTimeout(r, 300));
        try { fs.rmSync(userDataDir, { recursive: true, force: true }); } catch { /* best effort */ }
    }
}

// --------------------------------------------------------------- load --

async function measureLoad(rows) {
    const cmd = `node scripts/perf_probe.mjs --only load  # drives an isolated headless Chrome via CDP, navigates to ${WEB_URL}`;

    const webCheck = await checkWebUp();
    if (!webCheck.up) {
        rows.push(skip('load', 'DOMContentLoaded', cmd, `web server unreachable: ${webCheck.reason}`));
        rows.push(skip('load', 'load', cmd, `web server unreachable: ${webCheck.reason}`));
        rows.push(skip('load', 'heaviest resources', cmd, `web server unreachable: ${webCheck.reason}`));
        return;
    }

    const chromeBin = findChromeBinary();
    if (!chromeBin) {
        rows.push(skip('load', 'DOMContentLoaded', cmd, `no headless Chrome/Chromium binary found (checked: ${CHROME_CANDIDATES.join(', ')})`));
        rows.push(skip('load', 'load', cmd, 'no headless Chrome/Chromium binary found'));
        rows.push(skip('load', 'heaviest resources', cmd, 'no headless Chrome/Chromium binary found'));
        return;
    }

    try {
        const result = await withChromePage(chromeBin, async (session) => {
            const loadFired = session.once('Page.loadEventFired');
            await session.send('Page.navigate', { url: WEB_URL + '/' });
            await Promise.race([
                loadFired,
                new Promise((_, reject) => setTimeout(() => reject(new Error('load event did not fire within 30s')), 30000)),
            ]);
            const raw = await session.evalJS(`JSON.stringify((() => {
                const nav = performance.getEntriesByType('navigation')[0];
                const res = performance.getEntriesByType('resource').map(r => ({
                    name: r.name, transferSize: r.transferSize, duration: r.duration, encodedBodySize: r.encodedBodySize,
                }));
                res.sort((a, b) => b.transferSize - a.transferSize);
                return {
                    dclMs: nav ? nav.domContentLoadedEventEnd : null,
                    loadMs: nav ? nav.loadEventEnd : null,
                    top: res.slice(0, 3),
                    resourceCount: res.length,
                };
            })())`);
            return JSON.parse(raw);
        });

        rows.push(row('load', 'DOMContentLoaded', {
            value: Math.round(result.dclMs), unit: 'ms', cmd,
            note: `fresh Chrome profile (empty cache), first navigation to ${WEB_URL}/`,
        }));
        rows.push(row('load', 'load', {
            value: Math.round(result.loadMs), unit: 'ms', cmd,
            note: `fresh Chrome profile (empty cache), first navigation to ${WEB_URL}/`,
        }));
        for (const [i, res] of result.top.entries()) {
            rows.push(row('load', `heaviest resource #${i + 1}`, {
                value: res.transferSize, unit: 'bytes transferred', cmd,
                note: `${res.name.replace(WEB_URL + '/', '')} · duration ${Math.round(res.duration)} ms · of ${result.resourceCount} resources total`,
            }));
        }
    } catch (e) {
        rows.push(skip('load', 'DOMContentLoaded', cmd, `chrome/CDP driver failed: ${e.message}`));
        rows.push(skip('load', 'load', cmd, `chrome/CDP driver failed: ${e.message}`));
        rows.push(skip('load', 'heaviest resources', cmd, `chrome/CDP driver failed: ${e.message}`));
    }
}

// --------------------------------------------------------------- swap --

// Not aspirin: SPEC.md Flow A ("Import molecule · SMILES -> 3D") turns out
// not to reach the Fields facet on the currently-built frontend (see
// docs/PERF.md "SPEC contradictions") — pasting a SMILES never sets the
// field-wells module's ligand, so no field-btn click ever leaves the
// idle/no-op guard. Flow B (a deposited PDB ligand) does work, and is the
// path this section drives: the built-in 1CBS fixture carries a real
// non-polymer ligand (retinoic acid, chain A residue 200).
const SWAP_MOLECULE_ID = '1CBS';

async function measureSwap(rows) {
    const cmd = `node scripts/perf_probe.mjs --only swap  # loads fixture ${SWAP_MOLECULE_ID} via the #molecule select, then clicks each field-btn`;

    const webCheck = await checkWebUp();
    const backendCheck = await checkBackendUp();
    const chromeBin = findChromeBinary();
    const problems = [];
    if (!webCheck.up) problems.push(`web server: ${webCheck.reason}`);
    if (!backendCheck.up) problems.push(`backend: ${backendCheck.reason}`);
    if (!chromeBin) problems.push(`no headless Chrome/Chromium binary found (checked: ${CHROME_CANDIDATES.join(', ')})`);
    if (problems.length) {
        for (const kind of FIELD_KINDS) rows.push(skip('swap', kind, cmd, `cannot warm the browser cache: ${problems.join('; ')}`));
        return;
    }

    try {
        const perKind = await withChromePage(chromeBin, async (session) => {
            const loadFired = session.once('Page.loadEventFired');
            await session.send('Page.navigate', { url: WEB_URL + '/' });
            await Promise.race([
                loadFired,
                new Promise((_, reject) => setTimeout(() => reject(new Error('page load timed out')), 20000)),
            ]);
            await new Promise((r) => setTimeout(r, 500));

            const selectResult = await session.evalJS(`(() => {
                const sel = document.getElementById('molecule');
                if (!sel) return 'no-select';
                sel.value = ${JSON.stringify(SWAP_MOLECULE_ID)};
                sel.dispatchEvent(new Event('change', {bubbles:true}));
                return sel.value;
            })()`);
            if (selectResult !== SWAP_MOLECULE_ID) {
                throw new Error(`#molecule select did not accept '${SWAP_MOLECULE_ID}' (got '${selectResult}')`);
            }

            // Wait for the deposited ligand to be detected AND for the
            // classical + cheap-quantum kinds to finish prefetching into the
            // browser cache (facets/field-wells prefetchAll()).
            let prefetchText = '';
            let ligandLabel = '';
            for (let i = 0; i < 90; i++) {
                await new Promise((r) => setTimeout(r, 500));
                const st = JSON.parse(await session.evalJS(`JSON.stringify({
                    prefetch: (document.getElementById('field-prefetch')||{}).textContent || '',
                    summary: (document.getElementById('fields-summary')||{}).textContent || '',
                })`));
                prefetchText = st.prefetch;
                ligandLabel = st.summary;
                if (/fields cached in browser/.test(prefetchText)) break;
            }
            if (!/fields cached in browser/.test(prefetchText)) {
                throw new Error(`ligand/prefetch never completed in 45s (last prefetch note: '${prefetchText}', ligand: '${ligandLabel}')`);
            }

            await session.evalJS(`(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === 'Fields'); b && b.click(); return !!b; })()`);
            await new Promise((r) => setTimeout(r, 200));

            // Completion is detected via #field-status's own data-tone
            // attribute ('busy' while in flight, 'ok'/'error' once settled —
            // a small controlled vocabulary the app itself writes via
            // setStatus() in facets/field-wells/index.ts), NOT by matching
            // the status text. The button's own visible label ("QM
            // potential") and the Kinds registry's label used in status text
            // ("QM potential well") have drifted apart for mep_qm, which
            // makes text-matching fragile; tone does not have that problem.
            // requireCacheHit=true additionally demands the text end in
            // "(browser cache)." — used for the timed repetitions, where a
            // real cache hit is the whole point. The one-time seed click for
            // mep_qm (never auto-prefetched — see PREFETCH_CLASSICAL/QUANTUM
            // in facets/field-wells/index.ts) is a genuine network compute
            // and is not expected to say "(browser cache)"; its own timing is
            // not reported here (the backend section times fresh computes
            // over HTTP directly).
            async function clickAndTime(kind, timeoutMs, requireCacheHit) {
                const raw = await session.evalJS(`(() => new Promise((resolve) => {
                    const btn = document.querySelector('.field-btn[data-field="${kind}"]');
                    const statusEl = document.getElementById('field-status');
                    if (!btn || !statusEl) { resolve({error: 'missing DOM element'}); return; }
                    const requireCacheHit = ${requireCacheHit ? 'true' : 'false'};
                    statusEl.textContent = '__perf_probe_pending__';
                    statusEl.dataset.tone = 'busy';
                    const t0 = performance.now();
                    btn.click();
                    const deadline = t0 + ${timeoutMs};
                    (function check() {
                        const tone = statusEl.dataset.tone;
                        const cur = statusEl.textContent;
                        const settled = tone === 'ok' || tone === 'error';
                        const isCacheHit = /rendered \\(browser cache\\)\\.$/.test(cur);
                        if (settled && (!requireCacheHit || isCacheHit)) {
                            resolve({ms: performance.now() - t0, text: cur, tone, cacheHit: isCacheHit});
                        } else if (performance.now() > deadline) {
                            resolve({error: 'timeout', text: cur, tone});
                        } else {
                            requestAnimationFrame(check);
                        }
                    })();
                }))()`);
                return raw;
            }

            const result = {};
            for (const kind of FIELD_KINDS) {
                // mep_qm is deliberately excluded from prefetchAll (only
                // mep/mlp/homo/lumo/density are prefetched) — see
                // facets/field-wells/index.ts PREFETCH_CLASSICAL/QUANTUM.
                // Seed its cache with one uncounted, generously-timed click
                // (server-side quantum budget is 60s) before timing repeats.
                const needsSeed = kind === 'mep_qm';
                if (needsSeed) {
                    const seed = await clickAndTime(kind, 65000, false);
                    if (seed.error) { result[kind] = { error: `seed click failed: ${seed.error} (${seed.text ?? ''})` }; continue; }
                    await new Promise((r) => setTimeout(r, 150));
                }
                const samples = [];
                const errors = [];
                for (let rep = 0; rep < 5; rep++) {
                    const r = await clickAndTime(kind, 5000, true);
                    if (r.error) errors.push(r.error); else samples.push(r.ms);
                    await new Promise((res) => setTimeout(res, 120));
                }
                result[kind] = { samples, errors, ligandLabel };
            }
            return result;
        });

        for (const kind of FIELD_KINDS) {
            const r = perKind[kind];
            if (!r || r.error) {
                rows.push(skip('swap', kind, cmd, r?.error ?? 'no result'));
                continue;
            }
            if (r.samples.length === 0) {
                rows.push(skip('swap', kind, cmd, `all ${r.errors.length} repetitions failed: ${r.errors.join('; ')}`));
                continue;
            }
            rows.push(row('swap', kind, {
                value: Math.round(median(r.samples)), unit: 'ms (median)', cmd,
                n: r.samples.length,
                min: Math.round(Math.min(...r.samples)),
                max: Math.round(Math.max(...r.samples)),
                note: `ligand: ${r.ligandLabel} · click-to-"rendered (browser cache)" · ${r.errors.length} of ${r.errors.length + r.samples.length} repetitions timed out`,
            }));
        }
    } catch (e) {
        for (const kind of FIELD_KINDS) rows.push(skip('swap', kind, cmd, `driver failed: ${e.message}`));
    }
}

// -------------------------------------------------------------- backend --

async function embed(smiles) {
    const r = await fetchWithTimeout(`${BACKEND_URL}/embed`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ smiles }),
    }, 15000);
    const body = await r.json();
    if (!r.ok || !body.ok) throw new Error(`embed failed: ${JSON.stringify(body)}`);
    return body;
}

async function requestField(molfile, kind, timeoutMs) {
    const t0 = performance.now();
    const r = await fetchWithTimeout(`${BACKEND_URL}/field`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ molfile, kind }),
    }, timeoutMs);
    const elapsedMs = performance.now() - t0;
    const body = await r.json();
    if (!r.ok || !body.ok) return { ok: false, elapsedMs, error: body.error ?? `HTTP ${r.status}` };
    return { ok: true, elapsedMs, cubeBytes: body.cube.length, cache: body.meta?.cache, meta: body.meta };
}

async function measureBackend(rows) {
    const embedCmd = `POST ${BACKEND_URL}/embed {"smiles":"${ASPIRIN_SMILES}"}`;
    const fieldCmd = (kind) => `POST ${BACKEND_URL}/field {"molfile":<aspirin>,"kind":"${kind}"}`;

    const backendCheck = await checkBackendUp();
    if (!backendCheck.up) {
        for (const kind of FIELD_KINDS) {
            rows.push(skip('backend', `${kind} first-call`, fieldCmd(kind), backendCheck.reason));
            rows.push(skip('backend', `${kind} second-call`, fieldCmd(kind), backendCheck.reason));
            rows.push(skip('cache', `${kind} hit-rate`, fieldCmd(kind), backendCheck.reason));
            rows.push(skip('cache', `${kind} computed/cached ratio`, fieldCmd(kind), backendCheck.reason));
        }
        return;
    }

    let molfile;
    try {
        const e = await embed(ASPIRIN_SMILES);
        molfile = e.molfile;
        rows.push(row('backend', 'embed aspirin', {
            value: null, unit: 'n/a', cmd: embedCmd,
            note: `molfile obtained (${molfile.length} chars) — ${JSON.stringify(e.meta)}`,
        }));
    } catch (e) {
        for (const kind of FIELD_KINDS) {
            rows.push(skip('backend', `${kind} first-call`, embedCmd, `embed failed: ${e.message}`));
            rows.push(skip('backend', `${kind} second-call`, embedCmd, `embed failed: ${e.message}`));
        }
        return;
    }

    for (const kind of FIELD_KINDS) {
        try {
            await measureOneKind(rows, molfile, kind, fieldCmd);
        } catch (e) {
            // A single kind's network hiccup (this backend is a shared dev
            // process other sessions restart/rebuild) must not erase every
            // kind after it — record what broke and keep going.
            rows.push(skip('backend', `${kind} first-call`, fieldCmd(kind), `unexpected error: ${e.message}`));
            rows.push(skip('backend', `${kind} second-call`, fieldCmd(kind), `unexpected error: ${e.message}`));
            rows.push(skip('cache', `${kind} hit-rate`, fieldCmd(kind), `unexpected error: ${e.message}`));
        }
    }
}

async function measureOneKind(rows, molfile, kind, fieldCmd) {
    const quantum = kind !== 'mep' && kind !== 'mlp';
    const timeoutMs = quantum ? 95000 : 15000;

    const preHealth = await checkBackendUp();
    const dbCacheNote = preHealth.up ? ` (server db_cache='${preHealth.health.db_cache}' just before this call)` : '';

    const first = await requestField(molfile, kind, timeoutMs);
    if (!first.ok) {
        rows.push(skip('backend', `${kind} first-call`, fieldCmd(kind), `refused: ${first.error}`));
        rows.push(skip('backend', `${kind} second-call`, fieldCmd(kind), 'first call failed'));
        rows.push(skip('cache', `${kind} hit-rate`, fieldCmd(kind), 'first call failed'));
        return;
    }
    rows.push(row('backend', `${kind} first-call`, {
        value: Math.round(first.elapsedMs), unit: 'ms', cmd: fieldCmd(kind),
        note: `cache=${first.cache} · cube_bytes=${first.cubeBytes} (Gaussian-cube TEXT, Bohr units)${dbCacheNote}`,
    }));
    rows.push(row('backend', `${kind} cube bytes`, {
        value: first.cubeBytes, unit: 'bytes', cmd: fieldCmd(kind),
        note: 'raw text-cube payload size, first call',
    }));

    const second = await requestField(molfile, kind, timeoutMs);
    if (!second.ok) {
        rows.push(skip('backend', `${kind} second-call`, fieldCmd(kind), `refused: ${second.error}`));
        rows.push(skip('cache', `${kind} hit-rate`, fieldCmd(kind), 'second call failed'));
        return;
    }
    rows.push(row('backend', `${kind} second-call`, {
        value: Math.round(second.elapsedMs), unit: 'ms', cmd: fieldCmd(kind),
        note: `cache=${second.cache} · cube_bytes=${second.cubeBytes}`,
    }));

    rows.push(row('cache', `${kind} hit-rate (2nd call)`, {
        value: second.cache === 'db' ? 1 : 0, unit: 'boolean (1=hit)', cmd: fieldCmd(kind),
        note: `server-reported meta.cache = '${second.cache}'`
            + (second.cache !== 'db' ? ` — this probe's own db_cache health-check read 'on' immediately before both `
                + `calls (ruling out SPEC.md F7's tripwire-to-'off' as the cause here), which points instead at F5's `
                + `background persistence thread (two autocommit statements) not having committed yet when this `
                + `immediate second call landed — re-requesting the same kind minutes later does return cache='db'. `
                + `See docs/PERF.md.` : ''),
    }));

    if (first.cache === 'computed' && second.cache === 'db') {
        rows.push(row('cache', `${kind} computed/cached ratio`, {
            value: Number((first.elapsedMs / Math.max(second.elapsedMs, 0.001)).toFixed(1)), unit: 'x', cmd: fieldCmd(kind),
            note: `${Math.round(first.elapsedMs)} ms computed / ${Math.round(second.elapsedMs)} ms cached`,
        }));
    } else {
        rows.push(skip('cache', `${kind} computed/cached ratio`, fieldCmd(kind),
            `first=${first.cache}, second=${second.cache} — need first='computed' and second='db' for a real ratio; `
            + `comparing two calls of the same cache state is not a computed/cached ratio`));
    }
}

// ------------------------------------------------------------------ rss --

async function measureRss(rows) {
    const cmd = `GET ${BACKEND_URL}/health  (rss_mb, scf_cached) before/after a 6-molecule quantum-field batch`;
    const backendCheck = await checkBackendUp();
    if (!backendCheck.up) {
        rows.push(skip('rss', 'before', cmd, backendCheck.reason));
        rows.push(skip('rss', 'after', cmd, backendCheck.reason));
        return;
    }

    rows.push(row('rss', 'before', {
        value: backendCheck.health.rss_mb, unit: 'MB', cmd: `curl -s ${BACKEND_URL}/health`,
        note: `scf_cached=${backendCheck.health.scf_cached}/${backendCheck.health.scf_cache_max} · `
            + `⚠ this backend process is shared with any other concurrently-running session — `
            + `this is the state of the WHOLE process, not an isolated measurement`,
    }));

    const errors = [];
    for (const smiles of RSS_BATCH_SMILES) {
        try {
            const e = await embed(smiles);
            const f = await requestField(e.molfile, 'homo', 95000);
            if (!f.ok) errors.push(`${smiles}: ${f.error}`);
        } catch (err) {
            errors.push(`${smiles}: ${err.message}`);
        }
    }

    const after = await checkBackendUp();
    if (!after.up) {
        rows.push(skip('rss', 'after', cmd, after.reason));
        return;
    }
    const scfGrew = after.health.scf_cached > backendCheck.health.scf_cached;
    rows.push(row('rss', 'after', {
        value: after.health.rss_mb, unit: 'MB', cmd: `curl -s ${BACKEND_URL}/health`,
        note: `scf_cached=${after.health.scf_cached}/${after.health.scf_cache_max} `
            + `(bound is ${after.health.scf_cache_max}; a value at that bound after 6 distinct molecules is the `
            + `containment this row exists to check) after a batch of 6 distinct-molecule 'homo' requests`
            + (errors.length ? ` · ${errors.length}/6 molecules in the batch errored: ${errors.join('; ')}` : '')
            + (!scfGrew ? ` · scf_cached did NOT grow this run — field_server.py checks db_get_cube() before `
                + `run_scf() (backend/field_server.py:1526), so if these six molecules were already persisted `
                + `by an earlier run of this same probe, this batch was served entirely from Postgres and never `
                + `touched the in-memory SCF cache or its RSS. That is the cache working, not a failed measurement `
                + `— see docs/PERF.md for a genuinely-cold run's numbers.` : ''),
    }));
    rows.push(row('rss', 'delta', {
        value: after.health.rss_mb - backendCheck.health.rss_mb, unit: 'MB', cmd,
        note: 'after - before; can be negative or noisy if another session used the same backend process during '
            + 'this run, or near-zero on a re-run once these six molecules are already DB-cached (see the '
            + "'after' row's note)",
    }));
}

// ---------------------------------------------------------------- output --

function printHuman(rows) {
    const bySection = new Map();
    for (const r of rows) {
        if (!bySection.has(r.section)) bySection.set(r.section, []);
        bySection.get(r.section).push(r);
    }
    for (const [section, rs] of bySection) {
        console.log(`\n=== ${section} ===`);
        for (const r of rs) {
            if (r.skipped) {
                console.log(`  SKIPPED  ${r.metric}`);
                console.log(`           reason: ${r.reason}`);
                console.log(`           cmd:    ${r.cmd}`);
                continue;
            }
            const valueStr = r.n
                ? `${r.value} ${r.unit} (min ${r.min}, max ${r.max}, n=${r.n})`
                : `${r.value ?? ''} ${r.unit}`;
            console.log(`  ${r.metric}: ${valueStr}`);
            if (r.note) console.log(`           note: ${r.note}`);
            console.log(`           cmd:  ${r.cmd}`);
            console.log(`           at:   ${r.timestamp}`);
        }
    }
    console.log('');
}

// ------------------------------------------------------------------ main --

const SECTION_RUNNERS = {
    bundle: (rows) => measureBundle(rows),
    load: (rows) => measureLoad(rows),
    swap: (rows) => measureSwap(rows),
    backend: (rows) => measureBackend(rows),
    rss: (rows) => measureRss(rows),
};

async function main() {
    const args = process.argv.slice(2);
    const asJson = args.includes('--json');
    const onlyIdx = args.indexOf('--only');
    const only = onlyIdx >= 0 ? args[onlyIdx + 1].split(',').map((s) => s.trim()) : Object.keys(SECTION_RUNNERS);

    for (const name of only) {
        if (!SECTION_RUNNERS[name]) {
            console.error(`unknown section '${name}' — valid: ${Object.keys(SECTION_RUNNERS).join(', ')}`);
            process.exit(1);
        }
    }

    const rows = [];
    for (const name of only) {
        try {
            await SECTION_RUNNERS[name](rows);
        } catch (e) {
            rows.push(skip(name, '(section)', `node scripts/perf_probe.mjs --only ${name}`, `unexpected error: ${e.stack ?? e.message}`));
        }
    }

    if (asJson) {
        console.log(JSON.stringify(rows, null, 2));
    } else {
        printHuman(rows);
    }
}

main();
