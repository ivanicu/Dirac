import {
    CanvasTexture,
    Group,
    LinearFilter,
    Sprite,
    SpriteMaterial,
    SRGBColorSpace,
} from 'three/webgpu';
import { isPolymer } from '../../../mol-model/structure/model/types';
import type { R4Annotation, R4StructureSnapshot } from '../types';

function createLabelSprite(annotation: R4Annotation, snapshot: R4StructureSnapshot) {
    const canvas = document.createElement('canvas');
    canvas.width = 512;
    canvas.height = 128;
    const context = canvas.getContext('2d')!;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.font = '600 42px system-ui, sans-serif';
    const width = Math.min(480, context.measureText(annotation.label).width + 56);
    context.fillStyle = 'rgba(10, 18, 26, 0.84)';
    context.strokeStyle = `#${(annotation.color ?? 0x70e1d1).toString(16).padStart(6, '0')}`;
    context.lineWidth = 4;
    context.beginPath();
    context.roundRect((512 - width) / 2, 22, width, 84, 24);
    context.fill();
    context.stroke();
    context.fillStyle = '#f4f7fb';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillText(annotation.label, 256, 65, 450);

    const texture = new CanvasTexture(canvas);
    texture.colorSpace = SRGBColorSpace;
    texture.minFilter = LinearFilter;
    const material = new SpriteMaterial({ map: texture, transparent: true, depthTest: false, depthWrite: false, sizeAttenuation: false });
    const sprite = new Sprite(material);
    const atomIndex = annotation.atomIndex ?? -1;
    const position = annotation.position ?? (atomIndex >= 0
        ? [snapshot.positions[atomIndex * 3], snapshot.positions[atomIndex * 3 + 1], snapshot.positions[atomIndex * 3 + 2]]
        : [0, 0, 0]);
    sprite.position.set(position[0], position[1] + 1.4, position[2]);
    sprite.scale.set(0.18, 0.045, 1);
    sprite.userData.r4Kind = 'annotation';
    sprite.userData.r4AtomIndex = atomIndex;
    sprite.userData.r4Snapshot = snapshot;
    sprite.userData.r4Annotation = annotation;
    sprite.renderOrder = 100;
    return sprite;
}

export function deriveR4Annotations(snapshot: R4StructureSnapshot): R4Annotation[] {
    const annotations: R4Annotation[] = [];
    const chains = new Set<string>();
    const residues = new Set<string>();
    snapshot.atoms.forEach((atom, atomIndex) => {
        if (!chains.has(atom.chainId)) {
            chains.add(atom.chainId);
            annotations.push({ id: `chain:${atom.chainId}`, label: `Chain ${atom.chainId}`, atomIndex });
        }
        if (isPolymer(atom.moleculeType)) return;
        const residue = `${atom.chainId}:${atom.residueId}:${atom.residueName}`;
        if (residues.has(residue)) return;
        residues.add(residue);
        annotations.push({
            id: `residue:${residue}`,
            label: `${atom.residueName} ${atom.residueId}`,
            atomIndex,
            color: 0xffc857,
        });
    });
    return annotations;
}

export function createR4Annotations(snapshot: R4StructureSnapshot, custom: readonly R4Annotation[] = []) {
    const group = new Group();
    group.name = 'r4-annotations';
    const annotations = [...deriveR4Annotations(snapshot), ...custom];
    for (const annotation of annotations) group.add(createLabelSprite(annotation, snapshot));
    group.userData.r4AnnotationCount = annotations.length;
    return group;
}
