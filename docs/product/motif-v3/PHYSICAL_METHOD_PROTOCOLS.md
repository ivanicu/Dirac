# Physical method protocols

## Chemical state and proposals

Protonation, tautomerism and stereochemistry are a coupled enumeration problem. Blind
Cartesian products are prohibited. A state ensemble freezes pH, temperature, solvent,
ionic strength, engine/version/parameters, microstate populations, uncertainty,
retained/discarded mass, truncation and parent aggregation.

Chemistry alerts are typed separately:

- method unsupported;
- program hard-forbidden;
- risk annotation;
- assay-interference warning;
- method-specific incompatibility.

PAINS or a reactive group is not automatically a universal chemical rejection.

A ProposalRecord retains parent/template lineage, reactant roles, atom mapping,
stereochemical outcome, route assessment state, lineage depth and duplicate class.
Quotas are enforced by parent and reaction template so one prolific generator cannot
consume the portfolio.

## Docking

The exact expansion is:

```text
ChemicalMicrostate
→ 50 requested conformers
→ failed/nonconverged/energy filter
→ force-field-specific clustering
→ K representatives
× PreparedReceptorState
× independent seed
→ PoseHypothesis[]
→ cross-run pose clustering
→ PoseEnsembleEvidence
```

MMFF and UFF energies are never compared across force-field families. Vina and AD4 raw
scores are never combined. Flexible-receptor docking is a separate MethodManifest.
Receptor preparation freezes construct, missing-residue policy, waters, cofactors,
metals, protonation and coordinate artifact.

Validation contains redocking, cross-docking, decoy/enrichment, box perturbation, seed
stability, ligand-size stratification and rank diagnostics. A docking output remains a
pose/score hypothesis, never binding free energy.

## Fields, QM and torsions

Every field freezes grid origin, axes, dimensions, spacing, units, alignment and atom
selection. A Gasteiger-derived surface is named `Gasteiger partial-charge potential`,
not QM MEP. QM identity includes geometry source/optimization, method, basis, dispersion,
solvent, charge, multiplicity, SCF controls, convergence, integration grid and program.
Torsion scans freeze bond definition, constrained coordinates, angular grid, relaxation,
energy zero, symmetry handling and failure policy.

## Molecular dynamics

`ParameterizationRelease` freezes protein/ligand/water/ion force fields, charge method,
charge artifact, residue templates, constraints, nonbonded settings, PME, box, salt,
integrator, timestep, thermostat/barostat, minimization, equilibration and production.

A support matrix declares neutral, charged, charge-changing, metal, covalent,
macrocycle, peptide, noncanonical-residue and membrane applicability. Unsupported means
the method cannot answer; it does not reject the ChemicalEntity.

Analysis freezes selections, PBC handling, imaging/unwrapping, alignment, cutoffs,
block definitions and repeat aggregation. Ligand departure may be accepted negative
evidence if the protocol and quality checks remain valid; it is not automatically an
invalid trajectory.

OpenMM produces two restart tracks:

- exact binary checkpoint for same-platform continuation;
- portable XML state plus topology/system artifacts for inspection/migration.

Comparisons use MethodManifest tolerances, not blanket bitwise equality.

## OpenFE / RBFE

The official OpenFE planner produces a redundant `LigandNetwork` using Lomap and
Kartograf mappings. Dirac persists the native network serialization, selected mappings,
both mapper proposals and their Jaccard disagreement. RDKit FMCS remains an independent
diagnostic; it is not the authoritative network planner.

Every edge freezes ligand charges once. The same charge digest must be used in both
complex and solvent legs and all repeats. An OpenFE edge retains serialized native
objects (`LigandNetwork`, `ChemicalSystem`, `AlchemicalNetwork`, `Transformation` and
`ProtocolUnit` where present) as an Artifact.

Release-scale minimum:

```text
4 nodes
≥4 edges
× 2 legs (complex, solvent)
× 3 independent repeats
= ≥24 production executions
```

Pilot execution is separately labelled and is not counted among the 24 production
executions. Pilot selection must cover representative and risky mappings, not only the
easiest edge.

Edge assembly is `ΔΔG(right-left) = ΔGcomplex − ΔGsolvent`. Repeat aggregation exposes
within- and between-repeat variance and effective sample size. Network fitting freezes
reference/gauge/sign, accepts a full observation covariance matrix, propagates node
covariance and reports redundant-edge cycle residuals. Bootstrap count is an analysis
setting, not a convergence guarantee; overlap, autocorrelation, ESS, block stability,
repeat agreement and cycle closure remain independent gates.
