"""One fail-closed Draft 2020-12 validator for Commands and Methods.

Contract validation is part of the control boundary, not an optional developer
extra.  Importing this module therefore fails if ``jsonschema`` is unavailable;
starting a service that silently accepts unchecked scientific payloads is unsafe.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import UUID

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import SchemaError, ValidationError
except ImportError as exc:  # pragma: no cover - exercised in an isolated process
    raise RuntimeError(
        "jsonschema is required to enforce Dirac Command and Method contracts; "
        "refusing to start without it"
    ) from exc


FORMAT_CHECKER = FormatChecker()


@FORMAT_CHECKER.checks("uuid", raises=(ValueError, AttributeError))
def _is_uuid(value: object) -> bool:
    return isinstance(value, str) and str(UUID(value)) == value.lower()


@FORMAT_CHECKER.checks("date-time", raises=(ValueError, AttributeError))
def _is_rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return False
    # Scientific provenance needs an absolute instant.  Naive ISO timestamps are
    # syntactically tempting but cannot be ordered across sites.
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


@FORMAT_CHECKER.checks("uri")
def _is_absolute_uri(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return bool(parsed.scheme and (parsed.netloc or parsed.path))


@FORMAT_CHECKER.checks("sha256-digest")
def _is_sha256_digest(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value[7:]
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


@dataclass(frozen=True)
class ContractViolation:
    pointer: str
    message: str
    validator: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "pointer": self.pointer,
            "message": self.message,
            "validator": self.validator,
        }


def check_schema(schema: dict[str, Any]) -> None:
    """Refuse malformed schemas when registries load, before any request runs."""
    Draft202012Validator.check_schema(schema)


def validator_for(schema: dict[str, Any]) -> Draft202012Validator:
    check_schema(schema)
    return Draft202012Validator(schema, format_checker=FORMAT_CHECKER)


def violations(schema: dict[str, Any], value: Any) -> list[ContractViolation]:
    errors: Iterable[ValidationError] = validator_for(schema).iter_errors(value)
    ordered = sorted(errors, key=lambda error: list(error.absolute_path))
    return [
        ContractViolation(
            pointer="/" + "/".join(str(part) for part in error.absolute_path),
            message=error.message,
            validator=str(error.validator) if error.validator is not None else None,
        )
        for error in ordered
    ]


__all__ = [
    "ContractViolation",
    "FORMAT_CHECKER",
    "SchemaError",
    "check_schema",
    "validator_for",
    "violations",
]
