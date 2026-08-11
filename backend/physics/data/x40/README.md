# X40 benchmark set (fetched, not remembered)

Source: http://cuby4.molecular.cz/dataset_x40.html (Rezac, Riley, Hobza,
JCTC 2012, 10.1021/ct300647k), fetched 2026-08-10 by the fields session.

- `x40.yaml` — 40 complexes with CCSD(T)/CBS reference interaction energies
  (kcal/mol, `reference_value`), names and interaction groups.
- `NN_*.xyz` — equilibrium geometries; the comment line carries
  `selection_a=... selection_b=...` (monomer partition for interaction-energy
  runs).

Why it exists here: the sigma-hole gate needs reference numbers that are
INDEPENDENT of our compute chain, and reference data is the one number that
must never be filled from a model's memory. The halogen-bond block
(iodobenzene / bromobenzene ... acetone / trimethylamine / methanethiol,
HX dimers) is the donor-side anchor set: correlate V_S,max against these
dissociation energies at fixed acceptor.

Caveat carried from the literature: the X40x10 revisit (JPCA 2018,
10.1021/acs.jpca.7b10958) revises some Br/I dissociation energies via
subvalence correlation — ordering-level gates are safe, sub-kcal absolute
gates are not.
