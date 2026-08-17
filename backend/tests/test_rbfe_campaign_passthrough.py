from __future__ import annotations

import pytest


def test_openfe_plan_preserves_campaign_and_chemistry_evidence(monkeypatch):
    from motif import rbfe

    observed = {}

    def fake_plan(compounds, *, runtime=None, campaign_context=None):
        observed["campaign_context"] = campaign_context
        return {
            "engine": "OpenFE",
            "edges": [{
                "left_id": "A",
                "right_id": "B",
                "mapping_score": 0.91,
                "selected_atom_mapping": {"0": 0, "1": 1},
                "mapping_methods": ["lomap", "kartograf"],
                "mapping_disagreement_jaccard": 0.0,
                "mapping_proposals": [],
                "depiction_contract": {"digest": "sha256:edge"},
                "chemistry_evidence": {"element": "confirmed"},
            }],
            "identity_contract": {"digest": "sha256:identity"},
            "depiction_contract": {"digest": "sha256:plan"},
            "chemistry_evidence": {"verdict": "unverified"},
            "rejected_edges": [{"left_id": "A", "right_id": "C"}],
            "planner_diagnostics": {"stage": "complete"},
        }

    monkeypatch.setattr(rbfe, "_plan_with_openfe", fake_plan)
    context = {
        "campaign_id": "7e6243c8-0e58-4adf-8841-2a308a47ca53",
        "campaign_scientific_generation": 4,
        "campaign_scientific_digest": "sha256:" + "c" * 64,
        "prepared_system_id": "824dba94-9fae-480b-9d5e-2e4fe958a5c3",
    }
    result = rbfe.plan_rbfe_network(
        [{"id": "A", "smiles": "c1ccccc1"},
         {"id": "B", "smiles": "Fc1ccccc1"}],
        campaign_context=context,
    )

    assert observed["campaign_context"] == context
    assert result["campaign_context"] == context
    assert result["identity_contract"]["digest"] == "sha256:identity"
    assert result["rejected_edges"] == [{"left_id": "A", "right_id": "C"}]
    assert result["edges"][0]["depiction_contract"]["digest"] == "sha256:edge"
    assert result["edges"][0]["chemistry_evidence"]["element"] == "confirmed"


def test_network_handler_rejects_every_partial_campaign_context(monkeypatch):
    from motif import structure_methods

    monkeypatch.setattr(
        structure_methods, "plan_rbfe_network",
        lambda *_args, **_kwargs: pytest.fail("partial context reached planner"))

    class Context:
        @staticmethod
        def check_budget():
            return None

    payload = {
        "compounds": [
            {"id": "A", "smiles": "CC"},
            {"id": "B", "smiles": "CCC"},
        ],
        "campaign_id": "7e6243c8-0e58-4adf-8841-2a308a47ca53",
        "campaign_scientific_generation": 4,
        "campaign_scientific_digest": "sha256:" + "c" * 64,
    }
    with pytest.raises(Exception, match="prepared_system_id"):
        structure_methods.rbfe_plan_handler(payload, Context())
