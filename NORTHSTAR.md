# Dirac — North Star

**Dirac is a headless-capable scientific application platform whose GUI, CLI, SDK,
MCP and agents are projections over the same scientific objects, commands, evidence
and durable computation.**

The scientist sees eight stable workspaces: Programs, Design, Structures, Campaigns,
Synthesis, Experiments, Knowledge and Runs. The system underneath remains simple:
canonical identity, one scientific context, semantic commands, versioned methods,
durable Jobs, content-addressed artifacts, typed provenance and controlled relations.

## We pursue

- scientist-facing workflows that connect intent, computation, evidence and decisions;
- mol* as one persistent 3D scene and RDKit as the chemistry substrate;
- browser-native interaction with honest backend escalation for real scientific compute;
- one command surface shared by human and agent actors;
- exact method identity, reproducible artifacts and explicit uncertainty;
- additive Workspaces and modules over stable application contracts.

## Invariants

- No adapter or Workspace invents private scientific semantics.
- No result is current without its Method identity and provenance.
- Long compute creates a durable Job.
- A scientific object has one canonical `ObjectRef`.
- ScientificContextStore owns focus, selection and stale-result rejection.
- Mission, Run and Job remain distinct.
- Agents use the same commands and objects as humans; autonomy changes policy, not API.
- Unimplemented or unsupported capability is gated or refused, never simulated as real.

## Scope discipline

Do not build eight disconnected products or horizontally fill empty screens. Extend the
platform in dependency order: domain object → method if computation is needed → semantic
command → durable state and provenance → SDK/UI projection → safe agent projection.

The detailed product architecture is
[`docs/product/PRODUCT_ARCHITECTURE.md`](docs/product/PRODUCT_ARCHITECTURE.md).
