# ADR-002 · JSON Schema 2020-12 is the single contract authority

    status: ACCEPTED · 2026-08-11
    enforcement: scripts/check_contract_drift.mjs (gate 7, ships its own red proof)

## Decision

`contracts/schema/*.schema.json`, `contracts/methods/*.method.json` and
`contracts/errors.json` are the ROOT source. Python models, TypeScript types, MCP
tool schemas, HTTP/OpenAPI documents and the DB method registry are all GENERATED
or VALIDATED from them. Not OpenAPI as root, not Pydantic as root, not a
TypeScript interface as root.

## Why

Three times today an undeclared response key was live in production and found by a
gate within the hour. Each fix was two minutes; each was also a hand-edit to a
hand-written declaration in two languages. `contracts/iface.pyi` still marks
`register_method` and `job_create` as `PLANNED seam` while both landed — verified,
3 occurrences — which is the proof that hand-synchronised interface files describe
the system their author last remembered, not the system that runs.

MCP consumes JSON Schema natively; Python and TypeScript can both be generated from
it; the method registry already stores `in_schema`/`out_schema` as JSON Schema. So
the format is not a preference, it is the intersection of every consumer.

## Consequences

- A schema change without regenerating must turn CI red. That is the acceptance
  criterion for this ADR, and it belongs in gate 7 (`regenerate` + `git diff
  --exit-code`).
- `Basis` stops being a global closed union. It is a METHOD-SPECIFIC capability:
  the physics daemon already accepts `def2-tzvp` while the shared contract forbids
  it, so the global type is already false. Domain base type becomes `BasisId =
  string`; each method's schema declares its own enum.
- Input schemas use `"additionalProperties": false` so a misspelled field fails at
  the boundary instead of being silently ignored.
- Output: the server validates strictly; clients tolerate ADDED fields; removing or
  changing one is a major schema bump.

## How this ADR fails

If a fourth hand-written type declaration appears — a third `.pyi`, a second
`.d.ts`, a Python dict of field names — this decision was not implemented.
