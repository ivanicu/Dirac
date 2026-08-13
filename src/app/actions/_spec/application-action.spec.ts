import { actionKey } from '../application-action';

describe('application action identity', () => {
    it('is semantic and versioned independently of invocation surface', () => {
        expect(actionKey({ id: 'design.proposal.promote', version: 2 }))
            .toBe('design.proposal.promote@2');
    });

    it('rejects workspace/UI-shaped and unversioned identities', () => {
        expect(() => actionKey({ id: 'Campaign Promote Button', version: 1 })).toThrow();
        expect(() => actionKey({ id: 'design.proposal.promote', version: 0 })).toThrow();
    });
});
