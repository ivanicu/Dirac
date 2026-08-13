# HCI v2.1 Glossary and Decision Record

## Normative terms

- **Application action:** stable, versioned domain intent decided by an
  application service, independent of UI, CLI, agent, route, or telemetry name.
- **Projection:** a permission-scoped representation and invocation affordance;
  it never owns canonical truth.
- **Question-centred, object-anchored:** work is oriented by a scientific
  question and performed on exact canonical or versioned draft objects.
- **Semantically material:** a field or relation whose change could alter
  eligibility, consequence, interpretation, identity, or an informed decision.
- **Traceable, semantically complete human projection:** an authorized user can
  understand every semantically material fact and trace collapsed/derived facts
  without exposing inaccessible content.
- **ActionOffer:** authoritative statement that an action is presently available
  to an actor for subjects under a permission envelope.
- **ActionPreview:** authoritative proposed consequences tied to source versions
  by a signed, expiring precondition token.
- **ActionReceipt:** authoritative outcome with applied/failed/compensated effects,
  outputs, source versions, and recovery actions.
- **Transport retry:** retransmission of the same operation and idempotency key.
- **Scientific retry:** a new deliberate attempt with lineage to a prior attempt.
- **WorkThread:** scientific question plus a typed work graph.
- **Handoff:** versioned offer/accept/deliver/verify workflow protocol, not route navigation.
- **AttentionItem:** permission-filtered durable request for an accountable response.
- **Frozen input:** exact versioned payload preserved at handoff.
- **Live input:** authorized query intentionally reevaluated when used.
- **Safe tombstone:** existence-safe representation of unavailable, revoked,
  deleted, stale, or superseded context.
- **TTFUS:** time to first useful scientific state, measured against a named fixture.

## Normative versus supporting material

Charter, semantic, quality, acceptance, migration, and workspace briefs are
normative. The generated JSON is the machine contract. The visual map is
guidance. Tests, traces, receipts, screenshots, benchmark artifacts, and manual
protocol logs are evidence. Rationale explains decisions and cannot weaken a
normative `SHALL`.

## Changelog

- **2.1.0 — 2026-08-13:** replaced UI-owned `HumanAction` with channel-neutral
  application actions; split state axes; introduced typed context/selection,
  WorkThread graph, Handoff/Attention/delegation protocols, risk-based coverage,
  and vertical-slice migration; removed implementation-complete overclaim.
- **2.0.0 — superseded:** monolithic form-abolition and visual-compression draft.

## Assumptions

- Existing canonical identity and versioned Program foundations remain available
  during migration.
- The approved Dirac visual language is preserved.
- Backend and projections can coexist behind capability flags during rollout.

## Material risks

- Existing direct command calls may bypass future preview and reauthorization.
- Current URLs expose raw context identities and require a compatibility period.
- Work Item lane semantics cannot represent the required graph without migration.
- Material conservation and aggregate disclosure are not yet proven end to end.
- A reference state machine can pass while production persistence is absent.

## Open questions requiring implementation evidence

- Which authorization engine owns field/relationship/aggregate decisions?
- Which storage transaction or saga owns ActionReceipt durability?
- How are opaque context handles issued, expired, and revoked?
- What WorkThread migration preserves existing Work Item references and history?
- Which objective normalization/identity policies are approved for promotion?
- Which performance profile and device matrix become the first release baseline?
