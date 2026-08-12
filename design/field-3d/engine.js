/**
 * One WebGL2 context, thirty-five programs, N tiles.
 *
 * WHY ONE CONTEXT. Browsers cap live WebGL contexts at roughly sixteen and
 * silently kill the oldest when you pass it — so a page of 35 <canvas> elements
 * each holding its own context does not throw, it just starts blanking tiles
 * you already rendered, from the top. The failure looks like "some of my
 * shaders are broken" and it is not. So: one offscreen GL canvas does every
 * draw, and each tile is a plain 2D canvas that receives a drawImage() blit.
 * The volume texture is uploaded exactly once instead of 35 times.
 *
 * WHY ONE PROGRAM PER MODE, not one uber-shader with a switch. A switch over 35
 * branches compiles as one enormous program: every tile pays the register
 * pressure of the worst branch, and a syntax error anywhere blanks all 35 with
 * one message. Separate programs mean a broken mode fails alone and says which.
 */

const VERT = `#version 300 es
in vec2 aPos; out vec2 vUV;
void main() { vUV = aPos; gl_Position = vec4(aPos, 0.0, 1.0); }`;

/** Everything every mode can use. The modes below are pure `shade()` bodies. */
const PRELUDE = `#version 300 es
precision highp float; precision highp sampler3D;
in vec2 vUV; out vec4 fragColor;

uniform sampler3D uVol;      // R = MEP, linear in [-VMAX,+VMAX]; G = log10 rho
uniform vec3  uHalf;         // object-space half extents, longest axis = 0.5
uniform mat3  uRot;          // camera orbit
uniform float uDist;         // eye distance
uniform float uIso;          // display isovalue, fraction of VMAX  (0..1)
uniform float uRhoSurf;      // density surface, in the texture's own [0,1]
uniform float uTime;
uniform vec2  uRes;
uniform int   uSteps;

const vec3 NEG = vec3(0.855, 0.400, 0.416);   // the app's own diverging pair,
const vec3 POS = vec3(0.322, 0.478, 0.769);   // OKLCH chroma <= 0.106
const vec3 PAPER = vec3(0.961, 0.957, 0.945);
const vec3 INKY  = vec3(0.055, 0.059, 0.071);

/** MEP as a signed fraction of full scale. NOT gamma'd: a 3D language decides
 *  for itself what to do with the dynamic range, and several of them below make
 *  a different choice on purpose. That is the point of the page. */
/** The cube is where the calculation exists; outside it we know NOTHING, and a
 *  hard stop at the face draws that ignorance as a solid hexagonal object —
 *  every volume-integrating mode below rendered the bounding box as if the box
 *  were part of the molecule. This fades the outer 7% of each axis to zero. It
 *  is a DISPLAY WINDOW, exactly like the +-35 clamp, and it is stated on the
 *  page rather than left for the reader to mistake for physics. */
float boxWin(vec3 u) {
    vec3 d = min(u, 1.0 - u);
    vec3 w = smoothstep(vec3(0.0), vec3(0.07), d);
    return w.x * w.y * w.z;
}
float mep(vec3 p)  { return (texture(uVol, p).r * 2.0 - 1.0) * boxWin(p); }
float rho(vec3 p)  { return texture(uVol, p).g; }
/** how much of the value at p is REAL rather than window. Any mode that treats
 *  a small value as meaningful must gate on this: the window drives the field
 *  to zero at the faces, so "near zero" and "outside the data" become the same
 *  number, and the neutral-shell tile lit up the entire bounding box as though
 *  the box were a chemical feature. A display convenience became a claim. */
float known(vec3 p) { return boxWin(p); }
bool  inside(vec3 p) { return rho(p) >= uRhoSurf; }

vec3 gradMep(vec3 p) {
    float e = 0.008;
    return vec3(mep(p+vec3(e,0,0)) - mep(p-vec3(e,0,0)),
                mep(p+vec3(0,e,0)) - mep(p-vec3(0,e,0)),
                mep(p+vec3(0,0,e)) - mep(p-vec3(0,0,e))) / (2.0*e);
}
vec3 gradRho(vec3 p) {
    float e = 0.008;
    return vec3(rho(p+vec3(e,0,0)) - rho(p-vec3(e,0,0)),
                rho(p+vec3(0,e,0)) - rho(p-vec3(0,e,0)),
                rho(p+vec3(0,0,e)) - rho(p-vec3(0,0,e))) / (2.0*e);
}

/** signed power law, for the modes that want the 2D page's transfer */
float gam(float v, float g) { return sign(v) * pow(abs(v), g); }
vec3  ramp(float s) { return s < 0.0 ? mix(PAPER, NEG, -s) : mix(PAPER, POS, s); }
vec3  rampDark(float s) { return s < 0.0 ? mix(BG, NEG, -s) : mix(BG, POS, s); }

/** object point -> texture coords */
vec3 uvw(vec3 p) { return p / (2.0*uHalf) + 0.5; }

/** slab test against the box */
bool hitBox(vec3 ro, vec3 rd, out float t0, out float t1) {
    vec3 inv = 1.0 / rd;
    vec3 a = (-uHalf - ro) * inv, b = (uHalf - ro) * inv;
    vec3 lo = min(a,b), hi = max(a,b);
    t0 = max(max(lo.x, lo.y), lo.z);
    t1 = min(min(hi.x, hi.y), hi.z);
    return t1 > max(t0, 0.0);
}

float hash(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
float hash2(vec2 p){ return fract(sin(dot(p, vec2(41.3, 289.1))) * 24634.6345); }

vec3 lambert(vec3 nrm, vec3 base) {
    vec3 L = normalize(vec3(-0.5, 0.75, 0.55));
    float d = max(0.0, dot(nrm, L));
    float rim = pow(1.0 - abs(nrm.z), 2.5);
    return base * (0.30 + 0.78*d) + vec3(0.14)*rim;
}

vec4 shade(vec3 ro, vec3 rd, float t0, float t1);

void main() {
    vec2 uv = vUV;
    uv.x *= uRes.x / uRes.y;
    vec3 rd = normalize(vec3(uv, -1.85));
    vec3 ro = vec3(0.0, 0.0, uDist);
    ro = uRot * ro; rd = uRot * rd;
    float t0, t1;
    if (!hitBox(ro, rd, t0, t1)) { fragColor = vec4(BG, 1.0); return; }
    t0 = max(t0, 0.0);
    fragColor = shade(ro, rd, t0, t1);
}
`;

/** the molecular skeleton, drawn by the CPU into the 2D tile after the blit —
 *  cheaper and crisper than putting 21 spheres through every raymarcher. */
function projectAtoms(data, rot, dist, S) {
    const h = data._half, out = [];
    const R = rot;                                  // column-major 3x3, as in GL
    for (const a of data.atoms) {
        const o = [(a.p[0]-0.5)*2*h[0], (a.p[1]-0.5)*2*h[1], (a.p[2]-0.5)*2*h[2]];
        // inverse of the camera rotation: R is orthonormal, so transpose
        const c = [R[0]*o[0]+R[1]*o[1]+R[2]*o[2],
                   R[3]*o[0]+R[4]*o[1]+R[5]*o[2],
                   R[6]*o[0]+R[7]*o[1]+R[8]*o[2]];
        const z = dist - c[2];
        if (z <= 0.02) { out.push(null); continue; }
        const f = 1.85 / z;
        out.push({ x: (c[0]*f*0.5 + 0.5) * S, y: (0.5 - c[1]*f*0.5) * S, z, e: a.e });
    }
    return out;
}

class FieldEngine {
    /** async because the volume arrives deflated and the only inflater that is
     *  actually present in the browser — DecompressionStream — is a stream.
     *  The alternative was shipping a copy of pako or 683 KB of raw base64;
     *  a design study should not carry a decompression library to look at a
     *  molecule. */
    static async create(data, size) {
        const raw = Uint8Array.from(atob(data.tex), ch => ch.charCodeAt(0));
        const ds = new DecompressionStream('deflate');
        const buf = await new Response(new Blob([raw]).stream().pipeThrough(ds)).arrayBuffer();
        return new FieldEngine(data, size, new Uint8Array(buf));
    }

    constructor(data, size, bytes) {
        this.data = data;
        this.size = size;
        const c = document.createElement('canvas');
        c.width = c.height = size;
        const gl = c.getContext('webgl2', { antialias: false, preserveDrawingBuffer: true,
                                            powerPreference: 'high-performance' });
        if (!gl) throw new Error('WebGL2 unavailable');
        this.canvas = c; this.gl = gl;
        this.programs = new Map();
        this.failed = new Map();

        const quad = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, quad);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 3,-1, -1,3]), gl.STATIC_DRAW);
        this.quad = quad;

        // ── volume upload, once ──────────────────────────────────────────
        const N = data.n;
        if (bytes.length !== N*N*N*2)
            throw new Error(`volume is ${bytes.length} bytes, expected ${N*N*N*2}`);
        const tex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_3D, tex);
        gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        for (const p of ['TEXTURE_WRAP_S','TEXTURE_WRAP_T','TEXTURE_WRAP_R'])
            gl.texParameteri(gl.TEXTURE_3D, gl[p], gl.CLAMP_TO_EDGE);
        gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
        gl.texImage3D(gl.TEXTURE_3D, 0, gl.RG8, N, N, N, 0, gl.RG, gl.UNSIGNED_BYTE, bytes);
        this.tex = tex;

        const b = data.boxA, m = Math.max(b[0], b[1], b[2]);
        data._half = [b[0]/m*0.5, b[1]/m*0.5, b[2]/m*0.5];
        this.half = data._half;

        // FRAME THE MOLECULE, NOT THE BOX. The cube is padded well past the
        // atoms so the potential has room to decay; framing its diagonal put
        // the specimen in the middle third of every tile and left the rest as
        // background. Third time in this repo that the crop, not the drawing,
        // was the defect. Radius is taken over the ATOMS plus the margin the
        // field actually occupies outside them.
        let r = 0;
        for (const a of data.atoms) {
            const o = [(a.p[0]-0.5)*2*this.half[0], (a.p[1]-0.5)*2*this.half[1],
                       (a.p[2]-0.5)*2*this.half[2]];
            r = Math.max(r, Math.hypot(o[0], o[1], o[2]));
        }
        data._rad = r + 0.085;                 // the field's reach past the nuclei
        data._dist = data._rad * 1.85 / 0.86;  // fill ~86% of the frame height
    }

    program(mode, body, bg) {
        if (this.failed.has(mode)) return null;
        let p = this.programs.get(mode);
        if (p) return p;
        const gl = this.gl;
        const src = PRELUDE.replace('precision highp float;', `precision highp float;\nconst vec3 BG = ${bg};`)
                  + '\n' + body;
        const vs = this.compile(gl.VERTEX_SHADER, VERT);
        const fs = this.compile(gl.FRAGMENT_SHADER, src);
        if (!vs || !fs) { this.failed.set(mode, 'compile'); return null; }
        p = gl.createProgram();
        gl.attachShader(p, vs); gl.attachShader(p, fs);
        gl.bindAttribLocation(p, 0, 'aPos');
        gl.linkProgram(p);
        if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
            console.error(`[${mode}] link:`, gl.getProgramInfoLog(p));
            this.failed.set(mode, gl.getProgramInfoLog(p)); return null;
        }
        p._u = {};
        for (const u of ['uVol','uHalf','uRot','uDist','uIso','uRhoSurf','uTime','uRes','uSteps'])
            p._u[u] = gl.getUniformLocation(p, u);
        this.programs.set(mode, p);
        return p;
    }

    compile(type, src) {
        const gl = this.gl, s = gl.createShader(type);
        gl.shaderSource(s, src); gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
            const log = gl.getShaderInfoLog(s);
            const bad = /ERROR: 0:(\d+)/.exec(log);
            const line = bad ? src.split('\n')[+bad[1]-1] : '';
            console.error('shader:', log, line ? '\n  >> ' + line.trim() : '');
            return null;
        }
        return s;
    }

    render(mode, body, bg, rot, dist, iso, steps, t) {
        const gl = this.gl, p = this.program(mode, body, bg);
        if (!p) return false;
        gl.viewport(0, 0, this.size, this.size);
        gl.useProgram(p);
        gl.bindBuffer(gl.ARRAY_BUFFER, this.quad);
        gl.enableVertexAttribArray(0);
        gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_3D, this.tex);
        gl.uniform1i(p._u.uVol, 0);
        gl.uniform3fv(p._u.uHalf, this.half);
        gl.uniformMatrix3fv(p._u.uRot, false, rot);
        gl.uniform1f(p._u.uDist, dist);
        gl.uniform1f(p._u.uIso, iso);
        gl.uniform1f(p._u.uRhoSurf, this.data.rhoSurfByte / 255);
        gl.uniform1f(p._u.uTime, t);
        gl.uniform2f(p._u.uRes, this.size, this.size);
        gl.uniform1i(p._u.uSteps, steps);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        return true;
    }
}

/** Orbit matrix, column-major for GL. */
function orbit(yaw, pitch) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    return new Float32Array([
        cy, sy*sp, -sy*cp,
        0,  cp,     sp,
        sy, -cy*sp, cy*cp,
    ]);
}

window.FieldEngine = FieldEngine;
window.orbit = orbit;
window.projectAtoms = projectAtoms;
