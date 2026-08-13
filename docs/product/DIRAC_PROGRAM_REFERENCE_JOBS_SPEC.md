# Dirac Program Reference Jobs Specification

**Status:** implementation baseline

**Version:** 1.0

**Date:** 2026-08-12

**Scope:** reproduce the mature user jobs of DAIKON, openBIS, OpenProject,
Chemotion, GSRS, Fragalysis, and Open Targets inside one Dirac Program model.

## 1. Decision

Dirac will not embed seven products, mirror their screens, or create seven
independent stores. It will reproduce their strongest **jobs, invariants, and
handoffs** in a native Dirac backend.

The authority split is:

| Reference system | Job Dirac absorbs | What remains authoritative in Dirac |
|---|---|---|
| DAIKON | discovery stages, compound evolution, advisory AI, preserved failures | Program semantics and the human decision loop |
| openBIS | typed research objects, immutable datasets, parent-child lineage, access boundaries | research-data custody and provenance |
| OpenProject | durable work items, ownership, status, dependencies, phases and gates | teamwork and delivery state |
| Chemotion | reaction-to-material workflow and physical samples | compound form, batch, sample, reaction and analytical context |
| GSRS | stable substance identity, aliases, relationships, validation and approval | canonical chemical identity and identifier resolution |
| Fragalysis | experimental observations, computed designs, curation, tags and restorable snapshots | structure-design collaboration |
| Open Targets | normalized target-disease evidence and association views | external evidence snapshots and their provenance |
| Dirac | one identity spine, one Program aggregate, computation and decisions | cross-system truth and handoff orchestration |

The Program is a **coordination aggregate**, not a container that owns private
copies of compounds, structures, samples, datasets, or jobs.

## 2. Terms: three meanings of “job”

The word `job` must not be overloaded in contracts or UI.

1. **Reference Job** — a user outcome learned from another system, such as
   “register a substance” or “curate a fragment hit.” This document specifies
   these jobs.
2. **Program Work Item** (`work_item`) — one durable unit of scientific intent.
   Its ID survives every workflow stage.
3. **Runtime Job** (`job`) — one execution attempt performed by compute or an
   external executor.

One Work Item may have zero or many Runtime Jobs. One Runtime Job may belong to
exactly one Work Item. Moving a Work Item between stages never creates a copy.

## 3. Two independent lifecycle axes

Dirac must expose two different state machines. They must never be collapsed
into one status field.

### 3.1 Discovery maturity

This is the Program's scientific maturity, adapted from DAIKON:

`discovery → target_validation → hit_discovery → hit_to_lead → lead_optimization → candidate_selection → preclinical`

A Program changes maturity only through an evidence-bearing Stage Gate and a
recorded human Decision.

### 3.2 Current work lane

This is where an individual Work Item is being acted on:

`Understand → Design → Decide → Make → Test & Learn`

A Work Item may move forward, backward, or repeat a lane. This does not
automatically advance the Program's discovery maturity.

## 4. Canonical identity model

Every durable entity has one `ObjectRef { kind, id }` registered in
`app.entity`. Workspaces store references and contextual roles; they do not
mint workspace-local copies.

### 4.1 Chemical and physical identity chain

The minimum identity chain is:

```text
Compound ──has_form──> Compound Form ──produced_as──> Batch
                                               └──sampled_from──> Sample
                                                        └──used──> Experiment
```

| Entity | Meaning | Identity rule |
|---|---|---|
| `compound` | stable registered small-molecule concept | one Dirac ID after identity review; names and external IDs are aliases |
| `compound_form` | defined stereochemical, salt, solvate, isotope or charge form | never folded into a batch or sample |
| `molecule` | a computational structure representation used by a method | may be regenerated or versioned; never substitutes for registered compound identity |
| `batch` | a physically produced lot of one form | unique batch code; immutable parent form |
| `sample` | an aliquot or physical unit drawn from a batch | unique sample ID/barcode; amount, container and custody can change through events |
| `formulation` | a prepared composition containing one or more batches | composition and preparation are versioned |

This makes “the compound shown in Program,” “the compound edited in Design,”
and “the compound requested in Make” the same `compound` reference. Make and
Test add form, batch and sample specificity rather than cloning the compound.

### 4.2 Identity resolution

All imports must pass through an identity-resolution service:

1. normalize the source representation without destroying the raw input;
2. search canonical keys and namespace-qualified aliases;
3. return `matched`, `candidate_match`, `conflict`, or `new`;
4. require curator review for ambiguous stereochemistry, salts, mixtures,
   polymers, biologics, or conflicting identifiers;
5. preserve merge and split history; never silently rewrite foreign keys;
6. register external identifiers as aliases such as `GSRS:UNII`, `CHEMBL`,
   `PUBCHEM_CID`, `FRAGALYSIS`, or a partner namespace.

InChIKey is useful for candidate matching, not a universal proof of substance
identity. GSRS-style validation and approval are separate from creation.

### 4.3 Program membership

Program association is an edge:

```text
Program ──member_of/role──> Canonical Entity
```

The edge records role, rationale, actor, time, and retirement. Removing an
entity from a Program retires the edge; it does not delete the entity.

## 5. Reference Jobs to reproduce

### RJ-01 — Start and govern a discovery Program

**Inspired by:** DAIKON and OpenProject.

**Actor:** Program lead.

**Outcome:** a durable Program with indication, target context, objectives,
hypotheses, members, maturity stage and decision rights.

Required behavior:

- create or resume a Program before entering scientific work;
- display a shareable context chain: Program → Target/Disease → Campaign/Series
  → focused object;
- version objectives and hypotheses instead of overwriting them;
- preserve paused, failed and abandoned paths;
- keep AI recommendations advisory until a human accepts or rejects them.

### RJ-02 — Build a target-disease evidence case

**Inspired by:** Open Targets and DAIKON.

**Actor:** disease biologist or translational scientist.

**Outcome:** an inspectable evidence case, not a naked score.

Required behavior:

- resolve canonical Target and Disease references;
- ingest source-specific evidence records with release, source ID, ontology
  mappings, method, score and retrieval time;
- retain every evidence item and derive target-disease associations as views;
- expose direct and indirect evidence separately;
- bind evidence to hypotheses and decisions as `supports`, `contradicts`,
  `tests`, or `explains`;
- allow rescoring against a frozen source release.

### RJ-03 — Register or resolve a compound

**Inspired by:** GSRS and Chemotion.

**Actor:** compound registrar or medicinal chemist.

**Outcome:** one canonical Compound reference or an explicit unresolved case.

Required behavior:

- exact, substructure and similarity search are discovery aids, not identity
  decisions;
- record names, codes, structures, references and relationships independently;
- validate before approval and record who approved the identity;
- support merge, split and supersession without losing historical aliases;
- return the same Compound reference in every Program and Workspace.

### RJ-04 — Turn a design into traceable physical material

**Inspired by:** Chemotion and GSRS.

**Actor:** synthetic chemist or compound manager.

**Outcome:** a traceable chain from intended compound to released sample.

Required behavior:

- connect Design candidate → synthesis route → reaction → form → batch → sample;
- record reactant and product sample ancestry;
- preserve yield, purity, analytical datasets, amount, unit, location and
  custody events;
- forbid an Experiment from claiming a generic Compound when a physical Sample
  was actually used;
- release or reject a Batch through explicit quality evidence.

### RJ-05 — Curate experimental structural observations

**Inspired by:** Fragalysis.

**Actor:** structural biologist.

**Outcome:** trustworthy experimental observations aligned to a target and site.

Required behavior:

- distinguish raw crystal data, aligned structure, experimental Observation,
  interpreted Pose and computed design;
- preserve maps, coordinates, transformations and source datasets;
- group observations by canonical binding site without changing raw evidence;
- support curator tags and peer-review annotations as separate objects;
- record one accountable main quality status plus independent peer reviews;
- keep experimental and computed objects visibly distinct.

### RJ-06 — Explore, design and share a restorable structural state

**Inspired by:** Fragalysis.

**Actor:** structural or medicinal chemist.

**Outcome:** a reproducible design conversation.

Required behavior:

- filter hits by tags, compound identity, site and 3D geometry;
- compare experimental observations and computed designs in one scene;
- create a content-addressed Analysis Snapshot containing selected ObjectRefs,
  camera, representations, filters, data-release IDs and annotations;
- support both `live` snapshots that follow an explicitly named release channel
  and `preserved` snapshots pinned to immutable versions;
- hand selected candidates to the same Program Work Item in Design or Decide.

### RJ-07 — Plan and move one piece of scientific work

**Inspired by:** OpenProject.

**Actor:** Program member.

**Outcome:** one accountable Work Item with no stage-local duplicates.

Required behavior:

- immutable Work Item identity, type, title and Program membership;
- versioned Work Package specification;
- owner, collaborators, priority, due date, status and dependencies;
- comments, attachments and events attributed to actors;
- lane transitions record from, to, reason, actor and time;
- deliverables are typed canonical ObjectRefs;
- a blocked item records the blocking dependency or reason.

### RJ-08 — Make an evidence-bearing gate decision

**Inspired by:** OpenProject phase gates and DAIKON human approval.

**Actor:** accountable review group.

**Outcome:** approve, reject, hold or recycle with a reproducible rationale.

Required behavior:

- gates contain versioned criteria and required evidence types;
- readiness is computed from criteria, never asserted by UI decoration;
- approval or rejection requires a Decision, actor and assessed time;
- the Decision records alternatives, conflicts, waivers and rationale;
- advancing Program maturity and moving a Work Item are separate commands.

### RJ-09 — Capture an experiment and its data immutably

**Inspired by:** openBIS and Chemotion.

**Actor:** experimental scientist.

**Outcome:** protocol + inputs + execution + immutable datasets + measurements.

Required behavior:

- Experiment references a versioned Protocol, actual Samples, equipment and
  operator;
- raw files are registered as immutable, content-digested Artifacts inside a
  versioned Dataset manifest;
- revised data create a new Dataset version linked by `supersedes`;
- Measurements retain unit, endpoint definition, uncertainty, censoring,
  quality state and source row/file location;
- derived datasets declare parents and the Runtime Job or human operation that
  generated them;
- access control is evaluated at Program, Dataset and sensitive-object scopes.

### RJ-10 — Execute computation without confusing work and execution

**Inspired by:** openBIS provenance and Dirac's execution plane.

**Actor:** scientist or automation.

**Outcome:** repeatable execution attached to scientific intent.

Required behavior:

- Work Item → Mission → Run → Runtime Job remain distinct;
- execution request freezes Program Snapshot, inputs, method release, parameters
  and environment identity;
- retry creates another attempt, not another Work Item;
- outputs register Artifacts, Datasets, Predictions or Measurements and link
  back to the Runtime Job;
- cancellation, failure and partial outputs remain queryable.

### RJ-11 — Close the learning loop

**Inspired by:** DAIKON and the cross-system Dirac aggregate.

**Actor:** multidisciplinary Program team.

**Outcome:** results change a hypothesis, portfolio choice or next Work Item.

Required behavior:

- review results in the context of the original hypothesis and objective;
- promote, reject or hold compounds with evidence-bound rationale;
- create a superseding hypothesis/objective revision when understanding changes;
- generate follow-up Work Items that reference predecessors and the evidence
  that motivated them;
- snapshot the complete Program decision context at every formal gate.

## 6. Native Dirac domain objects

### 6.1 Existing foundation to retain

The current implementation already provides:

- Program, Portfolio, Objective, Hypothesis, Decision, Milestone and Stage Gate;
- canonical `app.entity` plus namespace-qualified aliases;
- Compound, Compound Form and Batch identities;
- stable Work Item, versioned Work Package, transitions and Runtime Job links;
- Program evidence bindings, events and content-digested snapshots;
- Mission → Run → Runtime Job execution identities;
- Dataset Snapshot, Artifact, Measurement and generic object relations.

### 6.2 Required additions

| Addition | Purpose |
|---|---|
| `disease` canonical entity | target-disease evidence cannot use indication text as identity |
| `substance_registration` | validation, approval, merge/split and registrar audit around canonical compounds |
| `sample` relational aggregate | physical aliquot identity, quantity, container, location and custody |
| `protocol_version` | freeze the procedure actually executed |
| `structure_observation` | separate experimental observation from structure file and interpreted pose |
| `analysis_snapshot` | restore a collaborative molecular scene independently of Program snapshots |
| `external_evidence_record` | source-release-pinned Open Targets-style evidence atom |
| `target_disease_association` read model | derived aggregation; never the sole evidence record |
| `annotation` and `review` | curator tags, main status and peer review without mutating evidence |
| `dataset_version` manifest | immutable files, schema, lineage, access policy and supersession |
| `work_comment` and `work_attachment` | collaboration history on the stable Work Item |
| `gate_criterion_assessment` | machine-computed readiness with evidence-level explanations |

## 7. Command boundary

Commands are actor-attributed, idempotent and return canonical ObjectRefs.

```text
program.create
program.context.link
program.objective.record
program.hypothesis.record
program.work_item.create
program.work_item.revise
program.work_item.transition
program.work_execution.attach
program.stage_gate.assess
program.decision.record

identity.resolve
compound.register
compound.validate
compound.approve
compound.merge
compound.split
sample.create
sample.transfer
batch.release

evidence.import_release
evidence.bind
experiment.record
dataset.commit_version
measurement.record

structure.observation.register
structure.annotation.record
structure.review.record
structure.analysis_snapshot.create
```

No workspace receives a `cloneCompound`, `copyWorkItem`, or
`duplicateExperimentContext` command.

## 8. Read models and user experience

| Surface | Primary question | Required context |
|---|---|---|
| Program Home | Why does this Program exist, what is its maturity, and what needs attention? | Program, Target/Disease, objectives, gates |
| Understand | What evidence and structures support the current hypothesis? | Program + focused Work Item + target/site |
| Design | What change should we make and why? | same Work Item + canonical compounds/poses |
| Decide | Which compounds or series should advance? | same Work Item + portfolio evidence |
| Make | What material must be produced and released? | same Work Item + compound/form/route/batch |
| Test & Learn | What sample was tested, what happened, and what changes next? | same Work Item + sample/protocol/experiment/results |
| Work Queue | What is running, blocked, failed or awaiting approval? | cross-Program Runtime Jobs and attention items |
| Knowledge | What canonical entity or evidence record am I looking for? | global resolver and provenance filters |

Every surface must show the same resolvable context tokens. Switching surfaces
changes the view and allowed actions, not entity identity.

## 9. Hard invariants

The database, not only application code, must enforce:

1. unique `(program_id, work_key)` for stable Work Items;
2. a Runtime Job belongs to at most one Work Item;
3. every polymorphic reference resolves through `app.entity`;
4. one namespace-qualified alias resolves to one canonical entity at a time;
5. Program membership never owns or deletes the referenced entity;
6. Batch has exactly one parent Compound Form;
7. Sample has exactly one immediate material source and preserves ancestry;
8. Experiment input identifies the actual Sample when physical material exists;
9. raw Artifact bytes and committed Dataset versions are immutable;
10. derived data records parents and producer identity;
11. external evidence records name source and source release;
12. experimental observations and computed predictions cannot share a type;
13. approved/rejected Stage Gate requires a Decision and assessed criteria;
14. superseding a scientific atom preserves every older revision;
15. AI cannot approve an identity, gate or portfolio decision without a named
    human actor under an explicit delegated policy.

## 10. Connector policy

Initial delivery is a native Dirac implementation. Connectors are optional
adapters, not runtime prerequisites.

- An adapter imports or exports canonical records with `source`, `external_id`,
  `source_version`, `retrieved_at`, checksum and mapping version.
- Imported raw payloads remain available as immutable Artifacts.
- Mapping errors enter a review queue; they never create silent duplicates.
- Re-import is idempotent for the same source record and release.
- Source deletion does not cascade into Dirac; it records a tombstone or
  retraction event.
- No reference project's source code is copied unless its license and notices
  are reviewed separately. This specification reproduces behavior and data
  contracts, not implementation text.

## 11. Implementation sequence

### Phase A — Identity completion

Add Disease, Sample and substance-registration governance. Implement identity
resolution, ambiguity review, merge/split history and the full
Compound → Form → Batch → Sample chain.

**Exit gate:** the same compound resolves to one ID in Program, Design, Decide,
Make and Test; two physical samples remain distinct.

### Phase B — Work and gate completion

Add Work Item comments, attachments, collaborators, blockers and criterion-level
gate assessment. Project phase changes remain separate from Work Item lanes.

**Exit gate:** a gate cannot advance through an empty status change; the full
decision and evidence can be reconstructed.

### Phase C — Research data governance

Add Protocol versions, immutable Dataset manifests, lineage, custody and scoped
access controls based on openBIS jobs.

**Exit gate:** a result can be traced to sample, protocol, raw file, derivation,
producer, method and Program Snapshot.

### Phase D — Structure collaboration

Add experimental Structure Observations, annotations, reviews and restorable
Analysis Snapshots. Integrate these with the existing Mol* surface.

**Exit gate:** another user can open a preserved URL and recover the same data
release, selected structures, camera, filters and annotations.

### Phase E — External evidence graph

Add source-release-pinned Open Targets ingestion, Disease identity, evidence
records and derived association views.

**Exit gate:** every displayed target-disease score expands to constituent
evidence and can be reproduced from a named release.

### Phase F — Closed-loop Program UX

Expose all Reference Jobs as context-aware actions and handoffs across the five
lanes. Add attention views for unresolved identity, blocked work, failed runs,
unreleased material, unreviewed results and gate readiness.

**Exit gate:** a user can complete one end-to-end discovery loop without copying
an ID or manually reconstructing context.

## 12. Acceptance scenarios

### A. One compound everywhere

Register a compound from a GSRS alias, select it in Program, open it in Design,
request a route, create a Batch and allocate a Sample to an Experiment. Every
screen resolves the same Compound ID; only form, batch and sample add physical
specificity.

### B. One Work Item through every lane

Create one Work Item in Understand, transition it through Design, Decide, Make
and Test & Learn, send it back to Design, and attach three Runtime Jobs. The
Work Item ID never changes; all transitions and attempts remain visible.

### C. Reproducible negative result

Commit raw assay files, record a failed/negative Measurement, bind it as
contradicting a Hypothesis, revise that Hypothesis and reject a compound. The
negative result and rejected path remain queryable after the Program advances.

### D. Defensible gate

Attempt to approve a gate with missing criteria and receive a structured denial.
Attach the required evidence, record waivers and alternatives, create a human
Decision, then approve. A Program Snapshot reproduces the full review context.

### E. Restorable structure collaboration

Select experimental observations and computed designs, add a curator tag and
peer review, preserve the analysis, then open its URL in a new session. The
same release, objects, filters, representations and camera are restored.

### F. External evidence refresh

Import two Open Targets releases. Existing evidence is not overwritten. The
association view can compare releases, explain score changes and resolve every
record to its source identifiers.

## 13. Explicit non-goals

- reproducing every screen, wiki, calendar, billing or administration feature
  of the reference products;
- using OpenProject's generic project phase as a substitute for scientific
  maturity;
- treating a SMILES string, name, InChIKey or external database row as
  sufficient universal identity;
- storing mutable files directly on scientific records;
- collapsing Compound, Form, Batch and Sample into one object;
- collapsing Work Item, Mission, Run and Runtime Job into one object;
- treating an aggregate score, AI recommendation or green UI state as evidence.

## 14. Reference basis

The specification is behaviorally derived from official project documentation:

- [DAIKON](https://saclab.github.io/daikon/) — discovery stages, advisory AI,
  provenance, compound evolution and preservation of failures.
- [openBIS data modelling](https://openbis.readthedocs.io/en/20.10.12-plus/user-documentation/advance-features/openbis-data-modelling.html)
  — Space/Project/Collection/Object/Dataset hierarchy, access boundaries and
  parent-child lineage.
- [OpenProject work packages](https://www.openproject.org/docs/user-guide/work-packages/)
  and [project lifecycle](https://www.openproject.org/docs/system-admin-guide/projects/project-life-cycle/)
  — durable IDs, assignments, relations, phases and gates.
- [Chemotion samples](https://chemotion.net/docs/eln/ui/elements/samples) —
  physical samples, compound structure, amount, purity and external labels.
- [GSRS](https://gsrs.ncats.nih.gov/) and its
  [API](https://gsrs.ncats.nih.gov/api-documentation) — standardized substance
  definitions, identifiers, aliases, relationships, validation and approval.
- [Fragalysis user guide](https://fragalysis.readthedocs.io/en/latest/user_guide.html)
  — experimental observations, computed sets, tags, reviews, snapshots and
  preserved downloads.
- [Open Targets data model](https://platform-docs.opentargets.org/getting-started)
  and [evidence model](https://platform-docs.opentargets.org/evidence) — canonical
  entities, source evidence and derived target-disease associations.
