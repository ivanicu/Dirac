#!/usr/bin/env node
/** Continuously regenerate the Dirac Architecture Optimization Twin on source changes. */
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { execFileSync, spawn } from 'node:child_process';

const ROOT = path.resolve(import.meta.dirname, '..');
const scope = JSON.parse(fs.readFileSync(path.join(ROOT, 'scripts/digital_twin_scope.json'), 'utf8'));
const rootFiles = new Set(scope.include_root_files);
const roots = scope.include_roots.map(x => x.replace(/\/$/, ''));
const externalRoots = scope.external_roots.map(x => x.replace(/\/$/, ''));
const externalPathspecs = externalRoots.flatMap(root => [
    `:(exclude)${root}`,
    `:(exclude)${root}/**`,
]);
const outputs = new Set(scope.generated_outputs);
let timer;
let running = false;
let rerun = false;
const changed = new Set();

function normalized(value) {
    return value.replaceAll(path.sep, '/').replace(/^\.\//, '');
}

function inScope(rel) {
    rel = normalized(rel);
    if (outputs.has(rel)) return false;
    const explicit = rootFiles.has(rel) || roots.some(root => rel === root || rel.startsWith(`${root}/`));
    const external = externalRoots.some(root => rel === root || rel.startsWith(`${root}/`));
    if (!explicit && (!scope.auto_include_code_extensions.includes(path.extname(rel)) || external)) return false;
    if (scope.exclude_fragments.some(fragment => `/${rel}`.includes(fragment))) return false;
    if (scope.exclude_suffixes.some(suffix => rel.endsWith(suffix))) return false;
    return scope.extensions.includes(path.extname(rel));
}

function build(reason = 'initial synchronization') {
    if (running) {
        rerun = true;
        return;
    }
    running = true;
    const reasons = [...changed];
    changed.clear();
    const stamp = new Date().toISOString();
    process.stdout.write(`[${stamp}] twin rebuild · ${reason}${reasons.length ? ` · ${reasons.slice(0, 8).join(', ')}` : ''}\n`);
    const child = spawn('python3', ['scripts/build_digital_twin.py'], {
        cwd: ROOT, stdio: 'inherit', env: process.env,
    });
    child.on('exit', code => {
        running = false;
        if (code !== 0) process.stderr.write(`[${new Date().toISOString()}] twin rebuild failed (${code})\n`);
        if (rerun || changed.size) {
            rerun = false;
            schedule('changes arrived during rebuild');
        }
    });
}

function schedule(reason) {
    clearTimeout(timer);
    timer = setTimeout(() => build(reason), 900);
}

function sourceFiles() {
    // Exclude classified external roots inside Git itself. Filtering only after
    // capture made a 6.4 GB conda runtime emit 175k paths and overflow maxBuffer
    // before inScope() had a chance to reject a single one.
    const raw = execFileSync('git', [
        'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', ...externalPathspecs,
    ], {
        cwd: ROOT, encoding: 'buffer', maxBuffer: 32 * 1024 * 1024,
    });
    return raw.toString().split('\0').filter(Boolean).map(normalized).filter(inScope).sort();
}

function sourceState() {
    const state = new Map();
    for (const rel of sourceFiles()) {
        try {
            const absolute = path.join(ROOT, rel);
            if (!fs.statSync(absolute).isFile()) continue;
            const digest = createHash('sha256').update(fs.readFileSync(absolute)).digest('hex');
            state.set(rel, digest);
        } catch (error) {
            if (error?.code !== 'ENOENT') throw error;
        }
    }
    return state;
}

function changedFiles(previous, current) {
    const paths = new Set([...previous.keys(), ...current.keys()]);
    return [...paths].filter(rel => previous.get(rel) !== current.get(rel)).sort();
}

if (process.argv.includes('--selftest')) {
    const probes = ['backend/new_module.py', 'src/app/new-view.ts', 'future-dir/new.py', 'bin/new-command',
        'src/mol-plugin/state.ts', 'openfe-runtime-v2/pkgs/tool/run.py',
        'docs/architecture/dirac-digital-twin.json'];
    const verdicts = probes.map(file => ({ file, watched: inScope(file) }));
    const expected = [true, true, true, true, false, false, false];
    if (verdicts.some((item, i) => item.watched !== expected[i])) {
        process.stderr.write(`${JSON.stringify(verdicts)}\n`);
        process.exit(1);
    }
    const discovered = sourceState().size;
    if (!discovered) throw new Error('source poller discovered no first-party files');
    process.stdout.write(`watch scope OK · ${roots.length} first-party roots · ${discovered} files polled · upstream and generated outputs excluded\n`);
    process.exit(0);
}

if (process.argv.includes('--once')) {
    build('one-shot synchronization');
} else {
    let baseline = sourceState();
    build();
    setInterval(() => {
        try {
            const current = sourceState();
            const updates = changedFiles(baseline, current);
            baseline = current;
            if (!updates.length) return;
            for (const rel of updates) changed.add(rel);
            schedule('source change');
        } catch (error) {
            process.stderr.write(`[${new Date().toISOString()}] source poll failed · ${error.message}\n`);
        }
    }, 2_000);
    process.stdout.write(`polling ${baseline.size} first-party files · interval 2 s · debounce 900 ms · runtime captured on rebuild\n`);
}
