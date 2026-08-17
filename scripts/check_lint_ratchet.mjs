#!/usr/bin/env node
/**
 * Keep inherited lint debt visible and monotonically bounded.
 *
 * The Mol* single tree was first put under the current ESLint config with substantial
 * pre-existing debt. A permanently red workflow is not a gate, while ignoring the tree
 * would hide regressions. This gate therefore requires exact group totals and separately
 * requires the product-shell surfaces to remain at zero. Any cleanup must lower the
 * matching baseline in this file in the same commit.
 */
import path from 'node:path';
import process from 'node:process';
import { ESLint } from 'eslint';

const ROOT = process.cwd();
const BASELINE = {
    app: { errors: 8, warnings: 0 },
    dirac: { errors: 186, warnings: 0 },
    chemistry: { errors: 92, warnings: 0 },
    inherited: { errors: 546, warnings: 193 },
};

const protectedFiles = new Set([
    'src/app.frontend.facets.molstar-rdkit.editable/index.ts',
    'scripts/extract_ts_digital_twin.mjs',
]);
const protectedPrefixes = ['src/app/shell/'];

function groupFor(file) {
    if (file.startsWith('src/app/')) return 'app';
    if (file.startsWith('src/app.frontend.facets.molstar-rdkit.editable/')) return 'dirac';
    if (file.startsWith('src/chemistry.backend.perception.rdkit-wasm.editable/')) return 'chemistry';
    return 'inherited';
}

const results = await new ESLint({ cwd: ROOT }).lintFiles(['.']);
const counts = Object.fromEntries(Object.keys(BASELINE).map(key => [key, { errors: 0, warnings: 0 }]));
const protectedViolations = [];

for (const result of results) {
    const file = path.relative(ROOT, result.filePath).replaceAll(path.sep, '/');
    // The developer workstation has a local Python environment that GitHub does not.
    // Its vendored browser JavaScript is neither repository source nor stable across hosts.
    if (file.startsWith('backend/env/')) continue;
    const group = counts[groupFor(file)];
    group.errors += result.errorCount;
    group.warnings += result.warningCount;
    if ((protectedFiles.has(file) || protectedPrefixes.some(prefix => file.startsWith(prefix)))
        && (result.errorCount || result.warningCount)) {
        protectedViolations.push({ file, result });
    }
}

let failed = false;
for (const [group, expected] of Object.entries(BASELINE)) {
    const actual = counts[group];
    const matches = actual.errors === expected.errors && actual.warnings === expected.warnings;
    console.log(`${matches ? 'OK  ' : 'FAIL'} ${group.padEnd(10)} `
        + `${actual.errors} errors / ${actual.warnings} warnings `
        + `(baseline ${expected.errors} / ${expected.warnings})`);
    if (!matches) failed = true;
}

if (protectedViolations.length) {
    failed = true;
    console.error('\nProduct-shell surfaces must remain lint-clean:');
    for (const { file, result } of protectedViolations) {
        for (const message of result.messages.slice(0, 8)) {
            console.error(`  ${file}:${message.line}:${message.column} ${message.ruleId}: ${message.message}`);
        }
    }
}

if (failed) {
    console.error('\nLint ratchet moved. Fix new findings; if debt was removed, lower the explicit baseline.');
    process.exit(1);
}
console.log('lint ratchet OK · inherited debt did not move · product shell is clean');
