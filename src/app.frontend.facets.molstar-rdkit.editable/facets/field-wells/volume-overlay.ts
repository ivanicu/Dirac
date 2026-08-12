/**
 * The field, drawn as light over the scene instead of as plastic inside it.
 *
 * WHAT THIS REPLACES. Until now a field was two or three mol* isosurface
 * representations per sign: opaque or xray-shaded shells at fractions of one
 * isovalue. That is the incumbent everywhere and it has one defect that no
 * amount of tuning fixes — IT OCCLUDES THE LIGAND. The thing the chemist is
 * reasoning about goes behind a coloured shell, and the only escape is to turn
 * the alpha down until the field stops being readable.
 *
 * WHY AN OVERLAY IS LEGITIMATE HERE, and would not be for a normal
 * representation. This renderer is ADDITIVE and NON-OCCLUDING by construction:
 * it never claims to be in front of anything. So it does not need to
 * participate in mol*'s depth buffer, and compositing it over the finished
 * frame with a blend mode is not a shortcut — it is the correct model. A
 * representation that DID occlude could not do this and would have to go
 * through mol*'s own renderer.
 *
 * FOUR SEPARABLE CHANNELS, from the design study in design/field-3d:
 *   shape      — the rim of the +-iso surface. The only channel that states a
 *                boundary, and it states it without filling it in.
 *   steepness  — |grad V| outside that surface. Where a partner feels a FORCE,
 *                which is not where V is large.
 *   direction  — noise smeared along grad V. The grain is the vector.
 *   sign       — sampled AT the first crossing, not integrated along the ray.
 *
 * The last one is the whole reason the other three are usable. Shape,
 * steepness and direction all discard sign — |grad V| by construction, the LIC
 * grain by noise, a rim because a rim is the thinnest possible carrier for a
 * hue. Donor versus acceptor is the first question anyone asks of an
 * electrostatic potential, so three channels that cannot answer it are an
 * atmosphere rather than an instrument.
 *
 * AND SIGN IS SAMPLED, NOT AVERAGED. The study first integrated a
 * signal-weighted mean sign along each ray, and a mean is not a measurement
 * until you say how much it averaged: a ray grazing the molecule collected
 * almost nothing and still returned a full-strength +-1, so the sign channel
 * painted the entire bounding box a flat colour. Weighting it by its own
 * support made it vanish instead. The fix was not a better weight, it was a
 * better locus — one value per ray, taken where the ray meets the surface the
 * user is already choosing with the isovalue slider.
 */

import { PluginContext } from '../../../mol-plugin/context';
import { Grid, Volume } from '../../../mol-model/volume';
import { Mat4, Vec3 } from '../../../mol-math/linear-algebra';

const VERT = `#version 300 es
in vec2 aPos; void main() { gl_Position = vec4(aPos, 0.0, 1.0); }`;

const FRAG = `#version 300 es
precision highp float; precision highp sampler3D;
out vec4 fragColor;

uniform sampler3D uVol;
uniform mat4  uWorldToTex;   // world Angstrom -> [0,1]^3 texture coords
uniform mat4  uInvView;      // camera basis, world space
uniform vec3  uEye;
uniform vec2  uRes;
uniform float uTanHalfFov;
uniform float uOrtho;        // 0 perspective, 1 orthographic
uniform float uOrthoHeight;
uniform float uIso;          // isovalue, in the volume's own units
uniform float uScale;        // |value| that saturates the SIGN channel
uniform float uGradScale;    // |grad| that saturates the STEEPNESS channel,
                             // MEASURED off this volume — see setVolume()
uniform float uStepWorld;    // march step, Angstrom
uniform int   uSteps;
uniform vec4  uChan;         // shape · steepness · direction · sign
uniform float uLight;        // 0 = dark scene (screen), 1 = light scene (multiply)
uniform vec3  uNeg;
uniform vec3  uPos;
uniform float uProbe;
uniform int   uStyle;   // 0 ink · 1 wash · 2 contour · 3 blueprint · 4 glow(dark)

float val(vec3 t) { return texture(uVol, t).r; }

/** how much of the sample is real rather than boundary window.
 *  The cube is where the calculation exists; a hard stop at the face draws
 *  that ignorance as a solid hexagonal object, so the outer shell is faded.
 *  But a window has a DERIVATIVE and a LEVEL SET too — left ungated, the
 *  gradient channels redraw the box from the window's own slope and the rim
 *  finds a spurious crossing on the face. Every channel gates on this. */
float known(vec3 t) {
    vec3 d = min(t, 1.0 - t);
    vec3 w = smoothstep(vec3(0.0), vec3(0.055), d);
    return w.x * w.y * w.z;
}

vec3 gradAt(vec3 t, float e) {
    return vec3(val(t + vec3(e,0,0)) - val(t - vec3(e,0,0)),
                val(t + vec3(0,e,0)) - val(t - vec3(0,e,0)),
                val(t + vec3(0,0,e)) - val(t - vec3(0,0,e))) / (2.0 * e);
}

float hash(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }

// premultiplied 'over', accumulating into (col, a)
#define OVER(C, A) { float _a = clamp(A, 0.0, 1.0); col = (C) * _a + col * (1.0 - _a); a = _a + a * (1.0 - _a); }

/** slab test in TEXTURE space, which handles a non-orthogonal cell for free */
bool hitBox(vec3 ro, vec3 rd, out float t0, out float t1) {
    vec3 inv = 1.0 / rd;
    vec3 a = (vec3(0.0) - ro) * inv, b = (vec3(1.0) - ro) * inv;
    vec3 lo = min(a, b), hi = max(a, b);
    t0 = max(max(lo.x, lo.y), lo.z);
    t1 = min(min(hi.x, hi.y), hi.z);
    return t1 > max(t0, 0.0);
}

void main() {
    // probe 1 = paint the whole canvas: does the GL pipeline reach the screen
    // at all? probe 2 = paint only where a ray enters the box: is the ray/box
    // math right? Two different failures that look identical from outside.
    if (uProbe > 0.5 && uProbe < 1.5) { fragColor = vec4(0.10, 0.75, 0.30, 0.40); return; }
    vec2 ndc = (gl_FragCoord.xy / uRes) * 2.0 - 1.0;
    float aspect = uRes.x / uRes.y;

    vec3 right = uInvView[0].xyz, up = uInvView[1].xyz, fwd = -uInvView[2].xyz;
    vec3 ro, rd;
    if (uOrtho > 0.5) {
        float h = uOrthoHeight * 0.5;
        ro = uEye + right * (ndc.x * h * aspect) + up * (ndc.y * h);
        rd = normalize(fwd);
    } else {
        ro = uEye;
        rd = normalize(fwd + right * (ndc.x * aspect * uTanHalfFov) + up * (ndc.y * uTanHalfFov));
    }

    // into texture space: the box is the unit cube there whatever the cell is
    vec3 tro = (uWorldToTex * vec4(ro, 1.0)).xyz;
    vec3 trd = (uWorldToTex * vec4(rd, 0.0)).xyz;

    float t0, t1;
    // A RAY THAT MISSES THE BOX MUST WRITE NOTHING. This line survived from
    // the multiply-filter model, where "no field here" meant WHITE (multiply's
    // identity). Under alpha compositing white is opaque, so the overlay was
    // painting the entire viewport outside the volume — the protein vanished
    // and only the box-shaped hole showed mol* underneath. That hole is the
    // "brown blob" I spent three shader rewrites trying to fix: it was never
    // my renderer, it was the ligand seen through a gap in my own white sheet.
    // Proven, not guessed: all-channels-ON and all-channels-OFF differed by
    // exactly ZERO pixels.
    if (!hitBox(tro, trd, t0, t1)) { fragColor = vec4(0.0); return; }
    if (uProbe > 1.5) { fragColor = vec4(0.90, 0.10, 0.10, 0.45); return; }
    t0 = max(t0, 0.0);

    float span = t1 - t0;
    float dt = span / float(uSteps);
    // Accumulate per FRACTION OF THE TRAVERSAL, so a full pass through the box
    // is O(1) whatever the step count or the cube's physical size. The previous
    // expression multiplied a world step by a texture-space dt and collapsed to
    // ~0.003, so the steepness and direction channels arrived at roughly 5%
    // alpha — present, correct, and invisible. A quantity that is merely too
    // small does not announce itself; it looks exactly like a quantity that is
    // absent, which is why this hid behind the opaque-white bug above.
    float dtn = 1.0 / float(uSteps);

    float steep = 0.0, grain = 0.0;
    vec3 rim = vec3(0.0);
    float sgn = 0.0, face = 1.0;
    bool gotSign = false;
    float prev = 0.0; bool first = true;
    float e = dt * 0.9;

    for (int i = 0; i < 384; i++) {
        if (i >= uSteps) break;
        vec3 t = tro + trd * (t0 + dt * float(i));
        float k = known(t);
        float v = val(t) * k;

        if (!first && k > 0.985 && abs(v) > uIso && abs(prev) <= uIso) {
            vec3 g = gradAt(t, e);
            vec3 n = normalize(-g * sign(v));
            // rd is world; the normal is texture-space, so compare in texture
            // space too — mixing the two is a silent, plausible, wrong picture
            vec3 rdt = normalize(trd);
            float r = pow(1.0 - abs(dot(n, rdt)), 3.0);
            // MAX, NOT SUM. A rim is a SILHOUETTE — "is this pixel near an edge
            // of the surface" — and summing turns it into "how many times did
            // this ray cross the surface", which is a different quantity that
            // happens to look similar on a dark ground where it reads as glow.
            // On paper it reads as INK: an MEP ray crosses the +-iso surface
            // many times through a lobed molecule, rim pinned at its ceiling
            // over the whole box footprint, and near-maximum neutral ink over
            // warm paper is precisely the brown smear Ivan was looking at.
            // Bounded by construction, and it is now the quantity it claims.
            // Accumulate, bounded later. 'max' made this a pure silhouette and
            // lost the nested-shell structure that gives the picture its form:
            // a molecule with many lobes crosses the contour many times, and
            // THAT is the shape. Kept achromatic so hue stays the sign's alone.
            rim += vec3(r);
            if (!gotSign) {
                gotSign = true;
                sgn = clamp(v / uScale, -1.0, 1.0);
                face = 0.32 + 0.68 * max(0.0, dot(n, -rdt));
            }
        }
        prev = v; first = false;

        // Steepness lives OUTSIDE the contour — but "outside the contour" also
        // means "everywhere in the empty half of the box", and integrating
        // there filled the whole bounding box with uniform ink: a rectangle,
        // not a molecule. Require the field to be actually PRESENT, not merely
        // below the contour.
        if (k < 0.985 || abs(v) > uIso || abs(v) < uIso * 0.10) continue;
        vec3 g = gradAt(t, e);
        float gm = length(g) / max(uGradScale, 1e-9);
        steep += gm * dtn * 2.6;

        if (gm > 0.02) {
            vec3 dir = g / max(length(g), 1e-6);
            float sm = 0.0;
            for (int q = -3; q <= 3; q++) sm += hash(floor((t + dir * float(q) * 0.011) * 150.0));
            grain += pow(sm / 7.0, 3.0) * clamp(gm * 4.0, 0.0, 1.0) * dtn * 5.0;
        }
    }

    // EVERY CHANNEL ENTERS BOUNDED IN [0,1]. rim and grain were raw ray
    // accumulations with no ceiling — a ray crossing the contour many times
    // added 1.35 per crossing — so they ran away and saturated. Saturating
    // exponentially keeps the ordering: more crossings still reads as more.
    steep = clamp(steep, 0.0, 1.0);
    float rimD   = 1.0 - exp(-rim.x * 1.30);
    float grainD = 1.0 - exp(-grain);
    float steepD = pow(steep, 0.75);
    float signD  = abs(sgn) * face;

    vec3 rimHue = length(rim) > 1e-5 ? normalize(rim) * 1.732 : vec3(1.0);
    vec3 signHue = sgn < 0.0 ? uNeg : uPos;

    // COLOUR + ALPHA, COMPOSITED NORMALLY — not a CSS blend mode.
    //
    // The first version emitted a multiplicative filter and asked CSS to
    // mix-blend-mode:multiply it onto the scene (no backticks in here: this
    // whole shader is a template literal and one would end it). That is
    // only correct if
    // mol*'s canvas is in the SAME STACKING CONTEXT as this one, because a
    // blend mode blends with the backdrop *within* its context. It is not, so
    // the backdrop was the parent's own background: multiply against white
    // returns the filter unchanged, and the canvas then painted that opaque
    // over the viewport. The symptom was total — the whole scene went white
    // with a single brown blob in it, and the protein vanished — which reads
    // as "the renderer is broken" rather than "the compositing model is
    // wrong", and sent me tuning the shader three times.
    //
    // Straight alpha has no such dependency: alpha 0 is exactly "nothing here"
    // no matter what is underneath or how the DOM is nested.
    // Weights raised ~3x after LOOKING at it in the app rather than at a pixel
    // histogram. Correctly placed, correctly composited, and so faint it read
    // as nothing — which is the failure mode that wastes the most time,
    // because "too weak to see" and "not drawing at all" are the same picture.
    // PER-GROUND WEIGHTS. Additive light on a dark scene saturates far faster
    // than ink on paper: the same numbers that made the light version readable
    // blew the dark one to flat white. The app's --scene-bg resolves to
    // #0b0e12 today, so the dark path is the one actually on screen -- and the
    // premise I built the light path on (a #f1f0eb panel, read out of ONE
    // theme block rather than from the resolved token) was wrong from the
    // start. Read the computed value, not a stylesheet line.
    float kg = uLight > 0.5 ? 1.0 : 0.38;
    float dRim   = rimD   * 1.55 * kg * uChan.x;
    float dSteep = steepD * 1.05 * kg * uChan.y;
    float dGrain = grainD * 0.55 * kg * uChan.z;
    float dSign  = signD  * 1.90 * kg * uChan.w;

    // EVERY CHANNEL IS A SATURATED COLOUR. None of them is grey, and none is
    // allowed near black.
    //
    // The rim ink was a neutral (0.20,0.19,0.17) and the other two were
    // desaturated blue-greys. Neutral dark over a warm paper is mud — that is
    // arithmetic, not taste — and with four channels layered the result was a
    // brown smear with no hue left to read. A dark neutral cannot encode
    // anything either: it is one number pretending to be a colour.
    //
    // So each channel gets its OWN hue, distinguishable at a glance, and the
    // sign channel keeps the theme's own diverging pair. If a channel is hard
    // to see, the answer is a different colour of light, never a darker one.
    // The rim is ACHROMATIC on purpose. Tinting it with the sign made every
    // ink in the picture warm whenever the first crossing happened to be
    // negative, so the whole volume read as one brown mass and the sign
    // channel had nothing left to say. One quantity, one channel.
    vec3 cRim   = uLight > 0.5 ? vec3(0.22, 0.21, 0.20) : vec3(1.0);
    vec3 cSteep = uLight > 0.5 ? vec3(0.16, 0.45, 0.74)     // cyan-blue
                               : vec3(0.62, 0.72, 0.95);
    vec3 cGrain = uLight > 0.5 ? vec3(0.52, 0.30, 0.70)     // violet
                               : vec3(0.80, 0.84, 0.95);

    // ── STYLES ─────────────────────────────────────────────────────────
    // Same four measured channels; different arguments about which of them
    // the reader should be looking at. These are not palettes: each style
    // reweights the CHANNELS, so switching style changes what the picture is
    // claiming, not just how it is coloured.
    if (uStyle == 1) {          // WASH — sign dominant, structure implied
        dRim *= 0.22; dSteep *= 0.55; dGrain = 0.0; dSign *= 1.45;
    } else if (uStyle == 2) {   // CONTOUR — the boundary, and little else
        dRim *= 1.65; dSteep *= 0.12; dGrain = 0.0; dSign *= 0.55;
    } else if (uStyle == 3) {   // BLUEPRINT — one ink, drafting-table
        dRim *= 1.25; dSteep *= 0.75; dGrain *= 0.45; dSign *= 0.30;
    }

    // ── FOUR LAYERS, COMPOSITED — NOT AVERAGED ─────────────────────────
    //
    // This was 'col = sum(colour_i * density_i) / sum(density_i)': a weighted
    // AVERAGE of four colours into one. That is not compositing, and it
    // destroys exactly what the four channels exist for — each channel's hue
    // is dissolved into a single mid-tone, so a rim, a gradient and a sign all
    // arrive as the same muddy grey-blue and the picture stops being separable.
    // The study version looked the way it did because its channels were
    // separate LIGHTS ADDED on top of one another, each still legible.
    //
    // Layered 'over' keeps that property on paper: sign lays down the broad
    // wash, steepness and grain sit on it, the rim draws last and thinnest so
    // the boundary stays crisp instead of being averaged into the fill.
    vec3 col = vec3(0.0); float a = 0.0;
    if (uStyle == 3) {
        // blueprint: one ink, so layering is a density question only
        vec3 base = vec3(0.13, 0.30, 0.52);
        float d = 1.0 - exp(-(dRim + dSteep + dGrain + dSign) * 2.2);
        col = base; a = d * 0.85;
    } else {
        OVER(signHue,  dSign  * 0.95);
        OVER(cSteep,   dSteep * 0.80);
        OVER(cGrain,   dGrain * 0.70);
        OVER(cRim,     dRim   * 0.90);
        a *= (uStyle == 2 ? 0.95 : 0.86);
    }
    if (a < 1e-3) { fragColor = vec4(0.0); return; }
    col /= max(a, 1e-4);
    float lum = dot(col, vec3(0.2126, 0.7152, 0.0722));
    if (uLight > 0.5 && lum < 0.22) col *= 0.22 / max(lum, 1e-4);
    fragColor = vec4(col * a, a);   // premultiplied, as the context expects
}`;

export interface OverlayChannels { shape: boolean; steepness: boolean; direction: boolean; sign: boolean; }

/** Longest texture edge. A pocket-scale cube can be far larger than anything a
 *  240 px viewport resolves, and a 3D texture costs memory in the cube of its
 *  edge — 160^3 R16F is already 8 MB. Downsampled volumes say so in the meta. */
const MAX_DIM = 160;

export class FieldVolumeOverlay {
    private plugin: PluginContext | null = null;
    private canvas: HTMLCanvasElement | null = null;
    private gl: WebGL2RenderingContext | null = null;
    private prog: WebGLProgram | null = null;
    private tex: WebGLTexture | null = null;
    private u: Record<string, WebGLUniformLocation | null> = {};
    private sub: { unsubscribe(): void } | null = null;
    private worldToTex = Mat4();
    private iso = 0; private scale = 1; private stepWorld = 0.25; private gradScale = 1;
    private neg: [number, number, number] = [0.855, 0.400, 0.416];
    private pos: [number, number, number] = [0.322, 0.478, 0.769];
    private chan: OverlayChannels = { shape: true, steepness: true, direction: true, sign: true };
    // -1, not 0 or 1: setSceneLuminance early-returns when the value is
    // unchanged, so any real initial value means the CSS blend mode is never
    // APPLIED — the state says "light scene" while the element still composites
    // normally, and an overlay that outputs alpha 1 everywhere then paints over
    // the entire scene. An initial value that no measurement can produce forces
    // the first call to do the work.
    private light = -1;
    private steps = 128;
    private style = 0;
    /** debug: paint the volume's box flat, to separate a geometry/compositing
     *  failure from an accumulation that is merely too faint to see. */
    private _probe = 0;
    get probe() { return this._probe; }
    /** requestDraw() was not enough: mol* coalesces and the counter showed the
     *  probe never rendered at all (drawsBefore 3 -> draws 3). Draw directly. */
    set probe(v: number | boolean) {
        this._probe = typeof v === 'number' ? v : (v ? 2 : 0);
        this.plugin?.canvas3d?.requestDraw();
        this.draw();
    }
    /** how many times draw() has actually run, and how many of those had a
     *  volume to draw. 'the canvas is empty' has two very different causes. */
    drawCount = 0;
    lastGlError: number | null = null;
    centrePixel: number[] | null = null;
    liveDrawCount = 0;
    private live = false;
    /** set when the volume was too big for MAX_DIM, so the panel can say so */
    downsampledFrom: number[] | null = null;
    lastError: string | null = null;

    get active() { return this.live; }

    mount(plugin: PluginContext): boolean {
        this.plugin = plugin;
        const host = plugin.canvas3d?.webgl?.gl?.canvas as HTMLCanvasElement | undefined;
        const parent = host?.parentElement;
        if (!host || !parent) { this.lastError = 'no mol* canvas to overlay'; return false; }
        if (this.canvas) return true;

        const c = document.createElement('canvas');
        c.className = 'dirac-field-overlay';
        Object.assign(c.style, {
            position: 'absolute', inset: '0', width: '100%', height: '100%',
            pointerEvents: 'none',
            // mol* gives its own canvas a stacking position, and at z-index 2
            // this overlay was painted UNDER it: fully visible in the DOM,
            // correct rect, correct parent, drawing every frame — and
            // contributing exactly zero pixels to the screen. Proven by
            // painting the entire canvas flat green and finding 1,072 green
            // pixels out of 985,504.
            zIndex: '40',
        } as CSSStyleDeclaration);
        if (getComputedStyle(parent).position === 'static') parent.style.position = 'relative';
        // Never composite normally, not even for one frame: this canvas is
        // opaque and would black out the scene until the first theme sync.
        c.dataset.ground = this.light === 0 ? 'dark' : 'light';

        parent.appendChild(c);

        const gl = c.getContext('webgl2', { alpha: true, premultipliedAlpha: true,
                                            antialias: false, depth: false,
                                            // so a harness can read the buffer back at all
                                            preserveDrawingBuffer: true });
        if (!gl) { c.remove(); this.lastError = 'WebGL2 unavailable'; return false; }
        this.canvas = c; this.gl = gl;

        const p = this.build(gl);
        if (!p) { this.unmount(); return false; }
        this.prog = p;

        const quad = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, quad);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
        gl.enableVertexAttribArray(0);
        gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

        // Draw straight after mol* draws, off its own signal. A private rAF
        // loop would render the field against a camera that had already moved,
        // and the lag reads as the field lagging the molecule.
        this.sub = plugin.canvas3d!.didDraw.subscribe(() => this.draw());
        return true;
    }

    private build(gl: WebGL2RenderingContext): WebGLProgram | null {
        const mk = (type: number, src: string) => {
            const s = gl.createShader(type)!;
            gl.shaderSource(s, src); gl.compileShader(s);
            if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
                this.lastError = gl.getShaderInfoLog(s) || 'shader compile failed';
                console.error('[field overlay]', this.lastError);
                return null;
            }
            return s;
        };
        const vs = mk(gl.VERTEX_SHADER, VERT), fs = mk(gl.FRAGMENT_SHADER, FRAG);
        if (!vs || !fs) return null;
        const p = gl.createProgram()!;
        gl.attachShader(p, vs); gl.attachShader(p, fs);
        gl.bindAttribLocation(p, 0, 'aPos');
        gl.linkProgram(p);
        if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
            this.lastError = gl.getProgramInfoLog(p) || 'link failed';
            console.error('[field overlay]', this.lastError);
            return null;
        }
        gl.useProgram(p);
        for (const n of ['uVol', 'uWorldToTex', 'uInvView', 'uEye', 'uRes', 'uTanHalfFov',
                         'uOrtho', 'uOrthoHeight', 'uIso', 'uScale', 'uGradScale',
                         'uStepWorld', 'uSteps', 'uChan', 'uLight', 'uNeg', 'uPos', 'uProbe', 'uStyle']) {
            this.u[n] = gl.getUniformLocation(p, n);
        }
        return p;
    }

    /**
     * Upload the volume mol* already built, rather than re-parsing the cube.
     * Re-deriving the box from the cube header means re-deriving the Bohr to
     * Angstrom conversion and the axis order, and a placement that is wrong by
     * one convention renders a plausible field in the wrong place — the worst
     * available failure, because it looks like chemistry.
     */
    setVolume(volume: Volume, isovalue: number, scale: number): boolean {
        const gl = this.gl;
        if (!gl) return false;
        const grid = volume.grid;
        const [nx, ny, nz] = grid.cells.space.dimensions as number[];
        const src = grid.cells.data;   // Tensor.Data — read it through the space, not as a raw array

        const big = Math.max(nx, ny, nz);
        const f = big > MAX_DIM ? MAX_DIM / big : 1;
        const dx = Math.max(2, Math.round(nx * f));
        const dy = Math.max(2, Math.round(ny * f));
        const dz = Math.max(2, Math.round(nz * f));
        this.downsampledFrom = f < 1 ? [nx, ny, nz] : null;

        // WebGL wants x fastest; mol*'s space is column-major over (x,y,z) with
        // its own strides, so ask the space for the offset instead of assuming.
        const get = grid.cells.space.get;
        const out = new Float32Array(dx * dy * dz);
        let w = 0;
        for (let k = 0; k < dz; k++) {
            const zk = f < 1 ? Math.min(nz - 1, Math.round(k / (dz - 1) * (nz - 1))) : k;
            for (let j = 0; j < dy; j++) {
                const yj = f < 1 ? Math.min(ny - 1, Math.round(j / (dy - 1) * (ny - 1))) : j;
                for (let i = 0; i < dx; i++) {
                    const xi = f < 1 ? Math.min(nx - 1, Math.round(i / (dx - 1) * (nx - 1))) : i;
                    out[w++] = get(src, xi, yj, zk);
                }
            }
        }

        if (this.tex) gl.deleteTexture(this.tex);
        const tex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_3D, tex);
        gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        for (const w2 of ['TEXTURE_WRAP_S', 'TEXTURE_WRAP_T', 'TEXTURE_WRAP_R'] as const) {
            gl.texParameteri(gl.TEXTURE_3D, gl[w2], gl.CLAMP_TO_EDGE);
        }
        gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
        // R16F, not R8. The app's own lesson: an 8-bit encoding needs a clamp,
        // a clamp needs a decision about the dynamic range, and a molecular
        // potential is singular at every nucleus — so the clamp would have to
        // be re-argued per molecule. A half float renders the real values.
        gl.texImage3D(gl.TEXTURE_3D, 0, gl.R16F, dx, dy, dz, 0, gl.RED, gl.FLOAT, out);
        const err = gl.getError();
        if (err !== gl.NO_ERROR) {
            this.lastError = `volume upload failed (gl ${err})`;
            gl.deleteTexture(tex); this.tex = null; this.live = false;
            return false;
        }
        this.tex = tex;

        // grid index -> world Angstrom, straight out of mol*
        const gridToWorld = Grid.getGridToCartesianTransform(grid);
        // texture coord -> grid index: scale by (n-1), because a texel CENTRE
        // at coord 0 is grid index 0 and at coord 1 is index n-1
        const texToGrid = Mat4.fromScaling(Mat4(), Vec3.create(dx - 1, dy - 1, dz - 1));
        // and we need the inverse of the whole chain, world -> texture
        const texToWorld = Mat4.mul(Mat4(), gridToWorld, texToGrid);
        Mat4.invert(this.worldToTex, texToWorld);

        // one march step, in Angstrom, from the actual voxel size
        const o = Vec3.transformMat4(Vec3(), Vec3.create(0, 0, 0), gridToWorld);
        const a = Vec3.transformMat4(Vec3(), Vec3.create(1, 0, 0), gridToWorld);
        this.stepWorld = Math.max(0.02, Vec3.distance(o, a));

        // MEASURE THE GRADIENT SCALE OFF THIS VOLUME. Dividing |grad V| by a
        // constant is the same defect this project has now hit three times: a
        // bare denominator is an unstated assumption about the UNITS of the
        // data, and it is wrong the moment the field kind, the basis, the grid
        // spacing or the molecule changes. Here it saturated the steepness
        // channel across the whole box, and under the light-ground multiply
        // that renders as near-black ink over the entire scene — the field did
        // not look mis-scaled, it looked like the app was broken.
        //
        // Strided so the cost is bounded: a 160^3 volume is 4M voxels and this
        // runs on every field load. p97, not max, because a potential is
        // singular at every nucleus and the max is set by one voxel.
        const idx = (x: number, y: number, z: number) => (z * dy + y) * dx + x;
        const stride = Math.max(1, Math.floor(Math.cbrt((dx * dy * dz) / 32768)));
        const mags: number[] = [];
        for (let z = 1; z < dz - 1; z += stride) {
            for (let y = 1; y < dy - 1; y += stride) {
                for (let x = 1; x < dx - 1; x += stride) {
                    const ex = (out[idx(x + 1, y, z)] - out[idx(x - 1, y, z)]) / 2;
                    const ey = (out[idx(x, y + 1, z)] - out[idx(x, y - 1, z)]) / 2;
                    const ez = (out[idx(x, y, z + 1)] - out[idx(x, y, z - 1)]) / 2;
                    mags.push(Math.hypot(ex, ey, ez));
                }
            }
        }
        mags.sort((a, b) => a - b);
        // per VOXEL above; the shader differences in texture coords, so convert
        const perTexel = mags.length ? mags[Math.floor(mags.length * 0.97)] : 0;
        this.gradScale = Math.max(perTexel * Math.max(dx, dy, dz), 1e-9);

        this.iso = isovalue;
        this.scale = Math.max(Math.abs(scale) || Math.abs(isovalue) * 3, 1e-6);
        this.live = true;
        this.lastError = null;
        this.plugin?.canvas3d?.requestDraw();
        return true;
    }

    setIso(isovalue: number, scale?: number) {
        this.iso = isovalue;
        if (scale) this.scale = Math.max(Math.abs(scale), 1e-6);
        this.plugin?.canvas3d?.requestDraw();
    }

    setChannels(c: Partial<OverlayChannels>) {
        this.chan = { ...this.chan, ...c };
        this.plugin?.canvas3d?.requestDraw();
    }

    getChannels(): OverlayChannels { return { ...this.chan }; }

    setColors(neg: [number, number, number], pos: [number, number, number]) {
        this.neg = neg; this.pos = pos;
        this.plugin?.canvas3d?.requestDraw();
    }

    /** the scene's own luminance decides additive vs subtractive */
    setSceneLuminance(lum: number) {
        const next = lum > 0.18 ? 1 : 0;
        if (next === this.light) return;
        this.light = next;
        if (this.canvas) this.canvas.dataset.ground = next ? 'light' : 'dark';
        this.plugin?.canvas3d?.requestDraw();
    }

    /** 0 ink · 1 wash · 2 contour · 3 blueprint. The dark-theme glow is not a
     *  style here: it is what every style becomes when the scene is dark, and
     *  it stays available because the dark theme is still being built. */
    setStyle(i: number) { this.style = i; this.plugin?.canvas3d?.requestDraw(); }
    getStyle() { return this.style; }

    setQuality(steps: number) {
        this.steps = Math.max(24, Math.min(384, Math.round(steps)));
        this.plugin?.canvas3d?.requestDraw();
    }

    clear() {
        this.live = false;
        if (this.gl && this.canvas) {
            this.gl.clearColor(0, 0, 0, 0);
            this.gl.clear(this.gl.COLOR_BUFFER_BIT);
        }
    }

    unmount() {
        this.sub?.unsubscribe(); this.sub = null;
        if (this.gl && this.tex) this.gl.deleteTexture(this.tex);
        this.canvas?.remove();
        this.canvas = null; this.gl = null; this.prog = null; this.tex = null;
        this.live = false;
    }

    draw() {
        this.drawCount++;
        const gl = this.gl, c = this.canvas, p = this.prog, plugin = this.plugin;
        if (!gl || !c || !p || !plugin?.canvas3d) return;
        const host = plugin.canvas3d.webgl.gl.canvas as HTMLCanvasElement;
        if (c.width !== host.width || c.height !== host.height) {
            c.width = host.width; c.height = host.height;
        }
        gl.viewport(0, 0, c.width, c.height);
        if (!this.live || !this.tex) {
            gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT); return;
        }

        this.liveDrawCount++;
        const cam = plugin.canvas3d.camera;
        const s = cam.state;
        const fwd = Vec3.sub(Vec3(), s.target, s.position);
        const dist = Vec3.magnitude(fwd);
        Vec3.normalize(fwd, fwd);
        const right = Vec3.normalize(Vec3(), Vec3.cross(Vec3(), fwd, s.up));
        const up = Vec3.cross(Vec3(), right, fwd);
        const ortho = s.mode === 'orthographic' ? 1 : 0;

        gl.useProgram(p);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_3D, this.tex);
        gl.uniform1i(this.u.uVol, 0);
        gl.uniformMatrix4fv(this.u.uWorldToTex, false, this.worldToTex as unknown as Float32Array);
        gl.uniformMatrix4fv(this.u.uInvView, false, new Float32Array([
            right[0], right[1], right[2], 0,
            up[0], up[1], up[2], 0,
            -fwd[0], -fwd[1], -fwd[2], 0,
            0, 0, 0, 1,
        ]));
        gl.uniform3f(this.u.uEye, s.position[0], s.position[1], s.position[2]);
        gl.uniform2f(this.u.uRes, c.width, c.height);
        gl.uniform1f(this.u.uTanHalfFov, Math.tan(s.fov * 0.5));
        gl.uniform1f(this.u.uOrtho, ortho);
        gl.uniform1f(this.u.uOrthoHeight, 2 * dist * Math.tan(s.fov * 0.5));
        gl.uniform1f(this.u.uIso, this.iso);
        gl.uniform1f(this.u.uScale, this.scale);
        gl.uniform1f(this.u.uGradScale, this.gradScale);
        gl.uniform1f(this.u.uStepWorld, this.stepWorld);
        gl.uniform1i(this.u.uSteps, this.steps);
        gl.uniform4f(this.u.uChan, this.chan.shape ? 1 : 0, this.chan.steepness ? 1 : 0,
                     this.chan.direction ? 1 : 0, this.chan.sign ? 1 : 0);
        gl.uniform1f(this.u.uLight, this.light);
        gl.uniform3fv(this.u.uNeg, this.neg);
        gl.uniform3fv(this.u.uPos, this.pos);
        gl.uniform1f(this.u.uProbe, this._probe);
        gl.uniform1i(this.u.uStyle, this.style);
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        // Report the pipeline's own verdict, once, instead of inferring it from
        // an empty screen — an empty screen has half a dozen causes and they
        // all look identical.
        if (this.lastGlError === null) {
            const e = gl.getError();
            this.lastGlError = e;
            const px = new Uint8Array(4);
            gl.readPixels((c.width / 2) | 0, (c.height / 2) | 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
            this.centrePixel = Array.from(px);
        }
    }
}

export const fieldOverlay = new FieldVolumeOverlay();
