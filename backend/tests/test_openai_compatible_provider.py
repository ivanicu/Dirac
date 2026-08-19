from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import threading
import unittest

from research.openai_compatible import (
    ModelOutputInvalid,
    OpenAICompatibleChatProvider,
    ProviderUnavailable,
    _generation_schema,
)
from research.provider_registry import FileAiProviderRegistry
from scripts.research_loop_fake_provider import FakeProviderServer, Handler


ROOT = pathlib.Path(__file__).resolve().parents[2]
EXAMPLE = json.loads(
    (ROOT / "deploy/ai/providers.example.json").read_text(encoding="utf-8")
)
OUTPUT_SCHEMA = json.loads(
    (ROOT / "contracts/domain/research/proposal.schema.json").read_text(encoding="utf-8")
)


class OpenAICompatibleProviderTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeProviderServer(("127.0.0.1", 0), Handler, mode="valid")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        profile = copy.deepcopy(EXAMPLE["profiles"][1])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as source:
            json.dump({"profiles": [profile]}, source)
            source.flush()
            registry = FileAiProviderRegistry(
                source.name,
                environ={
                    "DIRAC_LOCAL_QWEN_BASE_URL": (
                        f"http://127.0.0.1:{self.server.server_port}/v1"
                    ),
                    "DIRAC_LOCAL_QWEN_API_KEY": "test-secret-never-leak",
                },
            )
        self.profile = registry.resolve("qwen-local-isolated")
        self.sleeps = []
        self.provider = OpenAICompatibleChatProvider(sleep=self.sleeps.append)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def complete(self):
        return self.provider.complete_json(
            self.profile,
            system_prompt="Return one bounded JSON object and never call tools.",
            context_json='JSON context: {"facts":[]}',
            output_schema=OUTPUT_SCHEMA,
        )

    def test_valid_response_returns_only_bounded_public_fields(self):
        result = self.complete()
        self.assertEqual(result.resolved_model, "fake-qwen-resolved")
        self.assertEqual(result.provider_request_id, "fake-1")
        self.assertEqual(result.usage["total_tokens"], 30)
        self.assertNotIn("reasoning", repr(result))
        self.assertNotIn("test-secret-never-leak", repr(result))
        response_format = self.server.last_payload["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertEqual(
            response_format["json_schema"]["schema"], _generation_schema(OUTPUT_SCHEMA)
        )
        for keyword in ("propertyNames", "uniqueItems"):
            self.assertIn(keyword, json.dumps(OUTPUT_SCHEMA))
            self.assertNotIn(
                keyword, json.dumps(response_format["json_schema"]["schema"])
            )
        generation = response_format["json_schema"]["schema"]
        self.assertEqual(generation["properties"]["candidate_actions"]["maxItems"], 1)
        self.assertEqual(generation["$defs"]["text4096"]["maxLength"], 512)
        self.assertEqual(OUTPUT_SCHEMA["properties"]["candidate_actions"]["maxItems"], 12)
        self.assertEqual(OUTPUT_SCHEMA["$defs"]["text4096"]["maxLength"], 4096)

    def test_classifier_sampling_is_profile_owned_and_cannot_be_named_arbitrarily(self):
        self.provider.complete_json(
            self.profile,
            system_prompt="Return one JSON classification object.",
            context_json='JSON goal: {"intent":"stop"}',
            output_schema={
                "type": "object", "additionalProperties": False,
                "required": ["selected_template_id"],
                "properties": {
                    "selected_template_id": {"enum": ["fep.stop.v1"]},
                },
            },
            request_profile_fields="classifier_request_fields",
        )
        self.assertEqual(self.server.last_payload["temperature"], 0)
        self.assertEqual(self.server.last_payload["top_k"], -1)
        with self.assertRaisesRegex(ModelOutputInvalid,
                                    "unknown_request_field_profile"):
            self.provider.complete_json(
                self.profile, system_prompt="Return JSON.",
                context_json="JSON context: {}", output_schema=OUTPUT_SCHEMA,
                request_profile_fields="request_fields_from_user",
            )

    def test_action_mode_binds_to_the_request_context_digest(self):
        self.server.mode = "action"
        result = self.provider.complete_json(
            self.profile,
            system_prompt="Return one bounded JSON object and never call tools.",
            context_json=("JSON research context:\n" + json.dumps({
                "research_context": {
                    "digest": "sha256:" + "9" * 64,
                    "facts": [],
                    "available_actions": [{
                        "template_id": "fep.run_selected_edge.v1",
                        "subject_refs": [{
                            "kind": "free_energy_transformation", "id": "edge-a-b",
                        }],
                    }],
                },
            })),
            output_schema=OUTPUT_SCHEMA,
        )
        proposal = json.loads(result.content)
        self.assertEqual(proposal["context_digest"], "sha256:" + "9" * 64)
        self.assertEqual(proposal["candidate_actions"][0]["subject_ref"]["id"],
                         "edge-a-b")

    def test_reasoning_content_is_discarded(self):
        self.server.mode = "reasoning"
        result = self.complete()
        self.assertNotIn("must never leave", repr(result))
        self.assertNotIn("reasoning_content", repr(result))

    def test_tool_call_and_truncated_output_fail_closed(self):
        for mode, reason in (
            ("tool-call", "attempted_tool_call"),
            ("length", "output_was_truncated"),
        ):
            self.server.mode = mode
            with self.assertRaisesRegex(ModelOutputInvalid, reason):
                self.complete()

    def test_transport_statuses_retry_without_provider_fallback(self):
        for mode in ("retry-429", "retry-500", "retry-502", "retry-503", "retry-504"):
            self.server.mode = mode
            self.server.attempts = 0
            result = self.complete()
            self.assertEqual(result.attempts, 3)
        self.assertEqual(len(self.sleeps), 10)

    def test_redirect_auth_invalid_json_and_oversize_are_safe_failures(self):
        cases = (
            ("redirect", ProviderUnavailable, "redirect_refused"),
            ("auth", ProviderUnavailable, "authentication_failed"),
            ("forbidden", ProviderUnavailable, "authentication_failed"),
            ("invalid-json", ModelOutputInvalid, "response_is_not_json"),
            ("oversized", ModelOutputInvalid, "exceeds_profile_bound"),
        )
        for mode, error_type, reason in cases:
            self.server.mode = mode
            self.server.attempts = 0
            with self.assertRaisesRegex(error_type, reason) as caught:
                self.complete()
            rendered = repr(caught.exception)
            self.assertNotIn("test-secret-never-leak", rendered)
            self.assertNotIn(str(self.server.server_port), rendered)

    def test_connection_reset_exhausts_bounded_attempts(self):
        self.server.mode = "connection-reset"
        with self.assertRaisesRegex(ProviderUnavailable, "transport_failure") as caught:
            self.complete()
        self.assertEqual(caught.exception.attempts, 3)
        self.assertEqual(len(self.sleeps), 2)

    def test_timeout_exhausts_bounded_attempts_without_exposing_endpoint(self):
        class AlwaysTimeout:
            def open(self, request, timeout):
                raise TimeoutError("socket timed out at a secret endpoint")

        provider = OpenAICompatibleChatProvider(
            opener=AlwaysTimeout(), sleep=self.sleeps.append
        )
        with self.assertRaisesRegex(ProviderUnavailable, "transport_failure") as caught:
            provider.complete_json(
                self.profile,
                system_prompt="Return JSON.",
                context_json="JSON context: {}",
                output_schema=OUTPUT_SCHEMA,
            )
        self.assertEqual(caught.exception.attempts, 3)
        self.assertNotIn(str(self.server.server_port), repr(caught.exception))
        self.assertNotIn("secret endpoint", repr(caught.exception))

    def test_request_bound_is_enforced_before_http(self):
        tiny = dict(self.profile.document)
        tiny["bounds"] = dict(tiny["bounds"], max_request_bytes=1024)
        object.__setattr__(self.profile, "document", tiny)
        with self.assertRaisesRegex(ModelOutputInvalid, "request_exceeds"):
            self.provider.complete_json(
                self.profile,
                system_prompt="Return JSON.",
                context_json="JSON " + "x" * 2000,
                output_schema=OUTPUT_SCHEMA,
            )
        self.assertEqual(self.server.attempts, 0)


if __name__ == "__main__":
    unittest.main()
