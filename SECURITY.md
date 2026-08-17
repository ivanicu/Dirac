# Security policy

## Supported code

Dirac is under active development. Security fixes target the current `main` branch; older
commits and local deployment snapshots are not maintained as separate supported releases.

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** flow for this repository to open a private security
advisory. Include:

- the affected endpoint, command, artifact or frontend;
- the deployment profile and authentication state;
- reproduction steps and the expected security boundary;
- impact and any known workaround;
- logs or payloads with credentials and private scientific data removed.

Do not open a public issue containing exploit details, tokens, private molecule data or
artifact contents.

## Deployment boundary

The default local/LAN profile assumes a trusted workstation or lab network and does not
enable authentication. It must not be exposed directly as a public multi-user service.

Remote mode is designed to fail closed on missing TLS, bearer identity, scope, request
limits, durable quota or artifact authorization. Operational details and configuration
requirements live in [`docs/security/REMOTE.md`](docs/security/REMOTE.md).

## Scientific integrity

Reports that could cause one actor to read, mutate, cancel or reuse another actor's Jobs,
campaigns, artifacts or results are security issues even when the numerical computation is
correct. Reports that can silently relabel refused, stale or unverified evidence as a valid
scientific result should also use the private vulnerability channel.
