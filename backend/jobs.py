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
    def reap(self) -> dict[str, int]:
        """Fail every 'running' row that no live process is doing.

        ⚠ THIS USED TO REAP ONLY `self.worker`, WHICH REAPED NOTHING. The worker
        name contains the pid, so after a restart the new process's name never
        matches the dead one's, and the dead one's rows stayed 'running' forever.
        Measured: a fields.qm.homo row sat at age_seconds = 10809 (three hours)
        from a worker whose pid had been gone for most of it, while
        `app.v_job_live` reported it as live work. This module's own docstring
        predicted the failure — "a reap keyed on [a bare pid] can either miss rows
        or claim another process's" — and then shipped the version that misses.

        TWO INDEPENDENT CRITERIA, because they fail differently:

        ① THE WORKER'S PROCESS IS GONE. Parsed out of the worker name and checked
           with signal 0. Precise, and safe when two daemons run at once: a live
           sibling's rows are never touched. Its blind spot is PID REUSE — a dead
           worker whose pid has been recycled by an unrelated process looks alive.

        ② THE ROW IS OLDER THAN ANY LEGITIMATE JOB. No job may outlive its own
           budget by much, so a row past the hard ceiling is finished no matter
           whose pid is on it. This is what catches ① 's blind spot, and it is
           also the only thing that catches a row from a worker on ANOTHER HOST
           once compute moves off this box (ROADMAP P2) — a pid check is
           meaningless across machines, so the age criterion is the one that
           survives the architecture it is written for.

        Counted separately in the return value, because "the process died" and
        "the job overran" are different operational facts and collapsing them
        would hide whichever is rarer.
        """
        out = {'dead_worker': 0, 'overran': 0}
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT worker FROM app.job "
                    " WHERE state IN ('queued','running') AND worker IS NOT NULL")
                workers = [r[0] for r in cur.fetchall()]
                for w in workers:
                    if w != self.worker and _worker_is_alive(w):
                        continue          # a live sibling — not ours to reap
                    if w == self.worker:
                        # Our own name can only appear if a previous process had
                        # the same pid AND the same source hash. Reap it: we know
                        # we did not start those.
                        pass
                    cur.execute('SELECT app.reap_orphaned_jobs(%s)', (w,))
                    row = cur.fetchone()
                    out['dead_worker'] += int(row[0]) if row else 0

                # ② the age ceiling, independent of any pid
                cur.execute(
                    "UPDATE app.job SET state = 'failed', error_code = 'INTERNAL', "
                    "       error_detail = 'exceeded the hard ceiling while in "
                    "flight; no job may outlive its own budget', "
                    '       started_at = coalesce(started_at, created_at), '
                    '       finished_at = now(), '
                    '       seconds = extract(epoch FROM now() - '
                    '                         coalesce(started_at, created_at)) '
                    " WHERE state IN ('queued','running') "
                    '   AND now() - coalesce(started_at, created_at) > %s * interval \'1 second\'',
                    (STALE_AFTER_SECONDS,))
                out['overran'] += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            return out
        except Exception as e:                                        # noqa: BLE001
            _bump('write_failed', f'{type(e).__name__}: {e}')
            return out


# The hard ceiling: MAX_MAX_SECONDS in field_server is 900 s, and a job that has
# been in flight for longer than that plus a generous margin cannot be honest —
# either the process is gone or the budget was not enforced. Deliberately NOT
# imported from field_server: this module must stay importable by a worker that
# has no HTTP layer at all.
STALE_AFTER_SECONDS = 1800


def _worker_is_alive(worker: str) -> bool:
    """Is the process named in `fields/<pid>/<version>` still running?

    A name this function cannot parse returns False — an unparseable worker is
    not evidence of a live process, and treating "I do not know" as "alive" is
    how the three-hour row survived. Unknown must fall to the side that gets
    CLEANED, because the age ceiling then bounds the damage of a wrong guess,
    whereas assuming life is unbounded.
    """
    import os
    parts = worker.split('/')
    if len(parts) < 2 or not parts[1].isdigit():
        return False
    try:
        os.kill(int(parts[1]), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # The pid exists and belongs to someone else. It is alive, but it is not
        # a fields daemon of ours — say alive, and let the age ceiling handle it.
        return True
    except Exception:                                                # noqa: BLE001
        return False


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
