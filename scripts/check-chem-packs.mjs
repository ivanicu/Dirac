/**
 * Verifies that every public chemistry pack can be bundled independently
 * without pulling in Mol*'s React UI layer.
 */

import * as esbuild from 'esbuild';

const root = process.cwd();

const entries = [
    ['api', `import { createChemWorkbench } from './src/mol-plugin-chem/index.ts'; console.log(createChemWorkbench);`],
    ['core', `import { corePack } from './src/mol-plugin-chem/packs/core.ts'; console.log(corePack);`],
    ['annotations', `import { annotationsPack } from './src/mol-plugin-chem/packs/annotations.ts'; console.log(annotationsPack);`],
    ['chemistry', `import { chemistryPack } from './src/mol-plugin-chem/packs/chemistry.ts'; console.log(chemistryPack);`],
    ['validation', `import { validationPack } from './src/mol-plugin-chem/packs/validation.ts'; console.log(validationPack);`],
    ['md', `import { mdPack } from './src/mol-plugin-chem/packs/md.ts'; console.log(mdPack);`],
    ['density', `import { densityPack } from './src/mol-plugin-chem/packs/density.ts'; console.log(densityPack);`],
    ['qm', `import { qmPack } from './src/mol-plugin-chem/packs/qm.ts'; console.log(qmPack);`],
    ['sites', `import { sitesPack } from './src/mol-plugin-chem/packs/sites.ts'; console.log(sitesPack);`],
    ['nucleic', `import { nucleicPack } from './src/mol-plugin-chem/packs/nucleic.ts'; console.log(nucleicPack);`],
    ['publication', `import { publicationPack } from './src/mol-plugin-chem/packs/publication.ts'; console.log(publicationPack);`],
    ['collaboration', `import { collaborationPack } from './src/mol-plugin-chem/packs/collaboration.ts'; console.log(collaborationPack);`],
    ['visual-r4', `import { visualR4Pack } from './src/mol-plugin-chem/visual-r4/index.ts'; console.log(visualR4Pack);`],
    ['all', `import { allChemPacks } from './src/mol-plugin-chem/presets.ts'; console.log(allChemPacks);`],
];

const forbiddenInput = /(?:^|\/)src\/mol-plugin-ui(?:\/|$)|node_modules\/(?:react|react-dom)(?:\/|$)|\.tsx$/;
async function main() {
    let failed = false;

    for (const [name, contents] of entries) {
        const result = await esbuild.build({
            absWorkingDir: root,
            stdin: { contents, resolveDir: root, sourcefile: `chem-pack-${name}.ts`, loader: 'ts' },
            bundle: true,
            write: false,
            metafile: true,
            treeShaking: true,
            platform: 'browser',
            format: 'esm',
            target: 'es2022',
            external: ['crypto', 'fs', 'path', 'stream'],
            define: {
                'process.env.NODE_ENV': JSON.stringify('production'),
                'process.env.DEBUG': JSON.stringify(false),
                __MOLSTAR_PLUGIN_VERSION__: JSON.stringify('pack-check'),
                __MOLSTAR_BUILD_TIMESTAMP__: '0',
            },
            logLevel: 'silent',
        });

        const inputs = Object.keys(result.metafile.inputs);
        const forbidden = inputs.filter(input => forbiddenInput.test(input));
        const bytes = result.outputFiles.reduce((sum, file) => sum + file.contents.byteLength, 0);
        console.log(`${name.padEnd(13)} ${String(inputs.length).padStart(5)} modules  ${(bytes / 1024).toFixed(1).padStart(9)} KiB`);

        if (forbidden.length) {
            failed = true;
            console.error(`  framework UI dependencies: ${forbidden.join(', ')}`);
        }
    }

    if (failed) process.exitCode = 1;
    else console.log('All chemistry pack entrypoints are framework-UI-free.');
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
