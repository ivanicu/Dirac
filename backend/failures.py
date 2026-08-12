"""Typed scientific refusals. One class per thing the caller can DO about it.

THE DEFECT THIS REPLACES, which is in the code right now: 24 `raise ValueError(...)`
inside the science functions, and an HTTP handler that GUESSES what they meant —

    reason = 'unsupported' if isinstance(e, ValueError) else 'internal'

That line decides, for every refusal in the system, whether a chemist sees "your
molecule is outside what this method can do" or "we broke". It decides it from the
Python exception type, which carries no such information: `ValueError` is what
RDKit raises for an unparseable SMILES, what the basis check raises for iodine
under 6-31g, and what a genuine bug raises when it multiplies the wrong things.
Three different facts, one type, and a route left holding the classification.

WHAT A REFUSAL HAS TO CARRY, and none of it survives str(exception):
  code           — from contracts/errors.json, so a client can BRANCH
  message        — for a person, with the measurement in it
  details        — machine-readable specifics (which elements, which basis)
  hint           — the invocation that would work instead
  retryable      — whether the same request can ever succeed

DEPENDENCY DIRECTION IS THE POINT (ADR-001, gate 11): this module imports the error
vocabulary and NOTHING else. No HTTP, no database, no RDKit, no pyscf. So the
science functions can raise it, a CLI can catch it, an SDK can re-raise it as its own
exception, and an MCP adapter can serialise it — without any of them importing the
others. It is also deliberately import-light so that a test of refusal semantics does
not need a chemistry toolkit; today 3 of 10 test suites can run without one, and that
ratio is the extraction's progress meter.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

_ERRORS_JSON = pathlib.Path(__file__).resolve().parent.parent / 'contracts' / 'errors.json'
_CATALOG: dict[str, dict] = json.loads(_ERRORS_JSON.read_text(encoding='utf-8'))['codes']


class DiracFailure(Exception):
    """A refusal the system MEANT. Never used for a bug — that is DiracInternal.

    Carries everything a caller needs to act, and validates its own code against the
    vocabulary at construction: an unregistered code cannot be raised, so a new kind
    of refusal has to be declared in contracts/errors.json before it can exist. That
    is the opposite of the current situation, where any string could become a
    refusal and the frontend could only render it as grey text.
    """

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None,
                 hint: dict[str, Any] | None = None,
                 user_message: str | None = None) -> None:
        if code not in _CATALOG:
            raise KeyError(
                f'{code!r} is not in contracts/errors.json (have: '
                f'{sorted(_CATALOG)}). A refusal that is not in the vocabulary '
                f'cannot be branched on by any client, so raising it would be a '
                f'free-text error wearing a code\'s clothes.')
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.hint = hint
        entry = _CATALOG[code]
        self.retryable: bool = bool(entry.get('retryable'))
        self.http_status: int = int(entry.get('http', 200))
        self.caller_action: str = entry.get('caller_action', '')
        self.user_message: str = user_message or entry.get('user_copy', message)
        self.points_at: str | None = entry.get('points_at')

    def to_error_payload(self) -> dict:
        """The `error` object of a v2 envelope. No transport, no HTTP status here —
        the status belongs to the adapter, and this object is the same whether it
        travels over HTTP, a CLI's stdout or an MCP tool result."""
        payload: dict[str, Any] = {
            'code': self.code,
            'message': self.message,
            'user_message': self.user_message,
            'retryable': self.retryable,
            'caller_action': self.caller_action,
        }
        if self.details:
            payload['details'] = self.details
        if self.hint is not None:
            payload['hint'] = self.hint
        elif self.points_at:
            payload['hint'] = {'method': self.points_at}
        return payload

    def __repr__(self) -> str:                                       # pragma: no cover
        return f'{type(self).__name__}({self.code}, {self.message!r})'


class DiracParseFailure(DiracFailure):
    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__('PARSE', message, **kw)


class DiracUnsupported(DiracFailure):
    """The request is well-formed and outside what this method can do.

    The distinction from INTERNAL is the whole reason this module exists: this one
    is a fact about the MOLECULE or the SETTINGS, and the caller can act on it.
    """

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__('UNSUPPORTED', message, **kw)


class DiracUnparameterized(DiracFailure):
    """An empirical method has no parameters for these atoms. Different from
    UNSUPPORTED because the alternative is usually a DIFFERENT METHOD, not different
    settings — Gasteiger cannot type hypervalent phosphorus and the quantum route
    does not need to."""

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__('UNPARAMETERIZED', message, **kw)


class DiracBudgetExceeded(DiracFailure):
    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__('BUDGET', message, **kw)


class DiracUnconverged(DiracFailure):
    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__('UNCONVERGED', message, **kw)


class DiracOpenShellSpinRequired(DiracFailure):
    """A question, not a dead end. The previous vocabulary collapsed this into
    UNSUPPORTED, which reads as 'give up' when the fix is one parameter."""

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__('OPEN_SHELL_SPIN_REQUIRED', message, **kw)


class DiracTooLarge(DiracFailure):
    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__('TOO_LARGE', message, **kw)


class DiracNotFound(DiracFailure):
    """The address is well-formed and nothing is there.

    Kept distinct from UNSUPPORTED because the caller's next move is completely
    different: UNSUPPORTED means change the request, NOT_FOUND means the thing you
    are holding a reference to is gone (or was never here) and re-sending the same
    reference will never work. An artifact store that collapsed the two would tell a
    CLI to retry a digest that does not exist.
    """

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__('NOT_FOUND', message, **kw)


class DiracInternal(DiracFailure):
    """Nothing about the request is known to be wrong. OUR fault.

    Constructed from an exception rather than a message so the original type and
    text survive into `details` — a caller cannot act on it, but whoever reads the
    ledger can.
    """

    def __init__(self, exc: BaseException | str) -> None:
        if isinstance(exc, BaseException):
            super().__init__('INTERNAL', f'{type(exc).__name__}: {exc}',
                             details={'exception': type(exc).__name__})
        else:
            super().__init__('INTERNAL', str(exc))


def from_exception(exc: BaseException) -> DiracFailure:
    """Adapt an untyped exception, and be honest that it is a GUESS.

    This function is the bridge that lets the typed path land incrementally, and it
    is deliberately unflattering: a bare `ValueError` from a science function becomes
    UNSUPPORTED with `guessed_from_type: true` in its details, because that is what
    the route has been doing invisibly all along. The flag is what makes the
    remaining untyped refusals countable — see scripts/check_layering.py, which
    ratchets the number of `raise ValueError` sites down.
    """
    if isinstance(exc, DiracFailure):
        return exc
    if isinstance(exc, ValueError):
        return DiracUnsupported(str(exc),
                                details={'guessed_from_type': True,
                                         'exception': type(exc).__name__})
    return DiracInternal(exc)


def codes() -> list[str]:
    return sorted(_CATALOG)
