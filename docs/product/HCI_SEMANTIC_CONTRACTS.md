# Dirac HCI Semantic Contracts

**Status:** normative v2.1
**Authority:** application services and domain repositories; projections are
clients of these contracts.

## 1. Scientific context

`ScientificContext` is a typed, permission-scoped navigation and action frame:

```text
ScientificContext {
  tenant, active_program?, active_work_thread?, active_question?,
  object_path[], focus?, selection?, comparison?,
  source_versions{}, permission_snapshot, origin, generation
}
```

Context transition rules are explicit:

- changing tenant clears every subordinate value;
- changing Program clears any work thread, question, path, focus, and selection
  not authorized and linked to the new Program;
- changing work thread retains only objects reachable from that thread;
- changing a parent object invalidates incompatible descendants;
- permission revocation removes labels as well as actions and records a local
  `unauthorized` tombstone without revealing prior content;
- stale/deleted/superseded objects remain represented only by safe tombstones;
- restoration reauthorizes opaque references before displaying labels;
- sensitive identifiers, SMILES, labels, filters, and selections are not placed
  in shareable URLs. A deep link carries an opaque, expiring context handle.

## 2. Selection

`SelectionRef` is a tagged union, not `ObjectRef[]`:

```text
ObjectSelection        { refs[], versions{}, mode }
StructureSelection     { structure_ref, model, chain?, residues?, atoms?,
                         altloc?, assembly?, snapshot_ref? }
MolecularSelection     { molecule_ref, atom_indices?, bond_indices?,
                         representation_version }
MaterialSelection      { sample_ref, quantity, unit, reservation_ref?, version }
PlateSelection         { plate_ref, wells[], layout_version }
DatasetSliceSelection  { dataset_version_ref, filter_ast, projection[], digest }
DerivedSetSelection    { definition_ref, member_digest, count, source_versions }
```

Every selection declares `scope` (`transient|named|comparison|bulk`), locality,
version, size, mixed-eligibility policy, and lifecycle. Surfaces support clear,
pin, save, share, and safe invalidation. Aggregate selection may name a server-
side set without exposing unauthorized members.

## 3. Orthogonal state

No single status field may conflate these axes:

| Axis | Required values |
|---|---|
| `DraftState` | absent, local, syncing, durable, superseded, discarded |
| `ConnectivityState` | online, degraded, offline |
| `AvailabilityState` | available, partial, unavailable, unauthorized, unknown |
| `SubmissionState` | not_submitted, previewed, submitting, accepted, rejected |
| `ExecutionState` | not_started, queued, running, waiting, blocked, failed, cancelled, completed |
| `FreshnessState` | current, changed, stale, superseded, retracted, unknown |
| `ReviewState` | not_required, pending, accepted, changes_requested, rejected, waived |

A projection may summarize these axes but must let the user inspect each one.

## 4. Channel-neutral action protocol

Stable action IDs express domain semantics, not workspace location. Example:
`design.proposal.promote@2`, not `campaigns.design.promote-button`. Route,
projection surface, telemetry event, and action identity are separate values.

```text
ApplicationActionDefinition
  id, version, intent, input_schema, consequence_class,
  authorization_policy, precondition_policy, idempotency_policy,
  conflict_policy, transaction_policy, receipt_schema

ActionOffer
  offer_id, action_id/version, actor, subjects, selection_digest,
  permission_envelope, preconditions, expires_at

ActionPreview
  preview_id, precondition_token, source_versions, proposed_effects,
  warnings, required_acknowledgements, expires_at

ActionReceipt
  operation_id, action_id/version, attempt_id, actor, status,
  applied_effects[], failed_effects[], compensation[], output_refs[],
  source_versions, committed_at, recovery_actions[]

HumanActionProjection | CliActionProjection | AgentActionProjection
  presentation only; each invokes the same offer/preview/commit protocol
```

The authority computes offers and previews. Commit reauthorizes the actor,
verifies the signed preview token and source versions, and either applies one
server transaction or an explicit saga with per-effect receipts and
compensation. A stale preview returns a semantic diff and a new preview option.
Transport retry reuses the idempotency key; a deliberate scientific retry uses
a new attempt ID and preserves lineage to the prior attempt.

Conflict policy is declared per object type: Program metadata may field-merge;
scientific conclusions require explicit supersession; molecule edits branch;
material quantities use reservations and serializable conservation checks;
plate layout edits require layout-version comparison; decision records append.

## 5. Work graph

`WorkThread` holds a scientific question and a directed, typed work graph. A
`WorkNode` replaces the fiction of one universal Work Item. Edges include
`depends_on`, `informs`, `blocks`, `delegates_to`, `supersedes`, `returns_to`,
`shares_dependency`, `splits_to`, `merges_from`, `fans_out`, and `fans_in`.

Nodes can have multiple owners, one accountable role, watchers, external
participants, due windows, eligibility, risk, evidence, and action history.
Graph operations preserve cycles policy, source versions, permissions, and
critical-path uncertainty. Views may render a board, timeline/Gantt, graph,
queue, or compact next-action list from the same graph.

## 6. Handoff protocol

A handoff is an application object, not a route button.

```text
Handoff {
  id, schema_version, work_thread_ref, source_node_refs[], target_scope,
  frozen_payload_refs[], live_query_refs[], acceptance_contract,
  permission_envelope, lifecycle, delivery_parts[], rationale,
  offered_by, accountable_role, sla, escalation, version
}
```

Lifecycle: `draft -> offered -> accepted|rejected|returned -> delivered ->
verified`, with `cancelled` and `superseded` terminal alternatives. Target scope
may be a person, role, team queue, service, or multi-recipient fan-out.
Readiness is derived from the acceptance contract; `missing` is the structured
set of unsatisfied clauses, never a second source of truth. Partial delivery and
verification are explicit. Rationale contains typed reason codes, evidence
references, risk acceptance, and optional free text.

## 7. Attention and collaboration

`AttentionItem` is a durable, permission-filtered request with subject, reason,
severity, due window, accountable role, watchers, available actions, source
versions, deduplication key, and lifecycle. Comments, mentions, reviews,
decisions, waivers, and presence are protocol objects, not unstructured UI
decorations. Concurrent collaborators see version changes; presence never
confers write authority.

## 8. AI delegation

An agent receives a bounded `DelegationEnvelope`: actor identity, tenant,
allowed actions, object scope, data-use scope, expiry, spend/compute limits,
required human approvals, and prohibited consequences. It returns proposals,
tool receipts, evidence, uncertainty (`calibrated`, `uncalibrated`, or
`unknown`), and unresolved questions. AI cannot expand its permission envelope,
hide source failures, convert unknown uncertainty to a score, or commit a
high-consequence action without the required accountable human.

## 9. Authoritative ownership

Application services own offers, previews, commits, authorization, concurrency,
idempotency, handoff lifecycle, material conservation, and receipts. Domain
repositories own canonical identity and versioned state. Projection modules own
layout, interaction affordances, local transient exploration, and accessible
rendering. Telemetry owns neither truth nor authorization.
