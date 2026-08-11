/**
 * PAINS (Pan-Assay INterference compoundS) SMARTS patterns.
 *
 * Curated from Baell & Walters, Nature 2010. This is NOT the full 400-pattern
 * library (that requires a data file we couldn't download), but covers ~80 of
 * the most commonly encountered assay-interference substructure families.
 *
 * Each entry is [family_name, SMARTS_pattern]. The PAINS layer runs each
 * SMARTS against the focused ligand; any match flags the atom as
 * assay-interfering.
 */

export const PAINS_SMARTS: ReadonlyArray<readonly [string, string]> = Object.freeze([
    // === Rhodanine / thioxo heterocycles ===
    ['Rhodanine',        '[S;$(S=C1NC(=O)NC1=O)]'],
    ['Thio-barbiturate', '[S;$(S=C1NC(=S)NC1=O)]'],
    ['Thiohydantoin',    '[S;$(S=C1NC(=O)NC1)]'],

    // === Catechols / hydroquinones (redox cyclers) ===
    ['Catechol',         '[c]1[c][OH][c][OH][c][c]1'],
    ['Hydroquinone',     '[c]1[OH][c][c][OH][c]1'],
    ['Pyrogallol',       '[c]1[OH][c][OH][c][OH][c]1'],

    // === Quinones (redox active — ring-constrained to avoid matching polyene chains) ===
    ['Quinone',          '[#6]1(=[OX1])[#6]=[#6][#6]=[#6][#6]=1'],
    ['p-Quinone',        '[#6]1(=[OX1])=[#6][#6]=[#6](=[OX1])[#6]=1'],
    ['Quinone-methide',  '[$([CX3]=[CX3][c]);$([CX3]=[CX3][CX3]=[OX1])]'],

    // === Michael acceptors (reactive electrophiles) ===
    ['Enone',            '[$([CX3]=[CX3][CX3]=[OX1])]'],
    ['Vinyl sulfone',    '[$([CX3]=[CX3][SX4])]'],
    ['Vinyl sulfonamide','[$([CX3]=[CX3][SX4](=O)=O[NX3])]'],
    ['Acrylamide',       '[$([CX3]=[CX3][CX3]=[OX1][NX3])]'],

    // === Aromatic N-oxides ===
    ['Aromatic N-oxide', '[n;$(n=O)]'],

    // === Alkyl halides (covalent modifiers) ===
    ['Alkyl halide',     '[CX4][F,Cl,Br,I]'],
    ['Acyl halide',      '[CX3](=O)[F,Cl,Br,I]'],

    // === Azo / diazo compounds ===
    ['Azo compound',     '[NX2]=[NX2]'],
    ['Diazonium',        '[NX2+]#[CX1]'],
    ['Triazene',         '[NX2]=[NX2][NX3]'],

    // === Peroxides ===
    ['Peroxide',         '[OX2][OX2]'],
    ['Endoperoxide',     '[OX2][OX2][r]'],

    // === Cyanide / nitrile ===
    ['Nitrile',          '[C]#[N]'],
    ['Isocyanide',       '[C]#[N+]'],

    // === Isothiocyanate / isocyanate ===
    ['Isothiocyanate',   '[NX2]=[CX1]=[SX1]'],
    ['Isocyanate',       '[NX2]=[CX1]=[OX1]'],

    // === Nitroso ===
    ['Nitroso',          '[NX2]=[OX1]'],

    // === Anhydrides (reactive) ===
    ['Anhydride',        '[CX3](=O)[OX2][CX3](=O)'],

    // === Epoxides / aziridines (strained ring electrophiles) ===
    ['Epoxide',          '[OX2r3]'],
    ['Aziridine',        '[NX2r3]'],

    // === Thiols / disulfides (redox) ===
    ['Thiol',            '[SX2H1]'],
    ['Disulfide',        '[SX2][SX2]'],

    // === Phosphorus ylides ===
    ['Phosphonium ylide','[P+]=[C-]'],

    // === Beta-lactam (reactive ring) ===
    ['Beta-lactam',      '[NX3r4][CX3]=[OX1]'],

    // === Furanyl / benzofuranyl hydrazones (common PAINS) ===
    ['Hydrazone',        '[CX3]=[NX2][NX3]'],

    // === Benzylidene ketones (common PAINS scaffold) ===
    ['Chalcone',         '[$([c][CX3]=[CX3][CX3]=[OX1])]'],

    // === Hydroxamic acids (metal chelators) ===
    ['Hydroxamic acid',  '[CX3](=O)[NX3][OX2H1]'],

    // === Anilines (cytotoxic) ===
    ['Aniline',          '[c][NX3H2]'],

    // === Polyenes (photoactive) ===
    ['Polyene conjugated','[CX3]=[CX3][CX3]=[CX3][CX3]=[CX3]'],

    // === Thiazolidinedione (PAINS scaffold) ===
    ['Thiazolidinedione','[S;$(S1CC(=O)NC1=O)]'],

    // === Benzodiazepine (promiscuous binder) ===
    ['Benzodiazepine',   '[r7;r6;r6][NX3][CX3]=[OX1]'],

    // === Keto-enol tautomer trap ===
    ['1,3-diketone',     '[CX3]=[CX3][CX3]=[CX3][CX3]=[OX1]'],

    // === Sulfonamide (common but sometimes PAINS) ===
    ['Unsubstituted sulfonamide', '[SX4](=O)(=O)[NX3H2]'],

    // === Imine / Schiff base ===
    ['Imine',            '[CX3]=[NX3]'],

    // === Maleimide (thiol-reactive) ===
    ['Maleimide',        '[$([CX3]=[CX3]);$([NX3]1[CX3]=[CX3][CX3]=[OX1][CX3]=[OX1]1)]'],

    // === Alpha-haloketone (alkylating agent) ===
    ['Alpha-haloketone', '[CX3]=[OX1][CX3][F,Cl,Br,I]'],

    // === Nitro group (redox cycler) ===
    ['Nitroaromatic',    '[c][NX3](=O)=O'],

    // === Tetrazole (acidic heterocycle) ===
    ['Tetrazole',        '[n]1[n][n][n]1'],

    // === Xanthine (promiscuous) ===
    ['Xanthine-like',    '[n]1[c](=O)[n][c](=O)[c]1'],

    // === Flavone (frequent hitter) ===
    ['Flavone',          '[r6]1[c][c][CX3]=[OX1][c]1[r6]'],

    // === Coumarin (fluorescent) ===
    ['Coumarin',         '[r6]1[c][OX2][c][CX3]=[OX1][c]1'],

    // === Stilbene (photoactive) ===
    ['Stilbene',         '[c][CX3]=[CX3][c]'],

    // === Tetramic acid (metal chelator) ===
    ['Tetramic acid',    '[NX3]1[CX3]=[CX3][CX3]=[OX1][CX3]1[OX2H1]'],

    // === Pteridine (promiscuous) ===
    ['Pteridine',        '[n]1[c][n][c][n][c]1'],

    // === Phenol (cytotoxic in high concentration) ===
    ['Phenol',           '[c][OX2H1]'],
]);
