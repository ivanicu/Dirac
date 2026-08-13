# HCI v2.1 Command Capability Audit

**Snapshot:** 2026-08-13
**Purpose:** prevent the frontend from assuming that an existing command is a
complete application action.

| Capability | Current evidence | Classification | v2.1 adapter/replacement |
|---|---|---|---|
| Canonical ObjectRef vocabulary | generated domain registry and command validation | reusable foundation | add new WorkThread/Handoff/Attention kinds only through domain migration |
| Canonical compound identity | `compound.register` standardizes, deduplicates, and links one Program entity | adapter-required | wrap in `design.proposal.promote@2` preview/commit; preserve safe duplicate outcome and inaccessible-identity policy |
| Program optimistic concurrency | Program writes require `expected_version` | reusable foundation | action preview captures source version; commit returns semantic diff, not generic invalid parameters |
| Program request idempotency | mutations accept `request_id` and memory/Postgres repositories deduplicate | reusable foundation | distinguish transport key from deliberate scientific `attempt_id`; reject key/payload mismatch |
| Program Work Item | one item moves through fixed lanes with dependency edges | replace/compatibility | migrate to WorkThread question graph supporting typed split/merge/fan/delegation/supersession/return edges |
| Static UI handoff | local ObjectKind presence gates route and may directly invoke `compound.register` | replace | server Handoff object and ActionOffer/Preview/Receipt; route becomes post-receipt suggestion only |
| Scientific context | client store holds selected `ObjectRef[]` and raw query-string identities | replace/compatibility | typed context transitions, SelectionRef union, opaque reauthorized context handles |
| Reference-job record commands | many Program reference jobs accept semantically typed records | adapter-required | audit per command for authorization, version, unit, conservation, conflict, receipt, and recovery semantics |
| Long-running commands | command registry distinguishes Job policy and dispatcher validates result job identity | reusable foundation | project Mission/Run/Attempt/Job and attach originating action/attempt/receipt |
| Command envelopes/traces | dispatcher returns stable envelope metadata and records traces | reusable foundation | ActionReceipt remains authoritative; telemetry/trace cannot substitute for transaction evidence |
| Material records | batch/sample/custody concepts exist | unknown until invariant audit | require reservation and serializable conservation tests before any HCI release claim |
| Authorization/disclosure | security modules exist but current HCI does not prove field/relationship/aggregate policy | unknown | application authorization envelope plus search/count/share/cache/AI/telemetry leak tests |

Classification meanings:

- `reusable foundation`: tested behavior can be composed, but does not by itself
  satisfy the HCI action protocol.
- `adapter-required`: useful domain command whose inputs/outputs must be wrapped
  by authoritative offer/preview/commit/receipt semantics.
- `replace/compatibility`: old behavior remains only behind an explicit migration
  boundary until the vertical slice passes.
- `unknown`: no implementation claim until a focused audit and integration
  witness exist.
