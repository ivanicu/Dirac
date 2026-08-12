# ADR-001 · One transport-neutral invocation kernel

    status: ACCEPTED · 2026-08-11
    enforcement: scripts/check_layering.py (gate 11) — 4 laws enforced, 2 ratchets,
                 3 reported N/A because their subject does not exist yet
    supersedes: nothing · superseded by: nothing

## Decision

Every scientific invocation — from the web UI, a CLI, an SDK, an MCP tool, or a
worker — goes through ONE `InvocationService`. No adapter holds scientific
business logic.

Concretely, and these are the sentences that decide arguments later:

- The CLI does not decide whether a basis covers an element.
- MCP does not decide whether a method runs sync or as a job.
- An HTTP handler does not decide what a non-finite budget means.
- The web UI does not define its own `FieldMeta`.
- An SDK does not re-interpret an error code.
- A job worker does not invent a response shape.

## Why now, and why this is not a rewrite

An external audit of this repository concluded: *the conceptual architecture
already supports agents and CLIs natively; the runtime architecture does not.
Dirac is agent-COMPATIBLE, not agent-NATIVE.* That reading matches the code. The
expensive ideas already exist and are already load-bearing:

- method identity with a source-and-constants digest, and cache reads keyed on
  method currency;
- a job ledger with a CHECK-enforced state machine, in-flight dedup and reaping;
- one error vocabulary with caller actions;
- a content-addressed blob store whose constraint refuses a mislabelled blob.

What is missing is a single executable boundary. `field_server.py` is today the
composition root, the application service, the repository, the scheduler AND the
transport adapter, dispatching by `if kind == 'mep' … elif …`. Measured, not
asserted: **6 direct calls to scientific functions from inside the HTTP Handler**
(gate 11), and **3 of 10 test suites can be imported without RDKit** (gate 10) —
because everything reaches its subject through that one module.

So the work is a seam consolidation, not a rewrite. Nothing above needs to be
replaced; it needs one place to be called from.

## Alternatives rejected

**Write `bin/dirac` as a shell script that curls the existing routes.** Fastest
path to a demo, and it leaves every typed-surface problem unsolved while creating a
second consumer of the inconsistent JSON. The audit's phrasing is right: the CLI
should be the first thing SHOWN, but the kernel must be the first thing BUILT.

**Let MCP spawn the CLI and parse stdout.** Acceptable as a throwaway spike, fatal
as an architecture: cancellation, progress, binary artifacts, job handles, actor
identity and process reuse all break at that boundary. ADR-004 forbids it.

**Give agents their own API.** The ROADMAP already calls this a rewrite failure:
an agent route that bypasses the job ledger, the error vocabulary or the method
registry forks the system in half.

## Consequences

- A new capability (docking, FEP, MD, an ML predictor) becomes a registered method
  plus a handler, and is immediately reachable from every surface without new
  adapter code. That is the test of whether this decision was implemented or only
  documented.
- Two laws are violated today (the 6 Handler calls, 8 facet `fetch(` calls). They
  are RATCHETS in gate 11 rather than aspirations: the numbers may only go down.
- Three laws report **N/A** rather than PASS, because the SDK, CLI and MCP do not
  exist yet. A law that passes for lack of a subject reads, in a green suite,
  exactly like a law being obeyed — the same defect as a zero from an instrument
  that has never returned non-zero.

## How this ADR fails

If, three PRs from now, `check_layering.py` still reports 6 Handler calls while
new surfaces have been added, then this decision was decoration and the ratchet is
the evidence. The number is the acceptance criterion, not this document.
