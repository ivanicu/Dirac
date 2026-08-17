"""Canonical validation for the executable RBFE Campaign binding.

The binding deliberately contains only the scientific execution clock.  The
editable Campaign revision clock (``version`` / ``state_digest``) is never a
valid member of this object.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any
from uuid import UUID


SCHEMA_VERSION = "rbfe-campaign-binding.v2"
VERDICT = "CONFIRMED"
BINDING_KEYS = frozenset({
    "schema_version",
    "campaign_id",
    "campaign_scientific_generation",
    "campaign_scientific_digest",
    "prepared_system_id",
    "network_digest",
    "verdict",
    "digest",
})
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _uuid(value: Any, field: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = UUID(text)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a UUID") from error
    if str(parsed) != text:
        raise ValueError(f"{field} must be a canonical UUID")
    return text


def _sha256(value: Any, field: str) -> str:
    text = str(value or "")
    if _SHA256.fullmatch(text) is None:
        raise ValueError(f"{field} must be a complete sha256 digest")
    return text


def validate_campaign_binding(value: Any) -> dict[str, Any]:
    """Return one exact, digest-verified v2 binding or raise ``ValueError``."""
    if not isinstance(value, dict):
        raise ValueError("campaign binding must be a JSON object")
    keys = set(value)
    missing = sorted(BINDING_KEYS - keys)
    extra = sorted(keys - BINDING_KEYS)
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing {missing}")
        if extra:
            parts.append(f"unexpected {extra}")
        raise ValueError("campaign binding has a non-v2 shape; " + "; ".join(parts))
    binding = dict(value)
    if binding["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"campaign binding schema must be {SCHEMA_VERSION}")
    if binding["verdict"] != VERDICT:
        raise ValueError("campaign binding verdict must be CONFIRMED")
    binding["campaign_id"] = _uuid(binding["campaign_id"], "campaign_id")
    binding["prepared_system_id"] = _uuid(
        binding["prepared_system_id"], "prepared_system_id")
    generation = binding["campaign_scientific_generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ValueError("campaign_scientific_generation must be an integer >= 1")
    binding["campaign_scientific_digest"] = _sha256(
        binding["campaign_scientific_digest"], "campaign_scientific_digest")
    binding["network_digest"] = _sha256(
        binding["network_digest"], "network_digest")
    declared = _sha256(binding["digest"], "digest")
    observed = _digest({key: val for key, val in binding.items() if key != "digest"})
    if not hmac.compare_digest(declared, observed):
        raise ValueError("campaign binding content does not match its digest")
    return binding


def build_campaign_binding(*, campaign_id: str,
                           campaign_scientific_generation: int,
                           campaign_scientific_digest: str,
                           prepared_system_id: str,
                           network_digest: str) -> dict[str, Any]:
    """Seal and revalidate one authoritatively generated execution binding."""
    binding: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "campaign_scientific_generation": campaign_scientific_generation,
        "campaign_scientific_digest": campaign_scientific_digest,
        "prepared_system_id": prepared_system_id,
        "network_digest": network_digest,
        "verdict": VERDICT,
    }
    binding["digest"] = _digest(binding)
    return validate_campaign_binding(binding)


def campaign_scientific_ref(*, campaign_id: str, generation: int,
                            digest: str) -> dict[str, Any]:
    """Build the public typed ref carried by RunSet read models."""
    if (isinstance(generation, bool) or not isinstance(generation, int)
            or generation < 1):
        raise ValueError("campaign scientific generation must be an integer >= 1")
    return {
        "kind": "rbfe_campaign",
        "id": _uuid(campaign_id, "campaign_id"),
        "version": generation,
        "sha256": _sha256(digest, "campaign_scientific_digest"),
    }
