/**
 * Bond-level graph facts for a single molecule.
 *
 * A structure diagram is a graph drawing whose vertices were given every channel —
 * element, charge, aromaticity, stereo, H-bond role, partial charge, pharmacophore
 * type — and whose edges were given one, line multiplicity. The bond is the largest
 * unused surface in molecular depiction, and most of what belongs on it is graph
 * theory over a structure already in memory: which bonds hold the molecule together,
 * how much mass each one swings, and where the molecule's own natural bisection lies.
 * No conformer, no force field, no model, no fit.
 *
 * Deliberately independent of any renderer and of mol* itself: it takes a molfile,
 * asks RDKit for the graph, and returns numbers. The facet that draws it is the only
 * thing that knows about the DOM.
 */

import { getRDKit } from './semantic-chemistry-rdkit';

const ROTATABLE_SMARTS = '[!$(*#*)&!D1]-&!@[!$(*#*)&!D1]';
const DONOR_SMARTS = '[$([N;!H0;v3]),$([N;!H0;+1;v4]),$([O,S;H1;+0]),$([n;H1;+0])]';
const ACCEPTOR_SMARTS = '[$([O,S;H1;v2;!$(*-*=[O,N,P,S])]),$([O,S;H0;v2]),$([O,S;-]),$([N;v3;!$(N-*=[O,N,P,S])]),$([nH0,o,s;+0])]';

export interface BondAtlasAtom {
    i: number;
    x: number; y: number;
    el: string;
    charge: number;
    implicitH: number;
    aromatic: boolean;
    degree: number;
    ringCount: number;
    donor: boolean;
    acceptor: boolean;
    /** Topological equivalence class; atoms sharing one are indistinguishable to the graph. */
    symClass: number;
    /** How many atoms share this class. Greater than one means substituting here is ONE decision. */
    symSize: number;
}

export interface BondAtlasBond {
    k: number;
    a: number; b: number;
    order: number;
    aromatic: boolean;
    /** Smallest ring this bond belongs to, or null when it is acyclic. */
    ringSize: number | null;
    rotatable: boolean;
    /** Cutting it disconnects the molecule. */
    bridge: boolean;
    /** Atom counts on either side of the cut, for a bridge. */
    split: [number, number] | null;
    /** Heavy atoms that move when this torsion turns; zero unless the bond can turn. */
    swing: number;
    /** 1.0 when the cut halves the molecule exactly. */
    balance: number;
}

export interface BondAtlas {
    atoms: BondAtlasAtom[];
    bonds: BondAtlasBond[];
    /** Index of the bond whose cut splits the molecule most evenly, or null if acyclic-free. */
    waist: number | null;
    nHeavy: number;
    counts: { bridges: number, rotatable: number, ringBonds: number, symClasses: number, equivalentAtoms: number };
}

/** Bridges (cut edges) by iterative Tarjan low-link. */
function findBridges(n: number, adj: { v: number, e: number }[][]): Set<number> {
    const disc = new Array<number>(n).fill(-1);
    const low = new Array<number>(n).fill(0);
    const bridges = new Set<number>();
    let timer = 0;
    for (let s = 0; s < n; s++) {
        if (disc[s] !== -1) continue;
        disc[s] = low[s] = timer++;
        const stack: { u: number, parentEdge: number, i: number }[] = [{ u: s, parentEdge: -1, i: 0 }];
        while (stack.length) {
            const fr = stack[stack.length - 1];
            if (fr.i < adj[fr.u].length) {
                const { v, e } = adj[fr.u][fr.i++];
                if (e === fr.parentEdge) continue;
                if (disc[v] === -1) {
                    disc[v] = low[v] = timer++;
                    stack.push({ u: v, parentEdge: e, i: 0 });
                } else low[fr.u] = Math.min(low[fr.u], disc[v]);
            } else {
                stack.pop();
                if (stack.length) {
                    const p = stack[stack.length - 1];
                    low[p.u] = Math.min(low[p.u], low[fr.u]);
                    if (low[fr.u] > disc[p.u]) bridges.add(fr.parentEdge);
                }
            }
        }
    }
    return bridges;
}

function sideSize(n: number, adj: { v: number, e: number }[][], start: number, skip: number): number {
    const seen = new Uint8Array(n);
    const st = [start];
    seen[start] = 1;
    let c = 0;
    while (st.length) {
        const u = st.pop()!;
        c++;
        for (const { v, e } of adj[u]) if (e !== skip && !seen[v]) { seen[v] = 1; st.push(v); }
    }
    return c;
}

/**
 * Topological equivalence by Weisfeiler-Lehman refinement. Bond order is part of the
 * initial label: without it two atoms differing only in which neighbour they are
 * double-bonded to merge into one class, which would understate the number of
 * distinct positions a chemist has to decide about.
 */
function symmetryClasses(n: number, seed: string[], adj: { v: number, e: number }[][], bondLabel: (e: number) => string): number[] {
    let lab = seed.slice();
    for (let round = 0; round < n; round++) {
        const next = lab.map((l, i) =>
            l + '(' + adj[i].map(({ v, e }) => bondLabel(e) + lab[v]).sort().join(',') + ')');
        const m = new Map<string, number>();
        const compact = next.map(s => { if (!m.has(s)) m.set(s, m.size); return String(m.get(s)); });
        if (compact.join() === lab.join()) break;
        lab = compact;
    }
    const m = new Map<string, number>();
    return lab.map(l => { if (!m.has(l)) m.set(l, m.size); return m.get(l)!; });
}

/**
 * Compute the atlas for a molfile. Returns null when RDKit cannot parse it, which is
 * the same contract the rest of the chemistry substrate uses.
 */
export async function computeBondAtlas(molfile: string): Promise<BondAtlas | null> {
    const RDKit = await getRDKit();
    const mol = RDKit.get_mol(molfile);
    if (!mol || !mol.is_valid()) { mol?.delete(); return null; }

    try {
        try { mol.set_new_coords(true); } catch { /* keep whatever coords the molfile carried */ }
        const block = mol.get_molblock();
        const j = JSON.parse(mol.get_json()).molecules[0];
        const ext = (j.extensions || []).find((e: any) => e.name === 'rdkitRepresentation') || {};
        const aromAtoms = new Set<number>(ext.aromaticAtoms || []);
        const aromBonds = new Set<number>(ext.aromaticBonds || []);
        const rings: number[][] = ext.atomRings || [];

        const lines = block.split('\n');
        const nA = parseInt(lines[3].slice(0, 3), 10);
        const atoms: BondAtlasAtom[] = [];
        for (let i = 0; i < nA; i++) {
            const L = lines[4 + i];
            atoms.push({
                i, x: parseFloat(L.slice(0, 10)), y: -parseFloat(L.slice(10, 20)),
                el: L.slice(31, 34).trim(),
                charge: j.atoms[i].chg ?? 0,
                implicitH: j.atoms[i].impHs ?? 0,
                aromatic: aromAtoms.has(i),
                degree: 0, ringCount: 0, donor: false, acceptor: false, symClass: 0, symSize: 1,
            });
        }
        const bonds: BondAtlasBond[] = j.bonds.map((b: any, k: number) => ({
            k, a: b.atoms[0], b: b.atoms[1], order: b.bo ?? 1, aromatic: aromBonds.has(k),
            ringSize: null, rotatable: false, bridge: false, split: null, swing: 0, balance: 0,
        }));

        const adj: { v: number, e: number }[][] = atoms.map(() => []);
        for (const b of bonds) { adj[b.a].push({ v: b.b, e: b.k }); adj[b.b].push({ v: b.a, e: b.k }); }

        for (const r of rings) {
            for (let i = 0; i < r.length; i++) {
                const a = r[i], c = r[(i + 1) % r.length];
                const bond = bonds.find(x => (x.a === a && x.b === c) || (x.a === c && x.b === a));
                if (bond && (bond.ringSize === null || r.length < bond.ringSize)) bond.ringSize = r.length;
            }
            for (const a of r) atoms[a].ringCount++;
        }

        // get_substruct_matches returns a non-array when nothing matches, and destructuring
        // that throws — benzene has no rotatable bond and would have crashed the caller.
        const matches = (sma: string) => {
            const q = RDKit.get_qmol(sma);
            if (!q) return [];
            let out: any = [];
            try { out = JSON.parse(mol.get_substruct_matches(q) || '[]'); } catch { out = []; }
            q.delete();
            return Array.isArray(out) ? out : [];
        };
        const rotSet = new Set<number>();
        for (const m of matches(ROTATABLE_SMARTS)) for (const bk of m.bonds || []) rotSet.add(bk);
        const donors = new Set<number>();
        for (const m of matches(DONOR_SMARTS)) for (const a of m.atoms || []) donors.add(a);
        const acceptors = new Set<number>();
        for (const m of matches(ACCEPTOR_SMARTS)) for (const a of m.atoms || []) acceptors.add(a);

        const bridges = findBridges(atoms.length, adj);
        const N = atoms.length;
        for (const b of bonds) {
            b.rotatable = rotSet.has(b.k);
            b.bridge = bridges.has(b.k);
            if (b.bridge) {
                const s = sideSize(N, adj, b.a, b.k);
                b.split = [s, N - s];
                // What swings is defined only for a bond that can turn: giving a methyl or a
                // C=O a swing mass made a rigid molecule render as maximally flexible.
                b.swing = b.rotatable ? Math.min(s, N - s) : 0;
                b.balance = Math.min(s, N - s) / (N / 2);
            }
        }
        const waistBond = bonds.filter(b => b.bridge).sort((x, y) => y.balance - x.balance)[0];

        const bondLabel = (e: number) => `${bonds[e].order}${bonds[e].aromatic ? 'a' : ''}`;
        const seed = atoms.map(a => `${a.el}|${a.charge}|${a.aromatic ? 1 : 0}|${a.implicitH}`);
        const sym = symmetryClasses(N, seed, adj, bondLabel);
        const symSize = new Map<number, number>();
        for (const c of sym) symSize.set(c, (symSize.get(c) || 0) + 1);
        atoms.forEach((a, i) => {
            a.symClass = sym[i];
            a.symSize = symSize.get(sym[i])!;
            a.degree = adj[i].length;
            a.donor = donors.has(i);
            a.acceptor = acceptors.has(i);
        });

        return {
            atoms, bonds, waist: waistBond ? waistBond.k : null, nHeavy: N,
            counts: {
                bridges: bonds.filter(b => b.bridge).length,
                rotatable: bonds.filter(b => b.rotatable).length,
                ringBonds: bonds.filter(b => b.ringSize !== null).length,
                symClasses: symSize.size,
                equivalentAtoms: [...symSize.values()].filter(v => v > 1).reduce((s, v) => s + v, 0),
            },
        };
    } finally {
        mol.delete();
    }
}
