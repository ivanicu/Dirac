import { DiracClient, type Envelope } from '../../app/services/dirac-client';
import { LigandDepiction, type AtomHighlight } from '../../chemistry.backend.perception.rdkit-wasm.editable/ligand-depiction';
import { getRDKit } from '../../chemistry.backend.perception.rdkit-wasm.editable/semantic-chemistry-rdkit';
import { MoleculeSketcher, type SketchedMolecule } from './molecule-sketcher';
import { PoseReviewer, type ReviewSystem } from './pose-reviewer';
import { decideWorkbenchBoot } from './workbench-boot';
import { escapeHtml, markPipelineReadyDom, renderOperationConfirmationDom, renderPreparationControlsDom, renderPreparationPolicyDom, renderRunHistoryDom, renderRunJobsDom, setSafeText } from './workbench-dom';
import { acknowledgedPreparationReceipt, campaignScientificRefFrom, contentRef, fullJobId, operationBindingFromReceipt, preparationReceiptBinding, runReceiptFromData, runReceiptMatchesData, runReceiptState, type AcknowledgedPreparationReceipt, type AnyPreparationReceipt as PreparationReceipt, type ArtifactRef, type CampaignScientificRef, type ContentRef, type PreparationReceipt as DurablePreparationReceipt, type PlannerOutputReceipt, type PlannerReceipt, type RunReceipt, type RunReceiptState } from './workbench-receipts';
import { globalPhysicalReceiptKey, RECEIPT_KEYS, WorkbenchReceiptStore, type ReceiptStorage } from './workbench-receipt-store';
import { OperationCoordinator, aggregateArmMatches, canonicalJson, chemistryEvidenceFrom, chemistryEvidenceView, exactOperationBindingMatches, executionEligibilityFrom, sameExactRef, type ExactAggregateArm, type ExactOperationBinding, type ExactRunBinding, type OperationScope } from './workbench-state';
import { aggregatePanelViewFrom, preparationPolicyGate, preparationPolicyViewFrom, runHistoryViewFrom, runJobsViewFrom } from './workbench-view-model';
import { workbenchShellMarkup } from './workbench-shell';
import { DemoCompounds, FallbackEdges, FallbackNetwork } from './workbench-fixture';
import { CAMPAIGN_CACHE_KEYS, createCampaignState, draftFromCampaignEnvelope } from './workbench-campaign-state';
import { createStoredPreparationReceipt, exactPreparationResultFrom, preparationElapsedSeconds, preparationReceiptMatchesOpenCampaign, preparationResultMatchesOpenCampaign, preparationSubmissionLockName, preparedSystemFromPreparationResult, submitPreparationExactlyOnce } from './workbench-preparation';
import type { AtomInfo, Bond, BuilderStage, CampaignDraftV2, Compound, DepictionContract, Edge, ExecutionContract, PreparedSystemOption, RunJob, Network } from './workbench-types';


const query = new URLSearchParams(location.search);
const apiBase = query.get('api') || `http://${location.hostname}:8901`;
const client = new DiracClient({ baseUrl: apiBase, timeoutMs: 600_000 });
const auditCopyId = (query.get('copy') || 'main').replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 16) || 'main';
const tabOwnerId=crypto.randomUUID();
const operations=new OperationCoordinator(tabOwnerId);
const copyStorageKey = (key:string) => auditCopyId === 'main' ? key : `${key}.copy.${auditCopyId}`;
const copyStorage = {
    get: (key:string) => localStorage.getItem(copyStorageKey(key)),
    set: (key:string,value:string) => localStorage.setItem(copyStorageKey(key),value),
    remove: (key:string) => localStorage.removeItem(copyStorageKey(key)),
};
const browserStorage:ReceiptStorage={get:key=>localStorage.getItem(key),set:(key,value)=>localStorage.setItem(key,value),remove:key=>localStorage.removeItem(key)};
const receiptStore=new WorkbenchReceiptStore(copyStorage,auditCopyId==='main'?browserStorage:undefined);
const campaignCacheKey=CAMPAIGN_CACHE_KEYS.draft;
const plannerReceiptKey=RECEIPT_KEYS.planner,plannerOutputReceiptKey=RECEIPT_KEYS.plannerOutput,runReceiptKey=RECEIPT_KEYS.run;
function readPlannerReceipt():PlannerReceipt|null{return receiptStore.readPlanner();}
function writePlannerReceipt(receipt:PlannerReceipt):void{receiptStore.writePlanner(receipt);syncPlannerRecoveryControl();}
function plannerReceiptOwned(ownerToken:string):PlannerReceipt|null{return receiptStore.plannerOwned(ownerToken);}
function removePlannerReceiptIf(ownerToken:string,jobId?:string|null):boolean{const removed=receiptStore.removePlanner(ownerToken,jobId);if(removed)syncPlannerRecoveryControl();return removed;}
function archiveDetachedPlannerReceipt(receipt:PlannerReceipt,reason:string):void{receiptStore.archivePlanner(receipt,reason,new Date().toISOString());syncPlannerRecoveryControl();}
function readPlannerOutputReceipt():PlannerOutputReceipt|null{return receiptStore.readPlannerOutput();}
function writePlannerOutputReceipt(receipt:PlannerOutputReceipt):void{receiptStore.writePlannerOutput(receipt);}
function readRunReceipt():RunReceipt|null{return receiptStore.readRun();}
function writeRunReceipt(receipt:RunReceipt):void{receiptStore.writeRun(receipt);}
function runReceiptOwned(ownerToken:string):RunReceipt|null{return receiptStore.runOwned(ownerToken);}
function removeRunReceiptIf(ownerToken:string,runId?:string|null):boolean{return receiptStore.removeRun(ownerToken,runId);}
function updateOwnedRunReceipt(receipt:RunReceipt,changes:Partial<RunReceipt>):RunReceipt|null{const current=runReceiptOwned(receipt.owner_token);if(!current||current.request_key!==receipt.request_key||current.run_id!==receipt.run_id)return null;const next={...current,...changes,updated_at:new Date().toISOString()};writeRunReceipt(next);activeRunReceipt=next;activeRunId=next.run_id;return next;}
function readPreparationReceipt():PreparationReceipt|null{return receiptStore.readPreparation();}
function removePreparationReceiptIf(receipt:PreparationReceipt):boolean{return receiptStore.removePreparationReceipt(receipt);}
function archiveDetachedPreparationReceipt(receipt:PreparationReceipt):boolean{return receiptStore.archivePreparation(receipt);}
const campaignState=createCampaignState({
    client,storage:copyStorage,copyId:auditCopyId,
    currentEpoch:()=>draftEpoch,currentEditRevision:()=>operations.edits,
    requestStillCurrent:(draft,epoch)=>draftRequestStillCurrent(draft,epoch),
    envelopeFailure,
});
const campaignStateAdapter=campaignState.adapter;
const readCampaignCache=campaignState.readCache;
const writeCampaignCache=campaignState.writeCache;
const archiveCampaignCache=campaignState.archiveCache;
function archiveDetachedRun(receiptOrId:RunReceipt|string,reason:string,runSnapshot?:Record<string,unknown>):void{const runId=typeof receiptOrId==='string'?receiptOrId:receiptOrId.run_id;if(!runId||!fullJobId(runId))return;const context=activeCampaignContext(),receipt=typeof receiptOrId==='string'?null:receiptOrId;receiptStore.archiveRun({...(receipt||{}),run_id:runId,detached_at:new Date().toISOString(),reason,edge_id:receipt?.edge_id||selectedEdge?.edge_id||null,campaign_scientific_ref:receipt?.campaign_scientific_ref||(context?.campaign_id?{kind:'rbfe_campaign',id:context.campaign_id,version:context.campaign_scientific_generation,sha256:context.campaign_scientific_digest}:null),...(runSnapshot?{run_snapshot:runSnapshot}: {})});}
const workbenchLabel = () => auditCopyId === 'main' ? 'FEP LAB' : `FEP LAB · COPY ${auditCopyId.toUpperCase()}`;
const DefaultNetworkJobId = 'b1a4ddf3-8663-4fa1-87d0-8c6a137702c5';
function currentNetworkJobId():string{return copyStorage.get('dirac.rbfe.active_network_job_id')||DefaultNetworkJobId;}
type CampaignContext = { network_job_id:string; name:string; receptor_label:string; ligand_count:number; prepared_system_id:string; campaign_id?:string; campaign_scientific_generation?:number; campaign_scientific_digest?:string };
let authoritativeCampaignContextState:CampaignContext|null=null;
function persistedCampaignContextHint():CampaignContext|null{try{const parsed=JSON.parse(copyStorage.get('dirac.rbfe.active_campaign_context')||'null') as CampaignContext|null;return parsed?.network_job_id===currentNetworkJobId()?parsed:null;}catch{return null;}}
function activeCampaignContext():CampaignContext|null{return authoritativeCampaignContextState;}
function plannerOutputReceiptMatches(receipt:PlannerOutputReceipt|null,context:CampaignContext|null,planRef:ArtifactRef|null=networkArtifactRef):boolean{return!!receipt&&!!context?.campaign_id&&receipt.network_job_id===context.network_job_id&&receipt.campaign_scientific_ref.id===context.campaign_id&&receipt.campaign_scientific_ref.version===context.campaign_scientific_generation&&receipt.campaign_scientific_ref.sha256===context.campaign_scientific_digest&&receipt.prepared_system_ref.id===context.prepared_system_id&&sameExactRef(receipt.plan_network_ref,planRef);}
function persistPlannerOutput(networkJobId:string,planRef:ArtifactRef,campaignRef:CampaignScientificRef,systemRef:ContentRef):PlannerOutputReceipt{const receipt:PlannerOutputReceipt={schema_version:1,network_job_id:networkJobId,created_at:new Date().toISOString(),campaign_scientific_ref:campaignRef,prepared_system_ref:systemRef,plan_network_ref:planRef};writePlannerOutputReceipt(receipt);return receipt;}
function campaignContextFromNetwork(value:Network,networkJobId:string,metadataHint:CampaignContext|null):CampaignContext|null{
    const bound=value.campaign_context;
    if(!bound)return null;
    const complete=typeof bound.campaign_id==='string'&&bound.campaign_id.length>0&&Number.isInteger(bound.campaign_scientific_generation)&&bound.campaign_scientific_generation>0&&/^sha256:[0-9a-f]{64}$/.test(bound.campaign_scientific_digest)&&typeof bound.prepared_system_id==='string'&&bound.prepared_system_id.length>0;
    if(!complete)throw new Error('rbfe.network has no complete immutable campaign context');
    const hintMatches=metadataHint?.network_job_id===networkJobId&&metadataHint.campaign_id===bound.campaign_id&&metadataHint.campaign_scientific_generation===bound.campaign_scientific_generation&&metadataHint.campaign_scientific_digest===bound.campaign_scientific_digest&&metadataHint.prepared_system_id===bound.prepared_system_id;
    return{network_job_id:networkJobId,name:hintMatches?metadataHint!.name:`CAMPAIGN ${bound.campaign_id}`,receptor_label:hintMatches?metadataHint!.receptor_label:'SERVER-BOUND RECEPTOR',ligand_count:value.compounds.length,prepared_system_id:bound.prepared_system_id!,campaign_id:bound.campaign_id,campaign_scientific_generation:bound.campaign_scientific_generation,campaign_scientific_digest:bound.campaign_scientific_digest};
}
function bindAuthoritativeCampaignContext(value:Network,networkJobId:string,metadataHint:CampaignContext|null):CampaignContext|null{
    const context=campaignContextFromNetwork(value,networkJobId,metadataHint);authoritativeCampaignContextState=context;
    if(context){copyStorage.set('dirac.rbfe.active_network_job_id',networkJobId);copyStorage.set('dirac.rbfe.active_campaign_context',JSON.stringify(context));}
    else{copyStorage.remove('dirac.rbfe.active_campaign_context');if(networkJobId!==DefaultNetworkJobId)copyStorage.remove('dirac.rbfe.active_network_job_id');}
    return context;
}
function applyCampaignContext(context:CampaignContext|null):void{
    if(!context)return;
    const dataset=document.querySelector('.context span:first-child b');if(dataset)dataset.textContent=context.name.toUpperCase();
    const scope=document.querySelector('.dataset-scope');if(scope)scope.textContent=`CAMPAIGN · ${context.name.toUpperCase()} · ${context.receptor_label} · ${context.ligand_count} LIGANDS`;
    const decisions=document.querySelectorAll<HTMLElement>('.decision-lines>span'),priority=(document.getElementById('portfolio-priority') as HTMLSelectElement|null)?.value||'UNRANKED',next=(document.getElementById('next-action') as HTMLInputElement|null)?.value||'add a decision-linked next action';if(decisions[0]){decisions[0].innerHTML='<b>ENGINEERING NEXT</b>';decisions[0].append(document.createTextNode(`${selectedEdge.left_id}→${selectedEdge.right_id} · qualify exact posed-system mapping`));}if(decisions[1]){decisions[1].innerHTML='<b>PROJECT NEXT</b>';decisions[1].append(document.createTextNode(`${priority} · ${next}`));}
    const brand=document.querySelector<HTMLElement>('.fep-topbar .brand span');if(brand)brand.textContent=workbenchLabel();
    const replan=document.getElementById('replan') as HTMLButtonElement|null;if(replan){replan.disabled=false;replan.textContent='REPLAN NETWORK';replan.setAttribute('title','Re-run this campaign network planner with the same compounds');}
}
function sameCampaignScientificContext(left:CampaignContext|null,right:CampaignContext|null):boolean{return left===right||!!left&&!!right&&left.campaign_id===right.campaign_id&&left.campaign_scientific_generation===right.campaign_scientific_generation&&left.campaign_scientific_digest===right.campaign_scientific_digest&&left.prepared_system_id===right.prepared_system_id;}

const app = document.getElementById('fep-lab');
if (!app) throw new Error('FEP Lab mount point is missing');
app.innerHTML=workbenchShellMarkup();
if(auditCopyId!=='main'){
    document.title=`DIRAC · FEP Audit Copy ${auditCopyId.toUpperCase()}`;
    document.querySelectorAll<HTMLElement>('.fep-topbar .brand span').forEach(element=>element.textContent=workbenchLabel());
    const builderKicker=document.querySelector<HTMLElement>('.builder-title small');if(builderKicker)builderKicker.textContent=`AUDIT COPY ${auditCopyId.toUpperCase()} · NEW CAMPAIGN`;
}

let network = FallbackNetwork;
let networkWorkspaceVisible=query.get('new')!=='1';
let selectedEdge: Edge = network.edges[2];
let sourceState: 'durable-job' | 'replanned-job' | 'cached-snapshot' = 'cached-snapshot';
let focusedCompoundId: string | null = null;
let compoundFocusOnly = false;
const atomInfoCache = new Map<string, AtomInfo>();
const executionContract: ExecutionContract = { system:null, parentPose:null, proposalPose:null, protocolPreset:'openfe-rfe-standard-v1', validated:false };
let networkArtifactRef: ArtifactRef | null = null;
let edgeNetworkRef: ArtifactRef | null = null;
let edgeSpecRef: ArtifactRef | null = null;
let complexTransformationRef: ArtifactRef | null = null;
let solventTransformationRef: ArtifactRef | null = null;
let preflightData: Record<string, any> | null = null;
let runJobs: RunJob[] = [];
let activeRunReceipt:RunReceipt|null=readRunReceipt();
let activeRunId: string | null = activeRunReceipt?.run_id||copyStorage.get('dirac.rbfe.active_run_id');
let aggregateArm:ExactAggregateArm|null=null;
let aggregateArmPhysicalSnapshot:string|null=null;
let riskPoints: Array<{ edge: Edge; x: number; y: number }> = [];
let executionBuildOperationId=0;

function text(id: string, value: string): void { setSafeText(document.getElementById(id), value); }
function artifactRef(value: Record<string, any> | undefined): ArtifactRef | null {
    if (!value?.id || !value?.sha256) return null;
    if(value.kind&&value.kind!=='artifact')return null;
    const sha=String(value.sha256);return {kind:'artifact',id:String(value.id),sha256:sha.startsWith('sha256:')?sha:`sha256:${sha}`};
}
function sleep(ms:number):Promise<void>{return new Promise(resolve=>setTimeout(resolve,ms));}
function value(v: number | null | undefined, digits = 3): string { return Number.isFinite(v) ? Number(v).toFixed(digits) : '—'; }
function scoreBand(edge: Edge): 'ready' | 'review' | 'blocked' {
    if (edge.mapping_score >= .8) return 'ready';
    if (edge.mapping_score >= .5) return 'review';
    return 'blocked';
}
function bandLabel(edge: Edge): string { return scoreBand(edge) === 'ready' ? 'MAP HIGH' : scoreBand(edge) === 'review' ? 'MAP MID' : 'MAP LOW'; }
function hasGovernedMapping(edge: Edge): boolean {
    return scoreBand(edge)==='ready'
        && edge.mapping_source==='openfe_system_builder.reviewed_receptor_frame'
        && !!chemistryEvidenceFrom(edge.chemistry_evidence)
        && executionEligibilityFrom(edge.execution_eligibility)?.verdict==='CONFIRMED';
}
function compoundIn(target:Network,id: string): Compound { return target.compounds.find(row => row.id === id) || { id, canonical_smiles: '' }; }
function compound(id: string): Compound { return compoundIn(network,id); }

async function atomInfo(smiles: string): Promise<AtomInfo> {
    const cached = atomInfoCache.get(smiles); if (cached) return cached;
    const RDKit = await getRDKit(); const mol = RDKit.get_mol(smiles); if (!mol||!mol.is_valid()) throw new Error(`RDKit could not parse ${smiles}`);
    try {
        const molblock = mol.get_molblock(),canonicalSmiles=mol.get_smiles(); const lines = molblock.split(/\r?\n/); const count = Number.parseInt(lines[3]?.slice(0, 3).trim() || '0', 10), bondCount=Number.parseInt(lines[3]?.slice(3,6).trim()||'0',10);
        const symbols = Array.from({ length: count }, (_, index) => lines[4 + index]?.slice(31, 34).trim() || '?');
        const chargeCodes: Record<number,number>={1:3,2:2,3:1,5:-1,6:-2,7:-3}; const charges=Array.from({length:count},(_,index)=>chargeCodes[Number.parseInt(lines[4+index]?.slice(36,39).trim()||'0',10)]||0), atomStereo=Array.from({length:count},(_,index)=>Number.parseInt(lines[4+index]?.slice(39,42).trim()||'0',10));
        const bonds:Bond[]=Array.from({length:bondCount},(_,index)=>{const line=lines[4+count+index]||'';return{left:Number.parseInt(line.slice(0,3).trim(),10)-1,right:Number.parseInt(line.slice(3,6).trim(),10)-1,order:Number.parseInt(line.slice(6,9).trim(),10),stereo:Number.parseInt(line.slice(9,12).trim()||'0',10)};}).filter(b=>b.left>=0&&b.right>=0);
        lines.slice(4+count+bondCount).forEach(line=>{if(!line.startsWith('M  CHG'))return;const tokens=line.trim().split(/\s+/).slice(3).map(Number);for(let i=0;i<tokens.length;i+=2)if(Number.isInteger(tokens[i]-1))charges[tokens[i]-1]=tokens[i+1];});
        const adjacency=Array.from({length:count},()=>new Set<number>());bonds.forEach(b=>{adjacency[b.left].add(b.right);adjacency[b.right].add(b.left);});let components=0;const unseen=new Set(symbols.map((_,i)=>i));while(unseen.size){components++;const stack=[unseen.values().next().value as number];unseen.delete(stack[0]);while(stack.length){adjacency[stack.pop()!].forEach(next=>{if(unseen.delete(next))stack.push(next);});}}const cycleRank=Math.max(0,bonds.length-count+components);
        let stereoAvailable=true,cipAtoms:Array<[number,string]>=[],cipBonds:Array<[number,number,string]>=[],potentialEzBonds:Array<[number,number]>=[];
        try{
            const stereo=JSON.parse(mol.get_stereo_tags()) as {CIP_atoms?:Array<[number,string]>;CIP_bonds?:Array<[number,number,string]>};cipAtoms=Array.isArray(stereo.CIP_atoms)?stereo.CIP_atoms:[];cipBonds=Array.isArray(stereo.CIP_bonds)?stereo.CIP_bonds:[];
            const json=JSON.parse(mol.get_json()) as {defaults?:{atom?:{impHs?:number}};molecules?:Array<{atoms?:Array<{impHs?:number}>;extensions?:Array<{name?:string;cipRanks?:number[]}>}>},molecule=json.molecules?.[0],atoms=molecule?.atoms||[],ranks=molecule?.extensions?.find(extension=>extension.name==='rdkitRepresentation')?.cipRanks||[],defaultHydrogens=Number(json.defaults?.atom?.impHs||0);
            const substituentKeys=(atomIndex:number,partnerIndex:number):string[]=>{const keys=bonds.filter(bond=>bond.left===atomIndex||bond.right===atomIndex).map(bond=>bond.left===atomIndex?bond.right:bond.left).filter(index=>index!==partnerIndex).map(index=>`A:${String(ranks[index]??`${symbols[index]}:${index}`)}`),implicitHydrogens=Number(atoms[atomIndex]?.impHs??defaultHydrogens);for(let index=0;index<implicitHydrogens;index++)keys.push('H');return keys;};
            potentialEzBonds=bonds.filter(bond=>bond.order===2&&!cipBonds.some(([left,right])=>(left===bond.left&&right===bond.right)||(left===bond.right&&right===bond.left))).filter(bond=>{const left=substituentKeys(bond.left,bond.right),right=substituentKeys(bond.right,bond.left);return left.length===2&&right.length===2&&left[0]!==left[1]&&right[0]!==right[1];}).map(bond=>[bond.left,bond.right]);
        }catch{stereoAvailable=false;}
        const result = { symbols, molblock, canonicalSmiles, charges, atomStereo, bonds, cycleRank, stereoAvailable, cipAtoms, cipBonds, potentialEzBonds }; atomInfoCache.set(smiles, result); return result;
    }
    finally { mol.delete(); }
}

async function moleculeSvg(smiles: string, width = 180, height = 120, atomHighlights: AtomHighlight[] = [], showAtomIndices = false): Promise<string> {
    const info = await atomInfo(smiles); if (!info) return ''; const result = await LigandDepiction.depict(info.molblock, { width, height, atomHighlights, showAtomIndices }); return result?.svgString || '';
}

function jaccardDisagreement(left: number[][], right: number[][]): number | null {
    if (!left.length && !right.length) return null; const a = new Set(left.map(pair => pair.join(':'))), b = new Set(right.map(pair => pair.join(':'))); const union = new Set([...a, ...b]); let intersection = 0; a.forEach(pair => { if (b.has(pair)) intersection++; }); return 1 - intersection / Math.max(1, union.size);
}

async function enrichMappingEvidence(target:Network): Promise<void> {
    const infos = new Map<string, { symbols: string[]; molblock: string }>(); for (const row of target.compounds) infos.set(row.id, await atomInfo(row.canonical_smiles));
    for (const edge of target.edges) {
        const leftCount = infos.get(edge.left_id)?.symbols.length || 0, rightCount = infos.get(edge.right_id)?.symbols.length || 0;
        const heavy = (pairs: number[][] = []) => pairs.filter(([a,b]) => a < leftCount && b < rightCount);
        const proposals = edge.mapping_proposals || {}; edge.heavy_atom_mapping_proposals = Object.fromEntries(Object.entries(proposals).map(([name,pairs]) => [name, heavy(pairs)]));
        edge.mapped_heavy_atom_count = edge.selected_atom_mapping ? heavy(edge.selected_atom_mapping).length : edge.rdkit_fmcs_diagnostic?.mapped_atom_count ?? undefined;
        const names = Object.keys(edge.heavy_atom_mapping_proposals).sort(); edge.heavy_mapping_disagreement_jaccard = names.length >= 2 ? jaccardDisagreement(edge.heavy_atom_mapping_proposals[names[0]], edge.heavy_atom_mapping_proposals[names[1]]) : null;
    }
}

async function mappingHighlights(edge: Edge,target:Network=network): Promise<{ left: AtomHighlight[]; right: AtomHighlight[]; summary: string; ledger: Array<{label:string;value:string;state:'confirmed'|'changed'|'unverified'}> }> {
    const evidenceView=chemistryEvidenceView(edge.chemistry_evidence),left=compoundIn(target,edge.left_id),right=compoundIn(target,edge.right_id);
    if(!edge.depiction_contract)return{left:[],right:[],summary:evidenceView.summary,ledger:evidenceView.ledger};
    const leftView=left.depiction_smiles||left.canonical_smiles,rightView=right.depiction_smiles||right.canonical_smiles,li=await atomInfo(leftView),ri=await atomInfo(rightView);
    const pairs=(Array.isArray(edge.depiction_contract.selected_heavy_atom_mapping)?edge.depiction_contract.selected_heavy_atom_mapping:[]).filter(pair=>Array.isArray(pair)&&pair.length===2&&Number.isInteger(pair[0])&&Number.isInteger(pair[1])&&pair[0]>=0&&pair[1]>=0&&pair[0]<li.symbols.length&&pair[1]<ri.symbols.length);
    const leftMapped=new Set<number>(),rightMapped=new Set<number>(),leftHighlights:AtomHighlight[]=[],rightHighlights:AtomHighlight[]=[];
    pairs.forEach(([parent,proposal])=>{leftMapped.add(parent);rightMapped.add(proposal);leftHighlights.push({atomIndex:parent,color:'#58dff4',alpha:.55});rightHighlights.push({atomIndex:proposal,color:'#58dff4',alpha:.55});});
    // Mapping gaps are colored only when the backend ledger attests UNMAPPED=CHANGED.
    // All chemistry verdicts and witness text remain server-owned.
    const unmappedChanged=evidenceView.evidence?.ledger.find(row=>row.dimension==='UNMAPPED')?.verdict==='CHANGED';
    if(unmappedChanged){
        li.symbols.forEach((_,index)=>{if(!leftMapped.has(index))leftHighlights.push({atomIndex:index,color:'#ff6476',alpha:.5});});
        ri.symbols.forEach((_,index)=>{if(!rightMapped.has(index))rightHighlights.push({atomIndex:index,color:'#57e59a',alpha:.5});});
    }
    return{left:leftHighlights,right:rightHighlights,summary:evidenceView.summary,ledger:evidenceView.ledger};
}

const positions: Record<string, [number, number]> = {
    'T4L-BEN': [.22,.28], 'T4L-FLU': [.78,.28], 'T4L-CL': [.50,.76],
};

async function renderNetwork(targetNetwork:Network=network): Promise<void> {
    const scope=operations.begin('render-network'),targetEdgeId=selectedEdge.edge_id,targetFocusId=focusedCompoundId,targetFocusOnly=compoundFocusOnly;
    const graph = document.getElementById('network-graph'); const edgeLayer = document.getElementById('edge-layer') as unknown as SVGSVGElement|null; const nodeLayer = document.getElementById('node-layer');
    if(!graph||!edgeLayer||!nodeLayer)return;
    const rect = graph.getBoundingClientRect(); const w = rect.width, h = rect.height,edgeFragment=document.createDocumentFragment(),nodeFragment=document.createDocumentFragment();
    const pointFor = (id:string):[number,number] => {
        const fixed=positions[id];if(fixed)return fixed;
        const index=Math.max(0,targetNetwork.compounds.findIndex(row=>row.id===id));
        if(targetNetwork.compounds.length===2)return index===0?[.26,.52]:[.74,.52];
        return [.5+.34*Math.cos(index/targetNetwork.compounds.length*Math.PI*2),.5+.34*Math.sin(index/targetNetwork.compounds.length*Math.PI*2)];
    };
    targetNetwork.edges.forEach(edge => {
        const a = pointFor(edge.left_id), b = pointFor(edge.right_id); const gate = scoreBand(edge); const selected = !targetFocusOnly&&edge.edge_id === targetEdgeId;
        const visible = document.createElementNS('http://www.w3.org/2000/svg', 'line'); visible.setAttribute('x1', String(a[0] * w)); visible.setAttribute('y1', String(a[1] * h)); visible.setAttribute('x2', String(b[0] * w)); visible.setAttribute('y2', String(b[1] * h)); visible.setAttribute('class', `network-edge ${gate}${selected ? ' selected' : ''}`); edgeFragment.appendChild(visible);
        const hit = visible.cloneNode() as SVGLineElement; hit.setAttribute('class', 'edge-hit'); hit.setAttribute('tabindex','0'); hit.setAttribute('role','button'); hit.setAttribute('aria-label',`${edge.left_id} to ${edge.right_id}; score ${value(edge.mapping_score)}; ${bandLabel(edge)} score band`); hit.addEventListener('click', () => void selectEdge(edge.edge_id)); hit.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); void selectEdge(edge.edge_id); } }); edgeFragment.appendChild(hit);
    });
    await Promise.all(targetNetwork.compounds.map(async row => {
        const [x,y] = pointFor(row.id); const node = document.createElement('button'); node.className = `network-node${targetFocusId===row.id?' focused':''}`; node.dataset.compound = row.id; node.style.left = `${x * 100}%`; node.style.top = `${y * 100}%`; const label=document.createElement('span');label.textContent=row.id;const depiction=document.createElement('div');depiction.innerHTML=await moleculeSvg(row.canonical_smiles);node.append(label,depiction); node.addEventListener('click', () => {if(network!==targetNetwork)return; const incident = targetNetwork.edges.filter(e => e.left_id === row.id || e.right_id === row.id).sort((a,b)=>b.mapping_score-a.mapping_score); focusedCompoundId=row.id; if (!incident[0]) return; const tied=incident.filter(edge=>Math.abs(edge.mapping_score-incident[0].mapping_score)<1e-6); if(tied.length>1){compoundFocusOnly=true;text('status',`${row.id} · ${tied.length} TIED HIGH-SCORE INCIDENT EDGES · CHOOSE FROM LEFT QUEUE · PROJECT PRIORITY UNRANKED`);text('queue-meta',`${tied.length} TIED · ${row.id} FOCUS`);showCompoundFocus(row,tied);renderQueue();drawRisk();void renderNetwork();return;} compoundFocusOnly=false;text('status',`${row.id} · UNIQUE HIGHEST-SCORE INCIDENT EDGE ${incident[0].left_id}→${incident[0].right_id} · PROJECT PRIORITY UNRANKED`); void selectEdge(incident[0].edge_id); }); nodeFragment.appendChild(node);
    }));
    if(!operations.current(scope)||network!==targetNetwork||selectedEdge.edge_id!==targetEdgeId||focusedCompoundId!==targetFocusId||compoundFocusOnly!==targetFocusOnly)return;
    edgeLayer.setAttribute('viewBox', `0 0 ${w} ${h}`);edgeLayer.replaceChildren(edgeFragment);nodeLayer.replaceChildren(nodeFragment);
}

function renderQueue(): void {
    const queue = document.getElementById('edge-queue')!;
    const visible = compoundFocusOnly && focusedCompoundId
        ? network.edges.filter(edge => edge.left_id === focusedCompoundId || edge.right_id === focusedCompoundId)
        : network.edges;
    const priorityRows=((document.getElementById('compound-priorities') as HTMLTextAreaElement|null)?.value||'').split(/\r?\n/).map(row=>row.split('|').map(value=>value.trim())).filter(row=>row[0]);const priorityFor=(id:string)=>priorityRows.find(row=>row[0]===id)?.[1]?.toUpperCase()||'UNRANKED';
    const edgePriority=(edge:Edge)=>{const ranks=[priorityFor(edge.left_id),priorityFor(edge.right_id)];return ranks.includes('HIGH')?'HIGH':ranks.includes('MEDIUM')?'MEDIUM':ranks.every(rank=>rank==='LOW')?'LOW':'UNRANKED';};
    const ordered = [...visible].sort((a,b) => b.mapping_score-a.mapping_score); queue.innerHTML = ordered.map(edge => {
        const selected=!compoundFocusOnly&&edge.edge_id===selectedEdge.edge_id,gate = scoreBand(edge),priority=edgePriority(edge); return `<button data-edge="${escapeHtml(edge.edge_id)}" aria-current="${selected?'true':'false'}" aria-label="${escapeHtml(edge.left_id)} to ${escapeHtml(edge.right_id)}; mapping support ${bandLabel(edge)}; execution eligibility requires posed-system qualification; portfolio priority ${priority}" class="queue-row ${gate}${selected ? ' selected' : ''}"><span>${escapeHtml(edge.left_id)} → ${escapeHtml(edge.right_id)}</span><b>${value(edge.mapping_score,2)}</b><em>${edge.mapped_heavy_atom_count ?? '—'}</em><i>${bandLabel(edge)}</i><strong class="priority-${priority.toLowerCase()}">${priority}</strong></button>`;
    }).join('');
    queue.querySelectorAll<HTMLButtonElement>('[data-edge]').forEach(button => button.addEventListener('click', () => void selectEdge(button.dataset.edge!)));
}

async function renderSelected(): Promise<void> {
    const scope=operations.begin('render-selected'),targetNetwork=network,edge = selectedEdge, left = compoundIn(targetNetwork,edge.left_id), right = compoundIn(targetNetwork,edge.right_id), diagnostic = edge.rdkit_fmcs_diagnostic || {}, highlights = await mappingHighlights(edge,targetNetwork), heavyDelta = edge.heavy_mapping_disagreement_jaccard;
    const [leftSvg,rightSvg]=await Promise.all([moleculeSvg(left.depiction_smiles || left.canonical_smiles, 340, 180, highlights.left),moleculeSvg(right.depiction_smiles || right.canonical_smiles, 340, 180, highlights.right)]);
    if(!operations.current(scope)||network!==targetNetwork||selectedEdge!==edge)return;
    text('transform-name', `${edge.left_id} → ${edge.right_id}`); text('left-name', left.id); text('right-name', right.id); text('mapped-atoms', String(edge.mapped_heavy_atom_count ?? '—')); text('mapping-score', value(edge.mapping_score)); text('mapping-disagreement', value(heavyDelta)); text('chemical-change',highlights.summary);
    const ledger=document.getElementById('change-ledger');if(ledger)ledger.innerHTML=highlights.ledger.map(row=>`<span class="${row.state}"><b>${escapeHtml(row.label)}</b>${escapeHtml(row.value)}</span>`).join('');
    const gateLabel=hasGovernedMapping(edge)?'POSED-SYSTEM MAP · RUN-READY':`${bandLabel(edge)} PRELIMINARY · QUALIFY IN SYSTEM`;
    text('edge-gate', gateLabel); text('edge-title', `${edge.left_id} → ${edge.right_id}`); text('edge-id', edge.edge_id); text('inspect-score', value(edge.mapping_score)); text('inspect-atoms', `${edge.mapped_atom_count} / ${edge.mapped_heavy_atom_count ?? '—'}`); text('inspect-tanimoto', value(diagnostic.tanimoto)); text('inspect-jaccard', value(heavyDelta));
    const l = diagnostic.left_heavy_atom_fraction, r = diagnostic.right_heavy_atom_fraction; const li = document.getElementById('left-coverage'); const ri = document.getElementById('right-coverage'); if (li) li.style.setProperty('--p', `${(l ?? 0) * 100}%`); if (ri) ri.style.setProperty('--p', `${(r ?? 0) * 100}%`);
    document.getElementById('left-structure')!.innerHTML = leftSvg; document.getElementById('right-structure')!.innerHTML = rightSvg;
    drawAgreement();drawRisk();renderQueue();await renderNetwork();
}

function physicalRunActive():boolean{const receipt=currentRunReceipt();return!!receipt&&receiptStateIsPhysical(receipt.state)||!!activeRunId&&!receipt||runJobs.some(job=>['pending','queued','running','aggregating'].includes(job.state));}
async function selectEdge(edgeId: string): Promise<void> { const edge = network.edges.find(row => row.edge_id === edgeId); if (!edge) return; const changed=edge.edge_id!==selectedEdge.edge_id;if(changed&&runContextLocked()){text('status','EDGE SELECTION FROZEN WHILE AN OPENFE RUNSET RECEIPT IS ATTACHED');return;}selectedEdge = edge;compoundFocusOnly=false;focusedCompoundId=null;if(changed){invalidatePreparedSystem();autoBindPreparedPoses();text('contract-gate','EDGE CHANGED · MATCHING REGISTERED POSES');}syncExecutionContract();await renderSelected();text('queue-meta',`${network.edges.filter(e=>scoreBand(e)==='ready').length} MAP ≥.80 · ${network.edges.filter(e=>scoreBand(e)==='blocked').length} MAP <.50`);text('status',`${edge.left_id}→${edge.right_id} SELECTED · MAPPING EVIDENCE ONLY · PROJECT PRIORITY UNRANKED`); }

function showCompoundFocus(row:Compound,tied:Edge[]):void{['left-name','right-name','mapped-atoms','mapping-score','mapping-disagreement','chemical-change','inspect-score','inspect-atoms','inspect-tanimoto','inspect-jaccard'].forEach(id=>text(id,'—'));text('transform-name',`${row.id} · CHOOSE 1 OF ${tied.length} EDGES`);text('edge-gate','COMPOUND FOCUS · NO EDGE SELECTED');text('edge-title',row.id);text('edge-id',`${tied.length} tied incident edges`);document.getElementById('left-structure')!.innerHTML='';document.getElementById('right-structure')!.innerHTML='';document.getElementById('change-ledger')!.innerHTML=`<span class="unverified"><b>EDGE REQUIRED</b>Choose one tied incident edge from the sorted queue.</span>`;const canvas=document.getElementById('agreement-canvas') as HTMLCanvasElement;canvas.getContext('2d')?.clearRect(0,0,canvas.width,canvas.height);}

function drawRisk(): void {
    const canvas = document.getElementById('risk-canvas') as HTMLCanvasElement|null;if(!canvas)return; const rect = canvas.getBoundingClientRect(), dpr = Math.min(devicePixelRatio, 2); canvas.width = rect.width * dpr; canvas.height = rect.height * dpr; const ctx = canvas.getContext('2d');if(!ctx)return; ctx.scale(dpr,dpr); ctx.clearRect(0,0,rect.width,rect.height);
    const pad = { l:42, r:16, t:16, b:31 }, w = rect.width-pad.l-pad.r, h=rect.height-pad.t-pad.b; ctx.strokeStyle='#183843'; ctx.lineWidth=1; ctx.font='9px DiracMono'; ctx.fillStyle='#789099';
    for(let i=0;i<=4;i++){ const y=pad.t+h*i/4; ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(pad.l+w,y);ctx.stroke(); ctx.fillText((i/4).toFixed(2),3,y+2); }
    for(let i=0;i<=5;i++){ const x=pad.l+w*i/5;ctx.beginPath();ctx.moveTo(x,pad.t);ctx.lineTo(x,pad.t+h);ctx.stroke(); ctx.fillText((i/5).toFixed(1),x-5,pad.t+h+13); }
    riskPoints=[];network.edges.forEach(edge=>{ const coverage=Math.min(edge.rdkit_fmcs_diagnostic?.left_heavy_atom_fraction??NaN,edge.rdkit_fmcs_diagnostic?.right_heavy_atom_fraction??NaN), delta=edge.heavy_mapping_disagreement_jaccard; if(!Number.isFinite(coverage)||!Number.isFinite(delta))return; const x=pad.l+w*coverage, y=pad.t+h*Number(delta), gate=scoreBand(edge);riskPoints.push({edge,x,y}); ctx.fillStyle=gate==='ready'?'#55e59a':gate==='review'?'#e8b94f':'#ff5d72'; ctx.shadowColor=ctx.fillStyle;ctx.shadowBlur=7;ctx.beginPath();ctx.arc(x,y,!compoundFocusOnly&&edge.edge_id===selectedEdge.edge_id?5:3.2,0,Math.PI*2);ctx.fill();ctx.shadowBlur=0; });
}

function drawAgreement(): void {
    const canvas = document.getElementById('agreement-canvas') as HTMLCanvasElement|null;if(!canvas)return; const rect=canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio,2);canvas.width=rect.width*dpr;canvas.height=rect.height*dpr;const ctx=canvas.getContext('2d')!;ctx.scale(dpr,dpr);ctx.clearRect(0,0,rect.width,rect.height); const proposals=selectedEdge.heavy_atom_mapping_proposals||{}, names=Object.keys(proposals).sort(); ctx.font='10px DiracMono'; ctx.fillStyle='#78939b';
    if(names.length<2){ctx.fillText('HEAVY-ATOM PROPOSALS UNAVAILABLE',10,28);return;} const maxIndex=Math.max(1,...names.flatMap(name=>proposals[name].map(pair=>pair[0]))); const sets=names.map(name=>new Set(proposals[name].map(pair=>pair.join(':'))));
    names.slice(0,2).forEach((name,row)=>{const y=30+row*(rect.height-52);ctx.fillStyle='#78939b';ctx.fillText(name.toUpperCase(),8,y-9);proposals[name].forEach(pair=>{const x=70+(rect.width-86)*pair[0]/maxIndex, common=sets.every(set=>set.has(pair.join(':')));ctx.fillStyle=common?'#62e6fa':'#dfaa4e';ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fill();ctx.fillStyle='#9bb0b6';ctx.fillText(`${pair[0]}→${pair[1]}`,x-7,y+14);});});ctx.fillStyle='#78939b';ctx.fillText('INDEX-EXACT · NOT AUTOMORPHISM-NORMALIZED',8,rect.height-3);
}

function updateSummary(jobId = currentNetworkJobId(), artifact = '8eeddd90c924…'): void {
    const high = network.edges.filter(e=>scoreBand(e)==='ready').length, low=network.edges.filter(e=>scoreBand(e)==='blocked').length, qualified=network.edges.filter(e=>scoreBand(e)!=='blocked'), adjacency=new Map(network.compounds.map(row=>[row.id,new Set<string>()])); qualified.forEach(edge=>{adjacency.get(edge.left_id)?.add(edge.right_id);adjacency.get(edge.right_id)?.add(edge.left_id);});
    const unseen=new Set(network.compounds.map(row=>row.id)),components:string[][]=[];while(unseen.size){const seed=unseen.values().next().value as string,stack=[seed],component:string[]=[];unseen.delete(seed);while(stack.length){const item=stack.pop()!;component.push(item);adjacency.get(item)?.forEach(next=>{if(unseen.delete(next))stack.push(next);});}components.push(component.sort());} const isolated=components.filter(group=>group.length===1).flat(); const topologyLabel=components.length>1?`${components.length} COMPONENTS · ISOLATED ${isolated.join(',')||'NONE'}`:'CONNECTED';
    text('node-count', String(network.compounds.length)); text('edge-count', String(network.edges.length)); text('ready-count', String(high)); text('blocked-count', String(low)); text('durable-job', jobId); text('queue-meta', `${high} MAP ≥.80 · ${low} MAP <.50${focusedCompoundId?` · ${focusedCompoundId} FOCUS`:''}`); text('atlas-meta',`${network.edges.length} EDGES · HOVER / CLICK`); text('network-meta',`${network.mode.toUpperCase()} · ${network.policy.planner.toUpperCase()} · MAP SCORE ≠ EXECUTION READINESS`); text('topology-state',`SCORE-FILTERED GRAPH · ${topologyLabel}`); const topology=document.getElementById('topology-state'); topology?.classList.toggle('fatal',components.length>1); text('footer-job', `JOB ${jobId}`); text('footer-artifact', `ARTIFACT ${artifact}`);
}

function networkFromJob(env: Envelope): Network | null { const job = env.data as Record<string, any>; return job?.result_summary?.data?.network || job?.network || null; }

async function loadServerCampaignById(campaignId:string):Promise<CampaignDraftV2>{
    const response=await client.execute('physics.rbfe-campaign.get',{campaign_id:campaignId});
    if(!response.ok)throw new Error(`campaign ${campaignId} could not be restored from the server · ${envelopeFailure(response)}`);
    const draft=draftFromCampaignEnvelope(response.data||{});
    if(draft.campaign_id!==campaignId)throw new Error(`campaign restore identity mismatch · requested ${campaignId}, received ${draft.campaign_id}`);
    return draft;
}

function benchmarkEdge(target:Network=network): Edge {
    return target.edges.find(edge => {
        const smiles = new Set([compoundIn(target,edge.left_id).canonical_smiles,
            compoundIn(target,edge.right_id).canonical_smiles]);
        return smiles.has('Fc1ccccc1') && smiles.has('Clc1ccccc1');
    }) || target.edges[0];
}

async function loadDurableNetwork(requestedNetworkJobId=currentNetworkJobId(),ownerScope:OperationScope=operations.begin('network-load'),preserveRun=false): Promise<boolean> {
    let scope=ownerScope;const metadataHint=persistedCampaignContextHint(),current=()=>operations.current(scope,{edits:true});
    try {
        const env=await client.jobGet(requestedNetworkJobId),loaded=env.ok?networkFromJob(env):null;if(!loaded)throw new Error(env.error?.message||'network job unavailable');
        if(!current())return false;
        const provisional=campaignContextFromNetwork(loaded,requestedNetworkJobId,metadataHint),job=env.data as Record<string,any>,artifact=job.artifacts?.find((a:Record<string,any>)=>a.role==='rbfe.network'),durableRef=artifactRef(artifact);
        if(requestedNetworkJobId!==DefaultNetworkJobId){
            if(!provisional?.campaign_id)throw new Error('user campaign network has no immutable campaign context');
            if(!durableRef)throw new Error('user campaign network has no content-addressed rbfe.network artifact');
            const serverDraft=await loadServerCampaignById(provisional.campaign_id);if(!current())return false;
            if(serverDraft.campaign_scientific_generation!==provisional.campaign_scientific_generation||serverDraft.campaign_scientific_digest!==provisional.campaign_scientific_digest)throw new Error('network belongs to a stale campaign scientific generation');
            const adopted=await restoreDraft(serverDraft,false,scope,preserveRun);if(!adopted)return false;scope=adopted;if(!current())return false;
            writeCampaignCache(serverDraft,'server-synced');
        }
        const context=campaignContextFromNetwork(loaded,requestedNetworkJobId,metadataHint);if(requestedNetworkJobId!==DefaultNetworkJobId&&!context)throw new Error('user campaign network has no immutable campaign context');
        await enrichMappingEvidence(loaded);if(!current())return false;
        bindAuthoritativeCampaignContext(loaded,requestedNetworkJobId,context);network=loaded;networkWorkspaceVisible=true;networkArtifactRef=durableRef;selectedEdge=benchmarkEdge(loaded);sourceState='durable-job';updateSummary(requestedNetworkJobId,artifact?.sha256?String(artifact.sha256):'rbfe.network');applyCampaignContext(context);if(context)await loadSystemCatalog();if(!current())return false;text('status',`${requestedNetworkJobId===DefaultNetworkJobId?'DURABLE T4L BENCHMARK':'DURABLE USER CAMPAIGN'} · ${job.seconds?.toFixed?.(3)||'—'} s · EXECUTOR LINKED`);text('engine-state','● API LINKED');
    } catch(error) {
        if(!current())return false;
        if(requestedNetworkJobId!==DefaultNetworkJobId){detachExecutionContext(scope,{preserveRun});text('status',`CAMPAIGN NETWORK DETACHED · ${error instanceof Error?error.message:String(error)}`);text('engine-state','● CONTEXT REFUSED');return false;}
        const fallback=structuredClone(FallbackNetwork);await enrichMappingEvidence(fallback);if(!current())return false;authoritativeCampaignContextState=null;sourceState='cached-snapshot';network=fallback;networkWorkspaceVisible=true;networkArtifactRef=null;selectedEdge=benchmarkEdge(fallback);updateSummary();text('status','CACHED T4L SNAPSHOT · EXECUTION DISABLED');text('engine-state','● SNAPSHOT');
    }
    autoBindPreparedPoses(); renderQueue(); drawRisk(); await renderSelected(); syncExecutionContract();
    return true;
}

async function replanNetwork(): Promise<void> {
    if(runContextLocked()){text('status',`REPLAN REFUSED · ATTACHED RUNSET ${activeRunId||'RECEIPT PENDING'} MUST REACH A TERMINAL STATE`);return;}
    const pendingPlanner=readPlannerReceipt();if(pendingPlanner){text('status',`REPLAN REFUSED · DURABLE PLANNER RECEIPT ${pendingPlanner.job_id||pendingPlanner.owner_token} MUST BE RECONCILED OR ARCHIVED`);return;}
    const button=document.getElementById('replan') as HTMLButtonElement,scope=operations.begin('planner'),priorContext=activeCampaignContext(),draftSnapshot=draftIdentitySnapshot(),priorSystem=executionContract.system,compounds=(priorContext?network.compounds:DemoCompounds).map(({id,canonical_smiles})=>({id,smiles:canonical_smiles})),input:Record<string,unknown>={compounds,extra_edge_fraction:1,minimum_similarity:.15};
    const current=()=>operations.current(scope,{edits:true})&&draftIdentityMatches(draftSnapshot)&&sameCampaignScientificContext(activeCampaignContext(),priorContext);let receipt:PlannerReceipt|null=null;
    button.disabled=true;invalidatePreparedSystem();clearCurrentRunView('replan_replaced_terminal_run_view');syncExecutionContract();text('status','SUBMITTING OPENFE NETWORK PLAN · PRIOR QUALIFICATION CLEARED');
    try {
        if(priorContext){
            if(!priorContext.campaign_id||!priorContext.campaign_scientific_generation||!priorContext.campaign_scientific_digest||!priorContext.prepared_system_id)throw new Error('campaign replan requires an immutable scientific generation');
            const campaignRef=campaignScientificRefFrom(priorContext.campaign_id,priorContext.campaign_scientific_generation,priorContext.campaign_scientific_digest),systemRef=contentRef(priorSystem?.prepared_receptor_state_ref,'prepared_receptor_state');if(!campaignRef||!systemRef||systemRef.id!==priorContext.prepared_system_id)throw new Error('campaign replan requires the exact prepared-system digest');
            Object.assign(input,{campaign_id:priorContext.campaign_id,campaign_scientific_generation:priorContext.campaign_scientific_generation,campaign_scientific_digest:priorContext.campaign_scientific_digest,prepared_system_id:priorContext.prepared_system_id});receipt={schema_version:2,owner_token:scope.id,created_at:new Date().toISOString(),status:'submitting',job_id:null,campaign_scientific_ref:campaignRef,prepared_system_ref:systemRef,input_signature:canonicalJson(input)};writePlannerReceipt(receipt);
        }
        const accepted=await client.execute('physics.rbfe-network',input);if(!accepted.ok)throw new Error(accepted.error?.message);const id=String(accepted.meta?.job_id||'');if(!fullJobId(id))throw new Error('planner returned no complete durable Job ID');
        if(receipt){const owned=plannerReceiptOwned(receipt.owner_token);if(owned){receipt={...owned,job_id:id,status:current()?'waiting':'detached'};writePlannerReceipt(receipt);}}
        if(!current())return;text('status',`${priorContext?'CAMPAIGN':'T4L'} PLAN JOB ${id} QUEUED`);const done=await client.waitForCommandResult(accepted,180);if(!current())return;if(!done.ok)throw new Error(done.error?.message);const loaded=done.data?.network as Network|undefined;if(!loaded)throw new Error('planner returned no network');const durableRef=artifactRef(done.artifacts?.find(item=>item.role==='rbfe.network') as Record<string,any>);if(!durableRef)throw new Error('planner returned no content-addressed rbfe.network artifact');const context=campaignContextFromNetwork(loaded,id,priorContext?{...priorContext,network_job_id:id}:null);if(priorContext&&!sameCampaignScientificContext(context,priorContext))throw new Error('replanned campaign network changed its immutable campaign/system context');if(!priorContext&&context)throw new Error('benchmark replan unexpectedly returned a user campaign binding');await enrichMappingEvidence(loaded);if(!current())return;
        bindAuthoritativeCampaignContext(loaded,id,context);network=loaded;networkWorkspaceVisible=true;networkArtifactRef=durableRef;executionContract.system=priorSystem;selectedEdge=benchmarkEdge(loaded);focusedCompoundId=null;sourceState='replanned-job';updateSummary(id,durableRef.sha256);applyCampaignContext(context);if(context&&receipt)persistPlannerOutput(id,durableRef,receipt.campaign_scientific_ref,receipt.prepared_system_ref);autoBindPreparedPoses();if(receipt)removePlannerReceiptIf(receipt.owner_token,id);text('status',`REPLANNED ${context?'USER CAMPAIGN':'T4L BENCHMARK'} · ${network.edges.length} MAPPING EDGES · 0 RESULTS · SYSTEM REQUALIFICATION REQUIRED`);renderQueue();drawRisk();await renderSelected();syncExecutionContract();
    }
    catch(error){if(receipt&&plannerReceiptOwned(receipt.owner_token)){const owned=plannerReceiptOwned(receipt.owner_token)!;writePlannerReceipt({...owned,status:owned.job_id?'detached':'submitting'});}if(current())text('status',error instanceof Error?error.message:String(error));}
    finally{if(current()){button.disabled=false;button.textContent=priorContext?'REPLAN NETWORK':'REPLAN BENCHMARK';}}
}

let systemCatalog: PreparedSystemOption[] = [];
let builderSystemCatalog: PreparedSystemOption[] = [];
let contractFailure: { gate: string; detail: string } | null = null;

function envelopeFailure(env: Envelope):string{
    const tail=String((env.error?.details as Record<string,unknown>|undefined)?.stderr_tail||'');
    const diagnostic=tail.split(/\r?\n/).reverse().find(line=>line.includes('ValueError:'))?.replace(/^.*ValueError:\s*/, '').trim();
    return diagnostic||env.error?.message||'server preparation failed';
}

function preparationFailureCopy(message: string): { gate: string; detail: string } {
    if (message.includes('artifact digest') || message.includes('content-address')) {
        return {
            gate: 'REGISTERED ARTIFACT INTEGRITY FAILURE · RUN LOCKED',
            detail: `${message}. The browser cannot override this server-side refusal.`,
        };
    }
    if (message.includes('coordinate frame') || message.includes('clashes')) {
        return {
            gate: 'POSE / RECEPTOR GEOMETRY REFUSED · RUN LOCKED',
            detail: `${message}. Select another registered prepared pose set; no Transformation was accepted.`,
        };
    }
    if (message.includes('endpoint poses do not match') || message.includes('posed SDF endpoints do not match')) {
        return {
            gate: 'POSES DO NOT MATCH SELECTED EDGE · RUN LOCKED',
            detail: `${selectedEdge.left_id} → ${selectedEdge.right_id} has no matching registered aligned pose pair. Choose another prepared system or edge.`,
        };
    }
    if (message.includes('formal charges differ')) {
        return {
            gate: 'CHARGE-CHANGING EDGE NOT ENABLED · RUN LOCKED',
            detail: `${message} Choose a charge-conserving edge or a governed charge-changing protocol. Nothing was accepted.`,
        };
    }
    return {
        gate: 'SYSTEM BUILD REFUSED · RUN LOCKED',
        detail: `${message}. No Transformation or execution spec was accepted.`,
    };
}

function invalidatePreparedSystem(): void {
    executionBuildOperationId+=1;
    operations.invalidate('execution-build');operations.invalidate('render-selected');
    aggregateArm=null;
    executionContract.validated=false;edgeNetworkRef=null;edgeSpecRef=null;complexTransformationRef=null;solventTransformationRef=null;preflightData=null;contractFailure=null;
    if(!runJobs.length)renderRunJobs();
}

function receiptStateIsPhysical(state:RunReceiptState):boolean{return['creating','pending','running','aggregating','cancel_requested'].includes(state);}
function currentRunReceipt():RunReceipt|null{const durable=readRunReceipt();if(durable)activeRunReceipt=durable;return activeRunReceipt;}
function runContextLocked():boolean{const receipt=currentRunReceipt();return!!receipt&&(['creating','pending','running','aggregating','cancel_requested','blocked'] as RunReceiptState[]).includes(receipt.state)||!!activeRunId&&!receipt;}
function clearCurrentRunView(reason='execution_context_detached_without_cancellation'):boolean{
    const receipt=currentRunReceipt();
    if(receipt&&receiptStateIsPhysical(receipt.state)){text('status',`CLEAR REFUSED · RUNSET ${receipt.run_id||'CREATION RECEIPT'} IS ${receipt.state.toUpperCase()}`);return false;}
    operations.invalidate('run-watch');operations.invalidate('run-start');
    if(receipt?.run_id)archiveDetachedRun(receipt,reason);else if(activeRunId)archiveDetachedRun(activeRunId,reason);
    if(receipt)removeRunReceiptIf(receipt.owner_token,receipt.run_id);else if(activeRunId&&copyStorage.get('dirac.rbfe.active_run_id')===activeRunId)copyStorage.remove('dirac.rbfe.active_run_id');
    activeRunReceipt=null;activeRunId=null;runJobs=[];aggregateArm=null;renderRunJobs();
    const cancel=document.getElementById('cancel-run') as HTMLButtonElement|null,retry=document.getElementById('retry-run') as HTMLButtonElement|null;if(cancel)cancel.disabled=true;if(retry)retry.disabled=true;
    return true;
}

function detachExecutionContext(owner?:OperationScope,{preserveRun=false}:{preserveRun?:boolean}={}):OperationScope|true|null{
    if(!preserveRun&&!clearCurrentRunView())return null;
    const adopted=advanceDraftEpoch(owner);
    plannerWaitController?.abort();plannerDetachWait?.();plannerInFlight=false;
    preparationOperationId+=1;preparationWaitController?.abort();preparationDetachWait?.();preparationInFlight=false;
    authoritativeCampaignContextState=null;
    copyStorage.remove('dirac.rbfe.active_campaign_context');copyStorage.remove('dirac.rbfe.active_network_job_id');
    if(preserveRun){runJobs=[];aggregateArm=null;renderRunJobs();}
    networkArtifactRef=null;preparedCampaignSystem=null;systemCatalog=[];builderSystemCatalog=[];
    executionContract.system=null;executionContract.parentPose=null;executionContract.proposalPose=null;invalidatePreparedSystem();
    network=FallbackNetwork;selectedEdge=FallbackEdges[0];focusedCompoundId=null;compoundFocusOnly=false;sourceState='cached-snapshot';
    renderRunJobs();showBlankCampaignShell();text('execution-meta','DETACHED · DURABLE SERVER JOBS WERE NOT CANCELLED');text('result-count','0 · CURRENT CAMPAIGN NONE');text('run-boundary','PLAN ONLY · CURRENT DRAFT HAS NO EXECUTION CONTEXT');syncExecutionContract();
    return adopted||true;
}

function autoBindPreparedPoses(): void {
    const system=executionContract.system;
    if(!system){executionContract.parentPose=null;executionContract.proposalPose=null;text('receptor-state','SERVER ARTIFACT');text('parent-pose-state','AUTO-MATCH EDGE');text('proposal-pose-state','AUTO-MATCH EDGE');return;}
    const left=compound(selectedEdge.left_id).canonical_smiles,right=compound(selectedEdge.right_id).canonical_smiles;
    const candidates=(smiles:string)=>system.poses.filter(pose=>pose.canonical_smiles===smiles&&pose.review_state==='accepted'&&!!contentRef(pose.pose_ref,'pose_hypothesis'));
    const parentCandidates=candidates(left),proposalCandidates=candidates(right);
    executionContract.parentPose=parentCandidates.length===1?parentCandidates[0]:null;
    executionContract.proposalPose=proposalCandidates.length===1?proposalCandidates[0]:null;
    text('receptor-state',`${system.pdb_id?`PDB ${system.pdb_id}`:'UPLOADED STRUCTURE'} · DIGEST ✓`);
    text('parent-pose-state',executionContract.parentPose?`${executionContract.parentPose.label} · EXACT ACCEPTED ✓`:parentCandidates.length>1?`${parentCandidates.length} AMBIGUOUS ACCEPTED POSES`:'NO UNIQUE ACCEPTED POSE');
    text('proposal-pose-state',executionContract.proposalPose?`${executionContract.proposalPose.label} · EXACT ACCEPTED ✓`:proposalCandidates.length>1?`${proposalCandidates.length} AMBIGUOUS ACCEPTED POSES`:'NO UNIQUE ACCEPTED POSE');
}

function draftMatchesCampaignContext(context:CampaignContext|null):boolean{return!!context?.campaign_id&&context.campaign_id===draftCampaignId&&context.campaign_scientific_generation===draftCampaignScientificGeneration&&context.campaign_scientific_digest===draftCampaignScientificDigest;}
function systemMatchesCampaignContext(system:PreparedSystemOption|null,context:CampaignContext|null):boolean{const plan=readPlannerOutputReceipt();if(!system||!context||!plannerOutputReceiptMatches(plan,context)||!sameExactRef(system.prepared_receptor_state_ref,plan?.prepared_system_ref))return false;const generation=system.campaign_scientific_generation,digest=system.campaign_scientific_digest;return(generation===undefined||generation===context.campaign_scientific_generation)&&(digest===undefined||digest===context.campaign_scientific_digest);}
function syncExecutionContract(): void {
    const campaignBinding=activeCampaignContext();
    const contextCurrent=draftMatchesCampaignContext(campaignBinding),systemCurrent=systemMatchesCampaignContext(executionContract.system,campaignBinding);
    const policyGate=preparationPolicyGate(executionContract.system),ready=contextCurrent&&systemCurrent&&Number.isInteger(campaignBinding?.campaign_scientific_generation)&&/^sha256:[0-9a-f]{64}$/.test(campaignBinding?.campaign_scientific_digest||'')&&policyGate.ok&&!!executionContract.parentPose&&!!executionContract.proposalPose&&!!networkArtifactRef;
    const locked=runContextLocked(),creationRetry=activeRunReceipt?.state==='creating'&&!activeRunReceipt.run_id&&currentExecutionMatchesReceipt(activeRunReceipt),confirmationReady=renderOperationConfirmation(),validate=document.getElementById('validate-contract') as HTMLButtonElement|null;if(validate){validate.disabled=locked||!ready||executionContract.validated||runJobs.some(job=>job.state==='queued'||job.state==='running');validate.textContent=executionContract.validated?'SYSTEM QUALIFIED':scoreBand(selectedEdge)==='ready'?'BUILD + VALIDATE 2 LEGS':'QUALIFY IN POSED SYSTEM';}
    const start=document.getElementById('prepare-edge') as HTMLButtonElement|null;if(start){start.disabled=auditCopyId!=='main'||!confirmationReady||(locked&&!creationRetry)||(!creationRetry&&(!executionContract.validated||!hasGovernedMapping(selectedEdge)))||runJobs.some(job=>job.state==='queued'||job.state==='running');if(auditCopyId!=='main')start.textContent='AUDIT COPY · PHYSICAL START DISABLED';else if(!start.disabled&&start.textContent?.startsWith('PREPARE'))start.textContent=creationRetry?'RETRY EXACT RUN RECEIPT':'START 6 JOBS';}
    document.querySelectorAll<HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement>('#campaign-builder [data-invalidate],#campaign-pdb,#campaign-ligands').forEach(element=>element.disabled=locked);document.querySelectorAll<HTMLButtonElement>('#campaign-builder [data-choice-group] button').forEach(element=>element.disabled=locked);const clear=document.getElementById('clear-campaign') as HTMLButtonElement|null;if(clear)clear.disabled=physicalRunActive()||!!readPlannerReceipt();
    const setState=(id:string,isReady:boolean,label:string)=>{const element=document.getElementById(id);if(!element)return;element.textContent=isReady?label:'NOT SUPPLIED';element.classList.toggle('missing',!isReady);element.classList.toggle('bound',isReady);};
    const system=executionContract.system;setState('target-state',!!system,system?.target_name||'');setState('pose-state',!!system,system?`PDB ${system.pdb_id} · PREPARED + ALIGNED`:'');setState('protocol-state',ready,'OPENFE RFE STANDARD · 11 λ');
    if(!executionContract.validated){const missing=[!contextCurrent?'network / open-draft scientific generation match':null,!system?'prepared system':null,system&&!systemCurrent?'prepared system / network identity match':null,system&&!policyGate.ok?`${policyGate.blockers.length} preparation policy axis${policyGate.blockers.length===1?'':'es'}`:null,!executionContract.parentPose?`${selectedEdge.left_id} registered pose`:null,!executionContract.proposalPose?`${selectedEdge.right_id} registered pose`:null,!networkArtifactRef?'durable network':null].filter(Boolean);const preliminary=scoreBand(selectedEdge)==='ready'?'PRELIMINARY MAP HIGH':'PRELIMINARY MAP INCONCLUSIVE';text('contract-gate',contractFailure?.gate||(missing.length?`MISSING · ${missing.join(' · ')}`:`${preliminary} · POSED-SYSTEM QUALIFICATION REQUIRED`));if(contractFailure)text('contract-detail',contractFailure.detail);else if(system&&!policyGate.ok)text('contract-detail',`Fail-closed: ${policyGate.blockers.map(row=>`${row.axis}=${row.verdict}`).join(' · ')}. Human pose review cannot override unresolved receptor preparation.`);else if(ready)text('contract-detail',`${system!.label} · both endpoint poses matched in one receptor frame · qualify the exact coordinates before any run can unlock`);}
}

async function loadSystemCatalog():Promise<void>{
    const select=document.getElementById('system-select') as HTMLSelectElement,builderSelect=document.getElementById('builder-system-select') as HTMLSelectElement|null;
    if(draftExpectedVersion<1){select.innerHTML='<option value="">SAVE / RESUME A SERVER CAMPAIGN FIRST</option>';if(builderSelect)builderSelect.innerHTML='<option value="">SAVE / RESUME SERVER CAMPAIGN TO BROWSE</option>';text('system-status','NO SERVER CAMPAIGN BOUND');syncExecutionContract();return;}
    const snapshot=draftIdentitySnapshot();select.disabled=true;if(builderSelect)builderSelect.disabled=true;
    try{const response=await client.execute('physics.rbfe-system.list',{campaign_id:snapshot.campaign_id,include_importable:true});if(!draftIdentityMatches(snapshot,{system:false}))return;if(!response.ok)throw new Error(envelopeFailure(response));builderSystemCatalog=(response.data?.systems||[]) as PreparedSystemOption[];systemCatalog=builderSystemCatalog.filter(system=>system.execution_eligible===true&&(system.campaign_scope==='owned'||system.campaign_scope==='imported'));const reviewLabel=(system:PreparedSystemOption)=>system.poses.length>0&&system.poses.every(pose=>pose.review_state==='accepted')?'REVIEWED':'UNREVIEWED';select.disabled=false;if(builderSelect)builderSelect.disabled=false;select.innerHTML='<option value="">SELECT PREPARED SYSTEM…</option>'+systemCatalog.map((system,index)=>`<option value="${index}">[${reviewLabel(system)}] ${escapeHtml(system.label)} · ${system.poses.length} POSES · ${escapeHtml(system.prepared_receptor_state_ref.id)}</option>`).join('');if(builderSelect)builderSelect.innerHTML='<option value="">NO REGISTERED SYSTEM SELECTED</option>'+builderSystemCatalog.map((system,index)=>`<option value="${index}">[${escapeHtml(system.campaign_scope?.replace(/_/g,' ').toUpperCase()||'SCOPE UNKNOWN')}] ${escapeHtml(system.label)} · ${escapeHtml(system.prepared_receptor_state_ref.id)}</option>`).join('');const reviewed=systemCatalog.filter(system=>reviewLabel(system)==='REVIEWED').length,foreign=builderSystemCatalog.filter(system=>system.import_required||system.campaign_scope==='import_stale').length;text('system-status',systemCatalog.length?`${reviewed} REVIEWED · ${systemCatalog.length} OWNED/IMPORTED · ${foreign} FOREIGN OR STALE`:`NO EXECUTION-ELIGIBLE SYSTEMS · ${foreign} FOREIGN OR STALE`);const context=activeCampaignContext(),matched=context?systemCatalog.findIndex(system=>systemMatchesCampaignContext(system,context)):-1;if(matched>=0){select.value=String(matched);chooseSystem(String(matched));}}
    catch(error){if(!draftIdentityMatches(snapshot,{system:false}))return;select.innerHTML='<option value="">SYSTEM CATALOG UNAVAILABLE</option>';select.disabled=true;if(builderSelect){builderSelect.innerHTML='<option value="">SYSTEM CATALOG UNAVAILABLE</option>';builderSelect.disabled=true;}text('system-status',error instanceof Error?error.message:String(error));}
    syncExecutionContract();
}

function chooseSystem(index:string):void{
    if(runContextLocked()){text('system-status',`SYSTEM CHANGE REFUSED · ATTACHED RUNSET ${activeRunId||'PENDING'}`);syncExecutionContract();return;}
    invalidatePreparedSystem();const candidate=index===''?null:systemCatalog[Number(index)]||null;executionContract.system=candidate&&systemMatchesCampaignContext(candidate,activeCampaignContext())?candidate:null;autoBindPreparedPoses();const system=executionContract.system,policy=preparationPolicyGate(system);text('system-status',candidate&&!system?'SYSTEM REFUSED · CAMPAIGN / NETWORK IDENTITY MISMATCH':system?`${system.pdb_id?`PDB ${system.pdb_id}`:'UPLOADED'} · ${(system.experimental_method||'MODEL').toUpperCase()}${system.resolution_angstrom?` · ${system.resolution_angstrom.toFixed(2)} Å`:''} · ${system.poses.length} POSES${policy.ok?'':` · ${policy.blockers.length} POLICY BLOCKERS`}`:'NO SYSTEM SELECTED');text('contract-detail',system&&!policy.ok?`Preparation policy gate blocked · ${policy.blockers.map(row=>`${row.axis}=${row.verdict}`).join(' · ')}`:system?system.claim_boundary:candidate?'The selected system does not match the exact campaign scientific generation and prepared-system ID bound into this network.':'Choose a server-attested prepared receptor and pose set.');syncExecutionContract();
}

async function validateExecutionContract():Promise<void>{
    if(runContextLocked()){text('contract-gate',`SYSTEM BUILD REFUSED · ATTACHED RUNSET ${activeRunId||'PENDING'}`);return;}
    syncExecutionContract();const binding=activeCampaignContext();const errors:string[]=[];if(!binding?.campaign_id||!binding.campaign_scientific_generation||!binding.campaign_scientific_digest)errors.push('campaign scientific generation');if(!executionContract.system)errors.push('prepared system');const policy=preparationPolicyGate(executionContract.system);if(executionContract.system&&!policy.ok)errors.push(`${policy.blockers.length} unresolved preparation policy axes`);if(!executionContract.parentPose)errors.push('registered parent pose');if(!executionContract.proposalPose)errors.push('registered proposal pose');if(!networkArtifactRef)errors.push('durable RBFE network');if(errors.length){text('contract-gate',`MISSING · ${errors.join(' · ')}`);return;}
    const operationId=++executionBuildOperationId,scope=operations.begin('execution-build'),draftSnapshot=draftIdentitySnapshot(),edgeId=selectedEdge.edge_id,systemRef={...executionContract.system!.prepared_receptor_state_ref},parentPoseRef={...executionContract.parentPose!.pose_ref},proposalPoseRef={...executionContract.proposalPose!.pose_ref},systemId=systemRef.id,networkRef={...networkArtifactRef!},bindingSnapshot={...binding!};
    const current=()=>operations.current(scope,{edits:true})&&operationId===executionBuildOperationId&&draftIdentityMatches(draftSnapshot)&&selectedEdge.edge_id===edgeId&&sameExactRef(executionContract.system?.prepared_receptor_state_ref,systemRef)&&sameExactRef(executionContract.parentPose?.pose_ref,parentPoseRef)&&sameExactRef(executionContract.proposalPose?.pose_ref,proposalPoseRef)&&sameExactRef(networkArtifactRef,networkRef)&&draftMatchesCampaignContext(bindingSnapshot);
    const button=document.getElementById('validate-contract') as HTMLButtonElement;button.disabled=true;text('contract-gate','OPENFE · BUILDING COMPLEX + SOLVENT LEGS');text('contract-detail','Parsing coordinates · checking endpoint charge · LoMap mapping · GUFE round-trip · freezing 6-job spec');text('status',`PREPARING ${selectedEdge.left_id}→${selectedEdge.right_id} ON SERVER`);
    try{
        const accepted=await client.execute('physics.rbfe-system.prepare',{campaign_id:bindingSnapshot.campaign_id,campaign_scientific_generation:bindingSnapshot.campaign_scientific_generation,campaign_scientific_digest:bindingSnapshot.campaign_scientific_digest,network_ref:networkRef,edge_id:edgeId,prepared_receptor_state_ref:executionContract.system!.prepared_receptor_state_ref,parent_pose_ref:executionContract.parentPose!.pose_ref,proposal_pose_ref:executionContract.proposalPose!.pose_ref,protocol_preset:executionContract.protocolPreset});
        if(!current())return;if(!accepted.ok)throw new Error(envelopeFailure(accepted));const done=await client.waitForCommandResult(accepted,300);if(!current())return;if(!done.ok)throw new Error(envelopeFailure(done));const returnedBinding=done.data?.campaign_binding as Record<string,unknown>|undefined;if(returnedBinding?.campaign_id!==bindingSnapshot.campaign_id||returnedBinding?.campaign_scientific_generation!==bindingSnapshot.campaign_scientific_generation||returnedBinding?.campaign_scientific_digest!==bindingSnapshot.campaign_scientific_digest||returnedBinding?.prepared_system_id!==systemId)throw new Error('server build returned a different campaign/system binding');if(done.data?.edge_id!==edgeId)throw new Error('server build returned a different edge');const candidatePreflight=done.data||{},candidateEdgeNetworkRef=artifactRef(done.artifacts?.find(item=>item.role==='rbfe.edge_network') as Record<string,any>),candidateEdgeSpecRef=artifactRef(done.artifacts?.find(item=>item.role==='rbfe.edge_spec') as Record<string,any>),candidateComplexRef=artifactRef(done.artifacts?.find(item=>item.role==='rbfe.openfe.complex_transformation') as Record<string,any>),candidateSolventRef=artifactRef(done.artifacts?.find(item=>item.role==='rbfe.openfe.solvent_transformation') as Record<string,any>);
        if(!candidateEdgeNetworkRef||!candidateEdgeSpecRef||!candidateComplexRef||!candidateSolventRef)throw new Error('server build returned incomplete RBFE execution artifacts');
        const build=candidatePreflight.system_build||{},depiction=build.depiction_contract as DepictionContract|undefined,chemistryEvidence=chemistryEvidenceFrom(build.chemistry_evidence),executionEligibility=executionEligibilityFrom(build.execution_eligibility);
        if(!chemistryEvidence||!executionEligibility)throw new Error('server build returned missing or malformed chemistry evidence / execution eligibility');
        const candidateEdge:Edge={...selectedEdge,preliminary_mapping_score:selectedEdge.preliminary_mapping_score??selectedEdge.mapping_score,mapping_score:Number(build.mapping_score||0),mapped_atom_count:Number(build.mapped_atom_count||0),mapped_heavy_atom_count:Number(build.mapped_heavy_atom_count||0),selected_atom_mapping:build.selected_atom_mapping||[],mapping_source:'openfe_system_builder.reviewed_receptor_frame',mapping_method:String(build.mapping_method||'LomapAtomMapper'),chemistry_evidence:chemistryEvidence,execution_eligibility:executionEligibility,...(depiction?{depiction_contract:depiction}:{})};if(!hasGovernedMapping(candidateEdge))throw new Error(`posed-system mapping / server execution eligibility did not pass the governed gate (score ${candidateEdge.mapping_score.toFixed(3)}, eligibility ${executionEligibility.verdict})`);if(!current())return;
        Object.assign(selectedEdge,candidateEdge);if(depiction){compound(selectedEdge.left_id).depiction_smiles=depiction.parent_smiles;compound(selectedEdge.right_id).depiction_smiles=depiction.proposal_smiles;}preflightData=candidatePreflight;edgeNetworkRef=candidateEdgeNetworkRef;edgeSpecRef=candidateEdgeSpecRef;complexTransformationRef=candidateComplexRef;solventTransformationRef=candidateSolventRef;executionContract.validated=true;text('contract-gate','2 LEGS + POSED MAP SERVER-ATTESTED · READY');text('contract-detail',`LoMap ${selectedEdge.mapping_score.toFixed(3)} · ${selectedEdge.mapped_heavy_atom_count||'—'} heavy / ${selectedEdge.mapped_atom_count||'—'} all atoms · REPEX ${build.protocol?.lambda_windows||11} λ`);text('protocol-state','OPENFE 1.11.1 · STANDARD V1');text('status','SYSTEM QUALIFIED · 6 PHYSICAL JOBS READY');const decisions=document.querySelectorAll<HTMLElement>('.decision-lines>span');if(decisions[0]){decisions[0].innerHTML='<b>ENGINEERING NEXT</b>';decisions[0].append(document.createTextNode(`${selectedEdge.left_id}→${selectedEdge.right_id} · review protocol + 6-job manifest`));}const boundaryCopy=document.querySelector<HTMLElement>('.boundary>div:last-child span');if(boundaryCopy)boundaryCopy.textContent='edge results · 6 jobs not started';updateSummary();await renderSelected();renderRunJobs();
    }catch(error){if(!current())return;const message=error instanceof Error?error.message:String(error);invalidatePreparedSystem();contractFailure=preparationFailureCopy(message);text('status','EXECUTION REMAINS LOCKED');}finally{if(current())syncExecutionContract();}
}

function renderRunJobs():void{if(currentMainMode==='runs'){renderRunHistory();return;}const matrix=document.getElementById('job-matrix');if(!matrix)return;const view=runJobsViewFrom(runJobs,executionContract.validated);renderRunJobsDom(document,matrix,view);if(view.executionMeta!==null)text('execution-meta',view.executionMeta);if(view.resultCount!==null)text('result-count',view.resultCount);if(view.boundary!==null)text('run-boundary',view.boundary);}

function runBindingForCurrentExecution():ExactRunBinding|null{const context=activeCampaignContext(),specDigest=String(preflightData?.spec_digest||'');if(!context?.campaign_id||!context.campaign_scientific_generation||!context.campaign_scientific_digest||!/^sha256:[0-9a-f]{64}$/.test(specDigest)||!edgeSpecRef||!edgeNetworkRef||!complexTransformationRef||!solventTransformationRef)return null;return{requestKey:`fep:${context.campaign_id}:${context.campaign_scientific_generation}:${selectedEdge.edge_id}:${specDigest}`,campaign:{id:context.campaign_id,version:context.campaign_scientific_generation,sha256:context.campaign_scientific_digest},edgeId:selectedEdge.edge_id,specDigest,edgeSpecRef,edgeNetworkRef,complexTransformationRef,solventTransformationRef};}
function operationBindingForCurrentExecution():ExactOperationBinding|null{const run=runBindingForCurrentExecution(),context=activeCampaignContext(),plan=readPlannerOutputReceipt(),system=contentRef(executionContract.system?.prepared_receptor_state_ref,'prepared_receptor_state'),parent=contentRef(executionContract.parentPose?.pose_ref,'pose_hypothesis'),proposal=contentRef(executionContract.proposalPose?.pose_ref,'pose_hypothesis');if(!run||!context||!networkArtifactRef||!plannerOutputReceiptMatches(plan,context,networkArtifactRef)||!plan||!system||!parent||!proposal||!sameExactRef(system,plan.prepared_system_ref))return null;return{...run,planNetworkJobId:plan.network_job_id,planNetworkRef:plan.plan_network_ref,preparedSystemRef:system,parentPoseRef:parent,proposalPoseRef:proposal};}
function currentExecutionMatchesReceipt(receipt:RunReceipt):boolean{return exactOperationBindingMatches(operationBindingForCurrentExecution(),operationBindingFromReceipt(receipt));}
function renderOperationConfirmation():boolean{const host=document.getElementById('operation-confirmation');if(!host)return false;const current=operationBindingForCurrentExecution(),receipt=currentRunReceipt(),receiptBinding=receipt?operationBindingFromReceipt(receipt):null,exact=!!current&&(!receipt||exactOperationBindingMatches(current,receiptBinding));renderOperationConfirmationDom(document,host,current,!!receipt,auditCopyId==='main',exact);return exact;}
function adoptRunSet(data:Record<string,any>,receipt:RunReceipt|null=currentRunReceipt()):boolean{
    const responseRunId=String(data.ref?.id||'');if(data.ref?.kind!=='run'||!receipt||!fullJobId(responseRunId)||!runReceiptMatchesData(data,receipt)){runJobs=[];renderRunJobs();const cancel=document.getElementById('cancel-run') as HTMLButtonElement,retry=document.getElementById('retry-run') as HTMLButtonElement;cancel.disabled=true;retry.disabled=true;text('contract-gate',`DETACHED RUN ${responseRunId||'UNKNOWN'} · REQUEST / CAMPAIGN / EDGE / SPEC / REF MISMATCH`);text('status','RUNSET RESPONSE WAS NOT ADOPTED; THE EXISTING DURABLE RECEIPT WAS PRESERVED');text('run-boundary','DETACHED HISTORY · CANCEL / RETRY DISABLED');return false;}
    const state=runReceiptState(data.state);if(!state){text('contract-gate',`RUNSET ${responseRunId} · UNKNOWN STATE REFUSED`);return false;}
    let durable=receipt;if(!durable.run_id){const updated=updateOwnedRunReceipt(durable,{run_id:responseRunId,state});if(!updated)return false;durable=updated;}else{const updated=updateOwnedRunReceipt(durable,{state});if(!updated)return false;durable=updated;}
    activeRunId=responseRunId;activeRunReceipt=durable;
    const jobs=Object.values(data.jobs||{}) as Record<string,any>[];
    runJobs=jobs.map(job=>({leg:job.leg,repeat:Number(job.repeat_index),jobId:String(job.job_id),state:String(job.state),error:job.error||undefined}));
    renderRunJobs();
    text('contract-gate',`RUNSET ${responseRunId} · ${state.toUpperCase()}`);text('status',`SERVER-OWNED RBFE RUNSET · ${state.toUpperCase()}`);
    const cancel=document.getElementById('cancel-run') as HTMLButtonElement,retry=document.getElementById('retry-run') as HTMLButtonElement;cancel.disabled=!['pending','running','aggregating'].includes(state);retry.disabled=state!=='blocked'||auditCopyId!=='main'||!currentExecutionMatchesReceipt(durable);
    if(state==='completed'){
        const rawResult=data.aggregate_output?.result?.data||data.aggregate_output?.result;
        const aggregate=aggregatePanelViewFrom(rawResult,selectedEdge.right_id);
        text('aggregate-state',aggregate.state);text('aggregate-detail',aggregate.detail);text('accepted-legs',aggregate.acceptedLegs);text('aggregate-convergence',aggregate.convergence);text('aggregate-ddg',aggregate.ddg);text('run-boundary',aggregate.boundary);
    }
    if(['completed','cancelled','failed','refused'].includes(state)){archiveDetachedRun(durable,`runset_${state}`,data);removeRunReceiptIf(durable.owner_token,responseRunId);activeRunReceipt=null;activeRunId=null;if(currentMainMode==='runs')renderRunHistory();}else writeRunReceipt(durable);
    syncExecutionContract();
    return true;
}

async function watchRunSet(receipt:RunReceipt):Promise<void>{
    if(!receipt.run_id)return;const runId=receipt.run_id,scope=operations.begin('run-watch');let transientFailures=0;
    const current=()=>{const durable=runReceiptOwned(receipt.owner_token);return operations.current(scope,{edits:false})&&!!durable&&durable.run_id===runId&&durable.request_key===receipt.request_key;};
    for(;;){if(!current())return;try{const response=await client.execute('physics.rbfe-run.get',{run_ref:{kind:'run',id:runId}});if(!current())return;if(!response.ok)throw new Error(response.error?.message);transientFailures=0;const data=response.data||{};if(!adoptRunSet(data,runReceiptOwned(receipt.owner_token)))return;if(['completed','blocked','cancelled','failed','refused'].includes(String(data.state)))return;}catch(error){if(!current())return;transientFailures++;text('status',`RUNSET RECONNECT ${transientFailures} · ${error instanceof Error?error.message:String(error)}`);if(transientFailures>=10){text('execution-meta','CONNECTION LOST · SERVER STATE UNKNOWN');text('run-boundary','DURABLE RECEIPT PRESERVED · SERVER STATE UNKNOWN');return;}}await sleep(3000);}
}

type LockManagerLike={request<T>(name:string,options:{mode:'exclusive'},callback:()=>Promise<T>):Promise<T>};
async function withCampaignPhysicalLock<T>(campaignId:string,callback:()=>Promise<T>):Promise<T|null>{const locks=(navigator as Navigator&{locks?:LockManagerLike}).locks;if(!locks){text('contract-gate','PHYSICAL START LOCK UNAVAILABLE · THIS BROWSER CANNOT START OR RETRY');return null;}return locks.request(`dirac-rbfe-physical:${campaignId}`,{mode:'exclusive'},callback);}
async function withCampaignPreparationLock<T>(campaignId:string,callback:()=>Promise<T>):Promise<T|null>{const locks=(navigator as Navigator&{locks?:LockManagerLike}).locks;if(!locks){showBuilderNotice('Preparation submission lock is unavailable in this browser. No request key or server Job was created.');return null;}return locks.request(preparationSubmissionLockName(auditCopyId,campaignId),{mode:'exclusive'},callback);}

async function startOpenFEJobs():Promise<void>{
    if(auditCopyId!=='main'){text('contract-gate','AUDIT COPY · PHYSICAL START DISABLED');return;}
    const existing=currentRunReceipt();
    if(!existing&&activeRunId){text('contract-gate',`START REFUSED · LEGACY RUNSET ${activeRunId} MUST BE RECONCILED FIRST`);return;}
    if(existing&&!existing.run_id&&!currentExecutionMatchesReceipt(existing)){text('contract-gate','START REFUSED · UNRESOLVED OR LEGACY CREATION RECEIPT CANNOT RECONSTRUCT THE EXACT OPERATION');return;}
    if(existing?.run_id||existing&&existing.state!=='creating'){text('contract-gate',`START REFUSED · RUNSET ${existing.run_id||'PENDING'} IS ALREADY ATTACHED`);return;}
    const creationRetry=!!existing&&!existing.run_id&&currentExecutionMatchesReceipt(existing),operation=operationBindingForCurrentExecution();
    if(!operation||!renderOperationConfirmation()||(!creationRetry&&(!executionContract.validated||!hasGovernedMapping(selectedEdge)))){text('contract-gate','RUNSET CREATION REFUSED · EXACT VISIBLE PLAN / SYSTEM / POSE / EDGE CONFIRMATION MISSING');return;}
    const button=document.getElementById('prepare-edge') as HTMLButtonElement,binding:ExactRunBinding=operation;
    if(!aggregateArmMatches(aggregateArm,binding,Date.now())){
        aggregateArm={...binding,expiresAt:Date.now()+5000};aggregateArmPhysicalSnapshot=localStorage.getItem(globalPhysicalReceiptKey(binding.campaign.id));const armed=aggregateArm;
        button.textContent='CONFIRM 6 GPU JOBS';text('status','EXACT OPERATION CARD ABOVE · COMPLEX + SOLVENT × 3 · CLICK AGAIN TO START');
        setTimeout(()=>{if(aggregateArm===armed){aggregateArm=null;aggregateArmPhysicalSnapshot=null;button.textContent=creationRetry?'RETRY EXACT RUN RECEIPT':'START 6 JOBS';text('status',creationRetry?'EXACT PRE-START RECEIPT PRESERVED · RETRY AVAILABLE':'SYSTEM QUALIFIED · 6 PHYSICAL JOBS READY');}},5000);return;
    }
    const markerSnapshot=aggregateArmPhysicalSnapshot;aggregateArm=null;aggregateArmPhysicalSnapshot=null;button.disabled=true;
    await withCampaignPhysicalLock(binding.campaign.id,async()=>{
        if(localStorage.getItem(globalPhysicalReceiptKey(binding.campaign.id))!==markerSnapshot){text('contract-gate','START REFUSED · ANOTHER TAB CHANGED THE CAMPAIGN PHYSICAL RECEIPT · REVIEW AND CONFIRM AGAIN');return;}
        const lockedExisting=readRunReceipt();
        if((lockedExisting&&!currentExecutionMatchesReceipt(lockedExisting))||lockedExisting?.run_id||lockedExisting&&lockedExisting.state!=='creating'){text('contract-gate',`START REFUSED · CAMPAIGN PHYSICAL RECEIPT ${lockedExisting?.run_id||lockedExisting?.state||'CHANGED'} WON THE LOCK`);return;}
        if(!exactOperationBindingMatches(operationBindingForCurrentExecution(),operation)||!renderOperationConfirmation()){text('contract-gate','START REFUSED · OPERATION CHANGED WHILE WAITING FOR THE CAMPAIGN LOCK');return;}
        runJobs=[];renderRunJobs();text('contract-gate','CREATING DURABLE RBFE RUNSET');
        const scope=operations.begin('run-start'),draftSnapshot=draftIdentitySnapshot(),now=new Date().toISOString(),campaignRef=campaignScientificRefFrom(binding.campaign.id,binding.campaign.version,binding.campaign.sha256);if(!campaignRef)return;
        const receipt:RunReceipt={schema_version:3,owner_token:scope.id,created_at:lockedExisting?.created_at||now,updated_at:now,run_id:null,request_key:binding.requestKey,state:'creating',campaign_scientific_ref:campaignRef,edge_id:binding.edgeId,spec_digest:binding.specDigest,edge_spec_ref:{kind:'artifact',id:binding.edgeSpecRef.id,sha256:binding.edgeSpecRef.sha256},edge_network_ref:{kind:'artifact',id:binding.edgeNetworkRef.id,sha256:binding.edgeNetworkRef.sha256},complex_transformation_ref:{kind:'artifact',id:binding.complexTransformationRef.id,sha256:binding.complexTransformationRef.sha256},solvent_transformation_ref:{kind:'artifact',id:binding.solventTransformationRef.id,sha256:binding.solventTransformationRef.sha256},plan_network_job_id:operation.planNetworkJobId,plan_network_ref:{kind:'artifact',id:operation.planNetworkRef.id,sha256:operation.planNetworkRef.sha256},prepared_system_ref:{...operation.preparedSystemRef},parent_pose_ref:{...operation.parentPoseRef},proposal_pose_ref:{...operation.proposalPoseRef}};
        writeRunReceipt(receipt);activeRunReceipt=receipt;activeRunId=null;syncExecutionContract();
        const current=()=>operations.current(scope,{edits:true})&&draftIdentityMatches(draftSnapshot)&&currentExecutionMatchesReceipt(receipt)&&runReceiptOwned(scope.id)?.request_key===receipt.request_key;
        try{
            const response=await client.execute('physics.rbfe-run.start',{request_key:binding.requestKey,campaign_id:binding.campaign.id,campaign_scientific_generation:binding.campaign.version,campaign_scientific_digest:binding.campaign.sha256,edge_spec_ref:receipt.edge_spec_ref,edge_network_ref:receipt.edge_network_ref,complex_transformation_ref:receipt.complex_transformation_ref,solvent_transformation_ref:receipt.solvent_transformation_ref,analysis_bootstraps:1000});
            if(!response.ok)throw new Error(response.error?.message);const data=response.data||{},runId=String(data.ref?.id||'');
            if(!fullJobId(runId)||!runReceiptMatchesData(data,receipt))throw new Error('RunSet response is not bound to the exact request key, campaign, edge spec and execution refs');
            if(!current())return;const state=runReceiptState(data.state);if(!state)throw new Error('RunSet returned an unknown state');const updated=updateOwnedRunReceipt(receipt,{run_id:runId,state});if(!updated||!adoptRunSet(data,updated))throw new Error('RunSet receipt ownership changed before adoption');const durable=currentRunReceipt();if(durable?.run_id)void watchRunSet(durable);
        }catch(error){if(current()){text('contract-gate',`RUNSET CREATION STATUS UNKNOWN · ${error instanceof Error?error.message:String(error)} · RETRY REUSES ${binding.requestKey}`);text('status','PRE-START RECEIPT PRESERVED · SAME REQUEST KEY REQUIRED');}}
    });
    button.disabled=false;button.textContent=currentRunReceipt()?.run_id?'RUNSET ATTACHED':currentRunReceipt()?.state==='creating'?'RETRY EXACT RUN RECEIPT':'START 6 JOBS';syncExecutionContract();
}

function riskPointAt(clientX:number,clientY:number):{edge:Edge;x:number;y:number}|null{const canvas=document.getElementById('risk-canvas') as HTMLCanvasElement|null;if(!canvas)return null;const rect=canvas.getBoundingClientRect(),x=clientX-rect.left,y=clientY-rect.top;let best:null|{edge:Edge;x:number;y:number}=null,distance=Infinity;riskPoints.forEach(point=>{const candidate=Math.hypot(point.x-x,point.y-y);if(candidate<distance){distance=candidate;best=point;}});return distance<=14?best:null;}

const riskCanvas=document.getElementById('risk-canvas') as HTMLCanvasElement|null;riskCanvas?.addEventListener('pointermove',event=>{const point=riskPointAt(event.clientX,event.clientY);text('atlas-tooltip',point?`${point.edge.left_id} → ${point.edge.right_id} · COVERAGE ${value(Math.min(point.edge.rdkit_fmcs_diagnostic?.left_heavy_atom_fraction??0,point.edge.rdkit_fmcs_diagnostic?.right_heavy_atom_fraction??0),2)} · Δ ${value(point.edge.heavy_mapping_disagreement_jaccard)}`:'HOVER / CLICK POINT');});riskCanvas?.addEventListener('click',event=>{const point=riskPointAt(event.clientX,event.clientY);if(point)void selectEdge(point.edge.edge_id);});
document.getElementById('system-select')?.addEventListener('change',event=>chooseSystem((event.target as HTMLSelectElement).value));
document.getElementById('validate-contract')?.addEventListener('click',()=>void validateExecutionContract());document.getElementById('prepare-edge')?.addEventListener('click',()=>void startOpenFEJobs());
document.getElementById('cancel-run')?.addEventListener('click',async()=>{const receipt=currentRunReceipt();if(!receipt?.run_id||!['pending','running','aggregating'].includes(receipt.state))return;const scope=operations.begin('run-command'),priorState=receipt.state,updated=updateOwnedRunReceipt(receipt,{state:'cancel_requested'});if(!updated)return;try{const response=await client.execute('physics.rbfe-run.cancel',{run_ref:{kind:'run',id:receipt.run_id}});if(!operations.current(scope,{edits:false})||!runReceiptOwned(receipt.owner_token))return;if(!response.ok)throw new Error(response.error?.message||'run cancellation refused');adoptRunSet(response.data||{},runReceiptOwned(receipt.owner_token));}catch(error){const current=runReceiptOwned(receipt.owner_token);if(current?.state==='cancel_requested')updateOwnedRunReceipt(current,{state:priorState});text('status',`RUNSET CANCEL FAILED · ${error instanceof Error?error.message:String(error)} · DURABLE RECEIPT PRESERVED`);syncExecutionContract();}});
document.getElementById('retry-run')?.addEventListener('click',async()=>{const receipt=currentRunReceipt();if(auditCopyId!=='main'){text('status','AUDIT COPY · PHYSICAL RETRY DISABLED');return;}if(!receipt?.run_id||receipt.state!=='blocked'||!currentExecutionMatchesReceipt(receipt)||!renderOperationConfirmation()){text('status','RUNSET RETRY REFUSED · EXACT VISIBLE OPERATION RECEIPT MISSING');return;}await withCampaignPhysicalLock(receipt.campaign_scientific_ref.id,async()=>{const currentReceipt=readRunReceipt();if(!currentReceipt?.run_id||currentReceipt.state!=='blocked'||currentReceipt.run_id!==receipt.run_id||!currentExecutionMatchesReceipt(currentReceipt)){text('status','RUNSET RETRY REFUSED · ANOTHER TAB CHANGED THE RECEIPT');return;}const scope=operations.begin('run-command');try{const response=await client.execute('physics.rbfe-run.retry',{run_ref:{kind:'run',id:receipt.run_id}});if(!operations.current(scope,{edits:false})||!runReceiptOwned(receipt.owner_token))return;if(!response.ok)throw new Error(response.error?.message||'run retry refused');if(adoptRunSet(response.data||{},runReceiptOwned(receipt.owner_token))){const durable=currentRunReceipt();if(durable?.run_id)void watchRunSet(durable);}}catch(error){text('status',`RUNSET RETRY FAILED · ${error instanceof Error?error.message:String(error)} · BLOCKED RECEIPT PRESERVED`);}});});

let replanArmed=false; document.getElementById('replan')?.addEventListener('click',()=>{const button=document.getElementById('replan') as HTMLButtonElement,context=activeCampaignContext(),resting=context?'REPLAN NETWORK':'REPLAN BENCHMARK';if(!replanArmed){replanArmed=true;button.textContent=context?'CONFIRM SAME CAMPAIGN':'CONFIRM BENCHMARK';text('status',`${network.compounds.length} ${context?'CAMPAIGN':'T4L'} LIGANDS · OPENFE PLANNER · CPU JOB · CLICK AGAIN TO SUBMIT`);setTimeout(()=>{replanArmed=false;button.textContent=resting;},5000);return;}replanArmed=false;button.textContent=resting;void replanNetwork();});

const campaignBuilder=document.getElementById('campaign-builder') as HTMLDialogElement|null;
const sketcherHost=document.getElementById('molecule-sketcher');
let builderNoticeTimer=0;
let builderReturnFocus:HTMLElement|null=null;
let receptorInputReady=false;
let receptorSourceLabel='NO RECEPTOR';
type BoundLigandCandidate={resname:string;chain:string;residue_number:string;heavy_atom_count:number;label:string;role:'ligand'|'cofactor'};
type LigandInputRow={id:string;smiles:string;sourceLine:number;raw:string;parseError?:string};
let builderStage:BuilderStage='inputs';
let receptorPdbText='';
let receptorRecord:{title:string;method:'xray'|'cryoem'|'nmr'|'predicted'|'model';resolution:number|null}={title:'',method:'model',resolution:null};
let boundLigands:BoundLigandCandidate[]=[];
let pdbFetchGeneration=0;
let ligandFileGeneration=0;
let receptorFileGeneration=0;
let preparedCampaignSystem:PreparedSystemOption|null=null;
let validatedLigandSignature='';
let validatedLigandCount=0;
let validatedLigandRowCount=0;
let validatedLigandErrorCount=0;
const ligandImportErrors=new Map<string,string>();
function reflectBuilderStage():void{campaignBuilder?.setAttribute('data-stage',builderStage);}
function currentLigandSignature():string{return((document.getElementById('campaign-ligands') as HTMLTextAreaElement|null)?.value||'').replace(/\r\n/g,'\n').trim();}

function inspectBoundLigands(pdbText:string):BoundLigandCandidate[]{
    const excluded=new Set(['HOH','WAT','DOD','NA','CL','K','CA','MG','MN','ZN','FE','CU','CO','NI','CD','HG','BR','IOD','SO4','PO4','GOL','EDO']);
    const cofactors=new Set(['HEM','HEC','FAD','FMN','NAD','NAP','SAM','SAH','COA','PLP','TPP']);
    const groups=new Map<string,{resname:string;chain:string;residue_number:string;atoms:number}>();
    pdbText.split(/\r?\n/).forEach(line=>{if(!line.startsWith('HETATM')||line.length<54)return;const resname=line.slice(17,20).trim().toUpperCase(),chain=line.slice(21,22).trim(),residue_number=line.slice(22,27).trim(),element=(line.length>=78?line.slice(76,78):line.slice(12,14)).trim().toUpperCase();if(excluded.has(resname)||element==='H'||element==='D')return;const key=`${resname}|${chain}|${residue_number}`,row=groups.get(key)||{resname,chain,residue_number,atoms:0};row.atoms++;groups.set(key,row);});
    return [...groups.values()].sort((a,b)=>Number(cofactors.has(a.resname))-Number(cofactors.has(b.resname))||b.atoms-a.atoms||a.resname.localeCompare(b.resname)).map(row=>({...row,heavy_atom_count:row.atoms,role:cofactors.has(row.resname)?'cofactor':'ligand',label:`${cofactors.has(row.resname)?'COFACTOR · ':''}${row.resname} · CHAIN ${row.chain||'—'} · RES ${row.residue_number} · ${row.atoms} HEAVY ATOMS`}));
}
function selectedReferenceLigand():BoundLigandCandidate|null{
    const value=(document.getElementById('site-select') as HTMLSelectElement|null)?.value||'';return value===''?null:boundLigands[Number(value)]||null;
}
function receptorChainIds():string[]{
    const ids=new Set<string>();
    receptorPdbText.split(/\r?\n/).forEach(line=>{if(line.startsWith('ATOM  ')&&line.length>21)ids.add(line.slice(21,22).trim()||'_');});
    return [...ids].sort();
}
function populateBoundLigands():void{
    boundLigands=inspectBoundLigands(receptorPdbText);const select=document.getElementById('site-select') as HTMLSelectElement|null;if(!select)return;
    const role=(document.getElementById('site-role-filter') as HTMLSelectElement|null)?.value||'ligand';
    const visible=boundLigands.map((row,index)=>({row,index})).filter(({row})=>role==='all'||row.role==='ligand');
    select.disabled=!visible.length;select.innerHTML=visible.length?'<option value="">SELECT THE MEASURED PARENT…</option>'+visible.map(({row,index})=>`<option value="${index}">${escapeHtml(row.label)}</option>`).join(''):'<option value="">NO DRUG-LIKE BOUND LIGAND FOUND</option>';
    const selected=selectedReferenceLigand();text('pose-reference-state',selected?selected.label:'NO REFERENCE · ALIGNMENT UNAVAILABLE');
}
function resetBuilderProgress():void{
    builderStage='inputs';reflectBuilderStage();preparedCampaignSystem=null;executionContract.system=null;invalidatePreparedSystem();const review=document.getElementById('review-inputs') as HTMLButtonElement|null;if(review)review.textContent='REVIEW INPUTS →';
    renderPreparationPolicyDom(document,preparationPolicyViewFrom(null));
    const generated=document.getElementById('generated-gate');generated?.classList.remove('pass');generated?.classList.add('pending');const icon=generated?.querySelector('i'),detail=generated?.querySelector('small');if(icon)icon.textContent='○';if(detail)detail.textContent='Not computed until campaign preparation starts';
    document.querySelectorAll<HTMLElement>('.artifact-pipeline li').forEach(item=>{item.classList.remove('ready');const state=item.querySelector('em');if(state)state.textContent=item.dataset.pipeline==='network'?'WILL PLAN':item.dataset.pipeline==='transforms'?'AFTER EDGE REVIEW':'WILL BUILD';});
}
function invalidateLigandValidation():void{
    ligandValidationGeneration+=1;
    validatedLigandSignature='';validatedLigandCount=0;validatedLigandRowCount=0;validatedLigandErrorCount=0;
    const rows=document.getElementById('ligand-identity-rows');if(rows)rows.innerHTML='<p>INPUT CHANGED · REVALIDATE EVERY ROW</p>';
}
function showBuilderNotice(message:string):void{
    const notice=document.getElementById('builder-notice');if(!notice)return;
    notice.textContent=message;notice.classList.add('visible');window.clearTimeout(builderNoticeTimer);const blocking=/refused|failed|conflict|unknown|receipt|preserved|unavailable|blocked|locked|mismatch|cannot|could not/i.test(message);notice.dataset.persistent=String(blocking);if(!blocking)builderNoticeTimer=window.setTimeout(()=>notice.classList.remove('visible'),4200);
}
function syncPlannerRecoveryControl():void{const button=document.getElementById('archive-planner-receipt') as HTMLButtonElement|null,receipt=readPlannerReceipt();if(!button)return;button.hidden=!receipt;button.disabled=!receipt||plannerInFlight;button.textContent=receipt?.job_id?'ARCHIVE DETACHED PLANNER RECEIPT':'ARCHIVE UNKNOWN PLANNER SUBMISSION';}
function setBuilderOpen(open:boolean):void{
    if(!campaignBuilder)return;
    if(open){builderReturnFocus=document.activeElement instanceof HTMLElement?document.activeElement:null;if(!campaignBuilder.open)campaignBuilder.showModal();campaignBuilder.classList.add('open');requestAnimationFrame(()=>(document.getElementById('campaign-name') as HTMLInputElement|null)?.focus());}
    else{campaignBuilder.classList.remove('open');if(campaignBuilder.open)campaignBuilder.close();builderReturnFocus?.focus();}
}
campaignBuilder?.addEventListener('cancel',event=>{event.preventDefault();setBuilderOpen(false);});
function updateBuilderReadiness(validLigands=validatedLigandSignature===currentLigandSignature()?validatedLigandCount:0):void{
    const requiredPortfolio=['campaign-question','assay-anchor','portfolio-priority','cost-cap','next-action','stop-rule'];
    const missingPortfolio=requiredPortfolio.filter(id=>!(document.getElementById(id) as HTMLInputElement|HTMLSelectElement|null)?.value.trim());
    const decisionReady=missingPortfolio.length===0;
    const validationCurrent=validatedLigandSignature===currentLigandSignature(),allLigandsReady=validationCurrent&&validatedLigandErrorCount===0&&validLigands>=2&&validLigands===validatedLigandRowCount;
    const reference=selectedReferenceLigand();const structureReady=receptorInputReady&&!!receptorPdbText&&!!reference;const ready=structureReady&&allLigandsReady&&decisionReady;
    const review=document.getElementById('review-inputs') as HTMLButtonElement|null;if(review)review.disabled=!ready;
    text('draft-readiness',builderStage==='accepted'?'POSES REVIEWED · READY TO PLAN':builderStage==='prepared'?'POSE + POLICY REVIEW REQUIRED':ready?'READY FOR INPUT REVIEW':!receptorInputReady?'ADD A RECEPTOR':!reference?'CHOOSE A BOUND REFERENCE LIGAND':!allLigandsReady?(validatedLigandErrorCount?`FIX ${validatedLigandErrorCount} LIGAND ERROR${validatedLigandErrorCount===1?'':'S'}`:'VALIDATE AT LEAST 2 LIGANDS'):`ADD DECISION CONTEXT · ${missingPortfolio.length} MISSING`);
    text('builder-next-label',builderStage==='accepted'?'NEXT · PLAN OPENFE NETWORK':builderStage==='prepared'?'NEXT · REVIEW POLICY + EVERY POSE':builderStage==='reviewed'?'NEXT · PREPARE RECEPTOR + POSES':ready?'NEXT · REVIEW NEW CAMPAIGN':!receptorInputReady?'NEXT · ADD A NEW RECEPTOR':!reference?'NEXT · CHOOSE THE CRYSTALLOGRAPHIC PARENT':!allLigandsReady?(validatedLigandErrorCount?'NEXT · FIX EVERY LIGAND ERROR':'NEXT · VALIDATE AT LEAST 2 NEW MOLECULES'):'NEXT · STATE WHY THIS CHANGES A PROJECT DECISION');
    text('proposed-system-title',`${receptorSourceLabel} · ${validLigands} LIGANDS · ${validLigands>=2?'POSE STRATEGY PENDING':'NOT PLANNED'}`);
    const previewEdges=validLigands<2?0:validLigands===2?1:Math.ceil(validLigands*1.5),previewJobs=previewEdges*6;text('preview-node-count',String(validLigands));text('preview-edge-count',String(previewEdges));text('preview-job-count',String(previewJobs));text('preview-hours',previewEdges?`~${previewEdges*30}`:'—');
    const previewMessage=document.getElementById('network-preview-message');if(previewMessage)previewMessage.innerHTML=validLigands>=2?'SERIES READY FOR NETWORK PLANNING<br><small>EDGES ARE ESTIMATES UNTIL OPENFE RUNS</small>':'DRAW AT LEAST TWO MOLECULES<br><small>NETWORK WILL BE GENERATED FROM YOUR NEW SERIES</small>';
    document.querySelector('.network-miniature')?.classList.toggle('empty',validLigands<2);
    const structureGate=document.getElementById('structure-gate');structureGate?.classList.toggle('pass',structureReady);structureGate?.classList.toggle('pending',!structureReady);
    const ligandGate=document.getElementById('ligand-gate');ligandGate?.classList.toggle('pass',allLigandsReady);ligandGate?.classList.toggle('pending',!allLigandsReady);ligandGate?.classList.toggle('blocked',validatedLigandErrorCount>0);
    const portfolioGate=document.getElementById('portfolio-gate');portfolioGate?.classList.toggle('pass',decisionReady);portfolioGate?.classList.toggle('pending',!decisionReady);
    if(structureGate){const icon=structureGate.querySelector('i'),detail=structureGate.querySelector('small');if(icon)icon.textContent=structureReady?'✓':'○';if(detail)detail.textContent=structureReady?`${receptorSourceLabel} · ${reference!.resname} reference selected · preparation not yet run`:receptorInputReady?'Select a bound ligand that matches Parent':'No receptor selected';}
    if(ligandGate){const icon=ligandGate.querySelector('i'),detail=ligandGate.querySelector('small');if(icon)icon.textContent=allLigandsReady?'✓':validatedLigandErrorCount?'!':'○';if(detail)detail.textContent=allLigandsReady?`${validLigands} complete browser-audited molecular identities`:validatedLigandErrorCount?`${validatedLigandErrorCount} explicit row/policy error${validatedLigandErrorCount===1?'':'s'} block review`:validLigands?'Validation is stale or fewer than two rows are ready':'No new molecules yet';}
    if(portfolioGate){const icon=portfolioGate.querySelector('i'),detail=portfolioGate.querySelector('small');if(icon)icon.textContent=decisionReady?'✓':'○';if(detail)detail.textContent=decisionReady?'Decision question, assay anchor, priority, cost cap, next action and stop rule recorded':`${missingPortfolio.length} required decision field${missingPortfolio.length===1?'':'s'} missing`;}
}
function addSketchedMolecule(molecule:SketchedMolecule):void{
    const textarea=document.getElementById('campaign-ligands') as HTMLTextAreaElement|null;if(!textarea)return;
    if(runContextLocked()){showBuilderNotice(`Molecule add refused while RunSet ${activeRunId||'creation receipt'} is attached.`);return;}
    const safeId=molecule.id.trim().replace(/\s+/g,'-')||`NEW-${String(textLigandRows().length+1).padStart(3,'0')}`;
    const rows=textLigandRows().filter(row=>row.id!==safeId),raw=`${safeId}  ${molecule.smiles}`;rows.push({id:safeId,smiles:molecule.smiles,sourceLine:rows.length+1,raw});textarea.value=rows.map(row=>`${row.id}  ${row.smiles}`).join('\n');
    invalidateScientificState('ligands',`${safeId} added from the 2D graph editor`);
    showBuilderNotice(`${safeId} added from the 2D graph editor · ${molecule.atomCount} explicit atoms · ${molecule.smiles}`);void validateBuilderLigands();
}
let moleculeSketcher:MoleculeSketcher|null=null;
function openMoleculeSketcher():void{
    if(!sketcherHost)return;
    try{moleculeSketcher??=new MoleculeSketcher(sketcherHost,addSketchedMolecule);moleculeSketcher.open();}
    catch(error){showBuilderNotice(`2D editor unavailable · paste SMILES remains available · ${error instanceof Error?error.message:String(error)}`);}
}
let currentMainMode:'build'|'review'|'runs'='review';
function setMainMode(mode:'build'|'review'|'runs'):void{
    currentMainMode=mode;(['build','review','runs'] as const).forEach(name=>{for(const prefix of ['main','builder']){const button=document.getElementById(`${prefix}-${name}`);button?.classList.toggle('active',name===mode);button?.setAttribute('aria-pressed',String(name===mode));}});
}
function renderRunHistory():void{
    const matrix=document.getElementById('job-matrix');if(!matrix)return;
    const view=runHistoryViewFrom(currentRunReceipt(),receiptStore.detachedRuns());
    renderRunHistoryDom(document,matrix,view);text('execution-meta',view.meta);
}
function showRunsWorkspace():void{
    setBuilderOpen(false);setMainMode('runs');renderRunHistory();
    const column=document.querySelector<HTMLElement>('.right-col'),panel=document.querySelector<HTMLElement>('.diagnostics');
    if(column&&panel)column.scrollTo({top:Math.max(0,panel.offsetTop-column.offsetTop),behavior:'smooth'});
}
function selectChoice(button:HTMLButtonElement):void{
    const group=button.closest<HTMLElement>('[data-choice-group]');if(!group)return;
    if(runContextLocked()){showBuilderNotice(`Scientific choice change refused while RunSet ${activeRunId||'creation receipt'} is attached.`);return;}
    group.querySelectorAll('button').forEach(item=>item.classList.toggle('active',item===button));
    invalidateScientificState(button.dataset.invalidate||group.dataset.choiceGroup||'input',`${button.textContent?.trim()||'choice'} changed`);
    if(group.dataset.choiceGroup==='pose'){
        const choice=button.dataset.choice||'align';
        const labels:Record<string,string>={align:'REFERENCE-ALIGNED',dock:'DOCKED POSE ENSEMBLE',upload:'IMPORTED ALIGNED POSES'};
        text('proposed-system-title',`${(document.getElementById('campaign-pdb') as HTMLInputElement)?.value.trim().toUpperCase()||'NEW STRUCTURE'} · ${textLigandRows().length} LIGANDS · ${labels[choice]}`);
    }
}
function executionContextAttached():boolean{return!!(authoritativeCampaignContextState||networkArtifactRef||executionContract.system||executionContract.validated||currentRunReceipt()||activeRunId||runJobs.length);}
function invalidateScientificState(scope:string,reason:string):boolean{
    if(runContextLocked()){showBuilderNotice(`Scientific edit refused while RunSet ${activeRunId||'creation receipt'} is attached. Complete/retry the RunSet before changing campaign identity.`);syncExecutionContract();return false;}
    if(scope==='ligands')invalidateLigandValidation();
    if(scope==='receptor'){receptorInputReady=false;receptorPdbText='';boundLigands=[];populateBoundLigands();}
    currentScientificInputs=null;copyStorage.remove(plannerOutputReceiptKey);
    detachExecutionContext();resetBuilderProgress();text('execution-meta','NO CURRENT-CAMPAIGN RUN DATA');text('result-count','0 · NONE');text('run-boundary','PLAN ONLY · INPUTS CHANGED');
    updateBuilderReadiness();showBuilderNotice(`Downstream artifacts invalidated atomically · ${reason}. Saved server artifacts remain immutable but are no longer bound to this draft.`);
    return true;
}
function textLigandRows():LigandInputRow[]{
    const raw=(document.getElementById('campaign-ligands') as HTMLTextAreaElement|null)?.value||'';
    const rows:LigandInputRow[]=[];
    raw.split(/\r?\n/).forEach((source,sourceIndex)=>{
        const line=source.trim();if(!line)return;
        const tokens=line.split(/[\s,\t,]+/).filter(Boolean);let id='',smiles='',parseError:string|undefined;
        if(tokens.length===1){id=`CMPD-${String(sourceIndex+1).padStart(3,'0')}`;smiles=tokens[0];}
        else if(tokens.length===2){[id,smiles]=tokens;if(!/^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}$/.test(id))parseError='ID must start with a letter or digit and use only letters, digits, _ . : + -';}
        else{id=tokens[0]||`ROW-${sourceIndex+1}`;smiles=tokens.slice(1).join(' ');parseError=`Expected exactly SMILES or ID + SMILES; found ${tokens.length} whitespace/comma tokens`;}
        rows.push({id,smiles,sourceLine:sourceIndex+1,raw:line,parseError:parseError||ligandImportErrors.get(id)});
    });
    const counts=new Map<string,number>();rows.forEach(row=>counts.set(row.id,(counts.get(row.id)||0)+1));rows.forEach(row=>{if((counts.get(row.id)||0)>1)row.parseError=`Duplicate full compound ID ${row.id}`;});
    return rows;
}
function stereoIdentity(info:AtomInfo):{label:string;unknown:boolean;unavailable:boolean}{
    if(!info.stereoAvailable)return{label:'UNVERIFIED · RDKIT CIP/E-Z AUDIT UNAVAILABLE',unknown:true,unavailable:true};
    const tags=[...info.cipAtoms.map(([index,label])=>`ATOM ${index}:${label.replace(/[()]/g,'')}`),...info.cipBonds.map(([left,right,label])=>`BOND ${left}-${right}:${label.replace(/[()]/g,'')}`),...info.potentialEzBonds.map(([left,right])=>`BOND ${left}-${right}:? UNSPECIFIED E/Z`)];
    const unknown=tags.some(tag=>tag.includes('?'));
    if(tags.length)return{label:`${unknown?'UNKNOWN':'SPECIFIED'} · ${tags.join(' · ')}`,unknown,unavailable:false};
    if(/[@\\/]/.test(info.canonicalSmiles))return{label:'SPECIFIED · ISOMERIC SMILES ENCODING PRESENT',unknown:false,unavailable:false};
    return{label:'ACHIRAL · NO CIP OR E/Z CENTERS PERCEIVED',unknown:false,unavailable:false};
}
function ligandMicrostatePolicy():{protonation:string;tautomer:string;stereo:string}{
    const protonation=(document.getElementById('ligand-ph') as HTMLSelectElement|null)?.value||'unreported',tautomer=(document.getElementById('ligand-tautomers') as HTMLSelectElement|null)?.value||'unreported',stereo=(document.getElementById('ligand-stereo') as HTMLSelectElement|null)?.value||'preserve_block_unknown';
    return{
        protonation:protonation==='enumerate_at_ph'?'SERVER ENUMERATE AT pH 7.4 ± 0.5 · UNVERIFIED UNTIL PREPARED':'INPUT PROTONATION ONLY · SERVER MUST CONFIRM',
        tautomer:tautomer==='enumerate'?'SERVER ENUMERATE + REPORT · UNVERIFIED UNTIL PREPARED':tautomer==='dominant_only'?'SERVER DOMINANT TAUTOMER · UNVERIFIED UNTIL PREPARED':'STRICT INPUT TAUTOMER · PRESERVE',
        stereo,
    };
}
async function validateBuilderLigands():Promise<{rows:Array<{id:string;smiles:string}>;valid:number;charged:number}>{
    const validationId=++ligandValidationGeneration,epoch=draftEpoch,capturedSignature=currentLigandSignature(),inputRows=textLigandRows(),policy=ligandMicrostatePolicy();let valid=0,charged=0,errors=0;const heavy:number[]=[];const rendered:string[]=[];
    for(const row of inputRows){
        let canonical='NOT GENERATED',charge='UNVERIFIED',stereo='UNVERIFIED',outcome='ERROR · INPUT ROW NOT PARSED',rowError=row.parseError||'';
        if(!rowError){
            try{
                const info=await atomInfo(row.smiles);if(validationId!==ligandValidationGeneration||epoch!==draftEpoch||capturedSignature!==currentLigandSignature())return{rows:inputRows.map(({id,smiles})=>({id,smiles})),valid:0,charged:0};const formalCharge=info.charges.reduce((sum,value)=>sum+value,0),stereoState=stereoIdentity(info);canonical=info.canonicalSmiles;charge=`${formalCharge>0?'+':''}${formalCharge} · ${formalCharge===0?'NEUTRAL':'NONZERO FORMAL CHARGE'}`;stereo=stereoState.label;
                if(formalCharge!==0)charged++;heavy.push(info.symbols.filter(symbol=>symbol.toUpperCase()!=='H').length);
                if(stereoState.unavailable)rowError='Browser could not produce a CIP/E-Z witness';
                else if(stereoState.unknown&&policy.stereo==='preserve_block_unknown')rowError='Unknown stereochemistry is blocked by PRESERVE SPECIFIED · BLOCK UNKNOWN';
                else{valid++;outcome=stereoState.unknown?'VALID · UNKNOWN STEREO WILL BE SERVER-ENUMERATED':'VALID · READY FOR SERVER MICROSTATE PREPARATION';}
            }catch(error){rowError=error instanceof Error?error.message:String(error);}
        }
        if(rowError){errors++;outcome=`ERROR · ${rowError}`;}
        rendered.push(`<article class="ligand-identity-row ${rowError?'error':'valid'}"><header><b>${escapeHtml(row.id||`ROW ${row.sourceLine}`)}</b><em>${rowError?'ERROR':'VALID'}</em><small>INPUT LINE ${row.sourceLine}</small></header><dl><div><dt>Input SMILES</dt><dd>${escapeHtml(row.smiles||row.raw)}</dd></div><div><dt>Canonical isomeric SMILES</dt><dd>${escapeHtml(canonical)}</dd></div><div><dt>Formal charge</dt><dd>${escapeHtml(charge)}</dd></div><div><dt>CIP / E-Z</dt><dd>${escapeHtml(stereo)}</dd></div><div><dt>Protonation policy</dt><dd>${escapeHtml(policy.protonation)}</dd></div><div><dt>Tautomer policy</dt><dd>${escapeHtml(policy.tautomer)}</dd></div><div><dt>Policy outcome</dt><dd>${escapeHtml(outcome)}</dd></div></dl></article>`);
    }
    if(validationId!==ligandValidationGeneration||epoch!==draftEpoch||capturedSignature!==currentLigandSignature())return{rows:inputRows.map(({id,smiles})=>({id,smiles})),valid:0,charged:0};
    const target=document.getElementById('ligand-identity-rows');if(target)target.innerHTML=rendered.join('')||'<p>NO LIGAND ROWS · NOTHING VALIDATED</p>';
    validatedLigandSignature=capturedSignature;validatedLigandCount=valid;validatedLigandRowCount=inputRows.length;validatedLigandErrorCount=errors;text('ligand-valid-count',String(valid));text('ligand-charge-count',String(charged));text('ligand-count-label',errors?`${errors} ERROR${errors===1?'':'S'}`:`${valid} VALID`);heavy.sort((a,b)=>a-b);text('ligand-heavy-median',heavy.length?String(heavy[Math.floor(heavy.length/2)]):'—');
    updateBuilderReadiness(valid);
    showBuilderNotice(errors===0&&valid===inputRows.length&&valid>=2?`${valid} complete ligand identities are browser-audited. Server protonation/tautomer work remains explicitly unverified until preparation.`:`${errors} explicit ligand row/policy error${errors===1?'':'s'} · review is blocked until every non-empty row is VALID.`);
    return {rows:inputRows.map(({id,smiles})=>({id,smiles})),valid,charged};
}
function clearCampaign():void{
    if(physicalRunActive()){showBuilderNotice(`Campaign clear refused while RunSet ${activeRunId||'creation receipt'} may be creating or physically active. No browser state or durable receipt was removed.`);return;}
    const planner=readPlannerReceipt();if(planner){showBuilderNotice(`Campaign clear refused while planner receipt ${planner.job_id||planner.owner_token} is unresolved. Reconcile it, or use the explicit archive control; no state was removed.`);syncPlannerRecoveryControl();return;}
    const pending=readPreparationReceipt();
    if(pending&&preparationReceiptBinding(pending).campaignScientificRef.id===draftCampaignId){const binding=preparationReceiptBinding(pending);showBuilderNotice(`Campaign clear refused while durable preparation ${binding.jobId||binding.requestKey||'submission receipt'} is attached. Detach the browser wait if needed, then resume it to a terminal state; the submitted scientific inputs are preserved.`);return;}
    if(pending&&!archiveDetachedPreparationReceipt(pending)){showBuilderNotice('Campaign clear refused because the preparation receipt changed ownership in another tab; no campaign state was removed.');return;}archiveCampaignCache(readCampaignCache(),'interactive_clear_before_new_campaign');copyStorage.remove(plannerOutputReceiptKey);
    if(!detachExecutionContext())return;const pdb=document.getElementById('campaign-pdb') as HTMLInputElement|null,ligands=document.getElementById('campaign-ligands') as HTMLTextAreaElement|null,name=document.getElementById('campaign-name') as HTMLInputElement|null;
    if(pdb)pdb.value='';if(ligands)ligands.value='';if(name)name.value='UNTITLED FEP CAMPAIGN';draftCampaignId=crypto.randomUUID();draftExpectedVersion=0;draftCampaignStateDigest='';draftCampaignScientificGeneration=0;draftCampaignScientificDigest='';draftServerStatus='draft';currentScientificInputs=null;receptorInputReady=false;receptorSourceLabel='NO RECEPTOR';receptorPdbText='';receptorRecord={title:'',method:'model',resolution:null};boundLigands=[];ligandImportErrors.clear();invalidateLigandValidation();populateBoundLigands();resetBuilderProgress();void campaignStateAdapter.clear();
    text('receptor-preview-title','NO RECEPTOR SELECTED');text('receptor-preview-detail','Enter a PDB ID or upload fixed-column PDB coordinates.');text('ligand-valid-count','0');text('ligand-charge-count','0');text('ligand-count-label','0 VALID');text('ligand-heavy-median','—');updateBuilderReadiness(0);showBuilderNotice('Blank campaign ready. Prior offline inputs were archived locally; no old receptor, ligand, network, or transformation was reused.');
}
document.getElementById('main-build')?.addEventListener('click',()=>{setMainMode('build');setBuilderOpen(true);});
document.getElementById('main-review')?.addEventListener('click',()=>{setMainMode('review');setBuilderOpen(false);renderRunJobs();});
document.getElementById('main-runs')?.addEventListener('click',showRunsWorkspace);
document.getElementById('builder-build')?.addEventListener('click',()=>{setMainMode('build');setBuilderOpen(true);});
document.getElementById('close-builder')?.addEventListener('click',()=>{setMainMode('review');setBuilderOpen(false);});
document.getElementById('builder-review')?.addEventListener('click',()=>{setMainMode('review');setBuilderOpen(false);renderRunJobs();});
document.getElementById('builder-runs')?.addEventListener('click',showRunsWorkspace);
document.querySelectorAll<HTMLButtonElement>('[data-target-source]').forEach(button=>button.addEventListener('click',()=>{
    document.querySelectorAll<HTMLButtonElement>('[data-target-source]').forEach(item=>{item.classList.toggle('active',item===button);item.setAttribute('aria-pressed',String(item===button));});
    const mode=button.dataset.targetSource;
    (['pdb','upload','existing'] as const).forEach(source=>{const panel=document.getElementById(`source-${source}-panel`);if(panel)panel.hidden=source!==mode;});
    showBuilderNotice(mode==='pdb'?'Enter a PDB accession; Dirac will retrieve and inspect the experimental structure.':mode==='upload'?'Upload fixed-column PDB coordinates. mmCIF is not accepted in this release. Protein preparation remains a generated backend job, not a prerequisite.':'Reuse a versioned receptor only when it is scientifically the same system.');
}));
document.querySelectorAll<HTMLButtonElement>('[data-choice-group] button').forEach(button=>button.addEventListener('click',()=>selectChoice(button)));
document.querySelectorAll<HTMLButtonElement>('[data-advanced]').forEach(button=>button.addEventListener('click',()=>showBuilderNotice(button.dataset.advanced==='receptor'?'Advanced receptor decisions: missing residues, alternate locations, waters, cofactors, metals, termini, histidines, protonation pH, and preparation release.':'Advanced ligand decisions: pH, formal-charge policy, tautomer/protonation enumeration, stereochemistry, and state-population cutoff.')));
document.getElementById('inspect-pdb')?.addEventListener('click',async()=>{
    if(runContextLocked()){showBuilderNotice(`Receptor fetch refused while RunSet ${activeRunId||'creation receipt'} is attached.`);return;}
    const pdb=(document.getElementById('campaign-pdb') as HTMLInputElement|null)?.value.trim().toUpperCase()||'';
    if(!/^[0-9][A-Z0-9]{3}$/.test(pdb)){showBuilderNotice('A PDB accession starts with a digit and contains four alphanumeric characters. Upload PDB coordinates for private or unpublished structures.');return;}
    const requestId=++pdbFetchGeneration,epoch=draftEpoch,button=document.getElementById('inspect-pdb') as HTMLButtonElement;button.disabled=true;button.textContent='FETCHING…';text('receptor-preview-title',`${pdb} · RETRIEVING EXPERIMENTAL RECORD`);
    try{
        const [response,coordinates]=await Promise.all([fetch(`https://data.rcsb.org/rest/v1/core/entry/${encodeURIComponent(pdb)}`),fetch(`https://files.rcsb.org/download/${encodeURIComponent(pdb)}.pdb`)]);if(!response.ok)throw new Error(`RCSB metadata returned HTTP ${response.status}`);if(!coordinates.ok)throw new Error(`RCSB coordinates returned HTTP ${coordinates.status}`);const record=await response.json() as Record<string,any>,downloadedPdb=await coordinates.text();if(!downloadedPdb.includes('\nATOM  ')&&!downloadedPdb.startsWith('ATOM  '))throw new Error('downloaded record contains no PDB ATOM coordinates');
        if(requestId!==pdbFetchGeneration||epoch!==draftEpoch||(document.getElementById('campaign-pdb') as HTMLInputElement|null)?.value.trim().toUpperCase()!==pdb)return;
        const title=String(record.struct?.title||record.rcsb_entry_info?.structure_determination_methodology||'EXPERIMENTAL STRUCTURE');const method=String(record.exptl?.[0]?.method||'METHOD UNREPORTED');const resolution=record.rcsb_entry_info?.resolution_combined?.[0];
        if(!invalidateScientificState('receptor',`PDB ${pdb} loaded`))return;
        const normalized=method.toUpperCase();receptorPdbText=downloadedPdb;receptorRecord={title,method:normalized.includes('X-RAY')?'xray':normalized.includes('ELECTRON')?'cryoem':normalized.includes('NMR')?'nmr':'model',resolution:Number.isFinite(resolution)?Number(resolution):null};receptorInputReady=true;receptorSourceLabel=pdb;resetBuilderProgress();populateBoundLigands();text('receptor-preview-title',`${pdb} · ${title}`);text('receptor-preview-detail',`${method}${Number.isFinite(resolution)?` · ${Number(resolution).toFixed(2)} Å`:''} · ${boundLigands.length} bound organic candidate${boundLigands.length===1?'':'s'} found`);updateBuilderReadiness();showBuilderNotice(`${pdb} coordinates loaded. Choose the bound ligand that corresponds to Parent; Dirac will preserve that measured pose and align the Proposal core to it.`);
    }catch(error){if(requestId!==pdbFetchGeneration||epoch!==draftEpoch)return;text('receptor-preview-title',`${pdb} · RETRIEVAL FAILED`);text('receptor-preview-detail',receptorPdbText?'Existing receptor was preserved; the failed response was not applied.':'No receptor was accepted. Check the accession or upload raw PDB coordinates.');updateBuilderReadiness();showBuilderNotice(`Could not inspect ${pdb}: ${error instanceof Error?error.message:String(error)}`);}
    finally{if(requestId===pdbFetchGeneration){button.disabled=false;button.textContent='FETCH & INSPECT';}}
});
document.getElementById('validate-ligands')?.addEventListener('click',()=>void validateBuilderLigands());
document.getElementById('campaign-ligands')?.addEventListener('input',()=>{ligandImportErrors.clear();if(executionContextAttached()||builderStage!=='inputs')invalidateScientificState('ligands','ligand series edited');else{advanceDraftEpoch();invalidateLigandValidation();}updateBuilderReadiness();});
document.getElementById('campaign-pdb')?.addEventListener('input',()=>{if((document.getElementById('campaign-pdb') as HTMLInputElement).value.trim().toUpperCase()!==receptorSourceLabel){pdbFetchGeneration+=1;if(executionContextAttached()||builderStage!=='inputs')invalidateScientificState('receptor','receptor accession edited');else{advanceDraftEpoch();receptorInputReady=false;receptorPdbText='';receptorRecord={title:'',method:'model',resolution:null};boundLigands=[];populateBoundLigands();resetBuilderProgress();}updateBuilderReadiness();}});
document.getElementById('ligand-file')?.addEventListener('change',async event=>{
    const input=event.target as HTMLInputElement,file=input.files?.[0];if(!file)return;const textarea=document.getElementById('campaign-ligands') as HTMLTextAreaElement|null;if(!textarea)return;
    const requestId=++ligandFileGeneration,epoch=draftEpoch,initialLigands=textarea.value,extension=file.name.split('.').pop()?.toLowerCase()||'',raw=await file.text(),records=(extension==='mol'?[raw]:raw.split(/\$\$\$\$/)).map(record=>record.trim()).filter(Boolean),existing=new Set(textLigandRows().map(row=>row.id)),lines:string[]=[];let imported=0,failed=0;const RDKit=await getRDKit(),safeBase=file.name.replace(/\.[^.]+$/,'').replace(/[^A-Za-z0-9_.:+-]+/g,'-').replace(/^[^A-Za-z0-9]+/,'').slice(0,90)||'IMPORTED';
    if(requestId!==ligandFileGeneration||epoch!==draftEpoch||textarea.value!==initialLigands){input.value='';return;}
    if(!records.length){const errorId=`${safeBase}-ERROR-001`;lines.push(`${errorId}  INVALID_IMPORT_RECORD_1`);ligandImportErrors.set(errorId,`${file.name}: no non-empty MOL/SDF record was found`);failed++;}
    records.forEach((record,index)=>{let id=(record.split(/\r?\n/)[0]?.trim()||`${safeBase}-${String(index+1).padStart(3,'0')}`).replace(/[^A-Za-z0-9_.:+-]+/g,'-').replace(/^[^A-Za-z0-9]+/,'').slice(0,120)||`${safeBase}-${index+1}`;const base=id;let suffix=2;while(existing.has(id))id=`${base}-${suffix++}`;existing.add(id);let mol:ReturnType<typeof RDKit.get_mol>|null=null;try{mol=RDKit.get_mol(record);if(!mol||!mol.is_valid())throw new Error('RDKit rejected the SDF/MOL record');const smiles=mol.get_smiles();if(!smiles)throw new Error('RDKit returned no canonical isomeric SMILES');lines.push(`${id}  ${smiles}`);imported++;}catch(error){const errorId=`${safeBase}-ERROR-${String(index+1).padStart(3,'0')}`;lines.push(`${errorId}  INVALID_IMPORT_RECORD_${index+1}`);ligandImportErrors.set(errorId,`${file.name} record ${index+1}: ${error instanceof Error?error.message:String(error)}`);failed++;}finally{mol?.delete();}});
    if(!invalidateScientificState('ligands',`${file.name} imported into the ligand series`)){input.value='';return;}textarea.value=[initialLigands.trim(),...lines].filter(Boolean).join('\n');invalidateLigandValidation();await validateBuilderLigands();showBuilderNotice(`${file.name} · ${imported} SDF/MOL record${imported===1?'':'s'} canonicalized locally · ${failed} explicit error${failed===1?'':'s'} · no job submitted.`);input.value='';
});
document.getElementById('receptor-file')?.addEventListener('change',async event=>{const input=event.target as HTMLInputElement,file=input.files?.[0];if(!file)return;const requestId=++receptorFileGeneration,epoch=draftEpoch,raw=await file.text();if(requestId!==receptorFileGeneration||epoch!==draftEpoch){input.value='';return;}if(!raw.split(/\r?\n/).some(line=>line.startsWith('ATOM  '))){showBuilderNotice(`${file.name} does not contain PDB ATOM records. Existing receptor state was preserved and no receptor was accepted.`);updateBuilderReadiness();input.value='';return;}if(!invalidateScientificState('receptor',`${file.name} loaded as a receptor`)){input.value='';return;}receptorPdbText=raw;receptorInputReady=true;receptorSourceLabel=file.name.toUpperCase();receptorRecord={title:file.name,method:'model',resolution:null};resetBuilderProgress();populateBoundLigands();text('receptor-preview-title',`${file.name.toUpperCase()} · RAW STRUCTURE INPUT`);text('receptor-preview-detail',`${(file.size/1024).toFixed(1)} KB · ${boundLigands.length} bound organic candidate${boundLigands.length===1?'':'s'} · backend preparation not started`);updateBuilderReadiness();showBuilderNotice(`${file.name} loaded locally. Choose the crystallographic ligand matching Parent; preparation remains a generated backend operation.`);input.value='';});
document.getElementById('site-select')?.addEventListener('change',()=>{invalidateScientificState('reference','bound reference ligand changed');const reference=selectedReferenceLigand();text('pose-reference-state',reference?reference.label:'NO REFERENCE · ALIGNMENT UNAVAILABLE');updateBuilderReadiness();});
document.getElementById('site-role-filter')?.addEventListener('change',()=>{if(!invalidateScientificState('reference','anchor role filter changed'))return;populateBoundLigands();});
document.getElementById('inspect-existing')?.addEventListener('click',async()=>{
    if(runContextLocked()){showBuilderNotice(`System import refused while RunSet ${activeRunId||'creation receipt'} is attached.`);return;}
    const select=document.getElementById('builder-system-select') as HTMLSelectElement|null,index=select?.value||'',system=index===''?null:builderSystemCatalog[Number(index)]||null;
    if(!system){showBuilderNotice('Select a registered system. Provenance, preparation release, coordinate-frame digest, aligned poses, and campaign scope are inspected before reuse.');return;}
    const fullId=system.prepared_receptor_state_ref.id,scope=system.campaign_scope||'scope_unknown';
    if(scope==='import_stale'){showBuilderNotice(`STALE FOREIGN SYSTEM · ${fullId} · its source scientific generation no longer matches; a new preparation is required and no import is allowed.`);return;}
    if(!system.import_required&&scope!=='import_required'){showBuilderNotice(`${scope.toUpperCase()} · ${system.label} · prepared_receptor_state ${fullId} · source campaign ${system.source_campaign_id||'unreported'} · ${system.execution_eligible?'execution eligible':'not execution eligible'}.`);return;}
    if(draftExpectedVersion<1){showBuilderNotice(`FOREIGN SYSTEM · ${fullId} · save this campaign server-side before an explicit import can be receipted.`);return;}
    if(!window.confirm(`Import prepared system ${fullId} from campaign ${system.source_campaign_id||'UNKNOWN'} into campaign ${draftCampaignId}? This creates a durable cross-campaign import receipt; it does not start FEP.`))return;
    const snapshot=draftIdentitySnapshot(),systemRef={...system.prepared_receptor_state_ref};
    try{const imported=await campaignStateAdapter.importSystem(snapshot.campaign_id,snapshot.audit_version,systemRef,'explicit_user_selected_cross_campaign_system_import');if(runContextLocked()||!draftIdentityMatches(snapshot)||system.prepared_receptor_state_ref.id!==systemRef.id||system.prepared_receptor_state_ref.sha256!==systemRef.sha256)return;draftExpectedVersion=imported.version;draftCampaignStateDigest=imported.stateDigest||'';draftCampaignScientificGeneration=imported.scientificGeneration||0;draftCampaignScientificDigest=imported.scientificDigest||'';writeCampaignCache({...draftFromUi('server-campaign'),expected_version:imported.version,state_digest:draftCampaignStateDigest,campaign_scientific_generation:draftCampaignScientificGeneration,campaign_scientific_digest:draftCampaignScientificDigest},'server-synced');const importedSnapshot=draftIdentitySnapshot();await loadSystemCatalog();if(!draftIdentityMatches(importedSnapshot))return;showBuilderNotice(`IMPORT CONFIRMED · ${fullId} · campaign v${imported.version} · scientific generation ${draftCampaignScientificGeneration} · receipt ${imported.receipt} · no compute job created.`);}
    catch(error){showBuilderNotice(`System import refused · ${error instanceof Error?error.message:String(error)}`);}
});
document.getElementById('draw-ligand')?.addEventListener('click',()=>{document.querySelectorAll('.ligand-actions button').forEach(item=>item.classList.toggle('active',item.id==='draw-ligand'));openMoleculeSketcher();});
document.getElementById('paste-smiles')?.addEventListener('click',()=>{document.querySelectorAll('.ligand-actions button').forEach(item=>item.classList.toggle('active',item.id==='paste-smiles'));(document.getElementById('campaign-ligands') as HTMLTextAreaElement|null)?.focus();});
let draftCampaignId:string=crypto.randomUUID();
let draftExpectedVersion=0;
let draftCampaignStateDigest='';
let draftCampaignScientificGeneration=0;
let draftCampaignScientificDigest='';
let draftEpoch=0;
let ligandValidationGeneration=0;
let currentScientificInputs:Record<string,unknown>|null=null;
let draftServerStatus:CampaignDraftV2['server_status']='draft';
type DraftIdentitySnapshot={epoch:number;session_revision:number;edit_revision:number;editable_signature:string;campaign_id:string;audit_version:number;scientific_generation:number;scientific_digest:string;ligand_signature:string;prepared_system_id:string|null};
function advanceDraftEpoch(owner?:OperationScope):OperationScope|null{draftEpoch+=1;ligandValidationGeneration+=1;return operations.transition(owner);}
function editableDraftSignature():string{return canonicalJson(builderValues());}
function draftRequestStillCurrent(draft:CampaignDraftV2,epoch:number):boolean{return epoch===draftEpoch&&draft.campaign_id===draftCampaignId&&draft.expected_version===draftExpectedVersion&&canonicalJson(draft.values)===editableDraftSignature();}
function draftIdentitySnapshot():DraftIdentitySnapshot{return{epoch:draftEpoch,session_revision:operations.session,edit_revision:operations.edits,editable_signature:editableDraftSignature(),campaign_id:draftCampaignId,audit_version:draftExpectedVersion,scientific_generation:draftCampaignScientificGeneration,scientific_digest:draftCampaignScientificDigest,ligand_signature:currentLigandSignature(),prepared_system_id:preparedCampaignSystem?.prepared_receptor_state_ref.id||null};}
function draftIdentityMatches(snapshot:DraftIdentitySnapshot,{audit=true,system=true,edits=true}:{audit?:boolean;system?:boolean;edits?:boolean}={}):boolean{return snapshot.epoch===draftEpoch&&snapshot.session_revision===operations.session&&(!edits||snapshot.edit_revision===operations.edits&&snapshot.editable_signature===editableDraftSignature())&&snapshot.campaign_id===draftCampaignId&&(!audit||snapshot.audit_version===draftExpectedVersion)&&snapshot.scientific_generation===draftCampaignScientificGeneration&&snapshot.scientific_digest===draftCampaignScientificDigest&&snapshot.ligand_signature===currentLigandSignature()&&(!system||snapshot.prepared_system_id===(preparedCampaignSystem?.prepared_receptor_state_ref.id||null));}
function builderValues():Record<string,string>{
    return Object.fromEntries([...document.querySelectorAll<HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement>('#campaign-builder input[id],#campaign-builder select[id],#campaign-builder textarea[id]')].filter(element=>element.type!=='file').map(element=>[element.id,element.value]));
}
function resetDraftEditableFields():void{
    document.querySelectorAll<HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement>('#campaign-builder input[id],#campaign-builder select[id],#campaign-builder textarea[id]').forEach(element=>{if(element.type==='file')return;if(element instanceof HTMLSelectElement)element.selectedIndex=0;else element.value='';});
}
function assertStrictImportedDraft(draft:CampaignDraftV2):void{
    if(!draft||draft.schema_version!==2||typeof draft.values!=='object'||draft.values===null)throw new Error('Imported campaign is not a complete schema v2 draft');
    const requiredIds=Object.keys(builderValues()),missing=requiredIds.filter(id=>typeof draft.values[id]!=='string');if(missing.length)throw new Error(`Imported campaign is partial; missing ${missing.length} editable field${missing.length===1?'':'s'}: ${missing.slice(0,8).join(', ')}`);
    const strings:Array<[string,unknown]>=[['campaign_id',draft.campaign_id],['name',draft.name],['pdb',draft.pdb],['receptor_pdb',draft.receptor_pdb],['receptor_source',draft.receptor_source],['reference_key',draft.reference_key],['ligands',draft.ligands]];const invalid=strings.filter(([,value])=>typeof value!=='string').map(([key])=>key);if(invalid.length)throw new Error(`Imported campaign has invalid fields: ${invalid.join(', ')}`);
    if(draft.values['campaign-name']!==draft.name||draft.values['campaign-pdb']!==draft.pdb||draft.values['campaign-ligands']!==draft.ligands)throw new Error('Imported campaign duplicates disagree: name, PDB or ligand series has two conflicting values');
    if(draft.receptor_record&&(typeof draft.receptor_record.title!=='string'||!['xray','cryoem','nmr','predicted','model'].includes(draft.receptor_record.method)))throw new Error('Imported receptor record is malformed');
}
function receptorPolicyFromUi():Record<string,unknown>{
    const protonation=(document.getElementById('prep-protonation') as HTMLSelectElement).value;
    return{assembly_id:(document.getElementById('assembly-select') as HTMLSelectElement).value,chain_ids:receptorChainIds(),missing_atoms:(document.getElementById('prep-missing-atoms') as HTMLSelectElement).value,missing_residues:(document.getElementById('prep-missing-residues') as HTMLSelectElement).value,altloc:(document.getElementById('prep-altloc') as HTMLSelectElement).value,occupancy:(document.getElementById('prep-occupancy') as HTMLSelectElement).value,waters:(document.getElementById('prep-waters') as HTMLSelectElement).value,water_site_decisions:[],cofactors:(document.getElementById('prep-cofactors') as HTMLSelectElement).value,metals:(document.getElementById('prep-metals') as HTMLSelectElement).value,histidines:protonation,termini:protonation,ph:7.4,forcefield_contract:{protein:'AMBER ff14SB',ligand:'OpenFF 2.2.1',water:'TIP3P',ionic_strength_molar:.15,release:'openfe-rfe-standard-v1'}};
}
function ligandPolicyFromUi():Record<string,unknown>{return{formal_charge:(document.getElementById('ligand-charge-policy') as HTMLSelectElement).value,tautomer:(document.getElementById('ligand-tautomers') as HTMLSelectElement).value,protonation:(document.getElementById('ligand-ph') as HTMLSelectElement).value,stereochemistry:(document.getElementById('ligand-stereo') as HTMLSelectElement).value,state_population_cutoff:Number((document.getElementById('ligand-state-cutoff') as HTMLSelectElement).value)};}
function scientificInputsFromUi(rows:Array<{id:string;smiles:string}>):Record<string,unknown>|null{
    const reference=selectedReferenceLigand();if(!reference||!receptorPdbText||rows.length<2)return null;
    const parent=rows.find(row=>/^parent(?:-|$)/i.test(row.id))||rows[0];
    return{campaign_name:(document.getElementById('campaign-name') as HTMLInputElement|null)?.value.trim()||'NEW FEP CAMPAIGN',target_name:receptorRecord.title||receptorSourceLabel,source_pdb_id:/^[A-Z0-9]{4}$/.test(receptorSourceLabel)?receptorSourceLabel:'',structure_method:receptorRecord.method,resolution_angstrom:receptorRecord.resolution,receptor_pdb:receptorPdbText,compounds:rows,parent_id:parent.id,pose_strategy:'align_to_reference',reference_ligand:{resname:reference.resname,chain:reference.chain,residue_number:reference.residue_number,role:reference.role==='ligand'?'experimental_ligand':'cofactor',altloc:null,occupancy:null},minimum_core_coverage:.5,seed:20260816,receptor_policy:receptorPolicyFromUi(),ligand_policy:ligandPolicyFromUi()};
}
function draftFromUi(origin:CampaignDraftV2['origin']='local-draft'):CampaignDraftV2{return{schema_version:2,campaign_id:draftCampaignId,saved_at:new Date().toISOString(),origin,name:(document.getElementById('campaign-name') as HTMLInputElement|null)?.value||'',pdb:(document.getElementById('campaign-pdb') as HTMLInputElement|null)?.value||'',receptor_pdb:receptorPdbText,receptor_source:receptorSourceLabel,receptor_record:receptorRecord,reference_key:(document.getElementById('site-select') as HTMLSelectElement|null)?.value||'',ligands:(document.getElementById('campaign-ligands') as HTMLTextAreaElement|null)?.value||'',builder_stage:builderStage,server_status:draftServerStatus,expected_version:draftExpectedVersion,state_digest:draftCampaignStateDigest,campaign_scientific_generation:draftCampaignScientificGeneration,campaign_scientific_digest:draftCampaignScientificDigest,values:builderValues(),...(currentScientificInputs?{scientific_inputs:currentScientificInputs}:{}),prepared_system_id:preparedCampaignSystem?.prepared_receptor_state_ref.id,network_job_id:activeCampaignContext()?.network_job_id};}
async function restoreDraft(draft:CampaignDraftV2,explicitImport=false,ownerScope:OperationScope=operations.begin('restore-draft'),preserveRun=false):Promise<OperationScope|null>{
    if(draft.schema_version!==2)throw new Error('Unsupported campaign draft schema');if(explicitImport)assertStrictImportedDraft(draft);
    if(!operations.current(ownerScope,{edits:true}))return null;const detached=detachExecutionContext(ownerScope,{preserveRun});if(!detached||detached===true)throw new Error('Draft restore refused while an active RunSet is attached');const restoreScope=detached;
    const restoreEpoch=draftEpoch,sourceCampaignId=draft.campaign_id||'UNIDENTIFIED';resetDraftEditableFields();draftCampaignId=explicitImport?crypto.randomUUID():(draft.campaign_id||crypto.randomUUID());draftExpectedVersion=explicitImport?0:(Number.isInteger(draft.expected_version)?draft.expected_version:0);draftCampaignStateDigest=explicitImport?'':(draft.state_digest||'');draftCampaignScientificGeneration=explicitImport?0:(Number.isInteger(draft.campaign_scientific_generation)?Number(draft.campaign_scientific_generation):0);draftCampaignScientificDigest=explicitImport?'':(draft.campaign_scientific_digest||'');draftServerStatus=explicitImport?'draft':(draft.server_status||'draft');Object.entries(draft.values||{}).forEach(([id,value])=>{const element=document.getElementById(id) as HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement|null;if(element&&element.type!=='file'&&typeof value==='string')element.value=value;});
    const name=document.getElementById('campaign-name') as HTMLInputElement|null,pdb=document.getElementById('campaign-pdb') as HTMLInputElement|null,ligands=document.getElementById('campaign-ligands') as HTMLTextAreaElement|null;if(name)name.value=draft.name||'UNTITLED FEP CAMPAIGN';if(pdb)pdb.value=draft.pdb||'';if(ligands)ligands.value=draft.ligands||'';
    receptorPdbText=draft.receptor_pdb||'';receptorSourceLabel=draft.receptor_source||draft.pdb||'NO RECEPTOR';receptorRecord=draft.receptor_record||{title:'',method:'model',resolution:null};currentScientificInputs=explicitImport?null:(draft.scientific_inputs||null);receptorInputReady=!!receptorPdbText;builderStage='inputs';preparedCampaignSystem=null;populateBoundLigands();const site=document.getElementById('site-select') as HTMLSelectElement|null;if(site&&draft.reference_key&&[...site.options].some(option=>option.value===draft.reference_key))site.value=draft.reference_key;
    invalidateLigandValidation();reflectBuilderStage();updateBuilderReadiness();if(draft.ligands)await validateBuilderLigands();if(restoreEpoch!==draftEpoch||!operations.current(restoreScope,{edits:true}))throw new Error('Draft restore was superseded by a newer campaign edit');text('receptor-preview-title',receptorInputReady?`${receptorSourceLabel} · RESTORED RAW STRUCTURE`:'NO RECEPTOR SELECTED');text('receptor-preview-detail',receptorInputReady?'Draft coordinates restored locally; generated receptor, poses and network must be requalified.':'Draft contains no restorable receptor coordinates.');
    showBuilderNotice(`${explicitImport?'Cross-campaign inputs imported into NEW campaign':'Draft resume'} complete · ${explicitImport?`${sourceCampaignId} → ${draftCampaignId}`:draftCampaignId} · generated evidence was not silently rebound.`);
    return restoreScope;
}
document.getElementById('save-draft')?.addEventListener('click',async event=>{
    const button=event.currentTarget as HTMLButtonElement;if(button.disabled)return;const scope=operations.begin('save-draft');button.disabled=true;button.textContent='SAVING…';
    try{
        const checked=await validateBuilderLigands();if(!operations.current(scope,{edits:true}))return;if(checked.valid===checked.rows.length&&checked.valid>=2){currentScientificInputs=scientificInputsFromUi(checked.rows);}const snapshot=draftIdentitySnapshot(),draft=draftFromUi();
        const receipt=await campaignStateAdapter.save(draft);if(!operations.current(scope,{edits:true})||!draftIdentityMatches(snapshot))return;draftExpectedVersion=receipt.version;draftCampaignStateDigest=receipt.stateDigest||'';draftCampaignScientificGeneration=receipt.scientificGeneration||0;draftCampaignScientificDigest=receipt.scientificDigest||'';draftServerStatus=receipt.status||draftServerStatus;
        if(receipt.durability==='server'){await loadSystemCatalog();text('builder-next-label',`SERVER-SAVED · CAMPAIGN V${receipt.version}`);showBuilderNotice(`SERVER-DURABLE draft saved · ${receipt.receipt} · no compute job created.`);}
        else{text('builder-next-label','OFFLINE CACHE ONLY · SERVER SAVE REQUIRED');showBuilderNotice(`${receipt.warning} · ${receipt.receipt} · local cache is recoverable but is not a durable campaign receipt.`);}
    }catch(error){const message=error instanceof Error?error.message:String(error);text('builder-next-label',message.startsWith('VERSION CONFLICT')?'VERSION CONFLICT · RESUME + REAPPLY':'DRAFT SAVE REFUSED');showBuilderNotice(`Draft save failed · ${message}`);}
    finally{button.disabled=false;button.textContent='SAVE DRAFT';}
});
document.getElementById('resume-draft')?.addEventListener('click',async event=>{
    const button=event.currentTarget as HTMLButtonElement;if(button.disabled)return;const scope=operations.begin('resume-draft');button.disabled=true;button.textContent='RESUMING…';
    try{
        const loaded=await campaignStateAdapter.load();if(!operations.current(scope,{edits:true}))return;if(!loaded){showBuilderNotice('No server campaign or offline cache exists in this campaign copy.');return;}
        const restored=await restoreDraft(loaded.draft,false,scope);if(!restored||!operations.current(restored,{edits:true}))return;draftExpectedVersion=loaded.version;draftCampaignStateDigest=loaded.stateDigest||loaded.draft.state_digest||'';draftCampaignScientificGeneration=loaded.scientificGeneration||loaded.draft.campaign_scientific_generation||0;draftCampaignScientificDigest=loaded.scientificDigest||loaded.draft.campaign_scientific_digest||'';
        if(loaded.source==='server'){await loadSystemCatalog();if(!operations.current(restored,{edits:true}))return;text('builder-next-label',`SERVER-RESTORED · CAMPAIGN V${loaded.version}`);showBuilderNotice(`SERVER-DURABLE campaign restored · ${loaded.draft.campaign_id} · v${loaded.version} · generated evidence was not silently rebound.`);}
        else{text('builder-next-label','OFFLINE CACHE RESTORED · NOT SERVER-DURABLE');showBuilderNotice(`${loaded.warning||'NOT SERVER-DURABLE'} · offline inputs restored; save to the server before preparation or import.`);}
    }catch(error){showBuilderNotice(`Draft resume failed · ${error instanceof Error?error.message:String(error)}`);}
    finally{button.disabled=false;button.textContent='RESUME DRAFT';}
});
document.getElementById('import-campaign-file')?.addEventListener('change',async event=>{const input=event.currentTarget as HTMLInputElement,file=input.files?.[0];if(!file)return;const scope=operations.begin('import-draft');try{const draft=JSON.parse(await file.text()) as CampaignDraftV2;if(!operations.current(scope,{edits:true}))return;assertStrictImportedDraft(draft);if(!window.confirm(`Import raw inputs from campaign ${draft.campaign_id||'UNKNOWN'} into a NEW campaign? Current inputs will be replaced; server IDs, versions, digests and generated evidence will not be reused.`)){input.value='';return;}if(!operations.current(scope,{edits:true}))return;draft.origin='cross-campaign-import';await restoreDraft(draft,true,scope);}catch(error){showBuilderNotice(`Campaign import refused · ${error instanceof Error?error.message:String(error)}`);}finally{input.value='';}});
campaignBuilder?.addEventListener('input',event=>{const element=event.target as HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement;if(element.type!=='file')operations.edit();});
campaignBuilder?.addEventListener('change',event=>{const element=event.target as HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement;if(element.type!=='file'&&!(element instanceof HTMLTextAreaElement)&&element.type!=='text')operations.edit();});
document.querySelectorAll<HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement>('#campaign-builder [data-invalidate]').forEach(element=>element.addEventListener('change',()=>{if(element.id==='site-select'||element.id==='site-role-filter'||element.id==='ligand-file')return;const scope=element.dataset.invalidate||'input';if(scope==='portfolio'){updateBuilderReadiness();return;}invalidateScientificState(scope,`${element.id} changed`);}));
document.getElementById('clear-campaign')?.addEventListener('click',clearCampaign);
document.getElementById('archive-planner-receipt')?.addEventListener('click',()=>{const receipt=readPlannerReceipt();if(!receipt||plannerInFlight)return;const identity=receipt.job_id||receipt.owner_token;if(!window.confirm(`Archive planner receipt ${identity} as detached history? This does not cancel or retry any server job.`))return;archiveDetachedPlannerReceipt(receipt,receipt.job_id?'explicit_detach_after_reconciliation_failure':'explicit_unknown_submission_archive');text('builder-next-label',`PLANNER RECEIPT ${identity} · ARCHIVED DETACHED · NO CANCEL / RETRY CLAIMED`);showBuilderNotice(`Planner receipt ${identity} was archived as detached history. No server cancellation, failure, or safe retry is claimed.`);syncPlannerRecoveryControl();updateBuilderReadiness();});
document.getElementById('cancel-preparation')?.addEventListener('click',()=>void requestPreparationCancel());
document.getElementById('detach-preparation')?.addEventListener('click',detachPreparationWait);
type PreparationCancelState='idle'|'requested'|'confirmed'|'failed'|'detached';
let preparationInFlight=false;
let preparationOperationId=0;
let preparationScope:OperationScope|null=null;
let preparationJobId='';
let preparationCancelState:PreparationCancelState='idle';
let preparationWaitController:AbortController|null=null;
let preparationDetachWait:(()=>void)|null=null;
function preparationInputSignature(inputs:Record<string,unknown>|null):string{return inputs?canonicalJson(inputs):'';}
function preparationReceiptMatchesCurrent(receipt:PreparationReceipt):boolean{return preparationReceiptMatchesOpenCampaign(receipt,{campaignId:draftCampaignId,auditVersion:draftExpectedVersion,scientificGeneration:draftCampaignScientificGeneration,scientificDigest:draftCampaignScientificDigest,inputSignature:preparationInputSignature(currentScientificInputs)});}
async function applyPreparationResult(data:Record<string,any>,receipt:AcknowledgedPreparationReceipt,seconds?:number):Promise<void>{
    const jobId=receipt.job_id;if(!preparationReceiptMatchesCurrent(receipt))throw new Error(`preparation job ${jobId} no longer matches the open campaign inputs`);const exact=exactPreparationResultFrom(data,receipt);if(!preparationResultMatchesOpenCampaign(receipt,exact,{campaignId:draftCampaignId,auditVersion:draftExpectedVersion,auditDigest:draftCampaignStateDigest,scientificGeneration:draftCampaignScientificGeneration,scientificDigest:draftCampaignScientificDigest,inputSignature:preparationInputSignature(currentScientificInputs)}))throw new Error(`preparation job ${jobId} returned a different committed campaign revision`);const candidate=preparedSystemFromPreparationResult(data,exact),outputVersion=exact.auditVersion,outputStateDigest=exact.auditDigest,outputScientificGeneration=exact.scientificGeneration,outputScientificDigest=exact.scientificDigest;if(!removePreparationReceiptIf(receipt))throw new Error('preparation result lost receipt ownership before atomic acceptance');receiptStore.markPreparationTerminal(jobId);
    preparedCampaignSystem=candidate;draftExpectedVersion=outputVersion;draftCampaignStateDigest=outputStateDigest;draftCampaignScientificGeneration=outputScientificGeneration;draftCampaignScientificDigest=outputScientificDigest;draftServerStatus='prepared';systemCatalog=[preparedCampaignSystem,...systemCatalog.filter(system=>system.prepared_receptor_state_ref.id!==preparedCampaignSystem!.prepared_receptor_state_ref.id)];executionContract.system=null;invalidatePreparedSystem();renderPreparationPolicyDom(document,preparationPolicyViewFrom(preparedCampaignSystem));
    const select=document.getElementById('system-select') as HTMLSelectElement;select.innerHTML='<option value="">SELECT PREPARED SYSTEM…</option>'+systemCatalog.map((system,index)=>`<option value="${index}">${escapeHtml(system.label)} · ${system.poses.length} ALIGNED POSES</option>`).join('');select.value='0';
    const rmsds=preparedCampaignSystem.poses.map(pose=>pose.core_rmsd_angstrom).filter((value):value is number=>Number.isFinite(value));const maxRmsd=rmsds.length?Math.max(...rmsds):NaN;const minimumCoverage=Math.min(...preparedCampaignSystem.poses.map(pose=>pose.core_coverage||0));
    const policy=preparationPolicyGate(preparedCampaignSystem);builderStage='prepared';reflectBuilderStage();markPipelineReadyDom(document,'receptor',policy.ok?'BUILT + POLICY CONFIRMED':'BUILT · POLICY BLOCKED');markPipelineReadyDom(document,'states',`${preparedCampaignSystem.poses.length} STATES`);markPipelineReadyDom(document,'poses','ALIGNED · REVIEW');
    const generated=document.getElementById('generated-gate');generated?.classList.remove('pending','pass','blocked');generated?.classList.add(policy.ok?'pass':'blocked');const icon=generated?.querySelector('i'),detail=generated?.querySelector('small');if(icon)icon.textContent=policy.ok?'✓':'!';if(detail)detail.textContent=policy.ok?`Prepared receptor + ${preparedCampaignSystem.poses.length} aligned pose hypotheses · human review pending`:`${policy.blockers.length} preparation policy axis${policy.blockers.length===1?'':'es'} unresolved · pose acceptance locked`;
    text('pose-review-state',`${policy.ok?'PENDING':'LOCKED · POLICY UNRESOLVED'} · MAX CORE RMSD ${Number.isFinite(maxRmsd)?maxRmsd.toFixed(2):'—'} Å · MIN COVERAGE ${(minimumCoverage*100).toFixed(0)}%`);text('proposed-system-title',`${preparedCampaignSystem.label} · ${preparedCampaignSystem.poses.length} POSES · ${policy.ok?'REVIEW PENDING':'POLICY BLOCKED'}`);text('system-status',`${preparedCampaignSystem.poses.length} NEW POSES · SERVER-ATTESTED · ${policy.ok?'REVIEW PENDING':`${policy.blockers.length} POLICY BLOCKERS`}`);text('builder-next-label',`PREP JOB ${jobId} · ${Number.isFinite(seconds)?`${Number(seconds).toFixed(1)}s · `:''}DONE · REVIEW POLICY + EVERY POSE`);(document.getElementById('review-inputs') as HTMLButtonElement).textContent='REVIEW POLICY + POSES →';
    writeCampaignCache({...draftFromUi('server-campaign'),expected_version:draftExpectedVersion,state_digest:draftCampaignStateDigest,campaign_scientific_generation:draftCampaignScientificGeneration,campaign_scientific_digest:draftCampaignScientificDigest,builder_stage:'prepared',server_status:'prepared'},'server-synced');
    showBuilderNotice(policy.ok?`Durable preparation job ${jobId} completed. Prepared receptor and ${preparedCampaignSystem.poses.length} receptor-frame poses were versioned; Parent keeps crystallographic coordinates and analogues are constrained to the shared crystal core.`:`Durable preparation job ${jobId} completed with inspectable artifacts, but acceptance is fail-closed: ${policy.blockers.map(row=>`${row.axis}=${row.verdict}`).join(' · ')}.`);
}
async function waitForPreparation(accepted:Envelope,receipt:AcknowledgedPreparationReceipt,operationId:number,scope:OperationScope):Promise<void>{
    const button=document.getElementById('review-inputs') as HTMLButtonElement;preparationInFlight=true;preparationJobId=receipt.job_id;preparationCancelState='idle';button.disabled=true;button.textContent='PREPARATION JOB ACTIVE';renderPreparationControlsDom(document,true,true,preparationCancelState);
    const sameOperation=()=>operationId===preparationOperationId&&preparationScope?.id===scope.id&&operations.current(scope,{edits:true}),current=()=>sameOperation()&&preparationReceiptMatchesCurrent(receipt),updateElapsed=()=>{if(current()&&preparationCancelState==='idle')text('builder-next-label',`PREP ${receipt.job_id} · ${preparationElapsedSeconds(receipt)}s · DURABLE · CANCEL REQUEST OR DETACH AVAILABLE`);};updateElapsed();const progressTimer=window.setInterval(updateElapsed,1000);
    const controller=new AbortController();preparationWaitController=controller;
    try{
        const done=await Promise.race([
            client.waitForCommandResult(accepted,900,controller.signal).catch(error=>{if(controller.signal.aborted)return null;throw error;}),
            new Promise<null>(resolve=>{preparationDetachWait=()=>resolve(null);}),
        ]);if(sameOperation())preparationDetachWait=null;if(!current())return;
        if(!done){showBuilderNotice(`Detached locally from preparation job ${receipt.job_id}. The durable receipt is preserved; no server cancellation is claimed.`);return;}
        if(!done.ok){const status=await client.jobGet(receipt.job_id),state=String(status.data?.state||'unknown');if(['failed','cancelled'].includes(state)){removePreparationReceiptIf(receipt);receiptStore.markPreparationTerminal(receipt.job_id);}throw new Error(`${state.toUpperCase()} · ${done.error?.user_message||done.error?.message||'preparation did not complete'}`);}
        await applyPreparationResult(done.data||{},receipt,Number(done.meta?.seconds));
    }catch(error){if(!current())return;builderStage='reviewed';reflectBuilderStage();text('builder-next-label',`PREP JOB ${receipt.job_id} · INPUTS PRESERVED · FAILED OR STILL QUERYABLE`);text('pose-review-state','NOT GENERATED');const preserved=!!readPreparationReceipt();showBuilderNotice(`Preparation job ${receipt.job_id}: ${error instanceof Error?error.message:String(error)}. Scientific inputs remain unchanged and the full receipt is ${preserved?'preserved for resume':'recorded as terminal'}.`);button.textContent=preserved?'RESUME PREPARATION JOB':'RETRY RECEPTOR + POSES →';
    }finally{window.clearInterval(progressTimer);if(sameOperation()){preparationWaitController=null;preparationDetachWait=null;preparationInFlight=false;preparationJobId='';preparationScope=null;renderPreparationControlsDom(document,false,false,preparationCancelState);button.disabled=false;updateBuilderReadiness();const pending=readPreparationReceipt();if(pending){button.textContent='RESUME PREPARATION JOB';text('builder-next-label',`PREP RECEIPT ${pending.job_id||preparationReceiptBinding(pending).requestKey} · ${preparationElapsedSeconds(pending)}s · RESUME OR LEAVE RUNNING`);}}}
}
async function resumePendingPreparation():Promise<boolean>{
    let receipt=readPreparationReceipt();if(!receipt)return false;
    const button=document.getElementById('review-inputs') as HTMLButtonElement;if(preparationInFlight)return true;
    try{
        let binding=preparationReceiptBinding(receipt),scope=operations.begin('preparation');const serverDraft=await loadServerCampaignById(binding.campaignScientificRef.id);if(!operations.current(scope,{edits:true}))return true;if(![binding.inputVersion,binding.inputVersion+1].includes(serverDraft.expected_version))throw new Error(`server campaign is v${serverDraft.expected_version}; preparation receipt was submitted from v${binding.inputVersion}`);const adopted=await restoreDraft(serverDraft,false,scope);if(!adopted)return true;scope=adopted;
        if(receipt.schema_version===3){const claimed=receiptStore.claimPreparation(receipt,scope.id,new Date().toISOString());if(!claimed)throw new Error('preparation receipt ownership changed in another tab');receipt=claimed;binding=preparationReceiptBinding(receipt);}
        const checked=await validateBuilderLigands();if(!operations.current(scope,{edits:true}))return true;if(checked.valid!==checked.rows.length||checked.valid<2)throw new Error('receipt campaign ligands no longer validate');currentScientificInputs=scientificInputsFromUi(checked.rows);if(preparationInputSignature(currentScientificInputs)!==binding.inputSignature)throw new Error('receipt scientific inputs do not match the server-restored campaign');
        const operationId=++preparationOperationId;preparationScope=scope;builderStage='reviewed';reflectBuilderStage();button.disabled=true;button.textContent=receipt.job_id?'RECONNECTING PREPARATION JOB…':'RECOVERING PREPARATION ACK…';text('builder-next-label',`RECONNECTING · ${receipt.job_id||binding.requestKey} · ${preparationElapsedSeconds(receipt)}s`);
        if(!acknowledgedPreparationReceipt(receipt)){
            if(receipt.schema_version!==3||!currentScientificInputs)throw new Error('preparation receipt has no replayable request key');
            const submitted=await submitPreparationExactlyOnce(client,receiptStore,receipt,currentScientificInputs,()=>operations.current(scope,{edits:true})&&preparationScope?.id===scope.id);if(!submitted)return true;receipt=submitted.receipt;
            await waitForPreparation(submitted.accepted,submitted.receipt,operationId,scope);return true;
        }
        const status=await client.jobGet(receipt.job_id);if(!status.ok)throw new Error(envelopeFailure(status));const state=String(status.data?.state||'unknown');
        if(['failed','cancelled'].includes(state)){removePreparationReceiptIf(receipt);receiptStore.markPreparationTerminal(receipt.job_id);button.disabled=false;button.textContent='RETRY RECEPTOR + POSES →';text('builder-next-label',`PREP JOB ${receipt.job_id} · ${state.toUpperCase()} · INPUTS PRESERVED`);showBuilderNotice(`Preparation job ${receipt.job_id} ended ${state}; saved campaign inputs were restored and no preparation success is claimed.`);return true;}
        await waitForPreparation({ok:true,meta:{job_id:receipt.job_id}},receipt,operationId,scope);return true;
    }catch(error){preparationInFlight=false;preparationScope=null;button.disabled=false;button.textContent='RESUME PREPARATION JOB';const binding=preparationReceiptBinding(receipt),identity=receipt.job_id||binding.requestKey||'UNKNOWN RECEIPT';text('builder-next-label',`PREP RECEIPT PRESERVED · ${identity} · SERVER STATUS UNAVAILABLE`);showBuilderNotice(`Could not reconnect to ${identity}: ${error instanceof Error?error.message:String(error)}. The exact receipt and scientific inputs were preserved; no cancellation, failure, or new attempt is inferred.`);return true;}
}
async function requestPreparationCancel():Promise<void>{
    const receipt=readPreparationReceipt(),jobId=preparationJobId||receipt?.job_id||'',cancelButton=document.getElementById('cancel-preparation') as HTMLButtonElement|null;
    if(!fullJobId(jobId)){showBuilderNotice('No complete preparation Job ID is available. No cancellation command was sent; use DETACH PREP WAIT to stop only this browser wait.');return;}
    preparationCancelState='requested';if(cancelButton){cancelButton.disabled=true;cancelButton.textContent='CANCEL REQUESTED…';}text('builder-next-label',`CANCEL REQUEST SENT · ${jobId} · AWAITING SERVER RECEIPT`);
    try{
        const response=await client.execute('job.cancel',{job_ref:{kind:'job',id:jobId}});if(!response.ok)throw new Error(envelopeFailure(response));
        const state=String(response.data?.state||'unknown'),cancel=response.data?.cancel as Record<string,unknown>|undefined,requestAccepted=cancel?.requested===true||state==='cancelled';if(!requestAccepted&&!['done','failed'].includes(state))throw new Error('server returned no cancellation-request acknowledgement');
        preparationCancelState='confirmed';const capability=String(cancel?.capability||'unreported'),terminal=['done','failed','cancelled'].includes(state),terminalPending=Boolean(cancel?.terminal_pending)||!terminal;
        text('builder-next-label',`CANCEL REQUEST RECEIVED · ${jobId} · ${state.toUpperCase()} · ${capability}${terminalPending?' · TERMINAL PENDING':''}`);showBuilderNotice(`Server received the cancellation request for ${jobId}. Current state: ${state}; capability: ${capability}. This does not claim an active native preparation phase was interrupted; its durable terminal state remains authoritative.`);
        if(cancelButton){cancelButton.textContent=terminalPending?'CANCEL RECEIVED · WAITING':'CANCEL RECEIVED';cancelButton.disabled=true;}
        if(state==='cancelled'||state==='failed'){if(receipt)removePreparationReceiptIf(receipt);receiptStore.markPreparationTerminal(jobId);preparationWaitController?.abort();preparationDetachWait?.();}
    }catch(error){preparationCancelState='failed';if(cancelButton){cancelButton.disabled=false;cancelButton.textContent='CANCEL FAILED · RETRY';}text('builder-next-label',`CANCEL FAILED · ${jobId} · JOB MAY STILL RUN`);showBuilderNotice(`Cancellation request failed for ${jobId}: ${error instanceof Error?error.message:String(error)}. The preparation wait and durable receipt remain active; no cancellation is claimed.`);}
}
function detachPreparationWait():void{
    const receipt=readPreparationReceipt(),jobId=preparationJobId||receipt?.job_id||'';preparationCancelState='detached';preparationWaitController?.abort();preparationDetachWait?.();renderPreparationControlsDom(document,false,fullJobId(jobId),preparationCancelState);text('builder-next-label',`${jobId?`DETACHED FROM ${jobId}`:'PREPARATION WAIT DETACHED'} · RECEIPT PRESERVED · NO CANCEL CLAIMED`);showBuilderNotice(`Stopped only this browser wait${jobId?` for ${jobId}`:''}. The durable preparation job was not cancelled and can be reattached after refresh or with RESUME PREPARATION JOB.`);
}
async function prepareCampaignSources():Promise<void>{
    const button=document.getElementById('review-inputs') as HTMLButtonElement,scope=operations.begin('preparation'),checked=await validateBuilderLigands();if(!operations.current(scope,{edits:true}))return;const reference=selectedReferenceLigand();
    if(preparationInFlight)return;
    const pendingReceipt=readPreparationReceipt();if(pendingReceipt&&preparationReceiptBinding(pendingReceipt).campaignScientificRef.id===draftCampaignId){await resumePendingPreparation();return;}if(pendingReceipt&&!archiveDetachedPreparationReceipt(pendingReceipt)){showBuilderNotice('Preparation refused because another tab changed the durable preparation receipt; no new request key was created.');return;}
    if(checked.valid!==checked.rows.length||checked.valid<2||!receptorPdbText||!reference){showBuilderNotice('Preparation refused: receptor coordinates, a bound reference ligand, Parent, and at least one analogue are required.');return;}
    const waterPolicy=(document.getElementById('prep-waters') as HTMLSelectElement).value;if(waterPolicy==='review_pocket'){showBuilderNotice('Preparation refused: REVIEW POCKET WATERS is locked until every water has identity, residue, occupancy, B-factor, distance, keep/remove and reason. Choose REMOVE ALL or KEEP ALL + SERVER POLICY GATE.');return;}
    const poseChoice=document.querySelector<HTMLButtonElement>('[data-choice-group="pose"] button.active')?.dataset.choice||'align';if(poseChoice!=='align'){showBuilderNotice('This connected release prepares reference-aligned analogues. Select ALIGN TO REFERENCE; docking and imported-pose review remain separate workflows.');return;}
    currentScientificInputs=scientificInputsFromUi(checked.rows);if(!currentScientificInputs){showBuilderNotice('Preparation refused: complete scientific inputs could not be sealed.');return;}const frozenInputs=JSON.parse(JSON.stringify(currentScientificInputs)) as Record<string,unknown>,inputSignature=preparationInputSignature(frozenInputs),submissionCampaignId=draftCampaignId;let operationId=0,snapshot:DraftIdentitySnapshot|null=null,receipt:DurablePreparationReceipt|null=null;
    button.disabled=true;button.textContent='SUBMITTING PREPARATION JOB…';text('builder-next-label','SUBMITTING · DURABLE RECEPTOR + POSE JOB');text('pose-review-state','NOT YET GENERATED');
    try{
        if(draftExpectedVersion<1||!/^sha256:[0-9a-f]{64}$/.test(draftCampaignStateDigest)){
            const saved=await campaignStateAdapter.save(draftFromUi());
            if(!operations.current(scope,{edits:true})||submissionCampaignId!==draftCampaignId||inputSignature!==preparationInputSignature(currentScientificInputs))return;
            if(saved.durability!=='server'||!saved.stateDigest)throw new Error('A server-durable campaign is required before scientific preparation');
            draftExpectedVersion=saved.version;draftCampaignStateDigest=saved.stateDigest;draftCampaignScientificGeneration=saved.scientificGeneration||0;draftCampaignScientificDigest=saved.scientificDigest||'';draftServerStatus=saved.status||'draft';
        }
        const chains=receptorChainIds();if(!chains.length)throw new Error('receptor contains no polymer chain identifiers');
        snapshot=draftIdentitySnapshot();operationId=++preparationOperationId;preparationScope=scope;preparationInFlight=true;
        const submitted=await withCampaignPreparationLock(snapshot.campaign_id,async()=>{if(!operations.current(scope,{edits:true})||preparationScope?.id!==scope.id||operationId!==preparationOperationId||!snapshot||!draftIdentityMatches(snapshot)||inputSignature!==preparationInputSignature(currentScientificInputs))return null;const concurrent=readPreparationReceipt();if(concurrent)throw new Error(`another tab already owns preparation ${preparationReceiptBinding(concurrent).jobId||preparationReceiptBinding(concurrent).requestKey||'receipt'}`);receipt=createStoredPreparationReceipt(receiptStore,{campaignId:snapshot.campaign_id,auditVersion:snapshot.audit_version,scientificGeneration:snapshot.scientific_generation,scientificDigest:snapshot.scientific_digest},inputSignature,scope.id,crypto.randomUUID());return submitPreparationExactlyOnce(client,receiptStore,receipt,frozenInputs,()=>operations.current(scope,{edits:true})&&preparationScope?.id===scope.id&&operationId===preparationOperationId&&!!snapshot&&draftIdentityMatches(snapshot)&&inputSignature===preparationInputSignature(currentScientificInputs));});if(!submitted){if(preparationScope?.id===scope.id){preparationInFlight=false;preparationScope=null;const pending=readPreparationReceipt();button.disabled=false;button.textContent=pending?'RESUME PREPARATION JOB':'RETRY RECEPTOR + POSES →';updateBuilderReadiness(checked.valid);}return;}receipt=submitted.receipt;await waitForPreparation(submitted.accepted,submitted.receipt,operationId,scope);
    }catch(error){if(operationId&&(operationId!==preparationOperationId||preparationScope?.id!==scope.id||!operations.current(scope,{edits:true})))return;const pending=readPreparationReceipt();builderStage='reviewed';reflectBuilderStage();text('builder-next-label',pending?'PREPARATION ACK UNKNOWN · EXACT RECEIPT PRESERVED':'PREPARATION SUBMISSION REFUSED · INPUTS PRESERVED');text('pose-review-state','NOT GENERATED');showBuilderNotice(`Preparation submission ${pending?'needs exact-key reconciliation':'was refused'}: ${error instanceof Error?error.message:String(error)}${pending?' · RESUME replays the same request key; it does not create a new logical attempt.':''}`);button.textContent=pending?'RESUME PREPARATION JOB':'RETRY RECEPTOR + POSES →';button.disabled=false;preparationInFlight=false;preparationScope=null;updateBuilderReadiness(checked.valid);}
}
async function createCampaignNetwork():Promise<void>{
    const button=document.getElementById('review-inputs') as HTMLButtonElement;
    if(plannerInFlight){await requestPlannerStop(button);return;}
    const pendingPlanner=readPlannerReceipt();if(pendingPlanner){showBuilderNotice(`Network planning refused: durable planner receipt ${pendingPlanner.job_id||pendingPlanner.owner_token} is unresolved. Resume/reload it or start a new campaign to archive it; no duplicate planner job was submitted.`);return;}
    const scope=operations.begin('planner'),checked=await validateBuilderLigands();if(!operations.current(scope,{edits:true}))return;
    if(checked.valid!==checked.rows.length||checked.valid<2){showBuilderNotice('Campaign creation refused: every ligand row must parse and at least two valid identities are required.');return;}if(!preparedCampaignSystem||builderStage!=='accepted'){showBuilderNotice('Review and accept every receptor-frame pose before network planning.');return;}
    const preparedCompounds=preparedCampaignSystem.poses.map(pose=>({id:pose.label,smiles:pose.canonical_smiles})),snapshot=draftIdentitySnapshot(),preparedSystemRef=contentRef(preparedCampaignSystem.prepared_receptor_state_ref,'prepared_receptor_state');
    if(preparedCompounds.length<2||preparedCompounds.some(row=>!row.id||!row.smiles)){showBuilderNotice('Campaign creation refused: the prepared pose ensemble has no complete canonical ligand series.');return;}
    plannerInFlight=true;plannerOwnerToken=scope.id;plannerCancelState='idle';plannerJobId='';button.disabled=false;button.textContent='STOP WAITING · KEEP RECEIPT';text('builder-next-label','SUBMITTING · DURABLE NETWORK JOB');
    const strategy=document.querySelector<HTMLButtonElement>('[data-choice-group="network"] button.active')?.dataset.choice||'balanced';
    const extraEdgeFraction=strategy==='minimum'?0:strategy==='dense'?1:.5,input={compounds:preparedCompounds,extra_edge_fraction:extraEdgeFraction,minimum_similarity:.15,campaign_id:draftCampaignId,campaign_scientific_generation:draftCampaignScientificGeneration,campaign_scientific_digest:draftCampaignScientificDigest,prepared_system_id:preparedCampaignSystem.prepared_receptor_state_ref.id},inputSignature=canonicalJson(input);let receipt:PlannerReceipt|null=null;
    const current=()=>operations.current(scope,{edits:true})&&plannerOwnerToken===scope.id&&draftIdentityMatches(snapshot)&&builderStage==='accepted'&&!!preparedCampaignSystem&&sameExactRef(preparedCampaignSystem.prepared_receptor_state_ref,preparedSystemRef)&&canonicalJson(input)===inputSignature;
    try{
        const campaignRef=campaignScientificRefFrom(draftCampaignId,draftCampaignScientificGeneration,draftCampaignScientificDigest);if(!campaignRef||!preparedSystemRef)throw new Error('campaign scientific generation and prepared-system digest must be server-attested');receipt={schema_version:2,owner_token:scope.id,created_at:new Date().toISOString(),status:'submitting',job_id:null,campaign_scientific_ref:campaignRef,prepared_system_ref:preparedSystemRef,input_signature:inputSignature};writePlannerReceipt(receipt);
        const accepted=await client.execute('physics.rbfe-network',input);
        if(!accepted.ok)throw new Error(accepted.error?.message||'network planner refused the campaign');
        const jobId=String(accepted.meta?.job_id||'');if(!fullJobId(jobId))throw new Error('planner returned no complete durable Job ID');plannerJobId=jobId;const owned=plannerReceiptOwned(scope.id);if(owned){receipt={...owned,job_id:jobId,status:current()?'waiting':'detached'};writePlannerReceipt(receipt);}if(!current())return;
        if(currentPlannerCancelState()==='stop-wait'){text('builder-next-label',`${jobId?`DETACHED FROM ${jobId}`:'SUBMISSION DETACHED'} · NO CANCEL REQUEST`);showBuilderNotice(`Stopped waiting locally${jobId?`; durable planner receipt ${jobId} preserved`:''}. No server cancellation was claimed and no physical FEP job was started.`);return;}
        let elapsed=0;plannerProgressTimer=window.setInterval(()=>{elapsed+=1;if(currentPlannerCancelState()==='idle')text('builder-next-label',`PLANNER ${jobId||'RECEIPT PENDING'} · ${elapsed}s · STOP REQUEST AVAILABLE`);},1000);
        const waitController=new AbortController();plannerWaitController=waitController;
        const done=await Promise.race([
            client.waitForCommandResult(accepted,300,waitController.signal).catch(error=>{if(waitController.signal.aborted)return null;throw error;}),
            new Promise<null>(resolve=>{plannerDetachWait=()=>resolve(null);}),
        ]);if(current())plannerDetachWait=null;if(!current())return;
        if(!done){
            if(currentPlannerCancelState()==='confirmed')showBuilderNotice(`Planner cancellation command confirmed for full job ${jobId}. The durable receipt remains authoritative until the job reaches a terminal state. No physical FEP job was started.`);
            else showBuilderNotice(`Stopped waiting locally for ${jobId||'a submission without a full job ID'}. No server cancellation was claimed; the durable receipt was preserved when available.`);
            return;
        }if(!done.ok)throw new Error(done.error?.message||'network planner failed');
        const planned=done.data?.network as Network|undefined;if(!planned)throw new Error('planner returned no rbfe.network artifact');
        const artifact=done.artifacts?.find(item=>item.role==='rbfe.network') as Record<string,any>|undefined;
        const ref=artifactRef(artifact);if(!ref)throw new Error('planner returned no content-addressed rbfe.network reference');
        const campaignHint:CampaignContext={network_job_id:jobId,name:(document.getElementById('campaign-name') as HTMLInputElement|null)?.value||'NEW FEP CAMPAIGN',receptor_label:receptorSourceLabel,ligand_count:planned.compounds.length,prepared_system_id:preparedCampaignSystem.prepared_receptor_state_ref.id,campaign_id:draftCampaignId,campaign_scientific_generation:draftCampaignScientificGeneration,campaign_scientific_digest:draftCampaignScientificDigest},campaignContext=campaignContextFromNetwork(planned,jobId,campaignHint);if(!campaignContext||!sameCampaignScientificContext(campaignContext,campaignHint)||campaignContext.prepared_system_id!==preparedSystemRef.id)throw new Error('planner returned a network without the exact immutable campaign/system context');await enrichMappingEvidence(planned);if(!current())return;
        bindAuthoritativeCampaignContext(planned,jobId,campaignHint);network=planned;networkWorkspaceVisible=true;networkArtifactRef=ref;persistPlannerOutput(jobId,ref,receipt!.campaign_scientific_ref,receipt!.prepared_system_ref);removePlannerReceiptIf(scope.id,jobId);selectedEdge=benchmarkEdge(planned);focusedCompoundId=null;sourceState='replanned-job';invalidatePreparedSystem();executionContract.system=preparedCampaignSystem;autoBindPreparedPoses();markPipelineReadyDom(document,'network','PLANNED + VERSIONED');
        updateSummary(jobId,artifact?.sha256?String(artifact.sha256):'rbfe.network');renderQueue();drawRisk();await renderSelected();syncExecutionContract();
        copyStorage.set('dirac.rbfe.last_campaign_name',(document.getElementById('campaign-name') as HTMLInputElement|null)?.value||'NEW FEP CAMPAIGN');
        text('status',`NEW CAMPAIGN NETWORK ${jobId} · ${network.compounds.length} NODES · ${network.edges.length} EDGES · 0 RESULTS`);applyCampaignContext(campaignContext);setMainMode('review');setBuilderOpen(false);
    }catch(error){const owned=plannerReceiptOwned(scope.id);if(owned)writePlannerReceipt({...owned,status:owned.job_id?'detached':'submitting'});if(currentPlannerCancelState()!=='confirmed'&&currentPlannerCancelState()!=='stop-wait'&&current()){showBuilderNotice(`Campaign creation failed: ${error instanceof Error?error.message:String(error)}`);text('builder-next-label',currentPlannerCancelState()==='failed'?'CANCEL FAILED · PLANNER RECEIPT STILL ACTIVE':'PLANNER FAILED · INPUTS + RECEIPT PRESERVED');}}
    finally{if(plannerOwnerToken===scope.id){window.clearInterval(plannerProgressTimer);plannerWaitController=null;plannerDetachWait=null;plannerInFlight=false;plannerOwnerToken='';plannerJobId='';button.disabled=false;button.textContent=builderStage==='accepted'?'PLAN / RETRY OPENFE NETWORK →':'REVIEW POSES IN 3D →';}}
}
type PlannerCancelState='idle'|'requested'|'confirmed'|'failed'|'stop-wait';
function currentPlannerCancelState():PlannerCancelState{return plannerCancelState;}
function stopPlannerWait(button:HTMLButtonElement,reason:string):void{
    plannerCancelState='stop-wait';const receipt=plannerReceiptOwned(plannerOwnerToken);if(receipt)writePlannerReceipt({...receipt,status:'detached'});plannerWaitController?.abort();plannerDetachWait?.();button.textContent='WAIT DETACHED';text('builder-next-label',reason);
}
async function requestPlannerStop(button:HTMLButtonElement):Promise<void>{
    if(plannerCancelState==='requested'){showBuilderNotice(`Cancellation request is still awaiting a server receipt${plannerJobId?` for ${plannerJobId}`:''}.`);return;}
    if(plannerCancelState==='failed'){stopPlannerWait(button,`${plannerJobId?`DETACHED FROM ${plannerJobId}`:'WAIT DETACHED'} · CANCEL FAILED`);showBuilderNotice('Server cancellation failed. Stopped waiting locally only; the planner job may still be active and its durable receipt remains authoritative.');return;}
    if(!fullJobId(plannerJobId)){stopPlannerWait(button,'WAIT DETACHED · NO FULL JOB ID · NO CANCEL SENT');showBuilderNotice('No complete planner job ID was available, so only the browser wait was stopped. No cancellation command was sent.');return;}
    const cancelJobId=plannerJobId,ownerToken=plannerOwnerToken;plannerCancelState='requested';const receipt=plannerReceiptOwned(ownerToken);if(receipt)writePlannerReceipt({...receipt,status:'cancel_requested'});button.textContent='CANCEL REQUESTED…';text('builder-next-label',`CANCEL REQUESTED · ${cancelJobId}`);showBuilderNotice(`Cancellation requested for full planner job ${cancelJobId}; awaiting server confirmation.`);
    try{
        const response=await client.execute('job.cancel',{job_ref:{kind:'job',id:cancelJobId}});
        if(plannerOwnerToken!==ownerToken)return;if(!response.ok)throw new Error(envelopeFailure(response));
        const cancel=response.data?.cancel as Record<string,unknown>|undefined,state=String(response.data?.state||'unknown');
        if(cancel?.requested!==true&&state!=='cancelled')throw new Error('server returned no cancellation acknowledgement');
        plannerCancelState='confirmed';plannerWaitController?.abort();plannerDetachWait?.();
        const capability=String(cancel?.capability||'unreported'),terminal=['done','failed','cancelled'].includes(state),pending=Boolean(cancel?.terminal_pending)||!terminal;if(terminal)removePlannerReceiptIf(ownerToken,cancelJobId);button.textContent=pending?'CANCEL CONFIRMED · TERMINAL PENDING':'CANCEL CONFIRMED';text('builder-next-label',`CANCEL CONFIRMED · ${cancelJobId} · ${state.toUpperCase()} · ${capability}`);
    }catch(error){plannerCancelState='failed';button.textContent='CANCEL FAILED · STOP WAIT ONLY';text('builder-next-label',`CANCEL FAILED · ${cancelJobId} · JOB MAY STILL RUN`);showBuilderNotice(`Cancellation failed for ${cancelJobId}: ${error instanceof Error?error.message:String(error)}. Click again to stop waiting locally; no server cancellation is claimed.`);}
}
let plannerInFlight=false;
let plannerOwnerToken='';
let plannerCancelState:PlannerCancelState='idle';
let plannerJobId='';
let plannerWaitController:AbortController|null=null;
let plannerDetachWait:(()=>void)|null=null;
let plannerProgressTimer=0;
const poseReviewHost=document.getElementById('pose-reviewer');
let poseReviewer:PoseReviewer|null=null;
async function acceptReviewedPoses(system:ReviewSystem):Promise<void>{
    const policy=preparationPolicyGate(system as PreparedSystemOption);if(!policy.ok)throw new Error(`preparation policy unresolved: ${policy.blockers.map(row=>`${row.axis}=${row.verdict}`).join(' · ')}`);
    const viewed=system.poses.map(pose=>pose.pose_ref.sha256).filter((digest):digest is string=>/^sha256:[0-9a-f]{64}$/.test(String(digest)));
    if(viewed.length!==system.poses.length)throw new Error('pose review cannot be recorded without immutable pose digests');
    const scope=operations.begin('accept-poses'),snapshot=draftIdentitySnapshot(),systemId=system.prepared_receptor_state_ref.id,systemDigest=String(system.prepared_receptor_state_ref.sha256||''),poseRefs=system.poses.map(pose=>({...pose.pose_ref}));if(!/^sha256:[0-9a-f]{64}$/.test(systemDigest))throw new Error('pose review cannot be recorded without the prepared-system digest');
    const response=await client.execute('physics.rbfe-campaign.accept-poses',{campaign_id:snapshot.campaign_id,expected_version:snapshot.audit_version,prepared_receptor_state_ref:system.prepared_receptor_state_ref,pose_refs:poseRefs,review_checks:['shared_coordinate_frame','core_alignment','pocket_geometry'],review_reason:'interactive_same_frame_pose_review',viewed_pose_digests:viewed});
    if(!operations.current(scope,{edits:true})||!draftIdentityMatches(snapshot)||preparedCampaignSystem?.prepared_receptor_state_ref.id!==systemId||preparedCampaignSystem.prepared_receptor_state_ref.sha256!==systemDigest||builderStage!=='prepared')return;
    if(!response.ok)throw new Error(response.error?.user_message||response.error?.message||'pose review was not accepted');
    if(!preparedCampaignSystem)throw new Error('prepared campaign was cleared during pose review');const campaignRef=response.data?.campaign_ref as Record<string,unknown>|undefined,scientificRef=response.data?.campaign_scientific_ref as Record<string,unknown>|undefined,nextVersion=Number(response.data?.campaign_version),nextStateDigest=String(response.data?.campaign_state_digest||''),nextScientificGeneration=Number(response.data?.campaign_scientific_generation),nextScientificDigest=String(response.data?.campaign_scientific_digest||'');if(campaignRef?.id!==snapshot.campaign_id||campaignRef.version!==nextVersion||campaignRef.sha256!==nextStateDigest||nextVersion!==snapshot.audit_version+1||!/^sha256:[0-9a-f]{64}$/.test(nextStateDigest))throw new Error('pose review returned no exact immutable audit revision');if(scientificRef?.id!==snapshot.campaign_id||scientificRef.version!==nextScientificGeneration||scientificRef.sha256!==nextScientificDigest||!Number.isInteger(nextScientificGeneration)||!/^sha256:[0-9a-f]{64}$/.test(nextScientificDigest))throw new Error('pose review returned no exact immutable scientific generation');const returnedSystem=response.data?.prepared_receptor_state_ref as Record<string,unknown>|undefined;if(returnedSystem?.id!==systemId||returnedSystem.sha256!==systemDigest)throw new Error('pose review attestation belongs to a different prepared system');if(!operations.current(scope,{edits:true})||!draftIdentityMatches(snapshot))return;
    draftExpectedVersion=nextVersion;draftCampaignStateDigest=nextStateDigest;draftCampaignScientificGeneration=nextScientificGeneration;draftCampaignScientificDigest=nextScientificDigest;draftServerStatus='poses_reviewed';preparedCampaignSystem.campaign_version=nextVersion;preparedCampaignSystem.campaign_state_digest=nextStateDigest;preparedCampaignSystem.campaign_scientific_generation=nextScientificGeneration;preparedCampaignSystem.campaign_scientific_digest=nextScientificDigest;preparedCampaignSystem.poses.forEach(pose=>pose.review_state='accepted');preparedCampaignSystem.preparation_state='server-attested-human-reviewed';executionContract.system=preparedCampaignSystem;builderStage='accepted';writeCampaignCache({...draftFromUi('server-campaign'),expected_version:nextVersion,state_digest:nextStateDigest,campaign_scientific_generation:nextScientificGeneration,campaign_scientific_digest:nextScientificDigest,builder_stage:'accepted',server_status:'poses_reviewed'},'server-synced');reflectBuilderStage();text('pose-review-state','ACCEPTED · HUMAN-REVIEWED · SAME RECEPTOR FRAME');text('system-status',`${preparedCampaignSystem.poses.length} POSES · HUMAN-REVIEWED · READY FOR EDGE SELECTION`);text('builder-next-label','NEXT · PLAN OPENFE NETWORK');(document.getElementById('review-inputs') as HTMLButtonElement).textContent='PLAN OPENFE NETWORK →';updateBuilderReadiness();showBuilderNotice('Pose review recorded. This unlocks network/system construction only; it does not claim a pose method validation or an FEP result.');
}
function getPoseReviewer():PoseReviewer|null{
    if(!poseReviewHost)return null;
    try{return poseReviewer??=new PoseReviewer(poseReviewHost,apiBase,acceptReviewedPoses);}
    catch(error){showBuilderNotice(`Pose viewer initialization failed · tabular evidence fallback required · ${error instanceof Error?error.message:String(error)}`);return null;}
}
document.getElementById('review-inputs')?.addEventListener('click',async()=>{
    if(builderStage==='accepted'){await createCampaignNetwork();return;}if(builderStage==='prepared'){if(preparedCampaignSystem){const parentId=String(currentScientificInputs?.parent_id||'');await getPoseReviewer()?.open({...preparedCampaignSystem,parent_id:parentId} as ReviewSystem);}return;}if(builderStage==='reviewed'){await prepareCampaignSources();return;}
    const checked=await validateBuilderLigands();if(checked.valid!==checked.rows.length||checked.valid<2)return;
    if(!selectedReferenceLigand()||!receptorPdbText){updateBuilderReadiness(checked.valid);return;}builderStage='reviewed';reflectBuilderStage();text('builder-next-label','NEXT · PREPARE RECEPTOR + POSES');(document.getElementById('review-inputs') as HTMLButtonElement).textContent='PREPARE RECEPTOR + POSES →';
    const boundary=document.querySelector('.prototype-boundary b');if(boundary)boundary.textContent='PREPARATION ENGINE CONNECTED';const boundaryDetail=document.querySelector('.prototype-boundary small');if(boundaryDetail)boundaryDetail.textContent='RAW INPUTS · SERVER-BUILT SCIENCE · PHYSICAL RUNS GATED';
    showBuilderNotice(`Review: ${receptorSourceLabel} · reference ${selectedReferenceLigand()!.resname} · Parent ${(checked.rows.find(row=>/^parent(?:-|$)/i.test(row.id))||checked.rows[0]).id} · ${checked.rows.length-1} analogue${checked.rows.length===2?'':'s'}. Next creates versioned receptor and pose hypotheses; no FEP job starts.`);
});
function showBlankCampaignShell():void{
    networkWorkspaceVisible=false;
    const context=document.querySelectorAll<HTMLElement>('.fep-topbar-left .context b');if(context[0])context[0].textContent='NEW CAMPAIGN · NO NETWORK';
    const scope=document.querySelector<HTMLElement>('.dataset-scope');if(scope)scope.textContent='DRAFT · NO DATASET OR BENCHMARK BOUND';
    text('status','AWAITING CAMPAIGN INPUTS');text('run-boundary','PLAN ONLY · NO NETWORK JOB');text('node-count','—');text('edge-count','—');text('ready-count','—');text('blocked-count','—');text('durable-job','—');text('queue-meta','NO CAMPAIGN NETWORK');text('network-meta','NO INTERACTIVE PLAN');text('target-state','NOT SUPPLIED');text('pose-state','NOT SUPPLIED');text('protocol-state','NOT SUPPLIED');
    const queue=document.getElementById('edge-queue');if(queue)queue.innerHTML='<div class="job-empty"><b>NO NETWORK</b><span>Build or resume a campaign; benchmark evidence is never imported implicitly.</span></div>';const edgeLayer=document.getElementById('edge-layer');if(edgeLayer)edgeLayer.innerHTML='';const nodeLayer=document.getElementById('node-layer');if(nodeLayer)nodeLayer.innerHTML='<div class="job-empty"><b>NO NETWORK PLAN</b><span>Interactive campaign inputs are not evidence.</span></div>';
    const replan=document.getElementById('replan') as HTMLButtonElement|null;if(replan){replan.disabled=true;replan.textContent='NO NETWORK TO REPLAN';}
}
function plannerOutputMatchesReceipt(value:Network,jobId:string,receipt:PlannerReceipt):boolean{try{const context=campaignContextFromNetwork(value,jobId,null),input={compounds:value.compounds.map(row=>({id:row.id,smiles:row.canonical_smiles})),extra_edge_fraction:value.policy.extra_edge_fraction,minimum_similarity:value.policy.minimum_similarity,campaign_id:receipt.campaign_scientific_ref.id,campaign_scientific_generation:receipt.campaign_scientific_ref.version,campaign_scientific_digest:receipt.campaign_scientific_ref.sha256,prepared_system_id:receipt.prepared_system_ref.id};return!!context&&context.campaign_id===receipt.campaign_scientific_ref.id&&context.campaign_scientific_generation===receipt.campaign_scientific_ref.version&&context.campaign_scientific_digest===receipt.campaign_scientific_ref.sha256&&context.prepared_system_id===receipt.prepared_system_ref.id&&canonicalJson(input)===receipt.input_signature;}catch{return false;}}
async function resumePlannerReceipt(receipt:PlannerReceipt):Promise<boolean>{
    if(!receipt.job_id){text('status','PLANNER SUBMISSION RECEIPT HAS NO JOB ID · START/PLAN LOCKED · ARCHIVE UNKNOWN SUBMISSION EXPLICITLY');showBuilderNotice(`Planner submission ${receipt.owner_token} has no acknowledged Job ID. It cannot be retried safely; use ARCHIVE UNKNOWN PLANNER SUBMISSION only after accepting that server reconciliation is impossible.`);syncPlannerRecoveryControl();return true;}
    try{let status=await client.jobGet(receipt.job_id);if(!status.ok)throw new Error(status.error?.message||'planner job unavailable');let state=String(status.data?.state||'unknown');if(['failed','cancelled'].includes(state)){archiveDetachedPlannerReceipt(receipt,`planner_${state}`);text('status',`PLANNER ${receipt.job_id} · ${state.toUpperCase()} · RECEIPT ARCHIVED`);return true;}if(!networkFromJob(status)){status=await client.waitForCommandResult({ok:true,meta:{job_id:receipt.job_id}},300);if(!status.ok)throw new Error(status.error?.message||'planner resume failed');state=String(status.data?.state||'done');}const planned=networkFromJob(status);if(!planned||!plannerOutputMatchesReceipt(planned,receipt.job_id,receipt))throw new Error('planner output does not match the receipt input signature, campaign scientific generation and prepared-system ID');const loaded=await loadDurableNetwork(receipt.job_id);if(!loaded)return true;const context=activeCampaignContext(),systemExact=builderSystemCatalog.some(system=>sameExactRef(system.prepared_receptor_state_ref,receipt.prepared_system_ref));if(!context||context.campaign_id!==receipt.campaign_scientific_ref.id||context.campaign_scientific_generation!==receipt.campaign_scientific_ref.version||context.campaign_scientific_digest!==receipt.campaign_scientific_ref.sha256||context.prepared_system_id!==receipt.prepared_system_ref.id||!systemExact||!networkArtifactRef)throw new Error('loaded planner network did not preserve the receipt campaign, exact network and prepared-system digest');persistPlannerOutput(receipt.job_id,networkArtifactRef,receipt.campaign_scientific_ref,receipt.prepared_system_ref);removePlannerReceiptIf(receipt.owner_token,receipt.job_id);text('status',`PLANNER ${receipt.job_id} · RECONCILED TO EXACT CAMPAIGN GENERATION`);return true;}catch(error){text('status',`PLANNER RECEIPT ${receipt.job_id} PRESERVED · ${error instanceof Error?error.message:String(error)}`);return true;}
}
function receiptMatchesLoadedCampaign(receipt:RunReceipt):boolean{const context=activeCampaignContext(),campaignMatches=!!context&&context.campaign_id===receipt.campaign_scientific_ref.id&&context.campaign_scientific_generation===receipt.campaign_scientific_ref.version&&context.campaign_scientific_digest===receipt.campaign_scientific_ref.sha256&&network.edges.some(edge=>edge.edge_id===receipt.edge_id);return campaignMatches&&(receipt.schema_version===2||!!receipt.plan_network_ref&&!!receipt.plan_network_job_id&&context!.network_job_id===receipt.plan_network_job_id&&sameExactRef(networkArtifactRef,receipt.plan_network_ref));}
async function reconcileRunReceipt(receipt:RunReceipt|null,legacyRunId:string|null):Promise<boolean>{
    const requestedPlanJob=receipt?.schema_version===3&&receipt.plan_network_job_id?receipt.plan_network_job_id:currentNetworkJobId(),loaded=await loadDurableNetwork(requestedPlanJob,operations.begin('boot-network'),true);if(!loaded){text('status','RUNSET RECEIPT PRESERVED · ITS DURABLE NETWORK COULD NOT BE RESTORED');return true;}
    let durable=receipt,response:Envelope|null=null;const runId=receipt?.run_id||legacyRunId;
    if(runId){response=await client.execute('physics.rbfe-run.get',{run_ref:{kind:'run',id:runId}});if(!response.ok){text('status',`RUNSET ${runId} RECEIPT PRESERVED · SERVER STATUS UNAVAILABLE`);return true;}if(!durable){const candidate=runReceiptFromData(response.data||{},`${tabOwnerId}:legacy-run`),context=activeCampaignContext();if(!candidate||!context||candidate.campaign_scientific_ref.id!==context.campaign_id||candidate.campaign_scientific_ref.version!==context.campaign_scientific_generation||candidate.campaign_scientific_ref.sha256!==context.campaign_scientific_digest||!network.edges.some(edge=>edge.edge_id===candidate.edge_id)){text('status',`LEGACY RUN ${runId} REFUSED · CURRENT NETWORK / CAMPAIGN BINDING DOES NOT MATCH`);return true;}durable=candidate;writeRunReceipt(durable);}}
    if(!durable||!receiptMatchesLoadedCampaign(durable)){text('status','RUNSET RECEIPT PRESERVED · CURRENT DURABLE NETWORK DOES NOT MATCH ITS CAMPAIGN / EDGE / EXACT PLAN REF');renderOperationConfirmation();return true;}
    const edge=network.edges.find(item=>item.edge_id===durable!.edge_id)!;selectedEdge=edge;edgeSpecRef=durable.edge_spec_ref;edgeNetworkRef=durable.edge_network_ref;complexTransformationRef=durable.complex_transformation_ref;solventTransformationRef=durable.solvent_transformation_ref;preflightData={spec_digest:durable.spec_digest};activeRunReceipt=durable;activeRunId=durable.run_id;
    if(durable.schema_version===3&&durable.plan_network_job_id&&durable.plan_network_ref&&durable.prepared_system_ref&&durable.parent_pose_ref&&durable.proposal_pose_ref){
        persistPlannerOutput(durable.plan_network_job_id,durable.plan_network_ref,durable.campaign_scientific_ref,durable.prepared_system_ref);const system=systemCatalog.find(item=>sameExactRef(item.prepared_receptor_state_ref,durable!.prepared_system_ref))||null,parent=system?.poses.find(pose=>pose.review_state==='accepted'&&sameExactRef(pose.pose_ref,durable!.parent_pose_ref))||null,proposal=system?.poses.find(pose=>pose.review_state==='accepted'&&sameExactRef(pose.pose_ref,durable!.proposal_pose_ref))||null;executionContract.system=system;executionContract.parentPose=parent;executionContract.proposalPose=proposal;if(!system||!parent||!proposal){if(durable.run_id&&response)adoptRunSet(response.data||{},durable);text('status','RUNSET RECONCILED FOR HISTORY / CANCEL ONLY · EXACT SYSTEM / POSES COULD NOT BE RECONSTRUCTED · START/RETRY LOCKED');renderOperationConfirmation();return true;}
    }
    await renderSelected();syncExecutionContract();
    if(!durable.run_id){text('status',currentExecutionMatchesReceipt(durable)?`RUNSET CREATION RECEIPT ${durable.request_key} · EXACT CARD RECONSTRUCTED · EXPLICIT RETRY AVAILABLE`:`RUNSET CREATION RECEIPT ${durable.request_key} · EXACT CARD INCOMPLETE · RETRY LOCKED`);syncExecutionContract();return true;}
    if(!response){response=await client.execute('physics.rbfe-run.get',{run_ref:{kind:'run',id:durable.run_id}});if(!response.ok){text('status',`RUNSET ${durable.run_id} RECEIPT PRESERVED · SERVER STATUS UNAVAILABLE`);return true;}}if(!adoptRunSet(response.data||{},durable))return true;const active=currentRunReceipt();if(active?.run_id&&!['blocked','completed','cancelled','failed','refused'].includes(active.state))await watchRunSet(active);return true;
}
async function bootReconciler():Promise<void>{
    const startupRun=readRunReceipt(),legacyRunId=startupRun?.run_id||copyStorage.get('dirac.rbfe.active_run_id'),startupPlanner=readPlannerReceipt(),startupPreparation=readPreparationReceipt(),blankRequested=query.get('new')==='1';
    const action=decideWorkbenchBoot({hasRunReceipt:!!startupRun,hasLegacyRunId:!!legacyRunId,hasPlannerReceipt:!!startupPlanner,hasPreparationReceipt:!!startupPreparation,blankRequested});
    if(action==='reconcile-run'){campaignBuilder?.setAttribute('aria-hidden','true');if(blankRequested)showBuilderNotice('Blank campaign was refused because an active or unknown-state RunSet receipt must be reconciled first.');await reconcileRunReceipt(startupRun,legacyRunId);return;}
    if(action==='new-campaign'){if(startupPreparation&&!archiveDetachedPreparationReceipt(startupPreparation)){showBuilderNotice('Blank campaign refused because another tab changed the durable preparation receipt.');return;}if(startupPlanner)archiveDetachedPlannerReceipt(startupPlanner,'new_campaign_requested');copyStorage.remove(plannerOutputReceiptKey);detachExecutionContext();setBuilderOpen(true);const detached=[startupPlanner?.job_id?'planner '+startupPlanner.job_id:null,startupPreparation?.job_id?'preparation '+startupPreparation.job_id:null].filter(Boolean).join(' · ');if(detached)showBuilderNotice(`New campaign is blank. Durable ${detached} was archived as detached history and was not rebound or cancelled.`);return;}
    campaignBuilder?.setAttribute('aria-hidden','true');if(action==='resume-planner'&&startupPlanner){await resumePlannerReceipt(startupPlanner);return;}if(action==='resume-preparation'&&startupPreparation){await resumePendingPreparation();return;}await loadDurableNetwork();
}
addEventListener('storage',event=>{if(event.storageArea!==localStorage||!event.key)return;const watched=[campaignCacheKey,RECEIPT_KEYS.preparation,plannerReceiptKey,plannerOutputReceiptKey,runReceiptKey,'dirac.rbfe.active_campaign_context','dirac.rbfe.active_network_job_id','dirac.rbfe.active_run_id'].map(copyStorageKey);if(!watched.includes(event.key))return;operations.externalStorageWrite();aggregateArm=null;aggregateArmPhysicalSnapshot=null;plannerWaitController?.abort();plannerDetachWait?.();preparationWaitController?.abort();preparationDetachWait?.();activeRunReceipt=readRunReceipt();activeRunId=activeRunReceipt?.run_id||copyStorage.get('dirac.rbfe.active_run_id');invalidatePreparedSystem();text('status','EXTERNAL TAB UPDATED CAMPAIGN / RECEIPT STATE · LOCAL ASYNC COMMITS INVALIDATED');syncPlannerRecoveryControl();syncExecutionContract();});
reflectBuilderStage();updateBuilderReadiness(0);syncPlannerRecoveryControl();
addEventListener('resize',()=>{if(!networkWorkspaceVisible)return;drawRisk();void renderNetwork();drawAgreement();});
void sourceState;syncExecutionContract();void bootReconciler();
