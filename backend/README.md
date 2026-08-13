# Dirac application and scientific service

`backend/field_server.py` is the unified local control plane for semantic Commands,
scientific Methods, durable Jobs, artifacts and compatibility routes. The historical
name remains, but it is no longer only a field-cube daemon.

## Runtime

The canonical workstation runs the service as `dirac-fields.service`:

```bash
systemctl --user status dirac-fields
systemctl --user restart dirac-fields
journalctl --user -u dirac-fields -n 100 --no-pager
```

For a clean development environment where the supervised unit is not installed:

```bash
backend/env/bin/python backend/field_server.py
```

The default bind is `0.0.0.0:8901`. It is LAN-reachable and the default local profile is
unauthenticated. Do not expose it to the public internet. See
[`../docs/security/REMOTE.md`](../docs/security/REMOTE.md) for the remote profile.

`backend/env/` is a repository-local, gitignored scientific environment. The checked
Motif runtime lock is `motif/requirements.lock.txt`, but full environment provisioning is
currently workstation-oriented rather than a supported one-command installation. A
missing optional toolkit must produce a typed unavailable/refusal result, not a fabricated
scientific output.

## Boundaries

| Path | Responsibility |
|---|---|
| `dirac_app/` | command loading, validation, dispatch, application handlers and repositories |
| `invocation.py` | one Method validation, cache, Job, executor, artifact and provenance path |
| `method_registry.py` | executable Method registration against canonical manifests |
| `jobs.py` | durable and in-memory JobStore implementations |
| `artifacts.py` | local and PostgreSQL content-addressed artifact stores |
| `programs/` | durable Program aggregate and repository logic |
| `motif/` | governed data, model, proposal, structure, physics and closed-loop workflows |
| `execution_control/` | allocation, attempt identity, leases, retries, reconciliation and completion |
| `executors/` | local process/GPU and Kubernetes/Kueue adapters |
| `db/` | PostgreSQL schema, migrations, checks and operational views |
| `physics/` | reusable surface/torsion implementations; its standalone `:8902` server is legacy |
| `tests/` | portable, scientific-stack, database and live transport tests |

Adapters do not own scientific business logic. HTTP parses/serializes, the CLI renders,
and MCP projects safe Commands; all delegate to the same dispatcher and invocation
kernel.

## HTTP surface

The current service exposes:

- `GET /health` for runtime and store health;
- `GET /v2/meta`, `/v2/commands`, `/v2/methods` and their detail routes;
- `POST /v2/execute` for semantic Commands;
- `POST /v2/invoke` for generic Method invocation;
- `/v2/jobs/*` for durable Job discovery, wait and cancellation;
- `/v2/artifacts/*` for authorized artifact retrieval and metadata;
- read-only `/admin/*` projections used by the operations console;
- compatibility routes retained behind the v2 kernel.

Long-running Commands declared `job_policy=required` must return a durable Job. Large
scientific results are Artifacts, not inline response payloads.

## PostgreSQL

PostgreSQL is the durable authority for application and execution state. Apply migrations
in order and stop on the first error:

```bash
for file in backend/db/migrations/*.sql; do
  psql "$DIRAC_DSN" -X -v ON_ERROR_STOP=1 -f "$file"
done
```

Never edit an applied migration. `backend/db/check_migration_hashes.sh` compares the files
with the migration ledger, and CI also migrates a clean PostgreSQL 18 + pgvector database.

## CLI smoke checks

```bash
PYTHONPATH=python/src python3 -m dirac.cli commands --json
PYTHONPATH=python/src python3 -m dirac.cli health --json
```

After changing any backend file on the canonical workstation, restart
`dirac-fields.service` before live verification; the daemon does not reload itself.

## Scientific and operational honesty

- Unconverged or unsupported calculations return typed failures; they are never cached or
  rendered as valid fields.
- Method versions and runtime locks are provenance, not decorative labels.
- Cache identity follows Method currency and exact inputs.
- A Job/transport parity test proves routing and persistence, not scientific validity.
- GPU work is submitted through the configured execution boundary; repository users must
  not launch competing unmanaged CUDA work.
