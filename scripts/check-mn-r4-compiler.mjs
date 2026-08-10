#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import process from 'node:process';
import { DefaultProgramRoots, stableStringify } from './lib/mn-graph-compiler.mjs';

const repoRoot = resolve(dirname(process.argv[1]), '..');
const compiledRoot = resolve(repoRoot, 'src/mol-plugin-chem/visual-r4/compiled');
const bundle = JSON.parse(readFileSync(resolve(compiledRoot, 'mn-r4.bundle.json'), 'utf8'));
const errors = [];

function sha256(value) {
    return createHash('sha256').update(value).digest('hex');
}

function check(condition, message) {
    if (!condition) errors.push(message);
}

check(bundle.schema === 'molecular-representation-ir@1', `unexpected schema ${bundle.schema}`);
const payload = { ...bundle };
delete payload.artifact;
check(sha256(stableStringify(payload)) === bundle.artifact.hash, 'bundle payload hash mismatch');
check(stableStringify(bundle.programs.map(program => program.name).sort()) === stableStringify([...DefaultProgramRoots].sort()), 'public program roots differ');

const groupNames = new Set(bundle.groups.map(group => group.name));
const groupIds = new Set(bundle.groups.map(group => group.id));
check(groupNames.size === bundle.groups.length, 'duplicate group name');
check(groupIds.size === bundle.groups.length, 'duplicate group id');

let links = 0;
for (const graph of [...bundle.groups, ...bundle.materials]) {
    const nodes = new Map(graph.nodes.map(node => [node.id, node]));
    check(nodes.size === graph.nodes.length, `duplicate node id in ${graph.name}`);
    for (const dependency of graph.dependencies) check(groupNames.has(dependency), `${graph.name} has missing dependency ${dependency}`);
    for (const node of graph.nodes) {
        if (node.call) check(groupNames.has(node.call), `${graph.name}/${node.name} calls missing group ${node.call}`);
    }
    for (const link of graph.links) {
        links++;
        const from = nodes.get(link.from);
        const to = nodes.get(link.to);
        check(from?.outputs.some(socket => socket.id === link.out), `${graph.name} has dangling output ${link.from}.${link.out}`);
        check(to?.inputs.some(socket => socket.id === link.in), `${graph.name} has dangling input ${link.to}.${link.in}`);
    }
}
check(links === bundle.stats.links, `link count ${links} differs from stats ${bundle.stats.links}`);
for (const program of bundle.programs) check(groupIds.has(program.entry), `${program.name} has missing entry ${program.entry}`);
for (const library of bundle.libraries) check(groupIds.has(library.entry), `${library.name} has missing entry ${library.entry}`);

for (const kernel of bundle.runtime.kernels) {
    const bytes = readFileSync(resolve(compiledRoot, kernel.source));
    check(sha256(bytes) === kernel.hash, `${kernel.id} WGSL hash mismatch`);
}

if (errors.length > 0) {
    for (const error of errors.slice(0, 50)) console.error(`- ${error}`);
    if (errors.length > 50) console.error(`... ${errors.length - 50} more errors`);
    process.exitCode = 1;
} else {
    const operations = new Set([...bundle.groups, ...bundle.materials].flatMap(graph => graph.nodes.map(node => node.op)));
    console.log(stableStringify({
        schema: bundle.schema,
        hash: bundle.artifact.hash,
        programs: bundle.programs.length,
        libraries: bundle.libraries.length,
        groups: bundle.groups.length,
        materials: bundle.materials.length,
        nodes: bundle.stats.retainedNodes,
        links,
        danglingLinks: 0,
        operationKinds: operations.size,
        kernels: bundle.runtime.kernels.length,
    }, 2));
}
