# Dirac HCI Acceptance Contract

**Status:** normative v2.1
**Machine source:** `hci/human-interface-v2.source.mjs`

## 1. Requirement evidence

Every normative requirement has:

```yaml
id: HCI-AREA-NNN
condition: observable precondition
actor: task entry profile or system actor
behavior: required observable behavior
authority: module or service that decides truth
failure: explicit refusal, degradation, or recovery behavior
evidence: automated test, integration witness, or manual protocol
owner: accountable code/domain owner
waiver: none, or approver plus expiry and recorded risk
```

Word matches and screenshots alone are not integration evidence. An
authoritative witness contains the offer/preview/commit/receipt or read/query
trace, actor and permission scope, source versions, durable result, and visible
projection outcome.

## 2. Evidence classes

- **Capability tests:** pure contract/type/state-machine tests.
- **Authoritative integration tests:** real application service plus repository
  transaction or declared saga.
- **E2E smoke journeys:** browser/CLI/agent invoke the same action and observe
  equivalent receipts.
- **Manual specialist protocols:** visual, 3D, molecule, plate, touch, and
  assistive-technology evaluation with named fixtures.
- **Chaos and adversarial tests:** stale preview, duplicate transport, partial
  effect, cancellation race, permission revocation, concurrent edit, offline
  reconnect, cross-tenant and existence-leak attacks.

## 3. Risk-based coverage

Coverage is pairwise across role, authority, data class, workspace, device,
input mode, connectivity, size, and consequence class, with exhaustive cases
only for safety invariants. Required journey families include:

1. Program lead frames a question, branches work, observes critical-path
   uncertainty, and records a gate decision.
2. Structural scientist selects exact residues/atoms against a versioned
   structure snapshot and offers the site to Design.
3. Medicinal chemist edits stereochemistry/tautomer/protonation state, detects
   duplicates, promotes a proposal, and adds the canonical compound to a
   Campaign without re-entry.
4. Portfolio owner compares mixed-eligibility candidates against a versioned
   objective and fans work out to Make, Test, and Compute.
5. Synthesis operator reserves, splits, transfers, releases, and concurrently
   attempts to over-allocate material; conservation must hold.
6. Assay scientist lays out wells, randomizes/blinds, consumes reserved samples,
   records deviations/QC, and returns results to SAR/decision.
7. Knowledge user searches a large permissioned graph, admits and retracts
   evidence, and observes derived invalidation without count leakage.
8. Compute user distinguishes Mission/Run/Attempt/Job, retries deliberately,
   cancels during completion, and traces environment/result validity.
9. Administrator changes permissions and verifies immediate revocation across
   search, share, cache, AI, export, and notification.
10. Executive reads a concise Program projection without access leakage.
11. External CRO accepts scoped work, receives frozen inputs, partially
    delivers, and passes verification.
12. Agent acts under a delegation envelope and is refused when it exceeds
    scope or cannot state uncertainty.
13. Screen-reader and keyboard user completes each primary journey.
14. Touch/scanner user completes bench material and plate journeys.
15. Concurrent editors receive semantic diffs and declared conflict policy.
16. Stale preview, offline draft, deleted subject, superseded evidence, and
    cross-tenant deep link each recover safely.

## 4. Formal journey fixture

```yaml
id: AJ-STRUCTURE-DESIGN-CAMPAIGN-001
roles: [structural_scientist, medicinal_chemist]
context:
  program: fixture-program
  work_thread: fixture-thread
  structure_snapshot: fixture-structure-v3
steps:
  - select exact residues and save a named site
  - preview and offer the site to Design
  - accept frozen structural inputs
  - promote a versioned molecular proposal
  - add the resulting canonical compound to the campaign
expected_receipts:
  - structures.site.save@1
  - handoff.offer@1
  - handoff.accept@1
  - design.proposal.promote@2
  - campaigns.candidate.add@1
negative_variants:
  - stale structure snapshot
  - revoked compound visibility
  - duplicate molecular identity
  - duplicate transport with same idempotency key
  - deliberate retry with new attempt id
```

## 5. Release gate

A workspace is not complete until its projection brief exists and all required
capability, authoritative integration, E2E, manual, and adversarial evidence is
linked by requirement ID. The first product release additionally requires the
failure-complete `Structures -> Design -> Campaigns` vertical slice, reversible
migration, permission/existence-leak review, accessibility protocol, and
observed p50/p95 performance profile.

No waiver may bypass authorization, identity, unit/condition integrity,
material conservation, provenance, or tenant isolation. Other waivers require
an accountable approver, recorded risk, expiry, and rollback signal.
