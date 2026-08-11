#!/usr/bin/env python3
"""backend/admin_routes.py — HTTP routing over the read-only operations layer.

The data layer (backend/admin_queries.py) is already built and already
tested; this file is only the wiring that lets `backend/field_server.py`'s
Handler reach it, in ONE dispatch call:

    res = handle_admin(self.path)
    if res:
        self._send(*res)
        return

`handle_admin()` returns `None` for anything that is not an admin path (so
the caller's normal routing falls through untouched), and otherwise a
`(http_status, json_body)` tuple ready for `Handler._send`. Nothing else is
required of the caller — no Host/Origin/Content-Type handling here, because
`do_GET` already does all of that before it would ever reach this call.

READ-ONLY, structurally. Every route below is a single call into one of
admin_queries.py's SELECT-only functions; there is no code path here that
can reach an INSERT/UPDATE/DELETE. Deletion stays in bin/dirac-sweep, where
shell access on this box is the actual auth boundary — an unauthenticated
LAN GET must never be able to erase hours of SCF (see admin_queries.py's own
docstring on the 36-minute runaway this whole surface exists to make
visible). If a mutation ever looks tempting to add here, it belongs in
dirac-sweep instead, not in this file.

The one property every route shares, and the reason this module exists at
all: the database can be down, and "down" must not look like "empty". A
route that cannot reach Postgres returns 503 with an `error` envelope; a
route that reaches Postgres and finds nothing returns 200 with an empty
`data.rows`. The two are different top-level shapes (`ok` differs, and
`error` vs `data` differ), never distinguishable only by content.
"""
from __future__ import annotations

import sys
import traceback
from typing import Any, Callable

import admin_queries
import envelope

# path -> zero-arg callable returning what admin_queries would print for the
# matching CLI section (see admin_queries.py's own _SECTIONS/printers table,
# which this mirrors one-for-one plus /admin/snapshot for "everything, one
# dict, one transaction").
_ROUTES: dict[str, Callable[[], Any]] = {
    '/admin/queue': admin_queries.queue,
    '/admin/cache': admin_queries.cache_summary,
    '/admin/stale': admin_queries.stale,
    '/admin/producers': admin_queries.producers,
    '/admin/methods': admin_queries.methods,
    '/admin/blobs': admin_queries.blob_health,
    '/admin/toolkits': admin_queries.toolkits,
    '/admin/snapshot': admin_queries.snapshot,
}


def _wrap_data(value: Any) -> dict:
    """envelope.ok(data, meta) does `dict(data)` on its first argument, which
    is fine for the admin_queries functions that already return a dict
    (cache_summary/blob_health/snapshot) and fails outright for the ones
    that return a list (queue/stale/producers/methods/toolkits — a list
    is not dict()-able). This is the one place that difference is folded
    away, so every admin route hands back the same envelope SHAPE
    (`data` is always a dict) regardless of which kind the underlying
    query returns; a list-shaped result rides under `data['rows']`.
    """
    if isinstance(value, dict):
        return value
    return {'rows': value}


def _error(code: str, message: str, *, http: int, **extra: Any) -> tuple[int, dict]:
    """A hand-built v2-shaped error envelope: `{ok, error, meta}` with the
    same fields envelope.err() produces (code/message, envelope/request_id).

    Deliberately NOT built by calling envelope.err(): every code in
    contracts/errors.json carries http in {200, 403, 413} and none of them
    is 404 or 503 — err() looks the code up in that table and raises
    ValueError for anything not registered there, and registering a new
    code means editing contracts/errors.json, which is out of scope (four
    other sessions are working in contracts/** right now, and neither of
    these failures is a chemistry error the wire vocabulary was built to
    describe — they are routing/ops failures one level below it). This
    keeps the same envelope shape without touching that contract.
    """
    error: dict[str, Any] = {'code': code, 'message': message}
    error.update(extra)
    return http, {
        'ok': False,
        'error': error,
        'meta': {'envelope': 2, 'request_id': envelope.new_request_id()},
    }


def _db_unavailable() -> tuple[int, dict]:
    """503 — the database could not be reached (or the query it was asked to
    run failed for any other reason). Never carries the exception's raw
    text: a psycopg OperationalError against a bad DSN puts the connection
    string and the connecting OS user in its message, and a caller here has
    no way to tell a safe exception from an unsafe one — so NONE of that
    text leaves this function. The detail is logged to stderr; the response
    only ever says the shape: unreachable, not why in the attacker's words.
    """
    return _error('DB_UNAVAILABLE',
                  'the dirac database is not reachable right now',
                  http=503)


def handle_admin(path: str) -> tuple[int, dict] | None:
    """The one dispatch entry point. `None` means "not an admin path, keep
    routing as before"; otherwise `(http_status, json_body)`.

    A query string on `path` (e.g. `/admin/queue?x=1`) is tolerated —
    stripped before matching — since nothing here is written to depend on
    the absence of one and a caller adding `?cache=no` later should not
    have to know this router would otherwise 404 it.
    """
    route_path = path.split('?', 1)[0]
    if not route_path.startswith('/admin/'):
        return None

    fn = _ROUTES.get(route_path)
    if fn is None:
        return _error('NOT_FOUND', f'{route_path!r} is not an admin route',
                       http=404, valid_routes=sorted(_ROUTES))

    try:
        result = fn()
    except Exception as e:                                   # noqa: BLE001
        # Logged here, in full, for whoever is watching the daemon's stdout —
        # and ONLY here. Never folded into the response (see _db_unavailable).
        print(f'[{route_path}] query failed: {type(e).__name__}: {e}',
              file=sys.stderr, flush=True)
        traceback.print_exc()
        return _db_unavailable()

    return 200, envelope.ok(_wrap_data(result), {})


if __name__ == '__main__':
    # A human-readable smoke test with no test framework and no HTTP server:
    # `backend/env/bin/python backend/admin_routes.py` walks every route and
    # prints (status, top-level keys) for each, plus the None/404 checks.
    import json as _json

    for _path in sorted(_ROUTES) + ['/admin/nope', '/field', '/health']:
        _res = handle_admin(_path)
        if _res is None:
            print(f'{_path:<20} -> None (not an admin path)')
            continue
        _code, _body = _res
        print(f'{_path:<20} -> {_code} ok={_body.get("ok")} '
              f'keys={sorted(_body.keys())}')
    print()
    print(_json.dumps(handle_admin('/admin/snapshot')[1], indent=2, default=str)[:2000])
