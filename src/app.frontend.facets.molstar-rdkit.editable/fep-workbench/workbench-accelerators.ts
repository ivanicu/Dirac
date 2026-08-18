export type AcceleratorLigandRow={id:string;smiles:string};
export type SeriesRepair={rows:AcceleratorLigandRow[];fixes:string[];blockers:string[]};
export type RecommendedSeries={rows:AcceleratorLigandRow[];parentId:string;scores:ReadonlyArray<{id:string;similarity:number|null}>};

export function workbenchUuid():string {
    if (typeof globalThis.crypto?.randomUUID==='function') return globalThis.crypto.randomUUID();
    const bytes=new Uint8Array(16); if (typeof globalThis.crypto?.getRandomValues==='function')globalThis.crypto.getRandomValues(bytes); else for (let index=0; index<bytes.length; index++)bytes[index]=Math.floor(Math.random()*256);
    bytes[6]=(bytes[6]&0x0f)|0x40; bytes[8]=(bytes[8]&0x3f)|0x80; const hex=[...bytes].map(value=>value.toString(16).padStart(2,'0')).join(''); return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}

export function duplicateCampaignInputs<T extends {name:string;values:Record<string,string>}>(draft:T):T {
    const name=`${draft.name||'UNTITLED FEP CAMPAIGN'} · COPY`;
    return { ...draft,name,values: { ...draft.values,'campaign-name': name } };
}

type AcceleratorOptions={
    locked:()=>boolean;
    notify:(message:string)=>void;
    getRawSeries:()=>string;
    getRows:()=>AcceleratorLigandRow[];
    getParent:()=>string;
    replaceSeries:(rows:AcceleratorLigandRow[],context:{parentId?:string})=>Promise<void>;
    similarity:(left:string,right:string)=>Promise<number|null>;
    duplicateCampaign:()=>Promise<void>;
    applyRecommended:()=>void;
};

function safeId(value:string,fallback:string):string {
    const normalized=value.trim().replace(/[^A-Za-z0-9_.:+-]+/g,'-').replace(/^[^A-Za-z0-9]+/,'').slice(0,128);
    return normalized||fallback;
}

export function repairLigandSeries(raw:string):SeriesRepair {
    const rows:AcceleratorLigandRow[]=[],fixes:string[]=[],blockers:string[]=[],usedIds=new Set<string>(),usedSmiles=new Set<string>();
    raw.split(/\r?\n/).forEach((source,index)=>{
        const line=source.trim(); if (!line) return;
        const tokens=line.split(/[\s,\t,]+/).filter(Boolean);
        if (tokens.length>2) { blockers.push(`Line ${index+1}: expected SMILES or ID + SMILES`); return; }
        const smiles=tokens.length===1?tokens[0]:tokens[1],original=tokens.length===1?`CMPD-${String(index+1).padStart(3,'0')}`:tokens[0];
        if (!smiles) { blockers.push(`Line ${index+1}: missing structure`); return; }
        if (usedSmiles.has(smiles)) { fixes.push(`Removed duplicate structure on line ${index+1}`); return; }
        const base=safeId(original,`CMPD-${String(index+1).padStart(3,'0')}`); let id=base,suffix=2;
        while (usedIds.has(id))id=`${base}-${suffix++}`;
        if (id!==original)fixes.push(`${original||`Line ${index+1}`} renamed to ${id}`);
        usedIds.add(id); usedSmiles.add(smiles); rows.push({ id,smiles });
    });
    return { rows,fixes,blockers };
}

export async function recommendEight(rows:ReadonlyArray<AcceleratorLigandRow>,parentId:string,similarity:(left:string,right:string)=>Promise<number|null>):Promise<RecommendedSeries> {
    if (!rows.length) throw new Error('Add compounds first');
    const unique=[...new Map(rows.map(row=>[row.id,row])).values()],parent=unique.find(row=>row.id===parentId)||unique[0];
    const scored=await Promise.all(unique.filter(row=>row.id!==parent.id).map(async row=>({ row,similarity: await similarity(parent.smiles,row.smiles) })));
    scored.sort((left,right)=>(right.similarity??-1)-(left.similarity??-1)||left.row.id.localeCompare(right.row.id));
    const selected=[parent,...scored.slice(0,7).map(item=>item.row)];
    return { rows: selected,parentId: parent.id,scores: scored.slice(0,7).map(item=>({ id: item.row.id,similarity: item.similarity })) };
}

export function applyRecommendedSetup(document:Document,nodeCount:number):string[] {
    const values:Readonly<Record<string,string>>={
        'assembly-select': 'deposited_asymmetric_unit','site-role-filter': 'ligand','prep-missing-atoms': 'auto_repair_report','prep-missing-residues': 'block','prep-altloc': 'highest_occupancy','prep-occupancy': 'reject_zero','prep-protonation': 'server_assign_review','prep-waters': 'remove_all','prep-cofactors': 'keep_parameter_gate','prep-metals': 'keep_parameter_gate','ligand-ph': 'enumerate_at_ph','ligand-tautomers': 'enumerate','ligand-stereo': 'enumerate_unknown','ligand-state-cutoff': '0.1','ligand-charge-policy': 'block_changes','protocol-select': 'openfe-rfe-standard-v1',
    };
    const applied:string[]=[];
    for (const [id,value] of Object.entries(values)) { const element=document.getElementById(id) as HTMLSelectElement|null; if (!element||![...element.options].some(option=>option.value===value)) continue; if (element.value!==value) { element.value=value; element.dispatchEvent(new Event('change',{ bubbles: true })); }applied.push(id); }
    for (const id of ['pose-choice-align','network-choice-balanced']) { const button=document.getElementById(id) as HTMLButtonElement|null; if (button&&!button.classList.contains('active'))button.click(); applied.push(id); }
    const cap=document.getElementById('cost-cap') as HTMLInputElement|null;
    if (cap&&!cap.value.trim()&&nodeCount>=2) { const edges=Math.min(nodeCount*(nodeCount-1)/2,Math.ceil(nodeCount*1.5)); cap.value=`${Math.max(60,edges*30)} GPU hours`; cap.dispatchEvent(new Event('input',{ bubbles: true })); applied.push('cost-cap'); }
    return applied;
}

export function setExceptionView(document:Document,enabled:boolean):void {
    document.getElementById('campaign-builder')?.setAttribute('data-only-exceptions',String(enabled));
    const button=document.getElementById('show-only-exceptions'); if (button) { button.setAttribute('aria-pressed',String(enabled)); button.textContent=enabled?'SHOW ALL CHECKS':'SHOW ONLY EXCEPTIONS'; }
}

export function bindWorkflowAccelerators(document:Document,options:AcceleratorOptions):void {
    document.getElementById('use-recommended-setup')?.addEventListener('click',async()=>{ if (options.locked()) return options.notify('Recommended setup is locked while a physical RunSet is attached.'); const rows=options.getRows(),parent=options.getParent(); options.applyRecommended(); if (rows.length) await options.replaceSeries(rows,{ parentId: parent||rows[0].id }); options.notify('Recommended receptor, ligand-state, pose, network and protocol settings applied. Only project-specific decisions remain.'); });
    document.getElementById('fix-safe-issues')?.addEventListener('click',async()=>{ if (options.locked()) return options.notify('Series repair is locked while a physical RunSet is attached.'); const repaired=repairLigandSeries(options.getRawSeries()); if (repaired.blockers.length) return options.notify(`Safe repair stopped · ${repaired.blockers.join(' · ')}`); if (!repaired.rows.length) return options.notify('Add compounds before running safe repair.'); const parent=repaired.rows.some(row=>row.id===options.getParent())?options.getParent():repaired.rows[0].id; options.applyRecommended(); await options.replaceSeries(repaired.rows,{ parentId: parent }); options.notify(repaired.fixes.length?`Fixed ${repaired.fixes.length} safe issue${repaired.fixes.length===1?'':'s'} · ${repaired.fixes.join(' · ')}`:'Series is already normalized · reference and recommended settings confirmed.'); });
    document.getElementById('select-recommended-eight')?.addEventListener('click',async event=>{ const button=event.currentTarget as HTMLButtonElement; if (options.locked()) return options.notify('Series selection is locked while a physical RunSet is attached.'); button.disabled=true; button.textContent='RANKING SERIES…'; try { const selected=await recommendEight(options.getRows(),options.getParent(),options.similarity); await options.replaceSeries(selected.rows,{ parentId: selected.parentId }); const scoreCopy=selected.scores.map(row=>`${row.id} ${row.similarity===null?'UNSCORED':row.similarity.toFixed(2)}`).join(' · '); options.notify(`Recommended ${selected.rows.length}: ${selected.parentId} reference + closest network-compatible candidates · ${scoreCopy||'reference only'}`); } catch (error) { options.notify(`Recommended-series selection refused · ${error instanceof Error?error.message:String(error)}`); } finally { button.disabled=false; button.textContent='SELECT RECOMMENDED 8'; } });
    document.getElementById('show-only-exceptions')?.addEventListener('click',event=>setExceptionView(document,(event.currentTarget as HTMLButtonElement).getAttribute('aria-pressed')!=='true'));
    document.getElementById('duplicate-campaign')?.addEventListener('click',()=>void options.duplicateCampaign());
}
