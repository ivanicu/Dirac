"""Typed, deterministic retry policy for Attempt failures."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import random
from typing import Any


NON_RETRYABLE_PREFIXES = (
    "INVALID_", "PARSE", "UNSUPPORTED", "UNPARAMETERIZED", "SCIENTIFIC_",
    "POLICY_", "AUTH", "FORBIDDEN", "NOT_FOUND", "STALE_ATTEMPT_RESULT",
)


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay_seconds: float
    preserve_seed: bool
    resume_from_checkpoint: bool
    reason: str


def classify_retry(*, code: str, attempt: int, policy: dict[str, Any],
                   execution_digest: str) -> RetryDecision:
    """Return a replayable decision; jitter is seeded by execution identity."""
    if attempt >= int(policy["max_attempts"]):
        return RetryDecision(False, 0, True, False, "max_attempts_exhausted")
    if code.startswith(NON_RETRYABLE_PREFIXES):
        return RetryDecision(False, 0, True, False, "non_retryable_failure_class")
    if code not in set(policy.get("retryable_codes", ())):
        return RetryDecision(False, 0, True, False, "code_not_allowlisted")

    backoff = policy["backoff"]
    initial = float(backoff["initial_seconds"])
    kind = backoff["kind"]
    delay = 0.0 if kind == "none" else initial
    if kind == "exponential":
        delay = initial * (2 ** max(0, attempt - 1))
    delay = min(delay, float(backoff["max_seconds"]))
    jitter = float(backoff.get("jitter_fraction", 0))
    if delay and jitter:
        seed = int.from_bytes(hashlib.sha256(
            f"{execution_digest}:{attempt}:{code}".encode()).digest()[:8], "big")
        delay *= 1 + random.Random(seed).uniform(-jitter, jitter)
    return RetryDecision(
        True, max(0.0, delay), bool(policy["preserve_seed"]),
        bool(policy["resume_from_checkpoint"]), "retryable_infrastructure_failure",
    )
