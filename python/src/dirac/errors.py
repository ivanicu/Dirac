"""Client-side typed refusals — the same vocabulary, raised as Python exceptions.

WHY THE SDK RAISES where the kernel returns: a Python caller writing a script wants
`try/except DiracUnsupported`, not `if not env['ok'] and env['error']['code'] == ...`.
But the mapping must be MECHANICAL and derived from contracts/errors.json, because the
moment it is hand-written it drifts, and a client catching `DiracUnsupported` silently
stops catching the refusal that was renamed.

WHAT IS NOT DONE HERE, on purpose: no re-classification. The kernel already decided
what kind of refusal this is, and a second opinion in the client would be a second
authority. This module maps a code to a class and attaches the payload. That is all.

An `error` envelope is turned into an exception ONLY at the ergonomic surface
(`DiracClient.invoke`). The raw envelope stays available via `.envelope`, because an
agent or a CLI emitting `--json` must be able to hand the whole typed object onward
without an exception having flattened it.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import pathlib
from typing import Any

# The vocabulary. Read from the canonical file when it can be found (a repo checkout),
# with a frozen fallback list for an installed SDK that has no contracts/ directory.
# The fallback is a LIST OF NAMES only — no semantics — so it cannot disagree with the
# file about whether something is retryable; that always comes from the payload.
_FALLBACK_CODES = ('PARSE', 'UNCONVERGED', 'UNPARAMETERIZED', 'BUDGET',
                   'OPEN_SHELL_SPIN_REQUIRED', 'UNSUPPORTED', 'TOO_LARGE', 'BAD_HOST',
                   'CANCELLED', 'INTERNAL', 'NOT_FOUND', 'DB_UNAVAILABLE')


def _load_codes() -> tuple[str, ...]:
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        f = parent / 'contracts' / 'errors.json'
        if f.is_file():
            try:
                return tuple(json.loads(f.read_text())['codes'])
            except Exception:                                      # noqa: BLE001
                break
    return _FALLBACK_CODES


CODES = _load_codes()


class DiracError(Exception):
    """Base for everything this SDK raises. Carries the whole typed payload.

    `.envelope` is the untouched server response. Keeping it is what lets a CLI print
    `--json` after catching, and what lets a caller inspect `hint` to build the
    invocation that would have worked.
    """

    code = 'INTERNAL'

    def __init__(self, message: str, *, envelope: dict | None = None,
                 details: dict | None = None, hint: dict | None = None,
                 retryable: bool = False, caller_action: str = '',
                 method_id: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.envelope = envelope or {}
        self.details = details or {}
        self.hint = hint
        self.retryable = retryable
        self.caller_action = caller_action
        self.method_id = method_id

    def __str__(self) -> str:
        out = f'[{self.code}] {self.message}'
        if self.caller_action:
            out += f'\n  → {self.caller_action}'
        if self.hint:
            out += f'\n  try: {json.dumps(self.hint)}'
        return out


def _make(code: str) -> type:
    """One exception class per declared code, generated rather than typed out.

    Generated because a hand-written list is a second home for the vocabulary, and the
    two homes disagree the first time a code is added. The class NAMES follow a fixed
    transformation so `except dirac.DiracUnsupported` is stable and predictable.
    """
    name = 'Dirac' + ''.join(p.capitalize() for p in code.lower().split('_'))
    return type(name, (DiracError,), {'code': code,
                                      '__doc__': f'The {code} refusal.'})


_BY_CODE: dict[str, type] = {c: _make(c) for c in CODES}
globals().update({cls.__name__: cls for cls in _BY_CODE.values()})
__all__ = ['DiracError', 'from_envelope', 'verify', 'decode_and_verify', 'CODES',
           *[cls.__name__ for cls in _BY_CODE.values()]]


def exception_for(code: str) -> type:
    """An UNKNOWN code becomes DiracError, never a guess and never silence.

    A server newer than this SDK can return a code this client has never heard of. The
    honest handling is to raise the base class with the code attached — the caller
    still gets the message, the hint and the retryable flag, and the unfamiliar code is
    visible in it. Mapping it onto the nearest known class would be the client
    inventing a classification, which is exactly the defect the typed vocabulary
    exists to remove.
    """
    return _BY_CODE.get(code, DiracError)


def from_envelope(env: dict, *, method_id: str | None) -> DiracError:
    err = (env or {}).get('error') or {}
    code = err.get('code') or 'INTERNAL'
    cls = exception_for(code)
    exc = cls(err.get('message') or 'the server returned an error with no message',
              envelope=env, details=err.get('details'), hint=err.get('hint'),
              retryable=bool(err.get('retryable')),
              caller_action=err.get('caller_action') or '', method_id=method_id)
    if cls is DiracError and code != 'INTERNAL':
        exc.code = code                      # keep the unfamiliar code visible
    return exc


# ── artifact verification, client side ─────────────────────────────────────────
class DiracDigestMismatch(DiracError):
    """The bytes are not what their address says. Deliberately its own class.

    Not folded into INTERNAL because the CALLER's next move is different and specific:
    a mismatch means the transport or the store corrupted something, retrying may
    genuinely help, and the bytes in hand must not be used. Folding it into "our
    fault" would hide the one failure that a client can actually detect on its own.
    """

    code = 'DIGEST_MISMATCH'


def verify(data: bytes, expected_sha256: str, *,
           advertised_by: str | None = None) -> None:
    """Hash what ARRIVED and compare. The client's own check, not the server's word.

    `advertised_by` is the server's header, compared as a THIRD value: if the header
    and the reference disagree, that is a different bug from the bytes disagreeing with
    both, and telling them apart in the message saves a debugging session.
    """
    actual = hashlib.sha256(data).hexdigest()
    if advertised_by and advertised_by != expected_sha256:
        raise DiracDigestMismatch(
            f'the reference says {expected_sha256[:12]}… and the response header says '
            f'{advertised_by[:12]}… — the server contradicted itself, so neither can be '
            f'trusted as the address of these {len(data)} bytes',
            details={'reference_sha256': expected_sha256,
                     'header_sha256': advertised_by, 'actual_sha256': actual})
    if actual != expected_sha256:
        raise DiracDigestMismatch(
            f'{len(data)} bytes hash to {actual[:12]}… but were served as '
            f'{expected_sha256[:12]}…',
            details={'expected_sha256': expected_sha256, 'actual_sha256': actual,
                     'bytes': len(data)},
            retryable=True)


def decode_and_verify(ref: dict) -> bytes:
    try:
        data = base64.b64decode(ref['inline_base64'], validate=True)
    except (binascii.Error, ValueError, KeyError) as e:
        raise DiracDigestMismatch(
            f'inline artifact bytes are not valid base64: {e}') from e
    verify(data, ref['sha256'])
    return data
