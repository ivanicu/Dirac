import fs from 'node:fs/promises';
import path from 'node:path';
import * as esbuild from 'esbuild';

const repo = path.resolve(import.meta.dirname, '..');
const source = path.join(repo, 'src/app.frontend.facets.molstar-rdkit.editable');
const output = path.join(repo, 'build/discovery-lab');
const production = process.argv.includes('--prd');

await fs.rm(output, { recursive: true, force: true });
await fs.mkdir(output, { recursive: true });

for (const [entry, outfile] of [
    ['discovery-lab/discovery-lab.ts', 'discovery-lab.js'],
    ['fep-workbench/fep-workbench.ts', 'fep-workbench.js'],
    ['field-workbench/field-workbench.ts', 'field-workbench.js'],
]) {
    await esbuild.build({
        entryPoints: [path.join(source, entry)],
        outfile: path.join(output, outfile),
        bundle: true,
        platform: 'browser',
        minify: production,
        minifyIdentifiers: false,
        sourcemap: !production,
        define: {
            'process.env.NODE_ENV': JSON.stringify(production ? 'production' : 'development'),
            'process.env.DEBUG': 'false',
        },
    });
}

for (const [from, to] of [
    ['discovery-lab/index.html', 'index.html'],
    ['discovery-lab/discovery-lab.css', 'discovery-lab.css'],
    ['fep-workbench/fep-workbench.html', 'fep-workbench.html'],
    ['fep-workbench/fep-workbench.css', 'fep-workbench.css'],
    ['fep-workbench/research-loop-panel.css', 'research-loop-panel.css'],
    ['field-workbench/field-workbench.html', 'field-workbench.html'],
    ['field-workbench/field-workbench.css', 'field-workbench.css'],
]) await fs.copyFile(path.join(source, from), path.join(output, to));

await fs.cp(path.join(source, 'fonts'), path.join(output, 'fonts'), { recursive: true });
await fs.cp(path.join(source, 'assets/rdkit'), path.join(output, 'assets/rdkit'), { recursive: true });
await fs.copyFile(path.join(repo, 'examples/docking/receptor_1.pdb'), path.join(output, 'assets/receptor.pdb'));
await fs.copyFile(path.join(repo, 'examples/docking/ligands_1.sdf'), path.join(output, 'assets/ligand.sdf'));

console.log(`Motif Workbench built at ${output}`);
