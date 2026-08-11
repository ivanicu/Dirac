# Dirac — Operations console

`ops/index.html` + `ops/ops.js`. A standalone, static, read-only page: no
build step, no framework, no CDN, zero coupling to `src/`'s facets or their
build. It reads `design/tokens.css` for every colour/space/type/radius value
it uses, so it stays visually native to the rest of Dirac without importing
any of its code.

## Why this exists

Today, "what is the backend doing / what is it holding / which producer
generation made this row" has no answer except reading a log by hand: a
1 GB RSS went unnoticed for hours, and a 36-minute runaway SCF was found
only because a human happened to be watching `top`. `backend/admin_queries.py`
already answers all of that with plain SQL against the live schema
(`app.v_job_live`, `app.v_field_cube_stale`, `meta.producer`, `meta.method`,
…). This page is that answer, rendered.

## How to open it

Any static file server, from the repo root:

```
node_modules/.bin/http-server . -p 1341 -c-1
```

then open `http://localhost:1341/ops/`. The backend it talks to is
`backend/env/bin/python backend/field_server.py` (port 8901) — start that
first if the console reports itself offline; it says so on the page, with
the exact command.

The page derives the backend host from `window.location.hostname` (never a
hardcoded `127.0.0.1`) so the same file works unmodified whether it is
opened on the box itself or from another machine on the LAN pointed at the
box's hostname or IP.

## Why there is no delete button

`bin/dirac-sweep` is the only thing on this system authorised to delete a
`field_cube` row or a `blob`. It runs by hand, on the box, and its own
header comment explains the reasoning: an unauthenticated LAN endpoint that
can delete hours of SCF compute is not an acceptable shape for that
capability, so shell access on the box **is** the auth boundary, deliberately,
rather than a login this page would otherwise need to grow. The Stale sweep
panel exists to make the delete-vs-recompute number visible — rows,
reclaimable bytes, and the compute-seconds they represent — never to act on
it. Every panel here is read-only; there is no `fetch()` in `ops.js` that is
not a `GET`.

## The four honest states

A monitoring page that goes blank or looks identical for two different
failures is worse than no page — an operator can't tell "it's calm" from
"it's dead". This console distinguishes:

1. **Loading** — the very first request hasn't resolved yet. Blue, pulsing.
2. **Backend unreachable** — no response at all: connection refused, DNS
   failure, or a 4-second timeout with nothing back. Red. The banner prints
   the exact start command.
3. **Degraded** — the backend process answered but the database didn't (an
   HTTP 503 from the route). Amber, deliberately *not* the same red as
   "unreachable" — the backend is alive; only the DB path is down. Conflating
   these two is the exact defect this console exists to avoid.
4. **Empty but healthy** — the backend and DB both answered and there are
   zero jobs in the queue. Green, worded as good news ("0 jobs running"),
   not rendered as an empty-looking error state.

A fifth, non-canonical state handles the real situation this page was built
against: `/admin/snapshot` may simply not exist yet on the backend (404),
because another agent is writing that route concurrently. That reads as a
neutral, muted "not wired up yet" pill — distinct from all four states
above, because it isn't an outage, it's an unfinished dependency.

Whichever state holds, the page also shows **the age of the last successful
snapshot** ("updated 3 s ago"), ticking every second independently of the
auto-refresh timer and independently of the Pause button — so a frozen page
can never masquerade as a calm system, and pausing on purpose looks
different from the backend having quietly stopped answering.

## Findings about `backend/admin_queries.py` (reported, not fixed — that file
is owned by another agent's concurrent session)

- `cache_summary()` does not expose `producer_generations`,
  `max_generations_per_unit`, or `rows_servable` at all, and names the
  "on current producer" count `rows_on_current_producer` rather than
  `rows_producer_current`. Those exact five names (`rows_total` /
  `rows_servable` / `rows_producer_current` / `producer_generations` /
  `max_generations_per_unit`) are the columns of the live view
  `app.v_cache_health` (`backend/db/migrations/010_cache_health_metric.sql`),
  which `cache_summary()` does not read — it re-derives an overlapping but
  different set of numbers with its own hand-written JOIN across
  `app.field_cube` + `app.blob`. The two never disagree on a shared number
  (`total_rows`/`rows_total` match live), but the generations pairing the
  console is supposed to show side-by-side simply isn't in `snapshot()`'s
  output today. `ops.js`'s `renderCache()` reads both naming schemes
  defensively and renders the pairing as "not exposed by this backend's
  `/admin/cache` yet" rather than fabricating a `0` — a silent zero here
  would read as "no invalidation churn", which would be a fabricated,
  falsely reassuring number, not an absence of one.
- `snapshot()`'s `cache` key nests `by_kind` but has no rollup for
  `distinct_molecules`/`total_bytes` per producer generation — not needed by
  this console today, noted only because the stale-sweep and cache panels
  are answering closely related questions with disjoint schemas.
- Every other section (`queue`, `stale`, `producers`, `methods`,
  `blob_health`, `toolkits`) matched this console's needs directly with no
  reshaping beyond lifting `refuses` (already done inside `methods()`
  itself) and computing an invalidation ratio client-side.
