/**
 * Executable check of the clipping claim, run against mol*'s OWN Camera rather
 * than a transcription of its arithmetic. The earlier evidence for this fix was
 * a hand-rewritten copy of updateClip in another language, which is exactly the
 * kind of proxy that can agree with a bug it inherited from the transcription.
 * This imports the real class and reads camera.near after a real focus.
 */

import { Camera } from '../../mol-canvas3d/camera';
import { Vec3 } from '../../mol-math/linear-algebra/3d/vec3';

const PROTEIN_RADIUS = 28;      // a ~35 kDa domain, angstrom
const FEATURE_RADIUS = 6;       // what focusSphere passes for a pharmacophore feature
const ORIGIN = Vec3.create(0, 0, 0);

function makeCamera() {
    const camera = new Camera();
    camera.setState({
        mode: 'perspective',
        target: Vec3.create(0, 0, 0),
        position: Vec3.create(0, 0, 100),
        up: Vec3.create(0, 1, 0),
        radius: PROTEIN_RADIUS,
        radiusMax: PROTEIN_RADIUS,
        fov: Math.PI / 4,
        minNear: 5,
        minFar: 5,
        fog: 50,
        clipFar: true,
        scale: 1,
    }, 0);
    camera.viewport.width = 1000;
    camera.viewport.height = 800;
    camera.update();
    return camera;
}

/** Depth of scene visible in front of the camera target, in angstrom. */
function visibleDepthInFront(camera: Camera) {
    const distance = Vec3.distance(camera.state.position, camera.state.target);
    return distance - camera.near;
}

const results: { name: string, depth: number, distance: number, pass: boolean }[] = [];
const record = (name: string, camera: Camera, pass: (d: number) => boolean) => {
    camera.update();
    const d = visibleDepthInFront(camera);
    const distance = Vec3.distance(camera.state.position, camera.state.target);
    results.push({ name, depth: d, distance, pass: pass(d) });
};

// --- 1. the bug, reproduced through the real API -------------------------------
{
    const camera = makeCamera();
    camera.focus(ORIGIN, FEATURE_RADIUS, 0);            // what the old code did
    record('OLD focus(feature): visible depth', camera, d => d < PROTEIN_RADIUS);

    // and now dolly the camera back, which is the gesture a user reaches for
    for (const factor of [2, 4, 10, 25]) {
        const dir = Vec3.sub(Vec3(), camera.state.position, camera.state.target);
        Vec3.setMagnitude(dir, dir, 100 * factor);
        camera.setState({ position: Vec3.add(Vec3(), camera.state.target, dir) }, 0);
        record(`OLD after zooming out ${factor}x`, camera, d => d < PROTEIN_RADIUS);
    }
}

// --- 2. the fix: same focus, radius field widened before the transition --------
//
// The first version of this file asserted that the fixed path shows PROTEIN_RADIUS
// of depth immediately after focusing, and it failed at 10.68. The assertion was
// wrong, not the code: focusing a 6 A feature puts the camera 15.7 A from the
// target, so anything 28 A in front of the target is BEHIND THE CAMERA and no
// clipping setting can reveal it. The reachable maximum is cameraDistance - minNear.
// What the fix actually has to deliver is that ZOOMING OUT now recovers the view,
// which is the thing the user tried and which used to be impossible.
{
    const camera = makeCamera();
    const snapshot = camera.getFocus(ORIGIN, FEATURE_RADIUS);
    snapshot.radius = PROTEIN_RADIUS;                   // what camera-slab.ts does
    camera.setState(snapshot, 0);
    const closeDistance = Vec3.distance(camera.state.position, camera.state.target);
    record('NEW focus(feature): at the geometric max', camera, d => d > closeDistance - 5 - 1e-6);

    // Checked BEFORE the zoom-out loop below: an earlier version of this file compared
    // the distance after the loop had already dollied the camera to 2500 A, and failed
    // its own moved camera. Read the quantity at the moment the claim is about.
    const reference = makeCamera();
    reference.focus(ORIGIN, FEATURE_RADIUS, 0);
    const referenceDistance = Vec3.distance(reference.state.position, reference.state.target);
    results.push({
        name: 'NEW flew as close as the old focus did',
        depth: closeDistance, distance: referenceDistance,
        pass: Math.abs(closeDistance - referenceDistance) < 1e-6,
    });

    for (const factor of [2, 4, 10, 25]) {
        const dir = Vec3.sub(Vec3(), camera.state.position, camera.state.target);
        Vec3.setMagnitude(dir, dir, 100 * factor);
        camera.setState({ position: Vec3.add(Vec3(), camera.state.target, dir) }, 0);
        record(`NEW after zooming out ${factor}x`, camera, d => d >= PROTEIN_RADIUS);
    }
}

// --- 3. restoreSceneSlab: widen without moving --------------------------------
{
    const camera = makeCamera();
    camera.focus(ORIGIN, FEATURE_RADIUS, 0);
    const before = Vec3.clone(camera.state.position);
    camera.setState({ radius: PROTEIN_RADIUS }, 0);     // what restoreSceneSlab does
    // Same geometry as above: the camera is still parked close, so the bar is the
    // reachable maximum, not the protein's full extent.
    const parked = Vec3.distance(camera.state.position, camera.state.target);
    record('RESTORE lifts depth to the geometric max', camera, d => d > parked - 5 - 1e-6);
    results.push({
        name: 'RESTORE left the camera where it was',
        depth: Vec3.distance(before, camera.state.position), distance: 0,
        pass: Vec3.distance(before, camera.state.position) < 1e-9,
    });
}

let failed = 0;
for (const r of results) {
    if (!r.pass) failed++;
    console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.name.padEnd(42)} depth=${r.depth.toFixed(2)}  cameraDist=${r.distance.toFixed(1)}`);
}
console.log(`\nprotein needs ${PROTEIN_RADIUS} A of depth to be fully visible`);
console.log(failed === 0 ? 'ALL PASS' : `${failed} FAILED`);
if (failed) process.exit(1);
