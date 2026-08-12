/**
 * Thirty visual languages for one scalar field.
 *
 * Every tile draws the SAME real data — aspirin's classical electrostatic
 * potential, a 96x96 slice through the molecular plane, -109.5 to +127.8
 * kcal/mol, computed by backend/field_server.py. Not a mock and not a texture:
 * if a language cannot survive real data it is not a language, it is a filter.
 *
 * THE POINT IS THAT THESE ARE NOT PALETTES. Thirty recolourings of one
 * isosurface is the first thing anyone thinks of, which is exactly why it is
 * worth nothing — it is a sweep through the dense region of the obvious. What
 * varies here is the MARK: what the field is made of on the page. A contour is
 * a line asserting equality. A stipple is a countable event. A hillshade is a
 * lie about light that the eye reads as height. Each tradition below already
 * solved "how do I show a continuous quantity" for a different medium and a
 * different reader, and each brings its own answer and its own distortion.
 *
 * They are grouped by that tradition, not by looks, so the page reads as a
 * question about representation rather than a mood board.
 */

const R = {};

// ── shared helpers ─────────────────────────────────────────────────────────

function grid(d) { return { w: d.w, h: d.h, v: d.v, min: d.min, max: d.max }; }

/** value at grid coords, clamped */
function at(g, x, y) {
    x = Math.max(0, Math.min(g.w - 1, x | 0));
    y = Math.max(0, Math.min(g.h - 1, y | 0));
    return g.v[y * g.w + x];
}

/** signed normalisation to [-1,1] about zero — a diverging field must keep
 * zero AT zero, or the neutral region drifts and the picture asserts a charge
 * the molecule does not have. */
const GAMMA = 0.42;   // stated on the page, not hidden in here
function norm(g, val) {
    const m = Math.max(Math.abs(g.min), Math.abs(g.max));
    const t = Math.max(-1, Math.min(1, val / m));
    // SIGNED POWER LAW. A 1/r Coulomb field collapses to near-zero a couple of
    // angstroms out, so a linear ramp spends 80% of its range on the region
    // right at the nuclei and leaves the rest of the frame blank — every one of
    // the thirty languages would then be drawing the same empty square.
    //
    // The transfer function is a DISPLAY choice and it is named on the page.
    // The data underneath stays raw: raw data, declared transfer, is honest;
    // pre-scaled data with a linear-looking legend is not.
    return Math.sign(t) * Math.pow(Math.abs(t), GAMMA);
}

function bilinear(g, fx, fy) {
    const x = fx * (g.w - 1), y = fy * (g.h - 1);
    const x0 = Math.floor(x), y0 = Math.floor(y);
    const tx = x - x0, ty = y - y0;
    return at(g, x0, y0) * (1 - tx) * (1 - ty) + at(g, x0 + 1, y0) * tx * (1 - ty)
         + at(g, x0, y0 + 1) * (1 - tx) * ty + at(g, x0 + 1, y0 + 1) * tx * ty;
}

/** central-difference gradient, in grid units */
function grad(g, x, y) {
    return [ (at(g, x + 1, y) - at(g, x - 1, y)) / 2,
             (at(g, x, y + 1) - at(g, x, y - 1)) / 2 ];
}

/** Gradient of the DISPLAYED field, scaled to its own 97th percentile.
 *
 * Three tiles independently divided the raw gradient by a hand-picked constant
 * — 6 in `edge`, 0.06 in `hillshade`, 26 in `schlieren` — each tuned by eye on
 * this one specimen before the transfer function existed. Every one of them
 * clipped to solid black the moment the display changed, and every one would
 * die silently on a different molecule, because a bare denominator is an
 * unstated assumption about the UNITS of the data. Cached per grid: this walks
 * the whole array and each tile calls it once. */
const _gradCache = new WeakMap();
function gradScale(g) {
    let v = _gradCache.get(g.v);
    if (v !== undefined) return v;
    const mags = [];
    for (let y = 1; y < g.h - 1; y++)
        for (let x = 1; x < g.w - 1; x++) {
            const gx = (norm(g, at(g, x + 1, y)) - norm(g, at(g, x - 1, y))) / 2;
            const gy = (norm(g, at(g, x, y + 1)) - norm(g, at(g, x, y - 1))) / 2;
            mags.push(Math.hypot(gx, gy));
        }
    mags.sort((a, b) => a - b);
    v = mags[Math.floor(mags.length * 0.97)] || 1;
    _gradCache.set(g.v, v);
    return v;
}

/** central difference on the DISPLAYED field, in units of gradScale */
function gradN(g, px, py, S) {
    const fx = px / (S - 1) * (g.w - 1), fy = py / (S - 1) * (g.h - 1);
    const k = gradScale(g);
    const gx = (norm(g, bilinear(g, (fx + 1) / (g.w - 1), fy / (g.h - 1)))
              - norm(g, bilinear(g, (fx - 1) / (g.w - 1), fy / (g.h - 1)))) / 2 / k;
    const gy = (norm(g, bilinear(g, fx / (g.w - 1), (fy + 1) / (g.h - 1)))
              - norm(g, bilinear(g, fx / (g.w - 1), (fy - 1) / (g.h - 1)))) / 2 / k;
    return [gx, gy];
}

function clear(ctx, S, bg) { ctx.fillStyle = bg; ctx.fillRect(0, 0, S, S); }

function eachPixel(ctx, S, g, fn) {
    const img = ctx.createImageData(S, S);
    for (let py = 0; py < S; py++) {
        for (let px = 0; px < S; px++) {
            const n = norm(g, bilinear(g, px / (S - 1), py / (S - 1)));
            const c = fn(n, px, py);
            const i = (py * S + px) * 4;
            img.data[i] = c[0]; img.data[i + 1] = c[1]; img.data[i + 2] = c[2];
            img.data[i + 3] = c.length > 3 ? c[3] : 255;
        }
    }
    ctx.putImageData(img, 0, 0);
}

function mix(a, b, t) {
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

/** the molecule, as a restrained overlay — the field is the subject, the
 * skeleton is only there so the field has somewhere to be. */
function atoms(ctx, S, d, opt = {}) {
    const col = opt.color || 'rgba(20,20,18,.55)';
    const r = opt.r || 1.7;
    ctx.fillStyle = col;
    for (const a of d.atoms) {
        if (a.e === 'H' && !opt.hydrogens) continue;
        ctx.beginPath();
        ctx.arc(a.x * S, a.y * S, r, 0, 6.2832);
        ctx.fill();
    }
}

// ── A · continuous field: the field as a substance ─────────────────────────

R.wash = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f4f3ef');
    const neg = [189, 119, 123], pos = [106, 140, 192], zero = [244, 243, 239];
    eachPixel(ctx, S, g, n => n < 0 ? mix(zero, neg, -n) : mix(zero, pos, n));
    atoms(ctx, S, d);
};

R.bands = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f4f3ef');
    const STEPS = 9;
    const neg = [189, 119, 123], pos = [106, 140, 192], zero = [246, 245, 241];
    eachPixel(ctx, S, g, n => {
        const q = Math.round(n * STEPS) / STEPS;   // posterised, not smooth
        return q < 0 ? mix(zero, neg, -q) : mix(zero, pos, q);
    });
    atoms(ctx, S, d);
};

R.contour = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#faf9f6');
    // marching-squares-lite: draw where the sign of (v - level) flips
    const levels = [-0.6, -0.4, -0.25, -0.12, -0.05, 0.05, 0.12, 0.25, 0.4, 0.6];
    for (const L of levels) {
        ctx.strokeStyle = L < 0 ? 'rgba(160,90,95,.85)' : 'rgba(80,110,165,.85)';
        ctx.lineWidth = Math.abs(L) > 0.35 ? 1.4 : 0.7;   // index contours, as a chart does
        ctx.beginPath();
        for (let y = 0; y < S; y += 2) {
            for (let x = 0; x < S; x += 2) {
                const a = norm(g, bilinear(g, x / S, y / S)) - L;
                const b = norm(g, bilinear(g, (x + 2) / S, y / S)) - L;
                const c = norm(g, bilinear(g, x / S, (y + 2) / S)) - L;
                if (a * b < 0) { ctx.moveTo(x, y); ctx.lineTo(x + 2, y); }
                if (a * c < 0) { ctx.moveTo(x, y); ctx.lineTo(x, y + 2); }
            }
        }
        ctx.stroke();
    }
    atoms(ctx, S, d);
};

R.haze = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#14161c');
    // accumulation, the 2D analogue of a ray march: brightness is integrated
    // magnitude, so the eye reads depth where there is only density
    eachPixel(ctx, S, g, n => {
        const a = Math.pow(Math.abs(n), 0.6);
        const c = n < 0 ? [214, 128, 132] : [128, 170, 235];
        return [c[0] * a, c[1] * a, c[2] * a];
    });
    atoms(ctx, S, d, { color: 'rgba(255,255,255,.5)' });
};

/** gradient magnitude of the DISPLAYED field, scaled to its own 97th
 * percentile. Both halves of that sentence were bugs on the first pass: the
 * gradient was taken on raw kcal/mol and divided by a hand-picked 6, so once
 * the transfer function went in, every pixel clipped and the tile rendered
 * solid black. A hard-coded denominator is a hidden assumption about the
 * units of the specimen — swap the molecule and the tile dies silently. */
R.edge = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f7f6f2');
    eachPixel(ctx, S, g, (n, px, py) => {
        const [gx, gy] = gradN(g, px, py, S);
        const m = Math.min(1, Math.hypot(gx, gy));
        const t = 1 - m;
        return [247 * t + 30 * m, 246 * t + 30 * m, 242 * t + 28 * m];
    });
    atoms(ctx, S, d, { color: 'rgba(150,60,50,.7)' });
};

// ── B · discrete marks: the field as countable ─────────────────────────────

R.stipple = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#faf9f6');
    let seed = 7;
    const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
    for (let i = 0; i < 26000; i++) {
        const x = rnd(), y = rnd();
        const n = norm(g, bilinear(g, x, y));
        if (rnd() > Math.abs(n) * 1.15) continue;   // density IS the value
        ctx.fillStyle = n < 0 ? 'rgba(150,70,76,.75)' : 'rgba(60,92,150,.75)';
        ctx.fillRect(x * S, y * S, 1.1, 1.1);
    }
    atoms(ctx, S, d);
};

R.halftone = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f7f6f2');
    const P = 7;
    for (let y = P / 2; y < S; y += P) {
        for (let x = P / 2; x < S; x += P) {
            const n = norm(g, bilinear(g, x / S, y / S));
            const r = Math.abs(n) * P * 0.62;
            if (r < 0.25) continue;
            ctx.fillStyle = n < 0 ? '#a85c62' : '#4a6fa8';
            ctx.beginPath(); ctx.arc(x, y, r, 0, 6.2832); ctx.fill();
        }
    }
    atoms(ctx, S, d);
};

R.hatch = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#faf9f6');
    ctx.lineWidth = 0.8;
    for (let k = -S; k < S * 2; k += 5) {
        for (const dir of [1, -1]) {
            ctx.beginPath();
            let drawing = false;
            for (let t = 0; t < S * 1.5; t += 2) {
                const x = dir > 0 ? t : S - t, y = t - k * dir;
                if (x < 0 || x >= S || y < 0 || y >= S) { drawing = false; continue; }
                const n = norm(g, bilinear(g, x / S, y / S));
                const want = dir > 0 ? n > 0.12 : n < -0.12;   // sign chooses the direction
                if (want && Math.abs(n) > 0.12 + (t % 11) / 90) {
                    if (!drawing) { ctx.moveTo(x, y); drawing = true; } else ctx.lineTo(x, y);
                } else drawing = false;
            }
            ctx.strokeStyle = dir > 0 ? 'rgba(60,92,150,.7)' : 'rgba(150,70,76,.7)';
            ctx.stroke();
        }
    }
    atoms(ctx, S, d);
};

R.engrave = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f6f4ee');
    // one family of lines whose WEIGHT carries the value — a banknote's answer
    // displacement must be comparable to the LINE SPACING or the whole tile
    // reads as a flat ruled page. At +-2.4px against a 4px pitch the field was
    // invisible at thumbnail size; a guilloche works because the lines nearly
    // collide. Weight carries magnitude so sign and strength are separable.
    const PITCH = 7, AMP = PITCH * 0.92;
    for (let y = -PITCH; y < S + PITCH; y += PITCH) {
        ctx.beginPath();
        let prevW = 0;
        for (let x = 0; x <= S; x += 1) {
            const n = norm(g, bilinear(g, x / S, Math.max(0, Math.min(1, y / S))));
            const yy = y + n * AMP;
            x === 0 ? ctx.moveTo(x, yy) : ctx.lineTo(x, yy);
            prevW = Math.max(prevW, Math.abs(n));
        }
        ctx.strokeStyle = 'rgba(26,26,24,.70)';
        ctx.lineWidth = 0.45 + prevW * 0.85;
        ctx.stroke();
    }
    atoms(ctx, S, d);
};

R.voxel = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f2f1ec');
    const P = 6;
    for (let y = 0; y < S; y += P) {
        for (let x = 0; x < S; x += P) {
            const n = norm(g, bilinear(g, x / S, y / S));
            if (Math.abs(n) < 0.06) continue;
            const q = Math.round(Math.abs(n) * 4) / 4;
            ctx.fillStyle = n < 0 ? `rgba(168,92,98,${q})` : `rgba(74,111,168,${q})`;
            ctx.fillRect(x + 0.5, y + 0.5, P - 1, P - 1);
        }
    }
    atoms(ctx, S, d);
};

R.rings = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#faf9f6');
    for (const a of d.atoms) {
        if (a.e === 'H') continue;
        const n = norm(g, bilinear(g, a.x, a.y));
        const k = Math.round(Math.abs(n) * 7) + 1;
        ctx.strokeStyle = n < 0 ? 'rgba(150,70,76,.55)' : 'rgba(60,92,150,.55)';
        ctx.lineWidth = 0.7;
        for (let i = 1; i <= k; i++) {
            ctx.beginPath(); ctx.arc(a.x * S, a.y * S, i * 2.6, 0, 6.2832); ctx.stroke();
        }
    }
    atoms(ctx, S, d);
};

// ── C · line-based: the field as flow ──────────────────────────────────────

R.streamlines = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#faf9f6');
    ctx.lineWidth = 0.7;
    for (let sy = 3; sy < 96; sy += 5) {
        for (let sx = 3; sx < 96; sx += 5) {
            let x = sx, y = sy;
            const n0 = norm(g, at(g, x, y));
            if (Math.abs(n0) < 0.05) continue;
            ctx.strokeStyle = n0 < 0 ? 'rgba(150,70,76,.5)' : 'rgba(60,92,150,.5)';
            ctx.beginPath(); ctx.moveTo(x / 96 * S, y / 96 * S);
            for (let step = 0; step < 26; step++) {
                const [gx, gy] = grad(g, x, y);
                const m = Math.hypot(gx, gy) || 1;
                x += gx / m * 0.9; y += gy / m * 0.9;      // walk uphill
                if (x < 0 || y < 0 || x > 95 || y > 95) break;
                ctx.lineTo(x / 96 * S, y / 96 * S);
            }
            ctx.stroke();
        }
    }
    atoms(ctx, S, d);
};

R.lic = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f7f6f2');
    // line integral convolution: smear noise ALONG the gradient, so texture
    // direction encodes a vector the eye cannot otherwise see in a scalar
    let seed = 11;
    const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
    const noise = new Float32Array(96 * 96);
    for (let i = 0; i < noise.length; i++) noise[i] = rnd();
    const img = ctx.createImageData(S, S);
    for (let py = 0; py < S; py++) for (let px = 0; px < S; px++) {
        let x = px / S * 96, y = py / S * 96, acc = 0, cnt = 0;
        for (let k = 0; k < 12; k++) {
            const [gx, gy] = grad(g, x, y);
            const m = Math.hypot(gx, gy) || 1;
            x += gx / m * 0.8; y += gy / m * 0.8;
            acc += noise[((y | 0) * 96 + (x | 0) + noise.length) % noise.length]; cnt++;
        }
        const t = acc / cnt;
        const n = norm(g, bilinear(g, px / S, py / S));
        const base = n < 0 ? [168, 92, 98] : [74, 111, 168];
        const c = mix([247, 246, 242], base, Math.abs(n) * 0.85);
        const i = (py * S + px) * 4;
        img.data[i] = c[0] * (0.6 + t * 0.55);
        img.data[i + 1] = c[1] * (0.6 + t * 0.55);
        img.data[i + 2] = c[2] * (0.6 + t * 0.55);
        img.data[i + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    atoms(ctx, S, d);
};

R.hairline = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#fbfaf7');
    const levels = [];
    for (let L = -0.9; L <= 0.9; L += 0.075) if (Math.abs(L) > 0.03) levels.push(L);
    ctx.lineWidth = 0.4;
    ctx.strokeStyle = 'rgba(30,30,28,.55)';
    ctx.beginPath();
    for (const L of levels) {
        for (let y = 0; y < S; y += 2) for (let x = 0; x < S; x += 2) {
            const a = norm(g, bilinear(g, x / S, y / S)) - L;
            const b = norm(g, bilinear(g, (x + 2) / S, y / S)) - L;
            const c = norm(g, bilinear(g, x / S, (y + 2) / S)) - L;
            if (a * b < 0) { ctx.moveTo(x, y); ctx.lineTo(x + 2, y); }
            if (a * c < 0) { ctx.moveTo(x, y); ctx.lineTo(x, y + 2); }
        }
    }
    ctx.stroke();
    atoms(ctx, S, d);
};

R.ribbon = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#15171d');
    for (let sy = 4; sy < 96; sy += 7) {
        for (let sx = 4; sx < 96; sx += 7) {
            let x = sx, y = sy;
            const n0 = norm(g, at(g, x, y));
            if (Math.abs(n0) < 0.08) continue;
            for (let step = 0; step < 20; step++) {
                const [gx, gy] = grad(g, x, y);
                const m = Math.hypot(gx, gy) || 1;
                const nx = x + gx / m, ny = y + gy / m;
                const n = norm(g, at(g, x, y));
                ctx.strokeStyle = n < 0 ? `rgba(226,140,144,${0.06 + Math.abs(n) * 0.5})`
                                        : `rgba(140,180,240,${0.06 + Math.abs(n) * 0.5})`;
                ctx.lineWidth = 0.4 + Math.abs(n) * 2.6;    // weight carries magnitude
                ctx.beginPath(); ctx.moveTo(x / 96 * S, y / 96 * S);
                ctx.lineTo(nx / 96 * S, ny / 96 * S); ctx.stroke();
                x = nx; y = ny;
                if (x < 0 || y < 0 || x > 95 || y > 95) break;
            }
        }
    }
    atoms(ctx, S, d, { color: 'rgba(255,255,255,.45)' });
};

// ── D · cartographic traditions ────────────────────────────────────────────

R.bathymetric = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#eef4f6');
    const ramp = [[8,48,84],[26,88,132],[58,132,176],[112,176,204],[176,212,226],[238,244,246],
                  [246,236,214],[226,196,142],[196,150,96],[152,104,62]];
    eachPixel(ctx, S, g, n => {
        const t = (n + 1) / 2 * (ramp.length - 1);
        const i = Math.max(0, Math.min(ramp.length - 2, Math.floor(t)));
        return mix(ramp[i], ramp[i + 1], t - i);
    });
    atoms(ctx, S, d, { color: 'rgba(255,255,255,.7)' });
};

R.hillshade = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f2f1ec');
    // a lie about light that the eye reads as height. The slope is CLAMPED: a
    // point-charge field is singular at a nucleus, so an unclamped Lambert term
    // saturates to pure black and pure white in a ring around every atom and
    // the relief reads as damage. Terrain has no such singularity, which is why
    // the borrowed convention needs this and the cartographer never states it.
    const EX = 1.15;                                   // vertical exaggeration
    const LX = -0.62, LY = -0.62, LZ = 0.48;           // NW light, as a map
    eachPixel(ctx, S, g, (n, px, py) => {
        let [gx, gy] = gradN(g, px, py, S);
        const m = Math.hypot(gx, gy);
        if (m > 1) { gx /= m; gy /= m; }               // clamp, do not clip
        const nx = -gx * EX, ny = -gy * EX, nz = 1;
        const len = Math.hypot(nx, ny, nz);
        const lam = Math.max(0, (nx * LX + ny * LY + nz * LZ) / len);
        const shade = 0.45 + 0.72 * lam;
        const base = n < 0 ? [198, 148, 146] : [140, 163, 198];
        const c = mix([246, 245, 241], base, Math.abs(n) * 0.78);
        return [Math.min(255, c[0] * shade), Math.min(255, c[1] * shade), Math.min(255, c[2] * shade)];
    });
    atoms(ctx, S, d, { color: 'rgba(40,38,32,.5)' });
};

R.topographic = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#fbf8f0');
    eachPixel(ctx, S, g, n => mix([251, 248, 240], n < 0 ? [232, 214, 206] : [214, 224, 236], Math.abs(n) * 0.9));
    const levels = [];
    for (let L = -0.9; L <= 0.9; L += 0.1) levels.push(L);
    levels.forEach((L, i) => {
        const index = i % 5 === 0;                     // index contour every fifth
        ctx.strokeStyle = index ? 'rgba(120,84,52,.9)' : 'rgba(150,120,86,.55)';
        ctx.lineWidth = index ? 1.2 : 0.5;
        ctx.beginPath();
        for (let y = 0; y < S; y += 2) for (let x = 0; x < S; x += 2) {
            const a = norm(g, bilinear(g, x / S, y / S)) - L;
            const b = norm(g, bilinear(g, (x + 2) / S, y / S)) - L;
            const c = norm(g, bilinear(g, x / S, (y + 2) / S)) - L;
            if (a * b < 0) { ctx.moveTo(x, y); ctx.lineTo(x + 2, y); }
            if (a * c < 0) { ctx.moveTo(x, y); ctx.lineTo(x, y + 2); }
        }
        ctx.stroke();
    });
    atoms(ctx, S, d, { color: 'rgba(90,64,40,.65)' });
};

R.choropleth = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f6f5f1');
    const P = 12;
    for (let y = 0; y < S; y += P) for (let x = 0; x < S; x += P) {
        let s = 0, c = 0;
        for (let j = 0; j < P; j += 3) for (let i = 0; i < P; i += 3) {
            s += norm(g, bilinear(g, (x + i) / S, (y + j) / S)); c++;
        }
        const n = s / c;                                // AREAL average, as a choropleth is
        ctx.fillStyle = n < 0 ? `rgba(168,92,98,${Math.abs(n) * 0.9})`
                              : `rgba(74,111,168,${Math.abs(n) * 0.9})`;
        ctx.fillRect(x, y, P, P);
        ctx.strokeStyle = 'rgba(255,255,255,.6)'; ctx.lineWidth = 0.5;
        ctx.strokeRect(x + 0.25, y + 0.25, P - 0.5, P - 0.5);
    }
    atoms(ctx, S, d);
};

R.spotheights = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#fbfaf7');
    eachPixel(ctx, S, g, n => mix([251, 250, 247], n < 0 ? [236, 220, 220] : [220, 228, 240], Math.abs(n)));
    // label the extrema, the way a chart marks summits and soundings
    const pts = [];
    for (let y = 4; y < g.h - 4; y += 3) for (let x = 4; x < g.w - 4; x += 3) {
        const v = at(g, x, y);
        let isMax = true, isMin = true;
        for (let j = -3; j <= 3; j += 3) for (let i = -3; i <= 3; i += 3) {
            if (!i && !j) continue;
            if (at(g, x + i, y + j) > v) isMax = false;
            if (at(g, x + i, y + j) < v) isMin = false;
        }
        if ((isMax || isMin) && Math.abs(norm(g, v)) > 0.25) pts.push([x, y, v]);
    }
    ctx.font = '7px ui-monospace, monospace';
    for (const [x, y, v] of pts.slice(0, 14)) {
        const px = x / g.w * S, py = y / g.h * S;
        ctx.fillStyle = v < 0 ? '#8c4a50' : '#3c5f96';
        ctx.beginPath(); ctx.arc(px, py, 1.6, 0, 6.2832); ctx.fill();
        ctx.fillText(v.toFixed(0), px + 3, py - 2);
    }
    atoms(ctx, S, d);
};

// ── E · print and reproduction ─────────────────────────────────────────────

R.riso = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f4f1e8');
    // two spot inks, deliberately misregistered — the overlap is the third colour
    for (const [dx, dy, ink, sign] of [[-1.5, 1, '0,116,162', 1], [1.5, -1, '236,88,90', -1]]) {
        const img = ctx.getImageData(0, 0, S, S);
        for (let py = 0; py < S; py++) for (let px = 0; px < S; px++) {
            const n = norm(g, bilinear(g, (px + dx) / S, (py + dy) / S)) * sign;
            if (n <= 0.05) continue;
            const a = Math.min(1, n * 1.25) * 0.8;
            const i = (py * S + px) * 4;
            const c = ink.split(',').map(Number);
            img.data[i] = img.data[i] * (1 - a) + c[0] * a;
            img.data[i + 1] = img.data[i + 1] * (1 - a) + c[1] * a;
            img.data[i + 2] = img.data[i + 2] * (1 - a) + c[2] * a;
        }
        ctx.putImageData(img, 0, 0);
    }
    atoms(ctx, S, d, { color: 'rgba(30,28,24,.5)' });
};

R.blueprint = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#123a63');
    ctx.strokeStyle = 'rgba(226,236,246,.75)';
    const levels = [];
    for (let L = -0.85; L <= 0.85; L += 0.12) levels.push(L);
    for (const L of levels) {
        ctx.lineWidth = Math.abs(L) > 0.5 ? 1.1 : 0.55;
        ctx.beginPath();
        for (let y = 0; y < S; y += 2) for (let x = 0; x < S; x += 2) {
            const a = norm(g, bilinear(g, x / S, y / S)) - L;
            const b = norm(g, bilinear(g, (x + 2) / S, y / S)) - L;
            const c = norm(g, bilinear(g, x / S, (y + 2) / S)) - L;
            if (a * b < 0) { ctx.moveTo(x, y); ctx.lineTo(x + 2, y); }
            if (a * c < 0) { ctx.moveTo(x, y); ctx.lineTo(x, y + 2); }
        }
        ctx.stroke();
    }
    atoms(ctx, S, d, { color: 'rgba(255,255,255,.85)' });
};

R.newsprint = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#efece4');
    const P = 5, ang = Math.PI / 4;                     // a screen has an ANGLE
    for (let y = -S; y < S * 2; y += P) for (let x = -S; x < S * 2; x += P) {
        const rx = x * Math.cos(ang) - y * Math.sin(ang);
        const ry = x * Math.sin(ang) + y * Math.cos(ang);
        if (rx < 0 || ry < 0 || rx >= S || ry >= S) continue;
        const n = Math.abs(norm(g, bilinear(g, rx / S, ry / S)));
        if (n < 0.05) continue;
        ctx.fillStyle = 'rgba(24,22,20,.88)';
        ctx.beginPath(); ctx.arc(rx, ry, n * P * 0.6, 0, 6.2832); ctx.fill();
    }
    atoms(ctx, S, d);
};

R.deboss = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#e9e7e0');
    // paper pressed: two offset shadows, no colour at all. The field becomes
    // relief, which is the only channel letterpress ever had.
    eachPixel(ctx, S, g, (n, px, py) => {
        const a = norm(g, bilinear(g, (px - 1) / S, (py - 1) / S));
        const b = norm(g, bilinear(g, (px + 1) / S, (py + 1) / S));
        const l = 233 + (a - b) * 120;
        const v = Math.max(196, Math.min(252, l));
        return [v, v - 2, v - 8];
    });
    atoms(ctx, S, d, { color: 'rgba(60,56,50,.45)' });
};

R.thermal = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f8f7f3');
    // 1-bit, ordered dither — a receipt printer's entire vocabulary
    const B = [[0,8,2,10],[12,4,14,6],[3,11,1,9],[15,7,13,5]];
    const img = ctx.createImageData(S, S);
    for (let py = 0; py < S; py++) for (let px = 0; px < S; px++) {
        const n = Math.abs(norm(g, bilinear(g, px / S, py / S)));
        const on = n * 16 > B[py % 4][px % 4];
        const v = on ? 26 : 248;
        const i = (py * S + px) * 4;
        img.data[i] = v; img.data[i + 1] = v; img.data[i + 2] = v - 4; img.data[i + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    atoms(ctx, S, d, { color: 'rgba(200,40,40,.75)' });
};

// ── F · instruments: how machines already draw fields ──────────────────────

R.phosphor = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#0a0f0c');
    const levels = [-0.7, -0.45, -0.25, -0.1, 0.1, 0.25, 0.45, 0.7];
    for (const L of levels) {
        ctx.strokeStyle = 'rgba(120,255,170,.5)';
        ctx.shadowColor = 'rgba(120,255,170,.9)';
        ctx.shadowBlur = 6;                              // the tube's own bloom
        ctx.lineWidth = 0.9;
        ctx.beginPath();
        for (let y = 0; y < S; y += 2) for (let x = 0; x < S; x += 2) {
            const a = norm(g, bilinear(g, x / S, y / S)) - L;
            const b = norm(g, bilinear(g, (x + 2) / S, y / S)) - L;
            const c = norm(g, bilinear(g, x / S, (y + 2) / S)) - L;
            if (a * b < 0) { ctx.moveTo(x, y); ctx.lineTo(x + 2, y); }
            if (a * c < 0) { ctx.moveTo(x, y); ctx.lineTo(x, y + 2); }
        }
        ctx.stroke();
    }
    ctx.shadowBlur = 0;
    for (let y = 0; y < S; y += 3) {                     // scanlines
        ctx.fillStyle = 'rgba(0,0,0,.22)'; ctx.fillRect(0, y, S, 1);
    }
    atoms(ctx, S, d, { color: 'rgba(190,255,215,.6)' });
};

R.schlieren = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#8a8a86');
    // a knife-edge in the optical path turns a REFRACTIVE GRADIENT into
    // brightness — the field's derivative, imaged, as in a wind tunnel. The
    // knife cuts ONE axis, which is why the picture has a direction the
    // molecule does not: that anisotropy is the instrument, not the specimen.
    eachPixel(ctx, S, g, (n, px, py) => {
        const [gx] = gradN(g, px, py, S);
        const v = 138 + Math.max(-1.6, Math.min(1.6, gx)) * 66;
        return [v, v, v * 0.99];
    });
    atoms(ctx, S, d, { color: 'rgba(20,20,20,.45)' });
};

R.fringes = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#101018');
    // interferometry: equal phase, not equal value — every fringe is one
    // wavelength of optical path, so the SPACING is the gradient
    eachPixel(ctx, S, g, n => {
        const phase = Math.cos(n * 22);
        const v = (phase * 0.5 + 0.5);
        return [v * 190 + 16, v * 200 + 18, v * 240 + 30];
    });
    atoms(ctx, S, d, { color: 'rgba(255,255,255,.6)' });
};

R.radar = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#04120c');
    const cx = S / 2, cy = S / 2;
    for (let a = 0; a < 6.2832; a += 0.006) {
        for (let r = 0; r < S / 2; r += 1) {
            const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
            const n = Math.abs(norm(g, bilinear(g, x / S, y / S)));
            if (n < 0.08) continue;
            const decay = 1 - r / (S / 2) * 0.35;        // range attenuation
            ctx.fillStyle = `rgba(90,240,150,${n * 0.5 * decay})`;
            ctx.fillRect(x, y, 1.2, 1.2);
        }
    }
    ctx.strokeStyle = 'rgba(90,240,150,.22)';
    for (let r = S / 8; r < S / 2; r += S / 8) {
        ctx.beginPath(); ctx.arc(cx, cy, r, 0, 6.2832); ctx.stroke();
    }
    atoms(ctx, S, d, { color: 'rgba(200,255,220,.55)' });
};

R.autoradiograph = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#0d0d0c');
    // film exposed by emission: response is LOGARITHMIC and grainy, and the
    // grain is not decoration — it is what tells you the exposure was finite
    let seed = 29;
    const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
    eachPixel(ctx, S, g, n => {
        const e = Math.log1p(Math.abs(n) * 9) / Math.log(10);
        const grain = (rnd() - 0.5) * 26;
        const v = Math.max(0, Math.min(255, e * 232 + grain));
        return [v, v * 0.99, v * 0.94];
    });
    atoms(ctx, S, d, { color: 'rgba(255,255,255,.28)' });
};

// ── G · the ones that argue with the others ────────────────────────────────

R.negative = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#1a1a18');
    // ONLY the neutral region is drawn. The claim: a chemist is looking for
    // where the field is NOT, and every other language buries that in white.
    eachPixel(ctx, S, g, n => {
        const flat = 1 - Math.min(1, Math.abs(n) * 5);
        return [26 + flat * 214, 26 + flat * 212, 24 + flat * 200];
    });
    atoms(ctx, S, d, { color: 'rgba(255,255,255,.5)' });
};

R.uncertainty = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f7f6f2');
    // VALUE and CONFIDENCE on separate channels: hue/lightness carry the value,
    // SHARPNESS carries how well it is known. Each pixel averages K samples
    // drawn from a disc whose radius grows where the claim is weak, so the
    // picture dissolves exactly where it should not be trusted.
    //
    // The first version drew jittered dots with acceptance p = |n|. Under the
    // transfer function |n| is near 1 almost everywhere, so it accepted almost
    // every dot and rendered uniform confetti — a tile that looked like noise
    // because it WAS noise. A stochastic mark whose probability saturates has
    // stopped encoding anything, and it fails looking busy rather than blank,
    // which is why it survived a glance.
    const K = 12;
    let seed = 3;
    const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
    const neg = [176, 96, 102], pos = [78, 112, 174], paper = [247, 246, 242];
    eachPixel(ctx, S, g, (n0, px, py) => {
        // confidence: the repo's measured +-25% on the classical field, plus a
        // floor that falls away with signal — a small number is a weak claim
        const conf = Math.min(1, Math.abs(n0) / 0.55);
        const rad = (1 - conf) * 0.085 + 0.004;           // in frame fractions
        let acc = 0;
        for (let k = 0; k < K; k++) {
            const a = rnd() * 6.2832, rr = Math.sqrt(rnd()) * rad;
            acc += norm(g, bilinear(g,
                Math.max(0, Math.min(1, px / (S - 1) + Math.cos(a) * rr)),
                Math.max(0, Math.min(1, py / (S - 1) + Math.sin(a) * rr))));
        }
        const n = acc / K;
        return mix(paper, n < 0 ? neg : pos, Math.abs(n) * 0.92);
    });
    atoms(ctx, S, d);
};

R.threshold = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f7f6f2');
    // one decision, drawn: above or below the contour a chemist actually acts
    // on. No gradient at all — the honest picture of a binary judgement.
    eachPixel(ctx, S, g, n => {
        if (n > 0.28) return [74, 111, 168];
        if (n < -0.28) return [168, 92, 98];
        return [247, 246, 242];
    });
    ctx.strokeStyle = 'rgba(26,26,24,.4)'; ctx.lineWidth = 0.6;
    ctx.strokeRect(0.5, 0.5, S - 1, S - 1);
    atoms(ctx, S, d);
};

R.duotoneDepth = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#f2efe9');
    // ink density only — no hue carries sign. Sign is carried by MARK SHAPE:
    // horizontal strokes for negative, vertical for positive. Colourblind-safe
    // by construction rather than by palette choice.
    // pitch and stroke length are set from the TILE, not from a constant: at
    // 3px on a 232px tile these strokes were sub-pixel once the grid widened
    // to 330, and the tile rendered as a faint grey haze with no readable sign.
    const P = Math.max(4, Math.round(S / 58));
    const L = P * 0.62;
    for (let y = P; y < S; y += P) for (let x = P; x < S; x += P) {
        const n = norm(g, bilinear(g, x / S, y / S));
        const a = Math.abs(n);
        if (a < 0.07) continue;
        ctx.strokeStyle = `rgba(26,26,24,${0.12 + a * 0.8})`;
        ctx.lineWidth = 0.6 + a * 0.9;
        ctx.beginPath();
        if (n < 0) { ctx.moveTo(x - L, y); ctx.lineTo(x + L, y); }
        else { ctx.moveTo(x, y - L); ctx.lineTo(x, y + L); }
        ctx.stroke();
    }
    atoms(ctx, S, d);
};

R.isolineOnly = (ctx, S, d) => {
    const g = grid(d);
    clear(ctx, S, '#fcfbf8');
    // exactly ONE contour, at the value the panel actually renders. Everything
    // else in this page is a way of showing all the values at once; this asks
    // whether the reader ever needed more than the one they act on.
    ctx.strokeStyle = '#1a1a18'; ctx.lineWidth = 1.6;
    ctx.beginPath();
    for (const L of [-0.28, 0.28]) {
        for (let y = 0; y < S; y += 1) for (let x = 0; x < S; x += 1) {
            const a = norm(g, bilinear(g, x / S, y / S)) - L;
            const b = norm(g, bilinear(g, (x + 1) / S, y / S)) - L;
            const c = norm(g, bilinear(g, x / S, (y + 1) / S)) - L;
            if (a * b < 0) { ctx.moveTo(x, y); ctx.lineTo(x + 1, y); }
            if (a * c < 0) { ctx.moveTo(x, y); ctx.lineTo(x, y + 1); }
        }
    }
    ctx.stroke();
    atoms(ctx, S, d, { color: 'rgba(26,26,24,.75)', r: 2 });
};

window.FIELD_RENDERERS = R;
