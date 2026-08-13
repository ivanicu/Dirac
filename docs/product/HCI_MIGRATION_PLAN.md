# Dirac HCI v2.1 Migration Plan

**Status:** executable staged plan
**Rule:** migrate by failure-complete vertical slice, not by painting eight
workspace shells.

## Phase 0 — freeze and measure

- Preserve the approved visual language and record reference screenshots.
- Inventory every current command, handler, authorization point, version check,
  idempotency policy, and UI invocation.
- Mark semantics `reusable`, `adapter-required`, `unknown`, or `replace` with an
  integration witness. Existing canonical identity is presumed reusable;
  command semantics are not.
- Baseline primary journeys, accessibility, large fixtures, errors, and rollback.
- Normalize glossary and prohibit new generic type/ID or raw-JSON primary paths.

**Exit:** capability audit and baseline evidence exist. No implementation-status
upgrade occurs here.

## Phase 1 — semantic spine

- Implement typed context and `SelectionRef` lifecycle.
- Implement orthogonal state axes.
- Implement stable channel-neutral action definition, offer, server preview,
  signed precondition token, commit, receipt, idempotency, and stale diff.
- Implement WorkThread graph, Handoff lifecycle, AttentionItem, permission
  envelope, collaboration events, and delegation envelope.
- Generate machine contracts from one canonical source.

**Exit:** capability and adversarial tests pass; projection adapters cannot
invent readiness or bypass authorization.

## Phase 2 — authoritative thin slice

Deliver `Structures -> Design -> Campaigns`:

1. exact versioned structural selection becomes a named site;
2. a handoff offer freezes the necessary structure inputs;
3. Design accepts, edits a versioned molecular proposal, and previews promotion;
4. the authority standardizes identity and returns duplicate/new identity
   outcome;
5. Campaigns adds the same canonical compound and records objective version.

**Exit:** browser, CLI, and agent projections produce observationally equivalent
receipts against a real repository transaction or declared saga.

## Phase 3 — make the slice failure-complete

Add revoked permission, stale source, duplicate retry, deliberate new attempt,
partial saga, reconnect, deleted object, concurrent molecule edit, inaccessible
duplicate, and cross-tenant cases. Complete keyboard/screen-reader/touch and
performance evidence. Keep the legacy path behind an explicit developer/admin
fallback and retain rollback.

## Phase 4 — Make and Test

Add material reservations/conservation, batch/sample/container/custody/QC,
plate/well/dilution/randomization/blinding/scheduling/deviation/result protocols,
and result return into the work graph.

## Phase 5 — Program, Knowledge, and Compute

Project the same semantic spine into Program graph/timeline/gates, large-scale
permissioned evidence search/admission/retraction, and Mission/Run/Attempt/Job
operations with quota, retry, cancel race, environment, provenance, and result
validity.

## Phase 6 — remove legacy

Remove each legacy form or static handoff only after the replacement's evidence
matrix passes, users can recover or migrate drafts, telemetry shows no critical
regression, and rollback has been exercised. Delete the generic primary UI last;
retain narrow admin/recovery tooling if it remains justified.

## Rollback and status

Every phase ships behind a projection capability flag while domain writes remain
compatible. Rollback switches projection routing, never rewrites canonical
scientific history. A phase is `designed`, `reference-tested`, `integrated`, or
`released`; those labels must not be collapsed into “done.”
