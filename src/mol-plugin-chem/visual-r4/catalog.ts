import type { R4MaterialKind, R4RepresentationGraph } from './types';

const source = { node: 'source' } as const;

function materialInput(node: string, kind: R4MaterialKind) {
    return {
        id: 'material',
        operator: 'material',
        inputs: { geometry: { node } },
        parameters: { kind },
    } as const;
}

export const r4CartoonGraph: R4RepresentationGraph = {
    id: 'mn-style-cartoon',
    label: 'Molecular Nodes Style Cartoon',
    nodes: [
        { id: 'source', operator: 'source.structure' },
        { id: 'polymers', operator: 'separate-polymers', inputs: { structure: source } },
        { id: 'backbone', operator: 'atoms-to-curves', inputs: { atoms: { node: 'polymers', output: 'peptide' } } },
        { id: 'secondary', operator: 'secondary-structure', inputs: { curves: { node: 'backbone' } } },
        { id: 'profile', operator: 'curve-profile', inputs: { curves: { node: 'secondary' } }, parameters: {
            helixWidth: 2.2, helixThickness: 0.6, sheetWidth: 2.2, sheetThickness: 0.6,
            loopRadius: 0.3, arrows: true, smoothing: 0.5,
        } },
        { id: 'peptideMesh', operator: 'curve-to-mesh', inputs: { curves: { node: 'profile' } } },
        { id: 'nucleicCurves', operator: 'atoms-to-curves', inputs: { atoms: { node: 'polymers', output: 'nucleic' } } },
        { id: 'nucleicMesh', operator: 'curve-to-mesh', inputs: { curves: { node: 'nucleicCurves' } }, parameters: { width: 3, thickness: 1, radius: 2 } },
        { id: 'bases', operator: 'nucleic-bases', inputs: { atoms: { node: 'polymers', output: 'nucleic' } } },
        { id: 'joined', operator: 'join', inputs: { peptide: { node: 'peptideMesh' }, nucleic: { node: 'nucleicMesh' }, bases: { node: 'bases' } } },
        { id: 'colored', operator: 'sample-colors', inputs: { geometry: { node: 'joined' }, atoms: source }, parameters: { blur: true } },
        materialInput('colored', 'default'),
    ],
    outputs: { geometry: { node: 'material' } },
};

export const r4RibbonGraph: R4RepresentationGraph = {
    id: 'mn-style-ribbon',
    label: 'Molecular Nodes Style Ribbon',
    nodes: [
        { id: 'source', operator: 'source.structure' },
        { id: 'polymers', operator: 'separate-polymers', inputs: { structure: source } },
        { id: 'curves', operator: 'atoms-to-curves', inputs: { atoms: { node: 'polymers' } } },
        { id: 'profile', operator: 'curve-profile', inputs: { curves: { node: 'curves' } }, parameters: { width: 3, thickness: 1, smoothing: 0.5 } },
        { id: 'mesh', operator: 'curve-to-mesh', inputs: { curves: { node: 'profile' } } },
        { id: 'colored', operator: 'sample-colors', inputs: { geometry: { node: 'mesh' }, atoms: source }, parameters: { blur: false } },
        materialInput('colored', 'default'),
    ],
    outputs: { geometry: { node: 'material' } },
};

export const r4SpheresGraph: R4RepresentationGraph = {
    id: 'mn-style-spheres',
    label: 'Molecular Nodes Style Spheres',
    nodes: [
        { id: 'source', operator: 'source.structure' },
        { id: 'spheres', operator: 'instance-spheres', inputs: { atoms: source }, parameters: { radiusScale: 0.8 } },
        materialInput('spheres', 'default'),
    ],
    outputs: { geometry: { node: 'material' } },
};

export const r4SticksGraph: R4RepresentationGraph = {
    id: 'mn-style-sticks',
    label: 'Molecular Nodes Style Sticks',
    nodes: [
        { id: 'source', operator: 'source.structure' },
        { id: 'sticks', operator: 'build-bonds', inputs: { atoms: source }, parameters: { radius: 0.2, splitBondOrder: true } },
        materialInput('sticks', 'default'),
    ],
    outputs: { geometry: { node: 'material' } },
};

export const r4BallAndStickGraph: R4RepresentationGraph = {
    id: 'mn-style-ball-and-stick',
    label: 'Molecular Nodes Style Ball and Stick',
    nodes: [
        { id: 'source', operator: 'source.structure' },
        { id: 'spheres', operator: 'instance-spheres', inputs: { atoms: source }, parameters: { radiusScale: 0.3 } },
        { id: 'bonds', operator: 'build-bonds', inputs: { atoms: source }, parameters: { radius: 0.3, splitBondOrder: true, findMissing: false } },
        { id: 'joined', operator: 'join', inputs: { spheres: { node: 'spheres' }, bonds: { node: 'bonds' } } },
        materialInput('joined', 'default'),
    ],
    outputs: { geometry: { node: 'material' } },
};

export const r4SurfaceGraph: R4RepresentationGraph = {
    id: 'mn-style-surface',
    label: 'Molecular Nodes Style Surface',
    nodes: [
        { id: 'source', operator: 'source.structure' },
        { id: 'density', operator: 'surface-density', inputs: { atoms: source }, parameters: { radiusScale: 1.5, probeRadius: 1 } },
        { id: 'mesh', operator: 'surface-mesh', inputs: { density: { node: 'density' } }, parameters: { quality: 3 } },
        { id: 'relaxed', operator: 'surface-relax', inputs: { geometry: { node: 'mesh' } }, parameters: { steps: 10 } },
        { id: 'colored', operator: 'sample-colors', inputs: { geometry: { node: 'relaxed' }, atoms: source }, parameters: { closestAlphaCarbon: true, blur: 2 } },
        materialInput('colored', 'default'),
    ],
    outputs: { geometry: { node: 'material' } },
};

export const r4DensityGraph: R4RepresentationGraph = {
    id: 'mn-density-surface',
    label: 'Molecular Nodes Density Surface',
    nodes: [
        { id: 'source', operator: 'source.structure' },
        { id: 'volume', operator: 'density-volume', inputs: { source } },
        { id: 'mesh', operator: 'surface-mesh', inputs: { density: { node: 'volume' } } },
        materialInput('mesh', 'transparent-outline'),
    ],
    outputs: { geometry: { node: 'material' } },
};

export const r4NucleicGraph: R4RepresentationGraph = {
    id: 'mn-nucleic',
    label: 'Molecular Nodes Nucleic Ribbon and Bases',
    nodes: [
        { id: 'source', operator: 'source.structure' },
        { id: 'polymers', operator: 'separate-polymers', inputs: { structure: source } },
        { id: 'curves', operator: 'atoms-to-curves', inputs: { atoms: { node: 'polymers', output: 'nucleic' } } },
        { id: 'profile', operator: 'curve-profile', inputs: { curves: { node: 'curves' } }, parameters: { width: 3, thickness: 1, radius: 2 } },
        { id: 'ribbon', operator: 'curve-to-mesh', inputs: { curves: { node: 'profile' } } },
        { id: 'bases', operator: 'nucleic-bases', inputs: { atoms: { node: 'polymers', output: 'nucleic' } } },
        { id: 'joined', operator: 'join', inputs: { ribbon: { node: 'ribbon' }, bases: { node: 'bases' } } },
        materialInput('joined', 'default'),
    ],
    outputs: { geometry: { node: 'material' } },
};

export const r4EnsembleGraph: R4RepresentationGraph = {
    id: 'mn-ensemble',
    label: 'Molecular Nodes Ensemble',
    nodes: [
        { id: 'source', operator: 'source.structure' },
        { id: 'instances', operator: 'ensemble-instances', inputs: { source }, parameters: { realize: false } },
        materialInput('instances', 'default'),
    ],
    outputs: { geometry: { node: 'material' } },
};

export const r4AnnotationsGraph: R4RepresentationGraph = {
    id: 'mn-annotations',
    label: 'Molecular Nodes Annotations',
    nodes: [
        { id: 'source', operator: 'source.structure' },
        { id: 'annotations', operator: 'annotations', inputs: { source } },
        materialInput('annotations', 'default'),
    ],
    outputs: { geometry: { node: 'material' } },
};

function presetGraph(id: string, label: string, nodes: R4RepresentationGraph['nodes']): R4RepresentationGraph {
    return {
        id,
        label,
        nodes: [{ id: 'source', operator: 'source.structure' }, ...nodes, materialInput('joined', 'default')],
        outputs: { geometry: { node: 'material' } },
    };
}

export const r4Preset1Graph = presetGraph('mn-style-preset-1', 'Molecular Nodes Style Preset 1', [
    { id: 'ribbon', operator: 'curve-to-mesh', inputs: { atoms: source }, parameters: { style: 'ribbon', polymers: true } },
    { id: 'other', operator: 'build-bonds', inputs: { atoms: source }, parameters: { nonPolymer: true, spheres: true } },
    { id: 'joined', operator: 'join', inputs: { ribbon: { node: 'ribbon' }, other: { node: 'other' } } },
]);

export const r4Preset2Graph = presetGraph('mn-style-preset-2', 'Molecular Nodes Style Preset 2', [
    { id: 'cartoon', operator: 'curve-to-mesh', inputs: { atoms: source }, parameters: { style: 'cartoon', polymers: true } },
    { id: 'sidechains', operator: 'build-bonds', inputs: { atoms: source }, parameters: { sidechains: true, proximity: true } },
    { id: 'joined', operator: 'join', inputs: { cartoon: { node: 'cartoon' }, sidechains: { node: 'sidechains' } } },
]);

export const r4Preset3Graph = presetGraph('mn-style-preset-3', 'Molecular Nodes Style Preset 3', [
    { id: 'surface', operator: 'surface-mesh', inputs: { atoms: source }, parameters: { peptide: true } },
    { id: 'spheres', operator: 'instance-spheres', inputs: { atoms: source }, parameters: { nonPeptide: true } },
    { id: 'joined', operator: 'join', inputs: { surface: { node: 'surface' }, spheres: { node: 'spheres' } } },
]);

export const r4Preset4Graph = presetGraph('mn-style-preset-4', 'Molecular Nodes Style Preset 4', [
    { id: 'ribbon', operator: 'curve-to-mesh', inputs: { atoms: source }, parameters: { style: 'ribbon', peptide: true } },
    { id: 'spheres', operator: 'instance-spheres', inputs: { atoms: source }, parameters: { remapRadius: true } },
    { id: 'joined', operator: 'join', inputs: { ribbon: { node: 'ribbon' }, spheres: { node: 'spheres' } } },
]);

export const r4StyleGraphs = Object.freeze({
    cartoon: r4CartoonGraph,
    ribbon: r4RibbonGraph,
    spheres: r4SpheresGraph,
    sticks: r4SticksGraph,
    ballAndStick: r4BallAndStickGraph,
    surface: r4SurfaceGraph,
    nucleic: r4NucleicGraph,
    density: r4DensityGraph,
    ensemble: r4EnsembleGraph,
    annotations: r4AnnotationsGraph,
    preset1: r4Preset1Graph,
    preset2: r4Preset2Graph,
    preset3: r4Preset3Graph,
    preset4: r4Preset4Graph,
});
