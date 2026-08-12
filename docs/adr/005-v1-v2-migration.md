# ADR-005 · v2 is the authority; v1 becomes a codec over it

    status: ACCEPTED · 2026-08-11
    enforcement: PENDING — gate asserting no live route builds a response dict by hand

## Decision

Every route computes a v2 envelope through the kernel, and v1 routes return
`envelope.to_v1(v2_response)`. Old routes keep working and stop executing compute
themselves. No flag day.

## Why

The envelope v2 implementation exists, has tests, and is NOT the live authority:
`field_server.py` imports exactly one symbol from it (`normalize_meta`, verified)
and live routes still assemble `{'ok': True, 'cube': …, 'meta': …}` by hand, with
several chemistry failures returning HTTP 200. So the honest status is:

```
Envelope v2 implementation : present
Envelope v2 tests          : present
Envelope v2 live authority : absent
```

A CLI shipped before that is closed would expose today's inconsistent JSON under a
new name — "machine-readable" would mean "the same shapes, now with a client".

## Consequences

- Scientific refusals raise `DiracFailure(code=…, details=…, hint=…)` rather than
  `ValueError`, so an HTTP handler stops GUESSING whether a failure is UNSUPPORTED
  or INTERNAL. That guess exists in the code today.
- `stored: true` is replaced. Its real meaning is "a background persistence thread
  was started", which for a machine caller is a lie of the most expensive kind; v2
  carries `not_requested | scheduled | stored | failed | ephemeral`.
- Warnings become structured and separate from errors, with a code and a SCOPE.
  Dirac's caveats are already good — frontier energies not quotable at a minimal
  basis, σ-hole not representable by point charges, contour not closed, waters
  excluded, simplified dielectric — and an agent must be able to distinguish
  "refused", "returned but scope-limited" and "fully supported".
- v1 golden responses are captured BEFORE the change and must not move.

## How this ADR fails

A v1 golden response changing, or a v2 chemistry failure still arriving with HTTP
200 outside the explicitly compatible v1 routes.
