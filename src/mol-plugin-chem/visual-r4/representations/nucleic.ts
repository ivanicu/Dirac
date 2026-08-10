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
import { isNucleic } from '../../../mol-model/structure/model/types';
import { createR4Material } from '../materials';
import type { R4StructureSnapshot } from '../types';

const BaseAtoms = new Set(['N1', 'N2', 'N3', 'N4', 'N6', 'N7', 'N9', 'C2', 'C4', 'C5', 'C6', 'C8', 'O2', 'O4', 'O6']);
const up = new Vector3(0, 1, 0);

export function createR4Nucleic(snapshot: R4StructureSnapshot) {
    const group = new Group();
    group.name = 'r4-nucleic-bases';
    const residues = new Map<string, number[]>();
    snapshot.atoms.forEach((atom, atomIndex) => {
        if (!isNucleic(atom.moleculeType)) return;
        const key = `${atom.unit.id}:${atom.chainId}:${atom.residueId}`;
        const current = residues.get(key);
        if (current) current.push(atomIndex);
        else residues.set(key, [atomIndex]);
    });

    const plates = new InstancedMesh(new SphereGeometry(1, 20, 12), createR4Material('default'), residues.size);
    const connectors = new InstancedMesh(new CylinderGeometry(1, 1, 1, 12), createR4Material('default'), residues.size);
    plates.name = 'r4-nucleic-plates';
    connectors.name = 'r4-nucleic-connectors';
    plates.userData.r4Kind = 'atoms';
    connectors.userData.r4Kind = 'atoms';
    plates.userData.r4Snapshot = snapshot;
    connectors.userData.r4Snapshot = snapshot;
    const atomIndices: number[] = [];
    const matrix = new Matrix4();
    const quaternion = new Quaternion();
    const position = new Vector3();
    const scale = new Vector3();
    const attachment = new Vector3();
    const center = new Vector3();
    const direction = new Vector3();
    const color = new Color();
    let instance = 0;
    for (const indices of residues.values()) {
        const baseIndices = indices.filter(index => BaseAtoms.has(snapshot.atoms[index].atomName.toUpperCase()));
        if (baseIndices.length < 3) continue;
        center.set(0, 0, 0);
        for (const index of baseIndices) center.add(position.fromArray(snapshot.positions, index * 3));
        center.multiplyScalar(1 / baseIndices.length);
        const attachmentIndex = indices.find(index => /^(C1'|C1\*)$/i.test(snapshot.atoms[index].atomName)) ?? baseIndices[0];
        attachment.fromArray(snapshot.positions, attachmentIndex * 3);
        direction.copy(center).sub(attachment);
        quaternion.setFromUnitVectors(up, direction.clone().normalize());
        scale.set(1.35, 0.24, 0.95);
        matrix.compose(center, quaternion, scale);
        plates.setMatrixAt(instance, matrix);
        color.fromArray(snapshot.colors, baseIndices[0] * 3);
        plates.setColorAt(instance, color.offsetHSL(0, 0.2, 0.15));

        const midpoint = attachment.clone().add(center).multiplyScalar(0.5);
        scale.set(0.12, direction.length(), 0.12);
        matrix.compose(midpoint, quaternion, scale);
        connectors.setMatrixAt(instance, matrix);
        connectors.setColorAt(instance, color);
        atomIndices.push(baseIndices[0]);
        instance++;
    }
    plates.count = instance;
    connectors.count = instance;
    plates.userData.r4AtomIndices = atomIndices;
    connectors.userData.r4AtomIndices = atomIndices;
    plates.instanceMatrix.needsUpdate = true;
    connectors.instanceMatrix.needsUpdate = true;
    if (plates.instanceColor) plates.instanceColor.needsUpdate = true;
    if (connectors.instanceColor) connectors.instanceColor.needsUpdate = true;
    group.add(connectors, plates);
    group.userData.r4ResidueCount = instance;
    return group;
}
