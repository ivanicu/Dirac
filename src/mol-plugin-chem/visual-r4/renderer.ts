import {
    AgXToneMapping,
    AmbientLight,
    Color,
    DirectionalLight,
    Group,
    Mesh,
    MeshBasicMaterial,
    PerspectiveCamera,
    PostProcessing,
    Raycaster,
    Scene,
    SphereGeometry,
    SRGBColorSpace,
    Vector2,
    Vector3,
    WebGPURenderer,
    type Material,
    type Object3D,
} from 'three/webgpu';
import { float, mrt, normalView, output, pass, uniform, vec3, vec4 } from 'three/tsl';
import { ao } from 'three/addons/tsl/display/GTAONode.js';
import { outline } from 'three/addons/tsl/display/OutlineNode.js';
import type { Subscription } from 'rxjs';
import { OrderedSet } from '../../mol-data/int';
import { StructureElement } from '../../mol-model/structure';
import { Volume } from '../../mol-model/volume';
import type { PluginContext } from '../../mol-plugin/context';
import { PluginStateObject } from '../../mol-plugin-state/objects';
import { buildR4StructureSnapshot, atomLoci, bondLoci } from './bridge';
import type { R4Annotation, R4StructureSnapshot } from './types';
import { createR4Cartoon } from './representations/cartoon';
import { createR4Surface } from './representations/surface';
import { createR4Density } from './representations/density';
import { deriveR4Annotations } from './representations/annotations';
import { createR4Nucleic } from './representations/nucleic';
import { createR4Bonds, createR4Spheres } from './representations/atomic';

const tmpPosition = new Vector3();

export type R4RepresentationOverride = (
    snapshot: R4StructureSnapshot,
    plugin: PluginContext,
) => Object3D | Promise<Object3D>;

function disposeObject(object: Object3D) {
    object.traverse(child => {
        const candidate = child as any;
        candidate.geometry?.dispose?.();
        const material = candidate.material as Material | Material[] | undefined;
        const disposeMaterial = (item: Material) => {
            (item as any).map?.dispose?.();
            item.dispose();
        };
        if (Array.isArray(material)) material.forEach(disposeMaterial);
        else if (material) disposeMaterial(material);
    });
}

export class R4HybridRenderer {
    readonly canvas = document.createElement('canvas');
    readonly annotationLayer = document.createElement('div');
    readonly scene = new Scene();
    readonly camera = new PerspectiveCamera();
    readonly molecularRoot = new Group();
    readonly hoverMarker = new Mesh(new SphereGeometry(0.36, 16, 12), new MeshBasicMaterial({ color: 0x70e1d1, wireframe: true }));
    readonly selectionMarker = new Mesh(new SphereGeometry(0.48, 20, 14), new MeshBasicMaterial({ color: 0xffc857, wireframe: true }));
    readonly renderer = new WebGPURenderer({ canvas: this.canvas, antialias: true, alpha: false });
    readonly postProcessing = new PostProcessing(this.renderer);

    private readonly subscriptions: Subscription[] = [];
    private readonly raycaster = new Raycaster();
    private readonly pointer = new Vector2();
    private readonly resizeObserver: ResizeObserver;
    private snapshot?: R4StructureSnapshot;
    private disposed = false;
    private rebuildGeneration = 0;
    private customAnnotations: readonly R4Annotation[] = [];
    private readonly annotationEntries: { element: HTMLDivElement, snapshot: R4StructureSnapshot, annotation: R4Annotation }[] = [];
    private representationOverride?: R4RepresentationOverride;
    private representationOverrideLabel = 'full-r4';

    constructor(readonly plugin: PluginContext, readonly target: HTMLElement) {
        this.canvas.className = 'r4-webgpu-canvas';
        Object.assign(this.canvas.style, {
            position: 'absolute', inset: '0', width: '100%', height: '100%',
            pointerEvents: 'none', zIndex: '2',
        });
        if (getComputedStyle(target).position === 'static') target.style.position = 'relative';
        target.appendChild(this.canvas);
        this.annotationLayer.className = 'r4-annotation-layer';
        Object.assign(this.annotationLayer.style, {
            position: 'absolute', inset: '0', pointerEvents: 'none', zIndex: '3', overflow: 'hidden',
        });
        target.appendChild(this.annotationLayer);

        this.scene.background = new Color(0x0d141b);
        this.hoverMarker.visible = false;
        this.selectionMarker.visible = false;
        this.hoverMarker.renderOrder = 200;
        this.selectionMarker.renderOrder = 201;
        this.scene.add(this.molecularRoot, this.hoverMarker, this.selectionMarker);
        this.installLights();
        this.resizeObserver = new ResizeObserver(() => this.resize());
        this.resizeObserver.observe(target);

        this.onPointerMove = this.onPointerMove.bind(this);
        this.onClick = this.onClick.bind(this);
        target.addEventListener('pointermove', this.onPointerMove);
        target.addEventListener('click', this.onClick);
    }

    async init() {
        await this.renderer.init();
        this.canvas.dataset.backend = (this.renderer as any).backend?.isWebGPUBackend ? 'webgpu' : 'webgl2';
        this.renderer.outputColorSpace = SRGBColorSpace;
        this.renderer.toneMapping = AgXToneMapping;
        this.renderer.toneMappingExposure = 1.05;
        this.renderer.shadowMap.enabled = true;
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        const scenePass = pass(this.scene, this.camera);
        scenePass.setMRT(mrt({ output, normal: normalView }));
        const sceneColor = scenePass.getTextureNode('output');
        const sceneNormal = scenePass.getTextureNode('normal');
        const sceneDepth = scenePass.getTextureNode('depth');
        const ambientOcclusion = ao(sceneDepth, sceneNormal, this.camera);
        ambientOcclusion.resolutionScale = 0.5;
        ambientOcclusion.radius.value = 1.1;
        ambientOcclusion.thickness.value = 1.4;
        const occlusion = ambientOcclusion.getTextureNode().r.mul(0.62).add(0.38);
        const occludedColor = sceneColor.mul(vec4(vec3(occlusion), 1));
        const outlines = outline(this.scene, this.camera, {
            selectedObjects: [this.molecularRoot],
            edgeThickness: float(0.7),
            edgeGlow: float(0),
            downSampleRatio: 2,
        });
        const outlineColor = outlines.visibleEdge
            .mul(uniform(new Color(0x17222c)))
            .add(outlines.hiddenEdge.mul(uniform(new Color(0x263746))))
            .mul(0.42);
        this.postProcessing.outputNode = occludedColor.add(outlineColor);
        this.resize();

        const molCamera = this.plugin.canvas3d?.camera;
        if (molCamera) this.subscriptions.push(molCamera.changed.subscribe(() => this.syncCamera()));
        this.subscriptions.push(this.plugin.managers.structure.hierarchy.behaviors.selection.subscribe(() => {
            void this.rebuildCurrentStructure();
        }));
        this.subscriptions.push(this.plugin.managers.volume.hierarchy.behaviors.selection.subscribe(() => {
            void this.rebuildCurrentStructure();
        }));
        this.subscriptions.push(this.plugin.managers.structure.selection.events.changed.subscribe(() => {
            this.syncSelectionMarker();
        }));
        this.subscriptions.push(this.plugin.state.data.events.object.updated.subscribe(({ obj }) => {
            if (PluginStateObject.Molecule.Structure.is(obj) || PluginStateObject.Volume.Data.is(obj)) void this.rebuildCurrentStructure();
        }));
        this.renderer.setAnimationLoop(() => {
            this.syncCamera();
            this.postProcessing.render();
        });

        const molCanvas = this.plugin.canvas3d?.webgl.gl.canvas;
        if (molCanvas instanceof HTMLCanvasElement) molCanvas.style.opacity = '0';
        await this.rebuildCurrentStructure();
    }

    async rebuildCurrentStructure() {
        if (this.disposed) return;
        const buildStart = performance.now();
        const generation = ++this.rebuildGeneration;
        const structures = this.plugin.managers.structure.hierarchy.current.structures.flatMap(ref => ref.cell.obj ? [ref.cell.obj.data] : []);
        this.clearMolecularScene();
        let atomCount = 0;
        let bondCount = 0;
        let annotationCount = 0;
        let surfaceTriangles = 0;
        let nucleicResidues = 0;
        for (let structureIndex = 0; structureIndex < structures.length; structureIndex++) {
            const snapshot = buildR4StructureSnapshot(structures[structureIndex]);
            if (structureIndex === 0) this.snapshot = snapshot;
            atomCount += snapshot.atoms.length;
            bondCount += snapshot.bonds.length;
            const atoms = createR4Spheres(snapshot, { scope: 'non-polymer', radiusScale: 0.34 });
            const bonds = createR4Bonds(snapshot, { scope: 'non-polymer', radius: 0.13 });
            const annotations = [
                ...deriveR4Annotations(snapshot),
                ...(structureIndex === 0 ? this.customAnnotations : []),
            ];
            this.addAnnotationElements(snapshot, annotations);
            if (this.representationOverride) {
                const object = await this.representationOverride(snapshot, this.plugin);
                if (this.disposed || generation !== this.rebuildGeneration) {
                    disposeObject(object);
                    return;
                }
                this.molecularRoot.add(object);
                annotationCount += annotations.length;
                continue;
            }
            const nucleic = createR4Nucleic(snapshot);
            annotationCount += annotations.length;
            nucleicResidues += nucleic.userData.r4ResidueCount;
            this.molecularRoot.add(createR4Cartoon(snapshot), bonds, atoms, nucleic);
            const surface = await createR4Surface(this.plugin, snapshot);
            if (this.disposed || generation !== this.rebuildGeneration) {
                disposeObject(surface);
                return;
            }
            this.molecularRoot.add(surface);
            surfaceTriangles += surface.geometry.index!.count / 3;
        }
        this.canvas.dataset.structures = String(structures.length);
        this.canvas.dataset.atoms = String(atomCount);
        this.canvas.dataset.bonds = String(bondCount);
        this.canvas.dataset.annotations = String(annotationCount);
        this.canvas.dataset.nucleicResidues = String(nucleicResidues);
        this.canvas.dataset.surfaceTriangles = String(surfaceTriangles);
        const volumes = this.representationOverride
            ? []
            : this.plugin.managers.volume.hierarchy.current.volumes.flatMap(ref => ref.cell.obj ? [ref.cell.obj.data] : []);
        if (!this.representationOverride) {
            const density = await createR4Density(this.plugin, volumes);
            if (this.disposed || generation !== this.rebuildGeneration) {
                disposeObject(density);
                return;
            }
            this.molecularRoot.add(density);
            this.canvas.dataset.densityTriangles = String(density.userData.r4TriangleCount);
        }
        this.canvas.dataset.densityVolumes = String(volumes.length);
        this.canvas.dataset.representation = this.representationOverrideLabel;
        this.updateGeometryMetrics(performance.now() - buildStart);
        this.syncSelectionMarker();
    }

    dispose() {
        if (this.disposed) return;
        this.disposed = true;
        this.renderer.setAnimationLoop(null);
        this.subscriptions.forEach(subscription => subscription.unsubscribe());
        this.resizeObserver.disconnect();
        this.target.removeEventListener('pointermove', this.onPointerMove);
        this.target.removeEventListener('click', this.onClick);
        this.clearMolecularScene();
        disposeObject(this.hoverMarker);
        disposeObject(this.selectionMarker);
        this.postProcessing.dispose();
        this.renderer.dispose();
        this.canvas.remove();
        this.annotationLayer.remove();
        const molCanvas = this.plugin.canvas3d?.webgl.gl.canvas;
        if (molCanvas instanceof HTMLCanvasElement) molCanvas.style.opacity = '';
    }

    setAnnotations(annotations: readonly R4Annotation[]) {
        this.customAnnotations = annotations;
        void this.rebuildCurrentStructure();
    }

    /** Lab hook: swap geometry construction while retaining Mol* state, camera and annotations. */
    async setRepresentationOverride(override?: R4RepresentationOverride, label = 'full-r4') {
        this.representationOverride = override;
        this.representationOverrideLabel = label;
        await this.rebuildCurrentStructure();
    }

    private updateGeometryMetrics(buildMilliseconds: number) {
        let drawObjects = 0;
        let vertices = 0;
        let triangles = 0;
        let instances = 0;
        this.molecularRoot.traverse(object => {
            const candidate = object as any;
            const geometry = candidate.geometry;
            if (!geometry) return;
            drawObjects++;
            const instanceCount = typeof candidate.count === 'number' ? candidate.count : 1;
            const vertexCount = geometry.getAttribute?.('position')?.count ?? 0;
            const triangleCount = geometry.index?.count ? geometry.index.count / 3 : vertexCount / 3;
            instances += instanceCount;
            vertices += vertexCount * instanceCount;
            triangles += triangleCount * instanceCount;
        });
        this.canvas.dataset.buildMs = buildMilliseconds.toFixed(3);
        this.canvas.dataset.drawObjects = String(drawObjects);
        this.canvas.dataset.vertices = String(Math.round(vertices));
        this.canvas.dataset.triangles = String(Math.round(triangles));
        this.canvas.dataset.instances = String(instances);
    }

    private installLights() {
        this.scene.add(new AmbientLight(0xbfd1e5, 1.8));
        const key = new DirectionalLight(0xfff4e6, 4.5);
        key.position.set(8, 12, 10);
        key.castShadow = true;
        const fill = new DirectionalLight(0x8fb8ff, 2.0);
        fill.position.set(-10, 3, 5);
        const rim = new DirectionalLight(0x6fffe9, 2.8);
        rim.position.set(2, 5, -12);
        this.scene.add(key, fill, rim);
    }

    private addAnnotationElements(snapshot: R4StructureSnapshot, annotations: readonly R4Annotation[]) {
        for (const annotation of annotations) {
            const element = document.createElement('div');
            element.textContent = annotation.label;
            Object.assign(element.style, {
                position: 'absolute', left: '0', top: '0', transform: 'translate(-50%, -120%)',
                padding: '4px 8px', borderRadius: '999px', whiteSpace: 'nowrap',
                color: '#f4f7fb', background: 'rgba(10, 18, 26, 0.84)',
                border: `1px solid #${(annotation.color ?? 0x70e1d1).toString(16).padStart(6, '0')}`,
                font: '600 11px/1.2 system-ui, sans-serif', letterSpacing: '0.02em',
                boxShadow: '0 2px 12px rgba(0, 0, 0, 0.32)',
            });
            this.annotationLayer.appendChild(element);
            this.annotationEntries.push({ element, snapshot, annotation });
        }
    }

    private syncSelectionMarker() {
        for (const ref of this.plugin.managers.structure.hierarchy.current.structures) {
            const structure = ref.cell.obj?.data;
            if (!structure) continue;
            const loci = this.plugin.managers.structure.selection.getLoci(structure);
            if (!StructureElement.Loci.is(loci) || StructureElement.Loci.isEmpty(loci)) continue;
            const sphere = StructureElement.Loci.getBoundary(loci).sphere;
            this.selectionMarker.position.fromArray(sphere.center);
            this.selectionMarker.scale.setScalar(Math.max(1, sphere.radius / 0.48));
            this.selectionMarker.visible = true;
            this.canvas.dataset.selectionMarker = 'true';
            return;
        }
        this.selectionMarker.visible = false;
        this.canvas.dataset.selectionMarker = 'false';
    }

    private syncCamera() {
        const source = this.plugin.canvas3d?.camera;
        if (!source) return;
        this.camera.matrixAutoUpdate = false;
        this.camera.matrixWorldInverse.fromArray(source.view);
        this.camera.matrixWorld.copy(this.camera.matrixWorldInverse).invert();
        this.camera.matrix.copy(this.camera.matrixWorld);
        this.camera.matrixWorld.decompose(this.camera.position, this.camera.quaternion, this.camera.scale);
        this.camera.projectionMatrix.fromArray(source.projection);
        this.camera.projectionMatrixInverse.copy(this.camera.projectionMatrix).invert();
        for (const { element, snapshot, annotation } of this.annotationEntries) {
            const atomIndex = annotation.atomIndex ?? -1;
            const position = annotation.position ?? (atomIndex >= 0
                ? [snapshot.positions[atomIndex * 3], snapshot.positions[atomIndex * 3 + 1], snapshot.positions[atomIndex * 3 + 2]]
                : undefined);
            if (!position) {
                element.style.display = 'none';
                continue;
            }
            tmpPosition.fromArray(position).project(this.camera);
            const visible = tmpPosition.z >= -1 && tmpPosition.z <= 1;
            element.style.display = visible ? 'block' : 'none';
            if (visible) {
                element.style.left = `${(tmpPosition.x * 0.5 + 0.5) * this.target.clientWidth}px`;
                element.style.top = `${(-tmpPosition.y * 0.5 + 0.5) * this.target.clientHeight}px`;
            }
        }
        if (this.snapshot?.atoms.length) {
            tmpPosition.fromArray(this.snapshot.positions, 0).project(this.camera);
            this.canvas.dataset.firstAtomNdc = `${tmpPosition.x.toFixed(3)},${tmpPosition.y.toFixed(3)},${tmpPosition.z.toFixed(3)}`;
        }
    }

    private resize() {
        const width = Math.max(1, this.target.clientWidth);
        const height = Math.max(1, this.target.clientHeight);
        this.renderer.setSize(width, height, false);
    }

    private clearMolecularScene() {
        for (const child of [...this.molecularRoot.children]) {
            this.molecularRoot.remove(child);
            disposeObject(child);
        }
        this.annotationEntries.length = 0;
        this.annotationLayer.replaceChildren();
        this.hoverMarker.visible = false;
        this.selectionMarker.visible = false;
        this.canvas.dataset.selectionMarker = 'false';
        this.snapshot = undefined;
        delete this.canvas.dataset.atoms;
        delete this.canvas.dataset.bonds;
        delete this.canvas.dataset.firstAtomNdc;
        delete this.canvas.dataset.surfaceTriangles;
        delete this.canvas.dataset.densityVolumes;
        delete this.canvas.dataset.densityTriangles;
        delete this.canvas.dataset.annotations;
        delete this.canvas.dataset.structures;
        delete this.canvas.dataset.nucleicResidues;
    }

    private getIntersection(event: PointerEvent | MouseEvent) {
        const rect = this.canvas.getBoundingClientRect();
        this.pointer.set(
            ((event.clientX - rect.left) / rect.width) * 2 - 1,
            -((event.clientY - rect.top) / rect.height) * 2 + 1,
        );
        this.raycaster.setFromCamera(this.pointer, this.camera);
        return this.raycaster.intersectObjects(this.molecularRoot.children, true)[0];
    }

    private onPointerMove(event: PointerEvent) {
        const hit = this.getIntersection(event);
        if (!hit) {
            this.plugin.managers.interactivity.lociHighlights.clearHighlights();
            this.hoverMarker.visible = false;
            return;
        }
        if (!this.isGeometryHit(hit) && hit.instanceId === undefined) return;
        const loci = this.hitLoci(hit);
        this.hoverMarker.position.copy(hit.point);
        this.hoverMarker.visible = true;
        this.plugin.managers.interactivity.lociHighlights.highlightOnly({ loci });
    }

    private onClick(event: MouseEvent) {
        const hit = this.getIntersection(event);
        this.canvas.dataset.lastClick = `${event.clientX},${event.clientY}`;
        if (!hit) {
            this.canvas.dataset.lastHit = 'none';
            return;
        }
        const kind = hit.object.userData.r4Kind;
        this.canvas.dataset.lastHit = `${kind ?? 'unknown'}:${hit.instanceId ?? hit.faceIndex ?? -1}`;
        if (!this.isGeometryHit(hit) && hit.instanceId === undefined) return;
        const loci = this.hitLoci(hit);
        this.selectionMarker.position.copy(hit.point);
        this.selectionMarker.visible = true;
        this.canvas.dataset.lastLoci = loci.kind;
        this.plugin.managers.interactivity.lociSelects.selectOnly({ loci });
        this.syncSelectionMarker();
        if (this.snapshot) {
            const selected = this.plugin.managers.structure.selection.getLoci(this.snapshot.structure);
            this.canvas.dataset.lastSelection = selected.kind === 'element-loci' ? `${selected.elements.length}` : '0';
        }
    }

    private isGeometryHit(hit: NonNullable<ReturnType<R4HybridRenderer['getIntersection']>>) {
        return hit.object.userData.r4Kind === 'cartoon'
            || hit.object.userData.r4Kind === 'surface'
            || hit.object.userData.r4Kind === 'density'
            || hit.object.userData.r4Kind === 'annotation';
    }

    private hitLoci(hit: NonNullable<ReturnType<R4HybridRenderer['getIntersection']>>) {
        const kind = hit.object.userData.r4Kind;
        const snapshot = hit.object.userData.r4Snapshot as R4StructureSnapshot | undefined;
        if (kind === 'atoms') return atomLoci(snapshot!, hit.object.userData.r4AtomIndices[hit.instanceId!]);
        if (kind === 'bonds') return bondLoci(snapshot!, hit.object.userData.r4BondIndices[hit.instanceId!]);
        if (kind === 'annotation') return atomLoci(snapshot!, hit.object.userData.r4AtomIndex);
        if (kind === 'density') {
            return Volume.Isosurface.Loci(
                hit.object.userData.r4Volume,
                Volume.IsoValue.absolute(hit.object.userData.r4IsoLevel),
                OrderedSet.ofSingleton(hit.object.userData.r4VolumeInstance as Volume.InstanceIndex),
            );
        }
        return this.geometryLoci(hit);
    }

    private geometryLoci(hit: ReturnType<R4HybridRenderer['getIntersection']>) {
        const snapshot = hit?.object.userData.r4Snapshot as R4StructureSnapshot | undefined;
        if (!snapshot) throw new Error('R4 geometry picking requires a structure snapshot');
        if (!hit?.face) return atomLoci(snapshot, -1);
        const attribute = (hit.object as any).geometry.getAttribute('r4AtomIndex');
        return atomLoci(snapshot, attribute.getX(hit.face.a));
    }
}
