"""Atomic streaming SHA-256 content-addressed storage for a local appliance."""
from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
from pathlib import Path
from typing import BinaryIO, Iterable

from .protocol import StoredBlob

_DIGEST = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")


def _hex(digest: str) -> str:
    match = _DIGEST.fullmatch(digest)
    if not match:
        raise ValueError("digest must be sha256:<64 lowercase hex>")
    return match.group(1)


class _BoundedReader(io.RawIOBase):
    def __init__(self, stream: BinaryIO, remaining: int | None) -> None:
        self._stream = stream
        self._remaining = remaining

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if self._remaining == 0:
            return b""
        if self._remaining is not None:
            size = self._remaining if size < 0 else min(size, self._remaining)
        data = self._stream.read(size)
        if self._remaining is not None:
            self._remaining -= len(data)
        return data

    def close(self) -> None:
        self._stream.close()
        super().close()


class _PendingLocalWrite:
    def __init__(self, store: "LocalCAS") -> None:
        self._store = store
        fd, raw_path = tempfile.mkstemp(prefix="upload-", suffix=".partial", dir=store.staging)
        self._path = Path(raw_path)
        self._stream = os.fdopen(fd, "wb")
        self._hash = hashlib.sha256()
        self._size = 0
        self._closed = False

    def write(self, chunk: bytes) -> int:
        if self._closed:
            raise ValueError("write session is closed")
        if not isinstance(chunk, bytes):
            raise TypeError("artifact chunks must be bytes")
        written = self._stream.write(chunk)
        self._hash.update(chunk[:written])
        self._size += written
        return written

    def commit(self) -> StoredBlob:
        if self._closed:
            raise ValueError("write session is closed")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._stream.close()
        self._closed = True
        digest_hex = self._hash.hexdigest()
        target = self._store._path(digest_hex)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._path.unlink()
            if target.stat().st_size != self._size:
                raise IOError("content-address collision has a different stored size")
        else:
            os.replace(self._path, target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return StoredBlob(
            digest=f"sha256:{digest_hex}",
            size_bytes=self._size,
            locator=str(target),
            backend=self._store.kind,
        )

    def abort(self) -> None:
        if not self._closed:
            self._stream.close()
            self._closed = True
        self._path.unlink(missing_ok=True)

    def __enter__(self) -> "_PendingLocalWrite":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is not None:
            self.abort()
        elif not self._closed:
            self.abort()
        return False


class LocalCAS:
    kind = "local_cas"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.blobs = self.root / "blobs" / "sha256"
        self.staging = self.root / ".staging"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        value = _hex(digest)
        return self.blobs / value[:2] / value[2:4] / value

    def begin(self) -> _PendingLocalWrite:
        return _PendingLocalWrite(self)

    def put_chunks(self, chunks: Iterable[bytes]) -> StoredBlob:
        pending = self.begin()
        try:
            for chunk in chunks:
                pending.write(chunk)
            return pending.commit()
        except BaseException:
            pending.abort()
            raise

    def open(self, digest: str, *, offset: int = 0, length: int | None = None) -> BinaryIO:
        if offset < 0 or (length is not None and length < 0):
            raise ValueError("offset and length must be non-negative")
        path = self._path(digest)
        stream = path.open("rb")
        size = path.stat().st_size
        if offset > size:
            stream.close()
            raise ValueError("offset exceeds artifact size")
        stream.seek(offset)
        return _BoundedReader(stream, length)

    def iter_bytes(
        self, digest: str, *, offset: int = 0, length: int | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> Iterable[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        with self.open(digest, offset=offset, length=length) as stream:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    def verify(self, digest: str) -> StoredBlob:
        expected = _hex(digest)
        hasher = hashlib.sha256()
        size = 0
        for chunk in self.iter_bytes(expected):
            hasher.update(chunk)
            size += len(chunk)
        if hasher.hexdigest() != expected:
            raise IOError(f"artifact digest verification failed for sha256:{expected}")
        path = self._path(expected)
        return StoredBlob(
            digest=f"sha256:{expected}",
            size_bytes=size,
            locator=str(path),
            backend=self.kind,
        )
