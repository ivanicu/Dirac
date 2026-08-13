# Dirac runtime and deployment

The canonical local deployment has one current topology:

| Unit | Port | Purpose |
|---|---:|---|
| `dirac-web.service` | 1360 | static `build/dirac` bundle; the only Dirac web server |
| `dirac-fields.service` | 8901 | unified application, Command, Method, Job and artifact service |
| `dirac-ops.service` | 1355 | read-only operations projection |
| `dirac-digital-twin.service` | — | source watcher and architecture-twin regeneration |

The standalone `dirac-physics.service` on 8902 is superseded and retained only under
`deploy/systemd/_archive/`. Physics implementations are reached through the unified 8901
service.

## Day-to-day workflow

```bash
npm run build:dirac
systemctl --user restart dirac-fields
systemctl --user status dirac-web dirac-fields dirac-ops dirac-digital-twin
```

The web unit serves files directly from `build/dirac`, with HTTP caching disabled. A
frontend rebuild does not require a second server or a different port: reload 1360.
Backend processes keep Python code in memory, so restart `dirac-fields` after backend
changes before taking live evidence.

`bin/dev` is a safe convenience wrapper around this supervised topology; it no longer
starts unmanaged servers or the retired 8902 daemon.

## Install or refresh the user units

The unit files contain canonical-workstation paths under `/home/ivan/dirac`. Review and
adapt those paths before using them elsewhere.

```bash
systemctl --user link "$PWD/deploy/systemd/dirac-web.service"
systemctl --user link "$PWD/deploy/systemd/dirac-fields.service"
systemctl --user link "$PWD/deploy/systemd/dirac-ops.service"
systemctl --user link "$PWD/deploy/systemd/dirac-digital-twin.service"
systemctl --user daemon-reload
systemctl --user enable --now dirac-web dirac-fields dirac-ops dirac-digital-twin
```

Verify state, socket ownership and HTTP responses rather than treating installation as
proof:

```bash
systemctl --user is-active dirac-web dirac-fields dirac-ops dirac-digital-twin
curl --fail http://127.0.0.1:1360/
curl --fail http://127.0.0.1:8901/health
curl --fail http://127.0.0.1:1355/
```

## Local security boundary

The local units bind the web and application service to all interfaces for LAN use. The
default profile is unauthenticated. Network reachability is therefore the security
boundary; this topology is not suitable for public exposure.

For remote/WAN operation, follow [`../docs/security/REMOTE.md`](../docs/security/REMOTE.md)
and [`Caddyfile.remote.example`](Caddyfile.remote.example). The remote profile must be
explicitly enabled behind HTTPS with operator-issued tokens, scopes, quotas, request caps,
artifact authorization and redacted audit. Do not copy example tokens into a live system.

## Kubernetes/Kueue execution

`kubernetes/` contains the worker, queue and persistent-exchange manifests used by the
remote executor boundary. The controller remains the durable authority for admission,
attempt identity, leases and completion; Kubernetes is an execution substrate, not a
second Job ledger.

Deployment changes can consume cluster resources and must be applied deliberately. Review
the manifests and current cluster state before applying them.

## Operational tools

- `bin/dirac-sweep` is the explicit admin deletion boundary for stale cached results. Its
  default is dry-run; `--apply` is intentionally separate.
- `ops/` is served independently so application build failure does not remove the
  diagnostic surface.
- `scripts/build_digital_twin.py` and `scripts/check_digital_twin.py` rebuild and validate
  the architecture projection.

Historical merged/standalone units live under `deploy/systemd/_archive/` and are not
installation candidates.
