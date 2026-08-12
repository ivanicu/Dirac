#!/usr/bin/env node
/** Continuously regenerate the Dirac Architecture Optimization Twin on source changes. */
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';

const ROOT = path.resolve(import.meta.dirname, '..');
const scope = JSON.parse(fs.readFileSync(path.join(ROOT, 'scripts/digital_twin_scope.json'), 'utf8'));
const rootFiles = new Set(scope.include_root_files);
const roots = scope.include_roots.map(x => x.replace(/\/$/, ''));
const externalRoots = scope.external_roots.map(x => x.replace(/\/$/, ''));
const outputs = new Set(scope.generated_outputs);
const watched = new Map();
let timer;
let running = false;
let rerun = false;
let changed = new Set();

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

function observed(base, filename) {
    if (!filename) return;
    const rel = normalized(path.relative(ROOT, path.join(base, filename.toString())));
    if (!inScope(rel)) return;
    changed.add(rel);
    schedule('source change');
}

function watchDirectory(absolute) {
    if (watched.has(absolute) || !fs.existsSync(absolute)) return;
    const watcher = fs.watch(absolute, { recursive: true }, (_event, filename) => observed(absolute, filename));
    watcher.on('error', () => {
        watcher.close();
        watched.delete(absolute);
    });
    watched.set(absolute, watcher);
}

function watchChildren(parent, parentRel) {
    const key = `${parent}:children`;
    if (watched.has(key) || !fs.existsSync(parent)) return;
    for (const entry of fs.readdirSync(parent, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue;
        const rel = normalized(`${parentRel}/${entry.name}`);
        if (!externalRoots.some(root => rel === root || rel.startsWith(`${root}/`))) {
            watchDirectory(path.join(parent, entry.name));
        }
    }
    const watcher = fs.watch(parent, (_event, filename) => {
        if (!filename) return;
        const name = filename.toString();
        const rel = normalized(`${parentRel}/${name}`);
        const absolute = path.join(parent, name);
        const external = externalRoots.some(root => rel === root || rel.startsWith(`${root}/`));
        if (fs.existsSync(absolute) && fs.statSync(absolute).isDirectory() && !external) {
            watchDirectory(absolute);
        } else if (inScope(rel)) {
            observed(parent, filename);
        }
    });
    watched.set(key, watcher);
}

if (process.argv.includes('--selftest')) {
    const probes = ['backend/new_module.py', 'src/app/new-view.ts', 'future-dir/new.py', 'bin/new-command',
        'src/mol-plugin/state.ts', 'docs/architecture/dirac-digital-twin.json'];
    const verdicts = probes.map(file => ({ file, watched: inScope(file) }));
    const expected = [true, true, true, true, false, false];
    if (verdicts.some((item, i) => item.watched !== expected[i])) {
        process.stderr.write(`${JSON.stringify(verdicts)}\n`);
        process.exit(1);
    }
    process.stdout.write(`watch scope OK · ${roots.length} first-party roots · upstream and generated outputs excluded\n`);
    process.exit(0);
}

if (process.argv.includes('--once')) {
    build('one-shot synchronization');
} else {
    build();
    for (const root of roots) watchDirectory(path.join(ROOT, root));
    for (const entry of fs.readdirSync(ROOT, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue;
        if (entry.name === 'src') {
            watchChildren(path.join(ROOT, 'src'), 'src');
            continue;
        }
        if (externalRoots.includes(entry.name)) continue;
        watchDirectory(path.join(ROOT, entry.name));
    }
    fs.watch(ROOT, (_event, filename) => {
        if (!filename) return;
        const name = filename.toString();
        const absolute = path.join(ROOT, name);
        if (!fs.existsSync(absolute) && watched.has(absolute)) {
            watched.get(absolute).close();
            watched.delete(absolute);
        }
        if (fs.existsSync(absolute) && fs.statSync(absolute).isDirectory()
            && !externalRoots.some(root => name === root || root.startsWith(`${name}/`))) {
            watchDirectory(absolute);
        }
        if (rootFiles.has(name) || inScope(name)) observed(ROOT, filename);
    });
    process.stdout.write(`watching ${roots.length} first-party roots · debounce 900 ms\n`);
}
