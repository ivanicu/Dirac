#!/usr/bin/env python3
"""Tests for backend/admin_routes.py — the HTTP routing layer over the
already-tested read-only data layer (backend/admin_queries.py).

This file tests the ROUTER, not the queries: does every route reach 200 on
a live database, does an unknown /admin/* name its alternatives, does a
non-admin path fall through as `None` (the contract the caller's one-line
dispatch depends on), and — the sharpest requirement in the spec — is a
database that cannot be reached DISTINGUISHABLE from a database that has
nothing to say. An empty `app.v_job_live` and an unreachable `dirac`
database must not render as the same shape; if they ever do, the ops
console cannot tell "quiet" from "broken", which is the exact failure this
whole read-layer exists to prevent (see admin_queries.py's own docstring on
the 36-minute runaway found only because a human happened to be watching
`top`).

Run either way:
    backend/env/bin/python backend/tests/test_admin_routes.py
    backend/env/bin/pytest backend/tests/test_admin_routes.py    # if present

pytest is NOT installed in backend/env — the dual-mode plumbing below is
copied from backend/tests/test_physics_contracts.py, which solves this
exact problem for this exact project.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import traceback

_HERE = pathlib.Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import admin_queries                                      # noqa: E402
import admin_routes                                        # noqa: E402
from admin_routes import handle_admin                       # noqa: E402

# ── dual-mode plumbing (pytest is NOT installed in backend/env) ─────────────

try:
    import pytest
    _HAVE_PYTEST = True
except ImportError:                                        # the normal case here
    pytest = None
    _HAVE_PYTEST = False


class Skipped(Exception):
    """Raised by skip(); the standalone runner reports it as SKIP + reason."""


def skip(reason: str):
    if _HAVE_PYTEST:
        pytest.skip(reason)
    raise Skipped(reason)


# A live, reachable database is assumed by default (admin_queries.py's own
# CLI ran cleanly against it while this file was written). If it genuinely
# is not reachable, every "on the live DB" test skips with that reason
# rather than failing — a down database is a fact about the environment,
# not a defect in this router, and the 503 tests below cover the router's
# behaviour when that happens on purpose.
def _live_db_reachable() -> bool:
    try:
        conn = admin_queries._connect()
    except Exception:
        return False
    conn.close()
    return True


_LIVE_DB = _live_db_reachable()

BAD_DSN = 'dbname=admin_routes_test_nonexistent_db_ivan_will_never_create_this'

# The real OS username on this box (peer auth is what admin_queries.dsn()
# falls through to with no explicit user= — see its own module docstring),
# so this is what would leak if a psycopg exception's raw text ever reached
# a response. `os.environ['USER']` is expected to be set on this box (it is
# — `whoami` == `ivan`); if it is ever unset this becomes an impossible
# sentinel rather than silently checking for an empty string.
_OS_USERNAME = admin_queries.os.environ.get('USER') or '\0no-such-user-env-var-unset\0'


def _body_text(body: dict) -> str:
    """The serialised JSON of a response body — what a client actually
    receives, which is the only thing requirement 5 (never leak a
    connection string or a username) can be checked against."""
    return json.dumps(body)


# ════════════════════════════════════════════════════════════════════════════
# contract: non-admin path returns None
# ════════════════════════════════════════════════════════════════════════════

def test_non_admin_path_returns_none():
    """The caller's whole dispatch is `res = handle_admin(self.path); if
    res: ...`. That only works if a path this router does not own comes
    back as exactly `None`, not an empty tuple, not `(None, None)` — those
    are all truthy or falsy in ways that would silently break the fallthrough.
    """
    for path in ('/field', '/health', '/embed', '/', '/adminfoo', '/administer'):
        result = handle_admin(path)
        assert result is None, (
            f'handle_admin({path!r}) returned {result!r}, not None — the '
            'caller would treat this as an admin response and never reach '
            'its own routing for this path')


def test_admin_prefixed_but_unmatched_path_is_404_not_none():
    """The boundary case right next to the one above: a path that DOES
    start with /admin/ but names no route must be handled HERE (404), not
    handed back as None — the caller has no other route for /admin/* and
    would 404 it anyway, but with a payload that names nothing."""
    result = handle_admin('/admin/does-not-exist')
    assert result is not None
    code, body = result
    assert code == 404


# ════════════════════════════════════════════════════════════════════════════
# every route, on the live DB
# ════════════════════════════════════════════════════════════════════════════

_EXPECTED_ROUTES = (
    '/admin/queue', '/admin/cache', '/admin/stale', '/admin/producers',
    '/admin/methods', '/admin/blobs', '/admin/toolkits', '/admin/snapshot',
)


def test_route_table_matches_the_spec():
    assert set(admin_routes._ROUTES) == set(_EXPECTED_ROUTES), (
        f'route table is {sorted(admin_routes._ROUTES)}, spec wants '
        f'{sorted(_EXPECTED_ROUTES)}')


def test_every_route_returns_200_with_json_serialisable_content_on_live_db():
    if not _LIVE_DB:
        skip('dirac database not reachable in this environment')
    for path in _EXPECTED_ROUTES:
        result = handle_admin(path)
        assert result is not None, f'{path} returned None — should be a route'
        code, body = result
        assert code == 200, f'{path} -> {code}, body={body!r}'
        assert body.get('ok') is True, f'{path}: ok={body.get("ok")!r}'
        assert 'data' in body, f'{path}: no data key in {sorted(body)}'
        # json.dumps is the actual test of "JSON-serialisable" — a stray
        # uuid.UUID/Decimal/datetime would raise TypeError here, exactly the
        # class of bug admin_queries._jsonable() exists to prevent.
        serialised = json.dumps(body)
        assert isinstance(serialised, str) and len(serialised) > 0


def test_snapshot_shape_matches_its_seven_sections():
    if not _LIVE_DB:
        skip('dirac database not reachable in this environment')
    code, body = handle_admin('/admin/snapshot')
    assert code == 200
    data = body['data']
    assert set(data) == {'queue', 'cache', 'stale', 'producers', 'methods',
                          'blob_health', 'toolkits'}, sorted(data)
    assert isinstance(data['queue'], list)
    assert isinstance(data['cache'], dict)
    assert isinstance(data['stale'], list)


def test_list_shaped_routes_carry_their_result_under_data_rows():
    """queue/stale/producers/methods/toolkits all return a LIST from
    admin_queries — this asserts the router's own wrapping contract
    (_wrap_data) rather than re-testing admin_queries' SQL."""
    if not _LIVE_DB:
        skip('dirac database not reachable in this environment')
    for path in ('/admin/queue', '/admin/stale', '/admin/producers',
                 '/admin/methods', '/admin/toolkits'):
        _, body = handle_admin(path)
        assert isinstance(body['data'], dict), f'{path}: data is not a dict'
        assert isinstance(body['data'].get('rows'), list), (
            f'{path}: data.rows is not a list: {body["data"]!r}')


def test_dict_shaped_routes_carry_their_result_directly_under_data():
    if not _LIVE_DB:
        skip('dirac database not reachable in this environment')
    for path in ('/admin/cache', '/admin/blobs'):
        _, body = handle_admin(path)
        assert isinstance(body['data'], dict)
        assert 'rows' not in body['data'], (
            f'{path}: dict-shaped result should not be double-wrapped: '
            f'{sorted(body["data"])}')


# ════════════════════════════════════════════════════════════════════════════
# 404 — unknown /admin/* names the valid routes
# ════════════════════════════════════════════════════════════════════════════

def test_unknown_admin_route_is_404_and_names_valid_routes():
    code, body = handle_admin('/admin/xyz')
    assert code == 404, f'expected 404, got {code}'
    assert body['ok'] is False
    error = body['error']
    assert 'valid_routes' in error, f'404 body does not name alternatives: {error!r}'
    assert set(error['valid_routes']) == set(_EXPECTED_ROUTES), (
        f'valid_routes={sorted(error["valid_routes"])} != spec routes')


# ════════════════════════════════════════════════════════════════════════════
# 503 — the database is down, and it must NOT look like "no rows"
# ════════════════════════════════════════════════════════════════════════════

def test_every_route_returns_503_when_the_database_is_unreachable():
    """The single most important behaviour in the spec: point DIRAC_DSN at
    a database that does not exist and confirm every route degrades to a
    503 that SAYS SO, rather than a traceback or a 200 with empty data.
    """
    saved = admin_queries.os.environ.get('DIRAC_DSN')
    admin_queries.os.environ['DIRAC_DSN'] = BAD_DSN
    try:
        for path in _EXPECTED_ROUTES:
            result = handle_admin(path)
            assert result is not None, f'{path} returned None with DB down'
            code, body = result
            assert code == 503, (
                f'{path} -> {code} with an unreachable DB, expected 503: {body!r}')
            assert body.get('ok') is False, f'{path}: ok={body.get("ok")!r} on DB-down'
            assert 'error' in body, f'{path}: no error key on DB-down: {sorted(body)}'
            assert 'data' not in body, (
                f'{path}: DB-down response still carries a data key — this is '
                'exactly the "empty result masquerading as reachable" shape '
                f'the spec forbids: {body!r}')
    finally:
        if saved is None:
            admin_queries.os.environ.pop('DIRAC_DSN', None)
        else:
            admin_queries.os.environ['DIRAC_DSN'] = saved


def test_db_down_and_empty_result_are_different_shapes_with_a_positive_control():
    """The full discriminating test, in one place: the SAME route
    (/admin/queue, which is empty on this database right now — 0 live
    jobs — so "empty" is not hypothetical) must render DIFFERENTLY
    depending on whether the database can be reached.

    The positive control matters on its own: without it, a test that only
    checks "DB down -> 503" would pass even if handle_admin() always
    returned 503 unconditionally, which would make the router useless
    without proving anything. The 200 branch here is what rules that out.
    """
    if not _LIVE_DB:
        skip('dirac database not reachable in this environment — cannot '
             'run the positive-control half of this test')

    # Positive control: real DB, real route, must be 200/ok/data.
    code_up, body_up = handle_admin('/admin/queue')
    assert code_up == 200, (
        f'positive control failed: /admin/queue -> {code_up} on the live '
        f'DB, so the 503 branch below would prove nothing: {body_up!r}')
    assert body_up['ok'] is True
    assert 'data' in body_up and 'error' not in body_up

    # The failure branch: same route, unreachable DB.
    saved = admin_queries.os.environ.get('DIRAC_DSN')
    admin_queries.os.environ['DIRAC_DSN'] = BAD_DSN
    try:
        code_down, body_down = handle_admin('/admin/queue')
    finally:
        if saved is None:
            admin_queries.os.environ.pop('DIRAC_DSN', None)
        else:
            admin_queries.os.environ['DIRAC_DSN'] = saved

    assert code_down == 503
    assert body_down['ok'] is False
    assert 'data' not in body_down and 'error' in body_down

    # The actual discrimination: two DIFFERENT shapes, not the same shape
    # with different content. `ok` differs, and the key present alongside
    # it (`data` vs `error`) differs — a client can branch on either alone.
    assert body_up['ok'] != body_down['ok']
    assert set(body_up) != set(body_down), (
        f'up={sorted(body_up)} vs down={sorted(body_down)} — identical key '
        'sets would mean a client has to inspect CONTENT to tell "quiet" '
        'from "broken", which is exactly what this route exists to avoid')


# ════════════════════════════════════════════════════════════════════════════
# never leak the connection string or the OS username
# ════════════════════════════════════════════════════════════════════════════

def test_no_response_body_leaks_dsn_or_username():
    """Requirement 5: never let a connection string, a username, or a raw
    psycopg exception's text reach the response. `BAD_DSN` above is
    engineered to make psycopg's own OperationalError message contain both
    'dbname=' (it always echoes the DSN it tried) and this box's real OS
    username would appear too if libpq falls through to peer auth — so this
    is a genuine adversarial probe, not a rubber-stamp check against a body
    that was never going to contain either string.
    """
    saved = admin_queries.os.environ.get('DIRAC_DSN')
    admin_queries.os.environ['DIRAC_DSN'] = BAD_DSN
    try:
        for path in _EXPECTED_ROUTES + ('/admin/xyz',):
            result = handle_admin(path)
            assert result is not None
            _, body = result
            text = _body_text(body)
            assert 'dbname=' not in text, (
                f'{path}: response body leaks the connection string: {text!r}')
            assert _OS_USERNAME not in text, (
                f'{path}: response body leaks the OS username {_OS_USERNAME!r}: '
                f'{text!r}')
    finally:
        if saved is None:
            admin_queries.os.environ.pop('DIRAC_DSN', None)
        else:
            admin_queries.os.environ['DIRAC_DSN'] = saved


def test_no_response_body_leaks_dsn_or_username_on_the_live_db_either():
    """The same check on the SUCCESS path — a producer's `notes` column or
    similar free text could in principle carry an ivan-authored DSN-looking
    string; this just confirms today's real rows don't."""
    if not _LIVE_DB:
        skip('dirac database not reachable in this environment')
    for path in _EXPECTED_ROUTES:
        _, body = handle_admin(path)
        text = _body_text(body)
        assert 'dbname=' not in text, f'{path}: {text!r}'


# ════════════════════════════════════════════════════════════════════════════
# GET-only / read-only, structurally
# ════════════════════════════════════════════════════════════════════════════

def test_handle_admin_signature_is_read_only_by_construction():
    """There is no request body, no method, nothing mutable in
    `handle_admin`'s signature — it takes a path and returns a tuple. This
    is the structural half of "GET only, no route may write"; the other
    half (every admin_queries.* function is a pure SELECT) is
    admin_queries.py's own claim, verified by its own docstring and by the
    absence of INSERT/UPDATE/DELETE in its module source."""
    import inspect
    sig = inspect.signature(handle_admin)
    assert list(sig.parameters) == ['path']

    # Matched as SQL-statement SHAPES (verb + a target), not as bare verbs —
    # a bare 'TRUNCATE' substring-matches this module's own prose ("display-
    # truncate", "the untruncated value"), which is not a mutation and would
    # be a false positive on the very check meant to prove there are none.
    src = pathlib.Path(admin_queries.__file__).read_text(encoding='utf-8')
    upper = src.upper()
    import re as _re
    for pattern in (r'\bINSERT\s+INTO\b', r'\bUPDATE\s+\w', r'\bDELETE\s+FROM\b',
                    r'\bTRUNCATE\s+TABLE\b', r'\bDROP\s+TABLE\b'):
        m = _re.search(pattern, upper)
        assert m is None, (
            f'admin_queries.py contains a {pattern!r}-shaped statement '
            f'({m.group(0)!r}) — the data layer this router dispatches to '
            'is supposed to be SELECT-only')


# ════════════════════════════════════════════════════════════════════════════
# standalone runner — pytest is not installed in backend/env
# ════════════════════════════════════════════════════════════════════════════

def _tests() -> list:
    return [v for k, v in globals().items()
            if k.startswith('test_') and callable(v)]


def main(argv: list[str]) -> int:
    only = [a for a in argv[1:] if not a.startswith('-')]
    tests = _tests()
    if only:
        tests = [t for t in tests if any(o in t.__name__ for o in only)]

    print(f'admin_routes — {len(tests)} tests, '
          f'pytest {"present" if _HAVE_PYTEST else "ABSENT (standalone mode)"}, '
          f'live DB {"reachable" if _LIVE_DB else "UNREACHABLE"}')
    print('─' * 100)
    passed = failed = skipped = 0
    failures: list[tuple[str, str]] = []

    for fn in tests:
        name = fn.__name__
        t0 = time.time()
        try:
            fn()
        except Skipped as e:
            print(f'SKIP    {name:<62}        {e}')
            skipped += 1
            continue
        except AssertionError:
            dt = time.time() - t0
            print(f'FAIL    {name:<62} {dt:6.2f}s')
            failed += 1
            failures.append((name, traceback.format_exc()))
            continue
        except Exception:
            dt = time.time() - t0
            print(f'ERROR   {name:<62} {dt:6.2f}s')
            failed += 1
            failures.append((name, traceback.format_exc()))
            continue
        dt = time.time() - t0
        print(f'PASS    {name:<62} {dt:6.2f}s')
        passed += 1

    print('─' * 100)
    print(f'{passed} passed · {skipped} skipped · {failed} failed')
    for name, tb in failures:
        print(f'\n══ {name} ' + '═' * (96 - len(name)))
        print(tb)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
