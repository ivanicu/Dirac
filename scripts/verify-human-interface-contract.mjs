import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { contract as source } from '../docs/product/hci/human-interface-v2.source.mjs';

const root = process.cwd();
const contractPath = path.join(root, source.generatedArtifact);
const contract = JSON.parse(fs.readFileSync(contractPath, 'utf8'));
const requiredRequirementFields = [
    'id', 'area', 'condition', 'actor', 'behavior', 'authority', 'failure',
    'evidence', 'owner', 'waiver',
];
const requiredStateAxes = [
    'draft', 'connectivity', 'availability', 'submission', 'execution',
    'freshness', 'review',
];
const requiredSelections = [
    'object', 'structure', 'molecular', 'material-quantity', 'plate-well',
    'dataset-slice', 'derived-set',
];

const unique = values => new Set(values).size === values.length;
const stable = value => JSON.stringify(value);

export function validate(candidate, canonical = source) {
    const errors = [];
    const requireArray = (label, values, minimum = 1) => {
        if (!Array.isArray(values) || values.length < minimum) {
            errors.push(`${label}: expected at least ${minimum}`);
        } else if (!unique(values)) errors.push(`${label}: duplicate values`);
    };
    if (candidate.schema !== 'dirac.hci.contract') errors.push('schema: invalid');
    if (candidate.version !== '2.1.0') errors.push('version: expected 2.1.0');
    if (candidate.status !== 'semantic-contract-plus-reference-slice') errors.push('status: overclaim or unknown');
    if (stable(candidate) !== stable(canonical)) errors.push('generated contract differs from canonical source');
    requireArray('workspaceBriefs', candidate.workspaceBriefs, 8);
    if (candidate.workspaceBriefs?.length !== 8) errors.push('workspaceBriefs: expected exactly 8');
    requireArray('invariants', candidate.invariants, 8);
    requireArray('selectionKinds', candidate.selectionKinds, requiredSelections.length);
    for (const value of requiredSelections) {
        if (!candidate.selectionKinds?.includes(value)) errors.push(`selectionKinds: missing ${value}`);
    }
    for (const axis of requiredStateAxes) requireArray(`stateAxes.${axis}`, candidate.stateAxes?.[axis]);
    requireArray('handoffLifecycle', candidate.handoffLifecycle, 9);
    requireArray('workEdgeKinds', candidate.workEdgeKinds, 8);
    requireArray('projections', candidate.projections, 4);
    requireArray('actionDefinitionRequired', candidate.actionDefinitionRequired, 10);
    if (!Array.isArray(candidate.actionDefinitions) || candidate.actionDefinitions.length < 5) {
        errors.push('actionDefinitions: reference slice is incomplete');
    } else {
        for (const [index, definition] of candidate.actionDefinitions.entries()) {
            for (const field of candidate.actionDefinitionRequired || []) {
                if (!(field in definition)) errors.push(`actionDefinitions[${index}]: missing ${field}`);
            }
        }
        const actionKeys = candidate.actionDefinitions.map(item => `${item.id}@${item.version}`);
        if (!unique(actionKeys)) errors.push('actionDefinitions: duplicate versioned action keys');
        for (const action of candidate.referenceSlice?.actions || []) {
            if (!actionKeys.includes(action)) errors.push(`referenceSlice: undefined action ${action}`);
        }
    }
    if (!Array.isArray(candidate.acceptanceJourneys) || !candidate.acceptanceJourneys.length) {
        errors.push('acceptanceJourneys: missing machine-readable journey');
    }
    if (candidate.referenceSlice?.releaseClaim !== false) errors.push('referenceSlice must not claim release');
    requireArray('referenceSlice.actions', candidate.referenceSlice?.actions, 5);
    if (!Array.isArray(candidate.requirements) || candidate.requirements.length < 24) {
        errors.push('requirements: expected broad v2.1 coverage');
    } else {
        const ids = candidate.requirements.map(item => item.id);
        if (!unique(ids)) errors.push('requirements: duplicate ids');
        for (const [index, requirement] of candidate.requirements.entries()) {
            for (const field of requiredRequirementFields) {
                if (typeof requirement[field] !== 'string' || !requirement[field].trim()) {
                    errors.push(`requirements[${index}]: missing ${field}`);
                }
            }
            if (!/^HCI-[A-Z]+-[0-9]{3}$/.test(requirement.id || '')) {
                errors.push(`requirements[${index}]: invalid id`);
            }
        }
    }
    for (const workspace of candidate.workspaceBriefs || []) {
        const file = path.join(root, 'docs/product/hci/workspaces', `${workspace}.md`);
        if (!fs.existsSync(file)) errors.push(`workspaceBriefs: missing ${workspace}.md`);
    }
    return errors;
}

if (process.argv.includes('--selftest')) {
    const mutations = [
        ['missing state axis', value => delete value.stateAxes.freshness],
        ['selection collapsed', value => value.selectionKinds = ['object']],
        ['workspace missing', value => value.workspaceBriefs.pop()],
        ['release overclaim', value => value.referenceSlice.releaseClaim = true],
        ['requirement authority missing', value => value.requirements[0].authority = ''],
        ['duplicate requirement', value => value.requirements.push(structuredClone(value.requirements[0]))],
        ['projection channel removed', value => value.projections.pop()],
        ['action definition field removed', value => delete value.actionDefinitions[0].conflict_policy],
        ['acceptance journey removed', value => value.acceptanceJourneys = []],
        ['generated drift', value => value.version = '2.1.1'],
    ];
    const missed = [];
    for (const [label, mutate] of mutations) {
        const candidate = structuredClone(contract);
        mutate(candidate);
        const convicted = validate(candidate).length > 0;
        console.log(`${convicted ? 'PASS' : 'FAIL'} selftest: ${label}`);
        if (!convicted) missed.push(label);
    }
    if (missed.length) process.exit(1);
    console.log(`Human interface verifier selftest valid: ${mutations.length} defects convicted.`);
    process.exit(0);
}

const errors = validate(contract);
if (errors.length) {
    console.error(`Human interface contract invalid (${errors.length})`);
    for (const error of errors) console.error(`- ${error}`);
    process.exit(1);
}

console.log(`Human interface contract valid: ${contract.requirements.length} requirements, ${contract.workspaceBriefs.length} workspace briefs, ${contract.projections.length} projections.`);
