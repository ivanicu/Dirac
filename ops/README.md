# Dirac operations console

`ops/` is a static, read-only diagnostic surface served independently from the product
bundle. The separation is deliberate: a broken application build must not remove the
page used to inspect backend health, Jobs, cache state, producers and stale artifacts.

## Current endpoint

The canonical workstation serves this directory through `dirac-ops.service`:

```text
http://localhost:1355/
```

It reads the unified application service on 8901 and links to the application on 1360.
It does not probe or depend on the retired standalone 8902 service.

## Data boundary

The console uses only read endpoints such as `/health` and `/admin/snapshot`. It must
distinguish:

- loading;
- service unreachable;
- service alive but durable dependencies degraded;
- healthy with no active work;
- route/capability not present.

Missing data is rendered as unavailable, never as zero. Snapshot age remains visible so a
frozen page cannot resemble a calm system.

## No mutation controls

There is intentionally no delete, retry or cancel button. `bin/dirac-sweep` is the
explicit shell-level deletion boundary for stale cache rows and defaults to dry-run.
Application mutations belong behind authenticated semantic Commands, not an
unauthenticated LAN operations page.

## Files

| File | Purpose |
|---|---|
| `index.html` / `ops.js` | operational snapshot and cache/Job projections |
| `host.html` / `host.js` | service-capability and endpoint view |
| `../design/tokens.css` | shared visual tokens; no product JavaScript dependency |

The current systemd and security guidance lives in [`../deploy/README.md`](../deploy/README.md)
and [`../docs/security/REMOTE.md`](../docs/security/REMOTE.md).
