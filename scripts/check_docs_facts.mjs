#!/usr/bin/env node
// scripts/check_docs_facts.mjs — a machine gate against doc rot.
//
// A doc fact that no machine checks WILL rot (measured: three of the four
// places that documented the fields-backend host were wrong at the time this
// gate was written). This script re-derives the truth from the SOURCE every
// run and compares every doc claim against it — it never hardcodes "the
// answer is 0.0.0.0", because the day the code changes the answer, a
// hardcoded expectation goes stale in exactly the way the docs did.
//
// Two independent checks:
//   1. HOST CLAIMS — every `127.0.0.1:PORT` / `0.0.0.0:PORT` / `localhost:PORT`
//      string in a fixed list of doc+source files, checked against the actual
//      bind address parsed out of the server that owns that port.
//   2. BUILD/RUN COMMANDS — every command in a ```bash fenced block in
//      README.md / backend/README.md, checked against package.json's
//      scripts or the real filesystem.
//
// Exit 1 with file:line for every mismatch. Exit 0 when clean.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

const violations = [];
const info = [];

function violate(file, line, message) {
    violations.push(`${file}:${line}: ${message}`);
}

function read(relPath) {
    return fs.readFileSync(path.join(ROOT, relPath), 'utf8');
}

// ─────────────────────────────────────────────────────────────────────────
// 1. HOST CLAIMS
// ─────────────────────────────────────────────────────────────────────────
//
// Files that may state, in prose or in a run command's comment, where a
// backend listens. Includes the two Python daemons even though this script
// may not edit them (per its own ownership boundary) — the gate's job is to
// CATCH a wrong claim there, not to fix it.
const HOST_CLAIM_FILES = [
    'README.md',
    'backend/README.md',
    'backend/physics/README.md',
    'backend/field_server.py',
    'backend/physics/server.py',
];

// Which source file is the ground truth for each port. This association
// (port N is owned by file F) is a structural fact of the repo layout, not
// the drifting value the gate exists to check — the drifting value (what
// host does F actually bind) is parsed below, never hardcoded.
const PORT_TRUTH_SOURCE = {
    8901: 'backend/field_server.py',
    8902: 'backend/physics/server.py',
};

// Resolve a Python identifier used as the host argument of
// `ThreadingHTTPServer((HOST_EXPR, ...))` down to its literal default value,
// by chasing `NAME = <expr>` assignments (including `os.environ.get('VAR',
// <default>)`) up to a fixed depth. Handles the current code's actual shape:
//   field_server.py:  ThreadingHTTPServer(('0.0.0.0', port), Handler)         -> literal, no chase
//   physics/server.py: ThreadingHTTPServer((host, port), Handler)            -> host
//                       host = os.environ.get('DIRAC_PHYSICS_HOST', HOST)    -> HOST
//                       HOST = os.environ.get('DIRAC_PHYSICS_HOST', '0.0.0.0') -> '0.0.0.0'
function resolveActualHost(absPath) {
    const src = fs.readFileSync(absPath, 'utf8');
    const lines = src.split('\n');
    const bindLineIdx = lines.findIndex(l => /ThreadingHTTPServer\(\(/.test(l));
    if (bindLineIdx === -1) {
        return { error: 'no ThreadingHTTPServer((...)) call found — cannot derive the actual bind host' };
    }
    const bindMatch = lines[bindLineIdx].match(/ThreadingHTTPServer\(\(\s*([^,]+?)\s*,/);
    if (!bindMatch) {
        return { error: `found ThreadingHTTPServer(( on line ${bindLineIdx + 1} but could not parse its host argument` };
    }
    let expr = bindMatch[1].trim();
    const seen = new Set();
    for (let depth = 0; depth < 10; depth++) {
        const literal = expr.match(/^['"]([^'"]*)['"]$/);
        if (literal) {
            return { host: literal[1], bindLine: bindLineIdx + 1 };
        }
        if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(expr)) {
            return { error: `host expression "${expr}" is neither a string literal nor a plain identifier — cannot resolve` };
        }
        if (seen.has(expr)) {
            return { error: `circular assignment resolving "${expr}"` };
        }
        seen.add(expr);
        // First `EXPR = <rhs>` assignment anywhere in the file (module-level
        // or inside a function — `host = os.environ.get(...)` typically
        // lives inside `if __name__ == '__main__':`, indented), case-sensitive.
        const assignRe = new RegExp(`^[ \\t]*${expr}\\s*=\\s*(.+)$`, 'm');
        const assignMatch = src.match(assignRe);
        if (!assignMatch) {
            return { error: `no assignment found for "${expr}" while resolving the bind host` };
        }
        let rhs = assignMatch[1].trim();
        const envGet = rhs.match(/os\.environ\.get\(\s*['"][^'"]+['"]\s*,\s*(.+?)\)\s*$/);
        if (envGet) {
            expr = envGet[1].trim();
            continue;
        }
        expr = rhs.replace(/#.*$/, '').trim();
    }
    return { error: 'resolution depth exceeded (>10 hops) — likely a parsing bug in this gate' };
}

// Canonicalize a host string to the two categories that actually matter for
// "is this claim true": LOOPBACK_ONLY (127.0.0.1 / localhost / [::1] / ::1)
// vs ALL_INTERFACES (0.0.0.0 / ::). Anything else is reported verbatim and
// treated as its own category, so a genuinely custom host does not silently
// compare equal to either.
function canonHost(h) {
    const v = h.trim();
    if (v === '127.0.0.1' || v.toLowerCase() === 'localhost' || v === '::1' || v === '[::1]') return 'LOOPBACK_ONLY';
    if (v === '0.0.0.0' || v === '::') return 'ALL_INTERFACES';
    return `OTHER(${v})`;
}

const actualHostCache = {};
function actualHostFor(port) {
    if (port in actualHostCache) return actualHostCache[port];
    const srcFile = PORT_TRUTH_SOURCE[port];
    if (!srcFile) {
        actualHostCache[port] = null;
        return null;
    }
    const resolved = resolveActualHost(path.join(ROOT, srcFile));
    actualHostCache[port] = resolved;
    return resolved;
}

// Self-check: does each truth-source file actually declare the port key it
// is registered under? If a future rename desyncs PORT_TRUTH_SOURCE from the
// code, say so loudly instead of silently comparing against the wrong file.
for (const [portStr, srcFile] of Object.entries(PORT_TRUTH_SOURCE)) {
    const port = Number(portStr);
    const src = read(srcFile);
    const portConstMatch = src.match(/^PORT\s*=\s*(\d+)/m);
    if (!portConstMatch) {
        violate(srcFile, 1, `gate self-check: expected a module-level "PORT = ${port}" constant, found none. PORT_TRUTH_SOURCE in this gate may be pointing at the wrong file, or the constant was renamed.`);
    } else if (Number(portConstMatch[1]) !== port) {
        violate(srcFile, 1, `gate self-check: this file's PORT constant is ${portConstMatch[1]}, but PORT_TRUTH_SOURCE claims it owns port ${port}. Update PORT_TRUTH_SOURCE in scripts/check_docs_facts.mjs.`);
    }
}

const HOST_PORT_RE = /\b(127\.0\.0\.1|0\.0\.0\.0|localhost)(?::(\d{2,5}))?\b/g;

for (const relFile of HOST_CLAIM_FILES) {
    const text = read(relFile);
    const lines = text.split('\n');
    lines.forEach((lineText, idx) => {
        let m;
        const re = new RegExp(HOST_PORT_RE);
        while ((m = re.exec(lineText)) !== null) {
            const claimedHost = m[1];
            const claimedPort = m[2] ? Number(m[2]) : null;
            const lineNo = idx + 1;
            if (claimedPort === null) {
                // A bare host mention with no port (e.g. an allow-list, a
                // rebinding instruction) cannot be attributed to one service.
                // Not evidence of anything — not even logged.
                continue;
            }
            const truthSrc = PORT_TRUTH_SOURCE[claimedPort];
            if (!truthSrc) {
                // A port this gate has no ground truth for (e.g. the static
                // file server on :1338, which is not one of the two Python
                // daemons). Nothing to check it against.
                info.push(`${relFile}:${lineNo}: "${claimedHost}:${claimedPort}" — no registered truth source for port ${claimedPort}, not checked`);
                continue;
            }
            const actual = actualHostFor(claimedPort);
            if (!actual || actual.error) {
                violate(relFile, lineNo, `claims ${claimedHost}:${claimedPort}, but the actual bind host could not be derived from ${truthSrc} (${actual ? actual.error : 'no result'}). Fix the parser or the source before trusting this doc.`);
                continue;
            }
            const claimedCanon = canonHost(claimedHost);
            const actualCanon = canonHost(actual.host);
            if (claimedCanon !== actualCanon) {
                violate(
                    relFile, lineNo,
                    `claims "${claimedHost}:${claimedPort}" (${claimedCanon}), but ${truthSrc}:${actual.bindLine} actually binds "${actual.host}" (${actualCanon}). ` +
                    (actualCanon === 'ALL_INTERFACES'
                        ? 'This service is reachable from the LAN, not loopback-only — a 127.0.0.1/localhost claim understates its exposure.'
                        : 'This service is loopback-only — a 0.0.0.0 claim overstates its exposure.')
                );
            }
        }
    });
}

// ─────────────────────────────────────────────────────────────────────────
// 2. DOCUMENTED BUILD/RUN COMMANDS
// ─────────────────────────────────────────────────────────────────────────
//
// A documented command that cannot run is the same defect class as a wrong
// host: it looks authoritative and it is false. Checked against package.json
// (npm scripts + declared deps) and the real filesystem — never against a
// hand-maintained list of "commands that should work".
const COMMAND_DOC_FILES = ['README.md', 'backend/README.md'];

const pkg = JSON.parse(read('package.json'));
const npmScripts = new Set(Object.keys(pkg.scripts ?? {}));
const npmDeps = new Set([
    ...Object.keys(pkg.dependencies ?? {}),
    ...Object.keys(pkg.devDependencies ?? {}),
]);

// npm subcommands that are npm itself, not one of our scripts — never a
// lookup miss, always skip.
const NPM_BUILTINS = new Set(['ci', 'install', 'i', 'uninstall', 'update', 'audit', 'outdated', 'link', 'pack', 'publish', 'version', 'test']);

function extractFencedBashBlocks(text) {
    const lines = text.split('\n');
    const blocks = [];
    let cur = null;
    lines.forEach((lineText, idx) => {
        const fenceOpen = lineText.match(/^```(\w+)?\s*$/);
        if (fenceOpen && !cur) {
            if (fenceOpen[1] === 'bash' || fenceOpen[1] === 'sh') {
                cur = { startLine: idx + 2, lines: [] }; // first content line
            } else {
                cur = { skip: true };
            }
            return;
        }
        if (fenceOpen && cur) {
            if (!cur.skip) blocks.push(cur);
            cur = null;
            return;
        }
        if (cur && !cur.skip) {
            cur.lines.push(lineText);
        }
    });
    return blocks;
}

function checkPathToken(token, relFile, lineNo) {
    // A repo-relative path ending in a checkable extension.
    if (!/\.(m?js|cjs|py)$/.test(token)) return;
    if (token.startsWith('~')) return; // outside the repo, not ours to check
    const abs = path.join(ROOT, token);
    if (!fs.existsSync(abs)) {
        violate(relFile, lineNo, `documented command references "${token}", which does not exist in the repo.`);
    }
}

for (const relFile of COMMAND_DOC_FILES) {
    const text = read(relFile);
    const blocks = extractFencedBashBlocks(text);
    for (const block of blocks) {
        block.lines.forEach((rawLine, offset) => {
            const lineNo = block.startLine + offset;
            const line = rawLine.trim();
            if (line === '' || line.startsWith('#')) return;
            // Split on && / ; so a compound line checks every segment.
            const segments = line.split(/&&|;/).map(s => s.trim()).filter(Boolean);
            for (const seg of segments) {
                const npmRunMatch = seg.match(/^npm\s+run\s+([A-Za-z0-9_:.-]+)/);
                if (npmRunMatch) {
                    if (!npmScripts.has(npmRunMatch[1])) {
                        violate(relFile, lineNo, `documented command "npm run ${npmRunMatch[1]}" — no such script in package.json. Known scripts: ${[...npmScripts].join(', ')}.`);
                    }
                    continue;
                }
                const npmBuiltinMatch = seg.match(/^npm\s+([A-Za-z0-9_:.-]+)/);
                if (npmBuiltinMatch && NPM_BUILTINS.has(npmBuiltinMatch[1])) {
                    continue; // npm ci / npm install — nothing to look up
                }
                const binMatch = seg.match(/^node_modules\/\.bin\/([\w-]+)/);
                if (binMatch) {
                    const tool = binMatch[1];
                    if (!npmDeps.has(tool)) {
                        violate(relFile, lineNo, `documented command runs "node_modules/.bin/${tool}", but "${tool}" is not a dependency in package.json — a fresh "npm ci" would not create this binary.`);
                        continue;
                    }
                    const nmExists = fs.existsSync(path.join(ROOT, 'node_modules'));
                    if (nmExists && !fs.existsSync(path.join(ROOT, 'node_modules', '.bin', tool))) {
                        violate(relFile, lineNo, `documented command runs "node_modules/.bin/${tool}"; "${tool}" is declared as a dependency but node_modules/.bin/${tool} is missing on disk right now.`);
                    }
                    continue;
                }
                const nodeMatch = seg.match(/^node\s+(\S+\.m?js)\b/);
                if (nodeMatch) {
                    checkPathToken(nodeMatch[1].replace(/^\.\//, ''), relFile, lineNo);
                    continue;
                }
                const pyMatch = seg.match(/\b([\w./-]+\.py)\b/);
                if (pyMatch) {
                    checkPathToken(pyMatch[1], relFile, lineNo);
                    continue;
                }
                // git clone / cd / conda create / pip install <pkgs> / bare
                // "open http://..." comments — not repo-relative commands
                // this gate can check against a script or a file. Left alone
                // on purpose: asserting on them would mean asserting on
                // external tools and URLs, not on this repo's own claims.
            }
        });
    }
}

// ─────────────────────────────────────────────────────────────────────────
// REPORT
// ─────────────────────────────────────────────────────────────────────────
if (info.length) {
    console.log('Informational (no ground truth to check against):');
    for (const line of info) console.log(`  ${line}`);
    console.log('');
}

if (violations.length > 0) {
    console.log(`check_docs_facts: ${violations.length} mismatch(es) found\n`);
    for (const v of violations) console.log(v);
    process.exit(1);
} else {
    console.log('check_docs_facts: OK — every host:port claim and every documented command checked out against the source.');
    process.exit(0);
}
