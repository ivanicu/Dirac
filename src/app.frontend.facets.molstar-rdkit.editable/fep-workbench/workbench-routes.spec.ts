import { describe, expect, it } from '@jest/globals';
import { readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { VIEWS } from '../../app/shell/registries';
import { WORKBENCH_ROUTES } from './workbench-routes';
import { workbenchShellMarkup } from './workbench-shell';

function routeMatches(template:string,path:string):boolean {
    return new RegExp('^'+template.replace(':programId','[^/]+')+'$').test(path);
}

describe('FEP navigation belongs to the deployable AppShell',()=>{
    it.each(Object.entries(WORKBENCH_ROUTES))('%s resolves to a registered shell route',(_name,route)=>{
        expect(VIEWS.some(view=>routeMatches(view.route,route))).toBe(true);
    });

    it('targets the connected field-overlay surface rather than a mock workbench',()=>{
        const fieldView=VIEWS.find(view=>routeMatches(view.route,WORKBENCH_ROUTES.fieldWorkbench));
        expect(fieldView).toMatchObject({id:'structures.site',delivery:'connected'});
        expect(fieldView?.modules).toContain('structure.field-overlay');
        expect(Object.values(WORKBENCH_ROUTES).some(route=>route.includes('time-tunnel'))).toBe(false);
    });

    it('ships the FEP static entrypoint beside the existing routed AppShell',()=>{
        const root=resolve(__dirname,'../../../');
        const appRoot=resolve(root,'src/app.frontend.facets.molstar-rdkit.editable');
        const indexHtml=readFileSync(resolve(appRoot,'index.html'),'utf8');
        const indexTs=readFileSync(resolve(appRoot,'index.ts'),'utf8');
        expect(statSync(resolve(appRoot,'index.html')).isFile()).toBe(true);
        expect(statSync(resolve(appRoot,'fep-workbench/fep-workbench.html')).isFile()).toBe(true);
        expect(indexHtml).toContain('<base href="/">');
        expect(indexHtml).toContain('src="./dirac.js"');
        expect(indexTs).toContain("import './index.html'");
        expect(indexTs).toContain('initShellNavigation');
        const build=readFileSync(resolve(root,'scripts/build.mjs'),'utf8');
        expect(build).toContain("['fep-workbench/fep-workbench.html', 'time-tunnel/fep-workbench.html']");
        const markup=workbenchShellMarkup();
        expect(markup).toContain(`href="${WORKBENCH_ROUTES.allLabs}"`);
        expect(markup).toContain(`href="${WORKBENCH_ROUTES.fieldWorkbench}"`);
        expect(markup).not.toContain('/time-tunnel/#/free-energy');
        expect(markup).not.toContain('/time-tunnel/field-workbench.html');
    });
});
