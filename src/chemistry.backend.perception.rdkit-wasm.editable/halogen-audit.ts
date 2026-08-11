/**
 * Halogen-bond audit: is this halogen doing a directional job, or is it a slide?
 *
 * Two halves of this question are each well served and have never been joined. The
 * electronics half — how deep the σ-hole actually is — is what the QM MEP endpoint in
 * this repo already computes, on an isolated ligand with no protein in sight. The
 * geometry half is what every structure profiler computes, with no electronics at all.
 *
 * PLIP is the free profiler most people actually run, and its criteria are read from
 * its own source rather than described from memory:
 *
 *     plip/basic/config.py:56-59   HALOGEN_DIST_MAX 4.0 Å · DON_ANGLE 165° ·
 *                                  ACC_ANGLE 120° · ANGLE_DEV 30°
 *     plip/structure/preparation.py:780
 *         if group == 'halocarbon' and atom.atomicnum in [9, 17, 35, 53]
 *
 * Atomic number 9 is fluorine. Fluorine has no σ-hole in any ordinary organic
 * environment — it is too electronegative and not polarisable enough — so a purely
 * geometric criterion will happily call a halogen bond on a C–F contact, and does.
 * That is why this module reports the geometric verdict SEPARATELY and never silently:
 * a chemist should be able to see the row that says the standard tool would have
 * called this, next to the row that says the electronics do not support it.
 *
 * The valuable answer here is usually the negative one. A halogen that occupies space
 * is a fine substituent; a halogen defended for eleven months as a halogen bond while
 * it pays only lipophilicity is a compound nobody kills.
 */

import { Vec3 } from '../mol-math/linear-algebra';

/** PLIP's own constants, so the comparison row is theirs and not a paraphrase. */
export const PLIP = {
    DIST_MAX: 4.0,
    DON_ANGLE: 165,
    ACC_ANGLE: 120,
    ANGLE_DEV: 30,
    /** Atomic numbers PLIP accepts as halogen-bond donors. 9 is fluorine. */
    DONOR_Z: [9, 17, 35, 53],
} as const;

/**
 * Electronic thresholds. These are OURS, they are conventions rather than
 * measurements, and they are named here so a reader can disagree with a number
 * instead of with a verdict.
 *
 * V_S,max ≥ +12 kcal/mol as "a σ-hole worth invoking": chlorobenzene sits near +18
 * and fluorobenzene near −19 in this repo's own def2-SVP numbers, so the bar sits
 * between the element that does this and the element that cannot. The MEP endpoint
 * declares 25% absolute uncertainty, so this bar is soft by ±3 and any compound
 * within that band should read as MARGINAL rather than as a decision.
 */
export const ELECTRONIC = {
    VS_MAX_REAL: 12,
    /** Half-angle of the cone a σ-hole actually projects into. */
    ON_AXIS_DEG: 25,
    /** Cone drawn for the eye; wider than the verdict window on purpose. */
    CONE_DEG: 30,
    CONE_LENGTH: 4.5,
} as const;

/**
 * UNMEASURED is a separate state from ABSENT on purpose. ABSENT asserts that there is no
 * positive cap on the axis — a finding. Not having run the QM yet is not a finding, and
 * folding the two together is how a "no" that nobody measured gets quoted as a result.
 */
export type HalogenVerdict = 'REAL' | 'MARGINAL' | 'DECORATIVE' | 'ABSENT' | 'MIS-CALLED' | 'UNMEASURED';

export interface PocketAtom {
    position: Vec3;
    element: string;
    /** Human label for the row: residue + atom name. */
    label: string;
    /** A covalently bonded neighbour, used for the X···A–Y angle. Optional. */
    neighbor?: Vec3;
}

export interface HalogenHit {
    label: string;
    element: string;
    distance: number;
    /** Angle between the C→X axis and X→A. 0° is dead on-axis. */
    offAxisDeg: number;
    /** C–X···A, the angle PLIP calls the donor angle. 180° is linear. */
    donorAngleDeg: number;
    /** X···A–Y where Y is the acceptor's neighbour, when one was supplied. */
    acceptorAngleDeg: number | null;
    plipWouldCall: boolean;
    /** Crystallographic water: shown, but never enough to make a verdict REAL. */
    isWater: boolean;
}

export interface HalogenAudit {
    element: string;
    /** V_S,max on the C–X extension, kcal/mol, from the QM endpoint. Null if not computed. */
    vsMax: number | null;
    /** V_S,max minus the belt minimum on the same atom — the quantity that means the
     *  same thing for an anion as for a neutral, which the bare value does not. */
    anisotropy: number | null;
    hits: HalogenHit[];
    nearestOffAxis: HalogenHit | null;
    verdict: HalogenVerdict;
    /** One sentence a chemist can paste into a slide without it becoming a lie. */
    reading: string;
    /** What PLIP alone would have reported, so the disagreement is visible. */
    plipVerdict: string;
    /** Permanently displayed scope. An instrument this good at producing physics is
     *  equally good at producing scope-free physics. */
    scope: string;
}

const angleBetween = (a: Vec3, b: Vec3) => {
    const na = Vec3.magnitude(a), nb = Vec3.magnitude(b);
    if (na < 1e-9 || nb < 1e-9) return NaN;
    return Math.acos(Math.max(-1, Math.min(1, Vec3.dot(a, b) / (na * nb)))) * 180 / Math.PI;
};

/**
 * @param carbon   the atom the halogen is bonded to
 * @param halogen  the halogen position
 * @param element  its element symbol
 * @param pocket   protein atoms already loaded in the scene
 * @param qm       V_S,max and anisotropy from the MEP endpoint, when it has been run
 */
export function auditHalogen(
    carbon: Vec3, halogen: Vec3, element: string, pocket: PocketAtom[],
    qm: { vsMax: number | null, anisotropy: number | null, basis?: string, method?: string } = { vsMax: null, anisotropy: null },
): HalogenAudit {
    const axis = Vec3.sub(Vec3(), halogen, carbon);          // C → X, extended is where the hole sits
    const hits: HalogenHit[] = [];

    for (const p of pocket) {
        if (!['N', 'O', 'S'].includes(p.element)) continue;   // Lewis bases only
        // A crystallographic water IS a Lewis base and is also the weakest thing to hang a
        // halogen-bond claim on: it is often placed by the refinement rather than observed,
        // and it moves. Kept in the hit table so the reader can see it, excluded from the
        // verdict so it cannot promote a contact to REAL on its own.
        const isWater = /^HOH|^WAT/.test(p.label);
        const toA = Vec3.sub(Vec3(), p.position, halogen);
        const distance = Vec3.magnitude(toA);
        if (distance > PLIP.DIST_MAX + 1.0) continue;
        const offAxisDeg = angleBetween(axis, toA);
        // The forward hemisphere, not the cone. The first version discarded anything past
        // 50 degrees and then reported "no Lewis base near the axis at all" for a pocket
        // whose acceptor sat at 68 degrees and 3.6 A — which is the single most useful
        // sentence this module can produce, thrown away by its own filter. The verdict
        // window stays at ON_AXIS_DEG; what widens is only what we are willing to SEE.
        if (offAxisDeg > 90) continue;
        const donorAngleDeg = 180 - offAxisDeg;               // C–X···A
        let acceptorAngleDeg: number | null = null;
        if (p.neighbor) {
            acceptorAngleDeg = angleBetween(
                Vec3.sub(Vec3(), halogen, p.position),
                Vec3.sub(Vec3(), p.neighbor, p.position));
        }
        const plipWouldCall =
            distance <= PLIP.DIST_MAX &&
            Math.abs(donorAngleDeg - PLIP.DON_ANGLE) <= PLIP.ANGLE_DEV &&
            (acceptorAngleDeg === null || Math.abs(acceptorAngleDeg - PLIP.ACC_ANGLE) <= PLIP.ANGLE_DEV);
        hits.push({ label: p.label, element: p.element, distance, offAxisDeg, donorAngleDeg, acceptorAngleDeg, plipWouldCall, isWater });
    }
    hits.sort((a, b) => a.offAxisDeg - b.offAxisDeg || a.distance - b.distance);

    const onAxis = hits.filter(h => !h.isWater && h.offAxisDeg <= ELECTRONIC.ON_AXIS_DEG && h.distance <= PLIP.DIST_MAX);
    const plipCalls = hits.filter(h => h.plipWouldCall);
    const holeIsReal = qm.vsMax !== null && qm.vsMax >= ELECTRONIC.VS_MAX_REAL;
    const holeIsMarginal = qm.vsMax !== null && qm.vsMax >= ELECTRONIC.VS_MAX_REAL * 0.75 && qm.vsMax < ELECTRONIC.VS_MAX_REAL;

    let verdict: HalogenVerdict;
    let reading: string;
    const nearest = hits[0] ?? null;

    if (qm.vsMax === null) {
        verdict = 'UNMEASURED';
        reading = nearest
            ? `No QM σ-hole computed yet, so the electronics are undecided — geometry alone cannot answer this. Nearest Lewis base: ${nearest.label}, ${nearest.distance.toFixed(2)} Å, ${nearest.offAxisDeg.toFixed(0)}° off the C–${element} axis. Run the MEP field for this ligand.`
            : 'No QM σ-hole computed yet, so the electronics are undecided, and there is no Lewis base near the axis either. Run the MEP field for this ligand.';
    } else if (qm.vsMax < 0) {
        verdict = 'ABSENT';
        reading = `${element} carries no positive cap on the C–${element} extension (V_S,max ${qm.vsMax.toFixed(0)} kcal/mol). There is no σ-hole here to bond with, whatever the geometry looks like.`;
    } else if (plipCalls.length && !holeIsReal && !holeIsMarginal) {
        verdict = 'MIS-CALLED';
        reading = `The geometric criteria pass and the electronics do not: V_S,max is only ${qm.vsMax!.toFixed(0)} kcal/mol. A profiler would report a halogen bond to ${plipCalls[0].label}; this contact is van der Waals wearing a name.`;
    } else if (onAxis.length && holeIsReal) {
        verdict = 'REAL';
        reading = `σ-hole ${qm.vsMax!.toFixed(0)} kcal/mol aimed at ${onAxis[0].label}, ${onAxis[0].distance.toFixed(2)} Å, ${onAxis[0].offAxisDeg.toFixed(0)}° off the C–${element} axis.`;
    } else if (onAxis.length && holeIsMarginal) {
        verdict = 'MARGINAL';
        reading = `σ-hole ${qm.vsMax!.toFixed(0)} kcal/mol is inside the endpoint's own ±25% band of the ${ELECTRONIC.VS_MAX_REAL} kcal/mol bar, aimed at ${onAxis[0].label}. Treat the direction as suggestive and the magnitude as undecided.`;
    } else {
        verdict = 'DECORATIVE';
        reading = nearest
            ? `The σ-hole is real (${qm.vsMax!.toFixed(0)} kcal/mol) and points at nothing: the nearest Lewis base, ${nearest.label}, sits ${nearest.offAxisDeg.toFixed(0)}° off the axis at ${nearest.distance.toFixed(2)} Å. This ${element} is paying lipophilicity, not making a halogen bond.`
            : `The σ-hole is real (${qm.vsMax!.toFixed(0)} kcal/mol) and there is no Lewis base anywhere in the forward hemisphere within ${(PLIP.DIST_MAX + 1).toFixed(1)} Å. This ${element} is occupying space.`;
    }

    const plipVerdict = plipCalls.length
        ? `PLIP criteria: HALOGEN BOND to ${plipCalls[0].label} (${plipCalls[0].distance.toFixed(2)} Å, donor angle ${plipCalls[0].donorAngleDeg.toFixed(0)}°). PLIP accepts F, Cl, Br and I on geometry alone.`
        : `PLIP criteria: no halogen bond (needs ≤${PLIP.DIST_MAX} Å and a donor angle within ${PLIP.ANGLE_DEV}° of ${PLIP.DON_ANGLE}°).`;

    return {
        element, vsMax: qm.vsMax, anisotropy: qm.anisotropy, hits,
        nearestOffAxis: nearest, verdict, reading, plipVerdict,
        scope: `${qm.method ?? 'QM'}/${qm.basis ?? '—'} · isolated ligand · gas phase · single pose · V_S,max carries ±25% absolute; the ordering between halogens is the robust part.`,
    };
}
