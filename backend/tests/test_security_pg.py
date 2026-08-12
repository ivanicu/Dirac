#!/usr/bin/env python3
"""Transactional PostgreSQL proof for PR-15 quota and audit persistence."""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from security import Principal
from security_pg import PostgresAuditSink, PostgresUsageStore


class NoCommitConnection:
    """Let stores share one test transaction without changing production code."""
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *_args):
        return False


def main() -> int:
    try:
        import psycopg
        conn = psycopg.connect('dbname=dirac')
    except Exception as error:  # DB is optional in a source-only CI job
        print(f'SKIP PostgreSQL security proof: {type(error).__name__}')
        return 2
    proxy = NoCommitConnection(conn)
    connect = lambda: proxy
    actor = Principal(
        'agent', 'security-pg-transaction-proof', '0123456789abcdef',
        frozenset({'*'}), 10, 2, 200, 4096)
    try:
        usage = PostgresUsageStore(connect)
        assert usage.reserve(actor, 100) is True
        assert usage.reserve(actor, 100) is True
        assert usage.reserve(actor, 1) is False
        with conn.cursor() as cur:
            cur.execute(
                "SELECT request_count,cost_units FROM app.remote_quota_usage "
                "WHERE actor_kind='agent' AND actor_id=%s AND "
                "usage_day=(now() AT TIME ZONE 'UTC')::date",
                (actor.actor_id,))
            assert cur.fetchone() == (2, 200)

        PostgresAuditSink(connect).write(
            request_id='security-pg-proof', principal=actor, method='POST',
            path='/v2/invoke?token=[REDACTED]',
            required_scopes=('method:fields.qm.homo:invoke',), status=429,
            error_code='QUOTA_EXCEEDED', request_bytes=128, response_bytes=256,
            cost_units=0, duration_ms=3)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT token_fingerprint,path,status,error_code "
                "FROM audit.remote_request WHERE request_id='security-pg-proof' "
                "ORDER BY id DESC LIMIT 1")
            assert cur.fetchone() == (
                '0123456789abcdef', '/v2/invoke?token=[REDACTED]',
                429, 'QUOTA_EXCEEDED')
        print('PASS PostgreSQL quota is atomic and audit evidence is redacted')
        return 0
    finally:
        conn.rollback()
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
