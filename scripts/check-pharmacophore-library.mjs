/**
 * Validation gate for the Pharmacophore Designer screening library.
 *
 * Uses the SAME RDKit build (@rdkit/rdkit 2025.03.4) and the SAME SMARTS
 * definitions as the runtime, so a pass here means the browser sees the same
 * numbers. Checks, in order:
 *
 *   1. Every entry's SMILES parses to a valid molecule.
 *   2. No two entries share a canonical SMILES (paste-dup detector).
 *   3. SMARTS parity: the donor/acceptor patterns hard-coded here match the
 *      literals in semantic-chemistry-rdkit.ts, and the hydrophobic pattern
 *      matches the literal in the facet's screening.ts (anti-drift check —
 *      the substrate file is shared and must not be silently diverged from).
 *   4. Probe assertions: exact expected feature counts for benzene,
 *      cyclohexane, pyridine, and caffeine — the screening engine's
 *      positive/negative controls. A zero from a broken instrument cannot
 *      pass this (the instrument must localize known chemistry first).
 *
 * Run: node scripts/check-pharmacophore-library.mjs
 */

import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

const require = createRequire(import.meta.url);
const initRDKitModule = require('@rdkit/rdkit');

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), '..');

// Same literals as the runtime. Parity with the sources is asserted below.
const DONOR_SMARTS = '[#7,#8;H1,H2,H3]';
const ACCEPTOR_SMARTS = '[#7,#8;H0,H1,H2;!+]';
const HYDROPHOBIC_SMARTS = '[#6;!$([#6]~[#7,#8])]';
const RING_SMARTS = ['a1aaaaa1', 'a1aaaa1'];

/** Exact expected counts for the probe molecules, from chemistry, not from RDKit. */
const PROBE_EXPECTATIONS = {
    benzene: { hba: 0, hbd: 0, aromatic: 1, hydrophobic: 6 },
    cyclohexane: { hba: 0, hbd: 0, aromatic: 0, hydrophobic: 6 },
    pyridine: { hba: 1, hbd: 0, aromatic: 1, hydrophobic: 3 },
    // Lipinski-style counts (all N + O minus charged): caffeine has 4 N + 2 O.
    caffeine: { hba: 6, hbd: 0, aromatic: 2, hydrophobic: 0 },
};

function fail(message) {
    console.error(`✗ ${message}`);
    process.exitCode = 1;
}

function extractLibrary() {
    const source = fs.readFileSync(
        path.join(root, 'src/app.frontend.facets.molstar-rdkit.editable/facets/pharmacophore-designer/library.ts'),
        'utf8',
    );
    const entries = [];
    const re = /\{\s*id:\s*'([^']+)',\s*name:\s*'([^']+)',\s*smiles:\s*'([^']+)',\s*category:\s*'([^']+)'\s*\}/g;
    let match;
    while ((match = re.exec(source)) !== null) {
        entries.push({ id: match[1], name: match[2], smiles: match[3], category: match[4] });
    }
    return entries;
}

function assertSmartsParity() {
    const substrate = fs.readFileSync(
        path.join(root, 'src/chemistry.backend.perception.rdkit-wasm.editable/semantic-chemistry-rdkit.ts'),
        'utf8',
    );
    if (!substrate.includes(`'${DONOR_SMARTS}'`)) fail(`Donor SMARTS drifted from semantic-chemistry-rdkit.ts (expected ${DONOR_SMARTS})`);
    if (!substrate.includes(`'${ACCEPTOR_SMARTS}'`)) fail(`Acceptor SMARTS drifted from semantic-chemistry-rdkit.ts (expected ${ACCEPTOR_SMARTS})`);
    for (const ring of RING_SMARTS) {
        if (!substrate.includes(`'${ring}'`)) fail(`Ring SMARTS drifted from semantic-chemistry-rdkit.ts (expected ${ring})`);
    }
    const screening = fs.readFileSync(
        path.join(root, 'src/app.frontend.facets.molstar-rdkit.editable/facets/pharmacophore-designer/screening.ts'),
        'utf8',
    );
    if (!screening.includes(`'${HYDROPHOBIC_SMARTS}'`)) fail(`Hydrophobic SMARTS drifted from screening.ts (expected ${HYDROPHOBIC_SMARTS})`);
}

function matchedAtomCount(mol, qmol) {
    const raw = JSON.parse(mol.get_substruct_matches(qmol));
    if (!Array.isArray(raw)) return 0;
    const atoms = new Set();
    for (const m of raw) for (const a of m.atoms ?? []) atoms.add(a);
    return atoms.size;
}

function ringCount(mol, qmols) {
    const seen = new Set();
    for (const qmol of qmols) {
        const raw = JSON.parse(mol.get_substruct_matches(qmol));
        if (!Array.isArray(raw)) continue;
        for (const m of raw) {
            const atoms = m.atoms ?? [];
            if (atoms.length < 5) continue;
            seen.add([...atoms].sort((a, b) => a - b).join(','));
        }
    }
    return seen.size;
}

const RDKit = await initRDKitModule();
console.log(`RDKit ${RDKit.version()}`);

assertSmartsParity();

const entries = extractLibrary();
if (entries.length < 50) fail(`Library extraction found only ${entries.length} entries — regex out of sync with library.ts?`);

const qDonor = RDKit.get_qmol(DONOR_SMARTS);
const qAcceptor = RDKit.get_qmol(ACCEPTOR_SMARTS);
const qHydrophobic = RDKit.get_qmol(HYDROPHOBIC_SMARTS);
const qRings = RING_SMARTS.map(s => RDKit.get_qmol(s));

const canonicalSeen = new Map();
let probesChecked = 0;

for (const entry of entries) {
    const mol = RDKit.get_mol(entry.smiles);
    if (!mol || !mol.is_valid()) {
        fail(`${entry.id}: SMILES does not parse: ${entry.smiles}`);
        continue;
    }
    const canonical = mol.get_smiles();
    if (canonicalSeen.has(canonical)) fail(`${entry.id}: duplicate of ${canonicalSeen.get(canonical)} (same canonical SMILES)`);
    canonicalSeen.set(canonical, entry.id);

    const counts = {
        hba: matchedAtomCount(mol, qAcceptor),
        hbd: matchedAtomCount(mol, qDonor),
        aromatic: ringCount(mol, qRings),
        hydrophobic: matchedAtomCount(mol, qHydrophobic),
    };

    const expected = PROBE_EXPECTATIONS[entry.id];
    if (expected) {
        probesChecked++;
        for (const kind of Object.keys(expected)) {
            if (counts[kind] !== expected[kind]) {
                fail(`${entry.id}: ${kind} = ${counts[kind]}, expected ${expected[kind]} — instrument or SMILES defect`);
            }
        }
    }
    mol.delete();
}

if (probesChecked !== Object.keys(PROBE_EXPECTATIONS).length) {
    fail(`Only ${probesChecked}/${Object.keys(PROBE_EXPECTATIONS).length} probe molecules found in the library`);
}

qDonor.delete();
qAcceptor.delete();
qHydrophobic.delete();
for (const q of qRings) q.delete();

if (process.exitCode) {
    console.error(`FAILED — ${entries.length} entries checked`);
} else {
    console.log(`✓ ${entries.length} entries: all parse, no duplicates, SMARTS in parity, ${probesChecked} probes exact`);
}
