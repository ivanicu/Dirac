"""Generic, method-version-aware result caching for the invocation kernel.

Field cubes keep their richer domain cache. This adapter covers every other
deterministic cacheable Method and stores only validated JSON plus references to the
content-addressed artifacts already owned by ArtifactStore.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from invocation import HandlerResult


def request_digest(payload: dict, *, execution_digest: str | None = None) -> bytes:
    canonical = json.dumps(
        {"schema_version": "2.0", "execution_digest": execution_digest,
         "payload": payload}, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).digest()


def legacy_request_digest(payload: dict) -> bytes:
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).digest()


class CompositeCache:
    """One cache contract composed from specialised repositories."""

    def __init__(self, *repositories: Any) -> None:
        self.repositories = tuple(r for r in repositories if r is not None)

    def lookup(self, method_id: str, payload: dict, *,
               execution_digest: str | None = None) -> HandlerResult | None:
        for repository in self.repositories:
            hit = repository.lookup(method_id, payload,
                                    execution_digest=execution_digest)
            if hit is not None:
                return hit
        return None

    def store(self, method_id: str, payload: dict, out: HandlerResult, **metadata) -> None:
        for repository in self.repositories:
            repository.store(method_id, payload, out, **metadata)


class PostgresResultCache:
    kind = 'postgres_result'

    def __init__(self, connect, artifact_store, *, excluded_methods=()) -> None:
        self._connect = connect
        self._artifacts = artifact_store
        self._excluded = frozenset(excluded_methods)
        self.counters = {'looked_up': 0, 'hit': 0, 'miss': 0,
                         'stored': 0, 'deduplicated': 0}

    def lookup(self, method_id: str, payload: dict, *,
               execution_digest: str | None = None) -> HandlerResult | None:
        if method_id in self._excluded:
            return None
        self.counters['looked_up'] += 1
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT c.id, c.result, c.provenance, c.warnings, c.parameters_used '
                'FROM app.v_result_cache_servable c '
                'JOIN meta.method m ON m.id = c.method_row_id '
                'WHERE m.method_id = %s AND c.request_digest = %s',
                (method_id, request_digest(payload, execution_digest=execution_digest)))
            row = cur.fetchone()
            if row is None and execution_digest is not None and '.motif.' not in method_id:
                # Compatibility window for results written before cache identity v2.
                # Motif never falls back because checkpoint/calibration/runtime identity
                # is part of correctness, not only freshness.
                cur.execute(
                    'SELECT c.id, c.result, c.provenance, c.warnings, c.parameters_used '
                    'FROM app.v_result_cache_servable c '
                    'JOIN meta.method m ON m.id = c.method_row_id '
                    'WHERE m.method_id = %s AND c.request_digest = %s',
                    (method_id, legacy_request_digest(payload)))
                row = cur.fetchone()
            if row is None:
                self.counters['miss'] += 1
                return None
            cur.execute(
                'SELECT a.id, r.role FROM app.result_cache_artifact r '
                'JOIN app.artifact a ON a.id = r.artifact_id '
                'WHERE r.result_cache_id = %s ORDER BY r.ordinal', (row[0],))
            artifact_rows = list(cur.fetchall())
        artifact_bytes: list[tuple[str, bytes]] = []
        for artifact_id, role in artifact_rows:
            _artifact, data = self._artifacts.read(str(artifact_id))
            artifact_bytes.append((role, data))
        self.counters['hit'] += 1
        return HandlerResult(
            result=dict(row[1]), artifacts=artifact_bytes,
            provenance=dict(row[2] or {}), warnings=list(row[3] or []),
            parameters_used=dict(row[4] or {}), cache='db')

    def store(self, method_id: str, payload: dict, out: HandlerResult, *,
              seconds: float, job_id: str | None = None,
              envelope: dict | None = None,
              execution_digest: str | None = None) -> None:
        if method_id in self._excluded or out.cache != 'computed' or envelope is None:
            return
        references = list(envelope.get('artifacts') or [])
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT id FROM meta.method WHERE method_id = %s '
                        'AND superseded_at IS NULL', (method_id,))
            method = cur.fetchone()
            if method is None:
                return
            cur.execute(
                'INSERT INTO app.result_cache '
                '(method_row_id, request_digest, result, provenance, warnings, '
                ' parameters_used, source_job_id, compute_seconds) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s) '
                'ON CONFLICT (method_row_id, request_digest) DO NOTHING RETURNING id',
                (method[0], request_digest(payload, execution_digest=execution_digest), json.dumps(out.result),
                 json.dumps(out.provenance), json.dumps(out.warnings),
                 json.dumps(out.parameters_used), job_id, seconds))
            inserted = cur.fetchone()
            if inserted is None:
                self.counters['deduplicated'] += 1
                return
            cache_id = inserted[0]
            for ordinal, ref in enumerate(references):
                if not ref.get('id'):
                    continue
                cur.execute(
                    'INSERT INTO app.result_cache_artifact '
                    '(result_cache_id, artifact_id, role, ordinal) VALUES (%s,%s,%s,%s)',
                    (cache_id, ref['id'], ref['role'], ordinal))
        self.counters['stored'] += 1
