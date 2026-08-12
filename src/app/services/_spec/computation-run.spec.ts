import { computationRunFromEnvelope, computationRunRows, failedComputationRun,
    observedComputationRun } from '../computation-run';

describe('computation run evidence projection', () => {
    it('keeps durable job, method, artifacts, and provenance visible', () => {
        const run = computationRunFromEnvelope('structure.field.compute', {
            ok: true,
            data: {},
            artifacts: [{ id: 'artifact-1', role: 'field.cube', sha256: 'abc',
                media_type: 'text/plain', size_bytes: 42, encoding: 'identity', url: '/a/1' }],
            meta: { job_id: 'job-1', method_id: 'fields.mep', version: 'v7', seconds: 1.25,
                provenance: { toolkit: 'rdkit', input_digest: 'sha256:1' } },
        });
        expect(run.phase).toBe('done');
        expect(run.jobId).toBe('job-1');
        expect(computationRunRows(run)).toEqual(expect.arrayContaining([
            ['Method', 'fields.mep'],
            ['Artifacts', 'field.cube · artifact-1'],
            ['Provenance', 'toolkit=rdkit · input_digest=sha256:1'],
        ]));
    });

    it('separates a scientific refusal from an operational failure', () => {
        const refused = computationRunFromEnvelope('structure.torsion.analyze', {
            ok: false, error: { code: 'UNPARAMETERIZED', message: 'MMFF cannot type atom 4' },
        });
        const failed = computationRunFromEnvelope('structure.torsion.analyze', {
            ok: false, error: { code: 'INTERNAL', message: 'worker vanished' },
        });
        expect(refused.phase).toBe('refused');
        expect(failed.phase).toBe('failed');
    });

    it('states that browser interactions do not mint a durable job', () => {
        const run = observedComputationRun('structure.interactions', {
            executor: 'browser', methodId: 'molstar.interactions', version: 'workspace',
            provenance: { contacts: 3 }, note: 'Geometry-qualified observation; not an energy.',
        });
        expect(computationRunRows(run)).toContainEqual(['Durable job', 'not used']);
        expect(computationRunRows(run)).toContainEqual([
            'Evidence boundary', 'Geometry-qualified observation; not an energy.',
        ]);
    });

    it('does not claim a running durable Job was cancelled', () => {
        const run = failedComputationRun(
            'structure.surface.compute', 'Cancellation requested', 'cancel-requested', 'job-9');
        expect(run.phase).toBe('cancel-requested');
        expect(run.error?.code).toBe('CANCEL_REQUESTED');
        expect(computationRunRows(run)).toContainEqual(['Durable job', 'job-9']);
    });
});
