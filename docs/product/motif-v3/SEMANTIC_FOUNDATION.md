# Semantic foundation

## Scientific object graph

```text
SubmittedCompoundRecord
  → ChemicalEntity
  → ChemicalStateEnsemble(condition, engine, retained/discarded mass)
  → ChemicalMicrostate(protonation + tautomer + stereo, jointly enumerated)
  → ConformerEnsemble
  → ConformerHypothesis

ProteinStructureSource
  → PreparedReceptorState
  → BindingSiteHypothesis

ChemicalMicrostate + PreparedReceptorState + BindingSiteHypothesis
  + PoseProtocolRelease
  → PoseHypothesis / PoseEnsemble
  → ComplexHypothesis
  + ParameterizationRelease
  → ParameterizedSystem
  → SimulationRun / FreeEnergyTransformation

MeasurementObservation[] → DatasetSnapshot → ModelRelease → PredictionEvidence
MethodRun → MethodOutcome → QualityAssessment → EvidenceItem
EvidenceItem[] → EvidenceSnapshot → DecisionSnapshot → RoutingAction
```

`CandidateState` is forbidden as a union of chemical identity, microstate, conformer
and pose. Each node is content-addressed and has explicit dependency edges. A changed
upstream digest invalidates downstream evidence; it does not mutate old evidence.

## Decision units

- Chemical selection is normally made at `ChemicalEntity` (parent) level.
- Method execution can target a `ChemicalMicrostate`, `PoseHypothesis`,
  `ComplexHypothesis` or `FreeEnergyTransformation`.
- State results return to the parent through a frozen aggregation policy.
- `best_state` and `best_receptor` aggregation are prohibited because they create a
  state/receptor lottery. Supported policies include population-weighted, explicit
  worst-case and a fully specified distributional policy.
- Missing microstate population is retained as `discarded_population_mass`; it is never
  silently renormalized away.

## Four orthogonal states

| Dimension | Values |
|---|---|
| Execution | created, queued, admitted, running, succeeded, failed, cancelled, lost |
| Applicability | unknown, applicable, not_applicable, unsupported, outside_validated_domain |
| Scientific assessment | not_assessed, accepted, provisional, rejected |
| Decision disposition | pending, selected, reserve, deferred, rejected, refused |

Claim eligibility is a fifth typed output, with codes such as
`ineligible_technical_smoke`, `ineligible_stale` and `ineligible_conflict`.
Execution failure cannot itself reject a scientific hypothesis. Retry creates a new
Attempt; it never reopens a terminal Attempt.

## MethodOutcome and EvidenceItem

`MethodOutcome` contains process state, output-manifest reference, telemetry and a
structured error. It is not scientific evidence.

`EvidenceItem` is one of:

- ScalarEstimateEvidence;
- CensoredEstimateEvidence;
- DistributionEvidence;
- PoseEnsembleEvidence;
- TrajectoryEvidence;
- TransformationEvidence;
- NetworkEstimateEvidence;
- QualitativeGateEvidence;
- ConflictEvidence.

All evidence carries subject, condition, MethodManifest, MethodOutcome,
QualityAssessment, payload schema/artifact, applicability, dependencies,
shared-assumption references, supersession and claim eligibility. Worker failure rate,
retry count and scheduler risk remain telemetry, never molecular evidence.

Evidence assembly rejects stale dependencies and incompatible conditions. Shared charge,
receptor, calibration or parameterization assumptions are grouped so downstream
uncertainty cannot treat correlated evidence as independent.

## Model releases

Lifecycle is:

1. `technical_smoke`;
2. `candidate_unvalidated`;
3. `scientific_candidate`;
4. `validated_release`;
5. `promoted_release`;
6. `retired`.

Training may create only the first two. Higher states require an independently persisted
validation record. Three compounds prove wiring only. Promotion gates cover independent
compound count, series count, split groups, effective sample size, censoring, label
noise, domain coverage, independent holdout and the complete specification curve.

Applicability is scoped by model × endpoint × representation. An OOD score or ensemble
dispersion is not automatically epistemic uncertainty. An ensemble release must state
task/scale compatibility, member exclusion, weights or stacking, and calibration.

## Scientific Action Planner

A `RoutingAction` records:

- EvidenceSnapshot;
- action kind and subject;
- scientific question;
- required inputs;
- versioned outcome model;
- expected utility delta (EVSI / expected regret reduction);
- resource estimate and priced cost;
- budget lease;
- policy release and reason codes.

The ranking target is expected utility improvement minus explicit time, CPU, GPU, VRAM,
scratch, persistent-growth and external costs. `p_decision_change` is diagnostic only.
Iteration count, per-subject/question action count and exact action fingerprints prevent
oscillation. When no action has positive net value, or budget is exhausted, `stop` is a
valid scientific result.
