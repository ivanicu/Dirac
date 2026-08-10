/**
 * Molecular descriptor computation and drug-likeness evaluation via RDKit-JS.
 *
 * Wraps `mol.get_descriptors()` and adds explicit Lipinski / Veber / lead-like
 * pass-fail scoring. Used by the Property Optimization Cockpit facet, but
 * lives in the chemistry substrate because it has no UI dependencies and the
 * same numbers may be useful to other facets (e.g., Conformer Explorer could
 * show Δ-Δ properties between conformers).
 *
 * Atom-index contract: the descriptors operate on the whole submitted
 * molfile, so they share the same atom-index walker as `computeLigandChemistry`
 * in semantic-chemistry-rdkit.ts.
 */

import { getRDKit } from './semantic-chemistry-rdkit';

export interface MolecularDescriptors {
    // Identity / size
    molecularWeight: number;        // amu (average)
    exactMass: number;              // amu (monoisotopic)
    numHeavyAtoms: number;
    numAtoms: number;               // including implicit Hs
    numHeteroatoms: number;

    // Lipinski
    logP: number;                   // Crippen cLogP
    hbd: number;                    // H-bond donors (Lipinski definition)
    hba: number;                    // H-bond acceptors (Lipinski definition)
    tpsa: number;                   // topological polar surface area, Å²

    // Veber
    numRotatableBonds: number;
    numAmideBonds: number;

    // Rings
    numRings: number;
    numAromaticRings: number;
    numAliphaticRings: number;
    numSaturatedRings: number;
    numHeterocycles: number;
    numAromaticHeterocycles: number;

    // Stereo / shape
    numAtomStereoCenters: number;
    numUnspecifiedStereoCenters: number;
    fractionCSP3: number;           // 0..1, sp3 character
    labuteASA: number;              // approximate surface area, Å²
    molarRefractivity: number;
}

export interface DruglikenessEvaluation {
    lipinski: {
        mwPass: boolean;            // MW ≤ 500
        logPPass: boolean;          // LogP ≤ 5
        hbdPass: boolean;           // HBD ≤ 5
        hbaPass: boolean;           // HBA ≤ 10
        violations: number;         // 0..4 (Lipinski says ≤1 is OK)
        overallPass: boolean;
    };
    veber: {
        rotatableBondsPass: boolean; // ≤ 10
        tpsaPass: boolean;           // ≤ 140
        overallPass: boolean;
    };
    leadLike: {
        // Lead-likeness (lower thresholds for fragment-to-lead optimization)
        mwPass: boolean;            // ≤ 300
        logPPass: boolean;          // ≤ 3
        numRingsPass: boolean;      // ≤ 3
        rotatableBondsPass: boolean; // ≤ 5
        overallPass: boolean;
    };
}

export interface DescriptorReport {
    descriptors: MolecularDescriptors;
    evaluation: DruglikenessEvaluation;
}

// === Parsing mol.get_descriptors() JSON ===

interface RawDescriptors {
    amw: number;
    exactmw: number;
    NumHeavyAtoms: number;
    NumAtoms: number;
    NumHeteroatoms: number;
    CrippenClogP: number;
    lipinskiHBD: number;
    lipinskiHBA: number;
    NumHBD: number;
    NumHBA: number;
    tpsa: number;
    NumRotatableBonds: number;
    NumAmideBonds: number;
    NumRings: number;
    NumAromaticRings: number;
    NumAliphaticRings: number;
    NumSaturatedRings: number;
    NumHeterocycles: number;
    NumAromaticHeterocycles: number;
    NumAtomStereoCenters: number;
    NumUnspecifiedAtomStereoCenters: number;
    FractionCSP3: number;
    labuteASA: number;
    CrippenMR: number;
}

function parseDescriptors(raw: RawDescriptors): MolecularDescriptors {
    return {
        molecularWeight: raw.amw,
        exactMass: raw.exactmw,
        numHeavyAtoms: raw.NumHeavyAtoms,
        numAtoms: raw.NumAtoms,
        numHeteroatoms: raw.NumHeteroatoms,
        logP: raw.CrippenClogP,
        hbd: raw.lipinskiHBD,
        hba: raw.lipinskiHBA,
        tpsa: raw.tpsa,
        numRotatableBonds: raw.NumRotatableBonds,
        numAmideBonds: raw.NumAmideBonds,
        numRings: raw.NumRings,
        numAromaticRings: raw.NumAromaticRings,
        numAliphaticRings: raw.NumAliphaticRings,
        numSaturatedRings: raw.NumSaturatedRings,
        numHeterocycles: raw.NumHeterocycles,
        numAromaticHeterocycles: raw.NumAromaticHeterocycles,
        numAtomStereoCenters: raw.NumAtomStereoCenters,
        numUnspecifiedStereoCenters: raw.NumUnspecifiedAtomStereoCenters,
        fractionCSP3: raw.FractionCSP3,
        labuteASA: raw.labuteASA,
        molarRefractivity: raw.CrippenMR,
    };
}

function evaluateDruglikeness(d: MolecularDescriptors): DruglikenessEvaluation {
    const mwPass = d.molecularWeight <= 500;
    const logPPass = d.logP <= 5;
    const hbdPass = d.hbd <= 5;
    const hbaPass = d.hba <= 10;
    const lipinskiViolations = [mwPass, logPPass, hbdPass, hbaPass].filter(p => !p).length;

    const rotatablePass = d.numRotatableBonds <= 10;
    const tpsaPass = d.tpsa <= 140;

    return {
        lipinski: {
            mwPass, logPPass, hbdPass, hbaPass,
            violations: lipinskiViolations,
            overallPass: lipinskiViolations <= 1,
        },
        veber: {
            rotatableBondsPass: rotatablePass,
            tpsaPass,
            overallPass: rotatablePass && tpsaPass,
        },
        leadLike: {
            mwPass: d.molecularWeight <= 300,
            logPPass: d.logP <= 3,
            numRingsPass: d.numRings <= 3,
            rotatableBondsPass: d.numRotatableBonds <= 5,
            overallPass: d.molecularWeight <= 300 && d.logP <= 3 && d.numRings <= 3 && d.numRotatableBonds <= 5,
        },
    };
}

/**
 * Compute molecular descriptors + drug-likeness evaluation for a ligand
 * molfile. Returns null if RDKit fails to parse the molfile.
 *
 * Mirrors `computeLigandChemistry` in semantic-chemistry-rdkit.ts but is
 * kept as a separate function because the data shape (scalar descriptors)
 * is fundamentally different from per-atom SMARTS flags.
 */
export async function computeLigandDescriptors(molfile: string): Promise<DescriptorReport | null> {
    const RDKit = await getRDKit();
    const mol = RDKit.get_mol(molfile);
    if (!mol || !mol.is_valid()) return null;
    try {
        const raw = JSON.parse(mol.get_descriptors()) as RawDescriptors;
        const descriptors = parseDescriptors(raw);
        const evaluation = evaluateDruglikeness(descriptors);
        return { descriptors, evaluation };
    } finally {
        mol.delete();
    }
}
