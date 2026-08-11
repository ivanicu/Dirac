#!/usr/bin/env python3
"""backend/admin_queries.py — the read-only data layer for the operations surface.

Today "what is it doing / what is it holding / which generation produced this"
has no answer except reading a log by hand: a 1 GB RSS went unnoticed for
hours, and a 36-minute runaway SCF (app.job's own reason for existing — see
migration 007_method_registry_and_job_ledger.sql) was found only because a
human happened to be watching `top`. The schema already has the answer —
app.v_job_live, app.v_field_cube_stale, meta.producer, meta.method — this file
just points queries at it and hands back plain dicts/lists a human, a CLI, or
a future /admin route can all read the same way.

Every function here is a pure SELECT. Nothing in this module writes to the
database. The destructive sweep lives in bin/dirac-sweep and stays there.

Usage from another module:

    import admin_queries
    admin_queries.snapshot()        # everything, one dict, one transaction

CLI, for a human at 3 a.m. with no browser:

    backend/env/bin/python backend/admin_queries.py [queue|cache|stale|
                                    producers|methods|blobs|toolkits|all]
    (no argument = all)

Connection: the `DIRAC_DSN` env var, default `dbname=dirac`. Deliberately
carries NO `user=`. backend/field_server.py hardcodes `DB_DSN = 'dbname=dirac
user=ivan'` — the one thing in this codebase that stops a stranger who clones
it from running it as themselves. This module falls through to libpq's normal
resolution (the OS user via peer auth, or PGUSER/PGHOST/etc. if set), same as
`psql` with no `-U` does.
"""
from __future__ import annotations

import datetime
import decimal
import json
import os
import sys
import uuid
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:                                   # pragma: no cover
    psycopg = None
    dict_row = None

DEFAULT_DSN = 'dbname=dirac'


def dsn() -> str:
    """The connection string this module will use: DIRAC_DSN, or the default.

    Read live, every call — not cached at import time — so a test or a CLI
    invocation can override it via the environment without reloading the
    module.
    """
    return os.environ.get('DIRAC_DSN', DEFAULT_DSN)


def _connect(autocommit: bool = True):
    """One psycopg connection helper. Every query function in this file goes
    through this (directly, or by receiving a `conn` a caller already opened).

    Not called at import time anywhere in this module — importing
    admin_queries must not touch the database (test_module_imports_without_a_
    database asserts this in a subprocess with an unreachable DSN).
    """
    if psycopg is None:
        raise RuntimeError(
            'psycopg is not importable — admin_queries.py needs it to reach '
            'the dirac database. Use backend/env/bin/python, which has it '
            '(plain `python3` on this box does not).')
    return psycopg.connect(dsn(), autocommit=autocommit, row_factory=dict_row)


# ── JSON-safe serialisation — every row funnels through this ────────────────
#
# psycopg3 hands back native Python types (uuid.UUID, decimal.Decimal,
# datetime.datetime/date, dict for jsonb, bytes for bytea — verified live
# against this database, 2026-08-11) — none of which json.dumps accepts. This
# is the one place that fact is handled, so every function below returns
# plain JSON-serialisable dicts/lists with no psycopg row object leaking out.

def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, memoryview):
        value = bytes(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _row(d: dict) -> dict:
    return {k: _jsonable(v) for k, v in d.items()}


def _rows(conn, sql: str, params: tuple = (), *, _force_empty: bool = False) -> list[dict]:
    """Run `sql`, return a list of JSON-safe dicts.

    `_force_empty` is a TEST-ONLY hook — no production caller passes it. It
    wraps the query so it returns the same columns with ZERO rows, without
    touching a single row of real data:

        SELECT * FROM (<sql>) AS probe WHERE false

    This is how the empty-database tests exercise the exact
    aggregation/serialisation code a fresh clone with zero rows would hit
    (COALESCE, empty list-comprehensions, the by-kind GROUP BY collapsing to
    nothing) — the alternative, deleting real rows inside a transaction and
    rolling back, takes real locks on tables three other agents are working
    against right now, for no better guarantee. It also correctly empties
    even a single-row aggregate (`SELECT count(*) FROM ...` always returns
    one row on a real empty table; wrapped in `WHERE false` it returns none),
    which is exactly the case `_one()` below is written to survive.
    """
    if _force_empty:
        sql = f'SELECT * FROM ({sql}) AS __admin_queries_empty_probe WHERE false'
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [_row(r) for r in cur.fetchall()]


def _one(conn, sql: str, params: tuple = (), *, _force_empty: bool = False,
          default: dict | None = None) -> dict:
    """Like `_rows`, but for a query that normally returns exactly one row
    (an aggregate with no GROUP BY). Falls back to `default` — a dict of
    zeros, never {} and never None — if `_force_empty` (or any other reason)
    leaves zero rows, so callers never see a bare KeyError.
    """
    rows = _rows(conn, sql, params, _force_empty=_force_empty)
    return rows[0] if rows else dict(default or {})


# ── queue() ───────────────────────────────────────────────────────────────

_QUEUE_SQL = """
SELECT id, method_id, method_version, state, compound_id,
       budget_seconds, est_seconds, age_seconds, worker, created_at, started_at
  FROM app.v_job_live
 ORDER BY created_at
"""


def queue(conn=None, *, _force_empty: bool = False) -> list[dict]:
    """The live queue (app.v_job_live) with `overdue` computed here.

    overdue = age_seconds > budget_seconds. This is the 36-minute runaway
    made into a boolean a dashboard can sort and alert on, instead of a
    number a human has to compare by eye in a log. A job with no
    budget_seconds recorded can never be overdue (there is nothing to be
    over) — that is a fact about the job, not a bug in this function.
    """
    owns = conn is None
    conn = conn or _connect()
    try:
        rows = _rows(conn, _QUEUE_SQL, _force_empty=_force_empty)
        out = []
        for r in rows:
            age = r['age_seconds']
            budget = r['budget_seconds']
            overdue = age is not None and budget is not None and age > budget
            out.append({
                'id': r['id'],
                'method': r['method_id'],
                'method_version': r['method_version'],
                'state': r['state'],
                'compound_id': r['compound_id'],
                'age_seconds': age,
                'budget_seconds': budget,
                'overdue': bool(overdue),
                'worker': r['worker'],
                'created_at': r['created_at'],
                'started_at': r['started_at'],
            })
        return out
    finally:
        if owns:
            conn.close()


# ── cache_summary() ──────────────────────────────────────────────────────

_CACHE_TOTALS_SQL = """
SELECT count(*) AS total_rows,
       count(DISTINCT c.molfile_sha256) AS distinct_molecules,
       coalesce(sum(b.byte_len), 0)::bigint AS total_bytes,
       count(c.method_row_id) AS rows_with_method_row_id,
       count(*) FILTER (WHERE c.method_row_id IS NULL) AS rows_producer_only
  FROM app.field_cube c
  JOIN app.blob b ON b.sha256 = c.blob_sha256
"""

_CACHE_TOTALS_ZERO = {
    'total_rows': 0, 'distinct_molecules': 0, 'total_bytes': 0,
    'rows_with_method_row_id': 0, 'rows_producer_only': 0,
}

_CACHE_BY_KIND_SQL = """
SELECT c.kind AS kind,
       count(*) AS rows,
       count(DISTINCT c.molfile_sha256) AS distinct_molecules,
       coalesce(sum(b.byte_len), 0)::bigint AS bytes
  FROM app.field_cube c
  JOIN app.blob b ON b.sha256 = c.blob_sha256
 GROUP BY c.kind
 ORDER BY c.kind
"""

# Not asked for explicitly, but one JOIN away from the same question
# cache_summary already answers ("what is it holding"), and it is the
# sharpest number in this file: on this database it is 0 / 18. Every cached
# row belongs to a superseded producer, so app.v_field_cube_current — the
# ONLY view a cache lookup is supposed to read (migration 006's own comment:
# "Read through this view and a superseded producer's row can never be
# served") — is EMPTY right now. Every /field request is a forced recompute.
_CACHE_PRODUCER_SPLIT_SQL = """
SELECT count(*) FILTER (WHERE p.superseded_at IS NULL)     AS rows_on_current_producer,
       count(*) FILTER (WHERE p.superseded_at IS NOT NULL) AS rows_on_superseded_producer
  FROM app.field_cube c
  JOIN meta.producer p ON p.id = c.producer_id
"""

_CACHE_PRODUCER_SPLIT_ZERO = {
    'rows_on_current_producer': 0, 'rows_on_superseded_producer': 0,
}


# app.v_cache_health (migration 010) is the AUTHORITY on invalidation churn,
# and this module was reproducing an overlapping computation of its own instead
# of reading it. Two reasons that is worse than a duplicate query: the view's
# `max_generations_per_unit` is the number that made the cold-cache diagnosis
# legible (13 producer generations in a day vs 2 per compute unit, i.e. reads
# keyed on the producer invalidated ~6x more than the physics actually changed),
# and it cannot be derived from the columns this module was selecting. A second
# home for the same fact also drifts: the hand-rolled join called it
# `rows_on_current_producer` while the view calls it `rows_producer_current`,
# so the console had to read both spellings defensively to avoid rendering a
# fabricated 0 — and a silent 0 there reads as "no churn", the exact opposite
# of the condition it was measuring.
_CACHE_HEALTH_SQL = """
SELECT rows_total, rows_with_method, rows_servable, rows_producer_current,
       producer_generations, max_generations_per_unit, compute_units
  FROM app.v_cache_health
"""
_CACHE_HEALTH_ZERO = {'rows_total': 0, 'rows_with_method': 0, 'rows_servable': 0,
                      'rows_producer_current': 0, 'producer_generations': 0,
                      'max_generations_per_unit': 0, 'compute_units': 0}


def cache_summary(conn=None, *, _force_empty: bool = False) -> dict:
    """What the cache is holding, plus two gaps made visible as numbers.

    `rows_producer_only` vs `rows_with_method_row_id` — migration 007 added
    field_cube.method_row_id as a nullable dual-write column ("existing rows
    keep producer_id, new rows carry both"). Nothing writes it yet: this is
    the "seam built, not wired" gap, reported as a count rather than narrated.

    `rows_on_current_producer` vs `rows_on_superseded_producer` — see the SQL
    comment above `_CACHE_PRODUCER_SPLIT_SQL`.
    """
    owns = conn is None
    conn = conn or _connect()
    try:
        totals = _one(conn, _CACHE_TOTALS_SQL, _force_empty=_force_empty,
                       default=_CACHE_TOTALS_ZERO)
        by_kind = _rows(conn, _CACHE_BY_KIND_SQL, _force_empty=_force_empty)
        split = _one(conn, _CACHE_PRODUCER_SPLIT_SQL, _force_empty=_force_empty,
                      default=_CACHE_PRODUCER_SPLIT_ZERO)
        health = _one(conn, _CACHE_HEALTH_SQL, _force_empty=_force_empty,
                      default=_CACHE_HEALTH_ZERO)
        return {
            'total_rows': totals['total_rows'],
            'distinct_molecules': totals['distinct_molecules'],
            'total_bytes': totals['total_bytes'],
            'rows_with_method_row_id': totals['rows_with_method_row_id'],
            'rows_producer_only': totals['rows_producer_only'],
            'rows_on_current_producer': split['rows_on_current_producer'],
            'rows_on_superseded_producer': split['rows_on_superseded_producer'],
            # From app.v_cache_health, under the VIEW's names. The overlapping
            # local spelling above is kept for one release so the console does
            # not break mid-flight, but the view's names are the ones to read.
            'rows_servable': health['rows_servable'],
            'rows_producer_current': health['rows_producer_current'],
            'producer_generations': health['producer_generations'],
            'max_generations_per_unit': health['max_generations_per_unit'],
            'compute_units': health['compute_units'],
            'by_kind': by_kind,
        }
    finally:
        if owns:
            conn.close()


# ── stale() ──────────────────────────────────────────────────────────────

# Deliberately NOT "SELECT * FROM app.v_field_cube_stale": that view formats
# reclaimable bytes with pg_size_pretty (a string like '47 MB'), and
# delete-vs-recompute needs a NUMBER, not something to re-parse. This
# reproduces the view's own JOIN/WHERE/GROUP BY exactly
# (006_producer_identity.sql's app.v_field_cube_stale), swaps the pretty
# string for a raw byte count, and adds blocked_by_job — the same predicate
# bin/dirac-sweep uses to decide what it will actually refuse to delete.
_STALE_SQL = """
SELECT p.service AS service, p.version AS producer_version, p.superseded_at,
       count(*) AS rows_to_sweep,
       coalesce(sum(b.byte_len), 0)::bigint AS reclaimable_bytes,
       coalesce(sum(c.seconds), 0)::double precision AS compute_seconds_represented,
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM app.job j WHERE j.field_cube_id = c.id)
       ) AS blocked_by_job
  FROM app.field_cube c
  JOIN meta.producer p ON p.id = c.producer_id
  JOIN app.blob b ON b.sha256 = c.blob_sha256
 WHERE p.superseded_at IS NOT NULL
 GROUP BY p.service, p.version, p.superseded_at
 ORDER BY p.superseded_at
"""


def stale(conn=None, *, _force_empty: bool = False) -> list[dict]:
    """Per superseded producer generation: rows, reclaimable bytes, compute
    time represented, and how many of those rows a job still points at
    (bin/dirac-sweep skips those even with --apply). `rows_to_sweep -
    blocked_by_job` is the number that will actually be freed.
    """
    owns = conn is None
    conn = conn or _connect()
    try:
        return _rows(conn, _STALE_SQL, _force_empty=_force_empty)
    finally:
        if owns:
            conn.close()


# ── producers() / methods() ──────────────────────────────────────────────

_PRODUCERS_SQL = """
SELECT id, service, version, source_sha256, toolkit_id, declared_at,
       superseded_at, notes,
       (superseded_at IS NULL) AS current
  FROM meta.producer
 ORDER BY service, declared_at
"""


def producers(conn=None, *, _force_empty: bool = False) -> list[dict]:
    """The producer generation history (meta.producer): what is current, and
    what was superseded, and when."""
    owns = conn is None
    conn = conn or _connect()
    try:
        return _rows(conn, _PRODUCERS_SQL, _force_empty=_force_empty)
    finally:
        if owns:
            conn.close()


_METHODS_SQL = """
SELECT id, method_id, version, source_sha256, exec_class, toolkit_id,
       declared_at, superseded_at, notes, capabilities,
       (superseded_at IS NULL) AS current
  FROM meta.method
 ORDER BY method_id, declared_at
"""


def methods(conn=None, *, _force_empty: bool = False) -> list[dict]:
    """The method generation history (meta.method), with `refuses` lifted out
    of `capabilities` to its own key so an operator can read what a method
    will refuse before a chemist hits it, without knowing the capabilities
    JSON shape. Missing/absent `refuses` reads as an empty list, never a
    KeyError.
    """
    owns = conn is None
    conn = conn or _connect()
    try:
        rows = _rows(conn, _METHODS_SQL, _force_empty=_force_empty)
        for r in rows:
            caps = r.get('capabilities') or {}
            r['refuses'] = list(caps.get('refuses', []))
        return rows
    finally:
        if owns:
            conn.close()


# ── blob_health() ────────────────────────────────────────────────────────

_BLOB_TOTALS_SQL = """
SELECT count(*) AS total_blobs, coalesce(sum(byte_len), 0)::bigint AS total_bytes
  FROM app.blob
"""
_BLOB_TOTALS_ZERO = {'total_blobs': 0, 'total_bytes': 0}

# Byte-for-byte bin/dirac-sweep's own orphan predicate ("orphaned blobs
# (app.blob rows with zero app.field_cube reference)") — the two-statement
# write that leaves orphans behind is dirac-sweep's incident to describe, not
# this file's to redefine. If these two ever disagree on what an orphan is,
# that is a bug worth finding, not two honest opinions.
_BLOB_ORPHAN_SQL = """
SELECT count(*) AS orphan_count, coalesce(sum(byte_len), 0)::bigint AS orphan_bytes
  FROM app.blob b
 WHERE NOT EXISTS (SELECT 1 FROM app.field_cube c WHERE c.blob_sha256 = b.sha256)
"""
_BLOB_ORPHAN_ZERO = {'orphan_count': 0, 'orphan_bytes': 0}


def blob_health(conn=None, *, _force_empty: bool = False) -> dict:
    """Blob store totals plus orphans — a blob no app.field_cube row
    references any more (left behind by a two-statement write, per
    bin/dirac-sweep's own comment)."""
    owns = conn is None
    conn = conn or _connect()
    try:
        totals = _one(conn, _BLOB_TOTALS_SQL, _force_empty=_force_empty,
                       default=_BLOB_TOTALS_ZERO)
        orphan = _one(conn, _BLOB_ORPHAN_SQL, _force_empty=_force_empty,
                       default=_BLOB_ORPHAN_ZERO)
        return {
            'total_blobs': totals['total_blobs'],
            'total_bytes': totals['total_bytes'],
            'orphan_count': orphan['orphan_count'],
            'orphan_bytes': orphan['orphan_bytes'],
        }
    finally:
        if owns:
            conn.close()


# ── toolkits() ───────────────────────────────────────────────────────────

_TOOLKITS_SQL = """
SELECT id, name, version, build_note, verified_at
  FROM meta.toolkit
 ORDER BY name, verified_at
"""


def toolkits(conn=None, *, _force_empty: bool = False) -> list[dict]:
    """The rdkit/pyscf (etc.) versions this database has seen (meta.toolkit)."""
    owns = conn is None
    conn = conn or _connect()
    try:
        return _rows(conn, _TOOLKITS_SQL, _force_empty=_force_empty)
    finally:
        if owns:
            conn.close()


# ── snapshot() ───────────────────────────────────────────────────────────

def snapshot(conn=None) -> dict:
    """Everything above, in one dict — what a future /admin route serialises
    directly.

    Opens exactly one connection and one (implicit) transaction, and passes
    it to every sub-function, so a "snapshot" is actually one: reading the
    queue, then the cache, then the stale list from SEPARATE transactions
    could interleave with a real write in between and hand back a state that
    never existed as a whole. Rolled back rather than committed on the way
    out — nothing here ever writes, and rollback says so.
    """
    owns = conn is None
    conn = conn or _connect(autocommit=False)
    try:
        return {
            'queue': queue(conn),
            'cache': cache_summary(conn),
            'stale': stale(conn),
            'producers': producers(conn),
            'methods': methods(conn),
            'blob_health': blob_health(conn),
            'toolkits': toolkits(conn),
        }
    finally:
        if owns:
            conn.rollback()
            conn.close()


# ════════════════════════════════════════════════════════════════════════════
# CLI — a compact human table, readable without a browser
# ════════════════════════════════════════════════════════════════════════════

_SECTIONS = ('queue', 'cache', 'stale', 'producers', 'methods', 'blobs', 'toolkits')


def _fmt_bytes(n) -> str:
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(n) < 1024.0:
            return f'{n:,.0f}{unit}' if unit == 'B' else f'{n:,.1f}{unit}'
        n /= 1024.0
    return f'{n:,.1f}TB'


def _fmt(v, width: int = 0, align: str = '<') -> str:
    s = '-' if v is None else str(v)
    return f'{s:{align}{width}}' if width else s


def _fmt_ts(v, width: int = 19) -> str:
    """Timestamps come back as full ISO-8601 with microseconds and a UTC
    offset (~32 chars) — display-truncate to second precision so table
    columns stay aligned. The untruncated value is still what every
    query function actually returns; this is print-only."""
    s = '-' if v is None else str(v)[:19]
    return f'{s:<{width}}'


def _print_queue(rows: list[dict]) -> None:
    n_overdue = sum(1 for r in rows if r['overdue'])
    print(f'== QUEUE — {len(rows)} live job(s), {n_overdue} overdue ==')
    if not rows:
        print('  (empty)')
        return
    print(f'  {"ID":<36} {"METHOD":<20} {"STATE":<9} {"AGE_S":>10} {"BUDGET_S":>10}  OVERDUE')
    for r in rows:
        flag = '<-- OVERDUE' if r['overdue'] else ''
        print(f'  {_fmt(r["id"], 36)} {_fmt(r["method"], 20)} {_fmt(r["state"], 9)} '
              f'{_fmt(r["age_seconds"], 10, ">")} {_fmt(r["budget_seconds"], 10, ">")}  {flag}')


def _print_cache(s: dict) -> None:
    print('== CACHE ==')
    print(f'  total_rows={s["total_rows"]}  distinct_molecules={s["distinct_molecules"]}  '
          f'total_bytes={s["total_bytes"]:,} ({_fmt_bytes(s["total_bytes"])})')
    gap_flag = '  <-- seam built, not wired' if s['rows_with_method_row_id'] == 0 and s['total_rows'] else ''
    print(f'  method_row_id wired: {s["rows_with_method_row_id"]}/{s["total_rows"]}   '
          f'producer-only: {s["rows_producer_only"]}/{s["total_rows"]}{gap_flag}')
    cur_flag = ('  <-- v_field_cube_current is EMPTY, every request recomputes'
                if s['rows_on_current_producer'] == 0 and s['total_rows'] else '')
    print(f'  on current producer: {s["rows_on_current_producer"]}/{s["total_rows"]}   '
          f'on superseded producer: {s["rows_on_superseded_producer"]}/{s["total_rows"]}{cur_flag}')
    if s['by_kind']:
        print(f'  {"KIND":<10} {"ROWS":>6} {"DISTINCT_MOL":>13} {"BYTES":>14}')
        for k in s['by_kind']:
            print(f'  {_fmt(k["kind"], 10)} {k["rows"]:>6} {k["distinct_molecules"]:>13} {k["bytes"]:>14,}')


def _print_stale(rows: list[dict]) -> None:
    print(f'== STALE — {len(rows)} superseded generation(s) ==')
    if not rows:
        print('  (nothing stale)')
        return
    print(f'  {"SERVICE":<14} {"VERSION":<10} {"SUPERSEDED_AT":<19} {"ROWS":>5} '
          f'{"RECLAIMABLE":>12} {"COMPUTE_S":>10} {"BLOCKED":>8}')
    tot_rows = tot_bytes = tot_secs = tot_blocked = 0
    for r in rows:
        print(f'  {_fmt(r["service"], 14)} {_fmt(r["producer_version"], 10)} '
              f'{_fmt_ts(r["superseded_at"])} {r["rows_to_sweep"]:>5} '
              f'{_fmt_bytes(r["reclaimable_bytes"]):>12} {r["compute_seconds_represented"]:>10.2f} '
              f'{r["blocked_by_job"]:>8}')
        tot_rows += r['rows_to_sweep']
        tot_bytes += r['reclaimable_bytes']
        tot_secs += r['compute_seconds_represented']
        tot_blocked += r['blocked_by_job']
    print(f'  TOTAL: {tot_rows} rows, {_fmt_bytes(tot_bytes)} reclaimable, '
          f'{tot_secs:.2f}s compute represented, {tot_blocked} blocked by a live job')


def _print_producers(rows: list[dict]) -> None:
    print(f'== PRODUCERS — {len(rows)} generation(s) ==')
    if not rows:
        print('  (none)')
        return
    print(f'  {"SERVICE":<14} {"VERSION":<12} {"DECLARED_AT":<19} {"SUPERSEDED_AT":<19} CURRENT')
    for r in rows:
        cur = 'YES' if r['current'] else 'no'
        print(f'  {_fmt(r["service"], 14)} {_fmt(r["version"], 12)} '
              f'{_fmt_ts(r["declared_at"])} {_fmt_ts(r["superseded_at"])} {cur}')


def _print_methods(rows: list[dict]) -> None:
    print(f'== METHODS — {len(rows)} generation(s) ==')
    if not rows:
        print('  (none)')
        return
    print(f'  {"METHOD_ID":<20} {"VERSION":<14} {"EXEC_CLASS":<12} CURRENT  REFUSES')
    for r in rows:
        cur = 'YES' if r['current'] else 'no '
        print(f'  {_fmt(r["method_id"], 20)} {_fmt(r["version"], 14)} '
              f'{_fmt(r["exec_class"], 12)} {cur:<8} {", ".join(r["refuses"]) or "(none declared)"}')


def _print_blobs(h: dict) -> None:
    print('== BLOB HEALTH ==')
    print(f'  total_blobs={h["total_blobs"]}  total_bytes={h["total_bytes"]:,} '
          f'({_fmt_bytes(h["total_bytes"])})')
    flag = '  <-- reclaimable by bin/dirac-sweep --apply' if h['orphan_count'] else ''
    print(f'  orphan_count={h["orphan_count"]}  orphan_bytes={h["orphan_bytes"]:,} '
          f'({_fmt_bytes(h["orphan_bytes"])}){flag}')


def _print_toolkits(rows: list[dict]) -> None:
    print(f'== TOOLKITS — {len(rows)} seen ==')
    if not rows:
        print('  (none)')
        return
    for r in rows:
        print(f'  {_fmt(r["name"], 10)} {_fmt(r["version"], 14)}  '
              f'verified_at={_fmt_ts(r["verified_at"], 0)}')


def main(argv: list[str]) -> int:
    which = argv[1] if len(argv) > 1 else 'all'
    valid = set(_SECTIONS) | {'all'}
    if which not in valid:
        print(f'usage: {argv[0]} [{"|".join(_SECTIONS)}|all]', file=sys.stderr)
        return 1

    sections = list(_SECTIONS) if which == 'all' else [which]

    try:
        conn = _connect(autocommit=False)
    except Exception as e:
        print(f'admin_queries: cannot reach the database ({dsn()!r}): {e}', file=sys.stderr)
        return 1

    printers = {
        'queue': lambda: _print_queue(queue(conn)),
        'cache': lambda: _print_cache(cache_summary(conn)),
        'stale': lambda: _print_stale(stale(conn)),
        'producers': lambda: _print_producers(producers(conn)),
        'methods': lambda: _print_methods(methods(conn)),
        'blobs': lambda: _print_blobs(blob_health(conn)),
        'toolkits': lambda: _print_toolkits(toolkits(conn)),
    }

    rc = 0
    try:
        for i, name in enumerate(sections):
            try:
                printers[name]()
            except Exception as e:
                print(f'[{name}] ERROR: {e}', file=sys.stderr)
                rc = 1
            if which == 'all' and i < len(sections) - 1:
                print()
    finally:
        conn.rollback()
        conn.close()
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv))
