export type DiscoveryWorkspace = 'fep' | 'field';

export const DISCOVERY_WORKSPACE_ROUTES = Object.freeze({
    fep: './fep-workbench.html',
    field: './field-workbench.html',
});

const WORKSPACES: ReadonlyArray<{ id: DiscoveryWorkspace; label: string }> = [
    { id: 'fep', label: 'FEP' },
    { id: 'field', label: 'FIELD' },
];

/** One navigation contract shared by the two initial Discovery Lab workspaces. */
export function discoveryWorkspaceNavigation(active: DiscoveryWorkspace): string {
    return `<nav class="discovery-workspace-nav" aria-label="Discovery Lab workspaces">${WORKSPACES.map(workspace => {
        const selected = workspace.id === active;
        return `<a class="discovery-workspace-link${selected ? ' active' : ''}" href="${DISCOVERY_WORKSPACE_ROUTES[workspace.id]}"${selected ? ' aria-current="page"' : ''}>${workspace.label}</a>`;
    }).join('')}</nav>`;
}
