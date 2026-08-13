from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from artifact_backends.local_cas import LocalCAS


class LocalCASTests(unittest.TestCase):
    def test_stream_commit_verify_deduplicate_and_range(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalCAS(directory)
            chunks = [b"abc", b"def" * 1024, b"xyz"]
            first = store.put_chunks(chunks)
            second = store.put_chunks(chunks)
            self.assertEqual(first.digest, second.digest)
            self.assertEqual(first.locator, second.locator)
            self.assertEqual(store.verify(first.digest).size_bytes, sum(map(len, chunks)))
            with store.open(first.digest, offset=2, length=7) as stream:
                self.assertEqual(stream.read(), b"cdefdef")

    def test_interrupted_write_never_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalCAS(directory)
            with self.assertRaisesRegex(RuntimeError, "interrupt"):
                with store.begin() as pending:
                    pending.write(b"partial")
                    raise RuntimeError("interrupt")
            self.assertEqual(list(Path(store.staging).iterdir()), [])
            self.assertEqual(list(Path(store.blobs).rglob("*")), [])

    def test_digest_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalCAS(directory)
            with self.assertRaises(ValueError):
                store.open("../../etc/passwd")


if __name__ == "__main__":
    unittest.main()
