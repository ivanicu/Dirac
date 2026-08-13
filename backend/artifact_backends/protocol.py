"""Narrow streaming capabilities injected into scientific workers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Iterable, Protocol


@dataclass(frozen=True)
class StoredBlob:
    digest: str
    size_bytes: int
    locator: str
    backend: str


class PendingWrite(Protocol):
    def write(self, chunk: bytes) -> int: ...
    def commit(self) -> StoredBlob: ...
    def abort(self) -> None: ...


class ArtifactReader(Protocol):
    def open(self, digest: str, *, offset: int = 0, length: int | None = None) -> BinaryIO: ...
    def iter_bytes(
        self, digest: str, *, offset: int = 0, length: int | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> Iterable[bytes]: ...
    def verify(self, digest: str) -> StoredBlob: ...


class ArtifactWriter(Protocol):
    def begin(self) -> PendingWrite: ...


class CheckpointWriter(ArtifactWriter, Protocol):
    """A separate capability so workers cannot mistake partial state for output."""


__all__ = [
    "ArtifactReader", "ArtifactWriter", "CheckpointWriter", "PendingWrite", "StoredBlob"
]
