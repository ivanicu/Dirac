import {
    BufferGeometry,
    Float32BufferAttribute,
    Mesh as ThreeMesh,
    Uint32BufferAttribute,
} from 'three/webgpu';
import { computeMarchingCubesMesh } from '../../../mol-geo/util/marching-cubes/algorithm';
import { Mesh as MolMesh } from '../../../mol-geo/geometry/mesh/mesh';
import { Tensor } from '../../../mol-math/linear-algebra';
import type { PluginContext } from '../../../mol-plugin/context';
import { PhysicalSizeTheme } from '../../../mol-theme/size/physical';
import {
    computeStructureGaussianDensity,
    DefaultGaussianDensityProps,
} from '../../../mol-repr/structure/visual/util/gaussian';
import { createR4Material } from '../materials';
import type { R4StructureSnapshot } from '../types';

export async function createR4Surface(plugin: PluginContext, snapshot: R4StructureSnapshot) {
    const props = {
        ...DefaultGaussianDensityProps,
        resolution: 0.72,
        smoothness: 1.5,
        radiusOffset: 0,
        floodfill: 'off' as const,
    };
    const sizeTheme = PhysicalSizeTheme({ structure: snapshot.structure }, { scale: 1 });
    const density = await plugin.runTask(computeStructureGaussianDensity(snapshot.structure, sizeTheme, props));
    const isoLevel = Math.exp(-props.smoothness) / density.radiusFactor;
    const surface = await plugin.runTask(computeMarchingCubesMesh({
        isoLevel,
        scalarField: props.floodfill === 'off'
            ? density.field
            : Tensor.createFloodfilled(density.field, isoLevel, props.floodfill),
        idField: density.idField,
    }));
    MolMesh.transform(surface, density.transform);

    const geometry = new BufferGeometry();
    const positions = surface.vertexBuffer.ref.value.slice(0, surface.vertexCount * 3);
    const normals = surface.normalBuffer.ref.value.slice(0, surface.vertexCount * 3);
    const indices = surface.indexBuffer.ref.value.slice(0, surface.triangleCount * 3);
    const groups = surface.groupBuffer.ref.value;
    const atomIndices = new Uint32Array(surface.vertexCount);
    const colors = new Float32Array(surface.vertexCount * 3);
    for (let i = 0; i < surface.vertexCount; i++) {
        const atomIndex = Math.max(0, Math.min(snapshot.atoms.length - 1, Math.round(groups[i])));
        atomIndices[i] = atomIndex;
        colors[i * 3] = snapshot.colors[atomIndex * 3];
        colors[i * 3 + 1] = snapshot.colors[atomIndex * 3 + 1];
        colors[i * 3 + 2] = snapshot.colors[atomIndex * 3 + 2];
    }
    geometry.setAttribute('position', new Float32BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new Float32BufferAttribute(normals, 3));
    geometry.setAttribute('color', new Float32BufferAttribute(colors, 3));
    geometry.setAttribute('r4AtomIndex', new Uint32BufferAttribute(atomIndices, 1));
    geometry.setIndex(new Uint32BufferAttribute(indices, 1));
    geometry.computeBoundingSphere();

    const material = createR4Material('squishy') as any;
    material.transparent = true;
    material.opacity = 0.18;
    material.depthWrite = false;
    const mesh = new ThreeMesh(geometry, material);
    mesh.name = 'r4-gaussian-surface';
    mesh.userData.r4Kind = 'surface';
    mesh.userData.r4Snapshot = snapshot;
    mesh.castShadow = false;
    mesh.receiveShadow = true;
    return mesh;
}
