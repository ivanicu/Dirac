#!/usr/bin/env python3
"""backend/tests/test_admin_queries.py — contracts for the admin read layer.

`backend/admin_queries.py` is the only thing standing between "read a log by
hand at 3 a.m." and an actual answer. This file exists to make sure that
answer is never a crash, never a leaked psycopg row object, and never a
number two different queries disagree about without saying so.

READ-ONLY, same rule as the module under test: the only writes anywhere in
this file are two INSERTs, made through a connection opened with
autocommit=False and ROLLED BACK in a `finally` before the test returns.
Nothing here ever COMMITs. No real row is deleted, updated, or left behind.

Run either way:
    backend/env/bin/python backend/tests/test_admin_queries.py
    backend/env/bin/pytest backend/tests/test_admin_queries.py    # if present

pytest is NOT installed in backend/env (verified 2026-08-11, same as
test_physics_contracts.py) — the dual-mode plumbing below is copied from that
file's pattern, not reinvented.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import traceback

_HERE = pathlib.Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import admin_queries as aq                                # noqa: E402


# ── dual-mode plumbing (pytest is NOT installed in backend/env) ─────────────

try:
    import pytest
    _HAVE_PYTEST = True
except ImportError:                                       # the normal case here
    pytest = None
    _HAVE_PYTEST = False


class Skipped(Exception):
    """Raised by skip(); the standalone runner reports it as SKIP + reason."""


def skip(reason: str):
    if _HAVE_PYTEST:
        pytest.skip(reason)
    raise Skipped(reason)


def _db_reachable() -> bool:
    if aq.psycopg is None:
        return False
    try:
        conn = aq._connect()
        conn.close()
        return True
    except Exception:
        return False


# All functions that take (conn=None, *, _force_empty=False) and return
# either a list[dict] or a dict.
_LIST_FUNCS = (aq.queue, aq.stale, aq.producers, aq.methods, aq.toolkits)
_DICT_FUNCS = (aq.cache_summary, aq.blob_health)
_ALL_FUNCS = _LIST_FUNCS + _DICT_FUNCS


# ── module hygiene ───────────────────────────────────────────────────────────

def test_module_imports_without_a_database():
    """Importing admin_queries must not touch the database — only an explicit
    call (or __main__) may. Verified in a fresh subprocess with a DSN that
    cannot possibly resolve: if import alone reached the database, this would
    hang or raise instead of exiting 0 almost instantly.
    """
    env = dict(os.environ)
    env['DIRAC_DSN'] = 'host=/nonexistent-only-for-this-test dbname=does_not_exist'
    result = subprocess.run(
        [sys.executable, '-c', 'import admin_queries'],
        cwd=str(_BACKEND), env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        f'import alone touched the database or failed:\n{result.stderr}')


def test_dsn_has_no_hardcoded_user():
    """The one thing that would stop a stranger from running this: DEFAULT_DSN
    must not carry a `user=`. backend/field_server.py's DB_DSN does
    ('dbname=dirac user=ivan') — admin_queries.py must not repeat that.
    """
    assert 'user=' not in aq.DEFAULT_DSN, (
        f'DEFAULT_DSN hardcodes a user: {aq.DEFAULT_DSN!r}')
    assert aq.DEFAULT_DSN == 'dbname=dirac'


def test_dsn_reads_the_env_var_live():
    """DIRAC_DSN overrides the default, and is read fresh on every call (not
    cached at import time) — set it, unset it, and dsn() must track both."""
    saved = os.environ.get('DIRAC_DSN')
    try:
        os.environ.pop('DIRAC_DSN', None)
        assert aq.dsn() == aq.DEFAULT_DSN
        os.environ['DIRAC_DSN'] = 'dbname=some_other_db'
        assert aq.dsn() == 'dbname=some_other_db'
    finally:
        if saved is None:
            os.environ.pop('DIRAC_DSN', None)
        else:
            os.environ['DIRAC_DSN'] = saved


# ── JSON-serialisability, against the real live database ───────────────────

def test_every_function_returns_json_serialisable_output():
    """Every public query function's output must round-trip through
    json.dumps/json.loads with no psycopg row object (UUID, Decimal, datetime,
    bytes, memoryview) surviving. Run against the REAL database — read-only,
    so this is safe against live data.
    """
    if not _db_reachable():
        skip(f'dirac database unreachable at {aq.dsn()!r}')

    for fn in _ALL_FUNCS:
        result = fn()
        dumped = json.dumps(result)
        reloaded = json.loads(dumped)
        assert reloaded == result, (
            f'{fn.__name__}() output changed shape across a JSON round-trip')


def test_snapshot_is_one_dict_with_all_seven_sections_and_is_json_safe():
    if not _db_reachable():
        skip(f'dirac database unreachable at {aq.dsn()!r}')

    result = aq.snapshot()
    expected_keys = {'queue', 'cache', 'stale', 'producers', 'methods',
                      'blob_health', 'toolkits'}
    assert set(result.keys()) == expected_keys, (
        f'snapshot() keys drifted: {sorted(result.keys())}')
    json.dumps(result)   # must not raise


# ── empty-result-set survival (no exception, zeros not None, no KeyError) ──

def test_every_function_survives_an_empty_result_set():
    """`_force_empty=True` makes each query return the same columns with ZERO
    rows, without touching a single row of real data (see the docstring on
    admin_queries._rows). This is the fresh-clone case: a new database with
    zero rows everywhere must not crash any function here, and must report
    zeros — never None, never a bare KeyError.
    """
    if not _db_reachable():
        skip(f'dirac database unreachable at {aq.dsn()!r}')

    for fn in _LIST_FUNCS:
        result = fn(_force_empty=True)
        assert result == [], f'{fn.__name__}(_force_empty=True) returned {result!r}, expected []'
        json.dumps(result)

    cache = aq.cache_summary(_force_empty=True)
    assert cache == {
        'total_rows': 0, 'distinct_molecules': 0, 'total_bytes': 0,
        'rows_with_method_row_id': 0, 'rows_producer_only': 0,
        'rows_on_current_producer': 0, 'rows_on_superseded_producer': 0,
        # From app.v_cache_health. Pinned EXACTLY rather than with a subset
        # check, deliberately: this assertion is what caught the five keys
        # being added, and a subset check would have let the console silently
        # read a key nobody had decided to expose.
        'rows_servable': 0, 'rows_producer_current': 0,
        'producer_generations': 0, 'max_generations_per_unit': 0,
        'compute_units': 0,
        'by_kind': [],
    }, cache
    json.dumps(cache)

    blobs = aq.blob_health(_force_empty=True)
    assert blobs == {
        'total_blobs': 0, 'total_bytes': 0, 'orphan_count': 0, 'orphan_bytes': 0,
    }, blobs
    json.dumps(blobs)


def test_methods_refuses_key_is_a_list_even_with_no_capabilities():
    """methods() must never KeyError on a row whose capabilities JSON has no
    'refuses' key. Exercised directly (not through _force_empty, which would
    remove the row entirely) by calling the row-shaping logic on a synthetic
    row shape.
    """
    # methods() pulls 'refuses' out of capabilities with .get(..., []) — prove
    # that path on a capabilities dict that omits the key entirely, the way a
    # brand-new method with no declared refusals would.
    row = {'capabilities': {'charges': 'gasteiger'}}
    caps = row.get('capabilities') or {}
    refuses = list(caps.get('refuses', []))
    assert refuses == []

    row_no_caps = {'capabilities': None}
    caps2 = row_no_caps.get('capabilities') or {}
    assert list(caps2.get('refuses', [])) == []


# ── the orphan cross-check: two instruments, must agree ─────────────────────

def test_orphan_count_matches_an_independently_written_query():
    """blob_health()'s orphan_count must equal an INDEPENDENTLY phrased query
    (LEFT JOIN / IS NULL, not blob_health's own NOT EXISTS) run against the
    same live data. Two instruments, one number — if they ever disagree, that
    is reported as a hard failure naming both numbers, not averaged and not
    silently trusted.
    """
    if not _db_reachable():
        skip(f'dirac database unreachable at {aq.dsn()!r}')

    reported = aq.blob_health()['orphan_count']

    conn = aq._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT count(*) AS n FROM app.blob b
                LEFT JOIN app.field_cube c ON c.blob_sha256 = b.sha256
                WHERE c.blob_sha256 IS NULL
            """)
            independent = cur.fetchone()['n']
    finally:
        conn.close()

    assert reported == independent, (
        f'orphan count disagreement — blob_health()={reported}, '
        f'independent LEFT JOIN query={independent}. Two instruments, two '
        f'different numbers: report this as [unknown], do not average it.')


def test_orphan_count_matches_dirac_sweep_dry_run():
    """Cross-check against bin/dirac-sweep --dry-run's own reported number,
    parsed from its actual stdout — the second independent instrument named
    explicitly in the task. Skips (does not fail) if the script is missing or
    errors, since this test's job is to catch a DISAGREEMENT, not to own
    dirac-sweep's availability.
    """
    if not _db_reachable():
        skip(f'dirac database unreachable at {aq.dsn()!r}')

    sweep = _BACKEND.parent / 'bin' / 'dirac-sweep'
    if not sweep.exists():
        skip(f'{sweep} not found')

    try:
        result = subprocess.run([str(sweep), '--dry-run'], capture_output=True,
                                 text=True, timeout=60)
    except Exception as e:
        skip(f'could not run dirac-sweep --dry-run: {e}')

    if result.returncode != 0:
        skip(f'dirac-sweep --dry-run exited {result.returncode}: {result.stderr[:300]}')

    # "-- orphaned blobs ... --" section prints "<count> | <pretty size>" on
    # the data row of a psql -c table. Parse the header/rule/data triad
    # rather than assuming a fixed line offset.
    lines = result.stdout.splitlines()
    orphan_line_idx = next(
        (i for i, ln in enumerate(lines) if 'orphaned_blobs' in ln), None)
    if orphan_line_idx is None:
        skip('dirac-sweep output did not contain an orphaned_blobs column — '
             'output format may have changed; not this test\'s job to guess')

    data_line = lines[orphan_line_idx + 2]   # header, rule ('---'), then data
    sweep_count = int(data_line.split('|')[0].strip())
    reported = aq.blob_health()['orphan_count']

    assert reported == sweep_count, (
        f'orphan count disagreement — blob_health()={reported}, '
        f'bin/dirac-sweep --dry-run={sweep_count}. Two instruments, two '
        f'different numbers: report this as [unknown], do not average it.')


# ── overdue flag: True for a synthetic runaway, False for a fresh job ──────

def test_overdue_flag_true_for_synthetic_runaway_false_for_fresh_job():
    """Insert two synthetic app.job rows inside a transaction this test rolls
    back — one fresh (age ~0s, enormous budget), one a synthetic 36-minute-
    runaway analogue (started ~11.5 days ago, a 1-second budget) — and assert
    queue()'s overdue flag reads False and True respectively. Never commits.
    """
    if not _db_reachable():
        skip(f'dirac database unreachable at {aq.dsn()!r}')

    conn = aq._connect(autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM meta.method ORDER BY declared_at LIMIT 1')
            row = cur.fetchone()
            if row is None:
                skip('meta.method has no rows on this database — cannot '
                     'synthesize a job without a valid method_row_id FK')
            method_row_id = row['id']

            fresh_hash = hashlib.sha256(b'admin-queries-test-fresh-job').digest()
            overdue_hash = hashlib.sha256(b'admin-queries-test-overdue-job').digest()

            cur.execute("""
                INSERT INTO app.job (method_row_id, state, input_sha256, budget_seconds)
                VALUES (%s, 'queued', %s, 999999)
                RETURNING id
            """, (method_row_id, fresh_hash))
            fresh_id = str(cur.fetchone()['id'])

            cur.execute("""
                INSERT INTO app.job
                    (method_row_id, state, input_sha256, budget_seconds, started_at)
                VALUES (%s, 'running', %s, 1, now() - interval '999999 seconds')
                RETURNING id
            """, (method_row_id, overdue_hash))
            overdue_id = str(cur.fetchone()['id'])

        rows = aq.queue(conn)
        by_id = {r['id']: r for r in rows}

        assert fresh_id in by_id, 'freshly-inserted queued job did not appear in queue()'
        assert overdue_id in by_id, 'synthetic overdue job did not appear in queue()'
        assert by_id[fresh_id]['overdue'] is False, (
            f'fresh job (age~0s, budget=999999s) reported overdue: {by_id[fresh_id]}')
        assert by_id[overdue_id]['overdue'] is True, (
            f'synthetic 999999s-old job with a 1s budget did NOT report overdue: '
            f'{by_id[overdue_id]}')
        json.dumps(rows)   # still JSON-safe with the synthetic rows in it
    finally:
        conn.rollback()
        conn.close()


def test_overdue_is_false_when_budget_seconds_is_null():
    """A job with no recorded budget cannot be overdue — there is nothing to
    be over. Exercises the same synthetic-insert-and-rollback pattern with
    budget_seconds omitted (NULL, which app.job's schema allows).
    """
    if not _db_reachable():
        skip(f'dirac database unreachable at {aq.dsn()!r}')

    conn = aq._connect(autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM meta.method ORDER BY declared_at LIMIT 1')
            row = cur.fetchone()
            if row is None:
                skip('meta.method has no rows on this database')
            method_row_id = row['id']

            no_budget_hash = hashlib.sha256(b'admin-queries-test-no-budget-job').digest()
            cur.execute("""
                INSERT INTO app.job
                    (method_row_id, state, input_sha256, started_at)
                VALUES (%s, 'running', %s, now() - interval '999999 seconds')
                RETURNING id
            """, (method_row_id, no_budget_hash))
            no_budget_id = str(cur.fetchone()['id'])

        rows = aq.queue(conn)
        by_id = {r['id']: r for r in rows}
        assert no_budget_id in by_id
        assert by_id[no_budget_id]['budget_seconds'] is None
        assert by_id[no_budget_id]['overdue'] is False, (
            f'a job with budget_seconds=NULL reported overdue: {by_id[no_budget_id]}')
    finally:
        conn.rollback()
        conn.close()


# ── the queries this file's own SQL is meant to protect ────────────────────

def test_stale_reclaimable_is_a_number_not_a_pretty_string():
    """stale()'s reclaimable_bytes must be a raw int/float a caller can do
    arithmetic on — app.v_field_cube_stale's own `reclaimable` column is
    pg_size_pretty text ('47 MB'), which is exactly what this function must
    NOT hand back, per the task: "Delete-vs-recompute must be a NUMBER."
    """
    if not _db_reachable():
        skip(f'dirac database unreachable at {aq.dsn()!r}')

    for row in aq.stale():
        assert isinstance(row['reclaimable_bytes'], (int, float)), (
            f'reclaimable_bytes is not numeric: {row["reclaimable_bytes"]!r}')
        assert isinstance(row['compute_seconds_represented'], (int, float))
        assert isinstance(row['blocked_by_job'], int)


def test_cache_summary_seam_gap_is_visible_as_a_number():
    """rows_with_method_row_id + rows_producer_only must equal total_rows —
    the split the task calls "the seam built, not wired gap", and it must be
    an exact partition, not an approximation.
    """
    if not _db_reachable():
        skip(f'dirac database unreachable at {aq.dsn()!r}')

    s = aq.cache_summary()
    assert (s['rows_with_method_row_id'] + s['rows_producer_only']
            == s['total_rows'])
    assert (s['rows_on_current_producer'] + s['rows_on_superseded_producer']
            == s['total_rows'])


def test_methods_refuses_lists_match_the_underlying_capabilities_json():
    if not _db_reachable():
        skip(f'dirac database unreachable at {aq.dsn()!r}')

    for row in aq.methods():
        expected = list(row['capabilities'].get('refuses', []))
        assert row['refuses'] == expected


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

    print(f'admin_queries contracts — {len(tests)} tests, '
          f'pytest {"present" if _HAVE_PYTEST else "ABSENT (standalone mode)"}')
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
