import { describe, expect, it } from '@jest/globals';
import { readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { DISCOVERY_WORKSPACE_ROUTES } from '../discovery-navigation';
import { WORKBENCH_ROUTES } from './workbench-routes';
import { workbenchShellMarkup } from './workbench-shell';

describe('FEP navigation belongs to the deployable AppShell',()=>{
    it('uses the two explicit Motif Workbench route modules',()=>{
        expect(WORKBENCH_ROUTES.fepWorkbench).toBe(DISCOVERY_WORKSPACE_ROUTES.fep);
        expect(WORKBENCH_ROUTES.fieldWorkbench).toBe(DISCOVERY_WORKSPACE_ROUTES.field);
    });

    it('ships the FEP static entrypoint beside the existing routed AppShell',()=>{
        const root=resolve(__dirname,'../../../');
        const appRoot=resolve(root,'src/app.frontend.facets.molstar-rdkit.editable');
        const indexHtml=readFileSync(resolve(appRoot,'index.html'),'utf8');
        const indexTs=readFileSync(resolve(appRoot,'index.ts'),'utf8');
        expect(statSync(resolve(appRoot,'index.html')).isFile()).toBe(true);
        expect(statSync(resolve(appRoot,'fep-workbench/fep-workbench.html')).isFile()).toBe(true);
        expect(indexHtml).toContain('<base href="/">');
        expect(readFileSync(resolve(appRoot,'fep-workbench/fep-workbench.html'),'utf8')).toContain('<base href="./">');
        expect(indexHtml).toContain('src="./dirac.js"');
        expect(indexTs).toContain("import './index.html'");
        expect(indexTs).toContain('initShellNavigation');
        const build=readFileSync(resolve(root,'scripts/build.mjs'),'utf8');
        expect(build).toContain("['fep-workbench/fep-workbench.html', 'time-tunnel/fep-workbench.html']");
        const markup=workbenchShellMarkup();
        expect(markup).toContain(`href="${WORKBENCH_ROUTES.allLabs}"`);
        expect(markup).toContain(`href="${WORKBENCH_ROUTES.fepWorkbench}"`);
        expect(markup).toContain(`href="${WORKBENCH_ROUTES.fieldWorkbench}"`);
        expect(markup).not.toContain('?mock=1');
        expect(markup).toContain('id="parent-compound-select"');
        expect(markup).toContain('id="campaign-state-label"');
        expect(markup).toContain('id="setup-job-count"');
        expect(markup).toContain('aria-label="Simulation protocol"');
        expect(markup).toContain('id="pose-choice-dock" class="choice-card" data-choice="dock" data-availability="unavailable" disabled');
        expect(markup).toContain('WHY THIS COMPUTE IS WORTH RUNNING');
        const sketcher=readFileSync(resolve(appRoot,'fep-workbench/molecule-sketcher.ts'),'utf8');
        expect(sketcher).toContain('START A BLANK MOLECULE');
        expect(sketcher).toContain('add.disabled=false');
    });
});
