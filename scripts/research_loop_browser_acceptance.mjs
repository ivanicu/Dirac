#!/usr/bin/env node
/** Real-Chrome acceptance for the attachment-defined Research Loop Drawer.
 *
 * The production Workbench bundle and its real DiracClient are exercised. HTTP is
 * intercepted at the browser boundary so the no-provider and deterministic fake-
 * provider paths are reproducible without claiming physical FEP execution. The
 * backend/PostgreSQL path is proved separately by research_loop_acceptance.py.
 */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtempSync, writeFileSync } from 'node:fs';

const baseUrl = process.argv.find(value => /^https?:/.test(value))
    || 'http://127.0.0.1:1370/fep-workbench.html';
const chromePort = 9500 + (process.pid % 300);
const profile = mkdtempSync('/tmp/dirac-research-loop-browser-');
const chrome = spawn('/usr/bin/google-chrome', [
    `--remote-debugging-port=${chromePort}`, `--user-data-dir=${profile}`,
    '--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
    '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--enable-webgl',
    'about:blank',
], { stdio: 'ignore' });

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const failures = [];
const observed = {};
const check = (condition, message) => { if (!condition) failures.push(message); };
const ids = {
    networkJob: '00000000-0000-4000-8000-000000000101',
    networkArtifact: '00000000-0000-4000-8000-000000000102',
    campaign: '00000000-0000-4000-8000-000000000103',
    program: '00000000-0000-4000-8000-000000000104',
    preparedSystem: '00000000-0000-4000-8000-000000000105',
    run: '00000000-0000-4000-8000-000000000106',
    context: '00000000-0000-4000-8000-000000000107',
    proposal: '00000000-0000-4000-8000-000000000108',
    contextCompleted: '00000000-0000-4000-8000-000000000109',
};
const sha = digit => `sha256:${digit.repeat(64)}`;

const network = {
    kind: 'rbfe_network', digest: sha('1'), mode: 'mst',
    compounds: [
        { id: 'C2', canonical_smiles: 'CCO' },
        { id: 'C7', canonical_smiles: 'CCN' },
    ],
    edges: [{
        edge_id: 'edge-c2-c7', left_id: 'C2', right_id: 'C7', status: 'planned',
        mapping_score: 0.91, mapped_atom_count: 2, mapped_heavy_atom_count: 2,
        mapping_methods: ['rdkit_fmcs'], mapping_disagreement_jaccard: 0,
        heavy_mapping_disagreement_jaccard: 0,
        selected_atom_mapping: [[0, 0], [1, 1]],
        mapping_proposals: { rdkit_fmcs: [[0, 0], [1, 1]] },
        heavy_atom_mapping_proposals: { rdkit_fmcs: [[0, 0], [1, 1]] },
        rdkit_fmcs_diagnostic: {
            tanimoto: 0.5, mapped_atom_count: 2,
            left_heavy_atom_fraction: 0.67, right_heavy_atom_fraction: 0.67,
        },
    }],
    policy: { planner: 'mst', mapping: 'rdkit_fmcs',
        minimum_similarity: 0.2, extra_edge_fraction: 0 },
    claim_boundary: 'governed execution plan, not scientific evidence',
    campaign_context: {
        campaign_id: ids.campaign, campaign_scientific_generation: 1,
        campaign_scientific_digest: sha('2'), prepared_system_id: ids.preparedSystem,
    },
};

const context = {
    digest: sha('4'),
    facts: [{
        fact_id: 'network-edge:edge-c2-c7', category: 'network_edge',
        source_class: 'method_result',
        source_ref: { kind: 'artifact', id: ids.networkArtifact, sha256: sha('1') },
        subject_ref: { kind: 'free_energy_transformation', id: 'edge-c2-c7' },
        condition_ref: null, structured_value: { mapping_score: 0.91 },
        freshness: { stale: false, source_generation: 1 },
        claim_boundary: { status: 'governed_execution_plan',
            eligible_as_scientific_evidence: false,
            reason_codes: ['NETWORK_PLAN_NOT_SCIENTIFIC_EVIDENCE'] },
    }],
    action_history: [], created_at: '2026-08-19T08:00:00Z',
};
const completedContext = {
    ...context,
    facts: [...context.facts, {
        fact_id: 'runset:fake-browser', category: 'fep_result',
        source_class: 'method_result',
        source_ref: { kind: 'artifact', id: ids.networkArtifact, sha256: sha('1') },
        subject_ref: { kind: 'free_energy_transformation', id: 'edge-c2-c7' },
        condition_ref: null,
        structured_value: { delta_g_kcal_mol: -1.2, uncertainty: 0.3 },
        freshness: { stale: false, source_generation: 1 },
        claim_boundary: { status: 'completed_unvalidated',
            eligible_as_scientific_evidence: false,
            reason_codes: ['METHOD_RESULT_NOT_EVIDENCE'] },
    }],
};
const artifactBytes = value => JSON.stringify(value);
const artifactDigest = value => `sha256:${createHash('sha256')
    .update(artifactBytes(value)).digest('hex')}`;
const proposal = {
    summary: 'Run one bounded edge, then refresh or stop.',
    hypothesis_drafts: [{
        statement: 'The selected edge may change the current ranking.',
        testable_prediction: 'The bounded result changes the next action.',
        falsifier: 'The result leaves every decision unchanged.',
        supporting_fact_ids: ['network-edge:edge-c2-c7'],
        contradicting_fact_ids: [], assumptions: ['Method output remains unvalidated.'],
    }],
    candidate_actions: [], preferred_action_id: 'candidate-1',
};
const preview = {
    template_id: 'fep.run_selected_edge.v1',
    subject_ref: { kind: 'free_energy_transformation', id: 'edge-c2-c7' },
    scientific_question: 'Will this edge change the lead ranking?',
    consequence: { risk_class: 'R3', approval: 'per_action', reversible: false,
        summary: 'Starts complex and solvent legs under the governed RunSet.' },
    estimate: { available: true, gpu_hours_upper_bound: 1, external_cost_upper_bound: 0 },
    required_acknowledgements: ['physical_fep_compute',
        'completed_unvalidated_claim_boundary'],
    action_fingerprint: sha('6'),
};
const events = completed => [
    { event_type: 'loop_created', stage: 'bootstrap',
        actor: { kind: 'human', id: 'local' }, occurred_at: '2026-08-19T08:00:00Z' },
    { event_type: 'approval_requested', stage: 'await_approval',
        actor: { kind: 'service', id: 'browser-acceptance' }, occurred_at: '2026-08-19T08:00:01Z' },
    ...(completed ? [{ event_type: 'action_approved', stage: 'dispatch',
        actor: { kind: 'human', id: 'local' }, occurred_at: '2026-08-19T08:00:02Z' },
    { event_type: 'action_dispatched', stage: 'wait_job',
        actor: { kind: 'service', id: 'browser-acceptance' }, occurred_at: '2026-08-19T08:00:03Z' },
    { event_type: 'runset_completed', stage: 'observe',
        actor: { kind: 'service', id: 'browser-acceptance' }, occurred_at: '2026-08-19T08:00:04Z' },
    { event_type: 'loop_completed', stage: 'completed',
        actor: { kind: 'service', id: 'browser-acceptance' }, occurred_at: '2026-08-19T08:00:05Z' }] : []),
];

function loop(completed) {
    return {
        run_ref: { kind: 'run', id: ids.run },
        state: completed ? 'completed' : 'waiting_approval',
        stage: completed ? 'completed' : 'await_approval', version: completed ? 12 : 7,
        iteration: completed ? 2 : 1,
        goal: { intent: 'Prioritize FEP evidence that could change the ranking.' },
        provider: { profile_id: 'qwen-local-isolated', profile_digest: sha('3') },
        budget: { remaining: { reasoner_calls: completed ? 2 : 3,
            fep_runsets: completed ? 0 : 1, gpu_hours: completed ? 11 : 12,
            external_cost: 10, iterations: completed ? 6 : 7 },
        spent: { reasoner_calls: completed ? 2 : 1, fep_runsets: completed ? 1 : 0,
            gpu_hours: completed ? 1 : 0, external_cost: 0, iterations: completed ? 2 : 1 } },
        context_ref: { kind: 'artifact',
            id: completed ? ids.contextCompleted : ids.context,
            sha256: artifactDigest(completed ? completedContext : context) },
        proposal_ref: { kind: 'artifact', id: ids.proposal,
            sha256: artifactDigest(proposal) },
        pending_action: completed ? null : { preview }, attention: {},
        events: events(completed), deep_links: { fep_workbench: '/motif/fep', jobs: '/workspace/jobs' },
        claim_boundary: 'model_proposal_not_scientific_evidence',
    };
}

async function connectChrome() {
    let version;
    for (let index = 0; index < 100 && !version; index++) {
        try { version = await fetch(`http://127.0.0.1:${chromePort}/json/version`).then(r => r.ok ? r.json() : null); } catch {}
        if (!version) await pause(100);
    }
    if (!version) throw new Error('isolated Chrome CDP unavailable');
    const socket = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
        socket.addEventListener('open', resolve, { once: true });
        socket.addEventListener('error', reject, { once: true });
    });
    let requestId = 0;
    const pending = new Map();
    const listeners = [];
    socket.addEventListener('message', event => {
        const message = JSON.parse(String(event.data));
        if (message.method) listeners.forEach(listener => listener(message));
        const request = pending.get(message.id);
        if (!request) return;
        pending.delete(message.id);
        message.error ? request.reject(new Error(message.error.message))
            : request.resolve(message.result);
    });
    const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
        const id = ++requestId;
        pending.set(id, { resolve, reject });
        socket.send(JSON.stringify({ id, method, params,
            ...(sessionId ? { sessionId } : {}) }));
    });
    return { socket, send, on: listener => listeners.push(listener) };
}

async function scenario(cdp, name, configured) {
    let completed = false;
    const unexpected = [];
    const consoleErrors = [];
    const copyId = `${name}-${process.pid}`.slice(0, 16);
    const target = await cdp.send('Target.createTarget', { url: 'about:blank' });
    const attached = await cdp.send('Target.attachToTarget', {
        targetId: target.targetId, flatten: true,
    });
    const session = attached.sessionId;
    const fulfill = (requestId, value, status = 200, contentType = 'application/json') =>
        cdp.send('Fetch.fulfillRequest', {
            requestId, responseCode: status,
            responseHeaders: [
                { name: 'Content-Type', value: contentType },
                { name: 'Access-Control-Allow-Origin', value: '*' },
                { name: 'Access-Control-Allow-Headers', value: 'Content-Type, X-Request-Id' },
                { name: 'Access-Control-Allow-Methods', value: 'GET, POST, OPTIONS' },
            ],
            body: Buffer.from(typeof value === 'string' ? value : JSON.stringify(value)).toString('base64'),
        }, session);
    const envelope = data => ({ ok: true, data, meta: { envelope: 2 } });
    cdp.on(message => {
        if (message.sessionId !== session) return;
        if (message.method === 'Runtime.exceptionThrown') {
            consoleErrors.push(message.params?.exceptionDetails?.text || 'uncaught exception');
        }
        if (message.method !== 'Fetch.requestPaused') return;
        void (async () => {
            const paused = message.params;
            const url = new URL(paused.request.url);
            if (paused.request.method === 'OPTIONS') return fulfill(paused.requestId, {});
            if (url.pathname.startsWith('/v2/artifacts/')) {
                const id = url.pathname.split('/').pop();
                const document = id === ids.context ? context
                    : id === ids.contextCompleted ? completedContext : proposal;
                return fulfill(paused.requestId, artifactBytes(document));
            }
            let body = {};
            try { body = JSON.parse(paused.request.postData || '{}'); } catch {}
            const command = body.command;
            let data;
            if (command === 'job.get') data = {
                id: ids.networkJob, state: 'done', result_summary: { data: { network } },
                artifacts: [{ id: ids.networkArtifact, role: 'rbfe.network',
                    sha256: sha('1').slice(7), media_type: 'application/json',
                    size_bytes: 1, encoding: 'identity',
                    url: `/v2/artifacts/${ids.networkArtifact}` }],
            };
            else if (command === 'physics.rbfe-campaign.get') data = {
                campaign_id: ids.campaign, version: 1, status: 'planned',
                state_digest: sha('7'), campaign_scientific_generation: 1,
                campaign_scientific_digest: sha('2'),
                state: { client_state: { schema_version: 2, campaign_id: ids.campaign,
                    saved_at: '2026-08-19T08:00:00Z', origin: 'server-campaign',
                    name: 'Browser Governed Campaign', pdb: '', receptor_pdb: '',
                    receptor_source: '', reference_key: '', ligands: '',
                    builder_stage: 'accepted', server_status: 'planned',
                    expected_version: 1, values: {}, prepared_system_id: ids.preparedSystem,
                    network_job_id: ids.networkJob } },
            };
            else if (command === 'physics.rbfe-system.list') data = { systems: [] };
            else if (command === 'ai.provider.list') data = { profiles: [{
                profile_id: 'qwen-local-isolated', profile_digest: sha('3'),
                label: 'Local Qwen', configured_model: 'Qwen/Fake-Browser',
                locality: 'local_network', external_egress: false,
                allowed_classifications: ['internal'], configured,
                ...(configured ? {} : { reason: 'missing_base_url_env' }),
            }] };
            else if (command === 'program.list') data = { programs: [{
                ref: { kind: 'program', id: ids.program }, code: 'AI-FEP',
                name: 'AI FEP Browser Acceptance', lifecycle: 'active',
            }] };
            else if (command === 'research.loop.create') data = {
                mission_ref: { kind: 'mission', id: ids.program },
                run_ref: { kind: 'run', id: ids.run }, state: 'active',
                stage: 'bootstrap', version: 1, created: true,
            };
            else if (command === 'research.loop.get') data = loop(completed);
            else if (command === 'research.loop.approve') {
                completed = true;
                data = { run_ref: { kind: 'run', id: ids.run },
                    state: 'active', stage: 'dispatch', version: 8 };
            } else {
                unexpected.push(command || `${paused.request.method} ${url.pathname}`);
                data = {};
            }
            await fulfill(paused.requestId, envelope(data));
        })().catch(error => failures.push(`${name} interception failed: ${error.message}`));
    });
    await cdp.send('Runtime.enable', {}, session);
    await cdp.send('Page.enable', {}, session);
    await cdp.send('Fetch.enable', { patterns: [{ urlPattern: '*://127.0.0.1:8999/*' }] }, session);
    await cdp.send('Emulation.setDeviceMetricsOverride', {
        width: 960, height: 900, deviceScaleFactor: 1, mobile: false,
    }, session);
    await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: `
        localStorage.setItem('dirac.rbfe.active_network_job_id.copy.${copyId}', '${ids.networkJob}');
        localStorage.setItem('dirac.rbfe.active_campaign_context.copy.${copyId}', JSON.stringify({
            network_job_id: '${ids.networkJob}', name: 'Browser Governed Campaign',
            receptor_label: 'SERVER-BOUND RECEPTOR', ligand_count: 2,
            prepared_system_id: '${ids.preparedSystem}', campaign_id: '${ids.campaign}',
            campaign_scientific_generation: 1, campaign_scientific_digest: '${sha('2')}'
        }));
    ` }, session);
    await cdp.send('Page.navigate', {
        url: `${baseUrl}?copy=${copyId}&api=http://127.0.0.1:8999`,
    }, session);
    const evaluate = async expression => {
        const result = await cdp.send('Runtime.evaluate', {
            expression, awaitPromise: true, returnByValue: true,
        }, session);
        if (result.exceptionDetails) throw new Error(
            result.exceptionDetails.exception?.description || result.exceptionDetails.text);
        return result.result?.value;
    };
    const waitFor = async (expression, timeout = 30000) => {
        const started = Date.now();
        while (Date.now() - started < timeout) {
            if (await evaluate(expression)) return true;
            await pause(100);
        }
        return false;
    };
    check(await waitFor(`document.readyState==='complete'&&!!document.querySelector('#research-loop-toggle')&&document.querySelector('#durable-job')?.textContent.includes('${ids.networkJob}')`, 45000),
        `${name}: governed Workbench did not bootstrap`);
    await evaluate(`(()=>{const button=document.querySelector('#research-loop-toggle');button.focus();button.click();})()`);
    check(await waitFor(`!document.querySelector('#research-loop-drawer').hidden`),
        `${name}: drawer did not open`);
    const base = await evaluate(`({
        text: document.querySelector('#research-loop-drawer').innerText,
        overflow: document.documentElement.scrollWidth-innerWidth,
        focus: document.activeElement?.getAttribute('aria-label')||document.activeElement?.textContent?.trim(),
        expanded: document.querySelector('#research-loop-toggle').getAttribute('aria-expanded'),
        live: document.querySelector('#research-loop-live')?.getAttribute('aria-live'),
        fepLoaded: document.body.innerText.includes('C2')&&document.body.innerText.includes('C7')
    })`);
    observed[name] = { base };
    check(base.overflow <= 1, `${name}: 960px viewport overflows by ${base.overflow}px`);
    check(base.focus === 'Close AI research loop', `${name}: initial drawer focus is ${base.focus}`);
    check(base.expanded === 'true' && base.live === 'polite', `${name}: disclosure/live semantics missing`);
    check(base.fepLoaded, `${name}: unrelated FEP Workbench did not remain loaded`);

    if (!configured) {
        check(base.text.includes('PROVIDER UNCONFIGURED'),
            'no-provider: capability unavailability is not explained');
        check(base.text.includes('Existing FEP planning, preparation and RunSets remain available'),
            'no-provider: unrelated workflow continuity is not explicit');
    } else {
        check(base.text.includes('START BOUNDED RESEARCH LOOP'),
            'fake-provider: create action is absent');
        await evaluate(`document.querySelector('.research-loop-primary').click()`);
        const reachedApproval = await waitFor(
            `document.querySelector('#research-loop-drawer').innerText.includes('APPROVAL · EXACT CONSEQUENCES')`);
        check(reachedApproval, 'fake-provider: exact R3 preview did not appear');
        if (!reachedApproval) {
            observed[name].afterCreate = await evaluate(
                `document.querySelector('#research-loop-drawer').innerText`);
            throw new Error(`fake-provider did not reach approval: ${observed[name].afterCreate}`);
        }
        const approval = await evaluate(`(()=>{
            const approve=[...document.querySelectorAll('button')].find(b=>b.textContent.includes('APPROVE EXACT ACTION'));
            const reject=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='REJECT');
            const a=approve.getBoundingClientRect(),r=reject.getBoundingClientRect();
            return {text:document.querySelector('#research-loop-drawer').innerText,
                approvalVisible:a.top>=0&&a.bottom<=innerHeight,
                hotZonesOverlap:!(a.right<=r.left||r.right<=a.left||a.bottom<=r.top||r.bottom<=a.top)};
        })()`);
        observed[name].approval = approval;
        check(approval.text.includes('network-edge:edge-c2-c7'),
            'fake-provider: proposal source fact is not visible');
        check(approval.text.includes('MODEL PROPOSAL ≠ SCIENTIFIC EVIDENCE'),
            'fake-provider: claim boundary is not visible');
        check(approval.approvalVisible, 'fake-provider: approval CTA is outside the 960px viewport');
        check(!approval.hotZonesOverlap, 'fake-provider: approve/reject hot zones overlap');
        const approvalImage = await cdp.send('Page.captureScreenshot', {
            format: 'png', captureBeyondViewport: false,
        }, session);
        writeFileSync('/tmp/dirac-research-loop-fake-provider-approval-960.png',
            Buffer.from(approvalImage.data, 'base64'));
        await evaluate(`(()=>{
            document.querySelectorAll('.research-loop-approval input[type=checkbox]').forEach(i=>i.click());
            const rationale=document.querySelector('.research-loop-approval textarea');
            rationale.value='This exact edge resolves the bounded decision gap.';
            rationale.dispatchEvent(new Event('input',{bubbles:true}));
            [...document.querySelectorAll('button')].find(b=>b.textContent.includes('APPROVE EXACT ACTION')).click();
        })()`);
        check(await waitFor(`document.querySelector('#research-loop-drawer').innerText.includes('STATE COMPLETED')`),
            'fake-provider: approved loop did not reach completed UI');
        const terminalText = await evaluate(`document.querySelector('#research-loop-drawer').innerText`);
        observed[name].terminalText = terminalText;
        check(terminalText.includes('COMPLETED UNVALIDATED')
            && terminalText.includes('NOT ELIGIBLE AS EVIDENCE'),
        'fake-provider: completed method result crossed the evidence boundary');
        check(!terminalText.includes('\nPAUSE\n') && !terminalText.includes('\nCANCEL\n')
            && terminalText.includes('This loop is terminal'),
        'fake-provider: terminal loop still exposes invalid mutation controls');
        await cdp.send('Page.reload', { ignoreCache: true }, session);
        check(await waitFor(`document.readyState==='complete'&&!!document.querySelector('#research-loop-toggle')&&document.querySelector('#durable-job')?.textContent.includes('${ids.networkJob}')`, 45000),
            'fake-provider: Workbench did not survive reload');
        await evaluate(`(()=>{const button=document.querySelector('#research-loop-toggle');button.focus();button.click();})()`);
        check(await waitFor(`document.querySelector('#research-loop-drawer').innerText.includes('LOOP COMPLETED')`),
            'fake-provider: terminal timeline did not survive reload');
        observed[name].timelineAfterReload = await evaluate(`document.querySelector('#research-loop-drawer').innerText.includes('RUNSET COMPLETED')`);
        check(observed[name].timelineAfterReload,
            'fake-provider: RunSet completion disappeared after reload');
    }
    const screenshot = await cdp.send('Page.captureScreenshot', {
        format: 'png', captureBeyondViewport: false,
    }, session);
    writeFileSync(`/tmp/dirac-research-loop-${name}-960.png`,
        Buffer.from(screenshot.data, 'base64'));
    await cdp.send('Input.dispatchKeyEvent', {
        type: 'keyDown', key: 'Escape', code: 'Escape',
    }, session);
    await cdp.send('Input.dispatchKeyEvent', {
        type: 'keyUp', key: 'Escape', code: 'Escape',
    }, session);
    check(await waitFor(`document.querySelector('#research-loop-drawer').hidden`),
        `${name}: Escape did not close drawer`);
    const returned = await evaluate(`({focus:document.activeElement?.id,
        expanded:document.querySelector('#research-loop-toggle').getAttribute('aria-expanded')})`);
    check(returned.focus === 'research-loop-toggle' && returned.expanded === 'false',
        `${name}: focus/disclosure did not return after Escape`);
    check(unexpected.length === 0, `${name}: unexpected HTTP commands ${unexpected.join(', ')}`);
    check(consoleErrors.length === 0, `${name}: ${consoleErrors.length} uncaught browser errors`);
    observed[name].returned = returned;
    await cdp.send('Target.closeTarget', { targetId: target.targetId });
}

try {
    const cdp = await connectChrome();
    await scenario(cdp, 'no-provider', false);
    await scenario(cdp, 'fake-provider', true);
    cdp.socket.close();
} finally {
    chrome.kill('SIGTERM');
}

process.stdout.write(`${JSON.stringify({
    ok: failures.length === 0, failures, observed,
    screenshots: ['/tmp/dirac-research-loop-no-provider-960.png',
        '/tmp/dirac-research-loop-fake-provider-approval-960.png',
        '/tmp/dirac-research-loop-fake-provider-960.png'],
    physical_execution: 'NOT_RUN_BROWSER_FAKE; BACKEND ACCEPTANCE IS SEPARATE',
}, null, 2)}\n`);
if (failures.length) process.exitCode = 1;
