import { DiracClient, type Envelope } from '../dirac-client';

function response(body: unknown) {
    return {
        status: 200,
        headers: new Headers(),
        text: async () => JSON.stringify(body),
    } as Response;
}

describe('DiracClient command jobs', () => {
    const originalFetch = globalThis.fetch;

    afterEach(() => {
        globalThis.fetch = originalFetch;
        jest.restoreAllMocks();
    });

    it('reports the accepted durable Job before waiting for the result', async () => {
        const accepted: Envelope = {
            ok: true,
            data: { job: { id: 'job-42', state: 'queued' } },
            meta: { job_id: 'job-42', command: 'structure.field.compute' },
        };
        const waited: Envelope = {
            ok: true,
            data: {
                state: 'done', method_id: 'fields.mep', method_version: 'v3', seconds: 0.4,
                result_summary: { data: { field: { kind: 'mep' } },
                    provenance: { toolkit: 'rdkit' } },
                artifacts: [{ id: 'artifact-7', role: 'field.cube', sha256: 'abc',
                    media_type: 'text/plain', size_bytes: 7 }],
            },
        };
        const fetchMock = jest.fn()
            .mockResolvedValueOnce(response(accepted))
            .mockResolvedValueOnce(response(waited));
        globalThis.fetch = fetchMock as typeof fetch;
        const onAccepted = jest.fn();

        const result = await new DiracClient({ baseUrl: 'http://dirac.test' })
            .fieldComputeAndWait({
                molecule: { format: 'molfile', content: 'mol' }, fieldKind: 'mep', onAccepted,
            });

        expect(onAccepted).toHaveBeenCalledWith(expect.objectContaining({
            ok: true, meta: expect.objectContaining({ job_id: 'job-42' }),
        }));
        expect(result).toMatchObject({
            ok: true,
            meta: { job_id: 'job-42', method_id: 'fields.mep', version: 'v3' },
            artifacts: [{ id: 'artifact-7', role: 'field.cube' }],
        });
        expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
            'http://dirac.test/v2/execute', 'http://dirac.test/v2/execute',
        ]);
    });
});
