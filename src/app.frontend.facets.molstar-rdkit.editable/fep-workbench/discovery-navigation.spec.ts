import { describe, expect, it } from '@jest/globals';
import { DISCOVERY_WORKSPACE_ROUTES, discoveryWorkspaceNavigation } from '../discovery-navigation';

describe('Motif Workbench navigation', () => {
    it('starts with exactly the FEP and Field workspaces', () => {
        expect(Object.keys(DISCOVERY_WORKSPACE_ROUTES)).toEqual(['fep', 'field']);
    });

    it.each(['fep', 'field'] as const)('links both workspaces and marks %s as current', active => {
        const markup = discoveryWorkspaceNavigation(active);
        expect(markup).toContain(`href="${DISCOVERY_WORKSPACE_ROUTES.fep}"`);
        expect(markup).toContain(`href="${DISCOVERY_WORKSPACE_ROUTES.field}"`);
        expect(markup.match(/aria-current="page"/g)).toHaveLength(1);
        expect(markup).toContain(`href="${DISCOVERY_WORKSPACE_ROUTES[active]}" aria-current="page"`);
    });
});
