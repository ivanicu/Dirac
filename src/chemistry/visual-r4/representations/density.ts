import {
    BufferGeometry,
    Color,
    Float32BufferAttribute,
    Group,
    Matrix4,
    Mesh as ThreeMesh,
    Uint32BufferAttribute,
} from 'three/webgpu';
import { computeMarchingCubesMesh } from '../../../mol-geo/util/marching-cubes/algorithm';
import { Mesh as MolMesh } from '../../../mol-geo/geometry/mesh/mesh';
import { Grid } from '../../../mol-model/volume';
import type { Volume } from '../../../mol-model/volume';
import type { PluginContext } from '../../../mol-plugin/context';
import { createR4Material } from '../materials';

function colorAttribute(count: number, hex: number) {
    const color = new Color(hex);
    const values = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) color.toArray(values, i * 3);
    return new Float32BufferAttribute(values, 3);
}

async function createIsosurface(plugin: PluginContext, volume: Volume, isoLevel: number, color: number) {
    const surface = await plugin.runTask(computeMarchingCubesMesh({
        isoLevel,
        scalarField: volume.grid.cells,
    }));
    MolMesh.transform(surface, Grid.getGridToCartesianTransform(volume.grid));
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new Float32BufferAttribute(surface.vertexBuffer.ref.value.slice(0, surface.vertexCount * 3), 3));
    geometry.setAttribute('normal', new Float32BufferAttribute(surface.normalBuffer.ref.value.slice(0, surface.vertexCount * 3), 3));
    geometry.setAttribute('color', colorAttribute(surface.vertexCount, color));
    geometry.setAttribute('r4CellIndex', new Uint32BufferAttribute(Uint32Array.from(surface.groupBuffer.ref.value.slice(0, surface.vertexCount), value => Math.max(0, Math.round(value))), 1));
    geometry.setIndex(new Uint32BufferAttribute(surface.indexBuffer.ref.value.slice(0, surface.triangleCount * 3), 1));
    geometry.computeBoundingSphere();

    const material = createR4Material('transparent-outline') as any;
    material.opacity = 0.42;
    const mesh = new ThreeMesh(geometry, material);
    mesh.name = isoLevel < volume.grid.stats.mean ? 'r4-density-negative' : 'r4-density-positive';
    mesh.userData.r4Kind = 'density';
    mesh.userData.r4Volume = volume;
    mesh.userData.r4IsoLevel = isoLevel;
    return mesh;
}

export async function createR4Density(plugin: PluginContext, volumes: readonly Volume[]) {
    const group = new Group();
    group.name = 'r4-density';
    let triangleCount = 0;
    for (const volume of volumes) {
        const { min, max, mean, sigma } = volume.grid.stats;
        if (sigma <= 0) continue;
        const levels: [number, number][] = [];
        const positive = mean + sigma * 1.5;
        const negative = mean - sigma * 1.5;
        if (positive < max) levels.push([positive, 0x4f9cff]);
        if (min < mean && negative > min) levels.push([negative, 0xff5a7a]);
        for (const [level, color] of levels) {
            const source = await createIsosurface(plugin, volume, level, color);
            triangleCount += source.geometry.index!.count / 3;
            for (let instanceIndex = 0; instanceIndex < Math.max(1, volume.instances.length); instanceIndex++) {
                const mesh = instanceIndex === 0 ? source : source.clone();
                const instance = volume.instances[instanceIndex];
                if (instance) {
                    mesh.matrixAutoUpdate = false;
                    mesh.matrix.copy(new Matrix4().fromArray(instance.transform));
                }
                mesh.userData.r4VolumeInstance = instanceIndex;
                group.add(mesh);
            }
        }
    }
    group.userData.r4TriangleCount = triangleCount;
    return group;
}
