import type { Canvas3DProps } from '../mol-canvas3d/canvas3d';
import { PluginCommands } from '../mol-plugin/commands';
import type { PluginContext } from '../mol-plugin/context';
import type { StructureComponentManager } from '../mol-plugin-state/manager/structure/component';
import { createStructureColorThemeParams } from '../mol-plugin-state/helpers/structure-representation-params';
import { Color } from '../mol-util/color';

export type MolstarVisualUpgradeCost = 'low' | 'medium' | 'high';

export const MolstarVisualUpgrades = [
    { id: 'curve-quality-high', label: 'High geometry quality', group: 'Geometry', cost: 'medium', recommended: true, description: 'Requests Mol* high-quality geometry without changing representation type.' },
    { id: 'curve-radial-tessellation', label: 'Radial curve tessellation', group: 'Geometry', cost: 'medium', recommended: true, description: 'Raises only the radial segments used by existing tubes, helices and bonds.' },
    { id: 'curve-longitudinal-tessellation', label: 'Longitudinal curve tessellation', group: 'Geometry', cost: 'medium', recommended: true, description: 'Raises only the longitudinal segments along existing curves and ribbons.' },
    { id: 'rounded-caps', label: 'Rounded curve caps', group: 'Geometry', cost: 'low', recommended: true, description: 'Rounds only the end caps of compatible existing curve geometry.' },
    { id: 'tubular-helices', label: 'Tubular helix geometry', group: 'Geometry', cost: 'low', recommended: true, description: 'Uses tubular helices where the active Native representation supports them.' },
    { id: 'rounded-helix-profile', label: 'Rounded helix profile', group: 'Geometry', cost: 'low', recommended: true, description: 'Changes only the compatible helix cross-section to a rounded profile.' },
    { id: 'sphere-vdw-scale', label: 'Sphere VDW scale 0.82', group: 'Sphere only', cost: 'low', recommended: false, description: 'Shrinks existing spacefill spheres to 82%; does nothing when the Native representation has no spheres.' },
    { id: 'sphere-chain-palette', label: 'Sphere chain palette', group: 'Sphere only', cost: 'low', recommended: false, description: 'Applies the cinematic chain palette only to existing spacefill spheres.' },
    { id: 'sphere-hide-hydrogens', label: 'Sphere hide hydrogens', group: 'Sphere only', cost: 'low', recommended: false, description: 'Hides hydrogen spheres only when an existing spacefill representation supports it.' },
    { id: 'sphere-impostor', label: 'Sphere GPU impostors', group: 'Sphere only', cost: 'low', recommended: false, description: 'Requests GPU impostors for existing spheres; it never creates sphere geometry.' },
    { id: 'dielectric-material', label: 'Dielectric material', group: 'Material', cost: 'low', recommended: true, description: 'Sets only material metalness to zero.' },
    { id: 'satin-roughness', label: 'Satin roughness', group: 'Material', cost: 'low', recommended: true, description: 'Sets only the material roughness used by existing geometry.' },
    { id: 'soft-subsurface', label: 'Soft molecular wrap light', group: 'Material', cost: 'low', recommended: false, description: 'Cheap subsurface-style diffuse wrapping softens spheres, surfaces and ribbons without a scattering pass.' },
    { id: 'micro-normal', label: 'Micro-normal amplitude', group: 'Material', cost: 'medium', recommended: false, description: 'Enables only the material bump amplitude.' },
    { id: 'micro-frequency', label: 'Micro-normal frequency', group: 'Material', cost: 'low', recommended: false, description: 'Raises only procedural micro-normal frequency on compatible geometry.' },
    { id: 'micro-density', label: 'Micro-surface density', group: 'Material', cost: 'low', recommended: false, description: 'Raises only the compatible surface density parameter.' },
    { id: 'surface-fusion', label: 'Softened fused surface', group: 'Surface only', cost: 'medium', recommended: false, description: 'Smooths only an already-present molecular or gaussian surface; it never adds a surface to a cartoon or atomic view.' },
    { id: 'key-light', label: 'Sculpting key light', group: 'Lighting', cost: 'low', recommended: true, description: 'A warm directional key reveals the molecular silhouette.' },
    { id: 'fill-light', label: 'Cool fill light', group: 'Lighting', cost: 'low', recommended: true, description: 'Recovers information in the key-light shadow side.' },
    { id: 'fresnel-rim', label: 'Rear edge light', group: 'Lighting', cost: 'low', recommended: true, description: 'A cool rear directional light separates thin ribbons from the background.' },
    { id: 'soft-key-shadow', label: 'Soft key shadow', group: 'Lighting', cost: 'medium', recommended: false, description: 'Screen-space key-light shadow adds broad assembly depth at a bounded ray-march cost.' },
    { id: 'environment-fill', label: 'Cool environment fill', group: 'Environment', cost: 'low', recommended: false, description: 'Adds a separate cool ambient fill without changing the key, fill or rim lights.' },
    { id: 'environment-gradient', label: 'Deep environment gradient', group: 'Environment', cost: 'low', recommended: false, description: 'Adds a native full-frame background gradient behind the same Mol* scene.' },
    { id: 'background-vignette', label: 'Cinematic vignette', group: 'Environment', cost: 'low', recommended: false, description: 'Darkens only the frame edges in postprocessing; it does not alter molecule geometry or color semantics.' },
    { id: 'ambient-occlusion', label: 'Contact ambient occlusion', group: 'Depth', cost: 'medium', recommended: true, description: 'Adds local contact depth at overlaps and packed secondary structure.' },
    { id: 'depth-outline', label: 'Sub-pixel depth contour', group: 'Depth', cost: 'medium', recommended: true, description: 'A restrained depth-derived contour preserves thin strands and sheet boundaries.' },
    { id: 'smaa', label: 'SMAA edge reconstruction', group: 'Image', cost: 'medium', recommended: true, description: 'Zoomed edges: reduces stair-stepping without increasing molecular geometry.' },
    { id: 'adaptive-sharpen', label: 'Adaptive sharpening', group: 'Image', cost: 'low', recommended: true, description: 'Restores local edge clarity after antialiasing and AO composition.' },
    { id: 'filmic-tone', label: 'Filmic highlight rolloff', group: 'Image', cost: 'low', recommended: false, description: 'A final filmic curve lifts midtones while compressing hard digital highlights.' },
    { id: 'depth-cue', label: 'Molecular depth cue', group: 'Focus', cost: 'low', recommended: false, description: 'Large assemblies: restrained distance fog separates front and back layers.' },
    { id: 'focus-dof', label: 'Macro depth of field', group: 'Focus', cost: 'high', recommended: false, description: 'Separates a focused interaction plane from distant structure.' },
    { id: 'highlight-bloom', label: 'Highlight bloom', group: 'Focus', cost: 'high', recommended: false, description: 'Softly spreads only the brightest material highlights.' },
    { id: 'mesoscale-copy-upper-left', label: 'Mesoscale copy · upper left', group: 'Composition', cost: 'high', recommended: false, description: 'Adds exactly one translated sibling copy of the current Native representation in the upper-left scene slot.' },
    { id: 'mesoscale-copy-right', label: 'Mesoscale copy · right', group: 'Composition', cost: 'high', recommended: false, description: 'Adds exactly one translated sibling copy of the current Native representation in the right scene slot.' },
    { id: 'mesoscale-copy-lower', label: 'Mesoscale copy · lower', group: 'Composition', cost: 'high', recommended: false, description: 'Adds exactly one translated sibling copy of the current Native representation in the lower scene slot.' },
    { id: 'hover-glow', label: 'Semantic hover glow', group: 'Interaction', cost: 'low', recommended: true, description: 'Changes only the hover marker color and strength attached to Mol* loci.' },
    { id: 'selection-glow', label: 'Semantic selection glow', group: 'Interaction', cost: 'low', recommended: true, description: 'Changes only the selected-loci color and strength.' },
    { id: 'true-subsurface-scattering', label: 'True subsurface scattering', group: 'Experimental', cost: 'high', recommended: false, available: false, description: 'Not executable in the current Mol* WebGL path. Kept separate from the cheap wrap-diffuse approximation.' },
    { id: 'global-illumination', label: 'Global illumination', group: 'Experimental', cost: 'high', recommended: false, available: false, description: 'Not executable in the current realtime Mol* WebGL renderer; no approximation is silently substituted.' },
    { id: 'path-tracing', label: 'Path tracing', group: 'Experimental', cost: 'high', recommended: false, available: false, description: 'Not executable in the current realtime Mol* WebGL renderer and therefore intentionally disabled.' },
] as const satisfies readonly {
    id: string,
    label: string,
    group: string,
    cost: MolstarVisualUpgradeCost,
    recommended: boolean,
    description: string,
    available?: boolean,
}[];

export type MolstarVisualUpgradeId = typeof MolstarVisualUpgrades[number]['id'];

export const RecommendedMolstarVisualUpgrades = Object.freeze(
    MolstarVisualUpgrades.filter(upgrade => upgrade.recommended).map(upgrade => upgrade.id)
);

export interface MolstarVisualSnapshot {
    readonly renderer: Canvas3DProps['renderer'];
    readonly postprocessing: Canvas3DProps['postprocessing'];
    readonly cameraFog: Canvas3DProps['cameraFog'];
    readonly componentOptions: StructureComponentManager.Options;
}

export function captureMolstarVisualState(plugin: PluginContext): MolstarVisualSnapshot {
    const canvas = plugin.canvas3d;
    if (!canvas) throw new Error('Mol* Canvas3D is unavailable');
    return {
        renderer: canvas.props.renderer,
        postprocessing: canvas.props.postprocessing,
        cameraFog: canvas.props.cameraFog,
        componentOptions: plugin.managers.structure.component.state.options,
    };
}

export async function restoreMolstarVisualState(plugin: PluginContext, snapshot: MolstarVisualSnapshot) {
    await PluginCommands.Canvas3D.SetSettings(plugin, {
        settings: old => ({
            renderer: { ...old.renderer, ...snapshot.renderer },
            postprocessing: { ...old.postprocessing, ...snapshot.postprocessing },
            cameraFog: snapshot.cameraFog,
        })
    });
    await plugin.managers.structure.component.setOptions(snapshot.componentOptions);
}

interface MutableRepresentationVisualParams {
    quality?: string;
    radialSegments?: number;
    linearSegments?: number;
    roundCap?: boolean;
    tubularHelices?: boolean;
    helixProfile?: 'elliptical' | 'rounded' | 'square';
    bumpFrequency?: number;
    density?: number;
    sizeFactor?: number;
    probeRadius?: number;
    smoothness?: number;
    radiusOffset?: number;
    smoothColors?: { name: 'on' | 'off', params: { resolutionFactor: number, sampleStride: number } };
}

const CinematicChainPalette = [
    Color(0x43a9a7),
    Color(0x6b9fc2),
    Color(0x86bd84),
    Color(0xad79bd),
    Color(0xc493cf),
    Color(0xb99a5c),
    Color(0x4f7e91),
    Color(0xc77f9a),
] as const;

const CinematicChainColorParams = {
    asymId: 'auth' as const,
    palette: {
        name: 'colors' as const,
        params: {
            list: { kind: 'set' as const, colors: [...CinematicChainPalette] },
        },
    },
};

async function applyExistingSphereUpgrades(plugin: PluginContext, enabled: ReadonlySet<MolstarVisualUpgradeId>) {
    const changeScale = enabled.has('sphere-vdw-scale');
    const changePalette = enabled.has('sphere-chain-palette');
    const hideHydrogens = enabled.has('sphere-hide-hydrogens');
    const useImpostor = enabled.has('sphere-impostor');
    if (!changeScale && !changePalette && !hideHydrogens && !useImpostor) return;
    const update = plugin.state.data.build();

    for (const structure of plugin.managers.structure.component.currentStructures) {
        for (const component of structure.components) {
            for (const representation of component.representations) {
                if (representation.cell.transform.params?.type?.name !== 'spacefill') continue;
                const colorTheme = changePalette
                    ? createStructureColorThemeParams(plugin, component.cell.obj?.data, 'spacefill', 'chain-id', CinematicChainColorParams)
                    : undefined;
                update.to(representation.cell).update(old => {
                    const params = old.type.params as MutableRepresentationVisualParams & {
                        ignoreHydrogens?: boolean,
                        ignoreHydrogensVariant?: 'all' | 'non-polar',
                        tryUseImpostor?: boolean,
                    };
                    if (changeScale && 'sizeFactor' in params) params.sizeFactor = 0.82;
                    if (hideHydrogens && 'ignoreHydrogens' in params) {
                        params.ignoreHydrogens = true;
                        params.ignoreHydrogensVariant = 'all';
                    }
                    if (useImpostor && 'tryUseImpostor' in params) params.tryUseImpostor = true;
                    if (colorTheme) old.colorTheme = colorTheme;
                });
            }
        }
    }

    await update.commit();
}

async function applyGeometryUpgrades(plugin: PluginContext, enabled: ReadonlySet<MolstarVisualUpgradeId>) {
    const highQuality = enabled.has('curve-quality-high');
    const radialTessellation = enabled.has('curve-radial-tessellation');
    const longitudinalTessellation = enabled.has('curve-longitudinal-tessellation');
    const roundedCaps = enabled.has('rounded-caps');
    const tubularHelices = enabled.has('tubular-helices');
    const roundedHelixProfile = enabled.has('rounded-helix-profile');
    const microFrequency = enabled.has('micro-frequency');
    const microDensity = enabled.has('micro-density');
    const surfaceFusion = enabled.has('surface-fusion');
    const update = plugin.state.data.build();

    for (const structure of plugin.managers.structure.component.currentStructures) {
        for (const component of structure.components) {
            for (const representation of component.representations) {
                update.to(representation.cell).update(old => {
                    const params = old.type.params as MutableRepresentationVisualParams;
                    if (highQuality && 'quality' in params) params.quality = 'high';
                    if (radialTessellation && 'radialSegments' in params) params.radialSegments = 20;
                    if (longitudinalTessellation && 'linearSegments' in params) params.linearSegments = 12;
                    if (roundedCaps && 'roundCap' in params) params.roundCap = true;
                    if (tubularHelices && 'tubularHelices' in params) params.tubularHelices = true;
                    if (roundedHelixProfile && 'helixProfile' in params) params.helixProfile = 'rounded';
                    if (microFrequency && 'bumpFrequency' in params) params.bumpFrequency = 3.2;
                    if (microDensity && 'density' in params) params.density = 0.18;
                    const representationName = old.type.name;
                    if (surfaceFusion && (representationName === 'molecular-surface' || representationName === 'gaussian-surface')) {
                        if ('smoothColors' in params) params.smoothColors = { name: 'on', params: { resolutionFactor: 1.4, sampleStride: 2 } };
                        if (representationName === 'molecular-surface' && 'probeRadius' in params) params.probeRadius = 1.55;
                        if (representationName === 'gaussian-surface') {
                            if ('smoothness' in params) params.smoothness = 1.6;
                            if ('radiusOffset' in params) params.radiusOffset = 0.35;
                        }
                    }
                });
            }
        }
    }
    await update.commit();
}

export async function applyMolstarVisualUpgrades(plugin: PluginContext, upgrades: Iterable<MolstarVisualUpgradeId>) {
    const enabled = new Set(upgrades);
    const hasKey = enabled.has('key-light');
    const hasFill = enabled.has('fill-light');
    const hasRim = enabled.has('fresnel-rim');
    const hasFilmicTone = enabled.has('filmic-tone');
    const hasSoftSubsurface = enabled.has('soft-subsurface');
    const hasEnvironmentFill = enabled.has('environment-fill');
    const hasLightingUpgrade = hasKey || hasFill || hasRim;
    const lights: Canvas3DProps['renderer']['light'] = [];
    if (hasKey) lights.push({ inclination: 138, azimuth: 318, color: Color(0xffead2), intensity: hasFill || hasRim ? 0.92 : 1.15 });
    if (hasFill) lights.push({ inclination: 102, azimuth: 118, color: Color(0x9fc5ff), intensity: hasKey || hasRim ? 0.5 : 0.9 });
    if (hasRim) lights.push({ inclination: 58, azimuth: 212, color: Color(0x73e7d2), intensity: hasKey || hasFill ? 0.42 : 0.85 });

    await PluginCommands.Canvas3D.SetSettings(plugin, {
        settings: old => ({
            renderer: {
                ...old.renderer,
                backgroundColor: Color(0x0d141b),
                exposure: hasLightingUpgrade ? (hasFilmicTone ? 1.28 : 1.16) : old.renderer.exposure,
                subsurfaceStrength: hasSoftSubsurface ? 0.38 : old.renderer.subsurfaceStrength,
                ambientColor: hasEnvironmentFill ? Color(0x9dc6e2) : hasLightingUpgrade ? Color(0xb9cadc) : old.renderer.ambientColor,
                ambientIntensity: hasEnvironmentFill ? 0.44 : hasLightingUpgrade ? 0.32 : old.renderer.ambientIntensity,
                light: lights.length ? lights : old.renderer.light,
                highlightColor: enabled.has('hover-glow') ? Color(0x70e1d1) : old.renderer.highlightColor,
                selectColor: enabled.has('selection-glow') ? Color(0xffc857) : old.renderer.selectColor,
                highlightStrength: enabled.has('hover-glow') ? 0.22 : old.renderer.highlightStrength,
                selectStrength: enabled.has('selection-glow') ? 0.3 : old.renderer.selectStrength,
            },
            postprocessing: {
                ...old.postprocessing,
                enabled: true,
                toneMapping: hasFilmicTone ? { name: 'on', params: {} } : { name: 'off', params: {} },
                vignette: enabled.has('background-vignette') ? { name: 'on', params: { strength: 0.42 } } : { name: 'off', params: {} },
                occlusion: enabled.has('ambient-occlusion') ? {
                    name: 'on',
                    params: {
                        samples: 24,
                        multiScale: { name: 'off', params: {} },
                        radius: 4,
                        bias: 0.75,
                        blurKernelSize: 11,
                        blurDepthBias: 0.5,
                        resolutionScale: 0.85,
                        color: Color(0x050a0e),
                        transparentThreshold: 0.45,
                    }
                } : { name: 'off', params: {} },
                shadow: enabled.has('soft-key-shadow') ? {
                    name: 'on',
                    params: { steps: 6, maxDistance: 5, tolerance: 1.1, strength: 0.32 }
                } : { name: 'off', params: {} },
                outline: enabled.has('depth-outline') ? {
                    name: 'on',
                    params: { scale: 1, threshold: 0.45, color: Color(0x071118), includeTransparent: true }
                } : { name: 'off', params: {} },
                dof: enabled.has('focus-dof') ? {
                    name: 'on',
                    params: { blurSize: 7, blurSpread: 1.15, inFocus: 0, PPM: 24, center: 'camera-target', mode: 'plane' }
                } : { name: 'off', params: {} },
                antialiasing: enabled.has('smaa')
                    ? { name: 'smaa', params: { edgeThreshold: 0.08, maxSearchSteps: 16 } }
                    : { name: 'off', params: {} },
                sharpening: enabled.has('adaptive-sharpen')
                    ? { name: 'on', params: { sharpness: 0.4, denoise: true } }
                    : { name: 'off', params: {} },
                bloom: enabled.has('highlight-bloom') ? {
                    name: 'on',
                    params: { strength: 0.8, radius: 0.45, threshold: 0.42, mode: 'luminosity', transparency: false }
                } : { name: 'off', params: {} },
                background: enabled.has('environment-gradient') ? {
                    variant: {
                        name: 'horizontalGradient',
                        params: {
                            topColor: Color(0x172b46),
                            bottomColor: Color(0x070b12),
                            ratio: 0.42,
                            coverage: 'viewport',
                        }
                    }
                } : { variant: { name: 'off', params: {} } },
            },
            cameraFog: enabled.has('depth-cue')
                ? { name: 'on', params: { intensity: 24 } }
                : { name: 'off', params: {} },
        })
    });
    await applyExistingSphereUpgrades(plugin, enabled);
    const options = plugin.managers.structure.component.state.options;
    const hasMaterialUpgrade = enabled.has('dielectric-material') || enabled.has('satin-roughness') || enabled.has('micro-normal');
    await plugin.managers.structure.component.setOptions({
        ...options,
        ignoreLight: false,
        visualQuality: enabled.has('curve-quality-high') ? 'high' : 'auto',
        materialStyle: hasMaterialUpgrade
            ? {
                metalness: enabled.has('dielectric-material') ? 0 : options.materialStyle.metalness,
                roughness: enabled.has('satin-roughness') ? (hasSoftSubsurface ? 0.36 : 0.26) : options.materialStyle.roughness,
                bumpiness: enabled.has('micro-normal') ? 0.18 : options.materialStyle.bumpiness,
            }
            : options.materialStyle,
    });
    await applyGeometryUpgrades(plugin, enabled);
}

/** Recommended MN-like visual return with bounded GPU cost and full Mol* semantics. */
export function applyMolstarCinematicPreset(plugin: PluginContext) {
    return applyMolstarVisualUpgrades(plugin, RecommendedMolstarVisualUpgrades);
}
