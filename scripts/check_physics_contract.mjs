#!/usr/bin/env node
// scripts/check_physics_contract.mjs — the σ-hole daemon's README, kept true by
// re-derivation instead of by proofreading.
//
// WHY THIS EXISTS, and why it is aimed at an INVARIANT rather than at sentences:
// backend/physics/README.md said "requests are refused BEFORE running when the
// prediction exceeds max_seconds" and "the caller's timeout is the real
// protection". The first was true of /surface/mep and had never been true of
// /surface/mep_at; the second was never true of anything — a client socket
// timeout does not stop a pyscf job holding 24 cores. Both were corrected by
// hand on 2026-08-11. That is the third README in this repo found describing one
// of two daemons, so the pattern is not carelessness: a doc claim is written
// once, about the route the author had open.
//
// A gate that string-matched those sentences would convict when someone EDITED
// THE DOC, which is backwards. So this gate does the thing the sentences claim
// is universal, and requires it to actually be universal: every route that can
// reach an SCF must carry all five protections. The doc's promise becomes an
// executable invariant, and prose is then free to be prose.
//
// PROXY LEDGER (this file's own honesty, per the repo's standing rule):
//   PROPERTY    every SCF-reaching route is bounded before AND during the run,
//               and refuses a basis it cannot describe.
//   PROXY       static source structure: the handler branch passes max_seconds
//               and calls validated_basis(); the target function calls
//               clamp_budget(), _install_watchdog(), and _check_deadline().
//   IMPLICATION missing call ⇒ missing protection (sound). Present call ⇒ the
//               protection WORKS is NOT implied — a clamp could be wrong, a
//               watchdog could be installed with a broken deadline.
//   SAFE SIDE   CONVICTION ONLY. Silence here means UNVERIFIED, not proven.
//               The sound instrument is backend/tests/test_cannot_fire.py,
//               which calls these things and checks their behaviour.
//
// Usage:  node scripts/check_physics_contract.mjs [--selftest]
// Exit:   0 clean · 1 a route is missing a protection · 2 could not run
//
// --selftest feeds a CRAFTED BROKEN SOURCE to the same extractor and requires
// four convictions. It deliberately does not mutate the real files: they belong
// to another session that edits them live, and a gate whose red proof races a
// teammate's writes is a gate that will one day commit their half-finished line.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SERVER = 'backend/physics/server.py';
const SURFACE = 'backend/physics/mep_surface.py';

// The five protections, in the order a request meets them.
const PROTECTIONS = [
    ['basis_validated', 'refuses a basis it cannot describe (validated_basis)'],
    ['budget_passed', 'passes the caller max_seconds through to the compute call'],
    ['budget_clamped', 'normalises a non-finite budget (clamp_budget)'],
    ['watchdog', 'bounds the SCF per cycle (_install_watchdog)'],
    ['whole_deadline', 'bounds post-SCF surface construction (_check_deadline)'],
];

function matchBrace(text, openIdx, open = '(', close = ')') {
    let depth = 0;
    for (let i = openIdx; i < text.length; i++) {
        if (text[i] === open) depth++;
        else if (text[i] === close && --depth === 0) return i;
    }
    return -1;
}

// A handler branch runs from its `if/elif self.path == '<route>':` to the next
// `elif self.path`/`else:` at the same nesting. Anchored on the route string so
// that reordering the branches cannot desync this from the code.
function routeBranches(serverText) {
    const branches = [];
    const re = /(?:if|elif)\s+self\.path\s*==\s*'([^']+)'\s*:/g;
    for (const m of [...serverText.matchAll(re)]) {
        const start = m.index + m[0].length;
        const rest = serverText.slice(start);
        const nextRe = /\n\s*(?:elif\s+self\.path|else\s*:)/.exec(rest);
        branches.push({
            route: m[1],
            body: nextRe ? rest.slice(0, nextRe.index) : rest,
        });
    }
    return branches;
}

function pyFunctionBody(text, name) {
    const m = new RegExp(`\\ndef ${name}\\s*\\(`).exec(text);
    if (!m) return '';
    const rest = text.slice(m.index + 1);
    const nextDef = /\n(?:def |class |@)/.exec(rest.slice(1));
    return nextDef ? rest.slice(0, nextDef.index + 1) : rest;
}

// Which mep_surface entry point does this branch call? Derived, not listed —
// a new route calling a new entry point must be judged too.
function calledEntryPoints(branchBody, surfaceText) {
    const defined = [...surfaceText.matchAll(/\ndef ([a-z_0-9]+)\s*\(/g)].map(m => m[1]);
    return defined.filter(fn => new RegExp(`\\b${fn}\\s*\\(`).test(branchBody));
}

function reachesScf(fnBody) {
    return /\bscf\.(RHF|UHF|RKS|UKS)\b|\bdft\.(RKS|UKS)\b/.test(fnBody);
}

function auditRoute(branch, surfaceText) {
    const entries = calledEntryPoints(branch.body, surfaceText);
    const scfEntries = entries.filter(fn => reachesScf(pyFunctionBody(surfaceText, fn)));
    if (!scfEntries.length) return null;          // not an SCF route; out of scope

    const found = { basis_validated: false, budget_passed: false,
                    budget_clamped: false, watchdog: false, whole_deadline: false };
    found.basis_validated = /validated_basis\s*\(/.test(branch.body);
    found.budget_passed = /max_seconds\s*=/.test(branch.body);

    // A route is only as protected as its WEAKEST entry point: if it can call
    // two functions and one of them is unclamped, the route is unclamped.
    found.budget_clamped = scfEntries.every(
        fn => /clamp_budget\s*\(/.test(pyFunctionBody(surfaceText, fn)));
    found.watchdog = scfEntries.every(
        fn => /_install_watchdog\s*\(/.test(pyFunctionBody(surfaceText, fn)));
    found.whole_deadline = scfEntries.every(
        fn => /_check_deadline\s*\(/.test(pyFunctionBody(surfaceText, fn)));

    return { route: branch.route, entries: scfEntries, found };
}

function audit(serverText, surfaceText) {
    return routeBranches(serverText)
        .map(b => auditRoute(b, surfaceText))
        .filter(Boolean);
}

// ── the crafted broken source for --selftest ────────────────────────────────
const BROKEN_SERVER = `
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
            if self.path == '/surface/mep':
                out = compute_surface_mep(
                    req['molfile'],
                    basis=validated_basis(req.get('basis', DEFAULT_BASIS)),
                    max_seconds=req.get('max_seconds', DEFAULT_MAX_SECONDS))
            elif self.path == '/surface/mep_at':
                values, meta = mep_at_points(req['molfile'], pts,
                                             basis=req.get('basis'))
            else:
                self.send_error(404)
`;
const BROKEN_SURFACE = `
def clamp_budget(value, default):
    return default

def _install_watchdog(mf, deadline, max_seconds, label):
    mf.callback = None

def compute_surface_mep(molblock, basis='sto-3g', max_seconds=120.0):
    max_seconds = clamp_budget(max_seconds, 120.0)
    mf = scf.RHF(mol)
    _install_watchdog(mf, time.time() + max_seconds, max_seconds, 'surface/mep')
    return out

def mep_at_points(molblock, points_ang, basis='sto-3g'):
    mf = scf.RHF(mol)
    return values, meta
`;

function selftest() {
    const rows = audit(BROKEN_SERVER, BROKEN_SURFACE);
    const missing = [];
    for (const r of rows) {
        for (const [key] of PROTECTIONS) if (!r.found[key]) missing.push(`${r.route}:${key}`);
    }
    const expected = ['/surface/mep:whole_deadline',
                      '/surface/mep_at:basis_validated', '/surface/mep_at:budget_passed',
                      '/surface/mep_at:budget_clamped', '/surface/mep_at:watchdog'];
    const ok = rows.length === 2
        && expected.every(e => missing.includes(e))
        && missing.includes('/surface/mep_at:whole_deadline');
    console.log('── selftest: the crafted broken source must be convicted ──');
    console.log(`  routes audited : ${rows.map(r => r.route).join(', ') || '(none)'}`);
    console.log(`  convictions    : ${missing.join(', ') || '(none)'}`);
    if (!ok) {
        console.log('SELFTEST FAIL — the extractor did not convict the known-broken source, '
            + 'so a green run against the real files proves nothing');
        process.exit(1);
    }
    console.log('SELFTEST PASS — old SCF-only protection is convicted on both routes');
    process.exit(0);
}

// ── main ────────────────────────────────────────────────────────────────────
if (process.argv.includes('--selftest')) selftest();

let serverText, surfaceText;
try {
    serverText = fs.readFileSync(path.join(ROOT, SERVER), 'utf8');
    surfaceText = fs.readFileSync(path.join(ROOT, SURFACE), 'utf8');
} catch (e) {
    console.log(`check_physics_contract: cannot read the sources (${e.message})`);
    process.exit(2);
}

const rows = audit(serverText, surfaceText);
if (!rows.length) {
    console.log(`check_physics_contract: found NO SCF-reaching route in ${SERVER} — the `
        + 'extractor is almost certainly broken, not the daemon. Refusing to report clean.');
    process.exit(2);
}

let failed = 0;
console.log(`── ${SERVER}: every SCF route must carry all five protections ──`);
for (const r of rows) {
    const missing = PROTECTIONS.filter(([k]) => !r.found[k]);
    const mark = missing.length ? 'FAIL' : 'OK  ';
    console.log(`  ${mark}  ${r.route}  → ${r.entries.join(', ')}`);
    for (const [k, desc] of missing) {
        failed++;
        console.log(`        missing: ${k} — ${desc}`);
    }
}
console.log('  (conviction-only: silence here is UNVERIFIED, not proven — the behavioural '
    + 'instrument is backend/tests/test_cannot_fire.py)');
if (failed) {
    console.log(`\n${failed} missing protection(s). backend/physics/README.md claims these hold `
        + 'for the module; make the code true or narrow the claim to the route it covers.');
    process.exit(1);
}
console.log(`\nall ${rows.length} SCF route(s) carry all ${PROTECTIONS.length} protections`);
process.exit(0);
