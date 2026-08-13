# Dirac Human Interface Charter

**Status:** normative v2.1
**Scope:** every human, CLI, agent, automation, and external-collaborator
projection over Dirac application actions
**Visual boundary:** preserve the approved Dirac visual language; interaction
architecture may change without rebranding the product.

## 1. Product definition

Dirac is a **question-centred, object-anchored scientific workbench**. A user
begins with a scientific question or operational responsibility, acts on
canonical scientific objects, and sees the evidence, consequence, ownership,
and next handoff without reconstructing hidden system state.

The UI is a traceable, semantically complete human projection of the
application. It does not own command plans, authorization, scientific identity,
inventory truth, or workflow truth. Human, CLI, MCP/agent, and automation
surfaces invoke the same stable application actions.

## 2. Correctness before convenience

The following invariants precede click reduction, visual compression, and
personalization. A surface must refuse or defer an action rather than violate
one of them.

- **HCI-INV-001 Authorization:** the application reauthorizes reads, previews,
  commits, shares, exports, and notifications at the moment of consequence.
- **HCI-INV-002 Identity:** a molecule, compound, batch, sample, experiment,
  dataset version, or work node has one canonical identity; workspaces project
  it rather than recreate it.
- **HCI-INV-003 Units and conditions:** scientific quantities retain units,
  conditions, uncertainty, and method context.
- **HCI-INV-004 Material conservation:** reservations, splits, transfers,
  consumption, and release cannot create or double-spend physical material.
- **HCI-INV-005 Provenance:** every durable scientific claim or external effect
  records actor, source versions, method, time, and lineage.
- **HCI-INV-006 Version and freshness:** stale previews, superseded evidence,
  revoked access, and changed source objects are visible and revalidated.
- **HCI-INV-007 Accountability:** consequential decisions identify the
  accountable actor and structured rationale; AI may propose but not silently
  assume accountability.
- **HCI-INV-008 External consequence:** release, submission, dispatch, export,
  and destructive effects show scope and consequence before commit.

## 3. Human outcomes

In order: orientation, recognition, comparison, decision, action, feedback,
recovery, collaboration, handoff, and traceability. Optimization means **zero
redundant transcription and minimum unnecessary decisions**, not zero typing.
Scientific rationale, exceptions, and risk acceptance are product data when
they cannot be derived safely.

Direct manipulation is an affordance, never the source of semantic truth.
Button, context menu, keyboard command, drag/drop, touch gesture, CLI, and agent
invocation may all project the same `ApplicationActionDefinition`.

## 4. Role and task entry profiles

A profile is not a permanent persona. A person may hold several entitlements
and responsibilities and choose an active task perspective.

| Entry profile | Frequent task | Authority / error cost | Expertise | Typical environment |
|---|---|---|---|---|
| Program lead | frame questions, gates, priorities | approves decisions; high portfolio cost | high domain, medium tool | desktop, meetings, interruptions |
| Structural scientist | inspect structures and sites | authors structural interpretation; high scientific cost | high domain/product | large display, 3D input |
| Medicinal chemist | design and compare compounds | proposes/promotes chemistry; high identity cost | high domain | desktop, pen/pointer |
| Synthesis/material operator | route, batch, reserve, release | changes physical custody; very high material cost | high process | bench, gloves, scanner/touch |
| Assay scientist | plate, execute, QC, interpret | consumes samples and releases results | high domain | bench + desktop |
| Data/compute scientist | datasets, runs, models | submits/cancels compute, publishes derived evidence | high technical | desktop/CLI |
| Reviewer/governance | review, waive, audit, export | accepts risk; very high compliance cost | medium-high | desktop |
| External CRO/partner | accept scoped work, deliver | limited program scope; leakage cost | variable | external network |
| Agent/automation operator | delegate and supervise | bounded delegated authority | technical | CLI/MCP/work queue |

Every workspace brief records frequency, authority, error cost, expertise,
access scope, modality, collaboration, pain, and success for its relevant entry
profiles rather than applying one generic layout to all users.

## 5. Interaction doctrine

- Show recognizable objects, relationships, ownership, and scientific state;
  never require a normal user to type an internal ID or raw JSON.
- Use domain-specific forms when the schema is itself the scientific object
  (protocol, objective, plate plan, rationale). Abolish generic schema forms and
  clerical duplication, not all forms.
- Let exploration remain transient; name, pin, save, share, or commit only when
  the user's intent becomes durable.
- Present one primary issue and a visible count of additional issues; never hide
  secondary blockers merely to keep a card simple.
- Give every durable action a visible receipt and recovery path.
- Preserve stable global anchors, while allowing full-screen specialized modes
  for 3D, molecule editing, plates, and large comparisons.

## 6. Semantic completeness

A human projection is semantically complete when, within the actor's current
permissions, it preserves every field and relation that could change the
meaning, eligibility, consequence, or interpretation of the action. Operational
metadata may be collapsed but remains inspectable; archival-only fields may be
linked; derived fields must expose derivation; inaccessible fields must not leak
existence. Two projections are observationally equivalent when an authorized
actor would make the same informed decision and produce the same application
action under either projection.

## 7. Status language

This charter and the semantic contracts are normative. Workspace briefs are
normative designs. Generated schemas are machine contracts. Screenshots and
journey logs are evidence. A requirement is not implemented merely because its
term appears in source code.
