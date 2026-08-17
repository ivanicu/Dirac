import { describe, expect, it } from '@jest/globals';
import { CHEMISTRY_DIMENSIONS, OperationCoordinator, aggregateArmMatches, campaignsVisibleToCopy, canonicalJson, chemistryEvidenceFrom, chemistryEvidenceView, exactOperationBindingMatches, exactRunBindingMatches, executionEligibilityFrom, isPotentialEzBond, sameExactRef, type ChemistryDimension, type ChemistryVerdict, type ExactOperationBinding, type ExactRunBinding } from './workbench-state';

describe('FEP workbench operation ownership', () => {
    it('excludes kekulized aromatic bonds from unknown E/Z candidates', () => {
        const aromaticAtoms = new Set([0, 1, 2, 3, 4, 5]);
        expect(isPotentialEzBond({ left: 0, right: 1, order: 2 }, aromaticAtoms)).toBe(false);
        expect(isPotentialEzBond({ left: 5, right: 6, order: 2 }, aromaticAtoms)).toBe(true);
        expect(isPotentialEzBond({ left: 6, right: 7, order: 1 }, aromaticAtoms)).toBe(false);
    });

    it('invalidates competitors while allowing the transition owner to continue', () => {
        const coordinator = new OperationCoordinator('tab-a');
        const owner = coordinator.begin('restore');
        const competitor = coordinator.begin('planner');
        const adopted = coordinator.transition(owner)!;
        expect(coordinator.current(owner)).toBe(false);
        expect(coordinator.current(competitor)).toBe(false);
        expect(coordinator.current(adopted)).toBe(true);
    });

    it('separates editable-value races from scientific operations', () => {
        const coordinator = new OperationCoordinator('tab-a');
        const save = coordinator.begin('save');
        const scientific = coordinator.begin('prepare');
        coordinator.edit();
        expect(coordinator.current(save, { edits: true })).toBe(false);
        expect(coordinator.current(scientific, { edits: false })).toBe(true);
    });

    it('makes the newest operation of a kind the sole owner', () => {
        const coordinator = new OperationCoordinator('tab-a');
        const first = coordinator.begin('render');
        const second = coordinator.begin('render');
        expect(coordinator.current(first)).toBe(false);
        expect(coordinator.current(second)).toBe(true);
    });

    it('invalidates every outstanding operation after a foreign-tab write', () => {
        const coordinator = new OperationCoordinator('tab-a');
        const run = coordinator.begin('run-start');
        coordinator.externalStorageWrite();
        expect(coordinator.current(run)).toBe(false);
    });

    it('can invalidate one operation kind without advancing the session', () => {
        const coordinator = new OperationCoordinator('tab-a');
        const render = coordinator.begin('render');
        const planner = coordinator.begin('planner');
        coordinator.invalidate('render');
        expect(coordinator.current(render)).toBe(false);
        expect(coordinator.current(planner)).toBe(true);
    });

    it('keeps independent operation kinds live until a session transition', () => {
        const coordinator = new OperationCoordinator('tab-a');
        const planner = coordinator.begin('planner');
        const preparation = coordinator.begin('preparation');
        expect(coordinator.current(planner)).toBe(true);
        expect(coordinator.current(preparation)).toBe(true);
        coordinator.transition();
        expect(coordinator.current(planner)).toBe(false);
        expect(coordinator.current(preparation)).toBe(false);
    });

    it('canonicalizes object key order for durable input signatures', () => {
        expect(canonicalJson({ b: 2, a: { d: 4, c: 3 } }))
            .toBe(canonicalJson({ a: { c: 3, d: 4 }, b: 2 }));
    });

    it('requires both identity and digest for exact execution references', () => {
        const digest = `sha256:${'a'.repeat(64)}`;
        expect(sameExactRef({ kind:'artifact',id: 'one', sha256: digest }, { kind:'artifact',id: 'one', sha256: digest })).toBe(true);
        expect(sameExactRef({ kind:'artifact',id: 'one', sha256: digest }, { kind:'artifact',id: 'two', sha256: digest })).toBe(false);
        expect(sameExactRef({ kind:'artifact',id: 'one', sha256: digest }, { kind:'other',id: 'one', sha256: digest })).toBe(false);
        expect(sameExactRef({ kind:'artifact',id: 'one', sha256: digest }, { kind:'artifact',id: 'one' })).toBe(false);
    });

    it('rejects non-content-addressed digest spellings', () => {
        const bare = 'a'.repeat(64);
        expect(sameExactRef({ kind:'artifact',id:'one',sha256:bare }, { kind:'artifact',id:'one',sha256:bare })).toBe(false);
    });

    it('requires every RunSet reference plus request key and campaign generation', () => {
        const digest = `sha256:${'a'.repeat(64)}`;
        const binding: ExactRunBinding = {
            requestKey: 'request-1', campaign: { id: 'campaign-1', version: 3, sha256: digest },
            edgeId: 'edge-1', specDigest: digest,
            edgeSpecRef: { kind:'artifact',id: 'spec', sha256: digest }, edgeNetworkRef: { kind:'artifact',id: 'network', sha256: digest },
            complexTransformationRef: { kind:'artifact',id: 'complex', sha256: digest }, solventTransformationRef: { kind:'artifact',id: 'solvent', sha256: digest },
        };
        const row = {
            request_key: binding.requestKey, campaign_scientific_ref: {kind:'rbfe_campaign',...binding.campaign}, edge_id: binding.edgeId,
            edge_spec_ref: binding.edgeSpecRef,
            edge_network_ref: binding.edgeNetworkRef, complex_transformation_ref: binding.complexTransformationRef,
            solvent_transformation_ref: binding.solventTransformationRef,
        };
        expect(exactRunBindingMatches(row, binding)).toBe(true);
        expect(exactRunBindingMatches({ ...row, request_key: 'other' }, binding)).toBe(false);
        expect(exactRunBindingMatches({ ...row, edge_spec_ref: { kind:'artifact',id: 'spec', sha256: `sha256:${'b'.repeat(64)}` } }, binding)).toBe(false);
        expect(exactRunBindingMatches({ ...row, campaign_scientific_ref: { ...row.campaign_scientific_ref, kind: 'other' } }, binding)).toBe(false);
        expect(exactRunBindingMatches({ ...row, solvent_transformation_ref: undefined }, binding)).toBe(false);
    });

    it('ignores a RunSet orchestration digest and requires edge-spec provenance instead', () => {
        const digest = `sha256:${'a'.repeat(64)}`, other = `sha256:${'b'.repeat(64)}`;
        const binding: ExactRunBinding = { requestKey:'key',campaign:{id:'c',version:1,sha256:digest},edgeId:'e',specDigest:digest,edgeSpecRef:{kind:'artifact',id:'s',sha256:digest},edgeNetworkRef:{kind:'artifact',id:'n',sha256:digest},complexTransformationRef:{kind:'artifact',id:'x',sha256:digest},solventTransformationRef:{kind:'artifact',id:'y',sha256:digest} };
        const row = {request_key:'key',campaign_scientific_ref:{kind:'rbfe_campaign',...binding.campaign},edge_id:'e',specification_digest:other,edge_spec_ref:binding.edgeSpecRef,edge_network_ref:binding.edgeNetworkRef,complex_transformation_ref:binding.complexTransformationRef,solvent_transformation_ref:binding.solventTransformationRef};
        expect(exactRunBindingMatches(row,binding)).toBe(true);
        expect(exactRunBindingMatches({...row,edge_spec_ref:{...binding.edgeSpecRef,sha256:other}},binding)).toBe(false);
    });

    it('arms only the exact execution binding and expires it', () => {
        const digest = `sha256:${'a'.repeat(64)}`;
        const binding: ExactRunBinding = { requestKey:'key', campaign:{id:'c',version:1,sha256:digest},edgeId:'e',specDigest:digest,edgeSpecRef:{kind:'artifact',id:'s',sha256:digest},edgeNetworkRef:{kind:'artifact',id:'n',sha256:digest},complexTransformationRef:{kind:'artifact',id:'x',sha256:digest},solventTransformationRef:{kind:'artifact',id:'y',sha256:digest} };
        expect(aggregateArmMatches({ ...binding, expiresAt: 100 }, binding, 99)).toBe(true);
        expect(aggregateArmMatches({ ...binding, expiresAt: 100 }, { ...binding, edgeId: 'other' }, 99)).toBe(false);
        expect(aggregateArmMatches({ ...binding, expiresAt: 100 }, binding, 101)).toBe(false);
    });

    it('requires the exact plan, system and endpoint poses in addition to RunSet refs', () => {
        const digest = `sha256:${'a'.repeat(64)}`;
        const operation: ExactOperationBinding = {
            requestKey:'key', campaign:{id:'c',version:1,sha256:digest}, edgeId:'e', specDigest:digest,
            edgeSpecRef:{kind:'artifact',id:'s',sha256:digest}, edgeNetworkRef:{kind:'artifact',id:'n',sha256:digest},
            complexTransformationRef:{kind:'artifact',id:'x',sha256:digest}, solventTransformationRef:{kind:'artifact',id:'y',sha256:digest},
            planNetworkJobId:'job-1', planNetworkRef:{kind:'artifact',id:'plan',sha256:digest},
            preparedSystemRef:{kind:'prepared_receptor_state',id:'system',sha256:digest},
            parentPoseRef:{kind:'pose_hypothesis',id:'parent',sha256:digest},
            proposalPoseRef:{kind:'pose_hypothesis',id:'proposal',sha256:digest},
        };
        expect(exactOperationBindingMatches(operation, structuredClone(operation))).toBe(true);
        expect(exactOperationBindingMatches(operation, { ...operation, parentPoseRef:{...operation.parentPoseRef,sha256:`sha256:${'b'.repeat(64)}`} })).toBe(false);
        expect(exactOperationBindingMatches(operation, null)).toBe(false);
    });

});

describe('audit-copy campaign ownership',()=>{
    const campaigns=[{id:'main-campaign'},{id:'copy-campaign'}];
    const visible=(copyId:string,cached:string|null)=>campaignsVisibleToCopy(campaigns,copyId,cached,row=>row.id);
    it('keeps the complete server list available to the main workbench',()=>{
        expect(visible('main',null)).toEqual(campaigns);
    });
    it('does not let an empty audit copy adopt the latest server campaign',()=>{
        expect(visible('audit-a',null)).toEqual([]);
    });
    it('lets an audit copy reload only its previously owned campaign UUID',()=>{
        expect(visible('audit-a','copy-campaign')).toEqual([{id:'copy-campaign'}]);
    });
});

function chemistryFixture(overrides:Partial<Record<ChemistryDimension,{verdict:ChemistryVerdict;summary:string;witnesses:Array<Record<string,unknown>>}>>={}):Record<string,unknown>{
    const ledger=CHEMISTRY_DIMENSIONS.map(dimension=>({
        dimension,
        verdict:overrides[dimension]?.verdict||'CONFIRMED',
        summary:overrides[dimension]?.summary||`${dimension.toLowerCase()} server witness`,
        witnesses:overrides[dimension]?.witnesses||[],
    }));
    const verdicts=new Set(ledger.map(row=>row.verdict));
    const verdict:ChemistryVerdict=verdicts.has('UNVERIFIED')?'UNVERIFIED':verdicts.has('CHANGED')?'CHANGED':'CONFIRMED';
    return{schema_version:'rbfe-chemistry-change.v1',verdict,full_heavy_atom_coverage:false,ledger};
}

describe('server-owned FEP chemistry evidence',()=>{
    it('renders the backend F-to-Cl 6/7 verdicts instead of promoting local no-change',()=>{
        const view=chemistryEvidenceView(chemistryFixture({
            SCOPE:{verdict:'CONFIRMED',summary:'mapped heavy subgraph 6/7 atoms; coverage 0.857',witnesses:[{mapped_heavy_atom_pairs:[[0,0],[1,1]],full_coverage:false}]},
            ELEMENT:{verdict:'UNVERIFIED',summary:'no mapped element change',witnesses:[]},
            CONNECTIVITY:{verdict:'UNVERIFIED',summary:'no adjacency change across 6 mapped bonds',witnesses:[]},
            BOND_ORDER:{verdict:'UNVERIFIED',summary:'no bond-order change across 6 mapped bonds',witnesses:[]},
            FORMAL_CHARGE:{verdict:'UNVERIFIED',summary:'total formal charge 0 in both endpoints',witnesses:[]},
            RING_CYCLE_RANK:{verdict:'UNVERIFIED',summary:'cycle rank 1 -> 1',witnesses:[{parent_cycle_rank:1,proposal_cycle_rank:1}]},
            UNMAPPED:{verdict:'CHANGED',summary:'1 parent / 1 proposal heavy atoms unmapped',witnesses:[{parent_atom_indices:[6],proposal_atom_indices:[6],parent_element:'F',proposal_element:'Cl'}]},
            PROTONATION_TAUTOMER:{verdict:'UNVERIFIED',summary:'endpoints are not microstate comparable',witnesses:[{kind:'ENDPOINTS_NOT_MICROSTATE_COMPARABLE'}]},
        }));
        const element=view.ledger.find(row=>row.label==='ELEMENT')!;
        const unmapped=view.ledger.find(row=>row.label==='UNMAPPED')!;
        expect(element.state).toBe('unverified');
        expect(element.value).toContain('UNVERIFIED · no mapped element change');
        expect(unmapped.state).toBe('changed');
        expect(unmapped.value).toContain('"parent_element":"F"');
        expect(unmapped.value).toContain('"proposal_element":"Cl"');
    });

    it('does not promote protonation or tautomer evidence when execution is eligible',()=>{
        const evidence=chemistryFixture({
            PROTONATION_TAUTOMER:{verdict:'UNVERIFIED',summary:'explicit endpoint protonation/tautomer contract not attached',witnesses:[]},
        });
        expect(executionEligibilityFrom({verdict:'CONFIRMED',reasons:[]} )?.verdict).toBe('CONFIRMED');
        expect(chemistryEvidenceView(evidence).ledger.find(row=>row.label==='PROTONATION TAUTOMER')?.state).toBe('unverified');
    });

    it('fails the complete ledger closed when evidence is missing or malformed',()=>{
        const malformed={...chemistryFixture(),ledger:(chemistryFixture().ledger as unknown[]).slice(0,-1)};
        expect(chemistryEvidenceFrom(malformed)).toBeNull();
        const view=chemistryEvidenceView(malformed);
        expect(view.verdict).toBe('UNVERIFIED');
        expect(view.ledger).toHaveLength(CHEMISTRY_DIMENSIONS.length);
        expect(view.ledger.every(row=>row.state==='unverified')).toBe(true);
    });

    it('rejects malformed execution eligibility instead of treating truthy data as executable',()=>{
        expect(executionEligibilityFrom({verdict:'CONFIRMED',reasons:[]})).toEqual({verdict:'CONFIRMED',reasons:[]});
        expect(executionEligibilityFrom({verdict:'CONFIRMED',reasons:['not a structured reason']})).toBeNull();
        expect(executionEligibilityFrom({verdict:'READY',reasons:[]})).toBeNull();
    });
});
