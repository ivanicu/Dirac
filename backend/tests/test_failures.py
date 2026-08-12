#!/usr/bin/env python3
"""A refusal must carry what the caller can DO, and an unregistered code must be
impossible to raise.

The line these tests exist to delete, live in field_server.py today:

    reason = 'unsupported' if isinstance(e, ValueError) else 'internal'

That decides, for every refusal in the system, whether a chemist reads "your molecule
is outside what this method can do" or "we broke" — from the Python exception type,
which does not know. RDKit raises ValueError for an unparseable SMILES; the basis
check raises it for iodine under 6-31g; a genuine bug raises it when the wrong things
get multiplied. One type, three facts, and the route holding the classification.

Import-light on purpose: no RDKit, no database, no HTTP. Refusal semantics are
testable on a bare interpreter, which is also why this suite raises the portability
ratchet (scripts/test_portability.py) rather than leaving it flat.

Run: python3 backend/tests/test_failures.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import failures as F                                                 # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f'PASS    {name}')
    except AssertionError as e:
        FAIL.append(name)
        print(f'FAIL    {name}\n        {e}')


def test_an_unregistered_code_cannot_be_raised():
    """The vocabulary is the gate. A refusal that is not in contracts/errors.json
    cannot be branched on by any client, so allowing it would be free text wearing a
    code's clothes."""
    try:
        F.DiracFailure('NOT_A_REAL_CODE', 'nope')
    except KeyError as e:
        assert 'errors.json' in str(e), (
            f'the refusal does not say WHERE codes are declared: {e}')
        return
    raise AssertionError('an unregistered code was accepted — the vocabulary is not '
                         'enforced at construction, so the next new refusal will be '
                         'invented at a call site')


def test_every_declared_code_is_constructible():
    """The other direction: no code in the vocabulary may be unusable. A code the
    catalog declares and the code path cannot express is a promise to clients that
    nothing keeps."""
    for code in F.codes():
        f = F.DiracFailure(code, f'test message for {code}')
        assert f.code == code
        assert f.user_message, f'{code} has no user-facing copy'
        assert f.caller_action, f'{code} does not say what the caller should do'


def test_the_payload_carries_action_not_just_text():
    f = F.DiracUnsupported(
        'basis 6-31g does not cover iodine',
        details={'basis': '6-31g', 'unsupported_elements': ['I'],
                 'supported_alternatives': ['def2-svp']},
        hint={'parameters': {'basis': 'def2-svp'}})
    p = f.to_error_payload()
    for key in ('code', 'message', 'user_message', 'retryable', 'caller_action'):
        assert key in p, f'the error payload has no {key}'
    assert p['details']['unsupported_elements'] == ['I'], (
        'the machine-readable specifics did not survive into the payload; a client '
        'that has to regex the message is a client that will get it wrong')
    assert p['hint'] == {'parameters': {'basis': 'def2-svp'}}, (
        'the hint did not survive — a refusal that names no way forward is a dead end')
    assert p['retryable'] is False, (
        'UNSUPPORTED came back retryable; a client would retry the same bytes forever')


def test_the_payload_carries_no_transport():
    """It must be identical over HTTP, a CLI's stdout and an MCP tool result."""
    p = F.DiracBudgetExceeded('too slow').to_error_payload()
    for forbidden in ('http', 'status', 'http_status', 'headers'):
        assert forbidden not in p, (
            f'the error payload carries {forbidden!r} — the status belongs to the '
            f'adapter, and a payload that names one is a payload with a transport '
            f'baked in')


def test_retryable_matches_the_vocabulary_not_the_class_name():
    """BUDGET and UNCONVERGED are retryable; PARSE and TOO_LARGE are not. The values
    come from errors.json, so this test fails if the two ever disagree."""
    assert F.DiracBudgetExceeded('x').retryable is True
    assert F.DiracUnconverged('x').retryable is True
    assert F.DiracParseFailure('x').retryable is False
    assert F.DiracTooLarge('x').retryable is False


def test_open_shell_is_a_question_not_a_dead_end():
    f = F.DiracOpenShellSpinRequired('this metal centre needs an explicit spin')
    assert f.retryable is True, (
        'OPEN_SHELL_SPIN_REQUIRED is not retryable, so a client would treat "send me '
        'one parameter" as "give up" — which is exactly what collapsing it into '
        'UNSUPPORTED used to do')


def test_adapting_an_untyped_exception_ADMITS_that_it_guessed():
    """The bridge that lets the typed path land incrementally must not launder a
    guess into a fact."""
    f = F.from_exception(ValueError('some science refusal'))
    assert f.code == 'UNSUPPORTED'
    assert f.details.get('guessed_from_type') is True, (
        'a guess from the exception TYPE was recorded as if it were known; that is '
        'the invisible classification this module exists to make visible')
    g = F.from_exception(ZeroDivisionError('bug'))
    assert g.code == 'INTERNAL', (
        f'a genuine bug was classified {g.code} — reporting our fault as the '
        f"molecule's fault is the one direction that must never happen")
    h = F.from_exception(F.DiracTooLarge('already typed'))
    assert h.code == 'TOO_LARGE' and 'guessed_from_type' not in h.details, (
        'an already-typed failure was re-guessed')


def test_it_imports_nothing_heavy():
    """The dependency direction, asserted rather than trusted (ADR-001)."""
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / 'failures.py'
    tree = ast.parse(src.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split('.')[0])
    forbidden = names & {'rdkit', 'pyscf', 'numpy', 'psycopg', 'http', 'urllib',
                         'field_server', 'envelope'}
    assert not forbidden, (
        f'failures.py imports {sorted(forbidden)} — a module the CLI, the SDK and an '
        f'MCP adapter all need cannot depend on the science stack or the transport')


for name, fn in list(globals().items()):
    if name.startswith('test_') and callable(fn):
        check(name, fn)

print('─' * 100)
print(f'{len(PASS)} passed · {len(FAIL)} failed')
sys.exit(1 if FAIL else 0)
