// scripts/lib/mutate.mjs — a red proof that cannot lie about having happened.
//
// THE DEFECT THIS EXISTS FOR, committed 2026-08-11 while proving a gate works:
// to show scripts/check_contract_drift.mjs convicts, I removed the string
// `'DB_UNAVAILABLE',` from contracts/iface.pyi. That line has no trailing comma.
// The replace matched nothing, the file was never modified, the gate correctly
// stayed green — and I read the green as "the gate does not convict."
//
// The failure mode is the one this repo keeps meeting from every direction: THE
// EXPERIMENT NEVER RAN, AND ITS NON-EXECUTION RENDERED AS A RESULT. A silent
// no-op mutation produces output indistinguishable from a genuine negative, so
// the harness certifies the absence of a defect it never looked for. That is a
// false ACQUITTAL, and nobody re-examines a cleared claim.
//
// So the rule, mechanised rather than remembered: a mutation harness must assert
// THE SOURCE CHANGED before it asserts anything about the verdict.
//
// It also restores byte-exactly and verifies the restore, because the second way
// a red proof does damage is by leaving the mutation behind — in this repo a
// sync timer commits and pushes the worktree every 60 s, so a mutation that
// outlives its proof by one minute is a mutation that ships.

import fs from 'node:fs';

export class NoOpMutation extends Error {}
export class RestoreFailed extends Error {}

/**
 * Apply `transform` to `file`, run `body()`, then restore the file exactly.
 *
 * @param {string} file       absolute path to mutate
 * @param {(s: string) => string} transform  must return DIFFERENT text
 * @param {(ctx: {original: string, mutated: string}) => any} body
 * @returns whatever `body` returns
 *
 * Throws NoOpMutation if the transform did not change the bytes — BEFORE body
 * runs, so a broken proof can never report a verdict at all. Throws
 * RestoreFailed if the file does not come back byte-identical, which is louder
 * than leaving a mutated file behind quietly.
 */
export function withMutation(file, transform, body) {
    const original = fs.readFileSync(file, 'utf8');
    const mutated = transform(original);

    if (mutated === original) {
        throw new NoOpMutation(
            `MUTATION WAS A NO-OP on ${file} — the transform matched nothing, so the `
            + 'proof that follows would have tested the UNMODIFIED file and reported its '
            + 'green as evidence the check cannot convict. Fix the transform (the usual '
            + 'cause is an assumed trailing comma, quote style, or indentation) and only '
            + 'then trust the verdict.');
    }

    fs.writeFileSync(file, mutated);
    try {
        return body({ original, mutated });
    } finally {
        fs.writeFileSync(file, original);
        const restored = fs.readFileSync(file, 'utf8');
        if (restored !== original) {
            throw new RestoreFailed(
                `${file} did not restore byte-identically after the red proof — the `
                + 'worktree is now dirty with a deliberate defect. This repo auto-commits '
                + 'and pushes on a timer, so fix it by hand NOW rather than after the '
                + 'next sync.');
        }
    }
}

/**
 * Convenience: mutate by replacing the FIRST occurrence of `needle`.
 * Kept separate from withMutation so the no-op check still owns the verdict —
 * a caller passing a needle that is not present gets NoOpMutation, not silence.
 */
export function replacingOnce(needle, replacement = '') {
    return text => {
        const at = text.indexOf(needle);
        return at < 0 ? text
            : text.slice(0, at) + replacement + text.slice(at + needle.length);
    };
}
