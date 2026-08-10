import type { PluginContext } from '../../../mol-plugin/context';
import type { R4Annotation, R4RepresentationKind, R4StructureSnapshot } from '../types';
import { createR4Annotations } from './annotations';
import { createR4BallAndStick, createR4Bonds, createR4Spheres } from './atomic';
import { createR4Cartoon } from './cartoon';
import { createR4Nucleic } from './nucleic';
import { createR4Surface } from './surface';

export interface R4RepresentationOptions {
    readonly annotations?: readonly R4Annotation[];
    readonly radiusScale?: number;
    readonly bondRadius?: number;
}

export async function createR4StructureRepresentation(
    kind: Exclude<R4RepresentationKind, 'density' | 'ensemble'>,
    snapshot: R4StructureSnapshot,
    plugin: PluginContext,
    options: R4RepresentationOptions = {},
) {
    switch (kind) {
        case 'cartoon': return createR4Cartoon(snapshot, { style: 'cartoon' });
        case 'ribbon': return createR4Cartoon(snapshot, { style: 'ribbon' });
        case 'spheres': return createR4Spheres(snapshot, { radiusScale: options.radiusScale ?? 0.8 });
        case 'sticks': return createR4Bonds(snapshot, { radius: options.bondRadius ?? 0.2 });
        case 'ball-and-stick': return createR4BallAndStick(snapshot);
        case 'surface': return createR4Surface(plugin, snapshot);
        case 'nucleic': return createR4Nucleic(snapshot);
        case 'annotations': return createR4Annotations(snapshot, options.annotations);
    }
}
