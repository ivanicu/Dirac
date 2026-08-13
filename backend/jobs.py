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

③ A CONFLICT ON `job_one_inflight` IS AN INSTRUCTION, and it used to be only a
   measurement. The index makes an identical (method, input, params) impossible
   to have twice in flight; for one day the conflict was merely COUNTED while
   both requests computed anyway, which was the honest intermediate state — the
   counter is what proved the duplicated work was real (it fired in production)
   rather than a story about concurrency. `wait_for` now acts on it: the second
   request waits for the first and uses its outcome. The counter stays, because
   `joined` vs `join_timeout` is how we will know whether waiting was the right
   call.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

# Counted rather than logged-and-forgotten. Same reasoning as the persist
# counters: a number nobody can read is a log line with extra steps.
_counters = {
    'opened': 0,          # rows inserted
    'done': 0,            # reached state='done'
    'failed': 0,          # reached state='failed'
    'inflight_conflict': 0,   # an identical job was ALREADY running (see ③)
    'joined': 0,          # waited for that job and used its outcome
    'join_timeout': 0,    # waited, gave up, computed it after all
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
    'UNSUPPORTED', 'TOO_LARGE', 'INVALID_PARAMETERS', 'INTERNAL', 'CANCELLED',
}

_EXPECTED_REFUSALS = {
    'PARSE', 'UNPARAMETERIZED', 'BUDGET', 'UNSUPPORTED', 'TOO_LARGE',
    'INVALID_PARAMETERS',
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


def job_outcome_class(state: str, code: str | None = None) -> str | None:
    """Mirror ``app.classify_job_outcome`` for process-local JobStores.

    The database function remains canonical for durable rows. This mirror gives an
    offline CLI the same public Job shape, and the contract test pins the two small,
    closed decision tables together.
    """
    if state == 'done':
        return 'success'
    if state == 'cancelled':
        return 'cancelled'
    if state != 'failed':
        return None
    mapped = job_error_code(code)
    if mapped == 'INTERNAL':
        return 'operational_failure'
    if mapped == 'UNCONVERGED':
        return 'scientific_failure'
    return 'expected_refusal'


def canonical_request_digest(method_row_id: str, input_sha256: bytes,
                             params: dict) -> bytes:
    material = json.dumps(
        {'method': str(method_row_id), 'input_sha256': bytes(input_sha256).hex(),
         'parameters': params}, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False)
    return hashlib.sha256(material.encode()).digest()


class JobLedger:
    """Writes job rows through a caller-supplied connection factory.

    `connect` is injected rather than imported so this module has no opinion
    about how the service connects, and so a test can hand it a factory that
    raises — which is the only way to prove property ① instead of asserting it.
    """

    kind = 'postgres'
    durability = 'durable'

    def __init__(self, connect: Callable[[], Any], worker: str,
                 method_rows: dict[str, str] | None = None) -> None:
        self._connect = connect
        self.worker = worker
        self._method_rows = dict(method_rows or {})

    def method_row_for(self, method_id: str) -> str | None:
        return self._method_rows.get(method_id)

    def bind_method_rows(self, rows: dict[str, str]) -> None:
        self._method_rows = dict(rows)

    # ── lifecycle ────────────────────────────────────────────────────────
    def open(self, *, method_row_id: str, input_sha256: bytes, params: dict,
             budget_seconds: float | None = None, est_seconds: float | None = None,
             compound_id: str | None = None,
             conformer_hash: bytes | None = None,
             queued: bool = False,
             request_digest: bytes | None = None,
             actor_kind: str = 'service', actor_id: str = 'dirac-kernel',
             command_id: str | None = None,
             request_id: str | None = None) -> tuple[str | None, bool]:
        """Insert a 'running' row. Returns (job_id, conflicted).

        THE SECOND VALUE EXISTS BECAUSE None HAD THREE MEANINGS — database
        unreachable, write failed, and "an identical job is already running" —
        and only the third one is an instruction to the caller. Collapsing them
        made the dedup signal unusable by the very code that could act on it,
        which is the same shape as a 403 that reads as an outage.

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
            request_digest = request_digest or canonical_request_digest(
                method_row_id, input_sha256, params)
            with self._connect() as conn, conn.cursor() as cur:
                # 'queued' MUST have a NULL started_at and 'running' MUST have one —
                # job_running_has_start says `(state = 'queued') = (started_at IS
                # NULL)`, so the two are written as one decision here rather than
                # as two statements that could disagree.
                state = 'queued' if queued else 'running'
                started = None if queued else 'now()'
                cur.execute(
                    "INSERT INTO app.job (method_row_id, state, input_sha256, "
                    "       request_digest, params, "
                    "       budget_seconds, est_seconds, compound_id, conformer_hash, "
                    f"       worker, actor_kind, actor_id, command_id, request_id, started_at) "
                    f"VALUES (%s, '{state}', %s, %s, %s, %s, %s, %s, %s, %s, "
                    f"        %s, %s, %s, %s, "
                    f"        {started or 'NULL'}) "
                    'RETURNING id',
                    (method_row_id, input_sha256, request_digest, json.dumps(params), budget,
                     est_seconds, compound_id, conformer_hash, self.worker,
                     actor_kind, actor_id, command_id, request_id))
                row = cur.fetchone()
            _bump('opened')
            return (str(row[0]) if row else None), False
        except Exception as e:                                       # noqa: BLE001
            # A unique-violation on job_one_inflight is the DEDUP signal, not a
            # fault. Distinguished by SQLSTATE 23505 rather than by message text
            # so a locale or a version change cannot turn a measurement into an
            # error count.
            conflict = False
            if str(getattr(e, 'sqlstate', '')) == '23505' or 'job_one_inflight' in str(e):
                _bump('inflight_conflict')
                conflict = True
            else:
                _bump('write_failed', f'{type(e).__name__}: {e}')
            if conflict:
                try:
                    with self._connect() as conn, conn.cursor() as cur:
                        cur.execute(
                            'SELECT id FROM app.job WHERE method_row_id = %s '
                            'AND request_digest = %s '
                            "AND state IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
                            (method_row_id, request_digest))
                        row = cur.fetchone()
                    return (str(row[0]) if row else None), True
                except Exception as lookup_error:                    # noqa: BLE001
                    _bump('write_failed', f'{type(lookup_error).__name__}: {lookup_error}')
            return None, conflict

    def start(self, job_id: str | None) -> None:
        """queued → running, stamping started_at.

        The transition exists because a bounded compute pool means a request can
        WAIT before it computes, and the difference between "waiting" and
        "running" is the only thing that makes v_job_live's age_seconds mean
        anything: without it, a queued job's age reads as compute time and a
        four-deep queue looks like four slow jobs.
        """
        if job_id is None:
            return
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE app.job SET state = 'running', started_at = now() "
                    "WHERE id = %s AND state = 'queued'", (job_id,))
        except Exception as e:                                        # noqa: BLE001
            _bump('write_failed', f'{type(e).__name__}: {e}')

    def done(self, job_id: str | None, *, seconds: float,
             field_cube_id: str | None = None, peak_rss_mb: int | None = None,
             result_summary: dict | None = None) -> None:
        if job_id is None:
            return
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE app.job SET state = 'done', finished_at = now(), "
                    '       seconds = %s, field_cube_id = %s, peak_rss_mb = %s, '
                    '       result_summary = %s '
                    "WHERE id = %s AND state = 'running'",
                    (round(float(seconds), 3), field_cube_id, peak_rss_mb,
                     json.dumps(result_summary) if result_summary is not None else None,
                     job_id))
            _bump('done')
        except Exception as e:                                        # noqa: BLE001
            _bump('write_failed', f'{type(e).__name__}: {e}')

    def failed(self, job_id: str | None, *, code: str, detail: str,
               seconds: float | None = None, retryable: bool | None = None) -> None:
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

    # ── public query/cancellation contract ──────────────────────────────
    def get(self, job_id: str) -> dict | None:
        rows = self._query_jobs('WHERE j.id = %s', (job_id,), limit=1)
        return rows[0] if rows else None

    def list(self, *, state: str | None = None, limit: int = 100) -> list[dict]:
        if state:
            return self._query_jobs('WHERE j.state = %s', (state,), limit=limit)
        return self._query_jobs('', (), limit=limit)

    def list_attention(self, *, limit: int = 100) -> list[dict]:
        """Read the derived intervention queue; it has no independent write model."""
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    'SELECT kind::text, object_id, reason, priority, at, '
                    'actor_kind::text, actor_id, command_id, detail '
                    'FROM app.v_attention ORDER BY at DESC LIMIT %s',
                    (max(1, min(int(limit), 500)),))
                rows = cur.fetchall()
        except Exception as e:                                      # noqa: BLE001
            _bump('write_failed', f'{type(e).__name__}: {e}')
            return []
        keys = ('kind', 'object_id', 'reason', 'priority', 'at', 'actor_kind',
                'actor_id', 'command_id', 'detail')
        return [{**{k: _json_value(v) for k, v in zip(keys, row)},
                 'ref': {'kind': str(row[0]), 'id': str(row[1])}}
                for row in rows]

    def request_cancel(self, job_id: str) -> dict | None:
        """Cancel queued work; report running work as not interruptible.

        A queued row can transition atomically; a running row remains running and its
        cancel_requested_at records intent without pretending the SCF was interrupted.
        """
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    'UPDATE app.job SET cancel_requested_at = coalesce('
                    'cancel_requested_at, now()) '
                    "WHERE id = %s AND state IN ('queued','running')", (job_id,))
                cur.execute(
                    "UPDATE app.job SET state = 'cancelled', "
                    "       started_at = coalesce(started_at, now()), finished_at = now(), "
                    "       error_code = 'CANCELLED', error_detail = 'cancelled while queued' "
                    " WHERE id = %s AND state = 'queued' RETURNING id", (job_id,))
                cancelled = cur.fetchone() is not None
            job = self.get(job_id)
            if job is not None:
                job['cancel'] = {
                    'requested': True,
                    'accepted': cancelled,
                    'capability': ('queued' if cancelled else 'not_interruptible'),
                }
            return job
        except Exception as e:                                       # noqa: BLE001
            _bump('write_failed', f'{type(e).__name__}: {e}')
            return None

    def _query_jobs(self, where: str, params: tuple, *, limit: int) -> list[dict]:
        sql = (
            'SELECT j.id, m.method_id, m.version, j.state::text, j.params, '
            'j.budget_seconds, j.est_seconds, j.seconds, j.error_code::text, '
            'j.error_detail, j.worker, j.created_at, j.started_at, j.finished_at, '
            'j.request_digest, j.durability, j.cancel_requested_at, j.result_summary, '
            'j.outcome_class::text, j.actor_kind::text, j.actor_id, j.command_id::text, '
            'j.request_id, '
            "coalesce(jsonb_agg(jsonb_build_object('id', a.id, 'role', ja.role, "
            "'sha256', encode(a.blob_sha256, 'hex'), 'media_type', a.media_type, "
            "'size_bytes', a.size_bytes)) FILTER (WHERE a.id IS NOT NULL), '[]'::jsonb) "
            'FROM app.job j JOIN meta.method m ON m.id = j.method_row_id '
            'LEFT JOIN app.job_artifact ja ON ja.job_id = j.id '
            'LEFT JOIN app.artifact a ON a.id = ja.artifact_id '
            f'{where} GROUP BY j.id, m.method_id, m.version ORDER BY j.created_at DESC '
            'LIMIT %s')
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(sql, (*params, max(1, min(int(limit), 500))))
                rows = cur.fetchall()
        except Exception as e:                                       # noqa: BLE001
            _bump('write_failed', f'{type(e).__name__}: {e}')
            return []
        keys = ('id', 'method_id', 'method_version', 'state', 'parameters',
                'budget_seconds', 'estimated_seconds', 'seconds', 'error_code',
                'error_detail', 'worker', 'created_at', 'started_at', 'finished_at',
                'request_digest', 'durability', 'cancel_requested_at', 'result_summary',
                'outcome_class', 'actor_kind', 'actor_id', 'command_id', 'request_id',
                'artifacts')
        return [{k: _json_value(v) for k, v in zip(keys, row)} for row in rows]

    # ── coordination ─────────────────────────────────────────────────────
    def wait_for(self, *, method_row_id: str, input_sha256: bytes, params: dict,
                 timeout: float, poll: float = 0.25,
                 request_digest: bytes | None = None) -> dict:
        """Wait for the identical job someone else is already running.

        THIS IS THE LEDGER BECOMING A COORDINATOR RATHER THAN AN OBSERVER, and
        it is the whole reason `job_one_inflight` exists. Until now two identical
        concurrent requests both computed: nothing queued, nothing waited, and the
        second one duplicated a six-minute SCF on 22 cores while the first was
        still running. The conflict counter had already fired in production, so
        the duplicated work was measured, not hypothesised.

        WHY POLL THE DATABASE INSTEAD OF AN IN-PROCESS EVENT. An in-process dict
        of Events would be faster and would dedup only within one process — and
        the requests that hurt most come from DIFFERENT clients (a Mac and a
        laptop on the LAN, two browser tabs) which land on different threads
        today and will land on different HOSTS at ROADMAP P2. The ledger is the
        one place both can see, so it is the only correct place to wait. Polling
        is unglamorous and survives the architecture; a shared Event does not.

        Returns {state, error_code, error_detail, waited}. `state` is 'done',
        'failed', 'cancelled' or 'timeout' — 'timeout' meaning the caller should
        compute it after all, because a waiter that gives up must degrade to
        doing the work rather than to an error the molecule did not cause.
        """
        import time
        deadline = time.time() + max(0.0, timeout)
        t0 = time.time()
        request_digest = request_digest or canonical_request_digest(
            method_row_id, input_sha256, params)
        last: dict = {}          # observations, never a verdict
        while time.time() < deadline:
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(
                        'SELECT state::text, error_code::text, error_detail '
                        '  FROM app.job '
                        ' WHERE method_row_id = %s AND request_digest = %s '
                        ' ORDER BY created_at DESC LIMIT 1',
                        (method_row_id, request_digest))
                    row = cur.fetchone()
            except Exception as e:                                   # noqa: BLE001
                _bump('write_failed', f'{type(e).__name__}: {e}')
                break
            if row is None:
                # The row vanished — the winner's transaction rolled back, or the
                # conflict was with a row that has since been reaped. Nothing to
                # wait for, so stop waiting rather than block until the deadline.
                break
            state, code, detail = row
            if state in ('done', 'failed', 'cancelled'):
                _bump('joined')
                return {'state': state, 'error_code': code, 'error_detail': detail,
                        'waited': round(time.time() - t0, 3)}
            # NOT stored as `state`: the caller branches on this value, and
            # returning the last OBSERVED state ('running') from a function
            # documented to return 'timeout' makes the caller's fall-through
            # depend on an undocumented value — it happens to work today because
            # 'running' is not terminal either, which is the kind of accident that
            # survives until someone writes `if state == 'timeout'`. Caught by
            # this module's own test.
            last = {'last_state': state, 'error_code': code, 'error_detail': detail}
            time.sleep(poll)
        _bump('join_timeout')
        return {'state': 'timeout', 'waited': round(time.time() - t0, 3),
                'error_code': None, 'error_detail': None, **last}

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


PostgresJobStore = JobLedger


class MemoryJobStore:
    """The same job contract with process-lifetime durability.

    This is a real fallback, not a fake durable handle: callers can inspect ``durability``
    and know that a restart will erase the records.
    """

    kind = 'memory'
    durability = 'process'

    def __init__(self) -> None:
        self.worker = 'memory'
        self._rows: dict[str, dict] = {}
        self._lock = threading.Lock()

    @staticmethod
    def method_row_for(method_id: str) -> str:
        return method_id

    def open(self, *, method_row_id: str, input_sha256: bytes, params: dict,
             budget_seconds: float | None = None, est_seconds: float | None = None,
             queued: bool = False, request_digest: bytes | None = None,
             actor_kind: str = 'service', actor_id: str = 'dirac-kernel',
             command_id: str | None = None, request_id: str | None = None,
             **_kw) -> tuple[str | None, bool]:
        digest = bytes(request_digest or canonical_request_digest(
            method_row_id, input_sha256, params)).hex()
        with self._lock:
            for row in self._rows.values():
                if (row['_digest'] == digest and row['method_id'] == method_row_id
                        and row['parameters'] == params
                        and row['state'] in ('queued', 'running')):
                    return row['id'], True
            jid = new_uuid()
            now = _now()
            self._rows[jid] = {
                'id': jid, 'method_id': method_row_id, 'method_version': None,
                'state': 'queued' if queued else 'running', 'parameters': dict(params),
                'budget_seconds': budget_seconds, 'estimated_seconds': est_seconds,
                'seconds': None, 'error_code': None, 'error_detail': None,
                'worker': self.worker, 'created_at': now,
                'started_at': None if queued else now, 'finished_at': None,
                'request_digest': digest, 'durability': self.durability,
                'cancel_requested_at': None, 'result_summary': None,
                'outcome_class': None, 'actor_kind': actor_kind, 'actor_id': actor_id,
                'command_id': command_id, 'request_id': request_id,
                'artifacts': [], '_digest': digest,
            }
        return jid, False

    def start(self, job_id: str | None) -> None:
        with self._lock:
            row = self._rows.get(job_id or '')
            if row and row['state'] == 'queued':
                row['state'], row['started_at'] = 'running', _now()

    def done(self, job_id: str | None, *, seconds: float,
             result_summary: dict | None = None, **_kw) -> None:
        with self._lock:
            row = self._rows.get(job_id or '')
            if row and row['state'] == 'running':
                row.update(state='done', seconds=round(float(seconds), 3),
                           finished_at=_now(), result_summary=result_summary,
                           outcome_class='success')

    def failed(self, job_id: str | None, *, code: str, detail: str,
               seconds: float | None = None, retryable: bool | None = None) -> None:
        with self._lock:
            row = self._rows.get(job_id or '')
            if row and row['state'] in ('queued', 'running'):
                mapped = job_error_code(code)
                state = 'cancelled' if mapped == 'CANCELLED' else 'failed'
                row.update(state=state,
                           started_at=row['started_at'] or _now(), finished_at=_now(),
                           seconds=seconds, error_code=mapped, error_detail=detail,
                           outcome_class=job_outcome_class(state, mapped))

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._rows.get(job_id)
            return _public_memory(row) if row else None

    def list(self, *, state: str | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = [r for r in self._rows.values() if state is None or r['state'] == state]
            rows.sort(key=lambda r: r['created_at'], reverse=True)
            return [_public_memory(r) for r in rows[:max(1, min(int(limit), 500))]]

    def list_attention(self, *, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = [r for r in self._rows.values()
                    if r.get('outcome_class') in ('scientific_failure',
                                                  'operational_failure')]
            rows.sort(key=lambda r: r.get('finished_at') or r['created_at'], reverse=True)
            return [{
                'kind': 'job', 'object_id': row['id'],
                'ref': {'kind': 'job', 'id': row['id']},
                'reason': row['outcome_class'],
                'priority': ('critical' if row['outcome_class'] == 'operational_failure'
                             else 'review'),
                'at': row.get('finished_at'), 'actor_kind': row.get('actor_kind'),
                'actor_id': row.get('actor_id'), 'command_id': row.get('command_id'),
                'detail': row.get('error_detail'),
            } for row in rows[:max(1, min(int(limit), 500))]]

    def request_cancel(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._rows.get(job_id)
            if row is None:
                return None
            accepted = row['state'] == 'queued'
            if row['state'] in ('queued', 'running'):
                row['cancel_requested_at'] = row['cancel_requested_at'] or _now()
            if accepted:
                row.update(state='cancelled', started_at=_now(), finished_at=_now(),
                           error_code='CANCELLED', error_detail='cancelled while queued',
                           outcome_class='cancelled')
            out = _public_memory(row)
            out['cancel'] = {'requested': True, 'accepted': accepted,
                             'capability': ('queued' if accepted else
                                            'not_interruptible')}
            return out

    def reap(self) -> dict[str, int]:
        return {'dead_worker': 0, 'overran': 0}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_memory(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith('_')}


def _json_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, list):
        return [_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            return float(value)
    except ImportError:                                             # pragma: no cover
        pass
    return value


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
