from __future__ import annotations

import hashlib
import json

import pytest

from motif.rbfe_binding import (build_campaign_binding,
                                validate_campaign_binding)


CAMPAIGN_ID = "58f58725-855b-45fb-88fe-ab2417bbd7a5"
SYSTEM_ID = "38eb597c-e430-429a-b70b-28f1e0c7ef91"
SCIENCE_DIGEST = "sha256:" + "a" * 64
NETWORK_DIGEST = "sha256:" + "b" * 64


def _digest(value: dict) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _binding() -> dict:
    return build_campaign_binding(
        campaign_id=CAMPAIGN_ID,
        campaign_scientific_generation=3,
        campaign_scientific_digest=SCIENCE_DIGEST,
        prepared_system_id=SYSTEM_ID,
        network_digest=NETWORK_DIGEST,
    )


def _reseal(binding: dict) -> dict:
    value = dict(binding)
    value.pop("digest", None)
    value["digest"] = _digest(value)
    return value


def test_exact_v2_campaign_binding_round_trips() -> None:
    binding = _binding()
    assert validate_campaign_binding(binding) == binding


@pytest.mark.parametrize("mutate, message", [
    (lambda row: row.update(schema_version="rbfe-campaign-binding.v1"),
     "schema must be rbfe-campaign-binding.v2"),
    (lambda row: row.update(campaign_version=7), "unexpected"),
    (lambda row: row.update(state_digest="sha256:" + "f" * 64),
     "unexpected"),
    (lambda row: row.pop("campaign_scientific_digest"), "missing"),
    (lambda row: row.update(campaign_scientific_digest="sha256:abcd"),
     "complete sha256"),
    (lambda row: row.update(network_digest="sha256:abcd"),
     "complete sha256"),
])
def test_campaign_binding_rejects_every_non_v2_shape(mutate, message) -> None:
    binding = _binding()
    mutate(binding)
    # Re-sealing proves shape/type rejection is independent of tamper detection.
    binding = _reseal(binding)
    with pytest.raises(ValueError, match=message):
        validate_campaign_binding(binding)


def test_campaign_binding_rejects_tampering() -> None:
    binding = _binding()
    binding["campaign_scientific_generation"] += 1
    with pytest.raises(ValueError, match="does not match its digest"):
        validate_campaign_binding(binding)


def test_execution_readers_all_delegate_to_the_exact_validator() -> None:
    from motif import rbfe_pipeline
    from motif.openfe_runner import _campaign_binding as runner_binding
    from motif.rbfe_runset import RbfeRunSetController

    invalid = _binding()
    invalid["state_digest"] = "sha256:" + "f" * 64
    invalid = _reseal(invalid)

    with pytest.raises(Exception, match="unexpected"):
        runner_binding({"campaign_binding": invalid})

    controller = object.__new__(RbfeRunSetController)
    with pytest.raises(Exception, match="unexpected"):
        controller._assert_campaign_current(
            {}, {"campaign_binding": invalid})

    with pytest.raises(Exception, match="unexpected"):
        rbfe_pipeline._assert_current_campaign_binding(
            {"campaign_binding": invalid}, {}, object())
