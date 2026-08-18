export type BuilderGuideStep='start'|'target'|'ligands'|'setup'|'review';
export type BuilderUxMode='guided'|'all';

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
        'cost-cap': '120 GPU hours',
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
    if (!focus)return;
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

export async function fetchPdbExperimentalRecord(pdb:string):Promise<{record:Record<string,any>;coordinates:string}> {
    const [metadata,coordinates]=await Promise.all([
        fetch(`https://data.rcsb.org/rest/v1/core/entry/${encodeURIComponent(pdb)}`),
        fetch(`https://files.rcsb.org/download/${encodeURIComponent(pdb)}.pdb`),
    ]);
    if (!metadata.ok)throw new Error(`RCSB metadata returned HTTP ${metadata.status}`);
    if (!coordinates.ok)throw new Error(`RCSB coordinates returned HTTP ${coordinates.status}`);
    const record=await metadata.json() as Record<string,any>,pdbText=await coordinates.text();
    if (!pdbText.includes('\nATOM  ')&&!pdbText.startsWith('ATOM  '))throw new Error('downloaded record contains no PDB ATOM coordinates');
    return { record,coordinates: pdbText };
}
