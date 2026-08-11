/**
 * Halogen audit facet — is this halogen doing a directional job, or is it a slide?
 *
 * The chemistry lives in chemistry.../halogen-audit.ts and is renderer-free; this file
 * only collects the pocket atoms out of the mol* structure and draws the rows.
 *
 * The panel's job is to show a DISAGREEMENT. One row is what the electronics say, and
 * the row under it, greyed, is what a purely geometric profiler would have reported —
 * PLIP's own criteria, with PLIP's own constants, including the fact that PLIP accepts
 * fluorine as a halogen-bond donor on geometry alone. A chemist should not have to take
 * this app's word over the tool they already trust; they should be able to see where the
 * two part company and decide for themselves.
 */

import { OrderedSet } from '../../../mol-data/int';
import { Vec3 } from '../../../mol-math/linear-algebra';
import { QueryContext, Structure, StructureElement, StructureProperties, StructureSelection, Unit } from '../../../mol-model/structure';
import { MolScriptBuilder as MS } from '../../../mol-script/language/builder';
import { compile } from '../../../mol-script/runtime/query/compiler';
import { StructureSelectionQueries } from '../../../mol-plugin-state/helpers/structure-selection-query';
import { auditHalogen, HalogenAudit, PocketAtom, ELECTRONIC } from '../../../chemistry.backend.perception.rdkit-wasm.editable/halogen-audit';
import { extractLigandAtomData, lociFromFocusOptions } from '../../../chemistry.backend.perception.rdkit-wasm.editable/ligand-pipeline';
import type { LigandFocusOptions } from '../../../chemistry.backend.perception.rdkit-wasm.editable/semantic-focus';

const HALOGENS = new Set(['F', 'CL', 'BR', 'I']);

/** Residues within 6 Å of the ligand, minus the ligand — the atoms a σ-hole could aim at. */
const PocketQuery = compile<StructureSelection>(MS.struct.modifier.union([
    MS.struct.modifier.exceptBy({
        0: MS.struct.modifier.includeSurroundings({
            0: StructureSelectionQueries.ligandPlusConnected.expression,
            radius: 6,
            'as-whole-residues': true,
        }),
        by: StructureSelectionQueries.ligandPlusConnected.expression,
    }),
]));

function collectPocketAtoms(structure: Structure): PocketAtom[] {
    const sel = PocketQuery(new QueryContext(structure.root));
    const loci = StructureSelection.toLociWithCurrentUnits(sel);
    if (StructureElement.Loci.isEmpty(loci)) return [];

    const out: PocketAtom[] = [];
    const position = Vec3();
    const location = StructureElement.Location.create(loci.structure);
    for (const e of loci.elements) {
        if (!Unit.isAtomic(e.unit)) continue;
        const count = OrderedSet.size(e.indices);
        for (let i = 0; i < count; i++) {
            location.unit = e.unit;
            location.element = e.unit.elements[OrderedSet.getAt(e.indices, i)];
            const element = StructureProperties.atom.type_symbol(location);
            if (element !== 'N' && element !== 'O' && element !== 'S') continue;
            e.unit.conformation.position(location.element, position);
            out.push({
                position: Vec3.clone(position),
                element,
                label: `${StructureProperties.residue.label_comp_id(location)}${StructureProperties.residue.label_seq_id(location)} ${StructureProperties.atom.label_atom_id(location)}`,
            });
        }
    }
    return out;
}

export interface HalogenRow {
    atomIndex: number;
    atomName: string;
    audit: HalogenAudit;
}

/**
 * @param qmBySymbol V_S,max per halogen ELEMENT from the QM MEP run, when one exists.
 *   Keyed by element rather than by atom because the MEP endpoint reports extrema against
 *   the isolated ligand's own atom indexing, and reconciling two indexings silently is how
 *   a confident wrong answer gets made. Per-atom keying is the follow-up, not a shortcut.
 */
export function auditLigandHalogens(
    structure: Structure, options: LigandFocusOptions,
    qmBySymbol: Map<string, { vsMax: number, anisotropy: number | null, basis?: string, method?: string }> = new Map(),
): HalogenRow[] {
    const loci = lociFromFocusOptions(structure, options);
    const data = extractLigandAtomData(loci);
    if (!data) return [];
    const pocket = collectPocketAtoms(structure);

    const rows: HalogenRow[] = [];
    for (let i = 0; i < data.atomElements.length; i++) {
        const el = data.atomElements[i].toUpperCase();
        if (!HALOGENS.has(el)) continue;
        const bond = data.bonds.find(b => b.a1 === i || b.a2 === i);
        if (!bond) continue;                       // a halogen with no bond is not a halogen bond donor
        const carbonIdx = bond.a1 === i ? bond.a2 : bond.a1;
        const qm = qmBySymbol.get(el) ?? null;
        rows.push({
            atomIndex: i,
            atomName: data.atomNames[i],
            audit: auditHalogen(
                data.atomPositions[carbonIdx], data.atomPositions[i],
                data.atomElements[i], pocket,
                qm ? { vsMax: qm.vsMax, anisotropy: qm.anisotropy, basis: qm.basis, method: qm.method }
                    : { vsMax: null, anisotropy: null }),
        });
    }
    return rows;
}

const VERDICT_TONE: Record<string, string> = {
    'REAL': 'ok', 'MARGINAL': 'warn', 'DECORATIVE': 'warn',
    'ABSENT': 'off', 'MIS-CALLED': 'off', 'UNMEASURED': 'unknown',
};

export function renderHalogenPanel(rows: HalogenRow[]) {
    const host = document.getElementById('halogen-audit');
    if (!host) return;
    if (!rows.length) {
        host.innerHTML = '<p class="ledger-empty">No halogen on the focused ligand.</p>';
        return;
    }
    host.innerHTML = rows.map(r => `
    <div class="halogen-row" data-tone="${VERDICT_TONE[r.audit.verdict] ?? 'off'}">
      <div class="halogen-head">
        <b>${r.audit.element}${r.atomName ? ' · ' + r.atomName : ''}</b>
        <span class="halogen-verdict">${r.audit.verdict}</span>
        <span class="halogen-vs">${r.audit.vsMax === null ? 'V<sub>S,max</sub> not computed' : `V<sub>S,max</sub> ${r.audit.vsMax.toFixed(0)} kcal/mol`}</span>
      </div>
      <p class="halogen-reading">${r.audit.reading}</p>
      <p class="halogen-plip">${r.audit.plipVerdict}</p>
      ${r.audit.hits.length ? `<table class="halogen-hits"><tr><th>Lewis base</th><th>d (Å)</th><th>off-axis</th></tr>` +
            r.audit.hits.slice(0, 4).map(h =>
                `<tr${!h.isWater && h.offAxisDeg <= ELECTRONIC.ON_AXIS_DEG ? ' class="on-axis"' : ''}><td>${h.label}${h.isWater ? ' <i>(water)</i>' : ''}</td><td>${h.distance.toFixed(2)}</td><td>${h.offAxisDeg.toFixed(0)}°</td></tr>`).join('') +
            '</table>' : ''}
      <p class="halogen-scope">${r.audit.scope}</p>
    </div>`).join('');
}

/** Called from the same lifecycle point that refreshes the other ligand facets. */
export function updateHalogenAudit(
    structure: Structure | null, options: LigandFocusOptions,
    qmBySymbol?: Map<string, { vsMax: number, anisotropy: number | null, basis?: string, method?: string }>,
) {
    if (!structure) { renderHalogenPanel([]); return; }
    try {
        renderHalogenPanel(auditLigandHalogens(structure, options, qmBySymbol));
    } catch (error) {
        console.error('halogen audit failed', error);
        renderHalogenPanel([]);
    }
}
