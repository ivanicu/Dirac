/**
 * Thirty-five volumetric languages for one molecular field.
 *
 * Each entry is a GLSL `shade()` body, appended to the prelude in engine.js.
 * They all march the SAME real volume — aspirin's RHF/STO-3G electrostatic
 * potential out of PySCF, with the self-consistent electron density on the
 * second channel so the molecular surface is available to any mode that wants
 * a surface to put something on.
 *
 * THE 2D PAGE ASKED WHAT MARK TO USE. THIS ONE ASKS A HARDER QUESTION, because
 * in three dimensions every language must also answer WHAT TO OCCLUDE. A slice
 * shows one plane and hides the rest. An isosurface shows one level and hides
 * both sides of it. A volume integral shows everything and resolves nothing.
 * There is no 3D display of a scalar field that does not throw something away —
 * so the honest comparison is not "which is prettiest" but "which loss can this
 * particular reader afford".
 */

const M = [];
const bg = { paper: 'vec3(0.945,0.941,0.929)', dark: 'vec3(0.078,0.075,0.067)',
             ink: 'vec3(0.043,0.047,0.055)', blue: 'vec3(0.086,0.243,0.404)',
             grey: 'vec3(0.541,0.541,0.525)', green: 'vec3(0.016,0.071,0.047)' };

// ══ A · ISOSURFACE — the incumbent, and what it costs ═══════════════════════

M.push({ k:'solidIso', fam:'A', t:'Solid isosurface pair', bg:bg.paper,
 d:'Two opaque lobes at ±iso. The default in every molecular package.',
 c:'Cost: hides its own interior AND everything behind it. Two numbers survive of a whole volume.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    float prev = 0.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 p = ro + rd * (t0 + dt*float(i));
        float v = mep(uvw(p));
        if (!first && abs(v) > uIso && abs(prev) <= uIso) {
            vec3 n = normalize(-gradMep(uvw(p)) * sign(v));
            return vec4(lambert(n, v < 0.0 ? NEG : POS), 1.0);
        }
        prev = v; first = false;
    }
    return vec4(BG, 1.0);
 }` });

M.push({ k:'nestedShells', fam:'A', t:'Nested transparent shells', bg:bg.paper,
 d:'Three levels per sign, each translucent, so magnitude becomes depth of stacking.',
 c:'Cost: transparency is order-dependent and the eye cannot count more than about three.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    vec3 acc = vec3(0.0); float a = 0.0, prev = 0.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps || a > 0.97) break;
        vec3 p = ro + rd * (t0 + dt*float(i));
        float v = mep(uvw(p));
        if (!first) {
            for (int L = 1; L <= 3; L++) {
                float lev = uIso * float(L) * 0.75;
                if ((abs(v) > lev) != (abs(prev) > lev)) {
                    vec3 n = normalize(-gradMep(uvw(p)) * sign(v));
                    vec3 col = lambert(n, v < 0.0 ? NEG : POS);
                    float sa = 0.16 + 0.10*float(L);
                    acc += col * sa * (1.0 - a); a += sa * (1.0 - a);
                }
            }
        }
        prev = v; first = false;
    }
    return vec4(acc + BG*(1.0-a), 1.0);
 }` });

M.push({ k:'wireIso', fam:'A', t:'Wireframe isosurface', bg:bg.paper,
 d:'The same surface as a lattice, so what is behind it survives.',
 c:'Cost: the mesh spacing is an arbitrary choice that reads as structure.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    vec3 acc = vec3(0.0); float a = 0.0, prev = 0.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps || a > 0.95) break;
        vec3 p = ro + rd * (t0 + dt*float(i));
        vec3 u = uvw(p); float v = mep(u);
        if (!first && abs(v) > uIso && abs(prev) <= uIso) {
            vec3 g = fract(u * 15.0);
            vec3 dd = min(g, 1.0-g);
            float line = 1.0 - smoothstep(0.0, 0.055, min(min(dd.x,dd.y),dd.z));
            if (line > 0.01) {
                vec3 n = normalize(-gradMep(u) * sign(v));
                vec3 col = lambert(n, v < 0.0 ? NEG : POS) * 0.85;
                float sa = line * 0.85;
                acc += col*sa*(1.0-a); a += sa*(1.0-a);
            }
        }
        prev = v; first = false;
    }
    return vec4(acc + BG*(1.0-a), 1.0);
 }` });

M.push({ k:'stippleIso', fam:'A', t:'Stippled surface', bg:bg.paper,
 d:'The surface exists only as a scatter of points; density carries how far past the level it is.',
 c:'Cost: reads as a cloud, not a boundary — the shape is inferred, never stated.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    vec3 acc = vec3(0.0); float a = 0.0, prev = 0.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps || a > 0.95) break;
        vec3 p = ro + rd * (t0 + dt*float(i));
        vec3 u = uvw(p); float v = mep(u);
        if (!first && abs(v) > uIso && abs(prev) <= uIso) {
            float keep = smoothstep(uIso, uIso*2.2, abs(v));
            if (hash(floor(u*58.0)) < 0.20 + keep*0.60) {
                vec3 n = normalize(-gradMep(u) * sign(v));
                vec3 col = lambert(n, v < 0.0 ? NEG : POS);
                acc += col*0.9*(1.0-a); a += 0.9*(1.0-a);
            }
        }
        prev = v; first = false;
    }
    return vec4(acc + BG*(1.0-a), 1.0);
 }` });

M.push({ k:'rimIso', fam:'A', t:'Silhouette only', bg:bg.dark,
 d:'The surface is drawn nowhere except where it turns away from the eye.',
 c:'Cost: pure outline. Tells you the shape and refuses to tell you the value.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    vec3 acc = vec3(0.0); float prev = 0.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 p = ro + rd * (t0 + dt*float(i));
        vec3 u = uvw(p); float v = mep(u);
        if (!first && abs(v) > uIso && abs(prev) <= uIso) {
            vec3 n = normalize(-gradMep(u) * sign(v));
            float rim = pow(1.0 - abs(dot(n, rd)), 3.0);
            acc += (v < 0.0 ? NEG : POS) * rim * 1.5;
        }
        prev = v; first = false;
    }
    return vec4(BG + acc, 1.0);
 }` });

// ══ B · VOLUME — no surface at all ══════════════════════════════════════════

M.push({ k:'emissive', fam:'B', t:'Emissive raymarch', bg:bg.dark,
 d:'Every voxel emits; the image is the integral along the ray. No level is chosen.',
 c:'Cost: a long weak region and a short strong one are indistinguishable.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    vec3 acc = vec3(0.0);
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 p = ro + rd * (t0 + dt*float(i));
        float v = gam(mep(uvw(p)), 0.62);
        acc += (v < 0.0 ? NEG : POS) * abs(v) * dt * 2.6;
    }
    return vec4(BG + acc, 1.0);
 }` });

M.push({ k:'xray', fam:'B', t:'Absorption (X-ray)', bg:bg.paper,
 d:'Beer–Lambert: the field absorbs light. Dense regions read as shadow, as a radiograph does.',
 c:'Cost: sign has to be thrown away — you cannot absorb a negative amount.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    float tau = 0.0;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 p = ro + rd * (t0 + dt*float(i));
        tau += abs(gam(mep(uvw(p)), 0.55)) * dt * 5.5;
    }
    float tr = exp(-tau);
    return vec4(mix(vec3(0.10,0.10,0.11), BG, tr), 1.0);
 }` });

M.push({ k:'mip', fam:'B', t:'Maximum intensity', bg:bg.dark,
 d:'Keep only the single largest |value| along each ray. The angiographer’s projection.',
 c:'Cost: destroys depth completely — one voxel speaks for the whole ray.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    float best = 0.0;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        float v = mep(uvw(ro + rd*(t0+dt*float(i))));
        if (abs(v) > abs(best)) best = v;
    }
    return vec4(rampDark(gam(best, 0.65)), 1.0);
 }` });

M.push({ k:'signedMip', fam:'B', t:'Two-sided maximum', bg:bg.paper,
 d:'The strongest positive AND the strongest negative on the ray, composited.',
 c:'Cost: honest that both exist, silent about which is nearer.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    float hi = 0.0, lo = 0.0;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        float v = mep(uvw(ro + rd*(t0+dt*float(i))));
        hi = max(hi, v); lo = min(lo, v);
    }
    vec3 c = BG;
    c = mix(c, POS, gam(hi,0.6)*0.95);
    c = mix(c, NEG, gam(-lo,0.6)*0.95);
    return vec4(c, 1.0);
 }` });

M.push({ k:'firstHit', fam:'B', t:'First crossing, depth-coded', bg:bg.paper,
 d:'Where the ray first passes the level, tinted by how far away that happened.',
 c:'Cost: an aerial view of a landscape — you see the top and never the relief under it.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        float t = t0 + dt*float(i);
        float v = mep(uvw(ro + rd*t));
        if (abs(v) > uIso) {
            float depth = clamp((t - t0) / max(t1-t0, 1e-4), 0.0, 1.0);
            vec3 base = v < 0.0 ? NEG : POS;
            return vec4(mix(base, BG, depth*0.72), 1.0);
        }
    }
    return vec4(BG, 1.0);
 }` });

// ══ C · ON THE MOLECULAR SURFACE — what chemists actually use ═══════════════

M.push({ k:'mepOnDensity', fam:'C', t:'MEP on the density surface', bg:bg.paper,
 d:'THE standard picture: colour the 0.001 a.u. electron-density isosurface by the potential on it.',
 c:'Cost: none that a chemist minds. This is the incumbent for a reason — the surface is where binding happens.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    float prev = -1.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 p = ro + rd*(t0+dt*float(i));
        vec3 u = uvw(p); float r = rho(u);
        if (!first && r > uRhoSurf && prev <= uRhoSurf) {
            vec3 n = normalize(-gradRho(u));
            float s = clamp(mep(u)/0.60, -1.0, 1.0);
            return vec4(lambert(n, ramp(s)), 1.0);
        }
        prev = r; first = false;
    }
    return vec4(BG, 1.0);
 }` });

M.push({ k:'surfContour', fam:'C', t:'Contoured surface', bg:bg.paper,
 d:'The same surface, but the potential is quantised into bands with drawn edges.',
 c:'Cost: invents a boundary at every band edge; readers treat the edge as a fact.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    float prev = -1.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 p = ro + rd*(t0+dt*float(i));
        vec3 u = uvw(p); float r = rho(u);
        if (!first && r > uRhoSurf && prev <= uRhoSurf) {
            vec3 n = normalize(-gradRho(u));
            float s = clamp(mep(u)/0.60, -1.0, 1.0);
            float q = floor(s * 6.0) / 6.0;
            float edge = smoothstep(0.0, 0.055, abs(fract(s*6.0) - 0.5)*2.0 - 0.86);
            vec3 col = lambert(n, ramp(q + 0.083));
            return vec4(mix(col, vec3(0.16,0.15,0.14), edge*0.85), 1.0);
        }
        prev = r; first = false;
    }
    return vec4(BG, 1.0);
 }` });

M.push({ k:'surfRelief', fam:'C', t:'Surface displaced by potential', bg:bg.paper,
 d:'The density surface is pushed out where the potential is positive, pulled in where negative.',
 c:'Cost: geometry now lies. A chemist reading shape reads the wrong molecule.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    float prev = -1.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 p = ro + rd*(t0+dt*float(i));
        vec3 u = uvw(p);
        float r = rho(u) + mep(u) * 0.055;
        if (!first && r > uRhoSurf && prev <= uRhoSurf) {
            vec3 n = normalize(-gradRho(u) - gradMep(u)*0.055);
            float s = clamp(mep(u)/0.60, -1.0, 1.0);
            return vec4(lambert(n, mix(vec3(0.80,0.78,0.74), ramp(s), 0.55)), 1.0);
        }
        prev = r; first = false;
    }
    return vec4(BG, 1.0);
 }` });

M.push({ k:'surfStipple', fam:'C', t:'Stippled surface', bg:bg.paper,
 d:'One ink. The potential on the surface is carried by dot density, as an engraving would.',
 c:'Cost: sign needs a second channel — here, dot SIZE against dot COUNT.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    float prev = -1.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 p = ro + rd*(t0+dt*float(i));
        vec3 u = uvw(p); float r = rho(u);
        if (!first && r > uRhoSurf && prev <= uRhoSurf) {
            vec3 n = normalize(-gradRho(u));
            float s = mep(u);
            float dens = smoothstep(0.05, 0.85, abs(s)) * 0.80;
            float cell = hash(floor(u * (s < 0.0 ? 62.0 : 40.0)));
            float ink = cell < dens ? 1.0 : 0.0;
            float sh = 0.35 + 0.65*max(0.0, dot(n, normalize(vec3(-0.5,0.75,0.55))));
            return vec4(mix(BG, vec3(0.11,0.11,0.10), ink*sh), 1.0);
        }
        prev = r; first = false;
    }
    return vec4(BG, 1.0);
 }` });

M.push({ k:'surfBinary', fam:'C', t:'Surface, one decision', bg:bg.paper,
 d:'The surface painted in exactly three states: donor-favourable, acceptor-favourable, neither.',
 c:'Cost: throws the gradient away — which is the point, and is why it survives a printer.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    float prev = -1.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 p = ro + rd*(t0+dt*float(i));
        vec3 u = uvw(p); float r = rho(u);
        if (!first && r > uRhoSurf && prev <= uRhoSurf) {
            vec3 n = normalize(-gradRho(u));
            float s = mep(u);
            vec3 c = vec3(0.86,0.85,0.82);
            if (s >  0.30) c = POS;
            if (s < -0.30) c = NEG;
            return vec4(lambert(n, c), 1.0);
        }
        prev = r; first = false;
    }
    return vec4(BG, 1.0);
 }` });

// ══ D · DISCRETE IN 3D ══════════════════════════════════════════════════════

M.push({ k:'pointCloud', fam:'D', t:'Point cloud', bg:bg.dark,
 d:'Particles seeded with probability |value|. The field as a countable population.',
 c:'Cost: stochastic — the same data drawn twice is not the same picture.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    vec3 acc = vec3(0.0);
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 u = uvw(ro+rd*(t0+dt*float(i)));
        float v = mep(u);
        vec3 cell = floor(u * 46.0);
        if (hash(cell) < abs(v)*1.5) {
            vec3 c = fract(u*46.0) - 0.5;
            float d = 1.0 - smoothstep(0.12, 0.38, length(c));
            acc += (v<0.0?NEG:POS) * d * abs(v) * 0.42;
        }
    }
    return vec4(BG + acc, 1.0);
 }` });

M.push({ k:'voxelBlocks', fam:'D', t:'Voxel blocks', bg:bg.paper,
 d:'The grid drawn as the grid: opaque cubes on a coarse lattice, no interpolation pretended.',
 c:'Cost: brutal about resolution, and the only tile here that does not flatter the data.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    float prev = 0.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 p = ro + rd*(t0+dt*float(i));
        vec3 u = uvw(p);
        vec3 q = (floor(u*22.0) + 0.5) / 22.0;
        float v = mep(q);
        if (!first && abs(v) > uIso && abs(prev) <= uIso) {
            vec3 f = fract(u*22.0) - 0.5;
            vec3 a = abs(f);
            float mx = max(max(a.x,a.y),a.z);
            vec3 n = normalize(-gradMep(q)*sign(v));
            float face = 0.72 + 0.28*step(0.40, mx);
            return vec4(lambert(n, (v<0.0?NEG:POS)) * face, 1.0);
        }
        prev = v; first = false;
    }
    return vec4(BG, 1.0);
 }` });

M.push({ k:'glyphLattice', fam:'D', t:'Glyph lattice', bg:bg.paper,
 d:'A regular lattice of discs, each sized by the value at its own node.',
 c:'Cost: the lattice is not in the data. Regularity reads as a property of the molecule.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    vec3 acc = vec3(0.0); float a = 0.0;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps || a > 0.96) break;
        vec3 p = ro + rd*(t0+dt*float(i));
        vec3 u = uvw(p);
        vec3 node = (floor(u*15.0) + 0.5)/15.0;
        float v = mep(node);
        float rad = abs(v)*0.42;
        float d = length((u - node)*15.0);
        if (d < rad) {
            float sa = 0.30;
            acc += (v<0.0?NEG:POS)*sa*(1.0-a); a += sa*(1.0-a);
        }
    }
    return vec4(acc + BG*(1.0-a), 1.0);
 }` });

M.push({ k:'dotShells', fam:'D', t:'Dotted shells', bg:bg.paper,
 d:'Three levels, each as a perforated shell, so all three are simultaneously visible.',
 c:'Cost: which shell a dot belongs to is guessed from its colour, not seen.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    vec3 acc = vec3(0.0); float a = 0.0, prev = 0.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps || a > 0.95) break;
        vec3 p = ro + rd*(t0+dt*float(i));
        vec3 u = uvw(p); float v = mep(u);
        if (!first) {
            for (int L = 1; L <= 3; L++) {
                float lev = uIso*float(L)*0.8;
                if ((abs(v) > lev) != (abs(prev) > lev)) {
                    if (hash(floor(u*(40.0+18.0*float(L)))) < 0.42) {
                        vec3 n = normalize(-gradMep(u)*sign(v));
                        vec3 col = lambert(n, v<0.0?NEG:POS) * (0.55 + 0.15*float(L));
                        acc += col*0.75*(1.0-a); a += 0.75*(1.0-a);
                    }
                }
            }
        }
        prev = v; first = false;
    }
    return vec4(acc + BG*(1.0-a), 1.0);
 }` });

M.push({ k:'halftone3D', fam:'D', t:'Screen-space halftone', bg:bg.paper,
 d:'A volume integral pushed through a print screen fixed to the VIEWPORT, not the molecule.',
 c:'Cost: the screen does not rotate with the object, so texture is not shape. Deliberately.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    float sum = 0.0;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        sum += abs(gam(mep(uvw(ro+rd*(t0+dt*float(i)))), 0.6)) * dt * 1.7;
    }
    sum = clamp(sum, 0.0, 1.0);
    vec2 sc = gl_FragCoord.xy;
    float ang = 0.4;
    vec2 rt = vec2(sc.x*cos(ang)-sc.y*sin(ang), sc.x*sin(ang)+sc.y*cos(ang));
    vec2 cell = fract(rt/5.0) - 0.5;
    float dot_ = 1.0 - smoothstep(0.0, 0.06, length(cell) - sum*0.52);
    return vec4(mix(BG, vec3(0.10,0.10,0.10), dot_), 1.0);
 }` });

// ══ E · FLOW — the gradient nobody draws ════════════════════════════════════

M.push({ k:'gradMagVol', fam:'E', t:'Gradient magnitude', bg:bg.dark,
 d:'|∇V| integrated along the ray: where the field is STEEP, not where it is strong.',
 c:'Cost: sign is gone. A wall, not a slope — but walls are what a ligand feels.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0) / float(uSteps);
    float sum = 0.0;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 u = uvw(ro+rd*(t0+dt*float(i)));
        if (rho(u) > uRhoSurf) continue;
        sum += length(gradMep(u)) * dt * 0.30;
    }
    sum = clamp(sum, 0.0, 1.0);
    return vec4(BG + vec3(0.62,0.72,0.95)*pow(sum,0.75)*1.35, 1.0);
 }` });

M.push({ k:'fieldLines', fam:'E', t:'Field lines', bg:bg.paper,
 d:'Paths walked down ∇V from a seed lattice — the route a probe charge actually takes.',
 c:'Cost: the seeds are invented, and their density reads as importance.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    vec3 acc = vec3(0.0); float a = 0.0;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps || a > 0.95) break;
        vec3 u = uvw(ro+rd*(t0+dt*float(i)));
        // lines live in SOLVENT, where a probe charge would actually travel.
        // Without this the tile filled the whole box with confetti, because
        // grad V is nonzero everywhere including deep inside the atoms.
        if (rho(u) > uRhoSurf) continue;
        vec3 g = gradMep(u); float gm = length(g);
        if (gm < 0.35) continue;
        vec3 dir = g/gm;
        vec3 perp = u - dir*dot(u, dir);
        vec3 e1 = normalize(cross(dir, vec3(0.0,0.0,1.0) + 1e-3));
        vec3 e2 = normalize(cross(dir, e1));
        vec2 cell = fract(vec2(dot(perp,e1), dot(perp,e2))*20.0) - 0.5;
        float line = 1.0 - smoothstep(0.06, 0.20, length(cell));
        if (line > 0.01) {
            float v = mep(u);
            float sa = line * clamp((gm-0.35)*0.55, 0.0, 0.60);
            acc += (v<0.0?NEG:POS)*sa*(1.0-a); a += sa*(1.0-a);
        }
    }
    return vec4(acc + BG*(1.0-a), 1.0);
 }` });

M.push({ k:'gradHairs', fam:'E', t:'Gradient hairs on the surface', bg:bg.paper,
 d:'Short segments on the density surface, aligned with ∇V and length-coded by |∇V|.',
 c:'Cost: only on the surface — the direction the field points INTO solvent is lost.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    float prev = -1.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 u = uvw(ro+rd*(t0+dt*float(i)));
        float r = rho(u);
        if (!first && r > uRhoSurf && prev <= uRhoSurf) {
            vec3 n = normalize(-gradRho(u));
            vec3 g = gradMep(u);
            vec3 tang = g - n*dot(g,n);
            float tm = length(tang);
            vec3 base = lambert(n, vec3(0.88,0.87,0.84));
            if (tm > 1e-4) {
                vec3 T = tang/tm;
                vec3 B = normalize(cross(n, T));
                vec3 local = u*52.0;
                float along = dot(local, T), across = dot(local, B);
                float hair = (1.0 - smoothstep(0.0, 0.22, abs(fract(across)-0.5)))
                           * step(fract(along*0.55), clamp(tm*0.55, 0.12, 0.9));
                base = mix(base, vec3(0.15,0.15,0.17), hair*0.85);
            }
            return vec4(base, 1.0);
        }
        prev = r; first = false;
    }
    return vec4(BG, 1.0);
 }` });

M.push({ k:'lic3d', fam:'E', t:'Volumetric LIC', bg:bg.dark,
 d:'Noise smeared along ∇V and integrated — the texture’s grain IS the direction of pull.',
 c:'Cost: gorgeous, and almost impossible to read a magnitude out of.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    vec3 acc = vec3(0.0);
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 u = uvw(ro+rd*(t0+dt*float(i)));
        vec3 g = gradMep(u); float gm = length(g);
        if (gm < 0.04) continue;
        vec3 dir = g/gm;
        float s = 0.0;
        for (int k = -3; k <= 3; k++) {
            vec3 q = u + dir*float(k)*0.011;
            s += hash(floor(q*150.0));
        }
        s /= 7.0;
        float v = mep(u);
        acc += (v<0.0?NEG:POS) * pow(s, 3.0) * clamp(gm*0.5,0.0,1.0) * dt * 3.4;
    }
    return vec4(BG + acc, 1.0);
 }` });

// ══ F · SLICES — 2D, honestly labelled as such ══════════════════════════════

M.push({ k:'triSlice', fam:'F', t:'Three orthogonal planes', bg:bg.paper,
 d:'The radiologist’s answer. Three exact readings, and an explicit refusal to guess between them.',
 c:'Cost: 3 planes out of 64. The other 61 are asserted to be uninteresting.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float best = 1e9; vec3 col = BG; bool got = false;
    for (int ax = 0; ax < 3; ax++) {
        float o = ax==0?ro.x:(ax==1?ro.y:ro.z);
        float d = ax==0?rd.x:(ax==1?rd.y:rd.z);
        if (abs(d) < 1e-5) continue;
        float t = -o/d;
        if (t < t0 || t > t1 || t > best) continue;
        vec3 p = ro + rd*t; vec3 u = uvw(p);
        if (any(lessThan(u, vec3(0.0))) || any(greaterThan(u, vec3(1.0)))) continue;
        best = t; got = true;
        col = ramp(gam(mep(u), 0.55));
        col *= 0.86 + 0.14*float(ax);
    }
    return vec4(got ? col : BG, 1.0);
 }` });

M.push({ k:'sliceStack', fam:'F', t:'Stack of translucent slices', bg:bg.dark,
 d:'Sixteen parallel planes, each partly transparent — the volume as a physical deck of cards.',
 c:'Cost: the stacking axis is privileged and the picture changes when you turn it.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    vec3 acc = vec3(0.0); float a = 0.0;
    for (int k = 0; k < 16; k++) {
        float zp = (float(k)+0.5)/16.0;
        float zo = (zp - 0.5)*2.0*uHalf.z;
        if (abs(rd.z) < 1e-5) continue;
        float t = (zo - ro.z)/rd.z;
        if (t < t0 || t > t1) continue;
        vec3 u = uvw(ro + rd*t);
        if (any(lessThan(u.xy, vec2(0.0))) || any(greaterThan(u.xy, vec2(1.0)))) continue;
        float v = gam(mep(u), 0.55);
        float sa = abs(v)*0.42;
        acc += (v<0.0?NEG:POS)*sa*(1.0-a); a += sa*(1.0-a);
    }
    return vec4(acc + BG*(1.0-a), 1.0);
 }` });

M.push({ k:'sliceContour', fam:'F', t:'Contours on the planes', bg:bg.paper,
 d:'The same three planes reduced to isolines, so the planes stop occluding each other.',
 c:'Cost: says nothing between the lines, in three places instead of one.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    vec3 col = BG; float ink = 0.0;
    for (int ax = 0; ax < 3; ax++) {
        float o = ax==0?ro.x:(ax==1?ro.y:ro.z);
        float d = ax==0?rd.x:(ax==1?rd.y:rd.z);
        if (abs(d) < 1e-5) continue;
        float t = -o/d;
        if (t < t0 || t > t1) continue;
        vec3 u = uvw(ro + rd*t);
        if (any(lessThan(u, vec3(0.0))) || any(greaterThan(u, vec3(1.0)))) continue;
        float v = gam(mep(u), 0.5) * 9.0;
        float f = abs(fract(v) - 0.5) * 2.0;
        float line = 1.0 - smoothstep(0.55, 0.95, f);
        if (line > ink) { ink = line; col = mix(BG, v<0.0?NEG*0.75:POS*0.75, line); }
    }
    return vec4(col, 1.0);
 }` });

M.push({ k:'slab', fam:'F', t:'One thick slab', bg:bg.paper,
 d:'A single plane given depth: everything within ±0.6 Å of it, integrated.',
 c:'Cost: a compromise, and compromises hide which half produced the signal.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    vec3 acc = vec3(0.0); float a = 0.0;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 p = ro + rd*(t0+dt*float(i));
        if (abs(p.z) > 0.05) continue;
        float v = gam(mep(uvw(p)), 0.55);
        float sa = abs(v)*0.30;
        acc += (v<0.0?NEG:POS)*sa*(1.0-a); a += sa*(1.0-a);
    }
    return vec4(acc + BG*(1.0-a), 1.0);
 }` });

// ══ G · INSTRUMENTS ═════════════════════════════════════════════════════════

M.push({ k:'ct', fam:'G', t:'CT window', bg:'vec3(0.02,0.02,0.02)',
 d:'Greyscale with a window and level, exactly as a radiologist sets a scan.',
 c:'Cost: the window is a decision made before the reader arrives, and it is invisible.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    float sum = 0.0;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 u = uvw(ro+rd*(t0+dt*float(i)));
        sum += abs(mep(u)) * dt * 3.4;
    }
    float w = clamp((sum - 0.10)/0.70, 0.0, 1.0);
    return vec4(vec3(w), 1.0);
 }` });

M.push({ k:'schlieren3d', fam:'G', t:'Schlieren', bg:bg.grey,
 d:'A knife-edge integrated through the volume: one component of ∇V becomes brightness.',
 c:'Cost: the knife has a direction the molecule does not — that anisotropy is the instrument.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    float s = 0.0;
    vec3 knife = normalize(vec3(1.0, 0.35, 0.0));
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 u = uvw(ro+rd*(t0+dt*float(i)));
        s += dot(gradMep(u), knife) * dt * 0.85;
    }
    float v = clamp(0.54 + s, 0.0, 1.0);
    return vec4(vec3(v, v, v*0.985), 1.0);
 }` });

M.push({ k:'hologram', fam:'G', t:'Phosphor hologram', bg:bg.green,
 d:'Additive contours on a storage tube, with bloom and a scanline raster.',
 c:'Cost: borrows the authority of an instrument. It looks measured because it looks green.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    float glow = 0.0;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 u = uvw(ro+rd*(t0+dt*float(i)));
        float v = gam(mep(u), 0.55) * 7.0;
        float f = abs(fract(v) - 0.5)*2.0;
        glow += (1.0 - smoothstep(0.62, 0.98, f)) * dt * 3.2;
    }
    float scan = 0.72 + 0.28*sin(gl_FragCoord.y*2.1);
    vec3 c = BG + vec3(0.28,1.0,0.52) * glow * scan;
    c += vec3(0.10,0.42,0.20) * pow(glow, 2.4) * 0.5;
    return vec4(c, 1.0);
 }` });

M.push({ k:'film', fam:'G', t:'Autoradiograph', bg:'vec3(0.90,0.89,0.86)',
 d:'Log exposure onto grain. A finite number of silver halide crystals were hit, or were not.',
 c:'Cost: grain is honest about exposure and dishonest about noise — they look identical.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    float e = 0.0;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        e += abs(mep(uvw(ro+rd*(t0+dt*float(i))))) * dt * 5.0;
    }
    float dmax = 1.0 - exp(-e*1.6);
    float g = (hash2(gl_FragCoord.xy) - 0.5) * 0.16 * (0.35 + dmax);
    float v = clamp(1.0 - dmax + g, 0.0, 1.0);
    return vec4(vec3(v*0.98, v*0.975, v*0.94), 1.0);
 }` });

// ══ H · THE ONES THAT ARGUE ═════════════════════════════════════════════════

M.push({ k:'neutralOnly', fam:'H', t:'Only the neutral shell', bg:bg.dark,
 d:'Draws exclusively where the potential is near zero — the region every other tile discards.',
 c:'Proposes: the neutral band is where a halogen or a methyl goes, and nothing shows it.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    vec3 acc = vec3(0.0);
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 u = uvw(ro+rd*(t0+dt*float(i)));
        if (rho(u) > uRhoSurf) continue;
        // gate on known(): without it the boundary window makes the whole box
        // face read as "neutral" and the tile draws a glowing cube
        if (known(u) < 0.92) continue;
        float w = 1.0 - smoothstep(0.0, 0.10, abs(mep(u)));
        acc += vec3(0.92,0.86,0.62) * w * dt * 2.6;
    }
    return vec4(BG + acc, 1.0);
 }` });

M.push({ k:'confidence3d', fam:'H', t:'Value × confidence', bg:bg.paper,
 d:'Sharp where the surface is well determined, dissolved where the density is thin and the value is not.',
 c:'Proposes: an isosurface states a precision it does not have; make the uncertainty visible.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    float prev = -1.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 u = uvw(ro+rd*(t0+dt*float(i)));
        float r = rho(u);
        if (!first && r > uRhoSurf && prev <= uRhoSurf) {
            vec3 gr = gradRho(u);
            // a surface crossing a SHALLOW density gradient is poorly located:
            // move the isovalue a little and the surface moves a lot
            float conf = clamp(length(gr)*0.55, 0.0, 1.0);
            float jit = (1.0-conf)*0.10;
            float s = 0.0;
            for (int k = 0; k < 6; k++) {
                vec3 o = (vec3(hash(u*float(k+1)*37.0), hash(u*float(k+2)*61.0),
                               hash(u*float(k+3)*89.0)) - 0.5)*jit;
                s += mep(u+o);
            }
            s = clamp(s/6.0/0.60, -1.0, 1.0);
            vec3 n = normalize(-gr);
            vec3 col = lambert(n, ramp(s));
            return vec4(mix(BG, col, 0.30 + 0.70*conf), 1.0);
        }
        prev = r; first = false;
    }
    return vec4(BG, 1.0);
 }` });

M.push({ k:'signByShape', fam:'H', t:'Sign by texture, not hue', bg:bg.paper,
 d:'One ink. Positive lobes are smooth, negative lobes are ridged. Colourblind-safe by construction.',
 c:'Proposes: hue is the only channel we ever spend, and it is the one channel 8% of men cannot read.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    float prev = 0.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 u = uvw(ro+rd*(t0+dt*float(i)));
        float v = mep(u);
        if (!first && abs(v) > uIso && abs(prev) <= uIso) {
            vec3 n = normalize(-gradMep(u)*sign(v));
            float tex = 1.0;
            if (v < 0.0) {
                float rid = abs(fract(dot(u, vec3(1.0,1.0,1.0))*46.0) - 0.5)*2.0;
                tex = 0.58 + 0.42*smoothstep(0.25, 0.85, rid);
            }
            return vec4(lambert(n, vec3(0.72,0.70,0.67)) * tex, 1.0);
        }
        prev = v; first = false;
    }
    return vec4(BG, 1.0);
 }` });

M.push({ k:'occlusionHonest', fam:'H', t:'What is hidden, shown', bg:bg.paper,
 d:'The surface, plus a tally of how much signal the surface is OCCLUDING behind it, as a shadow tint.',
 c:'Proposes: every 3D language hides something; only this one says how much.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    float prev = 0.0; bool first = true; bool hit = false;
    vec3 col = BG; float behind = 0.0;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 u = uvw(ro+rd*(t0+dt*float(i)));
        float v = mep(u);
        if (!hit && !first && abs(v) > uIso && abs(prev) <= uIso) {
            vec3 n = normalize(-gradMep(u)*sign(v));
            col = lambert(n, v<0.0?NEG:POS); hit = true;
        } else if (hit) {
            behind += abs(v)*dt*2.4;               // signal the surface is eating
        }
        prev = v; first = false;
    }
    if (!hit) return vec4(BG, 1.0);
    behind = clamp(behind, 0.0, 1.0);
    return vec4(mix(col, vec3(0.10,0.09,0.08), behind*0.55), 1.0);
 }` });

M.push({ k:'pharmacophoreFocus', fam:'H', t:'Focus on one site', bg:bg.paper,
 d:'Full resolution inside a sphere around the carboxyl oxygen; everything else defocused.',
 c:'Proposes: a binding argument is about one site, and the whole-molecule view is the distraction.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    vec3 focus = vec3(0.30, 0.24, 0.50);
    float prev = -1.0; bool first = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 u = uvw(ro+rd*(t0+dt*float(i)));
        float r = rho(u);
        if (!first && r > uRhoSurf && prev <= uRhoSurf) {
            float d = length((u - focus)*vec3(1.0,1.0,0.75));
            float sharp = 1.0 - smoothstep(0.16, 0.40, d);
            vec3 n = normalize(-gradRho(u));
            float s = clamp(mep(u)/0.60, -1.0, 1.0);
            vec3 col = lambert(n, ramp(s));
            vec3 flat_ = mix(BG, vec3(0.80,0.79,0.77), 0.55);
            return vec4(mix(flat_, col, 0.18 + 0.82*sharp), 1.0);
        }
        prev = r; first = false;
    }
    return vec4(BG, 1.0);
 }` });

const FAMILIES3D = {
  A: ['Isosurface — the incumbent',
      'One level, drawn as a solid. Every molecular package opens here, and the choice is so standard that nobody states it is a choice: an isosurface answers "where is the field equal to X" and refuses every other question. Five ways of loosening that refusal.'],
  B: ['Volume — no surface at all',
      'March the ray, accumulate, choose no level. These see the whole field, which is exactly their problem: an integral along a ray cannot say where along the ray anything was.'],
  C: ['On the molecular surface — what chemists actually use',
      'The field is not read in free space; it is read where a partner can touch it. Painting the potential onto the electron-density surface is the incumbent for a real reason, and the variants ask what else that surface could carry.'],
  D: ['Discrete — the field as countable',
      'Marks you could in principle count, in three dimensions. Each imposes a lattice or a random seed that is not in the data, and each is honest in a way continuous tone is not: you can see the sampling.'],
  E: ['Flow — the gradient nobody draws',
      'A potential exists to have a gradient. What a charged group actually feels is −∇V, and almost no molecular viewer will draw it, which means the quantity chemists reason about is the one never rendered.'],
  F: ['Slices — 2D, honestly labelled',
      'The radiologist’s answer, and the only family that gives exact values rather than an impression. Its refusal to interpolate between planes is a feature, and it is why CT won over volume rendering in clinical use.'],
  G: ['Instruments — how machines already draw fields',
      'Before anyone rendered a molecular orbital, machines imaged continuous quantities onto phosphor, film and glass. Each brings a built-in claim of measurement, which is an asset and a liability in the same stroke.'],
  H: ['The ones that argue',
      'Each refuses something on purpose, and the refusal is the proposal. Included because a study that only widens the space has not made an argument.'],
};

window.MODES3D = M;
window.FAMILIES3D = FAMILIES3D;


/**
 * ══ THE COMPOSITE ═══════════════════════════════════════════════════════════
 *
 * Ivan kept three: silhouette (05), gradient magnitude (21), volumetric LIC
 * (24). They are not three picks. They are ONE discovery wearing three hats:
 * every one of them is DARK-GROUND, ADDITIVE, and NON-OCCLUDING — the field
 * arrives as light rather than as plastic, and the molecule stays visible
 * through it. That is a compositing model, not a look, and additive light is
 * commutative, so the three do not fight when stacked. Neither does the fourth
 * one that belongs with them, the neutral shell (33), which is already dark and
 * already additive.
 *
 * They also share one fatal gap, and it is the gap that matters most in this
 * particular domain: NONE OF THEM CARRIES SIGN. |grad V| discards it by
 * construction, LIC's noise destroys it, and a rim is the thinnest possible
 * carrier for a hue. For an electrostatic potential, sign — donor versus
 * acceptor — is the first question anyone asks. Three languages that cannot
 * answer it are an atmosphere, not an instrument.
 *
 * So the proposal is not "ship three of thirty-seven". It is: treat them as
 * SEPARABLE CHANNELS of one render, and add back the channel they are all
 * missing. Shape from the rim, steepness from |grad V|, direction from the LIC
 * grain, sign from a signed tint — four orthogonal quantities on four channels
 * that a viewer can decode independently, and each can be switched off to see
 * what it was carrying.
 *
 * The ground toggle is here because the app's scene background is #f1f0eb.
 * These languages were all designed on black. Whether an additive model
 * survives inversion onto the panel's actual paper is a question to LOOK at,
 * not to argue about.
 */
M.push({ k:'composite', fam:'HERO', t:'The composite', bg:'vec3(0.055,0.055,0.050)',
 d:'Silhouette, steepness, direction and sign as four independent channels of one additive render.',
 c:'Proposes: those three are one instrument with a missing channel, not three candidates.',
 s:`vec4 shade(vec3 ro, vec3 rd, float t0, float t1) {
    float dt = (t1-t0)/float(uSteps);
    vec3 DARK  = vec3(0.055,0.055,0.050);
    vec3 PAPERBG = vec3(0.945,0.941,0.921);        // the app's own --scene-bg
    float L = uGround;                              // 0 dark, 1 paper
    vec3 base = mix(DARK, PAPERBG, L);

    float steep = 0.0, grain = 0.0, signAcc = 0.0, wSign = 0.0;
    vec3  rim = vec3(0.0);
    float prev = 0.0; bool first = true;

    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 p = ro + rd*(t0 + dt*float(i));
        vec3 u = uvw(p);
        float v = mep(u);
        bool solvent = rho(u) <= uRhoSurf;

        // ① SHAPE — the rim, and the only channel that states a boundary.
        // Also gated on known(): the window drives the value to zero across the
        // face, which registers as a genuine level CROSSING, so the rim drew a
        // flat quadrilateral where the box is. Third artefact from one window —
        // the value, its derivative, and now its level set.
        if (!first && known(u) > 0.985 && abs(v) > uIso && abs(prev) <= uIso) {
            vec3 n = normalize(-gradMep(u)*sign(v));
            float r = pow(1.0 - abs(dot(n, rd)), 3.0);
            rim += mix(vec3(1.0), (v < 0.0 ? NEG : POS), 0.55) * r * 1.35;
        }
        prev = v; first = false;

        if (!solvent) continue;
        // THE WINDOW HAS A DERIVATIVE. boxWin() removes the hexagonal box from
        // every value-based mode, and in doing so it puts a large artificial
        // gradient at exactly the box face — so the two gradient channels drew
        // the box straight back, fainter and harder to attribute. A fix applied
        // to a quantity is not applied to its derivative.
        if (known(u) < 0.985) continue;
        vec3 g = gradMep(u); float gm = length(g);

        // ② STEEPNESS — where a partner feels a force, not where V is large
        steep += gm * dt * 0.30;

        // ③ DIRECTION — noise smeared along grad V; the grain IS the vector
        if (gm > 0.04) {
            vec3 dir = g/gm; float sm = 0.0;
            for (int k = -3; k <= 3; k++) sm += hash(floor((u + dir*float(k)*0.011)*150.0));
            grain += pow(sm/7.0, 3.0) * clamp(gm*0.5,0.0,1.0) * dt * 3.4;
        }

    }

    // ④ SIGN — and the ray integral was the WRONG CARRIER for it. A weighted
    // mean along a ray is a weak, washy quantity: weight it honestly by its own
    // support and it nearly vanishes; do not, and it paints the whole bounding
    // box. Two rounds of tuning were two rounds of arguing with the estimator
    // instead of replacing it.
    //
    // Family C already contains the answer. Sign is meaningful WHERE A PARTNER
    // CAN TOUCH IT, so it belongs on the 0.001 a.u. density surface — a hard
    // 2D locus, one value per ray, nothing to average away. Drawn at low alpha
    // it stays non-occluding, which is the property the other three channels
    // were chosen for in the first place.
    float dt2 = (t1-t0)/float(uSteps);
    float prevR = -1.0; bool firstR = true;
    for (int i = 0; i < 512; i++) {
        if (i >= uSteps) break;
        vec3 u = uvw(ro + rd*(t0 + dt2*float(i)));
        float r = rho(u);
        if (!firstR && r > uRhoSurf && prevR <= uRhoSurf) {
            signAcc = clamp(mep(u)/0.55, -1.0, 1.0);
            wSign = max(0.0, dot(normalize(-gradRho(u)), -rd));   // facing the eye
            break;
        }
        prevR = r; firstR = false;
    }

    steep = clamp(steep, 0.0, 1.0);
    // A MEAN IS NOT A MEASUREMENT UNTIL YOU SAY HOW MUCH IT AVERAGED. sgn is
    // the signal-weighted mean sign along the ray, and a ray that grazes the
    // molecule and collects almost nothing still returns a full-strength +-1 —
    // so the sign channel painted the entire box cross-section a flat colour
    // and read, again, as a box. Scale by the WEIGHT that produced the mean.
    float sgn = signAcc;
    float face = 0.30 + 0.70*wSign;      // the surface reads brighter facing you

    // additive on black; the SAME quantities subtractive on paper, because ink
    // removes light where a phosphor adds it. The architecture inverts; the
    // palette does not, which is the whole reason this toggle exists.
    vec3 shapeC = rim * uChan.x;
    vec3 steepC = vec3(0.62,0.72,0.95) * pow(steep,0.75) * 1.30 * uChan.y;
    vec3 grainC = vec3(0.80,0.84,0.95) * grain * 0.85 * uChan.z;
    vec3 signC  = (sgn < 0.0 ? NEG : POS) * abs(sgn) * face * uChan.w;

    vec3 outc;
    if (L < 0.5) {
        // TONE MAP, do not just add. Four additive channels summed past 1.0 and
        // clipped to white, which destroys the one channel whose entire content
        // is hue — the sign. Exposure keeps the ratios, so red stays red at the
        // top end instead of every bright region becoming the same white.
        vec3 lin = shapeC*0.55 + steepC*0.80 + grainC*0.62 + signC*1.45;
        outc = base + (vec3(1.0) - exp(-lin * 1.25));
    } else {
        float ink = clamp(dot(shapeC, vec3(0.33)) + dot(steepC, vec3(0.30))
                        + dot(grainC, vec3(0.30)), 0.0, 1.0);
        outc = base * (1.0 - ink*0.92);
        outc = mix(outc, (sgn<0.0?NEG:POS), abs(sgn)*0.40*uChan.w);
    }
    return vec4(outc, 1.0);
 }` });
