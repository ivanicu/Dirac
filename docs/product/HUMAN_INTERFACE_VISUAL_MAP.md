# Dirac HCI v2.1 — Visual Map

This map is explanatory. Normative behavior lives in the charter, semantic,
quality, acceptance, migration, and workspace contracts.

## 1. What the user is actually doing

```mermaid
flowchart LR
    Q["Scientific question"] --> C["Authorized context"]
    C --> X["Explore and select exact objects"]
    X --> D["Compare evidence and consequences"]
    D --> A["Invoke one semantic action"]
    A --> R["Read authoritative receipt"]
    R --> W["Update work graph and attention"]
    W --> H["Offer or accept a handoff"]
    H --> Q
```

The loop is question-centred and object-anchored. A workspace is a projection,
not a separate database or a mandatory linear stage.

## 2. The authority boundary

```mermaid
flowchart TB
    subgraph P["Projection channels"]
      UI["Human UI"]
      CLI["CLI"]
      AG["Agent / MCP"]
      AU["Automation"]
    end

    UI --> O["ActionOffer"]
    CLI --> O
    AG --> O
    AU --> O
    O --> V["Server ActionPreview<br/>effects · warnings · source versions · signed token"]
    V --> K["Commit<br/>reauthorize · revalidate · transaction or saga"]
    K --> R["ActionReceipt<br/>applied · failed · compensation · recovery"]
    R --> UI
    R --> CLI
    R --> AG
    R --> AU

    S["Domain repositories<br/>identity · versions · material · provenance"] --> O
    S --> V
    S --> K
```

Direct manipulation, buttons, keyboard commands, drag/drop, and command-line
calls are affordances over the same stable application action. The UI never
invents readiness or owns the command plan.

## 3. The information architecture

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ PROJECT / PROGRAM > QUESTION > CURRENT OBJECT     SEARCH  WORK QUEUE     │
├─────────┬───────────────────────────────────────────────────┬────────────┤
│ TASK    │ Human question + local view tabs                  │ Identity   │
│ DOMAINS │                                                   │ Evidence   │
│         │ Specialized workbench                             │ Lineage    │
│ Under-  │ 3D · molecule · landscape · route · plate · graph │ Actions    │
│ stand   │                                                   │ History    │
│ Design  │ Selection / comparison shelf                     │            │
│ Decide  ├───────────────────────────────────────────────────┤            │
│ Make    │ Next valid actions · blockers · handoff state     │            │
│ Test    │                                                   │            │
└─────────┴───────────────────────────────────────────────────┴────────────┘
```

Program home, Knowledge, and Compute/Work Queue are cross-cutting anchors.
Stable chrome can collapse for full-screen 3D, molecule, plate, or comparison
modes; current context and recovery remain reachable. Pixel widths are responsive
design guidance, not semantic invariants.

## 4. One typed context, many exact selections

```mermaid
flowchart LR
    CTX["ScientificContext<br/>tenant · Program · WorkThread · question · object path"]
    CTX --> S1["StructureSelection<br/>model · chain · residue · atom · version"]
    CTX --> S2["MolecularSelection<br/>atoms · bonds · representation version"]
    CTX --> S3["MaterialSelection<br/>sample · quantity · unit · reservation"]
    CTX --> S4["PlateSelection<br/>plate · wells · layout version"]
    CTX --> S5["DatasetSlice<br/>filter AST · projection · digest"]
    CTX --> S6["DerivedSet<br/>definition · member digest · source versions"]
```

Selections may be transient, named, comparison, or bulk. They can be cleared,
pinned, saved, shared, invalidated, or represented by a safe tombstone.

## 5. Work is a graph, not one item marching through eight pages

```mermaid
flowchart LR
    Q["ScientificQuestion"] --> A["Structure interpretation"]
    A -->|"informs"| B["Design hypothesis"]
    B -->|"fans out"| C["Make candidate"]
    B -->|"fans out"| D["Run calculation"]
    C -->|"provides sample"| E["Test assay"]
    D -->|"shares dependency"| E
    E -->|"returns to"| Q
    F["External CRO"] -->|"partial delivery"| C
    G["Superseding evidence"] -->|"supersedes"| A
```

The same graph projects as next actions, dependency view, board, timeline/Gantt,
queue, or decision history. Critical path communicates uncertainty.

## 6. Handoff is a protocol

```mermaid
stateDiagram-v2
    [*] --> Draft
    Draft --> Offered
    Offered --> Accepted
    Offered --> Rejected
    Offered --> Cancelled
    Accepted --> Delivered
    Accepted --> Returned
    Delivered --> Verified
    Delivered --> Returned
    Draft --> Superseded
    Offered --> Superseded
    Accepted --> Superseded
```

A handoff carries frozen and/or live inputs, permission envelope, acceptance
contract, structured rationale, accountable role, SLA/escalation, partial
delivery, and per-part verification. “Ready” is derived from the acceptance
contract.

## 7. State is deliberately orthogonal

| Axis | Example question |
|---|---|
| Draft | Is my change local, syncing, durable, or superseded? |
| Connectivity | Can the client reach authority? |
| Availability | Is required truth available, partial, unauthorized, or unknown? |
| Submission | Has the request been previewed and accepted? |
| Execution | Is work queued, running, waiting, failed, or complete? |
| Freshness | Are sources current, stale, superseded, or retracted? |
| Review | Is review required, accepted, changed, rejected, or waived? |

Cards may summarize these axes but cannot collapse them into a misleading green
“Ready” badge.

## 8. The first authoritative vertical slice

```mermaid
sequenceDiagram
    participant S as Structures
    participant A as Application authority
    participant D as Design
    participant C as Campaigns
    S->>A: structures.site.save@1 (exact versioned selection)
    A-->>S: receipt + named site
    S->>A: handoff.offer@1 (frozen inputs)
    D->>A: handoff.accept@1
    A-->>D: authorized accepted context
    D->>A: design.proposal.promote@2 preview
    A-->>D: duplicate/new identity outcome + signed token
    D->>A: commit token
    A-->>D: canonical compound receipt
    C->>A: campaigns.candidate.add@1
    A-->>C: same compound identity + objective version
```

This slice is not released until stale previews, permission revocation,
duplicates, deliberate retries, duplicate transport, partial effects,
accessibility, large fixtures, and rollback all have evidence.
