/**
 * Focus without collapsing the visible slab.
 *
 * mol*'s near clipping plane is `cameraDistance - camera.state.radius`
 * (mol-canvas3d/camera.ts, updateClip), and `camera.focus(target, radius)` sets
 * `state.radius` to the radius of the thing you focused. The two facts compose
 * badly: after focusing a 6 Å pharmacophore feature, only a 6 Å slab in front of
 * the target survives clipping, and because `cameraDistance` appears in both the
 * camera position and the near plane, **dollying the camera back cancels out
 * exactly**. Measured against the code: at camera distances of 40 / 160 / 1000 Å
 * the visible depth in front of the target is 6.0 / 6.0 / 6.0 Å. The protein needs
 * about 28 Å. So the one gesture a user will reach for — zoom out — is
 * mathematically incapable of recovering the view, and the sliced-open surfaces
 * render in `uInteriorColor`, which reads as dark caps on the cartoon.
 *
 * The fix is not to restore the radius after the camera arrives: the transition
 * lerps `radius` frame by frame (mol-canvas3d/camera/transition-functions.ts),
 * so a post-call `setState` is overwritten until the animation ends. Instead the
 * focus snapshot is built with the small radius — which is what decides how close
 * the camera flies — and then its `radius` field alone is widened to the scene
 * before the transition starts. Position comes from the target, clipping comes
 * from the scene, and there is no timing race.
 */

import { Sphere3D } from '../mol-math/geometry';
import { BoundaryHelper } from '../mol-math/geometry/boundary-helper';
import { Loci } from '../mol-model/loci';
import { PluginContext } from '../mol-plugin/context';

export interface SlabSafeFocusOptions {
    minRadius?: number;
    extraRadius?: number;
    durationMs?: number;
}

const boundaryHelper = new BoundaryHelper('98');

function unionSphere(loci: Loci | Loci[]): Sphere3D | undefined {
    const list = Array.isArray(loci) ? loci : [loci];
    const spheres: Sphere3D[] = [];
    for (const l of list) {
        const s = Loci.getBoundingSphere(l);
        if (s && s.radius >= 0) spheres.push(s);
    }
    if (spheres.length === 0) return undefined;
    if (spheres.length === 1) return spheres[0];

    boundaryHelper.reset();
    for (const s of spheres) boundaryHelper.includeSphere(s);
    boundaryHelper.finishedIncludeStep();
    for (const s of spheres) boundaryHelper.radiusSphere(s);
    return boundaryHelper.getSphere();
}

/** Fly to `sphere` but keep the clipping slab wide enough for the whole scene. */
export function focusSphereKeepingSlab(plugin: PluginContext, sphere: Sphere3D, options: SlabSafeFocusOptions = {}) {
    const canvas3d = plugin.canvas3d;
    if (!canvas3d) return;

    const { minRadius = 1, extraRadius = 4, durationMs = 250 } = options;
    const focusRadius = Math.max(sphere.radius + extraRadius, minRadius);

    const snapshot = canvas3d.camera.getFocus(sphere.center, focusRadius);
    // Position was computed from focusRadius above; only the clip radius is widened.
    snapshot.radius = Math.max(canvas3d.boundingSphere.radius, focusRadius);

    canvas3d.requestCameraReset({ snapshot, durationMs });
}

/** Same, for a loci or a set of loci. */
export function focusLociKeepingSlab(plugin: PluginContext, loci: Loci | Loci[], options: SlabSafeFocusOptions = {}) {
    const sphere = unionSphere(loci);
    if (!sphere) return;
    focusSphereKeepingSlab(plugin, sphere, options);
}

/**
 * Widen the slab to the scene without moving the camera. For use after any focus
 * path this module does not own — the camera stays where the user left it and only
 * the clipping recovers.
 */
export function restoreSceneSlab(plugin: PluginContext) {
    const canvas3d = plugin.canvas3d;
    if (!canvas3d) return;
    const r = canvas3d.boundingSphere.radius;
    if (r > 0) canvas3d.camera.setState({ radius: r }, 0);
}
