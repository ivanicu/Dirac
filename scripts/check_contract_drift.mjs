#!/usr/bin/env node
/**
 * Contract drift gate — the check that would have caught this: three
 * declared contracts (contracts/iface.pyi, contracts/iface.d.ts,
 * contracts/errors.json) drifted from the code and from EACH OTHER within a
 * day of being written, and nothing noticed until someone read the source by
 * hand. This script is that reading, mechanised, so the next drift is a red
 * exit code instead of a false green.
 *
 * Three independent checks, each printing every set difference it finds —
 * never just "FAIL", because a divergence you can't see is a divergence you
 * can't fix:
 *
 *   1. ERROR VOCABULARY — contracts/errors.json (canonical) vs the
 *      `ErrorCode` Literal in iface.pyi vs the `ErrorCode` union in
 *      iface.d.ts. All three must name exactly the same codes (12 as of
 *      NOT_FOUND/DB_UNAVAILABLE, but this script never hardcodes the count —
 *      it reads errors.json live, every run).
 *   2. DB ENUM SUBSET — app.job_error (migration 007) must be a SUBSET of
 *      errors.json, and the codes it is missing must be EXACTLY the ones
 *      errors.json's own `db_enum_note` names as legitimately absent. That
 *      "legitimate" set is DERIVED from the note's own text, not hardcoded
 *      here — it grew from 2 codes to 4 while this script was being written,
 *      when a concurrent session added ops codes for the admin router. A
 *      literal list in this file would have gone red on that legitimate
 *      change; reading the note instead means a THIRD-party doc update stays
 *      the single place this exception list is edited. A code missing from
 *      the DB enum with no matching mention in the note is still a new,
 *      unreviewed gap.
 *   3. FIELDMETA KEYS — every key backend/field_server.py can put into a
 *      FieldMeta dict, found by STATIC ANALYSIS of that file (not by reading
 *      a comment that claims to describe it), must appear in iface.pyi's
 *      FieldMeta. The frontend's own FieldMeta interface
 *      (facets/field-wells/index.ts) is checked too, but reported under a
 *      SEPARATE heading: this script owns contracts/, not src/, so a gap on
 *      that side is a finding, not something this gate can call "fixed".
 *
 * Run:  node scripts/check_contract_drift.mjs
 *
 * Exit codes (all nonzero = "not clean", but distinguishable):
 *   0  fully clean — contracts agree with the backend and with each other,
 *      AND the frontend's FieldMeta has caught up too.
 *   1  CONTRACT drift — one of contracts/iface.pyi, contracts/iface.d.ts
 *      disagrees with contracts/errors.json or with backend/field_server.py.
 *      This is the failure this script's own two files are meant to keep
 *      at zero.
 *   2  contracts are clean, but the frontend's FieldMeta interface
 *      (src/.../facets/field-wells/index.ts) is still missing backend keys —
 *      a real, reported, but not-fixable-from-here divergence.
 */
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

const ROOT = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), '..');
const read = p => fs.readFileSync(path.join(ROOT, p), 'utf8');

const out = [];
let contractFailed = false;
let frontendDrift = false;

function section(title) { out.push('', `── ${title} ${'─'.repeat(Math.max(0, 70 - title.length))}`); }
function ok(msg) { out.push(`  OK    ${msg}`); }
function fail(msg) { out.push(`  FAIL  ${msg}`); contractFailed = true; }
function frontendFail(msg) { out.push(`  FIND  ${msg}`); frontendDrift = true; }
function note(msg) { out.push(`  ..    ${msg}`); }

const setDiff = (a, b) => [...a].filter(x => !b.has(x)).sort();
const setEq = (a, b) => a.size === b.size && [...a].every(x => b.has(x));

// ── shared text-mining helpers ──────────────────────────────────────────────

/** Index just past the '}' matching the '{' at `openIdx` (simple depth
 * counting — safe here because none of the dict/object literals this parses
 * contain a brace inside a string value). */
function matchBrace(text, openIdx) {
    let depth = 0;
    for (let i = openIdx; i < text.length; i++) {
        if (text[i] === '{') depth++;
        else if (text[i] === '}') {
            depth--;
            if (depth === 0) return i + 1;
        }
    }
    throw new Error(`unbalanced braces starting at index ${openIdx}`);
}

/** Quoted dict/object keys directly inside a `{...}` block: 'key': or "key": */
function keysInBlock(block) {
    const out = new Set();
    for (const m of block.matchAll(/['"](\w+)['"]\s*:/g)) out.add(m[1]);
    return out;
}

/** `**identifier(` spreads inside a Python dict literal — a merge of another
 * function's returned dict (e.g. `**grid_spacing_meta(...)`). */
function spreadCallsInBlock(block) {
    const out = new Set();
    for (const m of block.matchAll(/\*\*(\w+)\(/g)) out.add(m[1]);
    return out;
}

/** For every match of `triggerRe` (which must end in a literal '{'), extract
 * the keys and `**fn(...)` spreads inside that brace-balanced block. */
function extractDictKeys(text, triggerRe) {
    const keys = new Set();
    const spreads = new Set();
    for (const m of text.matchAll(triggerRe)) {
        const openIdx = m.index + m[0].length - 1;
        const closeIdx = matchBrace(text, openIdx);
        const block = text.slice(openIdx, closeIdx);
        for (const k of keysInBlock(block)) keys.add(k);
        for (const s of spreadCallsInBlock(block)) spreads.add(s);
    }
    return { keys, spreads };
}

/** The keys of `def <fnName>(...): ... return {...}` — resolves a
 * `**fnName(...)` spread by reading what that function itself returns.
 * Handles exactly the shape this codebase uses: one unconditional
 * `return {...}` inside the function body. */
function returnDictKeys(text, fnName) {
    const defM = new RegExp(`\\bdef\\s+${fnName}\\s*\\(`).exec(text);
    if (!defM) return new Set();
    const rest = text.slice(defM.index + 1);
    const nextDef = rest.search(/\n(def |class )/);
    const body = nextDef === -1 ? text.slice(defM.index) : text.slice(defM.index, defM.index + 1 + nextDef);
    const retM = /return\s*\{/.exec(body);
    if (!retM) return new Set();
    const openIdx = retM.index + retM[0].length - 1;
    const closeIdx = matchBrace(body, openIdx);
    return keysInBlock(body.slice(openIdx, closeIdx));
}

/** The body of `def <fnName>(...): ...` up to (not including) the next
 * top-level `def `/`class `, as an exact substring of `text`. */
function functionBody(text, fnName) {
    const defM = new RegExp(`\\bdef\\s+${fnName}\\s*\\(`).exec(text);
    if (!defM) return '';
    const rest = text.slice(defM.index + 1);
    const nextDef = rest.search(/\n(def |class )/);
    return nextDef === -1 ? text.slice(defM.index) : text.slice(defM.index, defM.index + 1 + nextDef);
}

/** Text from `startRe`'s match (inclusive) up to `endRe`'s match (exclusive),
 * as an exact substring of `text`. Anchored on structural markers (endpoint
 * strings), never on line numbers, so an unrelated edit elsewhere in the file
 * cannot desync this from the code. */
function sliceBetween(text, startRe, endRe) {
    const s = startRe.exec(text);
    if (!s) throw new Error(`start anchor not found: ${startRe}`);
    const from = text.slice(s.index);
    const e = endRe.exec(from);
    return e ? from.slice(0, e.index) : from;
}

/** The body of a Python class (everything indented or blank after the
 * `class Name(...):` line, up to the first unindented non-blank line). */
function pyClassBody(text, className) {
    const startRe = new RegExp(`^class ${className}\\([^)]*\\):\\s*$`, 'm');
    const m = startRe.exec(text);
    if (!m) return null;
    const rest = text.slice(m.index).split('\n');
    const body = [];
    for (let i = 1; i < rest.length; i++) {
        const line = rest[i];
        if (line.trim() === '' || /^\s/.test(line)) body.push(line);
        else break;
    }
    return body.join('\n');
}

// ═════════════════════════════════════════════════════════════════════════
// 1 · ERROR VOCABULARY — errors.json vs iface.pyi vs iface.d.ts
// ═════════════════════════════════════════════════════════════════════════
section('1 · error vocabulary — errors.json vs iface.pyi vs iface.d.ts');

const errorsDoc = JSON.parse(read('contracts/errors.json'));
const canonical = new Set(Object.keys(errorsDoc.codes));
note(`errors.json declares ${canonical.size} codes: ${[...canonical].join(', ')}`);

const pyiText = read('contracts/iface.pyi');
const pyiErrM = /ErrorCode\s*=\s*Literal\[([^\]]*)\]/.exec(pyiText);
const pyiCodes = new Set(pyiErrM ? [...pyiErrM[1].matchAll(/'([^']+)'/g)].map(m => m[1]) : []);
if (!pyiErrM) {
    fail('iface.pyi: no `ErrorCode = Literal[...]` found at all');
} else {
    const missing = setDiff(canonical, pyiCodes);
    const extra = setDiff(pyiCodes, canonical);
    if (missing.length) fail(`iface.pyi ErrorCode is missing ${JSON.stringify(missing)} (present in errors.json)`);
    if (extra.length) fail(`iface.pyi ErrorCode has ${JSON.stringify(extra)}, which errors.json does not declare`);
    if (!missing.length && !extra.length) ok(`iface.pyi ErrorCode == errors.json codes (${pyiCodes.size}/${canonical.size})`);
}

const dtsText = read('contracts/iface.d.ts');
const dtsErrM = /export type ErrorCode\s*=([^;]*);/.exec(dtsText);
const dtsCodes = new Set(dtsErrM ? [...dtsErrM[1].matchAll(/'([^']+)'/g)].map(m => m[1]) : []);
if (!dtsErrM) {
    fail('iface.d.ts: no `export type ErrorCode = ...;` found at all');
} else {
    const missing = setDiff(canonical, dtsCodes);
    const extra = setDiff(dtsCodes, canonical);
    if (missing.length) fail(`iface.d.ts ErrorCode is missing ${JSON.stringify(missing)} (present in errors.json)`);
    if (extra.length) fail(`iface.d.ts ErrorCode has ${JSON.stringify(extra)}, which errors.json does not declare`);
    if (!missing.length && !extra.length) ok(`iface.d.ts ErrorCode == errors.json codes (${dtsCodes.size}/${canonical.size})`);
}
if (pyiErrM && dtsErrM && !setEq(pyiCodes, dtsCodes)) {
    fail('iface.pyi and iface.d.ts ErrorCode disagree directly: '
        + `only in .pyi=${JSON.stringify(setDiff(pyiCodes, dtsCodes))} `
        + `only in .d.ts=${JSON.stringify(setDiff(dtsCodes, pyiCodes))}`);
}

// ═════════════════════════════════════════════════════════════════════════
// 2 · DB ENUM SUBSET — app.job_error (migration 007) ⊆ errors.json
// ═════════════════════════════════════════════════════════════════════════
section('2 · app.job_error (migration 007) vs errors.json');

const migDir = path.join(ROOT, 'backend/db/migrations');
const mig007 = fs.readdirSync(migDir).find(f => /^007_.*\.sql$/.test(f));
if (!mig007) {
    fail('no backend/db/migrations/007_*.sql found');
} else {
    const migText = fs.readFileSync(path.join(migDir, mig007), 'utf8');
    const enumM = /CREATE\s+TYPE\s+app\.job_error\s+AS\s+ENUM\s*\(([^)]*)\)/i.exec(migText);
    if (!enumM) {
        fail(`${mig007}: no \`CREATE TYPE app.job_error AS ENUM (...)\` found`);
    } else {
        const dbCodes = new Set([...enumM[1].matchAll(/'([^']*)'/g)].map(m => m[1]));
        note(`${mig007} declares app.job_error = ${JSON.stringify([...dbCodes].sort())}`);

        const dbNotCanonical = setDiff(dbCodes, canonical);
        if (dbNotCanonical.length) {
            fail(`app.job_error carries ${JSON.stringify(dbNotCanonical)}, which errors.json does not `
                + 'declare — the DB enum is supposed to be a SUBSET (errors.json db_enum_note)');
        }

        // The "legitimately absent" set is NOT hardcoded here — it is DERIVED
        // from errors.json's own db_enum_note prose, every run. Measured need,
        // not a hypothetical: this set grew from 2 codes to 4 (NOT_FOUND,
        // DB_UNAVAILABLE joined BAD_HOST, OPEN_SHELL_SPIN_REQUIRED) WHILE this
        // very gate was being written, when a concurrent session extended the
        // admin router's error vocabulary. A literal `{'BAD_HOST', ...}` in
        // this file would have started failing the moment that legitimate,
        // documented change landed — which is exactly the false-positive that
        // makes a gate get disabled instead of trusted. errors.json's note is
        // the one place this list is allowed to change, so read it from there.
        const dbNoteText = (errorsDoc.db_enum_note ?? []).join(' ');
        const EXPECTED_ABSENT = new Set([...canonical].filter(code => dbNoteText.includes(code)));
        const actuallyAbsent = new Set(setDiff(canonical, dbCodes));
        const unexpectedAbsent = setDiff(actuallyAbsent, EXPECTED_ABSENT);
        const documentedButPresent = setDiff(EXPECTED_ABSENT, actuallyAbsent);

        if (EXPECTED_ABSENT.size === 0) {
            note("errors.json has no db_enum_note (or it names no codes) — this check has nothing "
                + 'to verify the "legitimate gap" against, so any absence below would count as new');
        }
        if (unexpectedAbsent.length) {
            fail(`app.job_error is missing ${JSON.stringify(unexpectedAbsent)}, and errors.json's `
                + `db_enum_note does not name ${unexpectedAbsent.length > 1 ? 'them' : 'it'} as a `
                + `legitimate omission (it names only ${JSON.stringify([...EXPECTED_ABSENT].sort())}) `
                + '— this is a NEW, unreviewed divergence');
        } else if (!dbNotCanonical.length) {
            ok('app.job_error ⊆ errors.json, and the only absent codes are exactly the ones '
                + `db_enum_note documents: ${JSON.stringify([...EXPECTED_ABSENT].sort())}`);
        }
        if (documentedButPresent.length) {
            note(`db_enum_note names ${JSON.stringify(documentedButPresent)} as absent, but `
                + 'app.job_error actually carries them now — the note is stale (not a failure, '
                + 'but worth a doc fix so the next reader is not told a false thing).');
        }
    }
}

// ═════════════════════════════════════════════════════════════════════════
// 3 · FIELDMETA KEYS — backend/field_server.py (ground truth) vs
//     iface.pyi's FieldMeta vs the frontend's FieldMeta interface
// ═════════════════════════════════════════════════════════════════════════
section("3 · FieldMeta keys — backend/field_server.py vs contracts vs frontend");

const backendPath = 'backend/field_server.py';
const backendText = read(backendPath);

// embed_molecule() builds EmbedMeta, not FieldMeta, and the HTTP handler's
// '/embed' branch mutates a variable that is ALSO just named `meta` for the
// same reason. Both must be excised before hunting for FieldMeta keys, or
// 'embed'/'seed'/'natoms_heavy'/... (EmbedMeta) would be misread as
// FieldMeta keys. Anchored on the function name and the endpoint string, not
// on line numbers, so a harmless edit elsewhere cannot desync this excision
// from the code.
const embedFnBody = functionBody(backendText, 'embed_molecule');
let embedHttpBranch = '';
try {
    embedHttpBranch = sliceBetween(backendText,
        /if self\.path == '\/embed':/, /if self\.path != '\/field':/);
} catch (e) {
    fail(`could not locate the '/embed' HTTP branch in ${backendPath} to exclude it `
        + `from FieldMeta extraction: ${e.message}`);
}

let fieldMetaSource = backendText;
for (const excise of [embedFnBody, embedHttpBranch]) {
    if (excise) fieldMetaSource = fieldMetaSource.replace(excise, '');
}

// (a) `meta = {...}` and `meta.update({...})` dict literals — covers
//     field_mep, field_mlp, field_quantum, and db_get_cube's cache-hit dict
//     plus its two `.update(` calls.
const passA = extractDictKeys(fieldMetaSource, /\bmeta\s*=\s*\{/g);
const passB = extractDictKeys(fieldMetaSource, /\bmeta\.update\(\s*\{/g);
const backendFieldMetaKeys = new Set([...passA.keys, ...passB.keys]);

// (b) resolve `**fn(...)` spreads by reading fn's own `return {...}` — this
//     is what catches grid_spacing_meta's spacing_requested/spacing/
//     grid_capped, which never appear as literal keys inside field_mep's or
//     field_mlp's own dict (they arrive only via `**grid_spacing_meta(...)`).
for (const fn of new Set([...passA.spreads, ...passB.spreads])) {
    const resolved = returnDictKeys(backendText, fn);
    if (resolved.size === 0) {
        note(`spread **${fn}(...) found inside a meta dict literal, but could not `
            + `resolve ${fn}()'s own return dict — if this is a real key source, `
            + 'the extraction under-counts');
    }
    for (const k of resolved) backendFieldMetaKeys.add(k);
}

// (c) `meta['key'] = ...` bracket assignments anywhere left in the excised
//     text — covers field_quantum's cube_seconds/cube_predicted_seconds and
//     the '/field' HTTP branch's total_seconds/cache/stored. The '/embed'
//     branch's `meta['seconds']` is already excised above, so this scan does
//     not need to be re-scoped.
for (const m of fieldMetaSource.matchAll(/\bmeta\[\s*['"](\w+)['"]\s*\]\s*=/g)) {
    backendFieldMetaKeys.add(m[1]);
}

if (backendFieldMetaKeys.size < 10) {
    fail(`static analysis found only ${backendFieldMetaKeys.size} FieldMeta key(s) in `
        + `${backendPath} — the extraction is almost certainly broken, not the backend `
        + '(expected on the order of 30+)');
}
note(`${backendFieldMetaKeys.size} FieldMeta keys found in ${backendPath} by static analysis: `
    + [...backendFieldMetaKeys].sort().join(', '));

// iface.pyi's FieldMeta TypedDict body
const pyiFieldMetaBody = pyClassBody(pyiText, 'FieldMeta');
const pyiFieldMetaKeys = new Set();
if (pyiFieldMetaBody === null) {
    fail('iface.pyi: could not locate `class FieldMeta(...):` body');
} else {
    for (const m of pyiFieldMetaBody.matchAll(/^\s{4}(\w+)\s*:/gm)) pyiFieldMetaKeys.add(m[1]);
}

const missingFromPyi = setDiff(backendFieldMetaKeys, pyiFieldMetaKeys);
const extraInPyi = setDiff(pyiFieldMetaKeys, backendFieldMetaKeys);
if (missingFromPyi.length) {
    fail('CONTRACT DRIFT (backend vs iface.pyi) — iface.pyi\'s FieldMeta is missing '
        + `${missingFromPyi.length} key(s) the backend emits: ${JSON.stringify(missingFromPyi)}`);
} else {
    ok('iface.pyi FieldMeta carries every key backend/field_server.py emits '
        + `(${pyiFieldMetaKeys.size} declared)`);
}
if (extraInPyi.length) {
    fail(`iface.pyi FieldMeta declares ${JSON.stringify(extraInPyi)}, which the backend `
        + 'extraction did not find anywhere — either a dead contract key or a broken '
        + 'extractor; check backend/field_server.py by hand before trusting either verdict');
}

// the frontend's FieldMeta interface (facets/field-wells/index.ts) — READ
// ONLY. This script never edits src/, but the whole point of separating this
// check is to attribute drift correctly, so it still has to look.
const frontendPath = 'src/app.frontend.facets.molstar-rdkit.editable/facets/field-wells/index.ts';
const frontendFieldMetaKeys = new Set();
let frontendReadable = true;
try {
    const frontendText = fs.readFileSync(path.join(ROOT, frontendPath), 'utf8');
    const feM = /interface FieldMeta\s*\{([\s\S]*?)\n\}/.exec(frontendText);
    if (!feM) {
        frontendReadable = false;
        frontendFail(`${frontendPath}: could not locate \`interface FieldMeta { ... }\` `
            + '— cannot check backend-vs-frontend drift at all');
    } else {
        for (const m of feM[1].matchAll(/^\s*(?:\/\*\*[\s\S]*?\*\/\s*)?(\w+)\??\s*:/gm)) {
            frontendFieldMetaKeys.add(m[1]);
        }
    }
} catch (e) {
    frontendReadable = false;
    frontendFail(`could not read ${frontendPath}: ${e.message}`);
}

if (frontendReadable) {
    const missingFromFrontend = setDiff(backendFieldMetaKeys, frontendFieldMetaKeys);
    if (missingFromFrontend.length) {
        frontendFail('BACKEND-VS-FRONTEND DRIFT (reported; not owned by this gate — '
            + `src/ is out of scope here) — ${frontendPath}'s FieldMeta interface is `
            + `missing ${missingFromFrontend.length} key(s) the backend emits: `
            + `${JSON.stringify(missingFromFrontend)}`);
    } else {
        ok(`${frontendPath} FieldMeta carries every key backend/field_server.py emits`);
    }
    const extraInFrontend = setDiff(frontendFieldMetaKeys, backendFieldMetaKeys);
    if (extraInFrontend.length) {
        note(`${frontendPath} FieldMeta declares ${JSON.stringify(extraInFrontend)}, which the `
            + 'backend extraction did not find — check by hand before trusting either side');
    }
}

// ═════════════════════════════════════════════════════════════════════════
// 4 · THE RUNTIME AUTHORITY — every key the backend emits must be DECLARED
//     in envelope.py's FIELD_META_SCHEMA, which normalize_meta() enforces at
//     request time.
//
//     WHY THIS CHECK EXISTS, measured today: three keys (model_caveat,
//     sigma_hole_representable, frontier_caveat) were added to field_server.py
//     by one session while normalize_meta() was being wired into both response
//     paths by another. At runtime that combination raised on EVERY mep and
//     homo request — survivable only because the compute path was written to
//     log the drift and serve un-normalised rather than discard an SCF. This
//     check moves that discovery from "the daemon is shouting in production"
//     to "the gate is red before the commit".
//
//     PROXY LEDGER. PROPERTY: no emitted key is undeclared. PROXY: string
//     literals mined from envelope.py's schema region. IMPLICATION: undeclared
//     ⇒ genuinely absent (sound), so a FAIL here is real. The reverse is
//     UNSOUND — a key that happens to appear as some other literal in that
//     region would be counted as declared. SAFE SIDE is therefore CONVICTION
//     ONLY: this check may fail loudly, and its silence means UNVERIFIED, not
//     proven-clean. normalize_meta() itself is the sound instrument, at the
//     cost of running only at request time.
// ═════════════════════════════════════════════════════════════════════════
section('4 · emitted keys vs envelope.py FIELD_META_SCHEMA (runtime authority)');
{
    const envText = read('backend/envelope.py');
    const start = envText.indexOf('_COMMON');
    const schemaIdx = envText.indexOf('FIELD_META_SCHEMA');
    const endBrace = envText.indexOf('}', schemaIdx);
    if (start < 0 || schemaIdx < 0 || endBrace < 0) {
        fail('could not locate the FIELD_META_SCHEMA region in backend/envelope.py — '
            + 'this check is blind, which is NOT the same as clean');
    } else {
        const region = envText.slice(start, endBrace + 1);
        // Drop `'mep':` style dict keys (kind names, not meta keys) so a kind
        // name cannot launder itself into the declared set.
        const declared = new Set(
            [...region.matchAll(/'([a-z_0-9]+)'(\s*):?/g)]
                .filter(m => m[0].slice(-1) !== ':')
                .map(m => m[1]));
        const undeclared = setDiff(backendFieldMetaKeys, declared);
        if (undeclared.length) {
            fail(`backend/field_server.py emits ${undeclared.length} key(s) that `
                + `envelope.py's FIELD_META_SCHEMA does not declare: `
                + `${JSON.stringify(undeclared)} — normalize_meta() will RAISE on every `
                + 'request carrying them. Add them to the schema region (the one home), '
                + 'with the scope note that says what the key is FOR.');
        } else {
            ok(`all ${backendFieldMetaKeys.size} emitted keys are declared in `
                + `FIELD_META_SCHEMA (${declared.size} declared names) — conviction-only `
                + 'check, see the ledger above');
        }
    }
}

// ── verdict ──────────────────────────────────────────────────────────────
section('verdict');
if (contractFailed) {
    out.push('  CONTRACT DRIFT — exit 1. Fix contracts/iface.pyi and/or contracts/iface.d.ts.');
} else if (frontendDrift) {
    out.push('  contracts/ are clean — exit 2. Frontend (src/, out of scope for this gate) has drifted.');
} else {
    out.push('  clean — exit 0.');
}

console.log(out.join('\n'));
process.exit(contractFailed ? 1 : (frontendDrift ? 2 : 0));
