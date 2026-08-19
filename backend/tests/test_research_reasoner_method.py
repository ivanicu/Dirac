from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
import threading
import unittest
import uuid

from artifacts import Artifact, MemoryArtifactStore
from catalog import MethodCatalog
from execution import ThreadExecutor
from invocation import InvocationService
from jobs import MemoryJobStore
from research.action_catalog import default_action_catalog
from research.provider_registry import FileAiProviderRegistry, canonical_json
from research.reasoner import _prompt_release
from scripts.research_loop_fake_provider import FakeProviderServer, Handler

from backend.tests.test_research_proposal_adversarial import context as context_fixture


ROOT = pathlib.Path(__file__).resolve().parents[2]
EXAMPLE = json.loads(
    (ROOT / "deploy/ai/providers.example.json").read_text(encoding="utf-8")
)


class FrozenContextReader:
    def __init__(self, artifact_id: str, raw: bytes):
        self.raw = raw
        self.artifact = Artifact(
            sha256=hashlib.sha256(raw).hexdigest(),
            role="research.context_snapshot",
            media_type="application/json",
            size_bytes=len(raw),
            id=artifact_id,
        )

    def read(self, address):
        if address != self.artifact.id:
            raise KeyError(address)
        return self.artifact, self.raw


class ResearchReasonerMethodTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeProviderServer(("127.0.0.1", 0), Handler, mode="valid")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        profile = EXAMPLE["profiles"][1]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as source:
            json.dump({"profiles": [profile]}, source)
            source.flush()
            self.registry = FileAiProviderRegistry(
                source.name,
                environ={
                    "DIRAC_LOCAL_QWEN_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/v1",
                    "DIRAC_LOCAL_QWEN_API_KEY": "reasoner-test-secret",
                },
            )
        self.profile = self.registry.resolve("qwen-local-isolated")
        self.context = context_fixture()
        self.raw_context = canonical_json(self.context)
        self.context_id = str(uuid.uuid4())
        self.reader = FrozenContextReader(self.context_id, self.raw_context)
        self.output_store = MemoryArtifactStore()
        self.jobs = MemoryJobStore()
        self.executor = ThreadExecutor(max_workers=2)
        self.service = InvocationService(
            MethodCatalog.load(), store=self.output_store,
            artifact_reader=self.reader, ledger=self.jobs, executor=self.executor,
            ai_provider_registry=self.registry,
        )

    def tearDown(self):
        self.executor.shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def payload(self, request_key="reasoner-test-1"):
        manifest, prompt_digest, _ = _prompt_release()
        return {
            "request_key": request_key,
            "run_ref": {"kind": "run", "id": str(uuid.uuid4())},
            "loop_version": self.context["loop_version"],
            "iteration": self.context["iteration"],
            "context_snapshot_ref": {
                "kind": "artifact", "id": self.context_id,
                "sha256": "sha256:" + self.reader.artifact.sha256,
            },
            "context_digest": self.context["digest"],
            "context_size_bytes": len(self.raw_context),
            "provider_profile_id": self.profile.profile_id,
            "provider_profile_digest": self.profile.profile_digest,
            "prompt_release_id": manifest["prompt_release_id"],
            "prompt_release_digest": prompt_digest,
            "output_schema_digest": manifest["proposal_schema_sha256"],
            "action_catalog_digest": default_action_catalog().digest,
            "data_classification": "internal",
        }

    def run_job(self, payload):
        submitted = self.service.submit(
            "ai.research.propose", payload,
            actor={"kind": "human", "id": "scientist-1"},
            command_id="research.loop.create",
        )
        job_id = submitted["data"]["job"]["id"]
        return submitted, self.service.wait_job(
            job_id, actor={"kind": "human", "id": "scientist-1"}, timeout=5
        )

    def test_method_is_job_only_non_cacheable_and_persists_only_canonical_proposal(self):
        spec = self.service.catalog.get("ai.research.propose")
        self.assertEqual(spec.execution["supported_modes"], ["job"])
        self.assertFalse(spec.cacheable)
        sync = self.service.invoke(
            "ai.research.propose", self.payload(),
            actor={"kind": "human", "id": "scientist-1"},
        )
        self.assertEqual(sync["error"]["code"], "UNSUPPORTED")
        submitted, job = self.run_job(self.payload())
        self.assertEqual(job["state"], "done", job)
        data = job["result_summary"]["data"]
        self.assertEqual(data["claim_boundary"], "model_proposal_not_scientific_evidence")
        self.assertEqual(data["validation_attempts"], 1)
        proposal_artifacts = [
            artifact for artifact in self.output_store._meta.values()
            if artifact.role == "research.proposal"
        ]
        self.assertEqual(len(proposal_artifacts), 1)
        _, raw = self.output_store.read(proposal_artifacts[0].id)
        self.assertNotIn(b"reasoning_content", raw)
        self.assertNotIn(b"reasoner-test-secret", raw)
        self.assertEqual(submitted["meta"]["execution_mode"], "job")

    def test_invalid_first_proposal_regenerates_once_with_bounded_error(self):
        self.server.mode = "invalid-then-valid"
        _, job = self.run_job(self.payload("reasoner-regenerate"))
        self.assertEqual(job["state"], "done", job)
        self.assertEqual(job["result_summary"]["data"]["validation_attempts"], 2)
        self.assertEqual(self.server.attempts, 2)

    def test_repeated_invalid_proposal_fails_with_typed_terminal_error(self):
        self.server.mode = "schema-invalid"
        _, job = self.run_job(self.payload("reasoner-invalid"))
        self.assertEqual(job["state"], "failed", job)
        self.assertEqual(job["error_code"], "MODEL_OUTPUT_INVALID")
        self.assertEqual(self.server.attempts, 2)

    def test_same_request_key_replays_job_without_second_provider_call(self):
        payload = self.payload("reasoner-idempotent")
        first, job = self.run_job(payload)
        self.assertEqual(job["state"], "done", job)
        calls = self.server.attempts
        second, replay = self.run_job(payload)
        self.assertEqual(first["data"]["job"]["id"], second["data"]["job"]["id"])
        self.assertEqual(replay["state"], "done")
        self.assertEqual(self.server.attempts, calls)


if __name__ == "__main__":
    unittest.main()
