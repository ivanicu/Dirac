/**
 * Canvas port of voice_to_claude_v8.py's tuned Dot renderer.
 *
 * The constants and equations intentionally mirror the Mac implementation:
 *  - idle: three dispersive travelling waves plus irrational drift
 *  - recording: 80-point golden-angle disc driven by loudness and zero crossings
 *  - transcribing/sending: evolving Y_4^3 spherical-harmonic wireframe
 *  - terminal states: identically-sized, semantically coloured flash rings
 */

export type VoiceDaemonState =
    'idle' | 'off' | 'calibrating' | 'recording' | 'transcribing' | 'sending'
    | 'done' | 'fail' | 'error' | 'no_speech' | 'filtered';

export interface VoiceSignal {
    readonly level: number;
    readonly tone?: number;
    readonly hearing?: boolean;
}

type RGB = readonly [number, number, number];
type Direction = readonly [number, number, number];
type HarmonicNode = readonly [Direction, number, number];
type HarmonicLine = readonly [number, number, readonly HarmonicNode[], number | null];

interface OrbPalette {
    readonly idle: RGB;
    readonly off: RGB;
    readonly calibrating: RGB;
    readonly recordNear: RGB;
    readonly recordFar: RGB;
    readonly processCyan: RGB;
    readonly processViolet: RGB;
    readonly processMint: RGB;
    readonly sending: RGB;
    readonly done: RGB;
    readonly noSpeech: RGB;
    readonly filtered: RGB;
    readonly fail: RGB;
    readonly idleAlpha: number;
    readonly idleLineWidth: number;
    readonly idleHalo: number;
    readonly activeGlass: readonly [number, number];
    readonly recordingAlpha: number;
    readonly recordingFloor: number;
    readonly processAlpha: number;
    readonly processLine: number;
    readonly scale: Readonly<Record<'idle' | 'recording' | 'processing' | 'terminal', number>>;
}

interface OrbColours {
    readonly idle: string;
    readonly off: string;
    readonly calibrating: string;
    readonly recordNear: string;
    readonly recordFar: string;
    readonly processCyan: string;
    readonly processViolet: string;
    readonly processMint: string;
    readonly sending: string;
    readonly done: string;
    readonly noSpeech: string;
    readonly filtered: string;
    readonly fail: string;
}

export interface VoicePaletteSystem {
    readonly id: string;
    readonly name: string;
    readonly character: string;
    readonly light: OrbColours;
    readonly dark: OrbColours;
}

export interface VoiceIdleMotionPreset {
    readonly id: string;
    readonly name: string;
    readonly contour: number;
    readonly halo: number;
    readonly character: string;
}

export interface VoiceIdleSizePreset {
    readonly id: string;
    readonly target: number;
    readonly multiplier: number;
}

export interface VoiceSendingSizePreset {
    readonly id: string;
    readonly target: number;
    readonly multiplier: number;
    readonly name: string;
    readonly character: string;
}

export interface VoiceSendingVariant {
    readonly id: string;
    readonly name: string;
    readonly character: string;
    readonly equation: string;
}

export const VOICE_IDLE_MOTION_PRESETS: readonly VoiceIdleMotionPreset[] = [
    { id: '01', name: 'Still', contour: .10, halo: .08, character: 'Nearly static' },
    { id: '02', name: 'Whisper', contour: .18, halo: .14, character: 'Barely breathing' },
    { id: '03', name: 'Quiet', contour: .26, halo: .21, character: 'Low excursion' },
    { id: '04', name: 'Controlled', contour: .34, halo: .29, character: 'Architectural' },
    { id: '05', name: 'Soft', contour: .42, halo: .38, character: 'Gentle presence' },
    { id: '06', name: 'Natural', contour: .50, halo: .48, character: 'Balanced breath' },
    { id: '07', name: 'Breathing', contour: .60, halo: .58, character: 'Clearly alive' },
    { id: '08', name: 'Expressive', contour: .72, halo: .70, character: 'Visible motion' },
    { id: '09', name: 'Wide', contour: .86, halo: .84, character: 'Broad excursion' },
    { id: '10', name: 'Full', contour: 1, halo: 1, character: 'Original range' },
] as const;

export const VOICE_IDLE_SIZE_PRESETS: readonly VoiceIdleSizePreset[] = [
    { id: '40', target: 40, multiplier: 40 / 46 },
    { id: '42', target: 42, multiplier: 42 / 46 },
    { id: '44', target: 44, multiplier: 44 / 46 },
] as const;

export const VOICE_SENDING_SIZE_PRESETS: readonly VoiceSendingSizePreset[] = [
    { id: '40', target: 40, multiplier: 40 / 54,
        name: 'Quiet Orbit', character: 'Slow rigid transport' },
    { id: '42', target: 42, multiplier: 42 / 54,
        name: 'Phase Wave', character: 'Travelling energy front' },
    { id: '44', target: 44, multiplier: 44 / 54,
        name: 'Laminar', character: 'Differential angular flow' },
    { id: '46', target: 46, multiplier: 46 / 54,
        name: 'Breathing', character: 'Radial eigenpulse' },
    { id: '48', target: 48, multiplier: 48 / 54,
        name: 'Counterflow', character: 'Alternating radial bands' },
] as const;

export const VOICE_SENDING_VARIANTS: readonly VoiceSendingVariant[] = [
    { id: '01', name: 'Eigenflow Sphere', equation: 'Y₄³:S²→ℂ', character: 'Fixed sphere carrying harmonic phase' },
    { id: '02', name: 'Zernike Disk', equation: 'Z₇³(r,θ−ωt)', character: 'Orthogonal aberration mode on D²' },
    { id: '03', name: 'Fourier–Bessel Drum', equation: 'J₄(j₄₂r)cos4θ', character: 'Circular membrane eigenmode' },
    { id: '04', name: 'Laguerre–Gaussian', equation: 'LG₃²(r,θ,t)', character: 'Optical vortex with orbital phase' },
    { id: '05', name: 'Poincaré Disk', equation: 'ds²=4|dz|²/(1−|z|²)²', character: 'Hyperbolic geodesic field' },
    { id: '06', name: 'Apollonian Packing', equation: '(Σbᵢ)²=2Σbᵢ²', character: 'Descartes tangent-circle hierarchy' },
    { id: '07', name: 'Hopf Fiber Atlas', equation: 'S³→S², fiber=S¹', character: 'Circular fibers across a spherical base' },
    { id: '08', name: 'KAM Tori', equation: 'I′=I+εsin(kθ−ωt)', character: 'Nested invariant curves' },
    { id: '09', name: 'Hamiltonian Vortex', equation: 'H=r²/2+εr⁴cos4θ', character: 'Closed symplectic streamlines' },
    { id: '10', name: 'Blaschke Flow', equation: 'B(z)=∏(z−aₖ)/(1−āₖz)', character: 'Automorphism field of the unit disk' },
    { id: '11', name: 'Jacobi Theta Ring', equation: 'ϑ₃(z,q)=1+2Σqⁿ²cos2nz', character: 'Elliptic spectral interference' },
    { id: '12', name: 'Farey Geodesics', equation: 'PSL(2,ℤ)↷ℍ', character: 'Modular tessellation folded into D²' },
    { id: '13', name: 'Wigner–Laguerre', equation: 'Wₙ∝(−1)ⁿe⁻ʳ²Lₙ(2r²)', character: 'Radial quantum phase-space shells' },
    { id: '14', name: 'Slepian Disk Mode', equation: 'K_Dψ=λψ', character: 'Band-limited concentration eigenfield' },
    { id: '15', name: 'Ginibre Gas', equation: 'P∝e⁻Σ|zᵢ|²∏|zᵢ−zⱼ|²', character: 'Two-dimensional Coulomb equilibrium' },
    { id: '16', name: 'Fibonacci Disk', equation: 'rₙ=√(n/N), θₙ=nφ', character: 'Golden-angle uniform sampling' },
    { id: '17', name: 'Fermat Spiral Pair', equation: 'r²=±a²θ', character: 'Counterwound parabolic transport' },
    { id: '18', name: 'Kuramoto Rings', equation: 'φ̇ᵢ=ωᵢ+KΣsin(φⱼ−φᵢ)', character: 'Coupled circular phase oscillators' },
    { id: '19', name: 'Gaussian Free Field', equation: 'Φ=ΣaₘₙJₘ(jₘₙr)eⁱᵐθ', character: 'Dirichlet random wave on a disk' },
    { id: '20', name: 'Heat Kernel Circle', equation: 'K_t(θ)=Σe⁻ⁿ²ᵗeⁱⁿθ', character: 'Diffusion spectrum on concentric S¹' },
] as const;

const SIZE = 72;
const R_OUT = 26;
const TAU = Math.PI * 2;
const PHI = (1 + Math.sqrt(5)) / 2;

const REC_N = 80;
const REC_K = 10;
const REC_W = 2.2;
const REC_DISP = .06;
const REC_DOT_MIN = .79;
const REC_DOT_MAX = 2.02;
const REC_DISP_G = [.22, 1.80] as const;
const REC_SPD_G = [.50, 1.75] as const;
const REC_TONE_G = [.72, 1.50] as const;
const REC_DOT_G = [.80, 1.16] as const;
const REC_ALP_G = [.66, 1] as const;
const REC_A_MAX = .95;
const REC_GA = Math.PI * (3 - Math.sqrt(5));

const IDLE_TRAINS = [[1, .80, 1, 0], [2, .60, -1, 2.1], [3, .40, 1, 4.2]] as const;
const IDLE_WAVE_SP = .55;
const IDLE_SPIN = .055;
const IDLE_WAVE_AMP = .15;
const IDLE_HALO_AMP = .11;
const IDLE_LW = 2.5;
const DRIFT_F1 = .029;
const DRIFT_F2 = DRIFT_F1 * PHI;
const SPEED_LO = 1.30;
const SPEED_HI = 1.70;
const AMPL_LO = 1.15;
const AMPL_HI = 1.50;

const PROC_SPIN = .26;
const PROC_TRAV = .26 * 1.6180339887;
const PROC_PULSE = .26 * .6180339887 * 1.7;
const PROC_A_LO = .52;
const PROC_A_HI = 1;
const PROC_BASE = .15;
const PROC_AMP = .85;
const PROC_NU = 13;
const PROC_NV = 8;
const PROC_SU = 40;
const PROC_SV = 48;
const PROC_TILT = .34;
const PROC_YAW = .62;
const PROC_BANDS = 6;
const PROC_LW_A = .74;
const PROC_LW_B = .92;
const PROC_AL_A = .26;
const PROC_AL_B = .72;
const PROC_U_LW = .80;
const PROC_U_AL = .95;
const PROC_V_LW = .60;
const PROC_V_AL = .70;

const R_FLASH_OUT = 17;
const FLASH_LW = 3.6;
const REC_VIS = .88;
const PROC_VIS = .85;
const HALO_MARGIN = 3;
const IDLE_HALO_R = (SIZE / 2 - HALO_MARGIN) / (R_OUT * (1 + IDLE_HALO_AMP * AMPL_HI));
const IDLE_R = (R_OUT - IDLE_LW / 2) / (1 + IDLE_WAVE_AMP * AMPL_HI);
const REC_R = ((R_OUT - REC_DOT_MAX * REC_DOT_G[1])
    / (1 + REC_DISP * REC_DISP_G[1]) * REC_VIS);

const colours = (idle: string, off: string, calibrating: string,
    recordNear: string, recordFar: string, processCyan: string,
    processViolet: string, processMint: string, sending: string, done: string,
    noSpeech: string, filtered: string, fail: string): OrbColours => ({ idle, off,
    calibrating, recordNear, recordFar, processCyan, processViolet, processMint,
    sending, done, noSpeech, filtered, fail });

export const VOICE_PALETTE_SYSTEMS: readonly VoicePaletteSystem[] = [
    { id: 'harbor', name: 'Harbor', character: 'Calm · trustworthy · quietly technical',
        light: colours('#456872', '#858B88', '#596E9B', '#385E96', '#167C99', '#286795', '#655A95', '#2F786D', '#285BA5', '#2F7656', '#6C767B', '#796C82', '#B33D3F'),
        dark: colours('#84BBC4', '#707A77', '#A4B2E1', '#85AFF0', '#43BFD3', '#55B6E8', '#AAA0F2', '#59CBB7', '#79A9FF', '#67D49D', '#929DA2', '#B6A8C0', '#FF6B69') },
    { id: 'iris', name: 'Iris', character: 'Composed · attentive · softly intelligent',
        light: colours('#5B657C', '#888B91', '#776E9E', '#5868A8', '#6B59A5', '#466A9A', '#785A9C', '#477A78', '#4F63B3', '#37745B', '#707680', '#7B6A87', '#B33E48'),
        dark: colours('#ACB6D7', '#747A85', '#C0B4E6', '#9CAEFF', '#B39AEF', '#7EA9ED', '#C19FE8', '#72CEC5', '#9AA8FF', '#72D0A7', '#979EAA', '#BDAEC9', '#FF6C78') },
    { id: 'estuary', name: 'Estuary', character: 'Organic · reassuring · fluidly present',
        light: colours('#3F6C68', '#858D89', '#527B86', '#28717C', '#23889A', '#2B6F91', '#5C6591', '#357B68', '#2A729A', '#2E7755', '#697975', '#6C727F', '#B2403D'),
        dark: colours('#83C7BC', '#6F7B76', '#9FCBD2', '#61C3CC', '#48D4E2', '#60BCE0', '#A7A7EA', '#66D0AE', '#62BDE8', '#68D49A', '#8F9F99', '#A9AAB8', '#FF7068') },
    { id: 'cobalt', name: 'Cobalt', character: 'Precise · decisive · computational',
        light: colours('#405F83', '#858A90', '#526FA6', '#315FAB', '#287DC0', '#265FA9', '#5D57A5', '#277887', '#315BC4', '#327354', '#697482', '#716D88', '#B43B42'),
        dark: colours('#8FB5E9', '#717A84', '#A3B8F2', '#719FFF', '#4AB8FF', '#5997FF', '#9991FF', '#4AC9D3', '#7190FF', '#62D398', '#929DAA', '#AAA6C0', '#FF666D') },
    { id: 'graphite', name: 'Graphite', character: 'Restrained · architectural · low-noise',
        light: colours('#53686C', '#888D8B', '#66768D', '#4E6E8A', '#607E98', '#506E8B', '#6B6285', '#50776F', '#4E6F98', '#3B7359', '#70797B', '#746F7C', '#AE403E'),
        dark: colours('#A2BDC0', '#737B78', '#AAB7CC', '#8FAEC8', '#A1BED3', '#91ABC9', '#B1A6C6', '#8DC5B9', '#94B3DD', '#74CF9F', '#969FA1', '#B0A9B8', '#FA6B65') },
] as const;

const LIGHT_CALIBRATION = {
    idleAlpha: .94,
    idleLineWidth: 2.3,
    idleHalo: .035,
    activeGlass: [.025, .045],
    recordingAlpha: .98,
    recordingFloor: .38,
    processAlpha: .92,
    processLine: .92,
    // State-specific optical targets: idle 46, listening 42, processing 54, terminal 44.
    scale: { idle: 1.345, recording: 1.28, processing: 1.65, terminal: 1.545 },
} as const;

const DARK_CALIBRATION = {
    idleAlpha: .82,
    idleLineWidth: 2.1,
    idleHalo: .08,
    activeGlass: [.06, .105],
    recordingAlpha: .94,
    recordingFloor: .31,
    processAlpha: .86,
    processLine: .90,
    scale: { idle: 1.32, recording: 1.39, processing: 1.59, terminal: 1.545 },
} as const;

const fromHex = (value: string): RGB => {
    const number = Number.parseInt(value.slice(1), 16);
    return [((number >> 16) & 255) / 255, ((number >> 8) & 255) / 255, (number & 255) / 255];
};

let cachedPaletteKey = '';
let cachedPalette: OrbPalette | undefined;

const resolvePalette = (dark: boolean, requested = 'graphite'): OrbPalette => {
    const cacheKey = `${dark ? 'dark' : 'light'}:${requested}`;
    if (cachedPalette && cachedPaletteKey === cacheKey) return cachedPalette;
    const system = VOICE_PALETTE_SYSTEMS.find(item => item.id === requested)
        || VOICE_PALETTE_SYSTEMS[0];
    const source = dark ? system.dark : system.light;
    const converted = Object.fromEntries(Object.entries(source)
        .map(([key, value]) => [key, fromHex(value)])) as unknown as Pick<OrbPalette,
            'idle' | 'off' | 'calibrating' | 'recordNear' | 'recordFar'
            | 'processCyan' | 'processViolet' | 'processMint' | 'sending'
            | 'done' | 'noSpeech' | 'filtered' | 'fail'>;
    cachedPaletteKey = cacheKey;
    cachedPalette = { ...converted, ...(dark ? DARK_CALIBRATION : LIGHT_CALIBRATION) };
    return cachedPalette;
};

const stateTint = (state: VoiceDaemonState, palette: OrbPalette): RGB | undefined => {
    if (state === 'calibrating') return palette.calibrating;
    if (state === 'recording') return palette.recordNear;
    if (state === 'transcribing') return palette.processCyan;
    if (state === 'sending') return palette.sending;
    if (state === 'done') return palette.done;
    if (state === 'no_speech') return palette.noSpeech;
    if (state === 'filtered') return palette.filtered;
    if (state === 'fail' || state === 'error') return palette.fail;
    return undefined;
};

const rgb = (colour: RGB, alpha: number) =>
    `rgba(${Math.round(colour[0] * 255)}, ${Math.round(colour[1] * 255)}, ${Math.round(colour[2] * 255)}, ${alpha})`;

const mix = (a: RGB, b: RGB, amount: number): RGB => [
    a[0] + (b[0] - a[0]) * amount,
    a[1] + (b[1] - a[1]) * amount,
    a[2] + (b[2] - a[2]) * amount,
];

const clamp = (value: number) => Math.min(1, Math.max(0, value));

const disc = Array.from({ length: REC_N }, (_, index) => [
    Math.sqrt((index + .5) / REC_N),
    index * REC_GA,
] as const);

function drift(time: number, lo: number, hi: number, seed: number): number {
    const value = (Math.sin(TAU * DRIFT_F1 * time + seed)
        + .55 * Math.sin(TAU * DRIFT_F2 * time + seed * 2.3)) / 1.55;
    return lo + (hi - lo) * (value + 1) / 2;
}

function waveAt(angle: number, time: number): number {
    let displacement = 0;
    let weight = 0;
    for (const [mode, amplitude, direction, phase] of IDLE_TRAINS) {
        displacement += amplitude * Math.sin(
            mode * angle - direction * Math.sqrt(mode) * time * IDLE_WAVE_SP + phase);
        weight += Math.abs(amplitude);
    }
    return weight ? displacement / weight : 0;
}

function wavePath(cx: number, cy: number, radius: number, time: number,
    spin: number, amplitude: number): Path2D {
    const path = new Path2D();
    for (let index = 0; index <= 120; index++) {
        const angle = index / 120 * TAU;
        const r = radius * (1 + amplitude * waveAt(angle - spin, time));
        const x = cx + Math.cos(angle) * r;
        const y = cy + Math.sin(angle) * r;
        if (index) path.lineTo(x, y);
        else path.moveTo(x, y);
    }
    path.closePath();
    return path;
}

function plgndr(l: number, m: number, value: number): number {
    if (m < 0 || m > l || Math.abs(value) > 1) return 0;
    let pmm = 1;
    if (m > 0) {
        const som = Math.sqrt((1 - value) * (1 + value));
        let factor = 1;
        for (let index = 1; index <= m; index++) {
            pmm *= -factor * som;
            factor += 2;
        }
    }
    if (l === m) return pmm;
    let pmmp1 = value * (2 * m + 1) * pmm;
    if (l === m + 1) return pmmp1;
    let pll = 0;
    for (let order = m + 2; order <= l; order++) {
        pll = (value * (2 * order - 1) * pmmp1 - (order + m - 1) * pmm) / (order - m);
        pmm = pmmp1;
        pmmp1 = pll;
    }
    return pll;
}

function buildY43(): readonly HarmonicLine[] {
    let normalizer = 0;
    for (let index = 0; index <= 96; index++) {
        normalizer = Math.max(normalizer, Math.abs(plgndr(4, 3, -1 + 2 * index / 96)));
    }
    const node = (u: number, v: number): HarmonicNode => {
        const cv = Math.cos(v);
        return [[cv * Math.cos(u), cv * Math.sin(u), Math.sin(v)],
            plgndr(4, 3, Math.sin(v)) / (normalizer || 1), u];
    };
    const lines: HarmonicLine[] = [];
    for (let index = 0; index < PROC_NU; index++) {
        const u = index / (PROC_NU - 1) * TAU;
        lines.push([PROC_U_LW, PROC_U_AL,
            Array.from({ length: PROC_SU + 1 }, (_, step) =>
                node(u, -Math.PI / 2 + step / PROC_SU * Math.PI)), u]);
    }
    for (let index = 0; index < PROC_NV; index++) {
        const v = -Math.PI / 2 + index / (PROC_NV - 1) * Math.PI;
        lines.push([PROC_V_LW, PROC_V_AL,
            Array.from({ length: PROC_SV + 1 }, (_, step) => node(step / PROC_SV * TAU, v)), null]);
    }
    return lines;
}

const harmonicLines = buildY43();

function harmonicExtent(): number {
    const cosYaw = Math.cos(PROC_YAW);
    const sinYaw = Math.sin(PROC_YAW);
    const cosTilt = Math.cos(PROC_TILT);
    const sinTilt = Math.sin(PROC_TILT);
    let extent = 0;
    for (let ai = 0; ai < 12; ai++) {
        const amplitude = PROC_A_LO + (PROC_A_HI - PROC_A_LO) * ai / 11;
        for (let di = 0; di < 12; di++) {
            const delta = di / 12 * TAU;
            for (const [, , nodes] of harmonicLines) {
                for (const [direction, factor, u] of nodes) {
                    const r = PROC_BASE + PROC_AMP * Math.abs(factor * Math.cos(3 * u - delta) * amplitude);
                    const x = direction[0] * r;
                    const y = direction[1] * r;
                    const z = direction[2] * r;
                    const px = x * cosYaw + z * sinYaw;
                    const pz = -x * sinYaw + z * cosYaw;
                    const py = y * cosTilt - pz * sinTilt;
                    extent = Math.max(extent, Math.hypot(px, py));
                }
            }
        }
    }
    return extent || 1;
}

const PROC_EXT = harmonicExtent();
const PROC_LW_MAX = (PROC_LW_A + PROC_LW_B) * Math.max(PROC_U_LW, PROC_V_LW);
const PROC_R = (R_OUT - PROC_LW_MAX / 2) * PROC_VIS / PROC_EXT;

export class VoiceDaemonOrb {
    readonly canvas: HTMLCanvasElement;
    private readonly context: CanvasRenderingContext2D;
    private state: VoiceDaemonState = 'idle';
    private frame = 0;
    private lastFrame = 0;
    private lastTime = performance.now() / 1000;
    private worldTime = 0;
    private processTime = 0;
    private radialPhase = 0;
    private level = 0;
    private amplitude = 0;
    private tone = .35;
    private targetLevel = 0;
    private targetTone = .35;
    private hearing = false;
    private hover = false;
    private pressed = false;
    private pressScale = 1;
    private revertAt = 0;
    private previousFrame?: HTMLCanvasElement;
    private transitionStarted = 0;
    private paletteId = 'graphite';
    private idleMotion = VOICE_IDLE_MOTION_PRESETS[3];
    private idleSize = VOICE_IDLE_SIZE_PRESETS[0];
    private sendingVariant = VOICE_SENDING_VARIANTS[15];
    private sendingSize = VOICE_SENDING_SIZE_PRESETS[1];
    private readonly reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

    constructor(host: HTMLElement, private readonly onAutoIdle?: () => void) {
        this.canvas = document.createElement('canvas');
        this.canvas.className = 'voice-daemon-orb';
        this.canvas.setAttribute('aria-hidden', 'true');
        this.context = this.canvas.getContext('2d')!;
        host.append(this.canvas);
        host.addEventListener('mouseenter', () => { this.hover = true; });
        host.addEventListener('mouseleave', () => { this.hover = false; this.pressed = false; });
        host.addEventListener('pointerdown', () => { this.pressed = true; });
        host.addEventListener('pointerup', () => { this.pressed = false; });
        this.frame = requestAnimationFrame(time => this.tick(time));
    }

    setState(state: VoiceDaemonState): void {
        if (state === this.state) return;
        if (this.canvas.width && this.canvas.height) {
            const snapshot = document.createElement('canvas');
            snapshot.width = this.canvas.width;
            snapshot.height = this.canvas.height;
            snapshot.getContext('2d')?.drawImage(this.canvas, 0, 0);
            this.previousFrame = snapshot;
            this.transitionStarted = performance.now();
        }
        this.state = state;
        this.revertAt = ['done', 'fail', 'error', 'no_speech', 'filtered'].includes(state)
            ? performance.now() + 1_200 : 0;
        if (state === 'idle' || state === 'off') {
            this.level = this.amplitude = this.targetLevel = 0;
            this.tone = this.targetTone = .35;
        }
    }

    setSignal(signal: VoiceSignal): void {
        this.targetLevel = clamp(signal.level);
        this.targetTone = clamp(signal.tone ?? .35);
        this.hearing = signal.hearing ?? this.targetLevel > .15;
    }

    setPalette(id: string): boolean {
        if (!VOICE_PALETTE_SYSTEMS.some(system => system.id === id)) return false;
        if (id === this.paletteId) return true;
        if (this.canvas.width && this.canvas.height) {
            const snapshot = document.createElement('canvas');
            snapshot.width = this.canvas.width;
            snapshot.height = this.canvas.height;
            snapshot.getContext('2d')?.drawImage(this.canvas, 0, 0);
            this.previousFrame = snapshot;
            this.transitionStarted = performance.now();
        }
        this.paletteId = id;
        return true;
    }

    setIdleMotionPreset(id: string): boolean {
        const preset = VOICE_IDLE_MOTION_PRESETS.find(candidate => candidate.id === id);
        if (!preset) return false;
        if (preset === this.idleMotion) return true;
        if (this.canvas.width && this.canvas.height) {
            const snapshot = document.createElement('canvas');
            snapshot.width = this.canvas.width;
            snapshot.height = this.canvas.height;
            snapshot.getContext('2d')?.drawImage(this.canvas, 0, 0);
            this.previousFrame = snapshot;
            this.transitionStarted = performance.now();
        }
        this.idleMotion = preset;
        return true;
    }

    setIdleSizePreset(id: string): boolean {
        const preset = VOICE_IDLE_SIZE_PRESETS.find(candidate => candidate.id === id);
        if (!preset) return false;
        if (preset === this.idleSize) return true;
        if (this.canvas.width && this.canvas.height) {
            const snapshot = document.createElement('canvas');
            snapshot.width = this.canvas.width;
            snapshot.height = this.canvas.height;
            snapshot.getContext('2d')?.drawImage(this.canvas, 0, 0);
            this.previousFrame = snapshot;
            this.transitionStarted = performance.now();
        }
        this.idleSize = preset;
        return true;
    }

    setSendingVariant(id: string): boolean {
        const variant = VOICE_SENDING_VARIANTS.find(candidate => candidate.id === id);
        if (!variant) return false;
        if (variant === this.sendingVariant) return true;
        if (this.canvas.width && this.canvas.height) {
            const snapshot = document.createElement('canvas');
            snapshot.width = this.canvas.width;
            snapshot.height = this.canvas.height;
            snapshot.getContext('2d')?.drawImage(this.canvas, 0, 0);
            this.previousFrame = snapshot;
            this.transitionStarted = performance.now();
        }
        this.sendingVariant = variant;
        return true;
    }

    setSendingSizePreset(id: string): boolean {
        const preset = VOICE_SENDING_SIZE_PRESETS.find(candidate => candidate.id === id);
        if (!preset) return false;
        if (preset === this.sendingSize) return true;
        if (this.canvas.width && this.canvas.height) {
            const snapshot = document.createElement('canvas');
            snapshot.width = this.canvas.width;
            snapshot.height = this.canvas.height;
            snapshot.getContext('2d')?.drawImage(this.canvas, 0, 0);
            this.previousFrame = snapshot;
            this.transitionStarted = performance.now();
        }
        this.sendingSize = preset;
        return true;
    }

    destroy(): void {
        cancelAnimationFrame(this.frame);
        this.canvas.remove();
    }

    private fit(): number {
        const size = this.canvas.clientWidth || SIZE;
        const ratio = Math.min(3, devicePixelRatio || 1);
        const pixels = Math.max(1, Math.round(size * ratio));
        if (this.canvas.width !== pixels || this.canvas.height !== pixels) {
            this.canvas.width = pixels;
            this.canvas.height = pixels;
        }
        this.context.setTransform(pixels / SIZE, 0, 0, pixels / SIZE, 0, 0);
        return pixels / SIZE;
    }

    private tick(nowMs: number): void {
        this.frame = requestAnimationFrame(time => this.tick(time));
        if (nowMs - this.lastFrame < 1000 / (this.reduceMotion.matches ? 4 : 24)) return;
        this.lastFrame = nowMs;
        const now = nowMs / 1000;
        const dt = Math.min(.1, now - this.lastTime);
        this.lastTime = now;
        if (this.revertAt && nowMs >= this.revertAt) {
            this.setState('idle');
            this.onAutoIdle?.();
        }

        const targetPressScale = this.pressed ? .96 : 1;
        this.pressScale += (targetPressScale - this.pressScale) * .46;

        if (this.state === 'idle' || this.state === 'off') {
            if (!this.reduceMotion.matches) this.worldTime += dt * drift(now, SPEED_LO, SPEED_HI, 1.7);
        } else if (this.state === 'recording' || this.state === 'calibrating') {
            const hearingTarget = this.hearing ? 1 : .12;
            this.level += (hearingTarget - this.level) * (hearingTarget > this.level ? .22 : .07);
            this.amplitude += (this.targetLevel - this.amplitude)
                * (this.targetLevel > this.amplitude ? .50 : .11);
            this.tone += (this.targetTone - this.tone) * .10;
            this.processTime += dt;
            this.radialPhase += dt * REC_W
                * (REC_SPD_G[0] + (REC_SPD_G[1] - REC_SPD_G[0]) * this.amplitude);
        } else if (this.state === 'transcribing' || this.state === 'sending') {
            this.processTime += dt;
        }
        this.draw(now);
    }

    private draw(now: number): void {
        this.fit();
        const context = this.context;
        context.clearRect(0, 0, SIZE, SIZE);
        const palette = resolvePalette(
            document.documentElement.getAttribute('data-theme') === 'dark', this.paletteId);
        const family = this.state === 'idle' || this.state === 'off' ? 'idle'
            : this.state === 'recording' || this.state === 'calibrating' ? 'recording'
            : this.state === 'transcribing' || this.state === 'sending' ? 'processing' : 'terminal';
        const scale = this.pressScale * palette.scale[family]
            * (family === 'idle' ? this.idleSize.multiplier : 1);
        const tint = stateTint(this.state, palette);
        const transition = this.previousFrame
            ? clamp((performance.now() - this.transitionStarted) / (this.reduceMotion.matches ? 1 : 160)) : 1;
        context.globalAlpha = transition;
        context.save();
        if (family === 'idle') context.translate(0, -1);
        if (family === 'processing') context.translate(-.5, -.5);
        if (tint && ['recording', 'calibrating', 'transcribing', 'sending'].includes(this.state)) {
            const glass = context.createRadialGradient(36, 33, 1, 36, 36, 20);
            const energy = family === 'recording' ? this.level : .42;
            glass.addColorStop(0, rgb(tint, palette.activeGlass[1] * (.72 + energy * .28)));
            glass.addColorStop(.72, rgb(tint, palette.activeGlass[0] * (.72 + energy * .28)));
            glass.addColorStop(1, rgb(tint, 0));
            context.fillStyle = glass;
            context.beginPath();
            context.arc(36, 36, 20, 0, TAU);
            context.fill();
        }
        if (!tint) this.drawIdle(context, now, scale, palette);
        else if (this.state === 'recording' || this.state === 'calibrating') this.drawRecording(context, scale, palette);
        else if (this.state === 'transcribing') this.drawProcessing(context, scale, palette);
        else if (this.state === 'sending') this.drawSending(context, scale, palette);
        else this.drawFlash(context, tint, scale);
        context.restore();
        context.globalAlpha = 1;
        if (this.previousFrame) {
            if (transition < 1) {
                context.save();
                context.setTransform(1, 0, 0, 1, 0, 0);
                context.globalAlpha = 1 - transition;
                context.drawImage(this.previousFrame, 0, 0);
                context.restore();
            } else {
                this.previousFrame = undefined;
            }
        }
    }

    private drawIdle(context: CanvasRenderingContext2D, now: number, scale: number,
        palette: OrbPalette): void {
        const amplitude = drift(now, AMPL_LO, AMPL_HI, 4.1);
        const spin = this.worldTime * IDLE_SPIN;
        const haloRadius = R_OUT * IDLE_HALO_R * scale;
        const haloPath = wavePath(36, 36, haloRadius, this.worldTime, spin,
            IDLE_HALO_AMP * amplitude * this.idleMotion.halo);
        context.save();
        context.clip(haloPath);
        const halo = context.createRadialGradient(36, 36, 0, 36, 36, haloRadius);
        const idleColour = this.state === 'off' ? palette.off : palette.idle;
        const peak = palette.idleHalo * (this.state === 'off' ? .32 : this.hover ? 1.55 : 1);
        halo.addColorStop(0, rgb(idleColour, peak));
        halo.addColorStop(.34, rgb(idleColour, peak * .55));
        halo.addColorStop(.66, rgb(idleColour, peak * .18));
        halo.addColorStop(1, rgb(idleColour, 0));
        context.fillStyle = halo;
        context.fillRect(0, 0, SIZE, SIZE);
        context.restore();

        context.strokeStyle = rgb(idleColour, this.state === 'off' ? .55
            : this.hover ? 1 : palette.idleAlpha);
        context.lineWidth = palette.idleLineWidth;
        context.stroke(wavePath(36, 36, IDLE_R * scale, this.worldTime, spin,
            IDLE_WAVE_AMP * amplitude * this.idleMotion.contour));
    }

    private drawRecording(context: CanvasRenderingContext2D, scale: number,
        palette: OrbPalette): void {
        const amplitude = this.amplitude;
        const radius = REC_R * scale;
        const displacement = REC_DISP * (REC_DISP_G[0]
            + (REC_DISP_G[1] - REC_DISP_G[0]) * amplitude);
        const waveNumber = REC_K * (REC_TONE_G[0]
            + (REC_TONE_G[1] - REC_TONE_G[0]) * this.tone);
        const dotGain = REC_DOT_G[0] + (REC_DOT_G[1] - REC_DOT_G[0]) * amplitude;
        const alphaGain = REC_ALP_G[0] + (REC_ALP_G[1] - REC_ALP_G[0]) * amplitude;
        for (const [r0, angle] of disc) {
            const wave = Math.sin(r0 * waveNumber - this.radialPhase) * .5;
            const radial = r0 + wave * displacement;
            const strength = wave + .5;
            const recordFar = this.state === 'calibrating' ? palette.idle : palette.recordFar;
            const recordNear = this.state === 'calibrating' ? palette.calibrating : palette.recordNear;
            const colour = mix(recordFar, recordNear,
                Math.min(1, strength * (.90 + .30 * amplitude)));
            const dotRadius = (REC_DOT_MIN + (REC_DOT_MAX - REC_DOT_MIN) * strength)
                * dotGain * scale;
            context.fillStyle = rgb(colour,
                (palette.recordingFloor + (REC_A_MAX - palette.recordingFloor) * strength) * alphaGain
                    * palette.recordingAlpha);
            context.beginPath();
            context.arc(36 + Math.cos(angle) * radial * radius,
                36 + Math.sin(angle) * radial * radius, dotRadius, 0, TAU);
            context.fill();
        }
    }

    private drawProcessing(context: CanvasRenderingContext2D, scale: number,
        palette: OrbPalette): void {
        const time = this.processTime;
        const delta = time * PROC_TRAV;
        const amplitude = (PROC_A_LO + PROC_A_HI) / 2
            + (PROC_A_HI - PROC_A_LO) / 2 * Math.cos(time * PROC_PULSE);
        const flip = Math.cos(time * PROC_PULSE) < 0;
        const theta = time * PROC_SPIN;
        const cosAngle = Math.cos(theta);
        const sinAngle = Math.sin(theta);
        const cosYaw = Math.cos(PROC_YAW);
        const sinYaw = Math.sin(PROC_YAW);
        const cosTilt = Math.cos(PROC_TILT);
        const sinTilt = Math.sin(PROC_TILT);
        const radius = PROC_R * scale;
        const lines: Array<{
            colour: RGB; lineWeight: number; alphaWeight: number;
            points: Array<readonly [number, number, number]>;
        }> = [];
        let depthRadius = 0;
        for (const [lineWeight, alphaWeight, nodes, uLine] of harmonicLines) {
            const points: Array<readonly [number, number, number]> = [];
            for (const [direction, factor, u] of nodes) {
                const field = factor * Math.cos(3 * u - delta);
                const r = PROC_BASE + PROC_AMP * Math.abs(field * amplitude);
                const x = direction[0] * r;
                const y = direction[1] * r;
                const z = direction[2] * r;
                const px = x * cosYaw + z * sinYaw;
                const pz = -x * sinYaw + z * cosYaw;
                const py = y * cosTilt - pz * sinTilt;
                const qz = y * sinTilt + pz * cosTilt;
                depthRadius = Math.max(depthRadius, Math.abs(qz));
                points.push([36 + (px * cosAngle - py * sinAngle) * radius,
                    36 + (px * sinAngle + py * cosAngle) * radius, qz]);
            }
            const positive = uLine !== null && ((Math.cos(3 * uLine - delta) >= 0) !== flip);
            lines.push({ colour: uLine === null ? palette.processMint
                : positive ? palette.processCyan : palette.processViolet,
                lineWeight, alphaWeight, points });
        }
        depthRadius ||= 1;
        for (const line of lines) {
            for (let band = 0; band < PROC_BANDS; band++) {
                const path = new Path2D();
                let hasSegment = false;
                for (let index = 0; index < line.points.length - 1; index++) {
                    const a = line.points[index];
                    const b = line.points[index + 1];
                    const depth = .5 + .5 * (a[2] + b[2]) / 2 / depthRadius;
                    const segmentBand = Math.min(PROC_BANDS - 1, Math.max(0, Math.floor(depth * PROC_BANDS)));
                    if (segmentBand !== band) continue;
                    path.moveTo(a[0], a[1]);
                    path.lineTo(b[0], b[1]);
                    hasSegment = true;
                }
                if (!hasSegment) continue;
                const depth = (band + .5) / PROC_BANDS;
                context.strokeStyle = rgb(line.colour,
                    (PROC_AL_A + PROC_AL_B * depth) * line.alphaWeight * palette.processAlpha);
                context.lineWidth = (PROC_LW_A + PROC_LW_B * depth) * line.lineWeight
                    * scale * palette.processLine;
                context.lineCap = 'round';
                context.stroke(path);
            }
        }
    }

    private drawSending(context: CanvasRenderingContext2D, scale: number,
        palette: OrbPalette): void {
        this.drawIntrinsicSendingField(context, scale, palette);
    }

    private drawIntrinsicSendingField(context: CanvasRenderingContext2D, scale: number,
        palette: OrbPalette): void {
        const t = this.processTime;
        const radius = 34.7 * scale / palette.scale.processing * this.sendingSize.multiplier;
        const primary = palette.sending;
        const secondary = palette.processViolet;
        const tertiary = palette.processMint;
        const colours = [primary, secondary, tertiary] as const;
        type Point2 = readonly [number, number];
        const point = (r: number, angle: number): Point2 => [
            36 + Math.cos(angle) * radius * r,
            36 + Math.sin(angle) * radius * r,
        ];
        const stroke = (points: readonly Point2[], colour: RGB = primary,
            alpha = .66, width = .62, closed = false) => {
            if (points.length < 2) return;
            context.strokeStyle = rgb(colour, alpha);
            context.lineWidth = width;
            context.lineCap = 'round';
            context.lineJoin = 'round';
            context.beginPath();
            points.forEach(([x, y], index) => index ? context.lineTo(x, y) : context.moveTo(x, y));
            if (closed) context.closePath();
            context.stroke();
        };
        const polar = (fn: (angle: number) => number, colour: RGB = primary,
            alpha = .66, width = .62, samples = 144) => stroke(
                Array.from({ length: samples + 1 }, (_, index) => {
                    const angle = index / samples * TAU;
                    return point(fn(angle), angle);
                }), colour, alpha, width, true);
        const circle = (r: number, colour: RGB = primary, alpha = .66, width = .62) =>
            polar(() => r, colour, alpha, width, 128);
        const dot = (r: number, angle: number, size: number, colour: RGB, alpha: number) => {
            const [x, y] = point(r, angle);
            context.fillStyle = rgb(colour, alpha);
            context.beginPath();
            context.arc(x, y, size, 0, TAU);
            context.fill();
        };
        const factorial = (n: number) => {
            let value = 1;
            for (let i = 2; i <= n; i++) value *= i;
            return value;
        };
        const bessel = (n: number, value: number) => {
            let sum = 0;
            for (let k = 0; k < 11; k++) sum += (-1) ** k * (value / 2) ** (2 * k + n)
                / (factorial(k) * factorial(k + n));
            return sum;
        };
        const laguerre = (n: number, a: number, value: number) => {
            let sum = 0;
            for (let k = 0; k <= n; k++) sum += (-1) ** k
                * factorial(n + a) / (factorial(n - k) * factorial(a + k) * factorial(k))
                * value ** k;
            return sum;
        };
        const zernikeRadial = (n: number, m: number, r: number) => {
            let sum = 0;
            for (let s = 0; s <= (n - m) / 2; s++) sum += (-1) ** s
                * factorial(n - s) / (factorial(s)
                    * factorial((n + m) / 2 - s) * factorial((n - m) / 2 - s))
                * r ** (n - 2 * s);
            return sum;
        };

        switch (this.sendingVariant.id) {
            case '01': { // Harmonic phase on a fixed sphere: geometry never deforms.
                // Uniform great-circle atlas. Every ellipse is a genuine plane
                // section of S²; the family, rather than a copied projection,
                // supplies the sphere's isotropic visual mass.
                for (let i = 0; i < 15; i++) {
                    const orientation = i / 15 * Math.PI + t * .045;
                    const co = Math.cos(orientation), so = Math.sin(orientation);
                    const nodes = Array.from({ length: 97 }, (_, j): Point2 => {
                        const angle = j / 96 * TAU;
                        const x = Math.cos(angle);
                        const y = .24 * Math.sin(angle);
                        return [36 + radius * (x * co - y * so),
                            36 + radius * (x * so + y * co)];
                    });
                    stroke(nodes, colours[i % 3], .48, .54);
                }
                break;
            }
            case '02': // Zernike Z_7^3 on nested disk coordinates.
                for (let band = 1; band <= 9; band++) {
                    const base = .09 + band * .095;
                    const radial = zernikeRadial(7, 3, base);
                    polar(angle => base + (1 - base) * .055 * radial
                        * Math.cos(3 * angle - t * .8), colours[band % 3],
                    .35 + band * .045, .54 + band * .025);
                }
                for (let node = 0; node < 6; node++) {
                    const angle = node / 6 * TAU + t * .028;
                    stroke([point(.08, angle), point(.94, angle)],
                        node % 2 ? secondary : tertiary, .28, .46);
                }
                break;
            case '03': // Fourier–Bessel membrane eigenmode.
                for (let band = 1; band <= 9; band++) {
                    const base = .08 + band * .096;
                    const amplitude = bessel(4, 7.6 * base);
                    polar(angle => base + .026 * amplitude * Math.cos(4 * angle - t),
                        colours[band % 3], .34 + band * .05, .52);
                }
                for (let spoke = 0; spoke < 8; spoke++) {
                    const angle = spoke / 8 * TAU + t * .055;
                    stroke(Array.from({ length: 48 }, (_, i) => point(.05 + i / 50 * .9,
                        angle + .018 * Math.sin(i / 47 * Math.PI * 2 + t))),
                    spoke % 2 ? secondary : primary, .3, .45);
                }
                break;
            case '04': { // LG_3^2 phase fronts: an intrinsic optical vortex.
                for (let arm = 0; arm < 10; arm++) {
                    const nodes: Point2[] = [];
                    for (let i = 0; i <= 90; i++) {
                        const r = .06 + i / 90 * .91;
                        const phase = arm / 10 * TAU + t * .24 + 2.15 * r * r
                            + .025 * laguerre(3, 2, 3.2 * r * r);
                        nodes.push(point(r, phase));
                    }
                    stroke(nodes, colours[arm % 3], .38 + arm % 3 * .14, .62);
                }
                for (let ring = 1; ring <= 5; ring++) circle(ring / 5.5,
                    colours[ring % 3], .2 + ring * .055, .44);
                break;
            }
            case '05': { // Uniform orbit of a genuine Poincaré geodesic.
                const delta = .37 * Math.PI;
                const centreDistance = 1 / Math.cos(delta);
                const geodesicRadius = Math.tan(delta);
                for (let family = 0; family < 16; family++) {
                    const mid = family / 16 * TAU + t * .035;
                    const nodes: Point2[] = [];
                    for (let i = 0; i <= 56; i++) {
                        const a = Math.PI / 2 + delta + i / 56 * (Math.PI - 2 * delta);
                        const x = centreDistance + geodesicRadius * Math.cos(a);
                        const y = geodesicRadius * Math.sin(a);
                        nodes.push([36 + radius * (x * Math.cos(mid) - y * Math.sin(mid)),
                            36 + radius * (x * Math.sin(mid) + y * Math.cos(mid))]);
                    }
                    stroke(nodes, colours[family % 3], .48, .56);
                }
                break;
            }
            case '06': { // Symmetric Descartes packing; every circle is structural.
                circle(.32, primary, .72, .75);
                for (let i = 0; i < 6; i++) {
                    const angle = i / 6 * TAU + t * .035;
                    const [cx, cy] = point(.64, angle);
                    context.strokeStyle = rgb(colours[i % 3], .58);
                    context.lineWidth = .65;
                    context.beginPath(); context.arc(cx, cy, radius * .32, 0, TAU); context.stroke();
                    const gapAngle = angle + Math.PI / 6;
                    const [gx, gy] = point(.54, gapAngle);
                    context.strokeStyle = rgb(colours[(i + 1) % 3], .48);
                    context.beginPath(); context.arc(gx, gy, radius * .085, 0, TAU); context.stroke();
                }
                break;
            }
            case '07': // Hopf fibers are circles; the atlas is circular before animation.
                for (let fiber = 0; fiber < 12; fiber++) {
                    const angle = fiber / 12 * TAU + t * .07;
                    const [cx, cy] = point(.19, angle);
                    const rr = radius * .78;
                    context.strokeStyle = rgb(colours[fiber % 3], .28 + fiber % 4 * .12);
                    context.lineWidth = .55;
                    context.beginPath(); context.arc(cx, cy, rr, 0, TAU); context.stroke();
                }
                break;
            case '08': // KAM invariant curves.
                for (let torus = 1; torus <= 11; torus++) {
                    const base = .06 + torus * .082;
                    polar(angle => base * (1 + .035 * (1 - base)
                        * Math.cos(5 * angle + torus * .37 - t * .7)),
                    colours[torus % 3], .28 + torus * .045, .5 + torus * .015);
                }
                break;
            case '09': // Level sets of H=r²/2+εr⁴cos4θ.
                for (let energy = 1; energy <= 10; energy++) {
                    const base = (.07 + energy * .086) * 1.03;
                    polar(angle => base / Math.sqrt(1 + .18 * base * base
                        * Math.cos(4 * angle - t * .55)), colours[energy % 3],
                    .3 + energy * .045, .54);
                }
                break;
            case '10': { // Phase contours of a five-zero finite Blaschke product.
                for (let band = 1; band <= 10; band++) {
                    const base = .06 + band * .088;
                    polar(angle => {
                        const zx = base * Math.cos(angle), zy = base * Math.sin(angle);
                        let br = 1, bi = 0;
                        for (let root = 0; root < 5; root++) {
                            const rootAngle = root / 5 * TAU + t * .08;
                            const ax = .34 * Math.cos(rootAngle), ay = .34 * Math.sin(rootAngle);
                            const nr = zx - ax, ni = zy - ay;
                            const dr = 1 - ax * zx - ay * zy;
                            const di = ax * zy - ay * zx;
                            const den = dr * dr + di * di;
                            const qr = (nr * dr + ni * di) / den;
                            const qi = (ni * dr - nr * di) / den;
                            [br, bi] = [br * qr - bi * qi, br * qi + bi * qr];
                        }
                        return base + .024 * (1 - base) * Math.cos(Math.atan2(bi, br) + band * .5);
                    }, colours[band % 3], .3 + band * .05, .54);
                }
                for (let root = 0; root < 5; root++) dot(.34,
                    root / 5 * TAU + t * .08, 1.15, colours[root % 3], .82);
                break;
            }
            case '11': // Jacobi theta spectral rings.
                for (let band = 1; band <= 10; band++) {
                    const base = .06 + band * .088;
                    polar(angle => {
                        let theta = 0;
                        for (let n = 1; n <= 6; n++) theta += 2 * .58 ** (n * n)
                            * Math.cos(2 * n * (3 * angle - t * .45 + band * .11));
                        return base + .018 * (1 - base) * theta;
                    }, colours[band % 3], .3 + band * .047, .52);
                }
                for (let i = 0; i < 24; i++) {
                    const angle = i / 24 * TAU;
                    const thetaPulse = .5 + .5 * Math.cos(6 * angle - t * .45);
                    dot(.72, angle, .45 + thetaPulse * .55,
                        colours[i % 3], .34 + thetaPulse * .42);
                }
                break;
            case '12': { // Farey/Poincaré geodesics, each orthogonal to ∂D².
                for (let q = 3; q <= 7; q++) for (let p = 0; p < q; p++) {
                    const start = (p / q + t * .002) * TAU;
                    const end = ((p + 1) / q + t * .002) * TAU;
                    const mid = (start + end) / 2;
                    const delta = (end - start) / 2;
                    const centreDistance = 1 / Math.cos(delta);
                    const geodesicRadius = Math.tan(delta);
                    const nodes: Point2[] = [];
                    for (let i = 0; i <= 42; i++) {
                        const a = Math.PI / 2 + delta + i / 42 * (Math.PI - 2 * delta);
                        const x = centreDistance + geodesicRadius * Math.cos(a);
                        const y = geodesicRadius * Math.sin(a);
                        const xr = x * Math.cos(mid) - y * Math.sin(mid);
                        const yr = x * Math.sin(mid) + y * Math.cos(mid);
                        nodes.push([36 + xr * radius, 36 + yr * radius]);
                    }
                    stroke(nodes, colours[(p + q) % 3], .18 + q * .065, .46);
                }
                break;
            }
            case '13': // Radial Wigner–Laguerre shells.
                for (let shell = 1; shell <= 11; shell++) {
                    const r = shell / 11.6;
                    const wigner = Math.exp(-3.2 * r * r) * laguerre(6, 0, 6.4 * r * r);
                    const phase = .78 + .22 * Math.sin(t * .55 + shell * .72);
                    circle(r, wigner >= 0 ? primary : secondary,
                        (.28 + .55 * Math.min(1, Math.abs(wigner))) * phase,
                        .5 + Math.abs(wigner) * .95);
                }
                break;
            case '14': // Slepian concentration mode in a circular domain.
                for (let band = 1; band <= 10; band++) {
                    const base = .06 + band * .089;
                    polar(angle => {
                        const mode = .55 * bessel(2, 4.8 * base) * Math.cos(2 * angle - t * .5)
                            + .3 * bessel(5, 7.2 * base) * Math.cos(5 * angle + t * .31);
                        return base + .026 * (1 - base) * mode;
                    }, colours[band % 3], .31 + band * .046, .52);
                }
                for (let node = 0; node < 10; node++) {
                    const angle = node / 10 * TAU + t * .018;
                    stroke(Array.from({ length: 42 }, (_, i) => {
                        const r = .08 + i / 44 * .86;
                        return point(r, angle + .025 * bessel(3, 6 * r));
                    }), colours[node % 3], .2, .42);
                }
                break;
            case '15': // Deterministic circular Fekete shells for the Ginibre gas.
                for (let shell = 1; shell <= 7; shell++) {
                    const count = 5 + shell * 4;
                    const r = Math.sqrt(shell / 7.5) * .99;
                    for (let i = 0; i < count; i++) dot(r,
                        i / count * TAU + t * .06 * (shell % 2 ? 1 : -1),
                    .52 + shell * .075, colours[(i + shell) % 3], .38 + shell * .075);
                }
                break;
            case '16': // Golden-angle disk sampling.
                for (let i = 0; i < 180; i++) {
                    const baseRadius = Math.sqrt((i + .5) / 180) * .97;
                    const band = Math.floor(baseRadius * 7);
                    let r = baseRadius;
                    let angle = i * REC_GA;
                    let energy = .5;
                    switch (this.sendingSize.id) {
                        case '40':
                            angle += t * .16;
                            energy = .44;
                            break;
                        case '42':
                            // A fast travelling energy front moves across the
                            // golden-angle disk while the 42px support stays fixed.
                            {
                                angle += t * .52 + .026 * Math.sin(t * 1.8 + i * .13);
                                const phaseWave = .5 + .5 * Math.cos(
                                    i * .15 - t * 2.15 + band * .38);
                                energy = .12 + .88 * phaseWave;
                            }
                            break;
                        case '44':
                            angle += t * (.14 + .28 * baseRadius);
                            energy = .42 + .18 * baseRadius;
                            break;
                        case '46':
                            r *= 1 + .022 * Math.sin(t * 1.45 + baseRadius * TAU * 2);
                            angle += t * .24;
                            energy = .48 + .14 * Math.sin(t * 1.45 + baseRadius * TAU * 2);
                            break;
                        default:
                            {
                                const direction = band % 2 ? 1 : -1;
                                angle += t * .78 * direction;
                                r *= 1 + .02 * Math.sin(t * 1.15 * direction + band * .74);
                                energy = .3 + .5 * (.5 + .5 * Math.cos(
                                    3 * angle - t * 1.05 * direction + band * .63));
                            }
                    }
                    const phaseWave = this.sendingSize.id === '42';
                    const colour = phaseWave && energy > .62
                        ? colours[band % 3]
                        : colours[(i + band) % 13 === 0 ? 2 : band % 3 === 0 ? 1 : 0];
                    dot(r, angle,
                        .46 + r * (.52 + energy * (phaseWave ? .58 : .28)),
                        colour,
                        .22 + r * .4 + energy * (phaseWave ? .42 : .24));
                }
                break;
            case '17': // Six genuine Fermat spirals form a circular transport field.
                for (let arm = 0; arm < 6; arm++) {
                    const nodes: Point2[] = [];
                    for (let i = 0; i <= 150; i++) {
                        const r = Math.sqrt(i / 150) * .97;
                        const angle = (i / 150) * TAU * 2.8 + arm / 6 * TAU
                            + t * .18 * (arm % 2 ? -1 : 1);
                        nodes.push(point(r, angle));
                    }
                    stroke(nodes, colours[arm % 3], .42 + arm % 2 * .22, .62);
                }
                break;
            case '18': // Coupled oscillator rings; phase evolves, support does not.
                for (let ring = 1; ring <= 5; ring++) {
                    const count = 7 + ring * 4;
                    const r = .12 + ring * .165;
                    circle(r, colours[ring % 3], .16 + ring * .035, .42);
                    for (let i = 0; i < count; i++) {
                        const natural = (i % 5 - 2) * .018;
                        const phase = i / count * TAU + t * (.1 + natural)
                            + .035 * Math.sin(t * .42 + ring);
                        dot(r, phase, .62 + ring * .07, colours[(i + ring) % 3], .52 + ring * .07);
                    }
                }
                break;
            case '19': // Gaussian free field sampled on an undeformed polar lattice.
                for (let ring = 1; ring <= 10; ring++) {
                    const r = .06 + ring * .089;
                    const count = 18 + ring * 2;
                    for (let i = 0; i < count; i++) {
                        const angle = i / count * TAU;
                        let field = 0;
                        for (let m = 1; m <= 5; m++) field += bessel(m, (m + 2.1) * r)
                            * Math.cos(m * angle + m * 1.37 - t * (.16 + m * .035)) / Math.sqrt(m);
                        dot(r, angle, .7 + Math.abs(field) * .05,
                            field >= 0 ? primary : secondary, .54 + Math.min(.04, Math.abs(field) * .05));
                    }
                }
                break;
            default: // Heat kernel on concentric S¹ fibers.
                for (let ring = 1; ring <= 10; ring++) {
                    const r = .06 + ring * .089;
                    const count = 28 + ring * 2;
                    for (let i = 0; i < count; i++) {
                        const angle = i / count * TAU;
                        let heat = 1;
                        const tau = .12 + ring * .008;
                        for (let n = 1; n <= 6; n++) heat += 2 * Math.exp(-n * n * tau)
                            * Math.cos(n * (angle - t * .22));
                        const normalized = Math.max(0, Math.min(1, heat / 3.4));
                        const radialPulse = .5 + .5 * Math.sin(t * .35 + ring * .57);
                        dot(r, angle, .68 + radialPulse * .12 + normalized * .04,
                            colours[(ring + i) % 3], .5 + radialPulse * .1 + normalized * .03);
                    }
                }
        }
    }

    private drawFlash(context: CanvasRenderingContext2D, tint: RGB, scale: number): void {
        context.strokeStyle = rgb(tint, .95);
        context.lineWidth = FLASH_LW;
        context.beginPath();
        context.arc(36, 36, (R_FLASH_OUT - FLASH_LW / 2) * scale, 0, TAU);
        context.stroke();
    }
}
