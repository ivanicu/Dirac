"""Remote boundary policy for the Dirac HTTP adapter.

The scientific kernel deliberately knows nothing about credentials.  This module
turns an HTTP request into an authenticated Principal, applies transport policy,
and returns the actor identity that the command/invocation layers may trust.

Local mode preserves the single-user workstation workflow.  Remote mode is
fail-closed: credentials are stored as SHA-256 digests, TLS termination must be
observable, every route has an explicit scope, and quota accounting is delegated
to a durable store supplied by the adapter.
"""
from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import fnmatch
import hashlib
import hmac
import json
import os
import pathlib
import re
import threading
import time
import urllib.parse
from typing import Any, Callable, Mapping, Protocol


class SecurityRefusal(Exception):
    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code, self.status, self.message = code, status, message


@dataclasses.dataclass(frozen=True)
class Principal:
    actor_kind: str
    actor_id: str
    token_fingerprint: str
    scopes: frozenset[str]
    rate_per_minute: int
    daily_requests: int
    daily_cost_units: int
    max_body_bytes: int

    @property
    def actor(self) -> dict[str, str]:
        return {'kind': self.actor_kind, 'id': self.actor_id}

    def permits(self, required: str) -> bool:
        return any(pattern == '*' or fnmatch.fnmatchcase(required, pattern)
                   for pattern in self.scopes)


class UsageStore(Protocol):
    def reserve(self, principal: Principal, cost_units: int) -> bool: ...


class MemoryUsageStore:
    """Test/local fallback. Remote production supplies the PostgreSQL store."""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: dict[tuple[str, str, str], list[int]] = {}

    def reserve(self, principal: Principal, cost_units: int) -> bool:
        day = dt.datetime.now(dt.timezone.utc).date().isoformat()
        key = (principal.actor_kind, principal.actor_id, day)
        with self._lock:
            requests, cost = self._usage.get(key, [0, 0])
            if requests + 1 > principal.daily_requests or \
                    cost + cost_units > principal.daily_cost_units:
                return False
            self._usage[key] = [requests + 1, cost + cost_units]
        return True


def redact(value: Any) -> Any:
    """Remove credentials recursively before a value can enter logs or audit."""
    secret_keys = {'authorization', 'token', 'api_key', 'apikey', 'password',
                   'secret', 'cookie', 'set-cookie'}
    if isinstance(value, Mapping):
        return {str(k): ('[REDACTED]' if str(k).lower() in secret_keys else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v) for v in value)
    return value


def safe_path(path: str) -> str:
    """Keep routing evidence, discard query-string secrets and values."""
    split = urllib.parse.urlsplit(path)
    if not split.query:
        return split.path
    names = sorted({k for k, _ in urllib.parse.parse_qsl(
        split.query, keep_blank_values=True)})
    return split.path + ('?' + '&'.join(f'{k}=[REDACTED]' for k in names)
                         if names else '')


def safe_request_id(value: Any) -> str | None:
    """Keep normal correlation IDs; fingerprint arbitrary/secret-like values."""
    if value is None:
        return None
    text = str(value)
    if re.fullmatch(r'[A-Za-z0-9._:-]{1,160}', text):
        return text
    return 'sha256:' + hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]


class SecurityPolicy:
    MODES = frozenset({'local', 'remote'})

    def __init__(self, mode: str = 'local', principals: Mapping[str, Principal] | None = None,
                 *, usage_store: UsageStore | None = None,
                 now: Callable[[], float] = time.time,
                 cost_resolver: Callable[[str, str, Mapping[str, Any] | None], int] | None = None,
                 require_forwarded_https: bool = True) -> None:
        if mode not in self.MODES:
            raise ValueError(f'DIRAC_SECURITY_MODE must be local|remote, got {mode!r}')
        self.mode = mode
        self._principals = dict(principals or {})
        if mode == 'remote' and not self._principals:
            raise ValueError('remote mode requires at least one hashed token')
        self.usage_store = usage_store or MemoryUsageStore()
        self._now = now
        self.cost_resolver = cost_resolver or request_cost_units
        self.require_forwarded_https = require_forwarded_https
        self._rate_lock = threading.Lock()
        self._recent: dict[str, collections.deque[float]] = {}

    @property
    def remote(self) -> bool:
        return self.mode == 'remote'

    @classmethod
    def from_env(cls, *, usage_store: UsageStore | None = None,
                 cost_resolver: Callable[[str, str, Mapping[str, Any] | None], int] | None = None) -> 'SecurityPolicy':
        mode = os.environ.get('DIRAC_SECURITY_MODE', 'local').strip().lower()
        if mode == 'local':
            return cls('local', usage_store=usage_store, cost_resolver=cost_resolver)
        token_file = os.environ.get('DIRAC_TOKEN_FILE', '').strip()
        if not token_file:
            raise ValueError('DIRAC_TOKEN_FILE is required in remote mode')
        raw = json.loads(pathlib.Path(token_file).read_text(encoding='utf-8'))
        principals: dict[str, Principal] = {}
        for item in raw.get('tokens', []):
            digest = str(item.get('token_sha256', '')).lower()
            if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('each remote token requires a 64-hex token_sha256')
            actor = item.get('actor') or {}
            kind, identifier = actor.get('kind'), str(actor.get('id', '')).strip()
            if kind not in ('human', 'agent', 'service') or not identifier:
                raise ValueError('each remote token requires a valid actor kind and id')
            scopes = frozenset(str(v) for v in item.get('scopes', []))
            if not scopes:
                raise ValueError(f'remote actor {identifier!r} has no scopes')
            principals[digest] = Principal(
                kind, identifier, digest[:16], scopes,
                _positive(item, 'rate_per_minute', 120),
                _positive(item, 'daily_requests', 10_000),
                _positive(item, 'daily_cost_units', 10_000),
                _positive(item, 'max_body_bytes', 8 * 1024 * 1024))
        require_https = os.environ.get('DIRAC_REQUIRE_FORWARDED_HTTPS', '1') != '0'
        return cls(mode, principals, usage_store=usage_store,
                   cost_resolver=cost_resolver,
                   require_forwarded_https=require_https)

    def authenticate(self, headers: Mapping[str, str]) -> Principal:
        if not self.remote:
            return Principal('human', 'local', 'local', frozenset({'*'}),
                             1_000_000, 1_000_000, 1_000_000_000,
                             8 * 1024 * 1024)
        if self.require_forwarded_https and \
                headers.get('X-Forwarded-Proto', '').lower() != 'https':
            raise SecurityRefusal(
                'TLS_REQUIRED', 426,
                'remote access must arrive through the configured HTTPS reverse proxy')
        value = headers.get('Authorization', '')
        scheme, _, token = value.partition(' ')
        if scheme.lower() != 'bearer' or not token:
            raise SecurityRefusal('AUTH_REQUIRED', 401, 'a Bearer token is required')
        digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
        principal = next((p for known, p in self._principals.items()
                          if hmac.compare_digest(digest, known)), None)
        if principal is None:
            raise SecurityRefusal('AUTH_REQUIRED', 401, 'the Bearer token is not valid')
        self._apply_rate_limit(principal)
        return principal

    def authorize(self, principal: Principal, method: str, path: str,
                  body: Mapping[str, Any] | None, body_bytes: int = 0) -> int:
        if body_bytes > principal.max_body_bytes:
            raise SecurityRefusal(
                'TOO_LARGE', 413,
                f'request body is {body_bytes} bytes, over this actor\'s '
                f'{principal.max_body_bytes} byte limit')
        required = required_scopes(method, path, body)
        missing = [scope for scope in required if not principal.permits(scope)]
        if missing:
            raise SecurityRefusal(
                'FORBIDDEN', 403,
                f'actor {principal.actor_id!r} lacks required scope {missing[0]!r}')
        cost = self.cost_resolver(method, path, body)
        if self.remote:
            try:
                reserved = self.usage_store.reserve(principal, cost)
            except Exception as error:  # fail closed when durable accounting is down
                raise SecurityRefusal(
                    'DB_UNAVAILABLE', 503,
                    'remote quota accounting is unavailable; refusing unmetered work') \
                    from error
            if not reserved:
                raise SecurityRefusal(
                    'QUOTA_EXCEEDED', 429,
                    'the actor daily request or compute-cost quota is exhausted')
        return cost

    def trusted_actor(self, principal: Principal,
                      claimed: Mapping[str, str] | None = None) -> dict[str, str]:
        if self.remote:
            return principal.actor
        if claimed and claimed.get('kind') in ('human', 'agent', 'service') \
                and claimed.get('id'):
            return {'kind': str(claimed['kind']), 'id': str(claimed['id'])}
        return principal.actor

    def _apply_rate_limit(self, principal: Principal) -> None:
        now = self._now()
        floor = now - 60.0
        with self._rate_lock:
            queue = self._recent.setdefault(principal.token_fingerprint,
                                            collections.deque())
            while queue and queue[0] <= floor:
                queue.popleft()
            if len(queue) >= principal.rate_per_minute:
                raise SecurityRefusal(
                    'RATE_LIMITED', 429,
                    'too many requests for this actor in the last minute')
            queue.append(now)


def required_scopes(method: str, path: str,
                    body: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    path = urllib.parse.urlsplit(path).path
    body = body or {}
    if method == 'OPTIONS':
        return ()
    if path.startswith('/v2/artifacts/'):
        return ('artifact:read',)
    if path.startswith('/admin/'):
        return ('admin:read',)
    if path in ('/health', '/v2/meta'):
        return ('system:read',)
    if path == '/v2/commands' or path.startswith('/v2/commands/'):
        return ('command:discover',)
    if path == '/v2/methods' or (path.startswith('/v2/methods/')
                                 and not path.endswith('/estimate')):
        return ('method:discover',)
    if path.endswith('/estimate') and path.startswith('/v2/methods/'):
        mid = urllib.parse.unquote(path[len('/v2/methods/'):-len('/estimate')]).strip('/')
        return ('method:estimate', f'method:{mid}:read')
    if path == '/v2/invoke':
        mid = str(body.get('method_id', '<missing>'))
        return (f'method:{mid}:invoke',)
    if path == '/v2/execute':
        command = str(body.get('command', '<missing>'))
        scopes = [f'command:{command}:execute']
        mid = request_method_id(method, path, body)
        if mid:
            scopes.append(f'method:{mid}:invoke')
        return tuple(scopes)
    if path == '/v2/jobs' and method == 'POST':
        mid = str(body.get('method_id', '<missing>'))
        return ('job:submit', f'method:{mid}:invoke')
    if path == '/v2/jobs' or (path.startswith('/v2/jobs/') and method in ('GET', 'HEAD')):
        return ('job:read',)
    if path.startswith('/v2/jobs/') and path.endswith('/cancel'):
        return ('job:cancel',)
    if path in ('/field', '/field/region', '/embed'):
        return ('legacy:invoke',)
    return ('route:unknown',)


def request_cost_units(method: str, path: str,
                       body: Mapping[str, Any] | None = None) -> int:
    mid = request_method_id(method, path, body)
    if mid and (mid.startswith('fields.qm.') or mid.startswith('surface.')
                or mid == 'torsion.strain'):
        return 100
    if mid == 'molecule.embed':
        return 5
    if mid:
        return 2
    return 1


def request_method_id(method: str, path: str,
                      body: Mapping[str, Any] | None = None) -> str | None:
    body = body or {}
    path = urllib.parse.urlsplit(path).path
    mid = str(body.get('method_id', ''))
    if path == '/v2/execute' and body.get('command') == 'structure.field.compute':
        kind = str((body.get('input') or {}).get('field_kind', ''))
        mid = f'fields.{kind}' if kind in ('mep', 'mlp') else f'fields.qm.{kind}'
    if path == '/v2/execute' and body.get('command') in (
            'structure.surface.compute', 'structure.torsion.analyze'):
        mid = 'surface.mep' if body['command'] == 'structure.surface.compute' \
            else 'torsion.strain'
    if path == '/v2/execute' and body.get('command') == 'conformer.generate':
        mid = 'molecule.embed'
    return mid or None


def _positive(item: Mapping[str, Any], key: str, default: int) -> int:
    value = int(item.get(key, default))
    if value <= 0:
        raise ValueError(f'{key} must be positive')
    return value
