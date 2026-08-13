# Dirac Human Interface Specification

**Status:** superseded compatibility entry point
**Current baseline:** HCI v2.1 semantic-contract suite
**Date:** 2026-08-13

The former monolithic V2 document mixed product intent, visual guidance, domain
semantics, implementation claims, and acceptance evidence. It is retained at
this path so existing links do not break, but it is no longer normative.

Use the v2.1 suite instead:

1. [`HUMAN_INTERFACE_CHARTER.md`](HUMAN_INTERFACE_CHARTER.md) — purpose,
   invariants, human outcomes, and vocabulary.
2. [`HCI_SEMANTIC_CONTRACTS.md`](HCI_SEMANTIC_CONTRACTS.md) — context,
   selection, state, action, handoff, work graph, collaboration, and AI
   delegation protocols.
3. [`hci/workspaces/`](hci/workspaces/) — one projection brief for each of the
   eight workspaces.
4. [`HCI_QUALITY_CONTRACT.md`](HCI_QUALITY_CONTRACT.md) — accessibility,
   performance, privacy, security, and telemetry.
5. [`HCI_ACCEPTANCE_CONTRACT.md`](HCI_ACCEPTANCE_CONTRACT.md) — executable
   evidence and release gates.
6. [`HCI_MIGRATION_PLAN.md`](HCI_MIGRATION_PLAN.md) — vertical-slice delivery
   and rollback order.
7. [`HCI_COMMAND_CAPABILITY_AUDIT.md`](HCI_COMMAND_CAPABILITY_AUDIT.md) — what
   existing code is reusable, adapter-bound, unknown, or replaced.
8. [`HCI_GLOSSARY.md`](HCI_GLOSSARY.md) — normalized terms, change history,
   assumptions, risks, and open questions.
9. [`HUMAN_INTERFACE_VISUAL_MAP.md`](HUMAN_INTERFACE_VISUAL_MAP.md) — a
   non-normative map of the architecture.

The generated machine contract is
[`hci/human-interface-v2.contract.json`](hci/human-interface-v2.contract.json).
It is generated from
[`hci/human-interface-v2.source.mjs`](hci/human-interface-v2.source.mjs); edits
to the generated JSON are rejected by the verification script.

No document in this suite claims that all eight workspaces are implemented.
The current status is **semantic contract plus verified reference slice**.
A workspace becomes implementation-complete only when its projection brief,
authoritative integration, failure-complete journey, accessibility evidence,
and rollback witness all pass.
