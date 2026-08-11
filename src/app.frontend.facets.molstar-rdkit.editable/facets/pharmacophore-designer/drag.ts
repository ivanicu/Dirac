/**
 * Pharmacophore Designer — 3D drag controller.
 *
 * Hover over a designer feature ARMS the drag: the trackball's drag bindings
 * are suppressed (so the camera holds still) and the cursor turns into a
 * grab hand. Dragging then moves the feature in the camera plane at its own
 * depth: the pointer position is unprojected at the feature's projected
 * window-space z, preserving the grab offset so the feature does not jump to
 * the cursor. Releasing restores the camera bindings.
 *
 * Why hover-armed rather than drag-start-picked: the pick precedes the
 * button press, so the trackball can be disabled BEFORE the drag begins —
 * there is no first-frame race where both the camera and the feature move.
 *
 * Coordinate contract (mirrors PickHelper.identifyInternal): input x/y are
 * canvas-relative CSS pixels, GL window coords are x·pixelRatio with y
 * flipped against drawingBufferHeight; Camera.project/unproject speak GL
 * window coords with z in [0, 1].
 */

import { PluginContext } from '../../../mol-plugin/context';
import { Vec3, Vec4 } from '../../../mol-math/linear-algebra';
import { Binding } from '../../../mol-util/binding';
import { ButtonsType } from '../../../mol-util/input/input-observer';
import { designerFeatureIndexFromLoci } from './shape';
import type { DesignerFeature } from './model';

export interface DesignerDragCallbacks {
    getFeature(index: number): DesignerFeature | undefined;
    /** Fired at most once per animation frame while dragging. */
    onDragMove(index: number, position: Vec3): void;
    onDragEnd(index: number): void;
}

const SuppressedDragBindings = {
    dragRotate: Binding.Empty,
    dragRotateZ: Binding.Empty,
    dragPan: Binding.Empty,
    dragZoom: Binding.Empty,
    dragFocus: Binding.Empty,
    dragFocusZoom: Binding.Empty,
};
type DragBindingKey = keyof typeof SuppressedDragBindings;
const DragBindingKeys = Object.keys(SuppressedDragBindings) as DragBindingKey[];

export function installDesignerDrag(plugin: PluginContext, callbacks: DesignerDragCallbacks): { dispose(): void } {
    let hoveredIndex: number | null = null;
    let draggingIndex: number | null = null;
    let savedBindings: Partial<Record<DragBindingKey, Binding>> | null = null;

    /** Grab state: window-space depth of the feature + world-space grab offset. */
    let grabDepth = 0;
    const grabOffset = Vec3();
    const tmpWindow = Vec3();
    const tmpWorld = Vec3();
    const tmpProjected = Vec4();

    let rafHandle = 0;
    const pendingPosition = Vec3();
    let pendingIndex: number | null = null;

    const canvas = plugin.canvas3dContext?.canvas;

    function setCursor(cursor: '' | 'grab' | 'grabbing') {
        if (canvas) canvas.style.cursor = cursor;
    }

    function suppressTrackball() {
        if (savedBindings || !plugin.canvas3d) return;
        const current = plugin.canvas3d.attribs.trackball.bindings;
        savedBindings = {};
        for (const key of DragBindingKeys) savedBindings[key] = current[key];
        plugin.canvas3d.setAttribs({ trackball: { bindings: { ...current, ...SuppressedDragBindings } } });
    }

    function restoreTrackball() {
        if (!savedBindings || !plugin.canvas3d) return;
        const current = plugin.canvas3d.attribs.trackball.bindings;
        plugin.canvas3d.setAttribs({ trackball: { bindings: { ...current, ...savedBindings } } });
        savedBindings = null;
    }

    /** Canvas-relative CSS px → GL window coords (see PickHelper.identifyInternal). */
    function toWindowCoords(x: number, y: number, out: Vec3, z: number) {
        const c3d = plugin.canvas3d!;
        const pixelRatio = c3d.webgl.pixelRatio;
        const height = c3d.webgl.gl.drawingBufferHeight;
        Vec3.set(out, x * pixelRatio, height - y * pixelRatio, z);
        return out;
    }

    function beginDrag(index: number, startX: number, startY: number): boolean {
        const c3d = plugin.canvas3d;
        const feature = callbacks.getFeature(index);
        if (!c3d || !feature) return false;

        c3d.camera.project(tmpProjected, feature.position);
        grabDepth = tmpProjected[2];
        if (!Number.isFinite(grabDepth) || grabDepth <= 0 || grabDepth >= 1) return false;

        toWindowCoords(startX, startY, tmpWindow, grabDepth);
        c3d.camera.unproject(tmpWorld, tmpWindow);
        Vec3.sub(grabOffset, feature.position, tmpWorld);

        draggingIndex = index;
        setCursor('grabbing');
        return true;
    }

    function moveDrag(x: number, y: number) {
        const c3d = plugin.canvas3d;
        if (!c3d || draggingIndex === null) return;
        toWindowCoords(x, y, tmpWindow, grabDepth);
        c3d.camera.unproject(tmpWorld, tmpWindow);
        Vec3.add(pendingPosition, tmpWorld, grabOffset);
        pendingIndex = draggingIndex;
        if (!rafHandle) {
            rafHandle = requestAnimationFrame(() => {
                rafHandle = 0;
                if (pendingIndex !== null) callbacks.onDragMove(pendingIndex, pendingPosition);
            });
        }
    }

    function endDrag() {
        if (draggingIndex === null) return;
        const index = draggingIndex;
        draggingIndex = null;
        pendingIndex = null;
        if (rafHandle) {
            cancelAnimationFrame(rafHandle);
            rafHandle = 0;
        }
        callbacks.onDragEnd(index);
        if (hoveredIndex === null) {
            restoreTrackball();
            setCursor('');
        } else {
            setCursor('grab');
        }
    }

    const hoverSub = plugin.behaviors.interaction.hover.subscribe(e => {
        const index = designerFeatureIndexFromLoci(e.current.loci);
        if (index !== null) {
            hoveredIndex = index;
            if (draggingIndex === null) {
                suppressTrackball();
                setCursor('grab');
            }
        } else {
            hoveredIndex = null;
            if (draggingIndex === null) {
                restoreTrackball();
                setCursor('');
            }
        }
    });

    const dragSub = plugin.behaviors.interaction.drag.subscribe(e => {
        if (draggingIndex !== null) {
            moveDrag(e.pageEnd[0], e.pageEnd[1]);
            return;
        }
        // Start only for a primary-button drag that began on an armed feature.
        if (hoveredIndex === null) return;
        if (!(e.buttons & ButtonsType.Flag.Primary)) return;
        if (beginDrag(hoveredIndex, e.pageStart[0], e.pageStart[1])) {
            moveDrag(e.pageEnd[0], e.pageEnd[1]);
        }
    });

    const endSub = plugin.canvas3d?.input.interactionEnd.subscribe(() => endDrag());
    const leaveSub = plugin.canvas3d?.input.leave.subscribe(() => endDrag());

    return {
        dispose() {
            hoverSub.unsubscribe();
            dragSub.unsubscribe();
            endSub?.unsubscribe();
            leaveSub?.unsubscribe();
            endDrag();
            restoreTrackball();
            setCursor('');
        },
    };
}
