export type ProteinStructureCandidate={
    pdbId:string;
    title:string;
    method:string;
    resolutionAngstrom:number|null;
    boundComponents:string[];
    completeness:number|null;
    relevance:number;
    score:number;
    reasons:string[];
    mutationCount:number|null;
    mutationLabels:string[];
    missingResidues:number|null;
    ligandSimilarity:number|null;
};
export type BoundLigandCandidate={resname:string;chain:string;residue_number:string;heavy_atom_count:number;label:string;role:'ligand'|'cofactor'};

type SearchHit={ identifier?:unknown;score?:unknown };
type JsonRecord=Record<string,any>;
type TargetSearchOptions={
    loadStructure:(pdbId:string)=>Promise<boolean>;
    locked:()=>boolean;
    notify:(message:string)=>void;
    referenceSmiles?:()=>string;
    similarity?:(left:string,right:string)=>Promise<number|null>;
};

const SearchEndpoint='https://search.rcsb.org/rcsbsearch/v2/query';
const EntryEndpoint='https://data.rcsb.org/rest/v1/core/entry/';

function finite(value:unknown):number|null { const number=Number(value); return Number.isFinite(number)?number:null; }
function words(value:string):string[] { return value.toLowerCase().match(/[a-z0-9]+/g)||[]; }

export function buildProteinSearchRequest(name:string):Record<string,unknown> {
    const tokens=words(name);
    if (!tokens.length) throw new Error('Enter a protein name or gene symbol');
    return {
        query: { type: 'terminal',service: 'full_text',parameters: { value: tokens.join(' + ') } },
        request_options: { paginate: { start: 0,rows: 12 },results_content_type: ['experimental'],results_verbosity: 'minimal' },
        return_type: 'entry',
    };
}

export function proteinStructureCandidate(hit:SearchHit,record:JsonRecord,query=''):ProteinStructureCandidate|null {
    const pdbId=String(record.entry?.id||hit.identifier||'').toUpperCase();
    if (!/^[0-9][A-Z0-9]{3}$/.test(pdbId)) return null;
    const info=record.rcsb_entry_info||{},title=String(record.struct?.title||'UNTITLED EXPERIMENTAL STRUCTURE'),method=String(record.exptl?.[0]?.method||info.experimental_method||'METHOD NOT REPORTED').toUpperCase();
    const resolution=finite(info.resolution_combined?.[0]??info.diffrn_resolution_high?.value),modeled=finite(info.deposited_modeled_polymer_monomer_count),deposited=finite(info.deposited_polymer_monomer_count);
    const completeness=modeled!==null&&deposited&&deposited>0?Math.min(1,modeled/deposited):null;
    const boundComponents:string[]=Array.isArray(info.nonpolymer_bound_components)?[...new Set<string>((info.nonpolymer_bound_components as unknown[]).map(value=>String(value)).filter(Boolean))]:[];
    const specificTerms=words(query).filter(term=>term.length>2&&!['protein','kinase','domain','human','mutant','receptor'].includes(term)),titleTerms=new Set(words(title)),targetMatch=specificTerms.length?specificTerms.filter(term=>titleTerms.has(term)).length/specificTerms.length:0;
    const relevance=Math.max(0,Math.min(1,finite(hit.score)??0)),xray=method.includes('X-RAY'),cryo=method.includes('ELECTRON');
    const score=relevance*50+targetMatch*30+(boundComponents.length?20:0)+(xray?15:cryo?10:0)+(resolution!==null?Math.max(0,12-resolution*3):0)+(completeness!==null?completeness*5:0);
    const reasons=[
        targetMatch?`${Math.round(targetMatch*100)}% specific name match`:'broad text match',
        boundComponents.length?`${boundComponents.length} bound component type${boundComponents.length===1?'':'s'}`:'no bound component reported',
        resolution!==null?`${resolution.toFixed(2)} Å ${method.replace(' DIFFRACTION','').toLowerCase()}`:method.toLowerCase(),
        completeness!==null?`${Math.round(completeness*100)}% polymer residues modeled`:'model completeness not reported',
    ];
    return { pdbId,title,method,resolutionAngstrom: resolution,boundComponents,completeness,relevance,score,reasons,mutationCount: null,mutationLabels: [],missingResidues: finite(info.deposited_unmodeled_polymer_monomer_count),ligandSimilarity: null };
}

export function rankProteinStructures(candidates:ProteinStructureCandidate[]):ProteinStructureCandidate[] {
    return [...candidates].sort((left,right)=>right.score-left.score||left.pdbId.localeCompare(right.pdbId));
}

async function enrichProteinStructure(candidate:ProteinStructureCandidate,record:JsonRecord,query:string,referenceSmiles:string,similarity:((left:string,right:string)=>Promise<number|null>)|undefined,fetcher:typeof fetch):Promise<ProteinStructureCandidate> {
    const identifiers=record.rcsb_entry_container_identifiers||{},entityIds:string[]=Array.isArray(identifiers.polymer_entity_ids)?identifiers.polymer_entity_ids.map(String):[];
    const entities=await Promise.all(entityIds.map(async entityId=>{ try { const response=await fetcher(`https://data.rcsb.org/rest/v1/core/polymer_entity/${candidate.pdbId}/${encodeURIComponent(entityId)}`); return response.ok?await response.json() as JsonRecord:null; } catch { return null; } }));
    const mutationCount=entities.reduce((sum,entity)=>sum+(finite(entity?.entity_poly?.rcsb_mutation_count)||0),0),mutationLabels=entities.map(entity=>String(entity?.entity_poly?.pdbx_mutation||'').trim()).filter(Boolean),targetTerms=words(query).filter(term=>term.length>2&&!['protein','kinase','domain','human','mutant','receptor'].includes(term)),entityText=entities.map(entity=>JSON.stringify([entity?.entity_src_gen,entity?.entity_src_nat])).join(' ').toLowerCase(),entityMatch=targetTerms.length?targetTerms.filter(term=>entityText.includes(term)).length/targetTerms.length:0,decoy=/\b(?:antibod(?:y|ies)|nanobod(?:y|ies)|scfv|fab)\b/i.test(candidate.title)&&entityMatch===0;
    let ligandSimilarity:number|null=null;
    if (referenceSmiles&&similarity&&candidate.boundComponents.length) {
        const scores=await Promise.all(candidate.boundComponents.slice(0,8).map(async compId=>{ try { const response=await fetcher(`https://data.rcsb.org/rest/v1/core/chemcomp/${encodeURIComponent(compId)}`); if (!response.ok) return null; const payload=await response.json() as JsonRecord,smiles=String(payload.rcsb_chem_comp_descriptor?.SMILES_stereo||payload.rcsb_chem_comp_descriptor?.SMILES||''); return smiles?await similarity(referenceSmiles,smiles):null; } catch { return null; } }));
        const finiteScores=scores.filter((score):score is number=>typeof score==='number'&&Number.isFinite(score)); ligandSimilarity=finiteScores.length?Math.max(...finiteScores):null;
    }
    const reasons=[...candidate.reasons]; reasons.unshift(entityMatch?`${Math.round(entityMatch*100)}% target identity in polymer entity`:decoy?'binder protein, not target identity':'polymer target identity not reported'); reasons.push(mutationCount?`${mutationCount} construct mutation${mutationCount===1?'':'s'}${mutationLabels.length?` · ${mutationLabels.join(', ')}`:''}`:'no construct mutation reported'); reasons.push(`${candidate.missingResidues??'—'} unmodeled polymer residues`); reasons.push(referenceSmiles?(ligandSimilarity===null?'co-crystal similarity unavailable':`best co-crystal similarity ${ligandSimilarity.toFixed(2)}`):'select a reference compound to score co-crystal similarity');
    return { ...candidate,score: candidate.score+entityMatch*55-(decoy?45:0),mutationCount,mutationLabels,ligandSimilarity,reasons };
}

export async function searchProteinStructures(name:string,fetcher:typeof fetch=fetch,referenceSmiles='',similarity?:((left:string,right:string)=>Promise<number|null>)):Promise<ProteinStructureCandidate[]> {
    const response=await fetcher(SearchEndpoint,{ method: 'POST',headers: { 'content-type': 'application/json' },body: JSON.stringify(buildProteinSearchRequest(name)) });
    if (response.status===204) return [];
    if (!response.ok) throw new Error(`RCSB search returned HTTP ${response.status}`);
    const payload=await response.json() as JsonRecord,hits=(Array.isArray(payload.result_set)?payload.result_set:[]) as SearchHit[];
    const unique=[...new Map(hits.filter(hit=>/^[0-9][A-Z0-9]{3}$/i.test(String(hit.identifier||''))).map(hit=>{ const identifier=String(hit.identifier).toUpperCase(); return [identifier,{ ...hit,identifier }]; })).values()];
    const records=await Promise.all(unique.map(async hit=>{
        try { const metadata=await fetcher(`${EntryEndpoint}${encodeURIComponent(String(hit.identifier))}`); if (!metadata.ok) return null; const record=await metadata.json() as JsonRecord,candidate=proteinStructureCandidate(hit,record,name); return candidate?{ candidate,record }:null; } catch { return null; }
    }));
    const valid=records.filter((row):row is {candidate:ProteinStructureCandidate;record:JsonRecord}=>row!==null),ranked=rankProteinStructures(valid.map(row=>row.candidate)).slice(0,6);
    return rankProteinStructures(await Promise.all(ranked.map(candidate=>enrichProteinStructure(candidate,valid.find(row=>row.candidate.pdbId===candidate.pdbId)!.record,name,referenceSmiles,similarity,fetcher))));
}

export async function fetchPdbExperimentalRecord(pdb:string):Promise<{record:JsonRecord;coordinates:string}> {
    const [metadata,coordinates]=await Promise.all([
        fetch(`${EntryEndpoint}${encodeURIComponent(pdb)}`),
        fetch(`https://files.rcsb.org/download/${encodeURIComponent(pdb)}.pdb`),
    ]);
    if (!metadata.ok) throw new Error(`RCSB metadata returned HTTP ${metadata.status}`);
    if (!coordinates.ok) throw new Error(`RCSB coordinates returned HTTP ${coordinates.status}`);
    const record=await metadata.json() as JsonRecord,pdbText=await coordinates.text();
    if (!pdbText.includes('\nATOM  ')&&!pdbText.startsWith('ATOM  ')) throw new Error('downloaded record contains no PDB ATOM coordinates');
    return { record,coordinates: pdbText };
}

export function inspectBoundLigands(pdbText:string):BoundLigandCandidate[] {
    const excluded=new Set(['HOH','WAT','DOD','NA','CL','K','CA','MG','MN','ZN','FE','CU','CO','NI','CD','HG','BR','IOD','SO4','PO4','GOL','EDO']);
    const cofactors=new Set(['HEM','HEC','FAD','FMN','NAD','NAP','SAM','SAH','COA','PLP','TPP','ATP','ADP','AMP','ANP','ACP','GTP','GDP','GMP','GNP']);
    const groups=new Map<string,{resname:string;chain:string;residue_number:string;atoms:number}>();
    pdbText.split(/\r?\n/).forEach(line=>{ if (!line.startsWith('HETATM')||line.length<54) return; const resname=line.slice(17,20).trim().toUpperCase(),chain=line.slice(21,22).trim(),residue_number=line.slice(22,27).trim(),element=(line.length>=78?line.slice(76,78):line.slice(12,14)).trim().toUpperCase(); if (excluded.has(resname)||element==='H'||element==='D') return; const key=`${resname}|${chain}|${residue_number}`,row=groups.get(key)||{ resname,chain,residue_number,atoms: 0 }; row.atoms++; groups.set(key,row); });
    return [...groups.values()].sort((a,b)=>Number(cofactors.has(a.resname))-Number(cofactors.has(b.resname))||b.atoms-a.atoms||a.resname.localeCompare(b.resname)).map(row=>({ ...row,heavy_atom_count: row.atoms,role: cofactors.has(row.resname)?'cofactor':'ligand',label: `${cofactors.has(row.resname)?'COFACTOR · ':''}${row.resname} · CHAIN ${row.chain||'—'} · RES ${row.residue_number} · ${row.atoms} HEAVY ATOMS` }));
}

export function receptorChainIds(pdbText:string):string[] {
    const ids=new Set<string>(); pdbText.split(/\r?\n/).forEach(line=>{ if (line.startsWith('ATOM  ')&&line.length>21)ids.add(line.slice(21,22).trim()||'_'); }); return [...ids].sort();
}

function renderCandidates(document:Document,candidates:ProteinStructureCandidate[],load:(pdbId:string)=>Promise<boolean>):void {
    const container=document.getElementById('protein-search-results'); if (!container) return;
    container.replaceChildren(); container.hidden=false;
    if (!candidates.length) { const empty=document.createElement('p'); empty.className='protein-search-empty'; empty.textContent='No experimental structures found. Try a gene symbol, protein family name, or upload a PDB file.'; container.append(empty); return; }
    const loadCandidate=async(candidate:ProteinStructureCandidate,button:HTMLButtonElement)=>{ const input=document.getElementById('campaign-pdb') as HTMLInputElement|null; if (input)input.value=candidate.pdbId; button.disabled=true; const prior=button.textContent||`USE ${candidate.pdbId}`; button.textContent='LOADING…'; const loaded=await load(candidate.pdbId); if (loaded)button.textContent='LOADED ✓'; else { button.disabled=false; button.textContent=prior; } };
    const recommended=document.createElement('button'); recommended.type='button'; recommended.className='use-recommended-structure'; recommended.textContent=`USE RECOMMENDED STRUCTURE · ${candidates[0].pdbId}`; recommended.addEventListener('click',()=>void loadCandidate(candidates[0],recommended)); container.append(recommended);
    candidates.forEach((candidate,index)=>{
        const card=document.createElement('article'); card.className=`protein-result${index===0?' recommended':''}`;
        const summary=document.createElement('div'),header=document.createElement('header'),id=document.createElement('b'),badge=document.createElement('em'),title=document.createElement('p'),facts=document.createElement('ul'),button=document.createElement('button');
        id.textContent=candidate.pdbId; badge.textContent=index===0?'BEST STARTING POINT':'CANDIDATE'; header.append(id,badge); title.textContent=candidate.title;
        candidate.reasons.forEach(reason=>{ const item=document.createElement('li'); item.textContent=reason; facts.append(item); });
        summary.append(header,title,facts); button.type='button'; button.textContent=`USE ${candidate.pdbId}`; button.setAttribute('aria-label',`Use PDB ${candidate.pdbId}: ${candidate.title}`);
        button.addEventListener('click',()=>void loadCandidate(candidate,button));
        card.append(summary,button); container.append(card);
    });
}

export function bindTargetStructureControls(document:Document,options:TargetSearchOptions):void {
    document.querySelectorAll<HTMLButtonElement>('[data-target-source]').forEach(button=>button.addEventListener('click',()=>{
        document.querySelectorAll<HTMLButtonElement>('[data-target-source]').forEach(item=>{ item.classList.toggle('active',item===button); item.setAttribute('aria-pressed',String(item===button)); });
        const mode=button.dataset.targetSource;
        (['pdb','upload','existing'] as const).forEach(source=>{ const panel=document.getElementById(`source-${source}-panel`); if (panel)panel.hidden=source!==mode; });
        options.notify(mode==='pdb'?'Search by protein name or enter a PDB accession. Review the ranked candidates before loading one.':mode==='upload'?'Upload fixed-column PDB coordinates. mmCIF is not accepted in this release.':'Reuse a versioned receptor only when it is scientifically the same system.');
    }));
    const input=document.getElementById('protein-name-search') as HTMLInputElement|null,button=document.getElementById('search-protein-structures') as HTMLButtonElement|null,status=document.getElementById('protein-search-status');
    if (!input||!button||!status) return;
    let generation=0;
    const run=async()=>{
        if (options.locked()) { options.notify('Structure search is locked while a physical RunSet is attached.'); return; }
        const name=input.value.trim(); if (!words(name).length) { status.textContent='Enter a protein name or gene symbol, such as EGFR or CDK2.'; input.focus(); return; }
        const request=++generation; button.disabled=true; button.textContent='SEARCHING…'; status.textContent=`Searching experimental PDB structures for “${name}”…`; status.setAttribute('aria-busy','true');
        try { const candidates=await searchProteinStructures(name,fetch,options.referenceSmiles?.()||'',options.similarity); if (request!==generation) return; renderCandidates(document,candidates,options.loadStructure); status.textContent=candidates.length?`${candidates.length} candidates compared · mutations, missing residues, construct coverage and co-crystal ligands included`:'No candidates found'; } catch (error) { if (request!==generation) return; renderCandidates(document,[],options.loadStructure); status.textContent=`Search unavailable · ${error instanceof Error?error.message:String(error)}`; } finally { if (request===generation) { button.disabled=false; button.textContent='FIND STRUCTURES'; status.removeAttribute('aria-busy'); } }
    };
    button.addEventListener('click',()=>void run()); input.addEventListener('keydown',event=>{ if (event.key==='Enter') { event.preventDefault(); void run(); } }); input.addEventListener('input',()=>{ generation+=1; });
}
