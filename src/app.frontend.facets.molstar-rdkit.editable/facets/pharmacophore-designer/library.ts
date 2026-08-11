/**
 * Pharmacophore Designer — screening library.
 *
 * A small curated set of drug-like molecules, fragments, and natural products
 * shipped as SMILES. Screening runs live in the browser through the same
 * RDKit SMARTS definitions the ligand-side perception uses, so a library
 * count and a ligand count always mean the same thing.
 *
 * Every entry is validated by `scripts/check-pharmacophore-library.mjs`
 * (node-side RDKit, same 2025.03.4 build): parse validity, canonical-SMILES
 * dedup, and exact feature-count assertions on the probe entries. Run it
 * after ANY edit to this file — an unparseable or duplicated SMILES fails CI
 * before it can silently return zero matches at runtime.
 *
 * The two `probe` entries are the screening engine's positive/negative
 * controls: benzene must match an aromatic-only query, cyclohexane must not.
 */

export interface LibraryEntry {
    readonly id: string;
    readonly name: string;
    readonly smiles: string;
    readonly category:
        | 'probe'
        | 'fragment'
        | 'analgesic-nsaid'
        | 'cns'
        | 'cardiovascular'
        | 'antibiotic'
        | 'kinase-inhibitor'
        | 'natural-product'
        | 'steroid'
        | 'nucleoside'
        | 'other-drug';
}

export const ScreeningLibrary: readonly LibraryEntry[] = Object.freeze([
    // === Probes (screening engine controls) ===
    { id: 'benzene', name: 'Benzene', smiles: 'c1ccccc1', category: 'probe' },
    { id: 'cyclohexane', name: 'Cyclohexane', smiles: 'C1CCCCC1', category: 'probe' },

    // === Fragments ===
    { id: 'pyridine', name: 'Pyridine', smiles: 'c1ccncc1', category: 'fragment' },
    { id: 'imidazole', name: 'Imidazole', smiles: 'c1c[nH]cn1', category: 'fragment' },
    { id: 'furan', name: 'Furan', smiles: 'c1ccoc1', category: 'fragment' },
    { id: 'thiophene', name: 'Thiophene', smiles: 'c1ccsc1', category: 'fragment' },
    { id: 'naphthalene', name: 'Naphthalene', smiles: 'c1ccc2ccccc2c1', category: 'fragment' },
    { id: 'aniline', name: 'Aniline', smiles: 'Nc1ccccc1', category: 'fragment' },
    { id: 'phenol', name: 'Phenol', smiles: 'Oc1ccccc1', category: 'fragment' },
    { id: 'benzoic-acid', name: 'Benzoic acid', smiles: 'O=C(O)c1ccccc1', category: 'fragment' },
    { id: 'toluene', name: 'Toluene', smiles: 'Cc1ccccc1', category: 'fragment' },
    { id: 'indole', name: 'Indole', smiles: 'c1ccc2[nH]ccc2c1', category: 'fragment' },
    { id: 'benzamidine', name: 'Benzamidine', smiles: 'NC(=N)c1ccccc1', category: 'fragment' },
    { id: 'adenine', name: 'Adenine', smiles: 'Nc1ncnc2[nH]cnc12', category: 'fragment' },

    // === Analgesics / NSAIDs ===
    { id: 'aspirin', name: 'Aspirin', smiles: 'CC(=O)Oc1ccccc1C(=O)O', category: 'analgesic-nsaid' },
    { id: 'paracetamol', name: 'Paracetamol', smiles: 'CC(=O)Nc1ccc(O)cc1', category: 'analgesic-nsaid' },
    { id: 'ibuprofen', name: 'Ibuprofen', smiles: 'CC(C)Cc1ccc(C(C)C(=O)O)cc1', category: 'analgesic-nsaid' },
    { id: 'naproxen', name: 'Naproxen', smiles: 'COc1ccc2cc(C(C)C(=O)O)ccc2c1', category: 'analgesic-nsaid' },
    { id: 'ketoprofen', name: 'Ketoprofen', smiles: 'CC(C(=O)O)c1cccc(C(=O)c2ccccc2)c1', category: 'analgesic-nsaid' },
    { id: 'celecoxib', name: 'Celecoxib', smiles: 'Cc1ccc(-c2cc(C(F)(F)F)nn2-c2ccc(S(N)(=O)=O)cc2)cc1', category: 'analgesic-nsaid' },
    { id: 'morphine', name: 'Morphine', smiles: 'CN1CC[C@]23c4c5ccc(O)c4O[C@H]2[C@@H](O)C=C[C@H]3[C@H]1C5', category: 'analgesic-nsaid' },
    { id: 'lidocaine', name: 'Lidocaine', smiles: 'CCN(CC)CC(=O)Nc1c(C)cccc1C', category: 'analgesic-nsaid' },
    { id: 'procaine', name: 'Procaine', smiles: 'CCN(CC)CCOC(=O)c1ccc(N)cc1', category: 'analgesic-nsaid' },

    // === CNS ===
    { id: 'caffeine', name: 'Caffeine', smiles: 'CN1C=NC2=C1C(=O)N(C)C(=O)N2C', category: 'cns' },
    { id: 'theophylline', name: 'Theophylline', smiles: 'Cn1c(=O)c2[nH]cnc2n(C)c1=O', category: 'cns' },
    { id: 'nicotine', name: 'Nicotine', smiles: 'CN1CCC[C@H]1c1cccnc1', category: 'cns' },
    { id: 'diazepam', name: 'Diazepam', smiles: 'CN1C(=O)CN=C(c2ccccc2)c2cc(Cl)ccc21', category: 'cns' },
    { id: 'fluoxetine', name: 'Fluoxetine', smiles: 'CNCCC(Oc1ccc(C(F)(F)F)cc1)c1ccccc1', category: 'cns' },
    { id: 'sertraline', name: 'Sertraline', smiles: 'CN[C@H]1CC[C@@H](c2ccc(Cl)c(Cl)c2)c2ccccc21', category: 'cns' },
    { id: 'diphenhydramine', name: 'Diphenhydramine', smiles: 'CN(C)CCOC(c1ccccc1)c1ccccc1', category: 'cns' },
    { id: 'chlorpheniramine', name: 'Chlorpheniramine', smiles: 'CN(C)CCC(c1ccc(Cl)cc1)c1ccccn1', category: 'cns' },
    { id: 'serotonin', name: 'Serotonin', smiles: 'NCCc1c[nH]c2ccc(O)cc12', category: 'cns' },
    { id: 'dopamine', name: 'Dopamine', smiles: 'NCCc1ccc(O)c(O)c1', category: 'cns' },
    { id: 'histamine', name: 'Histamine', smiles: 'NCCc1c[nH]cn1', category: 'cns' },

    // === Cardiovascular / metabolic ===
    { id: 'propranolol', name: 'Propranolol', smiles: 'CC(C)NCC(O)COc1cccc2ccccc12', category: 'cardiovascular' },
    { id: 'atenolol', name: 'Atenolol', smiles: 'CC(C)NCC(O)COc1ccc(CC(N)=O)cc1', category: 'cardiovascular' },
    { id: 'salbutamol', name: 'Salbutamol', smiles: 'CC(C)(C)NCC(O)c1ccc(O)c(CO)c1', category: 'cardiovascular' },
    { id: 'warfarin', name: 'Warfarin', smiles: 'CC(=O)CC(c1ccccc1)c1c(O)c2ccccc2oc1=O', category: 'cardiovascular' },
    { id: 'captopril', name: 'Captopril', smiles: 'CC(CS)C(=O)N1CCCC1C(=O)O', category: 'cardiovascular' },
    { id: 'losartan', name: 'Losartan', smiles: 'CCCCc1nc(Cl)c(CO)n1Cc1ccc(-c2ccccc2-c2nn[nH]n2)cc1', category: 'cardiovascular' },
    { id: 'metformin', name: 'Metformin', smiles: 'CN(C)C(=N)NC(N)=N', category: 'cardiovascular' },

    // === Antibiotics / anti-infectives ===
    { id: 'penicillin-g', name: 'Penicillin G', smiles: 'CC1(C)S[C@@H]2[C@H](NC(=O)Cc3ccccc3)C(=O)N2[C@H]1C(=O)O', category: 'antibiotic' },
    { id: 'amoxicillin', name: 'Amoxicillin', smiles: 'CC1(C)S[C@@H]2[C@H](NC(=O)[C@H](N)c3ccc(O)cc3)C(=O)N2[C@H]1C(=O)O', category: 'antibiotic' },
    { id: 'ampicillin', name: 'Ampicillin', smiles: 'CC1(C)S[C@@H]2[C@H](NC(=O)[C@H](N)c3ccccc3)C(=O)N2[C@H]1C(=O)O', category: 'antibiotic' },
    { id: 'sulfamethoxazole', name: 'Sulfamethoxazole', smiles: 'Cc1cc(NS(=O)(=O)c2ccc(N)cc2)no1', category: 'antibiotic' },
    { id: 'trimethoprim', name: 'Trimethoprim', smiles: 'COc1cc(Cc2cnc(N)nc2N)cc(OC)c1OC', category: 'antibiotic' },
    { id: 'ciprofloxacin', name: 'Ciprofloxacin', smiles: 'O=C(O)c1cn(C2CC2)c2cc(N3CCNCC3)c(F)cc2c1=O', category: 'antibiotic' },
    { id: 'chloramphenicol', name: 'Chloramphenicol', smiles: 'O=C(NC(CO)C(O)c1ccc([N+](=O)[O-])cc1)C(Cl)Cl', category: 'antibiotic' },
    { id: 'chloroquine', name: 'Chloroquine', smiles: 'CCN(CC)CCCC(C)Nc1ccnc2cc(Cl)ccc12', category: 'antibiotic' },
    { id: 'acyclovir', name: 'Acyclovir', smiles: 'Nc1nc2c(ncn2COCCO)c(=O)[nH]1', category: 'antibiotic' },

    // === Kinase inhibitors / oncology ===
    { id: 'imatinib', name: 'Imatinib', smiles: 'Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1', category: 'kinase-inhibitor' },
    { id: 'gefitinib', name: 'Gefitinib', smiles: 'COc1cc2ncnc(Nc3ccc(F)c(Cl)c3)c2cc1OCCCN1CCOCC1', category: 'kinase-inhibitor' },
    { id: 'methotrexate', name: 'Methotrexate', smiles: 'CN(Cc1cnc2nc(N)nc(N)c2n1)c1ccc(C(=O)N[C@@H](CCC(=O)O)C(=O)O)cc1', category: 'kinase-inhibitor' },

    // === Natural products ===
    { id: 'quercetin', name: 'Quercetin', smiles: 'O=c1c(O)c(-c2ccc(O)c(O)c2)oc2cc(O)cc(O)c12', category: 'natural-product' },
    { id: 'resveratrol', name: 'Resveratrol', smiles: 'Oc1ccc(/C=C/c2cc(O)cc(O)c2)cc1', category: 'natural-product' },
    { id: 'curcumin', name: 'Curcumin', smiles: 'COc1cc(/C=C/C(=O)CC(=O)/C=C/c2ccc(O)c(OC)c2)ccc1O', category: 'natural-product' },
    { id: 'quinine', name: 'Quinine', smiles: 'COc1ccc2nccc([C@@H](O)[C@H]3C[C@@H]4CCN3C[C@@H]4C=C)c2c1', category: 'natural-product' },
    { id: 'biotin', name: 'Biotin', smiles: 'O=C(O)CCCC[C@@H]1SC[C@@H]2NC(=O)N[C@H]12', category: 'natural-product' },
    { id: 'retinoic-acid', name: 'Retinoic acid (REA)', smiles: 'CC1=C(/C=C/C(C)=C/C=C/C(C)=C/C(=O)O)C(C)(C)CCC1', category: 'natural-product' },
    { id: 'folic-acid', name: 'Folic acid', smiles: 'Nc1nc2ncc(CNc3ccc(C(=O)N[C@@H](CCC(=O)O)C(=O)O)cc3)nc2c(=O)[nH]1', category: 'natural-product' },

    // === Steroids ===
    { id: 'cholesterol', name: 'Cholesterol', smiles: 'CC(C)CCC[C@@H](C)[C@H]1CC[C@H]2[C@@H]3CC=C4C[C@@H](O)CC[C@]4(C)[C@H]3CC[C@]12C', category: 'steroid' },
    { id: 'testosterone', name: 'Testosterone', smiles: 'C[C@]12CC[C@H]3[C@@H](CC[C@H]4CC(=O)CC[C@]34C)[C@@H]1CC[C@@H]2O', category: 'steroid' },
    { id: 'estradiol', name: 'Estradiol', smiles: 'C[C@]12CC[C@H]3c4ccc(O)cc4CC[C@H]3[C@@H]1CC[C@@H]2O', category: 'steroid' },

    // === Nucleosides / sugars ===
    { id: 'adenosine', name: 'Adenosine', smiles: 'Nc1ncnc2c1ncn2[C@@H]1O[C@H](CO)[C@@H](O)[C@H]1O', category: 'nucleoside' },
    { id: 'glucose', name: 'D-Glucose', smiles: 'OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O', category: 'nucleoside' },

    // === Other drugs ===
    { id: 'omeprazole', name: 'Omeprazole', smiles: 'COc1ccc2[nH]c(S(=O)Cc3ncc(C)c(OC)c3C)nc2c1', category: 'other-drug' },
    { id: 'cimetidine', name: 'Cimetidine', smiles: 'Cc1nc[nH]c1CSCCNC(=NC#N)NC', category: 'other-drug' },
    { id: 'sildenafil', name: 'Sildenafil', smiles: 'CCCc1nn(C)c2c(=O)[nH]c(-c3cc(S(=O)(=O)N4CCN(C)CC4)ccc3OCC)nc12', category: 'other-drug' },
]);
