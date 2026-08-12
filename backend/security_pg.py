"""PostgreSQL persistence for the transport-neutral remote security policy."""
from __future__ import annotations

from typing import Any

from security import Principal


class PostgresUsageStore:
    def __init__(self, connect) -> None:
        self._connect = connect

    def reserve(self, principal: Principal, cost_units: int) -> bool:
        """Atomically reserve today's request and cost before work can begin."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.remote_quota_usage "
                " (actor_kind, actor_id, usage_day, request_count, cost_units) "
                "VALUES (%s, %s, (now() AT TIME ZONE 'UTC')::date, 1, %s) "
                "ON CONFLICT (actor_kind, actor_id, usage_day) DO UPDATE SET "
                " request_count = app.remote_quota_usage.request_count + 1, "
                " cost_units = app.remote_quota_usage.cost_units + EXCLUDED.cost_units, "
                " updated_at = now() "
                "WHERE app.remote_quota_usage.request_count + 1 <= %s "
                "  AND app.remote_quota_usage.cost_units + EXCLUDED.cost_units <= %s "
                "RETURNING request_count, cost_units",
                (principal.actor_kind, principal.actor_id, cost_units,
                 principal.daily_requests, principal.daily_cost_units))
            return cur.fetchone() is not None


class PostgresAuditSink:
    def __init__(self, connect) -> None:
        self._connect = connect

    def write(self, *, request_id: str | None, principal: Principal | None,
              method: str, path: str, required_scopes: tuple[str, ...], status: int,
              error_code: str | None, request_bytes: int, response_bytes: int,
              cost_units: int, duration_ms: int,
              token_fingerprint: str | None = None) -> None:
        actor_kind = principal.actor_kind if principal else None
        actor_id = principal.actor_id if principal else None
        fingerprint = (principal.token_fingerprint if principal
                       else token_fingerprint)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit.remote_request "
                " (request_id, actor_kind, actor_id, token_fingerprint, http_method, "
                "  path, required_scopes, status, error_code, request_bytes, "
                "  response_bytes, cost_units, duration_ms) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (request_id, actor_kind, actor_id, fingerprint, method, path,
                 list(required_scopes), status, error_code, request_bytes,
                 response_bytes, cost_units, duration_ms))
