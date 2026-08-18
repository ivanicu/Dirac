export type BuilderGuideStep='start'|'target'|'ligands'|'setup'|'review';
export type BuilderUxMode='guided'|'all';

export type CampaignEstimate={ nodes:number;edges:number;jobs:number;gpuHours:number };
export type DecisionValidation={ ready:boolean;missing:string[];invalid:string[];costCapHours:number|null;estimate:CampaignEstimate };

const DECISION_LABELS:Readonly<Record<string,string>>={
    'campaign-question': 'Project question',
    'assay-anchor': 'Assay / potency anchor',
    'portfolio-priority': 'Portfolio priority',
    'cost-cap': 'Cost cap',
    'next-action': 'Next action',
    'stop-rule': 'Stop rule',
};

function meaningful(value:string,minimum=12):boolean {
    const normalized=value.trim().replace(/\s+/g,' ');
    return normalized.length>=minimum&&new Set(normalized.toLowerCase().replace(/[^a-z0-9]/g,'')).size>=4;
}

export function campaignEstimate(nodes:number,strategy='balanced'):CampaignEstimate {
    const count=Math.max(0,Math.floor(nodes)),maximum=count<2?0:count*(count-1)/2;
    const requested=count<2?0:strategy==='minimum'?count-1:strategy==='dense'?Math.ceil(count*2):Math.ceil(count*1.5);
    const edges=Math.min(maximum,requested),jobs=edges*6;
    return { nodes: count,edges,jobs,gpuHours: edges*30 };
}

export function parseGpuHourCap(value:string):number|null {
    const match=value.trim().match(/^(\d+(?:\.\d+)?)\s*(?:gpu\s*)?(?:h|hr|hrs|hour|hours)$/i);
    if (!match) return null;
    const hours=Number(match[1]);
    return Number.isFinite(hours)&&hours>0?hours:null;
}

export function validateDecisionInputs(values:Readonly<Record<string,string>>,estimate:CampaignEstimate):DecisionValidation {
    const missing=Object.keys(DECISION_LABELS).filter(id=>!values[id]?.trim()).map(id=>DECISION_LABELS[id]);
    const invalid:string[]=[];
    if (values['campaign-question']&&!meaningful(values['campaign-question'],20))invalid.push('Project question needs a specific decision, not a placeholder');
    if (values['assay-anchor']&&(!meaningful(values['assay-anchor'])||!/(?:nm|µm|um|mm|pm|ic50|ec50|ki|kd|pic50|kcal\s*\/\s*mol)/i.test(values['assay-anchor'])))invalid.push('Assay anchor needs an endpoint and unit');
    if (values['next-action']&&!meaningful(values['next-action'],16))invalid.push('Next action needs a concrete positive/negative decision');
    if (values['stop-rule']&&!meaningful(values['stop-rule'],16))invalid.push('Stop rule needs a concrete condition');
    const costCapHours=values['cost-cap']?parseGpuHourCap(values['cost-cap']):null;
    if (values['cost-cap']&&costCapHours===null)invalid.push('Cost cap must be a number followed by GPU hours');
    if (costCapHours!==null&&estimate.gpuHours>costCapHours)invalid.push(`Estimated ${estimate.gpuHours} GPU hours exceeds the ${costCapHours} GPU-hour cap`);
    return { ready: missing.length===0&&invalid.length===0,missing,invalid,costCapHours,estimate };
}

export function renderCampaignEstimate(document:Document,estimate:CampaignEstimate):void {
    const set=(id:string,value:string)=>{ const element=document.getElementById(id); if (element)element.textContent=value; };
    set('preview-node-count',String(estimate.nodes)); set('preview-edge-count',String(estimate.edges)); set('preview-job-count',String(estimate.jobs)); set('preview-hours',estimate.edges?`~${estimate.gpuHours}`:'—');
    set('setup-edge-count',String(estimate.edges)); set('setup-job-count',String(estimate.jobs)); set('setup-hours',estimate.edges?`~${estimate.gpuHours} h`:'—');
}

export function renderDecisionValidation(document:Document,decision:DecisionValidation):void {
    const escape=(value:string)=>value.replace(/[&<>"']/g,char=>({ '&': '&amp;','<': '&lt;','>': '&gt;','"': '&quot;',"'": '&#39;' })[char]!);
    const host=document.getElementById('decision-errors'),errors=[...decision.missing.map(label=>`${label} is required`),...decision.invalid];
    if (host) { host.hidden=!errors.length; host.innerHTML=errors.map(error=>`<span>${escape(error)}</span>`).join(''); }
    const ids=new Set<string>(),byLabel:Record<string,string>={ 'Project question': 'campaign-question','Assay / potency anchor': 'assay-anchor','Portfolio priority': 'portfolio-priority','Cost cap': 'cost-cap','Next action': 'next-action','Stop rule': 'stop-rule' };
    decision.missing.forEach(label=>{ if (byLabel[label])ids.add(byLabel[label]); });
    decision.invalid.forEach(message=>{ const id=message.startsWith('Project question')?'campaign-question':message.startsWith('Assay anchor')?'assay-anchor':message.startsWith('Next action')?'next-action':message.startsWith('Stop rule')?'stop-rule':message.includes('GPU')||message.startsWith('Cost cap')?'cost-cap':''; if (id)ids.add(id); });
    document.querySelectorAll<HTMLInputElement|HTMLSelectElement>('.portfolio-grid input,.portfolio-grid select').forEach(element=>{ const invalid=ids.has(element.id); element.setAttribute('aria-invalid',String(invalid)); element.classList.toggle('invalid',invalid); });
}

export function renderParentCompoundOptions(document:Document,ids:ReadonlyArray<string>,prior=''):void {
    const select=document.getElementById('parent-compound-select') as HTMLSelectElement|null; if (!select) return;
    const escape=(value:string)=>value.replace(/[&<>"']/g,char=>({ '&': '&amp;','<': '&lt;','>': '&gt;','"': '&quot;',"'": '&#39;' })[char]!);
    select.disabled=!ids.length; select.innerHTML=ids.length?'<option value="">CHOOSE THE REFERENCE COMPOUND…</option>'+ids.map(id=>`<option value="${escape(id)}">${escape(id)}</option>`).join(''):'<option value="">FIX AND REVALIDATE THE SERIES</option>'; if (ids.includes(prior))select.value=prior;
}

export function renderLigandAuditRows(document:Document,markup:string):void {
    for (const id of ['ligand-identity-rows','review-ligand-rows']) { const host=document.getElementById(id); if (host)host.innerHTML=markup||'<p>NO LIGANDS VALIDATED</p>'; }
}

export function restoreParentCompoundSelection(document:Document,desired:unknown):void {
    const select=document.getElementById('parent-compound-select') as HTMLSelectElement|null,value=String(desired||'');
    if (select&&[...select.options].some(option=>option.value===value))select.value=value;
}

export async function prepLigandsNext(document:Document,validate:()=>Promise<{rows:Array<{id:string;smiles:string}>;valid:number}>):Promise<boolean> {
    const checked=await validate();
    if (checked.valid!==checked.rows.length||checked.valid<2)return false;
    const select=document.getElementById('parent-compound-select') as HTMLSelectElement|null;
    if (select&&!select.value)select.value=checked.rows[0].id;
    return !!select?.value;
}

export function ligandIdentityCardHtml(row:{id:string;sourceLine:number;error:string;depiction:string;input:string;canonical:string;charge:string;stereo:string;protonation:string;tautomer:string;outcome:string}):string {
    const escape=(value:string)=>value.replace(/[&<>"']/g,char=>({ '&': '&amp;','<': '&lt;','>': '&gt;','"': '&quot;',"'": '&#39;' })[char]!);
    const fields=[['Input SMILES',row.input],['Canonical isomeric SMILES',row.canonical],['Formal charge',row.charge],['CIP / E-Z',row.stereo],['Protonation policy',row.protonation],['Tautomer policy',row.tautomer],['Policy outcome',row.outcome]];
    return `<article class="ligand-identity-row ${row.error?'error':'valid'}"><header><b>${escape(row.id||`ROW ${row.sourceLine}`)}</b><em>${row.error?'ERROR':'VALID'}</em><small>INPUT LINE ${row.sourceLine}</small></header>${row.depiction?`<div class="ligand-row-depiction">${row.depiction}</div>`:''}<dl>${fields.map(([label,value])=>`<div><dt>${label}</dt><dd>${escape(value)}</dd></div>`).join('')}</dl></article>`;
}

export function builderReadinessCopy(state:string,ready:boolean,receptor:boolean,boundReference:boolean,ligands:boolean,parent:boolean,ligandErrors:number,decisionProblems:number):{draft:string;next:string} {
    if (state==='accepted') return { draft: 'POSES REVIEWED · READY TO PLAN',next: 'NEXT · PLAN OPENFE NETWORK' };
    if (state==='prepared') return { draft: 'POSE + POLICY REVIEW REQUIRED',next: 'NEXT · REVIEW POLICY + EVERY POSE' };
    if (state==='reviewed') return { draft: 'INPUTS REVIEWED',next: 'NEXT · START RECEPTOR + POSE PREPARATION' };
    if (ready) return { draft: 'READY FOR INPUT REVIEW',next: 'NEXT · REVIEW NEW CAMPAIGN' };
    if (!receptor) return { draft: 'ADD A RECEPTOR',next: 'NEXT · ADD A NEW RECEPTOR' };
    if (!boundReference) return { draft: 'CHOOSE A BOUND REFERENCE LIGAND',next: 'NEXT · CHOOSE THE BOUND CRYSTAL LIGAND' };
    if (!ligands) return ligandErrors?{ draft: `FIX ${ligandErrors} LIGAND ERROR${ligandErrors===1?'':'S'}`,next: 'NEXT · FIX EVERY LIGAND ERROR' }:{ draft: 'VALIDATE AT LEAST 2 LIGANDS',next: 'NEXT · VALIDATE AT LEAST 2 MOLECULES' };
    if (!parent) return { draft: 'CHOOSE THE REFERENCE COMPOUND',next: 'NEXT · CHOOSE THE REFERENCE COMPOUND' };
    return { draft: `FIX ${decisionProblems} DECISION FIELD${decisionProblems===1?'':'S'}`,next: 'NEXT · FIX THE HIGHLIGHTED DECISION FIELDS' };
}

export type GuidedExample={
    pdb:string;
    referenceResname:string;
    ligands:string;
    fields:Readonly<Record<string,string>>;
};

export const T4L_EIGHT_LIGAND_EXAMPLE:GuidedExample={
    pdb: '181L',
    referenceResname: 'BNZ',
    ligands: [
        'BEN  c1ccccc1',
        'TOL  Cc1ccccc1',
        'OXY  Cc1ccccc1C',
        'PXY  Cc1ccc(C)cc1',
        'ETB  CCc1ccccc1',
        'BZF  c1ccc2occc2c1',
        'IDN  c1ccc2c(c1)CC=C2',
        'IDL  c1ccc2[nH]ccc2c1',
    ].join('\n'),
    fields: {
        'campaign-name': 'T4L L99A · eight-ligand design campaign',
        'campaign-question': 'Which analogue should advance after balancing predicted affinity with structural risk?',
        'assay-anchor': 'T4 lysozyme L99A binding free energy · kcal/mol',
        'selectivity-context': 'Single engineered cavity benchmark · selectivity not decision-limiting',
        'adme-context': 'Benchmark series · ADME not decision-limiting',
        'synthesis-status': 'ROUTE READY',
        'portfolio-priority': 'HIGH · DECISION-CHANGING',
        'compound-priorities': [
            'BEN | REFERENCE | crystallographic parent | IN HAND',
            'TOL | HIGH | methyl growth vector | ROUTE READY',
            'OXY | MEDIUM | ortho packing hypothesis | ROUTE READY',
            'PXY | MEDIUM | para packing hypothesis | ROUTE READY',
            'ETB | HIGH | hydrophobic growth vector | ROUTE READY',
            'BZF | HIGH | fused heteroaromatic test | ROUTE READY',
            'IDN | MEDIUM | fused-ring geometry test | ROUTE READY',
            'IDL | HIGH | heteroatom interaction test | ROUTE READY',
        ].join('\n'),
        'pose-hypothesis': 'Preserve the crystallographic aromatic core; test cavity growth and heteroatom vectors.',
        'cost-cap': '420 GPU hours',
        'next-action': 'Advance compounds whose predicted gain survives pose and convergence review.',
        'stop-rule': 'Stop an edge if pose evidence, mapping chemistry, or convergence is unresolved.',
        'ligand-stereo': 'enumerate_unknown',
    },
};

export function suggestedGuideStep(structureReady:boolean,ligandsReady:boolean,decisionReady:boolean):BuilderGuideStep {
    if (!structureReady) return 'target';
    if (!ligandsReady) return 'ligands';
    if (!decisionReady) return 'setup';
    return 'review';
}

export function renderBuilderGuide(document:Document,builder:HTMLElement|null,step:BuilderGuideStep,mode:BuilderUxMode,focus=true):void {
    builder?.setAttribute('data-guide-step',step); builder?.setAttribute('data-ux',mode);
    const welcome=document.getElementById('builder-welcome'),guide=document.getElementById('builder-guide');
    if (welcome)welcome.hidden=step!=='start'; if (guide)guide.hidden=step==='start';
    document.querySelectorAll<HTMLButtonElement>('[data-guide-step]').forEach(button=>{ const active=button.dataset.guideStep===step; button.classList.toggle('active',active); button.setAttribute('aria-current',active?'step':'false'); });
    const toggle=document.getElementById('toggle-all-controls') as HTMLButtonElement|null;
    if (toggle) { toggle.setAttribute('aria-pressed',String(mode==='all')); toggle.textContent=mode==='all'?'RETURN TO GUIDED VIEW':'SHOW ALL CONTROLS'; }
    if (!focus) return;
    const focusId:Record<BuilderGuideStep,string>={ start: 'load-t4l-example',target: 'campaign-pdb',ligands: 'campaign-ligands',setup: 'campaign-question',review: 'review-inputs' };
    requestAnimationFrame(()=>(document.getElementById(focusId[step]) as HTMLElement|null)?.focus());
}

export function renderBuilderGuideProgress(document:Document,structureReady:boolean,ligandsReady:boolean,decisionReady:boolean):void {
    const suggested=suggestedGuideStep(structureReady,ligandsReady,decisionReady),ready=structureReady&&ligandsReady&&decisionReady;
    document.querySelectorAll<HTMLButtonElement>('[data-guide-step]').forEach(button=>{ const key=button.dataset.guideStep,complete=key==='target'?structureReady:key==='ligands'?ligandsReady:key==='setup'?decisionReady:key==='review'?ready:false; button.classList.toggle('complete',complete); button.classList.toggle('suggested',key===suggested&&!complete); });
}

export function applyGuidedExample(document:Document,example:GuidedExample):void {
    const pdb=document.getElementById('campaign-pdb') as HTMLInputElement|null,ligands=document.getElementById('campaign-ligands') as HTMLTextAreaElement|null;
    if (pdb)pdb.value=example.pdb; if (ligands)ligands.value=example.ligands;
    for (const [id,value] of Object.entries(example.fields)) { const element=document.getElementById(id) as HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement|null; if (element)element.value=value; }
}

export function clearGuidedCampaignForm(document:Document):void {
    for (const id of ['campaign-question','assay-anchor','selectivity-context','adme-context','compound-priorities','pose-hypothesis','cost-cap','next-action','stop-rule']) { const element=document.getElementById(id) as HTMLInputElement|HTMLTextAreaElement|null; if (element)element.value=''; }
    for (const id of ['synthesis-status','portfolio-priority']) { const element=document.getElementById(id) as HTMLSelectElement|null; if (element)element.value=''; }
}
