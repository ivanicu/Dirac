import { getRDKit } from '../../chemistry.backend.perception.rdkit-wasm.editable/semantic-chemistry-rdkit';

export type CompoundCandidate={
    id:string;
    name:string;
    smiles:string;
    formula:string;
    charge:number;
    source:'PUBCHEM'|'CHEMBL';
    sourceId:string;
    sourceUrl:string;
    similarity?:number;
};

export type AssayRow={
    id:string;
    smiles:string;
    query:string;
    endpoint:string;
    activity:string;
    unit:string;
    reference:boolean;
    priority:string;
};

type JsonRecord=Record<string,any>;
type CompoundToolOptions={
    locked:()=>boolean;
    notify:(message:string)=>void;
    getRows:()=>Array<{id:string;smiles:string}>;
    getParent:()=>string;
    appendLigand:(candidate:CompoundCandidate)=>Promise<void>;
    replaceSeries:(rows:Array<{id:string;smiles:string}>,context:{parentId?:string;assay?:AssayRow[];analogueEvidence?:CompoundCandidate[]})=>Promise<void>;
};

const PubChem='https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound';
const ChEMBL='https://www.ebi.ac.uk/chembl/api/data';
const propertyFields='Title,IUPACName,CanonicalSMILES,IsomericSMILES,MolecularFormula,Charge';

function text(value:unknown):string { return typeof value==='string'?value.trim():String(value??'').trim(); }
function safeId(value:string,fallback:string):string { const normalized=value.toUpperCase().replace(/[^A-Z0-9_.:+-]+/g,'-').replace(/^[^A-Z0-9]+/,'').slice(0,100); return normalized||fallback; }
function smilesFromProperty(row:JsonRecord):string { return text(row.SMILES||row.IsomericSMILES||row.ConnectivitySMILES||row.CanonicalSMILES); }

export function pubChemCandidates(payload:unknown):CompoundCandidate[] {
    const rows=(payload as JsonRecord)?.PropertyTable?.Properties;
    if (!Array.isArray(rows)) return [];
    return rows.map((row:JsonRecord)=>{
        const cid=text(row.CID),name=text(row.Title||row.IUPACName||`PubChem CID ${cid}`),smiles=smilesFromProperty(row);
        return { id: safeId(name,`CID-${cid}`),name,smiles,formula: text(row.MolecularFormula)||'NOT REPORTED',charge: Number.isFinite(Number(row.Charge))?Number(row.Charge):0,source: 'PUBCHEM' as const,sourceId: `CID ${cid}`,sourceUrl: `https://pubchem.ncbi.nlm.nih.gov/compound/${encodeURIComponent(cid)}` };
    }).filter((row:CompoundCandidate)=>/^CID \d+$/.test(row.sourceId)&&!!row.smiles);
}

export function chemblCandidate(payload:unknown):CompoundCandidate|null {
    const row=payload as JsonRecord,id=text(row?.molecule_chembl_id).toUpperCase(),structure=row?.molecule_structures||{},smiles=text(structure.canonical_smiles);
    if (!/^CHEMBL\d+$/.test(id)||!smiles) return null;
    const properties=row?.molecule_properties||{},name=text(row.pref_name||id);
    return { id,name,smiles,formula: text(properties.full_molformula)||'NOT REPORTED',charge: Number(properties.full_molcharge)||0,source: 'CHEMBL',sourceId: id,sourceUrl: `https://www.ebi.ac.uk/chembl/explore/compound/${id}` };
}

async function getJson(url:string,fetcher:typeof fetch):Promise<JsonRecord> {
    const response=await fetcher(url,{ headers: { accept: 'application/json' } });
    if (!response.ok) throw new Error(`source returned HTTP ${response.status}`);
    return await response.json() as JsonRecord;
}

export async function resolveCompoundQuery(query:string,fetcher:typeof fetch=fetch):Promise<CompoundCandidate[]> {
    const normalized=query.trim(); if (!normalized) throw new Error('Enter a compound name, PubChem CID, or ChEMBL ID');
    const chembl=normalized.match(/^CHEMBL\s*(\d+)$/i);
    if (chembl) { const id=`CHEMBL${chembl[1]}`,candidate=chemblCandidate(await getJson(`${ChEMBL}/molecule/${id}.json`,fetcher)); return candidate?[candidate]:[]; }
    const cid=normalized.match(/^(?:CID\s*)?(\d+)$/i),namespace=cid?'cid':'name',identifier=cid?cid[1]:normalized;
    return pubChemCandidates(await getJson(`${PubChem}/${namespace}/${encodeURIComponent(identifier)}/property/${propertyFields}/JSON`,fetcher)).slice(0,6);
}

function parseCsvRecords(raw:string):string[][] {
    const records:string[][]=[]; let record:string[]=[],field='',quoted=false;
    for (let index=0; index<raw.length; index++) {
 const char=raw[index];
        if (char==='"') { if (quoted&&raw[index+1]==='"') { field+='"'; index++; } else quoted=!quoted; continue; }
        if (char===','&&!quoted) { record.push(field); field=''; continue; }
        if ((char==='\n'||char==='\r')&&!quoted) { if (char==='\r'&&raw[index+1]==='\n')index++; record.push(field); if (record.some(value=>value.trim()))records.push(record); record=[]; field=''; continue; }
        field+=char;
    }
    record.push(field); if (record.some(value=>value.trim()))records.push(record); return records;
}

function truthy(value:string):boolean { return /^(?:1|true|yes|y|reference|parent)$/i.test(value.trim()); }
export function parseAssayCsv(raw:string):AssayRow[] {
    const records=parseCsvRecords(raw); if (records.length<2) throw new Error('CSV needs a header and at least one compound row');
    const headers=records[0].map(value=>value.trim().toLowerCase().replace(/[\s-]+/g,'_'));
    const column=(aliases:string[])=>headers.findIndex(header=>aliases.includes(header));
    const indexes={ id: column(['compound_id','id','name']),smiles: column(['smiles','canonical_smiles','isomeric_smiles']),query: column(['pubchem_cid','cid','chembl_id','compound','compound_name']),endpoint: column(['endpoint','assay_endpoint','measure']),activity: column(['activity','value','ic50','ec50','ki','kd']),unit: column(['unit','units']),reference: column(['reference','is_reference','parent']),priority: column(['priority','portfolio_priority']) };
    if (indexes.smiles<0&&indexes.query<0) throw new Error('CSV needs a SMILES column or a compound/CID/ChEMBL identifier column');
    return records.slice(1).map((values,rowIndex)=>{ const at=(index:number)=>index<0?'':text(values[index]); const query=at(indexes.query),id=safeId(at(indexes.id)||query,`CMPD-${String(rowIndex+1).padStart(3,'0')}`); return { id,smiles: at(indexes.smiles),query,endpoint: at(indexes.endpoint),activity: at(indexes.activity),unit: at(indexes.unit),reference: truthy(at(indexes.reference)),priority: at(indexes.priority) }; }).filter(row=>row.smiles||row.query);
}

export async function resolveAssayRows(rows:AssayRow[],fetcher:typeof fetch=fetch):Promise<AssayRow[]> {
    if (rows.length>50) throw new Error('Import at most 50 assay rows at a time');
    return await Promise.all(rows.map(async row=>{ if (row.smiles) return row; const candidate=(await resolveCompoundQuery(row.query,fetcher))[0]; if (!candidate) throw new Error(`No structure found for ${row.query}`); return { ...row,smiles: candidate.smiles,id: row.id.startsWith('CMPD-')?candidate.id:row.id }; }));
}

export function applyAssayContext(document:Document,assay:AssayRow[]):void {
    const endpoint=assay.find(row=>row.endpoint)?.endpoint||'ASSAY',unit=assay.find(row=>row.unit)?.unit||'UNIT NOT REPORTED',anchor=document.getElementById('assay-anchor') as HTMLInputElement|null;
    if (anchor)anchor.value=`${endpoint} · ${unit}`;
    const priorities=document.getElementById('compound-priorities') as HTMLTextAreaElement|null;
    if (priorities)priorities.value=assay.map(row=>`${row.id} | ${row.priority||'UNRANKED'} | ${row.activity?`${row.endpoint||'ACTIVITY'} ${row.activity} ${row.unit}`:'imported assay compound'} | STATUS UNREPORTED`).join('\n');
    const priority=document.getElementById('portfolio-priority') as HTMLSelectElement|null; if (priority&&!priority.value)priority.value='HIGH · DECISION-CHANGING';
}

function tanimoto(left:string,right:string):number { let intersection=0,union=0; const length=Math.min(left.length,right.length); for (let index=0; index<length; index++) { const av=left[index]==='1',bv=right[index]==='1'; if (av&&bv)intersection++; if (av||bv)union++; } return union?intersection/union:0; }
export async function morganSimilarity(leftSmiles:string,rightSmiles:string):Promise<number|null> {
    const RDKit=await getRDKit(); let left:ReturnType<typeof RDKit.get_mol>|null=null,right:ReturnType<typeof RDKit.get_mol>|null=null;
    try { left=RDKit.get_mol(leftSmiles); right=RDKit.get_mol(rightSmiles); if (!left?.is_valid()||!right?.is_valid()) return null; const leftFp=(left as unknown as {get_morgan_fp():string}).get_morgan_fp(),rightFp=(right as unknown as {get_morgan_fp():string}).get_morgan_fp(); return tanimoto(leftFp,rightFp); } finally { left?.delete(); right?.delete(); }
}

export function transformationRisk(parent:string,candidate:CompoundCandidate):'LOW'|'MEDIUM'|'HIGH' {
    const stereoChanged=/[@\\/]/.test(parent)!==/[@\\/]/.test(candidate.smiles),similarity=candidate.similarity??0;
    return candidate.charge!==0||stereoChanged||similarity<0.30?'HIGH':similarity<0.45?'MEDIUM':'LOW';
}

export async function similarPubChemCompounds(parentSmiles:string,fetcher:typeof fetch=fetch):Promise<CompoundCandidate[]> {
    const result=await getJson(`${PubChem}/fastsimilarity_2d/smiles/${encodeURIComponent(parentSmiles)}/cids/JSON?Threshold=65&MaxRecords=30`,fetcher),cids=(result.IdentifierList?.CID||[]) as unknown[];
    const unique=[...new Set(cids.map(value=>String(value)).filter(value=>/^\d+$/.test(value)))].slice(0,24); if (!unique.length) return [];
    const candidates=pubChemCandidates(await getJson(`${PubChem}/cid/${unique.join(',')}/property/${propertyFields}/JSON`,fetcher));
    const scored=await Promise.all(candidates.map(async candidate=>({ ...candidate,similarity: await morganSimilarity(parentSmiles,candidate.smiles)??0 })));
    return scored.filter(candidate=>candidate.similarity<0.999).sort((a,b)=>(b.similarity??0)-(a.similarity??0)).slice(0,7);
}

function renderCompoundResults(document:Document,candidates:CompoundCandidate[],add:(candidate:CompoundCandidate)=>Promise<void>):void {
    const host=document.getElementById('compound-search-results'); if (!host) return; host.replaceChildren(); host.hidden=false;
    if (!candidates.length) { const empty=document.createElement('p'); empty.textContent='No exact structure record found. Try a PubChem CID or ChEMBL ID.'; host.append(empty); return; }
    candidates.forEach(candidate=>{ const card=document.createElement('article'),summary=document.createElement('div'),title=document.createElement('b'),identity=document.createElement('span'),smiles=document.createElement('code'),button=document.createElement('button'); card.className='compound-result'; title.textContent=candidate.name; identity.textContent=`${candidate.sourceId} · ${candidate.formula} · CHARGE ${candidate.charge>=0?'+':''}${candidate.charge}`; smiles.textContent=candidate.smiles; summary.append(title,identity,smiles); button.type='button'; button.textContent='ADD EXACT STRUCTURE'; button.addEventListener('click',async()=>{ button.disabled=true; try { await add(candidate); button.textContent='ADDED ✓'; } catch { button.disabled=false; } }); card.append(summary,button); host.append(card); });
}

function renderAnalogueEvidence(document:Document,parent:string,parentSmiles:string,candidates:CompoundCandidate[]):void {
    const host=document.getElementById('series-generation-results'); if (!host) return; host.replaceChildren(); host.hidden=false;
    const heading=document.createElement('b'); heading.textContent=`${parent} + ${candidates.length} ANALOGUES · TRANSFORMATION SCREEN`; host.append(heading);
    candidates.forEach(candidate=>{ const row=document.createElement('span'),risk=transformationRisk(parentSmiles,candidate); row.className=`risk-${risk.toLowerCase()}`; row.textContent=`${candidate.id} · SIMILARITY ${(candidate.similarity??0).toFixed(2)} · CHARGE ${candidate.charge>=0?'+':''}${candidate.charge} · ${risk} RISK`; host.append(row); });
}

export function bindCompoundWorkflow(document:Document,options:CompoundToolOptions):void {
    const searchInput=document.getElementById('compound-name-search') as HTMLInputElement|null,searchButton=document.getElementById('search-compounds') as HTMLButtonElement|null,status=document.getElementById('compound-search-status');
    const runSearch=async()=>{ if (!searchInput||!searchButton||!status) return; if (options.locked()) return options.notify('Compound editing is locked while a physical RunSet is attached.'); const query=searchInput.value.trim(); searchButton.disabled=true; searchButton.textContent='SEARCHING…'; status.textContent=`Resolving “${query}” to a database structure…`; try { const candidates=await resolveCompoundQuery(query); renderCompoundResults(document,candidates,options.appendLigand); status.textContent=candidates.length?`${candidates.length} exact identity record${candidates.length===1?'':'s'} · confirm before adding`:'No exact structure found'; } catch (error) { renderCompoundResults(document,[],options.appendLigand); status.textContent=`Search unavailable · ${error instanceof Error?error.message:String(error)}`; } finally { searchButton.disabled=false; searchButton.textContent='FIND COMPOUND'; } };
    searchButton?.addEventListener('click',()=>void runSearch()); searchInput?.addEventListener('keydown',event=>{ if (event.key==='Enter') { event.preventDefault(); void runSearch(); } });
    document.getElementById('assay-file')?.addEventListener('change',async event=>{ const input=event.target as HTMLInputElement,file=input.files?.[0]; if (!file) return; try { if (options.locked()) throw new Error('campaign is locked by a physical RunSet'); const assay=await resolveAssayRows(parseAssayCsv(await file.text())); if (options.getRows().length&&!window.confirm(`Replace the current series with ${assay.length} assay compounds?`)) return; applyAssayContext(document,assay); await options.replaceSeries(assay.map(row=>({ id: row.id,smiles: row.smiles })),{ parentId: assay.find(row=>row.reference)?.id,assay }); options.notify(`${file.name}: ${assay.length} compounds imported with assay context, reference and priorities.`); } catch (error) { options.notify(`Assay import refused · ${error instanceof Error?error.message:String(error)}`); } finally { input.value=''; } });
    const parent=document.getElementById('parent-compound-select') as HTMLSelectElement|null,generator=document.getElementById('build-analogue-series') as HTMLButtonElement|null;
    parent?.addEventListener('change',()=>{ if (generator)generator.disabled=!parent.value; });
    generator?.addEventListener('click',async()=>{ const parentId=options.getParent(),parentRow=options.getRows().find(row=>row.id===parentId); if (!parentRow) return options.notify('Choose the reference compound first.'); generator.disabled=true; generator.textContent='FINDING ANALOGUES…'; try { const analogues=await similarPubChemCompounds(parentRow.smiles); if (analogues.length<7) throw new Error(`only ${analogues.length} suitable analogues were found`); const unique=new Set<string>(),rows=[parentRow,...analogues.map(candidate=>{ let id=safeId(candidate.id,candidate.sourceId.replace(' ', '-')); const base=id; let suffix=2; while (unique.has(id)||id===parentId)id=`${base}-${suffix++}`; unique.add(id); candidate.id=id; return { id,smiles: candidate.smiles }; })],stereoPolicy=document.getElementById('ligand-stereo') as HTMLSelectElement|null; if (stereoPolicy)stereoPolicy.value='enumerate_unknown'; await options.replaceSeries(rows,{ parentId,analogueEvidence: analogues }); renderAnalogueEvidence(document,parentId,parentRow.smiles,analogues); options.notify(`Eight-compound series ready: ${parentId} plus seven PubChem analogues, each screened for similarity, formal charge and stereo risk.`); } catch (error) { options.notify(`Analogue generation refused · ${error instanceof Error?error.message:String(error)}`); } finally { generator.disabled=!options.getParent(); generator.textContent='ADD 7 SIMILAR ANALOGUES'; } });
}
