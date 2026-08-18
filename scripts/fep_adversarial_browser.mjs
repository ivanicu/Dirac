import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';

const baseUrl=process.argv.find(value=>/^https?:/.test(value))||'http://127.0.0.1:1370/fep-workbench.html',campaignOnly=process.argv.includes('--campaign'),prepareOnly=process.argv.includes('--prepare')||campaignOnly;
const port=9400+(process.pid%200),profile=mkdtempSync('/tmp/dirac-fep-adversarial-');
const chrome=spawn('/usr/bin/google-chrome',[
    `--remote-debugging-port=${port}`,`--user-data-dir=${profile}`,'--headless=new','--no-sandbox',
    '--disable-dev-shm-usage','--use-angle=swiftshader','--enable-unsafe-swiftshader','--enable-webgl','about:blank',
],{ stdio: 'ignore' });
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const failures=[],observed={},consoleErrors=[];
const check=(condition,message)=>{ if (!condition)failures.push(message); };

try {
    let version;
    for (let index=0;index<100&&!version;index++) { try { version=await fetch(`http://127.0.0.1:${port}/json/version`).then(response=>response.ok?response.json():null); } catch {} if (!version)await pause(100); }
    if (!version)throw new Error('isolated Chrome CDP unavailable');
    const socket=new WebSocket(version.webSocketDebuggerUrl); await new Promise((resolve,reject)=>{ socket.addEventListener('open',resolve,{ once: true }); socket.addEventListener('error',reject,{ once: true }); });
    let id=0; const pending=new Map();
    socket.addEventListener('message',event=>{ const message=JSON.parse(String(event.data)); if (message.method==='Runtime.exceptionThrown')consoleErrors.push(message.params?.exceptionDetails?.text||'uncaught exception'); if (message.method==='Log.entryAdded'&&message.params?.entry?.level==='error')consoleErrors.push(`${message.params.entry.text} · ${message.params.entry.url||'no-url'}`); const request=pending.get(message.id); if (!request)return; pending.delete(message.id); message.error?request.reject(new Error(message.error.message)):request.resolve(message.result); });
    const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{ const requestId=++id; pending.set(requestId,{ resolve,reject }); socket.send(JSON.stringify({ id: requestId,method,params,...(sessionId?{ sessionId }: {}) })); });
    const copyId=`adversarial-${process.pid}`.slice(0,16),target=await send('Target.createTarget',{ url: `${baseUrl}?copy=${copyId}&new=1` }),attached=await send('Target.attachToTarget',{ targetId: target.targetId,flatten: true }),session=attached.sessionId;
    await send('Runtime.enable',{},session); await send('Log.enable',{},session); await send('Page.enable',{},session);
    const evaluateAt=async(sessionId,expression)=>{ const result=await send('Runtime.evaluate',{ expression,awaitPromise: true,returnByValue: true },sessionId); if (result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text); return result.result?.value; },evaluate=expression=>evaluateAt(session,expression);
    const waitFor=async(expression,timeout=30000)=>{ const started=Date.now(); while (Date.now()-started<timeout) { if (await evaluate(expression))return true; await pause(100); } return false; };
    await send('Emulation.setDeviceMetricsOverride',{ width: 1600,height: 1000,deviceScaleFactor: 1,mobile: false },session);
    check(await waitFor(`document.readyState==='complete'&&!!document.querySelector('#main-build')`),'workbench did not bootstrap');
    observed.initialBodyText=await evaluate(`document.body.innerText.length`); check(observed.initialBodyText>1000,'page rendered as blank/near-empty');
    await evaluate(`(()=>{const button=document.querySelector('#main-build');button.focus();button.click();})()`); check(await waitFor(`document.querySelector('#campaign-builder').open`),'builder did not open');
    await evaluate(`document.querySelector('#load-t4l-example').click()`); check(await waitFor(`document.querySelector('#campaign-builder').dataset.guideStep==='review'`,45000),'one-click eight-ligand example did not reach review');
    observed.exampleLines=await evaluate(`document.querySelector('#campaign-ligands').value.trim().split(/\\n/).length`); check(observed.exampleLines===8,`example retained ${observed.exampleLines}/8 ligand rows`);
    observed.uniqueExampleIds=await evaluate(`new Set(document.querySelector('#campaign-ligands').value.trim().split(/\\n/).map(row=>row.trim().split(/\\s+/)[0])).size`); check(observed.uniqueExampleIds===8,'example ligand IDs are not unique');
    observed.reference=await evaluate(`document.querySelector('#parent-compound-select').value`); check(observed.reference==='BEN','one-click example did not select BEN as reference');
    observed.reviewRows=await evaluate(`document.querySelectorAll('#review-ligand-rows > *').length`); check(observed.reviewRows===8,`review retained ${observed.reviewRows}/8 ligand identity cards`);
    observed.creationReady=await evaluate(`document.querySelector('#draft-readiness').textContent.trim()`); check(/READY/.test(observed.creationReady),`one-click example creation contract is not ready: ${observed.creationReady}`);
    observed.reviewEnabled=await evaluate(`!document.querySelector('#review-inputs').disabled`); check(observed.reviewEnabled,'one-click example still leaves REVIEW INPUTS disabled');

    if (prepareOnly) {
        await evaluate(`document.querySelector('#review-inputs').click()`);
        check(await waitFor(`document.querySelector('#review-inputs').textContent.includes('START PREPARATION')`),'input review did not expose the explicit preparation action');
        await evaluate(`(()=>{window.confirm=()=>true;document.querySelector('#review-inputs').click();})()`);
        observed.preparationReachedTerminalUi=await waitFor(`document.querySelector('#pose-reviewer').open||document.querySelector('#campaign-builder').dataset.stage==='prepared'||/FAILED|REFUSED|UNAVAILABLE|CANCELLED/.test((document.querySelector('#builder-next-label').textContent||'')+' '+(document.querySelector('#builder-notice').textContent||''))`,600000);
        if (await evaluate(`document.querySelector('#campaign-builder').dataset.stage==='prepared'&&!document.querySelector('#pose-reviewer').open`)) {
            await evaluate(`document.querySelector('#review-inputs').click()`);
            await waitFor(`document.querySelector('#pose-reviewer').open`,30000);
        }
        await waitFor(`document.querySelectorAll('#pose-review-list [data-pose-index]').length===8`,60000);
        observed.poseReviewerOpen=await evaluate(`document.querySelector('#pose-reviewer').open`);
        observed.preparedPoseRows=await evaluate(`document.querySelectorAll('#pose-review-list [data-pose-index]').length`);
        observed.preparationState=await evaluate(`document.querySelector('#builder-next-label').textContent.trim()+' · '+document.querySelector('#builder-notice').textContent.trim()`);
        check(observed.preparationReachedTerminalUi,'preparation produced neither pose review nor a visible terminal refusal');
        check(observed.poseReviewerOpen,`preparation did not open pose review: ${observed.preparationState}`);
        check(observed.preparedPoseRows===8,`preparation produced ${observed.preparedPoseRows}/8 review poses`);
        observed.policySummary=await evaluate(`document.querySelector('#pose-policy-summary').textContent.trim()`);
        check(observed.policySummary==='ALL AXES CONFIRMED',`preparation policy is not fully confirmed: ${observed.policySummary}`);
        observed.poseRows=await evaluate(`[...document.querySelectorAll('#pose-review-list [data-pose-index]')].map(row=>({label:row.querySelector('b')?.textContent?.trim(),status:row.querySelector('em')?.textContent?.trim(),className:row.className}))`);
        const poseEvidence=[];
        for (let poseIndex=0;poseIndex<8;poseIndex++) {
            await evaluate(`document.querySelector('#pose-row-${poseIndex}').scrollIntoView({block:'nearest'});document.querySelector('#pose-row-${poseIndex}').click()`);
            await pause(80);
            poseEvidence.push(await evaluate(`({label:document.querySelector('#pose-review-name').textContent.trim(),rmsd:document.querySelector('#pose-review-rmsd').textContent.trim(),coverage:document.querySelector('#pose-review-coverage').textContent.trim(),distance:document.querySelector('#pose-review-distance').textContent.trim(),pairs:document.querySelector('#pose-pair-summary').textContent.trim(),firstPair:document.querySelector('#pose-pair-witnesses article b')?.textContent?.trim()||''})`));
        }
        observed.poseEvidence=poseEvidence;
        observed.poseProgress=await evaluate(`document.querySelector('#pose-review-progress').textContent.trim()`);
        observed.webglFallbackVisible=await evaluate(`!document.querySelector('.pose-webgl-fallback').hidden`);
        observed.webglVisiblePixels=await evaluate(`(()=>{const canvas=document.querySelector('#pose-reviewer canvas'),gl=canvas.getContext('webgl2')||canvas.getContext('webgl');if(!gl)return 0;const pixels=new Uint8Array(canvas.width*canvas.height*4);gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);let count=0;for(let index=0;index<pixels.length;index+=4)if(pixels[index]>8||pixels[index+1]>8||pixels[index+2]>8)count++;return count})()`);
        observed.hardClashRows=await evaluate(`document.querySelectorAll('#pose-review-list .hard-clash').length`);
        observed.missingWitnessRows=await evaluate(`document.querySelectorAll('#pose-review-list .missing-witness').length`);
        check(observed.poseProgress==='8 / 8 POSES VIEWED',`pose review progress did not reach 8/8: ${observed.poseProgress}`);
        check(observed.hardClashRows===0,`${observed.hardClashRows} prepared poses contain a hard clash`);
        check(observed.missingWitnessRows===0,`${observed.missingWitnessRows} prepared poses lack a nearest-pair witness`);
        check(observed.webglVisiblePixels>100||observed.webglFallbackVisible,'3D renderer produced a blank frame without exposing its fallback');
        check(poseEvidence.every(row=>!Object.values(row).some(value=>String(value).includes('UNVERIFIED'))),'at least one pose metric remained UNVERIFIED');
        check(poseEvidence.every(row=>/\b[A-Z]{3} [A-Za-z0-9]+ \d+\b/.test(row.firstPair)),`server pair evidence omitted a protein residue identity`);
        await evaluate(`document.querySelectorAll('[data-review-check]').forEach(input=>{input.checked=true;input.dispatchEvent(new Event('change',{bubbles:true}))})`);
        observed.acceptEnabled=await evaluate(`!document.querySelector('#pose-review-accept').disabled`);
        check(observed.acceptEnabled,'8/8 fully evidenced poses did not unlock explicit pose acceptance');
        const image=await send('Page.captureScreenshot',{ format: 'png',captureBeyondViewport: false },session); writeFileSync('/tmp/fep-adversarial-preparation.png',Buffer.from(image.data,'base64'));
        if (campaignOnly&&observed.acceptEnabled) {
            await evaluate(`document.querySelector('#pose-review-accept').click()`);
            observed.poseAccepted=await waitFor(`document.querySelector('#campaign-builder').dataset.stage==='accepted'&&!document.querySelector('#pose-reviewer').open`,60000);
            check(observed.poseAccepted,'server did not record the exact 8-pose human review');
            await evaluate(`document.querySelector('#review-inputs').click()`);
            await waitFor(`((!document.querySelector('#campaign-builder').open)&&document.querySelectorAll('.network-node').length===8)||/FAILED|REFUSED|inconsistent|error/i.test(document.querySelector('#builder-notice').textContent)`,300000);
            observed.networkPlanned=await evaluate(`(!document.querySelector('#campaign-builder').open)&&document.querySelectorAll('.network-node').length===8`);
            observed.networkStatus=await evaluate(`document.querySelector('#status').textContent.trim()`);
            observed.networkNodes=await evaluate(`document.querySelectorAll('.network-node').length`);
            observed.networkEdges=await evaluate(`document.querySelectorAll('#edge-queue .queue-row').length`);
            observed.networkJob=await evaluate(`document.querySelector('#durable-job').textContent.trim()`);
            observed.networkArtifact=await evaluate(`document.querySelector('#footer-artifact').textContent.trim()`);
            check(observed.networkPlanned,`accepted poses did not produce an 8-node campaign network: ${observed.networkStatus}`);
            check(observed.networkNodes===8,`campaign network rendered ${observed.networkNodes}/8 nodes`);
            check(observed.networkEdges>=7,`campaign network rendered only ${observed.networkEdges} edges`);
            check(/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(observed.networkJob),`planner did not expose a complete durable Job ID: ${observed.networkJob}`);
            const networkImage=await send('Page.captureScreenshot',{ format: 'png',captureBeyondViewport: false },session); writeFileSync('/tmp/fep-adversarial-campaign.png',Buffer.from(networkImage.data,'base64'));
            observed.systemSelectionReady=await waitFor(`!document.querySelector('#validate-contract').disabled`,60000);
            observed.systemStatus=await evaluate(`document.querySelector('#system-status').textContent.trim()`);
            check(observed.systemSelectionReady,`exact prepared system / poses did not auto-bind: ${observed.systemStatus}`);
            if (observed.systemSelectionReady) {
                await evaluate(`document.querySelector('#validate-contract').click()`);
                await waitFor(`/SYSTEM QUALIFIED|EXECUTION REMAINS LOCKED/.test(document.querySelector('#status').textContent)`,300000);
                observed.systemQualified=await evaluate(`document.querySelector('#contract-gate').textContent.includes('SERVER-ATTESTED')`);
                observed.systemQualificationStatus=await evaluate(`document.querySelector('#status').textContent.trim()+' · '+document.querySelector('#contract-gate').textContent.trim()+' · '+document.querySelector('#contract-detail').textContent.trim()`);
                check(observed.systemQualified,`posed-system build did not qualify both OpenFE legs: ${observed.systemQualificationStatus}`);
                observed.auditStartLocked=await evaluate(`document.querySelector('#prepare-edge').disabled&&document.querySelector('#prepare-edge').textContent.includes('AUDIT COPY')`);
                check(observed.auditStartLocked,'isolated audit copy exposed physical FEP start after qualification');
                const qualifiedImage=await send('Page.captureScreenshot',{ format: 'png',captureBeyondViewport: false },session); writeFileSync('/tmp/fep-adversarial-qualified.png',Buffer.from(qualifiedImage.data,'base64'));
            }
        }
    } else {

    await evaluate(`(()=>{const input=document.querySelector('#campaign-question');input.value='<img src=x onerror=globalThis.__owned=1>';input.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-guide-step="review"]').click();})()`);
    observed.injectedElements=await evaluate(`document.querySelectorAll('img[src="x"]').length`); check(observed.injectedElements===0,'hostile project text created executable markup'); check(await evaluate(`globalThis.__owned!==1`),'hostile project text executed');

    await evaluate(`document.querySelector('[data-guide-step="ligands"]').click()`);
    const originalSeries=await evaluate(`document.querySelector('#campaign-ligands').value`);
    await evaluate(`(()=>{const input=document.querySelector('#campaign-ligands');input.value+='\\nBEN C1CC';input.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#review-inputs').click();})()`);
    await pause(800);
    observed.invalidStayedInLigands=await evaluate(`document.querySelector('#campaign-builder').dataset.guideStep==='ligands'`); check(observed.invalidStayedInLigands,'invalid/duplicate ligand input advanced past validation');
    await evaluate(`(()=>{const input=document.querySelector('#campaign-ligands');input.value=${JSON.stringify(originalSeries)};input.dispatchEvent(new Event('input',{bubbles:true}));})()`);

    await evaluate(`(()=>{document.querySelector('.ligand-alternatives').open=true;const button=document.querySelector('#draw-ligand');button.focus();button.click();})()`);
    check(await waitFor(`document.querySelector('#molecule-sketcher').open`),'molecule sketcher did not open');
    observed.sketcherModal=await evaluate(`document.querySelector('#molecule-sketcher').matches(':modal')`); check(observed.sketcherModal,'molecule sketcher is not a modal dialog');
    await send('Input.dispatchKeyEvent',{ type: 'keyDown',key: 'Escape',code: 'Escape' },session); await send('Input.dispatchKeyEvent',{ type: 'keyUp',key: 'Escape',code: 'Escape' },session);
    check(await waitFor(`!document.querySelector('#molecule-sketcher').open`),'Escape did not close molecule sketcher');
    observed.sketcherFocusReturn=await evaluate(`document.activeElement?.id`); check(observed.sketcherFocusReturn==='draw-ligand',`sketcher returned focus to ${observed.sketcherFocusReturn||'nothing'}`);

    await pause(200);
    await send('Input.dispatchKeyEvent',{ type: 'keyDown',key: 'Escape',code: 'Escape' },session); await send('Input.dispatchKeyEvent',{ type: 'keyUp',key: 'Escape',code: 'Escape' },session);
    check(await waitFor(`!document.querySelector('#campaign-builder').open`),'Escape did not close campaign builder');
    observed.builderFocusReturn=await evaluate(`document.activeElement?.id`); check(observed.builderFocusReturn==='main-build',`builder returned focus to ${observed.builderFocusReturn||'nothing'}`);
    await evaluate(`document.querySelector('#main-build').focus()`); await send('Input.dispatchKeyEvent',{ type: 'rawKeyDown',key: ' ',code: 'Space',windowsVirtualKeyCode: 32,nativeVirtualKeyCode: 32 },session); await send('Input.dispatchKeyEvent',{ type: 'keyUp',key: ' ',code: 'Space',windowsVirtualKeyCode: 32,nativeVirtualKeyCode: 32 },session);
    check(await waitFor(`document.querySelector('#campaign-builder').open`),'keyboard Space did not reopen campaign builder');
    observed.builderModal=await evaluate(`document.querySelector('#campaign-builder').matches(':modal')`); check(observed.builderModal,'campaign builder is not a modal dialog');
    for (let index=0;index<40;index++) { await send('Input.dispatchKeyEvent',{ type: 'keyDown',key: 'Tab',code: 'Tab' },session); await send('Input.dispatchKeyEvent',{ type: 'keyUp',key: 'Tab',code: 'Tab' },session); }
    observed.focusEscapedModal=await evaluate(`!document.activeElement?.closest('#campaign-builder')`); check(!observed.focusEscapedModal,'Tab focus escaped the campaign builder modal');

    const widths=[1600,1180,960,720]; observed.layouts=[];
    for (const width of widths) { await send('Emulation.setDeviceMetricsOverride',{ width,height: 900,deviceScaleFactor: 1,mobile: false },session); await pause(100); const layout=await evaluate(`({width:document.documentElement.scrollWidth,viewport:innerWidth,builderWidth:document.querySelector('#campaign-builder').getBoundingClientRect().width})`); observed.layouts.push(layout); check(layout.width<=layout.viewport+1,`${width}px viewport has horizontal overflow ${layout.width-layout.viewport}px`); }

    observed.unnamedControls=await evaluate(`Array.from(document.querySelectorAll('button,input,select,textarea')).filter(element=>{const style=getComputedStyle(element),box=element.getBoundingClientRect(),label=element.labels?.[0]?.textContent||'';if(style.display==='none'||style.visibility==='hidden'||box.width===0||box.height===0||element.closest('[hidden],[inert]'))return false;const name=(element.getAttribute('aria-label')||element.getAttribute('title')||label||element.textContent||element.value||'').trim();return !name;}).map(element=>element.id||element.outerHTML.slice(0,80))`);
    check(observed.unnamedControls.length===0,`${observed.unnamedControls.length} visible controls have no accessible name`);
    observed.tinyTargets=await evaluate(`Array.from(document.querySelectorAll('button,input,select,textarea')).filter(element=>{const style=getComputedStyle(element),box=element.getBoundingClientRect();return style.display!=='none'&&style.visibility!=='hidden'&&box.width>0&&box.height>0&&!element.closest('[hidden],[inert]')&&!element.disabled&&(box.width<32||box.height<32);}).map(element=>({id:element.id,w:Math.round(element.getBoundingClientRect().width),h:Math.round(element.getBoundingClientRect().height)}))`);
    check(observed.tinyTargets.length===0,`${observed.tinyTargets.length} active controls are smaller than 32px`);
    const secondTarget=await send('Target.createTarget',{ url: `${baseUrl}?copy=${copyId}` }),secondAttached=await send('Target.attachToTarget',{ targetId: secondTarget.targetId,flatten: true }),secondSession=secondAttached.sessionId;
    await send('Runtime.enable',{},secondSession);
    const secondReadyStarted=Date.now(); while (Date.now()-secondReadyStarted<30000&&!await evaluateAt(secondSession,`document.readyState==='complete'&&!!document.querySelector('#main-build')`))await pause(100);
    await evaluateAt(secondSession,`localStorage.setItem(${JSON.stringify(`dirac.rbfe.active_network_job_id.copy.${copyId}`)},'00000000-0000-4000-8000-000000000099')`);
    observed.crossTabInvalidated=await waitFor(`document.querySelector('#status').textContent.includes('EXTERNAL TAB UPDATED')`);
    check(observed.crossTabInvalidated,'another tab changed execution state without invalidating this tab');
    observed.crossTabExecutionLocked=await evaluate(`document.querySelector('#prepare-edge').disabled&&document.querySelector('#validate-contract').disabled`);
    check(observed.crossTabExecutionLocked,'cross-tab state change left execution controls enabled');
    await send('Target.closeTarget',{ targetId: secondTarget.targetId });
    const image=await send('Page.captureScreenshot',{ format: 'png',captureBeyondViewport: false },session); writeFileSync('/tmp/fep-adversarial-round1.png',Buffer.from(image.data,'base64'));
    observed.corruptedKeys=await evaluate(`(()=>{const suffix=${JSON.stringify(`.copy.${copyId}`)},bases=['dirac.rbfe.campaign_draft.v2','dirac.rbfe.campaign_conflict.v2','dirac.rbfe.pending_prepare_job.v1','dirac.rbfe.pending_planner_job.v2','dirac.rbfe.planner_output_receipt.v1','dirac.rbfe.active_run.v2','dirac.rbfe.active_campaign_context','dirac.rbfe.active_network_job_id'],keys=bases.map(key=>key+suffix);for(const key of keys)localStorage.setItem(key,'{BROKEN');const url=new URL(location.href);url.searchParams.delete('new');history.replaceState(null,'',url);return keys.length;})()`);
    await send('Page.reload',{ ignoreCache: true },session);
    observed.corruptReloadRecovered=await waitFor(`document.readyState==='complete'&&document.body.innerText.length>1000&&!!document.querySelector('#main-build')`,30000);
    check(observed.corruptReloadRecovered,'corrupt local receipts made the workbench blank or unbootable');
    observed.consoleErrors=consoleErrors; check(consoleErrors.length===0,`${consoleErrors.length} browser errors were emitted`);
    }
    socket.close();
} finally { chrome.kill('SIGTERM'); }

process.stdout.write(`${JSON.stringify({ ok: failures.length===0,failures,observed },null,2)}\n`);
if (failures.length)process.exitCode=1;
