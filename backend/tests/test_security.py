#!/usr/bin/env python3
"""Executable red/green contract for the PR-15 remote boundary."""
from __future__ import annotations

import hashlib
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from security import (MemoryUsageStore, Principal, SecurityPolicy, SecurityRefusal,
                      redact, request_cost_units, required_scopes, safe_path,
                      safe_request_id)


TOKEN = 'dirac-unit-test-only-not-a-secret'
DIGEST = hashlib.sha256(TOKEN.encode()).hexdigest()


def principal(*, scopes=('*',), rate=10, requests=10, cost=1000, body=1024):
    return Principal('agent', 'planner-7', DIGEST[:16], frozenset(scopes),
                     rate, requests, cost, body)


def policy(p=None, **kw):
    return SecurityPolicy('remote', {DIGEST: p or principal()},
                          usage_store=MemoryUsageStore(), **kw)


def refusal(code, fn):
    try:
        fn()
    except SecurityRefusal as error:
        assert error.code == code, (error.code, error.message)
        return error
    raise AssertionError(f'expected {code}')


def headers(token=TOKEN, proto='https'):
    return {'Authorization': f'Bearer {token}', 'X-Forwarded-Proto': proto}


def test_remote_mode_is_fail_closed_without_credentials():
    try:
        SecurityPolicy('remote')
    except ValueError as error:
        assert 'hashed token' in str(error)
    else:
        raise AssertionError('remote mode started without a credential')


def test_auth_requires_https_and_a_valid_bearer_token():
    p = policy()
    refusal('TLS_REQUIRED', lambda: p.authenticate(headers(proto='http')))
    refusal('AUTH_REQUIRED', lambda: p.authenticate({'X-Forwarded-Proto': 'https'}))
    refusal('AUTH_REQUIRED', lambda: p.authenticate(headers('wrong')))
    assert p.authenticate(headers()).actor == {'kind': 'agent', 'id': 'planner-7'}


def test_authenticated_actor_cannot_be_spoofed_by_request_body():
    p = policy()
    actor = p.trusted_actor(p.authenticate(headers()),
                            {'kind': 'human', 'id': 'administrator'})
    assert actor == {'kind': 'agent', 'id': 'planner-7'}


def test_scopes_are_route_and_method_specific():
    p = policy(principal(scopes=('method:fields.qm.*:invoke', 'artifact:read')))
    who = p.authenticate(headers())
    assert p.authorize(who, 'POST', '/v2/invoke', {
        'method_id': 'fields.qm.homo', 'input': {}}, 20) == 100
    refusal('FORBIDDEN', lambda: p.authorize(
        who, 'POST', '/v2/invoke', {'method_id': 'fields.mep', 'input': {}}, 20))
    assert required_scopes('GET', '/v2/artifacts/abc') == ('artifact:read',)


def test_command_adapter_cannot_bypass_the_underlying_method_scope():
    p = policy(principal(scopes=('command:structure.field.compute:execute',)))
    who = p.authenticate(headers())
    body = {'command': 'structure.field.compute',
            'input': {'field_kind': 'homo'}}
    assert required_scopes('POST', '/v2/execute', body) == (
        'command:structure.field.compute:execute',
        'method:fields.qm.homo:invoke')
    refusal('FORBIDDEN', lambda: p.authorize(
        who, 'POST', '/v2/execute', body, 100))


def test_request_cap_rate_limit_and_daily_compute_quota_are_independent():
    refusal('TOO_LARGE', lambda: policy().authorize(
        principal(), 'POST', '/v2/invoke', {'method_id': 'fields.mep'}, 1025))

    clock = iter((100.0, 100.0, 101.0, 101.0, 102.0, 102.0))
    limited = policy(principal(rate=2), now=lambda: next(clock))
    limited.authenticate(headers())
    limited.authenticate(headers())
    refusal('RATE_LIMITED', lambda: limited.authenticate(headers()))

    quota = policy(principal(cost=100))
    who = quota.authenticate(headers())
    quota.authorize(who, 'POST', '/v2/invoke', {'method_id': 'fields.qm.homo'}, 20)
    refusal('QUOTA_EXCEEDED', lambda: quota.authorize(
        who, 'POST', '/v2/invoke', {'method_id': 'fields.mep'}, 20))


def test_cost_policy_prices_expensive_work_before_execution():
    assert request_cost_units('POST', '/v2/invoke', {
        'method_id': 'fields.qm.homo'}) == 100
    assert request_cost_units('POST', '/v2/execute', {
        'command': 'structure.field.compute',
        'input': {'field_kind': 'homo'}}) == 100
    assert request_cost_units('POST', '/v2/execute', {
        'command': 'structure.field.compute',
        'input': {'field_kind': 'mep'}}) == 2
    assert request_cost_units('GET', '/v2/meta') == 1


def test_audit_redaction_never_preserves_secrets_or_query_values():
    value = redact({'Authorization': 'Bearer secret', 'nested': {
        'token': 'secret', 'safe': 'visible'}})
    assert value == {'Authorization': '[REDACTED]', 'nested': {
        'token': '[REDACTED]', 'safe': 'visible'}}
    assert safe_path('/v2/jobs?token=secret&limit=4') == \
        '/v2/jobs?limit=[REDACTED]&token=[REDACTED]'
    assert safe_request_id('request-42:retry.1') == 'request-42:retry.1'
    assert safe_request_id('Bearer secret value').startswith('sha256:')


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        restricted = policy(principal(scopes=('system:read',)))
        who = restricted.authenticate(headers())
        error = refusal('FORBIDDEN', lambda: restricted.authorize(
            who, 'GET', '/v2/artifacts/deadbeef', None, 0))
        print(f'SELFTEST PASS — missing artifact scope was convicted as {error.code}')
        raise SystemExit(0)
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_') and callable(value)]
    failed = 0
    for test in tests:
        try:
            test()
            print(f'PASS {test.__name__}')
        except Exception as error:  # noqa: BLE001
            failed += 1
            print(f'FAIL {test.__name__}: {error}')
    print(f'{len(tests) - failed} passed, {failed} failed')
    raise SystemExit(1 if failed else 0)
