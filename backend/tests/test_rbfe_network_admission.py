from __future__ import annotations

import json

import pytest

import failures
from catalog import MethodCatalog
from invocation import HandlerResult, InvocationContext, InvocationService
from motif.structure_methods import (
    attest_rbfe_network_admission,
    rbfe_plan_handler,
)


CAMPAIGN_ID = "10000000-0000-4000-8000-000000000001"
PREPARED_SYSTEM_ID = "10000000-0000-4000-8000-000000000002"
OTHER_SYSTEM_ID = "10000000-0000-4000-8000-000000000003"
SCIENCE_DIGEST = "sha256:" + "c" * 64
PREPARED_DIGEST = "sha256:" + "d" * 64
ACTOR = {"kind": "human", "id": "network-owner"}


class _CampaignResolver:
    def __init__(self, *, prepared_system_id: str = PREPARED_SYSTEM_ID,
                 stale_system: bool = False) -> None:
        self.current_generation = 7
        self.current_digest = SCIENCE_DIGEST
        self.prepared_system_id = prepared_system_id
        self.stale_system = stale_system
        self.calls = 0

    def assert_campaign_generation(self, campaign_id, scientific_generation,
                                   scientific_digest, actor):
        self.calls += 1
        if actor != ACTOR:
            raise failures.DiracNotFound(
                "RBFE campaign does not exist or is not accessible")
        if (campaign_id != CAMPAIGN_ID
                or scientific_generation != self.current_generation
                or scientific_digest != self.current_digest):
            raise failures.DiracInvalidParameters("campaign generation is stale")
        prepared_ref = {
            "kind": "prepared_receptor_state",
            "id": self.prepared_system_id,
            "sha256": PREPARED_DIGEST,
        }
        if self.stale_system:
            prepared_ref["stale"] = True
        return {
            "campaign_id": CAMPAIGN_ID,
            "campaign_scientific_generation": self.current_generation,
            "campaign_scientific_digest": self.current_digest,
            "state": {"owned_object_refs": [prepared_ref]},
            "verdict": "CONFIRMED",
        }


class _PinnedNetworkCache:
    def __init__(self) -> None:
        network = {
            "schema_version": "1.0",
            "kind": "rbfe_network_plan",
            "digest": "sha256:" + "9" * 64,
            "compounds": [
                {"id": "A", "canonical_smiles": "CC"},
                {"id": "B", "canonical_smiles": "CCC"},
            ],
            "edges": [],
            "mode": "pilot",
            "official_openfe_plan": None,
            "policy": {
                "extra_edge_fraction": 0.35,
                "minimum_similarity": 0.15,
                "mapping": "RDKit FMCS fallback",
                "planner": "rdkit_fallback",
            },
            "claim_boundary": "cached network planning fixture only",
            "campaign_context": {
                "campaign_id": CAMPAIGN_ID,
                "campaign_scientific_generation": 7,
                "campaign_scientific_digest": SCIENCE_DIGEST,
                "prepared_system_id": PREPARED_SYSTEM_ID,
            },
            "campaign_admission": {
                "schema_version": "rbfe-network-admission.v1",
                "verdict": "CONFIRMED",
                "scope": "campaign_bound_network",
                "campaign_bound": True,
                "campaign_id": CAMPAIGN_ID,
                "campaign_scientific_generation": 7,
                "campaign_scientific_digest": SCIENCE_DIGEST,
                "prepared_system_id": PREPARED_SYSTEM_ID,
                "prepared_system_ref": {
                    "kind": "prepared_receptor_state",
                    "id": PREPARED_SYSTEM_ID,
                    "sha256": PREPARED_DIGEST,
                },
            },
        }
        self.result = HandlerResult(
            result={
                "network_digest": network["digest"],
                "compound_count": 2,
                "edge_count": 0,
                "network": network,
            },
            artifacts=[("rbfe.network", json.dumps(network).encode())],
            provenance={"fixture": True},
            cache="db",
        )
        self.lookups = 0

    def lookup(self, _method_id, _payload, *, execution_digest):
        assert execution_digest.startswith("sha256:")
        self.lookups += 1
        return self.result


class _CacheOnlyLocalCpuExecutor:
    kind = "remote"
    adapter_kind = "local_cpu"
    supports_submission = False

    @staticmethod
    def execution_adapter_for(_spec):
        return "local_cpu"

    @staticmethod
    def execute(*_args, **_kwargs):
        raise AssertionError("campaign admission test unexpectedly executed science")


def _payload(*, prepared_system_id: str = PREPARED_SYSTEM_ID) -> dict:
    return {
        "compounds": [
            {"id": "A", "smiles": "CC"},
            {"id": "B", "smiles": "CCC"},
        ],
        "campaign_id": CAMPAIGN_ID,
        "campaign_scientific_generation": 7,
        "campaign_scientific_digest": SCIENCE_DIGEST,
        "prepared_system_id": prepared_system_id,
    }


def _service(resolver, cache):
    return InvocationService(
        MethodCatalog.load(), cache=cache,
        executor=_CacheOnlyLocalCpuExecutor(),
        rbfe_reference_resolver=resolver,
    )


def test_bound_network_cache_hit_requires_current_owned_prepared_system():
    resolver = _CampaignResolver()
    cache = _PinnedNetworkCache()
    response = _service(resolver, cache).invoke(
        "physics.motif.rbfe_network", _payload(), actor=ACTOR)

    assert response["ok"] is True
    assert response["meta"]["cache"] == "db"
    witness = response["meta"]["provenance"]["server_attestations"][
        "rbfe_network_campaign"]
    assert witness["verdict"] == "CONFIRMED"
    assert witness["prepared_system_ref"] == {
        "kind": "prepared_receptor_state",
        "id": PREPARED_SYSTEM_ID,
        "sha256": PREPARED_DIGEST,
    }
    assert witness["actor"] == ACTOR
    assert resolver.calls == 1
    assert cache.lookups == 1


@pytest.mark.parametrize(
    ("actor", "resolver", "payload", "error_code"),
    (
        (
            {"kind": "human", "id": "other-chemist"},
            _CampaignResolver(),
            _payload(),
            "NOT_FOUND",
        ),
        (
            ACTOR,
            _CampaignResolver(prepared_system_id=OTHER_SYSTEM_ID),
            _payload(),
            "INVALID_PARAMETERS",
        ),
        (
            ACTOR,
            _CampaignResolver(stale_system=True),
            _payload(),
            "INVALID_PARAMETERS",
        ),
    ),
)
def test_bound_network_refuses_cross_actor_fake_or_stale_system_before_cache(
        actor, resolver, payload, error_code):
    cache = _PinnedNetworkCache()
    response = _service(resolver, cache).invoke(
        "physics.motif.rbfe_network", payload, actor=actor)

    assert response["ok"] is False
    assert response["error"]["code"] == error_code
    assert resolver.calls == 1
    assert cache.lookups == 0


def test_bound_network_old_cache_cannot_survive_campaign_invalidation():
    resolver = _CampaignResolver()
    cache = _PinnedNetworkCache()
    service = _service(resolver, cache)

    first = service.invoke(
        "physics.motif.rbfe_network", _payload(), actor=ACTOR)
    assert first["ok"] is True
    assert cache.lookups == 1

    resolver.current_generation = 8
    replay = service.invoke(
        "physics.motif.rbfe_network", _payload(), actor=ACTOR)

    assert replay["ok"] is False
    assert replay["error"]["code"] == "INVALID_PARAMETERS"
    assert resolver.calls == 2
    assert cache.lookups == 1, "stale replay reached cache.lookup"


def test_bound_network_missing_resolver_refuses_before_cache():
    cache = _PinnedNetworkCache()
    response = _service(None, cache).invoke(
        "physics.motif.rbfe_network", _payload(), actor=ACTOR)

    assert response["ok"] is False
    assert response["error"]["code"] == "UNSUPPORTED"
    assert cache.lookups == 0


def test_unbound_smoke_plan_has_explicit_sealed_verdict_without_resolver():
    context = InvocationContext(
        method_id="physics.motif.rbfe_network", actor=ACTOR)
    witness = attest_rbfe_network_admission(
        {"compounds": [
            {"id": "A", "smiles": "CC"},
            {"id": "B", "smiles": "CCC"},
        ]}, context)

    assert witness == {
        "schema_version": "rbfe-network-admission.v1",
        "verdict": "UNBOUND",
        "scope": "smoke_plan",
        "campaign_bound": False,
        "actor": ACTOR,
    }


def test_handler_consumes_sealed_witness_without_repeating_resolver(monkeypatch):
    resolver = _CampaignResolver()
    controller = InvocationContext(
        method_id="physics.motif.rbfe_network", actor=ACTOR,
        rbfe_reference_resolver=resolver)
    payload = _payload()
    witness = attest_rbfe_network_admission(payload, controller)
    assert resolver.calls == 1

    class _MustNotResolveAgain:
        @staticmethod
        def assert_campaign_generation(*_args, **_kwargs):
            raise AssertionError("handler repeated controller-only Campaign admission")

    monkeypatch.setattr(
        "motif.structure_methods.plan_rbfe_network",
        lambda _compounds, **kwargs: {
            "schema_version": "1.0", "kind": "rbfe_network_plan",
            "digest": "sha256:" + "1" * 64,
            "compounds": [
                {"id": "A", "canonical_smiles": "CC"},
                {"id": "B", "canonical_smiles": "CCC"},
            ],
            "edges": [], "mode": kwargs["mode"],
            "official_openfe_plan": None,
            "policy": {
                "extra_edge_fraction": kwargs["extra_edge_fraction"],
                "minimum_similarity": kwargs["minimum_similarity"],
                "mapping": "RDKit FMCS fallback", "planner": kwargs["planner"],
            },
            "claim_boundary": "fixture",
            "campaign_context": kwargs["campaign_context"],
        })
    worker = InvocationContext(
        method_id="physics.motif.rbfe_network", actor=ACTOR,
        rbfe_reference_resolver=_MustNotResolveAgain(),
        server_attestations={"rbfe_network_campaign": witness})

    output = rbfe_plan_handler(payload, worker)

    assert resolver.calls == 1
    assert output.provenance["campaign_admission"] == witness
    assert output.result["network"]["campaign_admission"] == {
        key: value for key, value in witness.items() if key != "actor"
    }
    assert output.result["network_digest"] == output.result["network"]["digest"]
