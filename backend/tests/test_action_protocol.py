from __future__ import annotations

import unittest

from dirac_app.action_protocol import (
    ActionAuthority, ActionImplementation, ApplicationActionDefinition,
    IdempotencyConflict, InvalidPreview, StalePreview, Unauthorized,
)


ACTOR = {"kind": "human", "id": "chemist:ivan"}
SUBJECTS = [{"kind": "molecule", "id": "draft-1"}]


class ActionProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1000.0
        self.allowed = True
        self.versions = {"molecule:draft-1": 3}
        self.commits = 0

        def authorize(actor, action, phase, subjects):
            return self.allowed and actor == ACTOR and bool(subjects)

        def read_versions(subjects):
            return dict(self.versions)

        def commit(context):
            self.commits += 1
            return {
                "status": "committed",
                "applied_effects": [{"kind": "compound-created"}],
                "output_refs": [{"kind": "compound", "id": "CMP-1"}],
            }

        definition = ApplicationActionDefinition(
            id="design.proposal.promote", version=2,
            intent="Promote a versioned molecular proposal",
            input_schema={"type": "object"},
            consequence_class="scientific-identity",
            authorization_policy="program-design-promote-v1",
            precondition_policy="proposal-and-objective-current-v1",
            idempotency_policy="transport-key-plus-payload-digest",
            conflict_policy="molecular-edits-branch",
            transaction_policy="identity-and-program-link-transaction",
            receipt_schema={"type": "object"},
        )
        self.authority = ActionAuthority(
            secret=b"a secure test secret at least thirty two bytes long",
            authorize=authorize, read_versions=read_versions,
            now=lambda: self.now, token_ttl_seconds=60,
        )
        self.authority.register(ActionImplementation(
            definition=definition,
            preview=lambda context: {
                "proposed_effects": [{"kind": "canonicalize-and-link"}],
                "warnings": ["normalized-identity-check"],
                "required_acknowledgements": ["identity-policy"],
            }, commit=commit,
        ))

    def preview(self, value="candidate"):
        offer = self.authority.offer(
            "design.proposal.promote@2", actor=ACTOR, subjects=SUBJECTS,
            permission_envelope="perm-1",
        )
        return self.authority.preview(offer["offer_id"], input={"value": value})

    def test_offer_preview_commit_returns_authoritative_receipt(self) -> None:
        preview = self.preview()
        receipt = self.authority.commit(
            preview["precondition_token"], input={"value": "candidate"},
            idempotency_key="transport-1", attempt_id="attempt-1",
            acknowledgements=["identity-policy"],
        )
        self.assertEqual(receipt["action"], "design.proposal.promote@2")
        self.assertEqual(receipt["output_refs"], [{"kind": "compound", "id": "CMP-1"}])
        self.assertEqual(self.commits, 1)

    def test_transport_retry_returns_same_receipt_once(self) -> None:
        preview = self.preview()
        args = dict(input={"value": "candidate"}, idempotency_key="transport-1",
                    attempt_id="attempt-1", acknowledgements=["identity-policy"])
        first = self.authority.commit(preview["precondition_token"], **args)
        second = self.authority.commit(preview["precondition_token"], **args)
        self.assertEqual(first, second)
        self.assertEqual(self.commits, 1)

    def test_same_idempotency_key_cannot_hide_a_different_attempt(self) -> None:
        preview = self.preview()
        self.authority.commit(
            preview["precondition_token"], input={"value": "candidate"},
            idempotency_key="transport-1", attempt_id="attempt-1",
            acknowledgements=["identity-policy"],
        )
        with self.assertRaises(IdempotencyConflict):
            self.authority.commit(
                preview["precondition_token"], input={"value": "candidate"},
                idempotency_key="transport-1", attempt_id="attempt-2",
                acknowledgements=["identity-policy"],
            )

    def test_source_change_returns_semantic_version_diff(self) -> None:
        preview = self.preview()
        self.versions["molecule:draft-1"] = 4
        with self.assertRaises(StalePreview) as caught:
            self.authority.commit(
                preview["precondition_token"], input={"value": "candidate"},
                idempotency_key="transport-1", attempt_id="attempt-1",
                acknowledgements=["identity-policy"],
            )
        self.assertEqual(caught.exception.details["diff"], [{
            "subject": "molecule:draft-1", "before": 3, "after": 4,
        }])
        self.assertEqual(self.commits, 0)

    def test_commit_reauthorizes_after_preview(self) -> None:
        preview = self.preview()
        self.allowed = False
        with self.assertRaises(Unauthorized):
            self.authority.commit(
                preview["precondition_token"], input={"value": "candidate"},
                idempotency_key="transport-1", attempt_id="attempt-1",
                acknowledgements=["identity-policy"],
            )

    def test_commit_rejects_changed_payload_and_missing_acknowledgement(self) -> None:
        preview = self.preview()
        with self.assertRaises(InvalidPreview):
            self.authority.commit(
                preview["precondition_token"], input={"value": "changed"},
                idempotency_key="transport-1", attempt_id="attempt-1",
                acknowledgements=["identity-policy"],
            )
        with self.assertRaises(InvalidPreview) as caught:
            self.authority.commit(
                preview["precondition_token"], input={"value": "candidate"},
                idempotency_key="transport-2", attempt_id="attempt-1",
            )
        self.assertEqual(caught.exception.details["missing"], ["identity-policy"])

    def test_expired_and_tampered_preview_tokens_fail(self) -> None:
        preview = self.preview()
        self.now += 61
        with self.assertRaises(InvalidPreview):
            self.authority.commit(
                preview["precondition_token"], input={"value": "candidate"},
                idempotency_key="transport-1", attempt_id="attempt-1",
                acknowledgements=["identity-policy"],
            )
        self.now = 1000
        with self.assertRaises(InvalidPreview):
            self.authority.commit(
                preview["precondition_token"] + "x", input={"value": "candidate"},
                idempotency_key="transport-2", attempt_id="attempt-1",
                acknowledgements=["identity-policy"],
            )

    def test_completed_transport_retry_survives_preview_expiry(self) -> None:
        preview = self.preview()
        args = dict(input={"value": "candidate"}, idempotency_key="transport-1",
                    attempt_id="attempt-1", acknowledgements=["identity-policy"])
        first = self.authority.commit(preview["precondition_token"], **args)
        self.now += 61
        again = self.authority.commit(preview["precondition_token"], **args)
        self.assertEqual(first, again)
        self.assertEqual(self.commits, 1)


if __name__ == "__main__":
    unittest.main()
