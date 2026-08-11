/**
 * The reverse field: what the ligand SITS IN, not what it emits.
 *
 * Every field in this panel until now answered "what does my molecule look
 * like electrostatically?" — a question a chemist can mostly answer from the
 * 2D structure. The question they cannot answer, and open a 3D viewer for, is
 * the complementarity one: is the pocket positive where my ligand is negative?
 *
 * Two independent reviewers converged on the same architectural move, and it
 * is what makes this expressible: SOURCE (the atoms generating the field) and
 * FRAME (the box it is sampled in) are separate. Here SOURCE is the residue
 * shell and FRAME is the ligand's own box, so the grid stays ligand-sized
 * while the source can be the whole pocket.
 *
 * NO TOPOLOGY IS PERCEIVED. The classical field is exactly linear in the atom
 * set (measured, 5.2e-16 relative), so this needs (element, position, residue
 * identity) and nothing else — no bonds, no capping, no valence, no molfile.
 * That is not an optimisation: the app's molfile builder cannot express more
 * than one residue without silently dropping bonds, and this path structurally
 * cannot reach that defect because it never asks the question.
 */

import { PluginContext } from '../../../mol-plugin/context';
import { StructureElement, StructureProperties, Unit } from '../../../mol-model/structure';
import { Vec3 } from '../../../mol-math/linear-algebra';
import { OrderedSet } from '../../../mol-data/int';
import { resolveFocus } from '../../../chemistry.backend.perception.rdkit-wasm.editable/semantic-focus';
import type { LigandFocusOptions } from '../../../chemistry.backend.perception.rdkit-wasm.editable/semantic-focus';

export interface RegionSource {
    element: string;
    x: number; y: number; z: number;
    resname: string;
    atom_name: string;
}

export interface RegionRequest {
    sources: RegionSource[];
    frame: { lo: [number, number, number], hi: [number, number, number], spacing: number };
    ligandAtoms: number;
}

/** Atom records for every atom in a loci, carrying the residue identity the
 * backend needs to look a template charge up. Positions are scene coordinates,
 * so the cube comes back already registered — no alignment step exists. */
function lociAtoms(loci: StructureElement.Loci): RegionSource[] {
    const out: RegionSource[] = [];
    const position = Vec3();
    const location = StructureElement.Location.create(loci.structure);
    for (const e of loci.elements) {
        if (!Unit.isAtomic(e.unit)) continue;
        const count = OrderedSet.size(e.indices);
        for (let i = 0; i < count; i++) {
            location.unit = e.unit;
            location.element = e.unit.elements[OrderedSet.getAt(e.indices, i)];
            e.unit.conformation.position(location.element, position);
            out.push({
                element: StructureProperties.atom.type_symbol(location),
                x: position[0], y: position[1], z: position[2],
                resname: StructureProperties.residue.label_comp_id(location),
                atom_name: StructureProperties.atom.label_atom_id(location),
            });
        }
    }
    return out;
}

/**
 * SOURCE = the residue shell around the ligand. FRAME = the ligand's box.
 *
 * The shell is the one `resolveFocus` already computes for the semantic layers
 * — whole residues within the cutoff, ligand subtracted — so this control
 * obeys the 3–8 Å slider that is already on screen instead of introducing a
 * second notion of "nearby" that could disagree with what is drawn.
 */
export function buildSurroundingsRequest(
    plugin: PluginContext, options: LigandFocusOptions, padding = 3.0,
): RegionRequest | { error: string } {
    const structure = plugin.managers.structure.component.pivotStructure?.cell.obj?.data;
    if (!structure) return { error: 'no structure loaded' };

    const { ligand, neighbourhood } = resolveFocus(structure, options);
    if (StructureElement.Loci.isEmpty(ligand)) {
        return { error: 'no deposited ligand — the reverse field needs something to sit IN' };
    }
    if (StructureElement.Loci.isEmpty(neighbourhood)) {
        return { error: 'no residues within the cutoff — widen the ligand-focus radius' };
    }

    const sources = lociAtoms(neighbourhood);
    const ligandAtoms = lociAtoms(ligand);
    if (sources.length === 0) return { error: 'the shell resolved to zero atoms' };

    // FRAME from the LIGAND, deliberately. Sizing it to the source would make
    // the grid pocket-sized and the cube enormous for no gain: the answer being
    // asked for is what the field looks like where the ligand is.
    const lo: [number, number, number] = [Infinity, Infinity, Infinity];
    const hi: [number, number, number] = [-Infinity, -Infinity, -Infinity];
    for (const a of ligandAtoms) {
        lo[0] = Math.min(lo[0], a.x - padding); hi[0] = Math.max(hi[0], a.x + padding);
        lo[1] = Math.min(lo[1], a.y - padding); hi[1] = Math.max(hi[1], a.y + padding);
        lo[2] = Math.min(lo[2], a.z - padding); hi[2] = Math.max(hi[2], a.z + padding);
    }

    return { sources, frame: { lo, hi, spacing: 0.4 }, ligandAtoms: ligandAtoms.length };
}
