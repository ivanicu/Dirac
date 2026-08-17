import type { ArtifactRef } from './workbench-receipts';
import type { ChemistryEvidence, ExecutionEligibility } from './workbench-state';

export type BuilderStage='inputs'|'reviewed'|'prepared'|'accepted';
export type Compound = { id: string; canonical_smiles: string; depiction_smiles?: string };
export type DepictionContract = {
    schema_version: string;
    parent_smiles: string;
    proposal_smiles: string;
    selected_heavy_atom_mapping: number[][];
    chemistry_evidence?: ChemistryEvidence;
};
export type Edge = {
    edge_id: string; left_id: string; right_id: string; status: string;
    mapping_score: number; mapped_atom_count: number;
    mapping_methods: string[]; mapping_disagreement_jaccard: number | null;
    selected_atom_mapping?: number[][];
    mapping_proposals?: Record<string, number[][]>;
    heavy_atom_mapping_proposals?: Record<string, number[][]>;
    mapped_heavy_atom_count?: number;
    mapping_disagreement_all_atoms_jaccard?: number | null;
    heavy_mapping_disagreement_jaccard?: number | null;
    preliminary_mapping_score?: number;
    mapping_source?: string;
    mapping_method?: string;
    depiction_contract?: DepictionContract;
    chemistry_evidence?: ChemistryEvidence;
    execution_eligibility?: ExecutionEligibility;
    rdkit_fmcs_diagnostic?: { tanimoto?: number | null; mapped_atom_count?: number | null; left_heavy_atom_fraction?: number | null; right_heavy_atom_fraction?: number | null };
};
export type Network = {
    kind: string; digest: string; mode: string; compounds: Compound[]; edges: Edge[];
    policy: { planner: string; mapping: string; minimum_similarity: number; extra_edge_fraction: number };
    claim_boundary: string;
    campaign_context?: NetworkCampaignContext;
};
export type NetworkCampaignContext = {
    campaign_id: string;
    campaign_scientific_generation: number;
    campaign_scientific_digest: string;
    prepared_system_id?: string;
};
export type Bond = { left: number; right: number; order: number; stereo: number };
export type AtomInfo = {
    symbols: string[]; molblock: string; canonicalSmiles: string;
    charges: number[]; atomStereo: number[]; bonds: Bond[]; cycleRank: number;
    stereoAvailable: boolean; cipAtoms: Array<[number,string]>; cipBonds: Array<[number,number,string]>;
    potentialEzBonds: Array<[number,number]>;
};
export type ExecutionContract = {
    system: PreparedSystemOption | null;
    parentPose: PoseOption | null; proposalPose: PoseOption | null;
    protocolPreset: string; validated: boolean;
};
export type ScientificRef<K extends string> = { kind: K; id: string; sha256?:string; version?:number };
export type PoseOption = {
    pose_ref: ScientificRef<'pose_hypothesis'>; label: string;
    canonical_smiles: string;
    core_rmsd_angstrom?: number; core_coverage?: number; review_state?: string;
    minimum_heavy_atom_distance_angstrom?:number; protein_contacts_within_6_angstrom?:number;
    nearest_pair_witness?:Record<string,unknown>;
    contact_pair_witnesses?:Array<Record<string,unknown>>;clash_pair_witnesses?:Array<Record<string,unknown>>;
    metal_coordination_witnesses?:Array<Record<string,unknown>>;cofactor_contact_witnesses?:Array<Record<string,unknown>>;water_contact_witnesses?:Array<Record<string,unknown>>;
    contact_pair_total?:number;contact_pairs_truncated?:boolean;pose_report?:Record<string,unknown>;geometry_evidence?:Record<string,unknown>;
    coordinate_artifact_ref?:ArtifactRef;
};
export type PolicyExecutionAxis={verdict?:string;observed_action?:unknown;witness?:unknown;witnesses?:unknown;reason?:unknown;details?:unknown};
export type PreparedSystemOption = {
    campaign_version?:number;campaign_state_digest?:string;campaign_scientific_generation?:number;campaign_scientific_digest?:string;
    prepared_receptor_state_ref: ScientificRef<'prepared_receptor_state'>;
    target_ref: { kind: 'target'; id: string }; target_name: string;
    protein_structure_ref: { kind: 'protein_structure'; id: string };
    pdb_id: string | null; experimental_method?: string; resolution_angstrom: number | null;
    preparation_state: string; claim_boundary: string; label: string;
    receptor_report?:{policy_execution?:Record<string,PolicyExecutionAxis>};
    stereo_enumeration?:Record<string,unknown>;
    campaign_scope?:'owned'|'imported'|'import_required'|'import_stale';source_campaign_id?:string;source_campaign_scientific_ref?:ScientificRef<'rbfe_campaign_scientific_generation'>;import_required?:boolean;execution_eligible?:boolean;
    coordinate_artifact_ref?:ArtifactRef;
    parent_id?:string;
    poses: PoseOption[];
};
export type RunJob = { leg: 'complex'|'solvent'; repeat: number; jobId: string; state: string; artifacts?: Array<Record<string, any>>; error?: string };
export type CampaignDraftV2 = {
    schema_version:2;campaign_id:string;saved_at:string;origin:'local-draft'|'server-campaign'|'cross-campaign-import';
    name:string;pdb:string;receptor_pdb:string;receptor_source:string;receptor_record?:{title:string;method:'xray'|'cryoem'|'nmr'|'predicted'|'model';resolution:number|null};reference_key:string;ligands:string;builder_stage:BuilderStage;server_status?:'draft'|'inputs_reviewed'|'prepared'|'poses_reviewed'|'planned'|'stale'|'archived';expected_version:number;state_digest?:string;campaign_scientific_generation?:number;campaign_scientific_digest?:string;
    values:Record<string,string>;scientific_inputs?:Record<string,unknown>;prepared_system_id?:string;network_job_id?:string;
};
export type CampaignCacheState='server-synced'|'offline-only'|'version-conflict';
export type CampaignCacheRecord={draft:CampaignDraftV2;cache_state:CampaignCacheState;cached_at:string;server_error?:string};
export type CampaignLoadResult={draft:CampaignDraftV2;source:'server'|'offline-cache';version:number;stateDigest?:string;scientificGeneration?:number;scientificDigest?:string;warning?:string};
export type CampaignSaveResult={receipt:string;durability:'server'|'offline-cache';version:number;stateDigest?:string;scientificGeneration?:number;scientificDigest?:string;status?:CampaignDraftV2['server_status'];warning?:string};
export type CampaignStateAdapter = {
    readonly mode:'server-first';
    load():Promise<CampaignLoadResult|null>;
    save(draft:CampaignDraftV2):Promise<CampaignSaveResult>;
    list():Promise<{campaigns:Array<Record<string,any>>;source:'server'|'offline-cache';warning?:string}>;
    invalidate(campaignId:string,expectedVersion:number,changedDomains:string[],reason:string):Promise<{version:number;status:string;stateDigest?:string;scientificGeneration?:number;scientificDigest?:string}>;
    importSystem(campaignId:string,expectedVersion:number,preparedSystemRef:ScientificRef<'prepared_receptor_state'>,reason:string):Promise<{version:number;receipt:string;stateDigest?:string;scientificGeneration?:number;scientificDigest?:string}>;
    clear():Promise<void>;
};
