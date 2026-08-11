/**
 * Inline SVG plotting for the ligand-physics panel.
 *
 * SVG built by hand rather than a charting library, for one reason that is not
 * taste: DESIGN.md forbids external assets, and a chart library would bring its
 * own colour defaults — which is precisely how the field colours ended up as
 * literals no theme could reach. Every colour below is a `var(--token)`, so a
 * theme swap moves these plots with everything else.
 *
 * Small multiples, deliberately. A torsion profile is only meaningful next to
 * the OTHER rotors of the same molecule: one curve says "this bond costs 2
 * kcal/mol", six curves side by side say "the strain is all in the amide and
 * the rest of the molecule is relaxed", which is the sentence a chemist acts on.
 */

export interface TorsionProfilePoint { deg: number; kcal: number }

export interface TorsionRow {
    atom_indices: number[];
    elements: string[];
    observed_deg: number;
    min_energy_deg: number;
    local_strain_kcal: number;
    barrier_kcal: number;
    verdict: string;
    scan_unconverged_points: number;
    profile: [number, number][];
}

const W = 150;
const H = 58;
const PAD_L = 4;
const PAD_R = 4;
const PAD_T = 6;
const PAD_B = 12;

function esc(s: string): string {
    return s.replace(/[&<>"]/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
}

/**
 * One rotor's relaxed scan.
 *
 * The observed angle is drawn as a vertical rule with a dot ON the curve, not
 * as a separate annotation: the whole question is where this conformer sits
 * relative to the minimum, and a legend that makes the reader match a colour to
 * a caption has already lost that comparison.
 */
export function torsionSparkline(row: TorsionRow): string {
    const pts = row.profile;
    if (!pts || pts.length < 2) return '';

    const xs = pts.map(p => p[0]);
    const ys = pts.map(p => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const y0 = Math.min(...ys), y1 = Math.max(...ys);
    // A flat profile must not be stretched to fill the box: a rotor with a 0.05
    // kcal/mol ripple would then look exactly like one with a 12 kcal/mol
    // barrier. Floor the range so flat reads as flat.
    const yRange = Math.max(y1 - y0, 1.0);

    const sx = (deg: number) => PAD_L + ((deg - x0) / (x1 - x0 || 1)) * (W - PAD_L - PAD_R);
    const sy = (k: number) => PAD_T + (1 - (k - y0) / yRange) * (H - PAD_T - PAD_B);

    const path = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join('');

    // Where the observed conformer sits, interpolated onto the curve.
    const obs = row.observed_deg;
    let obsY = row.local_strain_kcal + y0;
    for (let i = 1; i < pts.length; i++) {
        if ((pts[i - 1][0] <= obs && obs <= pts[i][0])) {
            const t = (obs - pts[i - 1][0]) / ((pts[i][0] - pts[i - 1][0]) || 1);
            obsY = pts[i - 1][1] + t * (pts[i][1] - pts[i - 1][1]);
            break;
        }
    }

    const label = esc(row.elements.join('–'));
    const strained = row.local_strain_kcal >= 1.0;

    return `<svg class="torsion-spark" viewBox="0 0 ${W} ${H}" role="img"
        aria-label="${label} torsion profile, observed ${obs.toFixed(0)} degrees,
        strain ${row.local_strain_kcal.toFixed(1)} kcal per mole">
      <line x1="${PAD_L}" y1="${(H - PAD_B).toFixed(1)}" x2="${W - PAD_R}" y2="${(H - PAD_B).toFixed(1)}"
            stroke="var(--border)" stroke-width="1"/>
      <path d="${path}" fill="none" stroke="var(--text-2)" stroke-width="1.25"/>
      <line x1="${sx(obs).toFixed(1)}" y1="${PAD_T}" x2="${sx(obs).toFixed(1)}" y2="${(H - PAD_B).toFixed(1)}"
            stroke="${strained ? 'var(--high)' : 'var(--text-3)'}" stroke-width="1" stroke-dasharray="2 2"/>
      <circle cx="${sx(obs).toFixed(1)}" cy="${sy(obsY).toFixed(1)}" r="2.6"
              fill="${strained ? 'var(--high)' : 'var(--ok, var(--text-2))'}"/>
      <text x="${PAD_L}" y="${H - 2}" font-size="7" fill="var(--text-3)">${x0.toFixed(0)}°</text>
      <text x="${W - PAD_R}" y="${H - 2}" font-size="7" fill="var(--text-3)" text-anchor="end">${x1.toFixed(0)}°</text>
      <text x="${W / 2}" y="${H - 2}" font-size="7" fill="var(--text-3)" text-anchor="middle"
            >${(y1 - y0).toFixed(1)} kcal span</text>
    </svg>`;
}

/**
 * The whole rotor set.
 *
 * `total_strain_kcal` is rendered SEPARATELY and never as a sum of the rows,
 * because it is not one: torsions are coupled and adding them double-counts.
 * The backend says so in its own meta note and the layout has to agree with it,
 * or a reader will do the addition themselves and get a different number than
 * the one printed beside it.
 */
export function torsionPanel(rows: TorsionRow[], total: number, verdict: string,
    meta: Record<string, unknown>): string {
    if (!rows.length) {
        return `<div class="phys-empty">No rotatable bonds — this molecule has no
            torsional strain to report. That is zero because there is nothing to
            scan, which is not the same as zero because nothing is strained.</div>`;
    }
    const cards = rows.map(r => {
        const strained = r.local_strain_kcal >= 1.0;
        return `<div class="torsion-card" data-strained="${strained}">
          <div class="torsion-head">
            <span class="torsion-atoms">${esc(r.elements.join('–'))}</span>
            <span class="torsion-verdict" data-verdict="${esc(r.verdict)}">${esc(r.verdict)}</span>
          </div>
          ${torsionSparkline(r)}
          <div class="torsion-nums">
            <span>obs ${r.observed_deg.toFixed(0)}°</span>
            <span>min ${r.min_energy_deg.toFixed(0)}°</span>
            <span>strain ${r.local_strain_kcal.toFixed(1)}</span>
            <span>barrier ${r.barrier_kcal.toFixed(1)}</span>
          </div>
        </div>`;
    }).join('');

    const note = String(meta.note ?? '');
    return `
      <div class="phys-headline">
        <span class="phys-big">${total.toFixed(1)}</span>
        <span class="phys-unit">kcal/mol total strain</span>
        <span class="phys-verdict" data-verdict="${esc(verdict)}">${esc(verdict)}</span>
      </div>
      <div class="phys-caveat">${esc(note)}</div>
      <div class="torsion-grid">${cards}</div>
      <div class="phys-method">${esc(String(meta.method ?? ''))} ·
        ${esc(String(meta.n_scanned ?? '?'))}/${esc(String(meta.n_rotatable_bonds ?? '?'))} rotors scanned ·
        hydrogens ${meta.hydrogens_relaxed ? 'relaxed first' : 'NOT relaxed'} ·
        ${esc(String(meta.seconds ?? '?'))} s</div>`;
}
