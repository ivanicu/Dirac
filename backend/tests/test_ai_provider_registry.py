from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest

from research.provider_registry import (
    AiProviderConfigurationError,
    FileAiProviderRegistry,
    validate_provider_url,
)


ROOT = pathlib.Path(__file__).resolve().parents[2]
EXAMPLE = json.loads(
    (ROOT / "deploy/ai/providers.example.json").read_text(encoding="utf-8")
)


def _registry(document, environ):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as source:
        json.dump({"profiles": [document]}, source)
        source.flush()
        return FileAiProviderRegistry(source.name, environ=environ)


class AiProviderRegistryTests(unittest.TestCase):
    def setUp(self):
        self.profile = copy.deepcopy(EXAMPLE["profiles"][1])
        self.environ = {
            "DIRAC_LOCAL_QWEN_BASE_URL": "http://127.0.0.1:8000/v1/",
            "DIRAC_LOCAL_QWEN_API_KEY": "test-secret-never-render",
        }

    def test_profile_resolves_and_all_public_surfaces_redact_secret_and_url(self):
        registry = _registry(self.profile, self.environ)
        resolved = registry.resolve("qwen-local-isolated")
        rendered = repr(resolved) + json.dumps(resolved.to_public_dict())
        self.assertNotIn("test-secret-never-render", rendered)
        self.assertNotIn("127.0.0.1", rendered)
        self.assertTrue(resolved.profile_digest.startswith("sha256:"))
        self.assertEqual(registry.list_public()[0]["configured"], True)

    def test_digest_attestation_and_classification_fail_closed(self):
        registry = _registry(self.profile, self.environ)
        resolved = registry.resolve("qwen-local-isolated")
        witness = registry.attest(
            resolved.profile_id, resolved.profile_digest, "restricted"
        )
        self.assertEqual(witness["profile_id"], resolved.profile_id)
        with self.assertRaisesRegex(AiProviderConfigurationError, "digest_mismatch"):
            registry.attest(resolved.profile_id, "sha256:" + "0" * 64, "internal")
        with self.assertRaisesRegex(AiProviderConfigurationError, "classification_denied"):
            registry.attest(resolved.profile_id, resolved.profile_digest, "regulated")

    def test_unconfigured_profile_is_degraded_not_absent(self):
        registry = _registry(self.profile, {})
        public = registry.list_public()
        self.assertEqual(len(public), 1)
        self.assertFalse(public[0]["configured"])
        self.assertEqual(public[0]["reason"], "provider_profile_is_unconfigured")

    def test_shared_gpu_profile_is_rejected_without_explicit_host_grant(self):
        self.profile["resource_isolation"] = "shared_dirac_gpu"
        registry = _registry(self.profile, self.environ)
        with self.assertRaisesRegex(
            AiProviderConfigurationError, "shared_gpu_provider_requires_explicit_grant"
        ) as caught:
            registry.resolve("qwen-local-isolated")
        self.assertIn("host-scoped", caught.exception.details["recovery"])

    def test_shared_gpu_profile_resolves_only_with_exact_host_grant(self):
        self.profile["resource_isolation"] = "shared_dirac_gpu"
        denied = {**self.environ, "DIRAC_ALLOW_SHARED_GPU_AI": "true"}
        with self.assertRaisesRegex(
            AiProviderConfigurationError, "shared_gpu_provider_requires_explicit_grant"
        ):
            _registry(self.profile, denied).resolve("qwen-local-isolated")

        granted = {**self.environ, "DIRAC_ALLOW_SHARED_GPU_AI": "1"}
        resolved = _registry(self.profile, granted).resolve("qwen-local-isolated")
        self.assertEqual(
            resolved.to_provenance()["resource_isolation"], "shared_dirac_gpu"
        )

    def test_profile_schema_rejects_arbitrary_static_request_fields(self):
        self.profile["static_request_fields"]["tools"] = []
        with self.assertRaisesRegex(AiProviderConfigurationError, "schema_invalid"):
            _registry(self.profile, self.environ)

    def test_profile_schema_rejects_arbitrary_classifier_request_fields(self):
        self.profile["classifier_request_fields"]["seed"] = 7
        with self.assertRaisesRegex(AiProviderConfigurationError, "schema_invalid"):
            _registry(self.profile, self.environ)

    def test_url_policy_separates_external_and_local_networks(self):
        public_resolver = lambda *a, **k: [
            (2, 1, 6, "", ("8.8.8.8", 443))
        ]
        private_resolver = lambda *a, **k: [
            (2, 1, 6, "", ("127.0.0.1", 8000))
        ]
        self.assertEqual(
            validate_provider_url(
                "https://model.example/v1", "external_cloud", resolver=public_resolver
            ),
            "https://model.example/v1",
        )
        with self.assertRaisesRegex(AiProviderConfigurationError, "requires_https"):
            validate_provider_url(
                "http://model.example/v1", "external_cloud", resolver=public_resolver
            )
        with self.assertRaisesRegex(AiProviderConfigurationError, "not_allowlisted"):
            validate_provider_url(
                "http://model.lan/v1", "local_network", resolver=private_resolver
            )
        self.assertEqual(
            validate_provider_url(
                "http://model.lan/v1", "local_network",
                allowed_local_hosts={"model.lan"}, resolver=private_resolver,
            ),
            "http://model.lan/v1",
        )


if __name__ == "__main__":
    unittest.main()
