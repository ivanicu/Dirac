#!/usr/bin/env node
/**
 * S1 gate 4 — CSS brace balance inside inline <style> blocks.
 *
 *   node scripts/check_css_braces.mjs [file.html ...]
 *
 * WHY THIS EXISTS. Commit a93c175: one unclosed brace in .ligand-legend-toggle
 * inside the 1000+ line inline <style> of the lab's index.html silently swallowed
 * 411 lines of CSS and blanked the entire UI. Nothing threw. The browser's CSS
 * parser recovers by discarding, so the failure is invisible to every other gate
 * in the suite — tsc does not read HTML, the bundler does not parse CSS, and the
 * palette gate reads tokens.css. This is the only check that can see it.
 *
 * WHAT IT ACTUALLY DOES. Extracts EVERY <style>...</style> block (not just the
 * first), then walks the CSS character by character with a four-state machine —
 * CSS / comment / single-quoted / double-quoted — so a brace inside `/* } *\/` or
 * inside content:"}" is not counted. A naive open/close count gets both wrong,
 * and gets them wrong in the PASS direction: two stray braces of opposite sense
 * inside two comments cancel and the file reads clean.
 *
 * FAILURE MODES IT REPORTS, each with file:line:col of the offending character:
 *   1. unclosed brace   — depth > 0 at </style>   (the a93c175 incident)
 *   2. stray closer     — depth goes negative     (a `}` with nothing open)
 *   3. unclosed comment — /* never terminated     (eats CSS the same way)
 *   4. unclosed <style> — no </style> tag
 *   5. no <style> block found at all
 *
 * (5) is a FAILURE, not a pass. A gate pointed at a file it cannot read must go
 * red: extracting zero blocks and reporting OK is how a check that can never
 * fail gets certified as green forever.
 */
import { readFileSync } from 'node:fs';

const DEFAULT_TARGET = 'src/app.frontend.facets.molstar-rdkit.editable/index.html';

const STYLE_OPEN = /<style\b[^>]*>/gi;
const STYLE_CLOSE = /<\/style\s*>/i;

/** Offset -> 1-based {line, col}, so every report points into the HTML file. */
function locator(source) {
    const starts = [0];
    for (let i = 0; i < source.length; i++) {
        if (source[i] === '\n') starts.push(i + 1);
    }
    return (offset) => {
        let lo = 0;
        let hi = starts.length - 1;
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (starts[mid] <= offset) lo = mid; else hi = mid - 1;
        }
        return { line: lo + 1, col: offset - starts[lo] + 1 };
    };
}

/**
 * Walk one CSS region of `source` spanning [from, to).
 * Returns a list of problems; offsets are absolute in `source`.
 */
function walk(source, from, to) {
    const problems = [];
    const open = [];              // offsets of currently-unclosed '{'
    let mode = 'css';             // css | comment | squote | dquote
    let commentStart = -1;
    let braces = 0;               // real braces only — not the ones in comments/strings
    let i = from;

    while (i < to) {
        const c = source[i];

        if (mode === 'comment') {
            if (c === '*' && source[i + 1] === '/') { mode = 'css'; i += 2; continue; }
            i++;
            continue;
        }

        if (mode === 'squote' || mode === 'dquote') {
            // A CSS string cannot span a raw newline. Recovering at the newline
            // keeps one stray quote from turning the whole rest of the file into
            // "string" and hiding every brace error after it.
            if (c === '\\') { i += 2; continue; }
            if (c === '\n') { mode = 'css'; i++; continue; }
            if ((mode === 'squote' && c === "'") || (mode === 'dquote' && c === '"')) {
                mode = 'css'; i++; continue;
            }
            i++;
            continue;
        }

        // mode === 'css'
        if (c === '/' && source[i + 1] === '*') { mode = 'comment'; commentStart = i; i += 2; continue; }
        if (c === "'") { mode = 'squote'; i++; continue; }
        if (c === '"') { mode = 'dquote'; i++; continue; }

        if (c === '{') { open.push(i); braces++; i++; continue; }

        if (c === '}') {
            braces++;
            if (open.length === 0) {
                problems.push({ offset: i, kind: 'stray closing brace `}` with no matching `{`' });
                // Depth is now desynchronised; everything after this point would
                // be reported against a shifted nesting. Stop and report.
                return { problems, braces, fatal: true };
            }
            open.pop();
            i++;
            continue;
        }

        i++;
    }

    if (mode === 'comment') {
        problems.push({ offset: commentStart, kind: 'unterminated `/*` comment — it swallows the CSS after it' });
    }
    for (const offset of open) {
        problems.push({ offset, kind: 'unclosed `{` — every rule after it is discarded by the CSS parser' });
    }
    return { problems, braces, fatal: false };
}

function checkFile(file) {
    let source;
    try {
        source = readFileSync(file, 'utf8');
    } catch (err) {
        console.error(`FAIL ${file}: cannot read (${err.code || err.message})`);
        return false;
    }

    const at = locator(source);
    const problems = [];
    let blocks = 0;
    let braces = 0;

    STYLE_OPEN.lastIndex = 0;
    let match;
    while ((match = STYLE_OPEN.exec(source)) !== null) {
        const bodyStart = match.index + match[0].length;
        const rest = source.slice(bodyStart);
        const close = rest.search(STYLE_CLOSE);
        if (close === -1) {
            const loc = at(match.index);
            problems.push({ line: loc.line, col: loc.col, kind: 'unclosed `<style>` — no `</style>` tag follows' });
            break;
        }
        const bodyEnd = bodyStart + close;
        blocks++;

        const { problems: found, braces: real } = walk(source, bodyStart, bodyEnd);
        braces += real;
        for (const p of found) {
            const loc = at(p.offset);
            problems.push({ line: loc.line, col: loc.col, kind: p.kind });
        }
        STYLE_OPEN.lastIndex = bodyEnd;
    }

    if (blocks === 0 && problems.length === 0) {
        console.error(`FAIL ${file}: no <style> block found — this gate guards inline CSS, so a file it cannot read is a misconfiguration, not a pass`);
        return false;
    }

    if (problems.length > 0) {
        problems.sort((a, b) => a.line - b.line || a.col - b.col);
        console.error(`FAIL ${file}: ${problems.length} CSS brace problem${problems.length > 1 ? 's' : ''}`);
        for (const p of problems) {
            console.error(`  ${file}:${p.line}:${p.col}  ${p.kind}`);
        }
        return false;
    }

    console.log(`OK   ${file}: ${blocks} <style> block${blocks > 1 ? 's' : ''}, ${braces} braces balanced`);
    return true;
}

const targets = process.argv.slice(2);
const files = targets.length > 0 ? targets : [DEFAULT_TARGET];
let ok = true;
for (const file of files) {
    if (!checkFile(file)) ok = false;
}
process.exit(ok ? 0 : 1);
