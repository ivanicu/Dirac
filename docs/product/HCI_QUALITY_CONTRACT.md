# Dirac HCI Quality Contract

**Status:** normative v2.1

## 1. Authorization, privacy, and tenancy

Authorization is evaluated for object existence, fields, relationships,
actions, aggregates, export, share, offline cache, AI use, and telemetry. Search
results, counts, graph degree, type-ahead, timing, error text, and disabled
controls must not leak inaccessible facts. Every shared or restored context is
reauthorized; a revoked recipient sees a safe tombstone, not cached labels.

Local durable drafts use platform encryption, tenant-scoped keys, explicit TTL,
logout wipe, and device-policy enforcement. Clipboard, download, export,
notification, screenshot/test fixture, and support bundle events are audited by
policy. Production telemetry and fixtures never cross tenants. Test artifacts
must use synthetic or approved de-identified data.

## 2. Accessibility

WCAG conformance is a floor. Each specialized surface has a named keyboard and
assistive contract:

- 3D: scene/structure tree parity, residue/atom navigation, selection summary,
  camera landmarks, non-colour encodings, and live result announcements;
- molecule editor: atom/bond graph navigation, announced valence/stereochemistry
  errors, keyboard editing, and textual structure summary;
- plate: row/column/well grid semantics, range selection, fill operation
  preview, and consumption/QC announcements;
- charts: data-table equivalent, series navigation, uncertainty and units in
  accessible names, and non-colour differentiation;
- boards/timelines/graphs: ordered list alternative, dependency descriptions,
  move preview, and undo;
- focus and dialogs: deterministic focus entry/return, no focus traps, and
  announced validation/authorization changes;
- live activity: user-controlled announcements, no noisy per-tick updates;
- touch: minimum target sizes, non-hover alternatives, and scanner/glove mode.

All primary actions must be available without drag, hover, fine pointer, colour,
or animation. Full-screen specialist modes retain a route back, current object
summary, and emergency recovery.

## 3. Performance

Performance claims name workload, fixture, hardware class, browser, network,
cold/warm state, percentile, and measurement method. Required measures include
p50/p95/p99 for time to first useful scientific state (TTFUS), action-offer and
preview latency, commit receipt, search, navigation, and selection feedback;
large-surface measures include steady-state FPS, worst-frame p95, memory, data
transfer, cancellation latency, and progress accuracy.

Reference fixtures:

| Surface | Small | Large |
|---|---:|---:|
| structure | 10k atoms | 2m atoms / trajectory |
| portfolio | 100 compounds | 1m virtualized rows |
| work graph | 50 nodes | 100k nodes / server aggregate |
| plate | 96 wells | 1536 wells + overlays |
| dataset | 10k rows | 100m rows / server slice |
| active work | 20 jobs | 100k jobs / server aggregate |

Budgets belong to a versioned performance profile, not prose constants. A
surface may stream progressively only if partialness and progress accuracy are
visible and consequential actions remain disabled until their required truth is
available.

## 4. Human-effort budgets

Optimize after correctness, comprehension, accessibility, and consequence
safety. Budgets are per journey, risk class, entry profile, device, and input
mode. Record p50 and p95 for unnecessary decisions, redundant transcription,
context switches, error recovery, and time-to-outcome. Pointer exploration,
scientific comparison, and required rationale are not automatically friction.
Bulk high-consequence work always receives scope preview even when it adds an
interaction.

## 5. Telemetry

Default telemetry records aggregate performance and contract events, not
scientific content or replayable interaction sequences. Object labels, IDs,
filters, selections, routes, free text, molecular strings, plate wells, and
fine-grained click streams are treated as potentially sensitive. Each event has
purpose, fields, tenant boundary, retention, access roles, opt-out behavior,
test/production separation, and privacy review owner.

Unknown or unrecorded values are `null`/`not_recorded`; they are never encoded as
zero. Telemetry is not evidence that an action was authorized or scientifically
valid. Release guardrails block new event schemas that lack classification and
retention metadata.

## 6. Recovery and offline behavior

Local exploration may continue offline. Canonical commit, material allocation,
external release, and permission-dependent disclosure require online authority.
Drafts show device, tenant, expiry, sync state, and last server base version.
Reconnect performs permission and version revalidation before sync. Recovery
means restoring the maximum currently authorized semantic state; revoked or
deleted data is replaced with a safe tombstone rather than reproduced exactly.
