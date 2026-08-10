import {
    BufferGeometry,
    CatmullRomCurve3,
    Color,
    Float32BufferAttribute,
    Group,
    Mesh,
    Uint32BufferAttribute,
    Vector3,
} from 'three/webgpu';
import { OrderedSet } from '../../../mol-data/int';
import { SecondaryStructureType, isNucleic } from '../../../mol-model/structure/model/types';
import { Unit } from '../../../mol-model/structure';
import { PolymerTraceIterator } from '../../../mol-repr/structure/visual/util/polymer';
import { createR4Material } from '../materials';
import type { R4StructureSnapshot } from '../types';

interface TracePoint {
    readonly position: Vector3;
    readonly atomIndex: number;
    readonly secondaryStructure: SecondaryStructureType;
    readonly secondaryStructureLast: boolean;
    readonly moleculeType: number;
}

export interface R4CartoonOptions {
    readonly style?: 'cartoon' | 'ribbon';
    readonly radialSegments?: number;
    readonly samplesPerResidue?: number;
}

function chainColor(chainId: string) {
    let hash = 2166136261;
    for (let i = 0; i < chainId.length; i++) hash = Math.imul(hash ^ chainId.charCodeAt(i), 16777619);
    return new Color().setHSL(((hash >>> 0) % 360) / 360, 0.52, 0.5);
}

function profile(point: TracePoint, angle: number, options: R4CartoonOptions) {
    const c = Math.cos(angle);
    const s = Math.sin(angle);
    if (options.style === 'ribbon') {
        return [
            Math.sign(c) * Math.pow(Math.abs(c), 0.5) * 1.35,
            Math.sign(s) * Math.pow(Math.abs(s), 0.5) * 0.22,
        ] as const;
    }
    const isSheet = SecondaryStructureType.is(point.secondaryStructure, SecondaryStructureType.Flag.Beta);
    const isHelix = SecondaryStructureType.is(point.secondaryStructure, SecondaryStructureType.Flag.Helix);
    if (!isSheet && !isHelix && !isNucleic(point.moleculeType)) return [c * 0.3, s * 0.3] as const;

    let width = isNucleic(point.moleculeType) ? 1.5 : 1.1;
    const height = isNucleic(point.moleculeType) ? 0.5 : 0.3;
    if (isSheet && point.secondaryStructureLast) width *= 1.45;
    const exponent = 4;
    const x = Math.sign(c) * Math.pow(Math.abs(c), 2 / exponent) * width;
    const y = Math.sign(s) * Math.pow(Math.abs(s), 2 / exponent) * height;
    return [x, y] as const;
}

function createSegmentGeometry(points: readonly TracePoint[], options: R4CartoonOptions) {
    const source = points.map(point => point.position);
    const curve = new CatmullRomCurve3(source, false, 'centripetal', 0.5);
    const lengthSegments = Math.max(2, (points.length - 1) * (options.samplesPerResidue ?? 8));
    const radialSegments = options.radialSegments ?? 12;
    const samples = curve.getPoints(lengthSegments);
    const frames = curve.computeFrenetFrames(lengthSegments, false);
    const positions: number[] = [];
    const residueIndices: number[] = [];
    const indices: number[] = [];

    for (let i = 0; i <= lengthSegments; i++) {
        const pointIndex = Math.min(points.length - 1, Math.round((i / lengthSegments) * (points.length - 1)));
        const trace = points[pointIndex];
        const center = samples[i];
        const normal = frames.normals[i];
        const binormal = frames.binormals[i];
        for (let ring = 0; ring < radialSegments; ring++) {
            const angle = (ring / radialSegments) * Math.PI * 2;
            const [x, y] = profile(trace, angle, options);
            positions.push(
                center.x + normal.x * x + binormal.x * y,
                center.y + normal.y * x + binormal.y * y,
                center.z + normal.z * x + binormal.z * y,
            );
            residueIndices.push(trace.atomIndex);
        }
    }

    for (let segment = 0; segment < lengthSegments; segment++) {
        const ring = segment * radialSegments;
        const nextRing = (segment + 1) * radialSegments;
        for (let side = 0; side < radialSegments; side++) {
            const nextSide = (side + 1) % radialSegments;
            indices.push(
                ring + side, nextRing + side, nextRing + nextSide,
                ring + side, nextRing + nextSide, ring + nextSide,
            );
        }
    }

    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new Float32BufferAttribute(positions, 3));
    geometry.setAttribute('r4AtomIndex', new Uint32BufferAttribute(residueIndices, 1));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    geometry.computeBoundingSphere();
    return geometry;
}

export function createR4Cartoon(snapshot: R4StructureSnapshot, options: R4CartoonOptions = {}) {
    const group = new Group();
    group.name = 'r4-cartoon';
    const atomIndices = new Map(snapshot.atoms.map((atom, index) => [`${atom.unit.id}:${atom.unitIndex}`, index]));

    for (const unit of snapshot.structure.units) {
        if (!Unit.isAtomic(unit) || unit.polymerElements.length < 2) continue;
        const iterator = PolymerTraceIterator(unit, snapshot.structure, { ignoreSecondaryStructure: false });
        let segment: TracePoint[] = [];
        while (iterator.hasNext) {
            const value = iterator.move();
            if (value.first) segment = [];
            const unitIndex = OrderedSet.indexOf(unit.elements, value.center.element);
            const atomIndex = atomIndices.get(`${unit.id}:${unitIndex}`);
            if (atomIndex !== undefined) {
                segment.push({
                    position: new Vector3(value.p3[0], value.p3[1], value.p3[2]),
                    atomIndex,
                    secondaryStructure: value.secStrucType,
                    secondaryStructureLast: value.secStrucLast,
                    moleculeType: value.moleculeType,
                });
            }
            if (value.last && segment.length > 1) {
                const material = createR4Material('default') as any;
                material.color = chainColor(snapshot.atoms[segment[0].atomIndex].chainId);
                const mesh = new Mesh(createSegmentGeometry(segment, options), material);
                mesh.userData.r4Kind = 'cartoon';
                mesh.userData.r4Snapshot = snapshot;
                mesh.castShadow = true;
                mesh.receiveShadow = true;
                group.add(mesh);
            }
        }
    }
    return group;
}
