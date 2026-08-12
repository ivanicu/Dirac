# Remote security boundary

Dirac remains in `local` security mode unless `DIRAC_SECURITY_MODE=remote` is
explicitly set. Remote mode is a different trust boundary, not a LAN toggle. It
refuses to start without a valid hashed-token file and refuses to bind a public
interface. TLS terminates at the reverse proxy; the Python service listens only on
loopback and requires `X-Forwarded-Proto: https`.

## Provision a credential

Run `bin/dirac-token-hash` in a TTY. Put only the printed SHA-256 digest in a
root/operator-readable copy of `deploy/dirac-tokens.example.json`; the raw token
goes to the human or agent that owns it and must not enter this repository,
systemd, logs, URLs or audit rows.

Each credential binds one stable actor identity to explicit scopes and four
ceilings: rolling requests per minute, UTC-day requests, UTC-day compute-cost
units, and request bytes. A request-body `actor` cannot override that identity.
Method invocation and artifact reading are separately scoped.
Command adapters are separately scoped too: for example,
`structure.field.compute` requires both
`command:structure.field.compute:execute` and the resolved
`method:fields.qm.homo:invoke` (or a matching wildcard). This prevents a command
adapter from becoming a privilege bypass around the method boundary.

## Put HTTPS in front

Use `deploy/Caddyfile.remote.example` after replacing its hostname. Apply
`deploy/systemd/dirac-fields-remote.conf.example` as a user-unit drop-in, point
`DIRAC_TOKEN_FILE` at the protected credential file, reload systemd, and restart
the service. Do not enable remote mode before the reverse proxy is serving HTTPS.

The Python SDK reads the raw client credential from `DIRAC_TOKEN`; TypeScript
accepts an explicit memory-only `token` constructor option. Neither client writes
the token to disk. Query parameters are never an authentication mechanism.

## Enforcement and evidence

- Missing/invalid credentials, missing scope, rate, quota and TLS refusals use the
  canonical error vocabulary.
- Expensive work is charged before execution. PostgreSQL updates the daily usage
  row atomically, so concurrent calls cannot overspend by racing.
- `audit.remote_request` records actor, token fingerprint, route, scopes, status,
  byte counts, cost and duration. It never stores request bodies, Authorization,
  cookies, raw tokens or query values.
- If durable quota accounting is unavailable, remote work fails closed with
  `DB_UNAVAILABLE`; it does not run unmetered.
- Content-addressed artifact bytes require their own `artifact:read` scope.

This profile is implemented and testable without making this workstation a public
deployment. Choosing a public hostname, exposing a firewall port and issuing real
credentials remain operator actions because they change the external trust boundary.
