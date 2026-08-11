/**
 * Generates src/app/services/error-codes.ts from contracts/errors.json.
 *
 * This is the TypeScript half of the fix described in errors.json's own
 * `$comment`: three vocabularies that used to disagree (the service's
 * free-text reason strings, the DB's app.job_error enum, and whatever the
 * frontend could branch on — which was nothing, because it could only render
 * free text). backend/envelope.py is the Python half; neither hand-copies
 * the other, both read the same JSON file.
 *
 * Deterministic by construction: the emitted key order is exactly the
 * `codes` object's own key order in errors.json (JSON.parse preserves
 * string-key insertion order in V8, so this is byte-identical across runs
 * for a fixed input file — nothing here sorts, hashes, or depends on
 * iteration order that could vary between Node versions).
 *
 * Run: node scripts/gen_error_codes.mjs
 * Verified in sync by: backend/tests/test_envelope.py
 * (re-runs this generator into a temp dir and byte-compares the output).
 */

import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

const ROOT = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), '..');
const SOURCE = path.join(ROOT, 'contracts', 'errors.json');
const OUT = path.join(ROOT, 'src', 'app', 'services', 'error-codes.ts');

export function render(doc) {
    const codes = doc.codes;
    const names = Object.keys(codes);

    const entries = names.map(name => {
        const c = codes[name];
        const fields = [
            `user_copy: ${JSON.stringify(c.user_copy)}`,
            `retryable: ${JSON.stringify(!!c.retryable)}`,
            `points_at: ${JSON.stringify(c.points_at ?? null)}`,
        ].join(', ');
        return `    ${name}: { ${fields} },`;
    }).join('\n');

    return `// GENERATED — do not edit by hand.
// Source: contracts/errors.json (version ${doc.version}).
// Regenerate: node scripts/gen_error_codes.mjs
//
// One home, two languages: backend/envelope.py reads the same JSON file for
// the Python side. A code that exists in one and not the other is exactly
// the drift contracts/errors.json's own $comment describes — see there for
// the three-vocabularies incident this file exists to prevent.

/** Per-code caller-facing copy. \`points_at\` is the working alternative when
 *  one exists (e.g. UNPARAMETERIZED -> the QM field that doesn't need params). */
export const ERROR_CODES = {
${entries}
} as const;

/** The full, and only, error vocabulary — derived from the object above so
 *  it can never list a code ERROR_CODES does not also carry. */
export type ErrorCode = keyof typeof ERROR_CODES;
`;
}

function main() {
    const doc = JSON.parse(fs.readFileSync(SOURCE, 'utf8'));
    const out = render(doc);
    const outArg = process.argv[2];
    const target = outArg ? path.resolve(outArg) : OUT;
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, out);
    console.log(`wrote ${path.relative(ROOT, target)} (${Object.keys(doc.codes).length} codes, from ${path.relative(ROOT, SOURCE)})`);
}

if (import.meta.url === url.pathToFileURL(process.argv[1] ?? '').href) {
    main();
}
