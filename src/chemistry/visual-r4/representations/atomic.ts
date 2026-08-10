import {
    Color,
    CylinderGeometry,
    Group,
    InstancedMesh,
    Matrix4,
    Quaternion,
    SphereGeometry,
    Vector3,
} from 'three/webgpu';
import { isPolymer } from '../../../mol-model/structure/model/types';
import { createR4Material } from '../materials';
import type { R4StructureSnapshot } from '../types';

export type R4AtomScope = 'all' | 'polymer' | 'non-polymer';

export interface R4SphereOptions {
    readonly scope?: R4AtomScope;
    readonly radiusScale?: number;
    readonly detail?: number;
}

export interface R4BondOptions {
    readonly scope?: R4AtomScope;
    readonly radius?: number;
    readonly radialSegments?: number;
    readonly splitBondOrder?: boolean;
}

function includeAtom(snapshot: R4StructureSnapshot, atomIndex: number, scope: R4AtomScope) {
    const polymer = isPolymer(snapshot.atoms[atomIndex].moleculeType);
    return scope === 'all' || (scope === 'polymer' ? polymer : !polymer);
}

export function createR4Spheres(snapshot: R4StructureSnapshot, options: R4SphereOptions = {}) {
    const scope = options.scope ?? 'all';
    const atomIndices = snapshot.atoms.flatMap((_, index) => includeAtom(snapshot, index, scope) ? [index] : []);
    const detail = options.detail ?? 24;
    const geometry = new SphereGeometry(1, detail, Math.max(8, Math.round(detail * 2 / 3)));
    const mesh = new InstancedMesh(geometry, createR4Material('default'), atomIndices.length);
    mesh.name = 'r4-atoms';
    mesh.userData.r4Kind = 'atoms';
    mesh.userData.r4AtomIndices = atomIndices;
    mesh.userData.r4Snapshot = snapshot;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    const matrix = new Matrix4();
    const position = new Vector3();
    const scale = new Vector3();
    const quaternion = new Quaternion();
    const color = new Color();
    for (let i = 0; i < atomIndices.length; i++) {
        const atomIndex = atomIndices[i];
        const offset = atomIndex * 3;
        position.fromArray(snapshot.positions, offset);
        scale.setScalar(snapshot.radii[atomIndex] * (options.radiusScale ?? 0.3));
        matrix.compose(position, quaternion.identity(), scale);
        mesh.setMatrixAt(i, matrix);
        color.fromArray(snapshot.colors, offset);
        mesh.setColorAt(i, color);
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    return mesh;
}

export function createR4Bonds(snapshot: R4StructureSnapshot, options: R4BondOptions = {}) {
    const scope = options.scope ?? 'all';
    const placements = snapshot.bonds.flatMap((bond, index) => {
        const atomA = includeAtom(snapshot, bond.atomA, scope);
        const atomB = includeAtom(snapshot, bond.atomB, scope);
        const include = scope === 'non-polymer' ? atomA || atomB : atomA && atomB;
        if (!include) return [];
        const order = options.splitBondOrder === false ? 1 : Math.max(1, Math.min(3, Math.round(bond.order)));
        return Array.from({ length: order }, (_, lane) => ({ bondIndex: index, lane, order }));
    });
    const geometry = new CylinderGeometry(1, 1, 1, options.radialSegments ?? 16, 1, false);
    const mesh = new InstancedMesh(geometry, createR4Material('default'), placements.length);
    mesh.name = 'r4-bonds';
    mesh.userData.r4Kind = 'bonds';
    mesh.userData.r4BondIndices = placements.map(placement => placement.bondIndex);
    mesh.userData.r4Snapshot = snapshot;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    const matrix = new Matrix4();
    const quaternion = new Quaternion();
    const scale = new Vector3();
    const a = new Vector3();
    const b = new Vector3();
    const midpoint = new Vector3();
    const direction = new Vector3();
    const colorA = new Color();
    const colorB = new Color();
    const up = new Vector3(0, 1, 0);
    const side = new Vector3();
    const reference = new Vector3();
    for (let i = 0; i < placements.length; i++) {
        const placement = placements[i];
        const bond = snapshot.bonds[placement.bondIndex];
        a.fromArray(snapshot.positions, bond.atomA * 3);
        b.fromArray(snapshot.positions, bond.atomB * 3);
        midpoint.copy(a).add(b).multiplyScalar(0.5);
        direction.copy(b).sub(a);
        if (placement.order > 1) {
            reference.set(Math.abs(direction.y) < 0.9 * direction.length() ? 0 : 1, Math.abs(direction.y) < 0.9 * direction.length() ? 1 : 0, 0);
            side.crossVectors(direction, reference).normalize();
            const laneOffset = (placement.lane - (placement.order - 1) / 2) * (options.radius ?? 0.13) * 2.6;
            midpoint.addScaledVector(side, laneOffset);
        }
        quaternion.setFromUnitVectors(up, direction.clone().normalize());
        scale.set(options.radius ?? 0.13, direction.length(), options.radius ?? 0.13);
        matrix.compose(midpoint, quaternion, scale);
        mesh.setMatrixAt(i, matrix);
        colorA.fromArray(snapshot.colors, bond.atomA * 3);
        colorB.fromArray(snapshot.colors, bond.atomB * 3);
        mesh.setColorAt(i, colorA.lerp(colorB, 0.5));
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    return mesh;
}

export function createR4BallAndStick(snapshot: R4StructureSnapshot, scope: R4AtomScope = 'all') {
    const group = new Group();
    group.name = 'r4-ball-and-stick';
    group.add(
        createR4Bonds(snapshot, { scope, radius: 0.13 }),
        createR4Spheres(snapshot, { scope, radiusScale: 0.3 }),
    );
    return group;
}
