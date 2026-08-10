import { Group } from 'three/webgpu';
import type { PluginContext } from '../../../mol-plugin/context';
import type { R4StructureSnapshot } from '../types';
import { createR4BallAndStick, createR4Bonds, createR4Spheres } from './atomic';
import { createR4Cartoon } from './cartoon';
import { createR4Nucleic } from './nucleic';
import { createR4Surface } from './surface';

export type R4PresetKind = 'preset-1' | 'preset-2' | 'preset-3' | 'preset-4';

/** Browser-native compositions derived from Molecular Nodes' four public preset dependency graphs. */
export async function createR4Preset(kind: R4PresetKind, plugin: PluginContext, snapshot: R4StructureSnapshot) {
    const group = new Group();
    group.name = `r4-${kind}`;
    switch (kind) {
        case 'preset-1':
            group.add(
                createR4Cartoon(snapshot, { style: 'ribbon' }),
                createR4Nucleic(snapshot),
                createR4BallAndStick(snapshot, 'non-polymer'),
            );
            break;
        case 'preset-2':
            group.add(
                createR4Cartoon(snapshot, { style: 'cartoon' }),
                createR4BallAndStick(snapshot, 'non-polymer'),
                createR4Bonds(snapshot, { scope: 'polymer', radius: 0.09 }),
            );
            break;
        case 'preset-3':
            group.add(
                await createR4Surface(plugin, snapshot),
                createR4Spheres(snapshot, { scope: 'non-polymer', radiusScale: 0.5 }),
            );
            break;
        case 'preset-4':
            group.add(
                createR4Cartoon(snapshot, { style: 'ribbon' }),
                createR4Spheres(snapshot, { scope: 'non-polymer', radiusScale: 0.8 }),
            );
            break;
    }
    group.userData.r4Preset = kind;
    return group;
}
