# Dirac roadmap

Current as of 2026-08-17. This roadmap starts from the source-derived state in
[`STATUS.md`](STATUS.md); it does not repeat completed implementation history.

## Direction

Dirac grows as one molecular-discovery operating system. New capability should add a
canonical ObjectKind or relation only when necessary, a Method for computation, a Command
for application behavior, durable state and evidence, and then one or more projections.
It must not create a private API, cache, Job state machine, error vocabulary, context store
or mol\* scene.

## Current priorities

### 1. Finish the authoritative HCI action spine

- Replace route-local and form-local mutations with offer, preview, commit and receipt.
- Carry versioned context, selection, authorization and idempotency through browser, CLI
  and agent projections.
- Make stale state, duplicate retry, partial saga and recovery first-class outcomes.
- Complete a failure-complete Structures → Design → Campaigns vertical slice before
  declaring broader HCI readiness.

Source: [`docs/product/HCI_MIGRATION_PLAN.md`](docs/product/HCI_MIGRATION_PLAN.md).

### 2. Connect the remaining product Views

- Preserve the 8-Workspace / 30-View information architecture.
- Connect Views only when their Commands, durable objects, permissions, errors and recovery
  paths exist.
- Keep shell-only Views visibly partial; navigation completeness is not capability.

### 3. Harden Program as the scientific operating root

- Finish work graphs, handoffs, stage gates, evidence admission and lineage.
- Preserve one identity chain from Program intent through Run, Job, Artifact and Decision.
- Make material, sample, protocol, experiment and external-evidence invariants transactional
  rather than UI conventions.

### 4. Validate Motif scientifically, not only architecturally

- Keep dataset versions, runtime locks, model gates and routing decisions reproducible.
- Benchmark models and acquisition policies against declared controls and uncertainty.
- Treat docking, MD, RBFE and OpenFE outcomes as evidence with explicit failure and
  applicability boundaries.
- Separate executable workflow coverage from method validation and prospective utility.

### 5. Production readiness

- Add a supported environment/bootstrap path for the full scientific stack.
- Exercise backup, restore, migration rollback boundaries and disaster recovery.
- Complete remote authentication, HTTPS, scopes, quotas, audit redaction and artifact
  authorization before any WAN exposure.
- Add observability and operator runbooks that do not depend on the application being up.

## Rewrite tripwires

A change is off-path if it:

- makes an adapter own scientific logic;
- embeds a large scientific result in a response instead of an Artifact;
- introduces another Job or error state machine;
- gives a Workspace its own scientific context or mol\* instance;
- treats a route transition as a scientific handoff;
- stores presentation state as evidence;
- equates a registered Method or passing transport test with scientific validation;
- starts a duplicate Dirac service instead of rebuilding the intended supervised bundle
  (full shell on 1360 or Discovery Lab on 1370).

## Deliberate non-goals for the current phase

- public unauthenticated hosting;
- claiming all 30 Views are scientifically implemented;
- fabricating browser-only approximations for unavailable compute;
- replacing upstream mol\* internals without a demonstrated Dirac requirement;
- autonomous architecture mutation by the observed twin.
