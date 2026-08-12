# Command Model

`contracts/commands/registry.json` is the authority. Every Command has a stable ID and
version, input/output JSON Schema, category, mutability, execution class, executor set,
idempotency, Job and provenance policies, ObjectKinds, typed errors and one application
handler. Generated Python and TypeScript identities come from `scripts/gen_commands.py`.

HTTP `/v2/execute`, Python `DiracClient.execute`, the CLI, TypeScript `DiracClient.execute`
and MCP all delegate to the same dispatcher. Required-long commands create durable Jobs;
the dispatcher contains no scientific implementation and transports contain no policy.
