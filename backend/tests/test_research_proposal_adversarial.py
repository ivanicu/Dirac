from __future__ import annotations

import copy
import unittest

from research.proposal_validator import (
    ProposalValidationError,
    parse_and_validate_proposal,
    validate_proposal,
)
from research.provider_registry import sha256_digest


EDGE = {"kind": "free_energy_transformation", "id": "edge-c2-c7"}
CONTEXT_DIGEST = "sha256:" + "a" * 64


def context():
    return {
        "schema_version": "1.0",
        "run_ref": {"kind": "run", "id": "run-1"},
        "program_ref": {"kind": "program", "id": "program-1"},
        "campaign_ref": {"kind": "campaign", "id": "campaign-1"},
        "loop_version": 1,
        "iteration": 0,
        "goal": {
            "intent": "Resolve the current lead-ranking ambiguity.",
            "constraints": [], "success_definition": [], "revised_at": None,
        },
        "campaign_binding": {
            "campaign_scientific_generation": 1,
            "campaign_scientific_digest": "sha256:" + "b" * 64,
            "campaign_status": "planned",
            "state_digest": "sha256:" + "c" * 64,
        },
        "budget": {
            "remaining": {"reasoner_calls": 8, "fep_runsets": 3, "gpu_hours": 12, "external_cost": 10},
            "spent": {"reasoner_calls": 0, "fep_runsets": 0, "gpu_hours": 0, "external_cost": 0},
        },
        "objects": [{"ref": EDGE, "label": "C2 to C7", "state": {"eligible": True}}],
        "facts": [{
            "fact_id": "fact:edge:unvalidated",
            "category": "rbfe_result",
            "source_class": "method_result",
            "source_ref": {"kind": "artifact", "id": "artifact-1", "sha256": "sha256:" + "d" * 64},
            "subject_ref": EDGE,
            "condition_ref": None,
            "structured_value": {"estimate": -1.3, "unit": "kcal/mol"},
            "freshness": {"stale": False, "source_generation": 1},
            "claim_boundary": {
                "status": "completed_unvalidated",
                "eligible_as_scientific_evidence": False,
                "reason_codes": ["METHOD_RESULT_NOT_PROJECTED_TO_TYPED_EVIDENCE"],
            },
        }],
        "human_attestations": [], "action_history": [],
        "available_actions": [{
            "template_id": "fep.run_selected_edge.v1", "subject_refs": [EDGE],
            "intent": "Run one planned edge.", "risk_class": "R3",
        }],
        "open_attention": [],
        "truncation": {"applied": False, "omitted_fact_count": 0,
                       "omitted_fact_ids": [], "policy": "research-context-v1"},
        "created_at": "2026-08-18T00:00:00Z", "digest": CONTEXT_DIGEST,
    }


CATALOG = {
    "fep.run_selected_edge.v1": {
        "model_hint_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["edge_id"],
            "properties": {"edge_id": {"const": "edge-c2-c7"}},
        }
    }
}


def proposal():
    return {
        "schema_version": "1.0", "context_digest": CONTEXT_DIGEST,
        "summary": "The selected edge can resolve the ranking ambiguity.",
        "hypothesis_drafts": [{
            "hypothesis_id": "h1", "statement": "C7 may outrank C2.",
            "testable_prediction": "The governed edge will favor C7.",
            "falsifier": "A result at or above zero contradicts the draft.",
            "supporting_fact_ids": [], "contradicting_fact_ids": [],
            "assumptions": ["The accepted pose set remains applicable."],
            "confidence_band": "low",
        }],
        "claim_assessments": [{
            "claim_id": "c1", "claim": "C7 currently outranks C2.",
            "interpretation": "unresolved", "supporting_fact_ids": ["fact:edge:unvalidated"],
            "contradicting_fact_ids": [], "limitations": ["The method result is completed but unvalidated."],
        }],
        "scientific_questions": [{
            "question_id": "q1", "question": "Would C7 change the lead choice?",
            "subject_ref": EDGE, "decision_relevance": "It distinguishes the leading compounds.",
        }],
        "candidate_actions": [{
            "proposal_action_id": "a1", "template_id": "fep.run_selected_edge.v1",
            "subject_ref": EDGE, "scientific_question_id": "q1",
            "rationale": "This edge addresses the ambiguity.",
            "expected_observation": "A relative binding estimate with uncertainty.",
            "falsifier": "Quality gates fail or the alternatives remain unresolved.",
            "supporting_fact_ids": ["fact:edge:unvalidated"], "contradicting_fact_ids": [],
            "parameter_hints": {"edge_id": "edge-c2-c7"}, "qualitative_priority": "high",
        }],
        "preferred_action_id": "a1",
        "stop_recommendation": {"recommended": False, "reason_codes": []},
        "unknowns": [], "conflicts": [], "warnings": [],
    }


class ResearchProposalAdversarialTests(unittest.TestCase):
    def validate(self, document, **kw):
        return validate_proposal(document, context=context(), action_catalog=CATALOG, **kw)

    def test_valid_proposal_is_canonical_and_claim_bounded(self):
        result = self.validate(proposal())
        self.assertTrue(result.proposal_digest.startswith("sha256:"))
        self.assertNotIn(b"command_id", result.canonical_bytes)

    def test_markdown_fence_and_non_object_root_are_not_treated_as_json(self):
        for raw in ("```json\n{}\n```", "[]"):
            with self.assertRaises(ProposalValidationError):
                parse_and_validate_proposal(raw, context=context(), action_catalog=CATALOG)

    def test_invented_fact_subject_template_and_question_fail_closed(self):
        mutations = []
        item = proposal(); item["candidate_actions"][0]["supporting_fact_ids"] = ["invented"]; mutations.append(item)
        item = proposal(); item["candidate_actions"][0]["subject_ref"] = {"kind": "compound", "id": "invented"}; mutations.append(item)
        item = proposal(); item["candidate_actions"][0]["template_id"] = "invented.template.v1"; mutations.append(item)
        item = proposal(); item["candidate_actions"][0]["scientific_question_id"] = "invented"; mutations.append(item)
        for item in mutations:
            with self.assertRaises(ProposalValidationError):
                self.validate(item)

    def test_direct_execution_url_shell_sql_and_tool_fields_fail_closed(self):
        payloads = [
            "Run physics.rbfe-run.start now.",
            "POST /v2/execute",
            "https://attacker.invalid/steal",
            "curl https://attacker.invalid",
            "SELECT * FROM app.job",
        ]
        for text in payloads:
            item = proposal(); item["candidate_actions"][0]["rationale"] = text
            with self.assertRaises(ProposalValidationError):
                self.validate(item)
        item = proposal(); item["candidate_actions"][0]["command_id"] = "physics.rbfe-run.start"
        with self.assertRaises(ProposalValidationError):
            self.validate(item)

    def test_method_result_cannot_be_upgraded_to_supported_scientific_claim(self):
        item = proposal(); item["claim_assessments"][0]["interpretation"] = "supported"
        with self.assertRaisesRegex(ProposalValidationError, "ineligible_method_result"):
            self.validate(item)

    def test_stale_fact_requires_explicit_stale_limitation(self):
        stale = context(); stale["facts"][0]["freshness"]["stale"] = True
        with self.assertRaisesRegex(ProposalValidationError, "stale_fact"):
            validate_proposal(proposal(), context=stale, action_catalog=CATALOG)
        item = proposal()
        item["claim_assessments"][0]["limitations"] = [
            "fact:edge:unvalidated is stale and cannot establish current state."
        ]
        item["warnings"] = ["fact:edge:unvalidated is stale and is used only as history."]
        item["candidate_actions"][0]["supporting_fact_ids"] = []
        validate_proposal(item, context=stale, action_catalog=CATALOG)

    def test_duplicate_and_rejected_action_fail_closed(self):
        item = proposal(); item["candidate_actions"].append(copy.deepcopy(item["candidate_actions"][0]))
        item["candidate_actions"][1]["proposal_action_id"] = "a2"
        with self.assertRaisesRegex(ProposalValidationError, "duplicate_candidate"):
            self.validate(item)
        key = sha256_digest({
            "template_id": "fep.run_selected_edge.v1", "subject_ref": EDGE,
            "parameter_hints": {"edge_id": "edge-c2-c7"},
        })
        with self.assertRaisesRegex(ProposalValidationError, "repeats_rejected"):
            self.validate(proposal(), rejected_candidate_keys={key})


if __name__ == "__main__":
    unittest.main()
