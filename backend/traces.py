"""Durable observations at the semantic-command boundary.

The dispatcher is the only place where every GUI, HTTP, CLI, SDK and MCP intent has
the same name. Recording there gives the architecture twin traffic and outcome data
keyed to its existing ``command:<id>`` nodes. The sink is injected and fail-open:
losing telemetry may lower observability, but must never change a scientific answer.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Callable


_lock = threading.Lock()
_counters: dict[str, Any] = {'recorded': 0, 'write_failed': 0, 'last_error': None}


def counters() -> dict[str, Any]:
    with _lock:
        return dict(_counters)


def _bump(key: str, error: str | None = None) -> None:
    with _lock:
        _counters[key] += 1
        if error is not None:
            _counters['last_error'] = error


def dispatch_outcome(envelope: dict) -> str:
    if envelope.get('ok'):
        return 'success'
    code = str((envelope.get('error') or {}).get('code') or 'INTERNAL')
    if code == 'INTERNAL':
        return 'operational_failure'
    if code == 'UNCONVERGED':
        return 'scientific_failure'
    if code == 'CANCELLED':
        return 'cancelled'
    return 'expected_refusal'


def observation(command_id: str, command_version: int, actor: dict[str, str],
                request_id: str | None, started_at: float, finished_at: float,
                envelope: dict) -> dict[str, Any]:
    meta = dict(envelope.get('meta') or {})
    error = envelope.get('error') or {}
    return {
        'command_id': command_id,
        'command_version': int(command_version),
        'actor_kind': actor['kind'],
        'actor_id': actor['id'],
        'request_id': request_id,
        'method_id': meta.get('method_id'),
        'method_version': meta.get('version'),
        'job_id': meta.get('job_id') or (((envelope.get('data') or {}).get('job') or {}).get('id')),
        'dispatch_outcome': dispatch_outcome(envelope),
        'cache_source': meta.get('cache'),
        'error_code': None if envelope.get('ok') else str(error.get('code') or 'INTERNAL'),
        'duration_seconds': max(0.0, finished_at - started_at),
        'started_at': datetime.fromtimestamp(started_at, timezone.utc),
        'finished_at': datetime.fromtimestamp(finished_at, timezone.utc),
        'meta': meta,
    }


class PostgresCommandTraceStore:
    kind = 'postgres'
    durability = 'durable'

    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    def record(self, *, command_id: str, command_version: int,
               actor: dict[str, str], request_id: str | None,
               started_at: float, finished_at: float, envelope: dict) -> bool:
        row = observation(command_id, command_version, actor, request_id,
                          started_at, finished_at, envelope)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    'INSERT INTO app.command_trace '
                    '(command_id, command_version, actor_kind, actor_id, request_id, '
                    ' method_id, method_version, job_id, dispatch_outcome, cache_source, '
                    ' error_code, duration_seconds, started_at, finished_at, meta) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                    (row['command_id'], row['command_version'], row['actor_kind'],
                     row['actor_id'], row['request_id'], row['method_id'],
                     row['method_version'], row['job_id'], row['dispatch_outcome'],
                     row['cache_source'], row['error_code'], row['duration_seconds'],
                     row['started_at'], row['finished_at'], json.dumps(row['meta'])))
            _bump('recorded')
            return True
        except Exception as exc:                                   # noqa: BLE001
            _bump('write_failed', f'{type(exc).__name__}: {exc}')
            return False


class MemoryCommandTraceStore:
    kind = 'memory'
    durability = 'process'

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, *, command_id: str, command_version: int,
               actor: dict[str, str], request_id: str | None,
               started_at: float, finished_at: float, envelope: dict) -> bool:
        row = observation(command_id, command_version, actor, request_id,
                          started_at, finished_at, envelope)
        with self._lock:
            self.rows.append(dict(row))
        _bump('recorded')
        return True
