import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { contract } from '../docs/product/hci/human-interface-v2.source.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const target = path.join(root, contract.generatedArtifact);
const rendered = `${JSON.stringify(contract, null, 2)}\n`;

if (process.argv.includes('--check')) {
    const current = await readFile(target, 'utf8').catch(() => '');
    if (current !== rendered) {
        console.error(`${contract.generatedArtifact} is stale; run node scripts/generate-human-interface-contract.mjs`);
        process.exit(1);
    }
    console.log(`HCI generated contract is current (${contract.requirements.length} requirements).`);
} else {
    await writeFile(target, rendered);
    console.log(`Generated ${contract.generatedArtifact} (${contract.requirements.length} requirements).`);
}
