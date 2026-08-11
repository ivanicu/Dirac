/**
 * 2D ligand depiction via RDKit-JS, with atom highlighting and click-to-atom.
 *
 * Pipeline: V2000 molfile (from mol*) → RDKit `get_svg_with_highlights` with
 * `forceCoords: true` → SVG string + atom-position table parsed from <metadata>.
 *
 * Atom-index contract: molfile atom i (1-based in V2000) == RDKit atom i-1
 * (0-based internal) == `<rdkit:atom idx="i">` == `.atom-N` where N = i-1.
 * Verified by reading RDKit C++ MolBlockToMol + the SVG metadata emitter.
 *
 * Click handling: nearest-neighbor over parsed atom positions, robust to CSS
 * scaling via `getScreenCTM().inverse()`.
 */

import { getRDKit } from './semantic-chemistry-rdkit';

export interface AtomHighlight {
    /** 0-based atom index in the molfile iteration order. */
    atomIndex: number;
    /** Hex color, e.g. "#5fd0c8". */
    color: string;
    /** Highlight opacity 0..1 (default 0.5). */
    alpha?: number;
}

export interface BondHighlight {
    bondIndex: number;
    color: string;
    alpha?: number;
}

export interface DepictOptions {
    width?: number;
    height?: number;
    atomHighlights?: AtomHighlight[];
    bondHighlights?: BondHighlight[];
    /** Show numeric atom indices on the diagram. Default false. */
    showAtomIndices?: boolean;
}

export interface AtomPosition {
    /** 0-based atom index. */
    idx: number;
    /** SVG user-space x. */
    x: number;
    /** SVG user-space y. */
    y: number;
}

export interface DepictionResult {
    svgString: string;
    atomPositions: AtomPosition[];
}

function hexToRgbaFloat(hex: string, alpha = 0.5): number[] {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16) / 255;
    const g = parseInt(h.slice(2, 4), 16) / 255;
    const b = parseInt(h.slice(4, 6), 16) / 255;
    return [r, g, b, alpha];
}

function parseAtomPositions(svgString: string): AtomPosition[] {
    const doc = new DOMParser().parseFromString(svgString, 'image/svg+xml');

    // Preferred path: <metadata><rdkit:atom idx drawing-x drawing-y/></metadata>
    // Up to RDKit-JS 2025.03.4 this is not emitted regardless of includeMetadata,
    // so we fall back to bond-path centroid inference below.
    const directEls = doc.getElementsByTagName('rdkit:atom');
    if (directEls.length > 0) {
        const out: AtomPosition[] = [];
        for (let i = 0; i < directEls.length; i++) {
            const el = directEls[i];
            const idx1Based = parseInt(el.getAttribute('idx') || '-1', 10);
            if (idx1Based < 1) continue;
            const x = parseFloat(el.getAttribute('drawing-x') || 'NaN');
            const y = parseFloat(el.getAttribute('drawing-y') || 'NaN');
            if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
            out.push({ idx: idx1Based - 1, x, y });
        }
        if (out.length > 0) return out;
    }

    // Fallback: parse <path class="bond-N atom-I atom-J" d="M x1,y1 L x2,y2">.
    // For each atom index, average all bond endpoints where it appears. The M
    // endpoint corresponds to the first atom in the class list, L to the second.
    // For atoms of degree >= 2 the centroid is very close to the true center;
    // for terminal atoms (degree 1) it is the single bond endpoint, slightly
    // offset by RDKit's bond shortening — still clickable with a 20px threshold.
    const endpointsByAtom = new Map<number, Array<{ x: number; y: number }>>();
    const paths = doc.getElementsByTagName('path');
    for (let i = 0; i < paths.length; i++) {
        const path = paths[i];
        const cls = path.getAttribute('class') || '';
        if (!cls.includes('bond-') || !cls.includes('atom-')) continue;
        const atomRefs = cls.match(/atom-(\d+)/g);
        if (!atomRefs || atomRefs.length < 2) continue;
        const a1 = parseInt(atomRefs[0].slice(5), 10);
        const a2 = parseInt(atomRefs[1].slice(5), 10);
        const dAttr = path.getAttribute('d') || '';
        const m = dAttr.match(/M\s*(-?[\d.]+),\s*(-?[\d.]+)\s+L\s*(-?[\d.]+),\s*(-?[\d.]+)/);
        if (!m) continue;
        const mx = parseFloat(m[1]);
        const my = parseFloat(m[2]);
        const lx = parseFloat(m[3]);
        const ly = parseFloat(m[4]);
        if (!Number.isFinite(mx) || !Number.isFinite(lx)) continue;
        if (!endpointsByAtom.has(a1)) endpointsByAtom.set(a1, []);
        if (!endpointsByAtom.has(a2)) endpointsByAtom.set(a2, []);
        endpointsByAtom.get(a1)!.push({ x: mx, y: my });
        endpointsByAtom.get(a2)!.push({ x: lx, y: ly });
    }

    const out: AtomPosition[] = [];
    for (const [idx, pts] of endpointsByAtom) {
        if (pts.length === 0) continue;
        let sx = 0;
        let sy = 0;
        for (const p of pts) { sx += p.x; sy += p.y; }
        out.push({ idx, x: sx / pts.length, y: sy / pts.length });
    }
    out.sort((a, b) => a.idx - b.idx);
    return out;
}

export class LigandDepiction {
    /**
     * Render a ligand molfile to an SVG string sized for the panel.
     * Returns null if RDKit fails to parse the molfile.
     *
     * Depiction strategy (verified on 1CBS REA, 4HHB HEM, 2POR C8E):
     *   1. Use `get_new_coords(true)` — CoordGen library — instead of RDKit's
     *      built-in depicter. CoordGen handles macrocycles and fused ring
     *      systems (HEM porphyrin, C8E detergent) without collapsing atoms.
     *   2. Render at 2× density (760×500). The SVG viewBox keeps it crisp at
     *      any rendered size; CSS max-width:100% scales for the panel.
     *
     * Empirical results on 4HHB HEM (43 atoms, 4 pyrrole rings):
     *   - forceCoords:true           → 11 overlapping atom pairs (min bond 0.4px)
     *   - RDKit depicter + 340x220   → 11 pairs
     *   - CoordGen + 340x220         → 4 pairs
     *   - RDKit depicter + 760x500   → 2 pairs
     *   - CoordGen + 760x500         → 0 pairs ✓
     */
    static async depict(molfile: string, options: DepictOptions = {}): Promise<DepictionResult | null> {
        const RDKit = await getRDKit();
        const mol = RDKit.get_mol(molfile);
        if (!mol || !mol.is_valid()) return null;

        try {
            // Mutate in place with CoordGen. Note: get_new_coords + re-parse
            // produces different (worse) output than set_new_coords on the
            // same mol — verified empirically. Always mutate.
            try {
                mol.set_new_coords(true);
            } catch { /* fall back to whatever coords existed */ }

            // 2× density for crisp down-scaling + better atom separation.
            const width = options.width ?? 760;
            const height = options.height ?? 500;

            const highlightAtomColors: Record<string, number[]> = {};
            const atoms: number[] = [];
            for (const h of options.atomHighlights ?? []) {
                atoms.push(h.atomIndex);
                highlightAtomColors[String(h.atomIndex)] = hexToRgbaFloat(h.color, h.alpha ?? 0.5);
            }
            const highlightBondColors: Record<string, number[]> = {};
            const bonds: number[] = [];
            for (const b of options.bondHighlights ?? []) {
                bonds.push(b.bondIndex);
                highlightBondColors[String(b.bondIndex)] = hexToRgbaFloat(b.color, b.alpha ?? 0.5);
            }

            const details = {
                width,
                height,
                atoms,
                bonds,
                highlightAtomColors,
                highlightBondColors,
                addAtomIndices: options.showAtomIndices ?? false,
                includeMetadata: true,
                includeAtomTags: true,
                continuousHighlight: true,
                bondLineWidth: 2,
                padding: 0.08,
                fixedFontSize: 12,
                additionalAtomLabelPadding: 0.2,
            };

            const svgString = mol.get_svg_with_highlights(JSON.stringify(details));
            if (!svgString) return null;

            const atomPositions = parseAtomPositions(svgString);
            return { svgString, atomPositions };
        } finally {
            mol.delete();
        }
    }

    /**
     * Given a click event on the rendered SVG, return the 0-based atom index
     * or -1 if the click was outside any atom's neighborhood.
     */
    static getAtomIndexFromClick(
        svgEl: SVGSVGElement,
        atomPositions: AtomPosition[],
        clientX: number,
        clientY: number,
        thresholdPx = 20,
    ): number {
        if (atomPositions.length === 0) return -1;
        const pt = svgEl.createSVGPoint();
        pt.x = clientX;
        pt.y = clientY;
        const ctm = svgEl.getScreenCTM();
        if (!ctm) return -1;
        const p = pt.matrixTransform(ctm.inverse());
        let best = -1;
        let bestDist = Infinity;
        for (const a of atomPositions) {
            const dx = a.x - p.x;
            const dy = a.y - p.y;
            const d = dx * dx + dy * dy;
            if (d < bestDist) {
                bestDist = d;
                best = a.idx;
            }
        }
        return bestDist <= thresholdPx * thresholdPx ? best : -1;
    }
}
