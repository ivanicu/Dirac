import { MeshBasicMaterial, MeshPhysicalMaterial, MeshSSSNodeMaterial, MeshToonMaterial, type Material } from 'three/webgpu';
import { color, float } from 'three/tsl';
import type { R4MaterialKind } from './types';

export function createR4Material(kind: R4MaterialKind): Material {
    switch (kind) {
        case 'ambient-occlusion':
            return new MeshBasicMaterial({ vertexColors: true });
        case 'flat-outline':
            return new MeshToonMaterial({ vertexColors: true });
        case 'squishy':
            const squishy = new MeshSSSNodeMaterial({
                vertexColors: true,
                roughness: 1,
                ior: 1.05,
                clearcoat: 1,
                clearcoatRoughness: 0.246,
            });
            squishy.thicknessColorNode = color(0xffb6a3);
            squishy.thicknessDistortionNode = float(0.18);
            squishy.thicknessAmbientNode = float(0.08);
            squishy.thicknessAttenuationNode = float(0.18);
            squishy.thicknessPowerNode = float(2.4);
            squishy.thicknessScaleNode = float(4.0);
            return squishy;
        case 'transparent-outline':
            return new MeshPhysicalMaterial({
                vertexColors: true,
                transparent: true,
                opacity: 0.7,
                roughness: 0.35,
                ior: 1.45,
                depthWrite: false,
            });
        case 'default':
            return new MeshPhysicalMaterial({
                vertexColors: true,
                roughness: 0.264,
                ior: 1.45,
            });
    }
}
