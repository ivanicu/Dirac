# ADR-004 · CLI and MCP are adapters over the same kernel

    status: ACCEPTED · 2026-08-11
    enforcement: scripts/check_layering.py (gate 11), laws currently N/A because the
                 subjects do not exist — reported as N/A, never as PASS

## Decision

```
CLI → SDK → InvocationService
MCP → SDK → InvocationService
```

NOT `MCP → spawn dirac CLI → parse stdout`. A CLI is an input parser, an SDK caller
and an output renderer. Nothing else.

## Why

Wrapping the CLI from MCP is the fastest demo and it breaks, permanently:
cancellation cannot cross a process boundary that only returns stdout; progress
becomes log scraping; binary artifacts become base64 in a pipe; job handles cannot
be reconnected; actor identity is lost; a long-lived process cannot be reused; and
typed errors degrade to exit codes plus text.

A throwaway spike may do it. The moment it ships, all seven of those are broken in
a way that is expensive to undo, because callers will have started depending on the
stdout shape.

## Consequences

- `dirac invoke <method_id> --input request.json --json` is the load-bearing
  command; ergonomic commands (`dirac field compute …`) only construct that same
  generic request, and `--show-request` must print it. Syntax sugar, not a second
  API.
- The CLI's `--json` contract: stdout carries exactly one JSON document, failures
  included; every log, progress line and diagnostic goes to stderr. `--jsonl`
  carries one event per line. Without progress instrumentation the CLI emits state
  transitions only — it does not fabricate a percentage.
- Exit codes are coarse and stable (0 ok · 2 local usage · 10 domain `ok:false` ·
  20 transport · 30 server · 70 artifact verification · 130 interrupted). The JSON
  is the detailed authority; twelve error codes do not become twelve exit codes.
- `dirac admin sweep` and the developer supervisor (`bin/dev`) are NOT the product
  CLI and are never exposed to MCP. The scientific surface and the operator surface
  stay physically separate.

## How this ADR fails

`from backend.field_server import field_quantum` appearing in any CLI, SDK or MCP
module. That import is the violation this ADR exists to prevent.
