import { describe, expect, it, jest } from '@jest/globals';
import { bindDialogEscape, escapeHtml, renderPreparationPolicyDom, renderRunJobsDom, safeElement, setSafeText } from './workbench-dom';
import { runJobsViewFrom } from './workbench-view-model';

class FakeElement {
    className = '';
    textContent: string | null = null;
    attributes: Record<string,string> = {};
    children: FakeElement[] = [];
    dataset: Record<string,string> = {};
    toggled: Record<string,boolean> = {};
    classList = { toggle: (name:string,value:boolean) => { this.toggled[name]=value; } };
    setAttribute(name:string,value:string):void { this.attributes[name]=value; }
    append(...children:FakeElement[]):void { this.children.push(...children); }
    replaceChildren(...children:FakeElement[]):void { this.children=[...children]; }
}

describe('safe workbench DOM helpers without a browser DOM',()=>{
    it('closes a dialog on cancel or Escape but ignores other keys',()=>{
        const listeners:Record<string,(event:Event)=>void>={};
        const dialog={ addEventListener: (name:string,handler:(event:Event)=>void)=>{ listeners[name]=handler; } };
        let closes=0;
        bindDialogEscape(dialog as unknown as HTMLDialogElement,()=>{ closes++; });
        const event=(type:string,key?:string)=>({ type,key,preventDefault: jest.fn() } as unknown as Event);
        listeners.keydown(event('keydown','Enter'));
        listeners.keydown(event('keydown','Escape'));
        listeners.cancel(event('cancel'));
        expect(closes).toBe(2);
    });

    it('assigns hostile content as text rather than markup',()=>{
        const target={ textContent: null as string|null };
        setSafeText(target,'<img src=x onerror=alert(1)>');
        expect(target.textContent).toBe('<img src=x onerror=alert(1)>');
    });

    it('creates text-only elements through an injected document',()=>{
        const fakeDocument={ createElement: ()=>new FakeElement() };
        const element=safeElement(fakeDocument as unknown as Pick<Document,'createElement'>,'article',{
            className: 'run-history-row',text: '<script>owned()</script>',attributes: { 'aria-label': 'history' },
        }) as unknown as FakeElement;
        expect(element).toMatchObject({
            className: 'run-history-row',textContent: '<script>owned()</script>',attributes: { 'aria-label': 'history' },
        });
    });

    it('escapes every HTML metacharacter still used by legacy templates',()=>{
        expect(escapeHtml(`<&>"'`)).toBe('&lt;&amp;&gt;&quot;&#39;');
    });

    it('renders hostile RunSet values only through textContent',()=>{
        const fakeDocument={ createElement: ()=>new FakeElement() };
        const host=new FakeElement();
        renderRunJobsDom(
            fakeDocument as unknown as Pick<Document,'createElement'>,
            host as unknown as HTMLElement,
            runJobsViewFrom([{
                leg: 'complex',repeat: 1,jobId: '<img onerror=owned>',state: 'failed',error: '<script>owned()</script>',
            }],false),
        );
        const row=host.children[1];
        expect(row.className).toBe('job-row failed');
        expect(row.children[2]).toMatchObject({
            textContent: '<img onerror=owned>',attributes: { title: '<img onerror=owned>' },
        });
        expect(row.children[3].textContent).toBe('<script>owned()</script>');
    });

    it('renders backend preparation-policy witnesses as inert text',()=>{
        const summary=new FakeElement(),host=new FakeElement(),audit=new FakeElement();
        const fakeDocument={
            createElement: ()=>new FakeElement(),
            getElementById: (id:string)=>id==='preparation-policy-summary'?summary
                :id==='preparation-policy-rows'?host:null,
            querySelector: ()=>audit,
        };
        renderPreparationPolicyDom(fakeDocument as unknown as Pick<Document,'createElement'|'getElementById'|'querySelector'>,{
            generated: true,ok: false,blocked: true,summary: '1 BLOCKING AXIS',
            blockers: [{ axis: '<img>',verdict: 'UNVERIFIED',witness: '<script>owned()</script>' }],
            rows: [{ axis: '<img>',verdict: 'UNVERIFIED',witness: '<script>owned()</script>' }],
        });
        expect(summary.textContent).toBe('1 BLOCKING AXIS');
        expect(host.children[0].children.map(child=>child.textContent)).toEqual([
            '<IMG>','UNVERIFIED','<script>owned()</script>',
        ]);
        expect(audit.toggled.blocked).toBe(true);
    });
});
