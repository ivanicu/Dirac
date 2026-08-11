"""app.job lifecycle writes — the seam that was built empty.

Migration 007 shipped the table, the CHECK-enforced state machine, the
one-in-flight unique index, `app.v_job_live` and `app.reap_orphaned_jobs()`.
Nothing ever wrote to it: `SELECT count(*) FROM app.job` read 0 for as long as it
existed, and a seam with no writer that reads as finished is how a schema rots.
This module is the writer.

WHAT IT DOES NOT DO, stated so nobody reads more into it: compute is still
SYNCHRONOUS per HTTP request. This does not make the service asynchronous and it
does not add a worker. It makes every long compute VISIBLE and RECONNECTABLE
while it runs, which is the precondition for a queue rather than the queue
itself — and per ROADMAP.md P1 the queue then lands as different writers to a
table that already refuses illegal states.

Three properties that are the actual content:

① A JOB ROW MAY NEVER COST A RESULT. Every failure here degrades to "no job
   row" and the field still computes and still returns. But NOT silently: the
   counters below are exposed at /health and through the ops surface, because a
   ledger that is quietly not being written looks exactly like a system with no
   work in it — and "the queue is empty" vs "nobody is recording" is the same
   OFFLINE-vs-EMPTY confusion the ops console was built to refuse.

② THE STATE MACHINE IS THE SCHEMA'S, NOT MINE. queued has no started_at; a
   terminal state has finished_at; failed has an error_code that is not
   CANCELLED; done has seconds. Those are CHECK constraints, so a wrong
   transition raises instead of storing a lie. This module does not re-implement
   them and deliberately does not pre-validate — a duplicated rule drifts, and
   the copy is never the one that gets corrected.

③ A CONFLICT ON `job_one_inflight` IS A MEASUREMENT, NOT AN ERROR. That index
   makes an identical (method, input, params) impossible to have twice in
   flight. Today two identical concurrent requests both compute — nothing
   queues, nothing waits — so the conflict is how often that duplicated work
   actually happens. It is counted, the request proceeds, and the number is the
   evidence for building the wait-for-result path. Guessing at that number was
   never going to justify the work.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

# Counted rather than logged-and-forgotten. Same reasoning as the persist
# counters: a number nobody can read is a log line with extra steps.
_counters = {
    'opened': 0,          # rows inserted
    'done': 0,            # reached state='done'
    'failed': 0,          # reached state='failed'
    'inflight_conflict': 0,   # an identical job was ALREADY running (see ③)
    'write_failed': 0,    # the ledger write itself failed; compute unaffected
    'last_error': None,   # type: ignore[dict-item]
}
_lock = threading.Lock()

# app.job_error (migration 007) is a SUBSET of contracts/errors.json — it omits
# the codes a job cannot carry. Mapping here rather than at the call site so
# there is one place to look when a new code appears; an unmapped code becomes
# INTERNAL rather than raising, because losing the ROW to an enum mismatch would
# be a worse outcome than losing the code's precision.
_JOB_ERROR = {
    'PARSE', 'UNCONVERGED', 'UNPARAMETERIZED', 'BUDGET',
    'UNSUPPORTED', 'TOO_LARGE', 'INTERNAL', 'CANCELLED',
}


def counters() -> dict:
    with _lock:
        return dict(_counters)


def _bump(key: str, error: str | None = None) -> None:
    with _lock:
        _counters[key] = _counters[key] + 1
        if error is not None:
            _counters['last_error'] = error


def job_error_code(code: str | None) -> str:
    """Map an envelope error code onto app.job_error, never raising."""
    if code in _JOB_ERROR:
        return code
    return 'INTERNAL'


class JobLedger:
    """Writes job rows through a caller-supplied connection factory.

    `connect` is injected rather than imported so this module has no opinion
    about how the service connects, and so a test can hand it a factory that
    raises — which is the only way to prove property ① instead of asserting it.
    """

    def __init__(self, connect: Callable[[], Any], worker: str) -> None:
        self._connect = connect
        self.worker = worker

    # ── lifecycle ────────────────────────────────────────────────────────
    def open(self, *, method_row_id: str, input_sha256: bytes, params: dict,
             budget_seconds: float | None = None, est_seconds: float | None = None,
             compound_id: str | None = None,
             conformer_hash: bytes | None = None) -> str | None:
        """Insert a 'running' row and return its id, or None.

        Inserted directly as RUNNING with started_at set: in a synchronous
        service the row would be 'queued' for microseconds, and a state the
        system cannot actually be observed in is noise in the ledger. The queued
        state stays valid in the schema for the worker that will use it.

        None means "no row" — the DB was unreachable, the write failed, or an
        identical job is already in flight. In every case the caller proceeds.
        """
        # budget_seconds has a CHECK (> 0), and 0 is a legitimate request here
        # ("refuse immediately, tell me the cost"), so 0 must arrive as NULL
        # rather than violate the constraint and lose the whole row.
        budget = float(budget_seconds) if budget_seconds else None
        if budget is not None and budget <= 0:
            budget = None
        try:
            import json
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO app.job (method_row_id, state, input_sha256, params, "
                    "       budget_seconds, est_seconds, compound_id, conformer_hash, "
                    "       worker, started_at) "
                    "VALUES (%s, 'running', %s, %s, %s, %s, %s, %s, %s, now()) "
                    'RETURNING id',
                    (method_row_id, input_sha256, json.dumps(params), budget,
                     est_seconds, compound_id, conformer_hash, self.worker))
                row = cur.fetchone()
            _bump('opened')
            return str(row[0]) if row else None
        except Exception as e:                                       # noqa: BLE001
            # A unique-violation on job_one_inflight is the DEDUP signal, not a
            # fault. Distinguished by SQLSTATE 23505 rather than by message text
            # so a locale or a version change cannot turn a measurement into an
            # error count.
            if getattr(getattr(e, 'sqlstate', None), 'strip', None) and e.sqlstate == '23505':
                _bump('inflight_conflict')
            elif '23505' in str(getattr(e, 'sqlstate', '')) or 'job_one_inflight' in str(e):
                _bump('inflight_conflict')
            else:
                _bump('write_failed', f'{type(e).__name__}: {e}')
            return None

    def done(self, job_id: str | None, *, seconds: float,
             field_cube_id: str | None = None, peak_rss_mb: int | None = None) -> None:
        if job_id is None:
            return
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE app.job SET state = 'done', finished_at = now(), "
                    '       seconds = %s, field_cube_id = %s, peak_rss_mb = %s '
                    "WHERE id = %s AND state = 'running'",
                    (round(float(seconds), 3), field_cube_id, peak_rss_mb, job_id))
            _bump('done')
        except Exception as e:                                        # noqa: BLE001
            _bump('write_failed', f'{type(e).__name__}: {e}')

    def failed(self, job_id: str | None, *, code: str, detail: str,
               seconds: float | None = None) -> None:
        """Terminal-failed. `code` is mapped onto app.job_error, never raising.

        A CANCELLED code is stored as state='cancelled', because the schema's
        job_failed_has_code says failed ⇔ a code that is not CANCELLED — the two
        are different facts about the world and the constraint knows it.
        """
        if job_id is None:
            return
        mapped = job_error_code(code)
        state = 'cancelled' if mapped == 'CANCELLED' else 'failed'
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    'UPDATE app.job SET state = %s, finished_at = now(), '
                    '       seconds = %s, error_code = %s, error_detail = %s '
                    "WHERE id = %s AND state = 'running'",
                    (state, None if seconds is None else round(float(seconds), 3),
                     mapped, detail[:2000], job_id))
            _bump('failed')
        except Exception as e:                                        # noqa: BLE001
            _bump('write_failed', f'{type(e).__name__}: {e}')

    # ── startup ──────────────────────────────────────────────────────────
    def reap(self) -> int:
        """Fail every row this worker left in flight when it died.

        Without this, a restart leaves rows 'running' forever and
        `app.v_job_live`'s age_seconds grows without bound — the ledger would
        report work that no process is doing, which is worse than no ledger
        because it reads as a hung system.
        """
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute('SELECT app.reap_orphaned_jobs(%s)', (self.worker,))
                row = cur.fetchone()
            return int(row[0]) if row else 0
        except Exception as e:                                        # noqa: BLE001
            _bump('write_failed', f'{type(e).__name__}: {e}')
            return 0


def make_worker_name(pid: int, producer_version: str) -> str:
    """Identify the process AND the code it is running.

    A bare pid is reusable after a restart, so a reap keyed on it can either
    miss rows or claim another process's. Including the producer version means a
    reap after a code change cannot silently adopt the previous generation's
    in-flight rows as its own.
    """
    return f'fields/{pid}/{producer_version}'


def new_uuid() -> str:
    return str(uuid.uuid4())
