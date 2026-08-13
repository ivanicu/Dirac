"""Fail-closed inspection for untrusted scientific archives before extraction."""
from __future__ import annotations

from pathlib import PurePosixPath
import stat
import tarfile
import zipfile


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def inspect_archive(path, *, maximum_files: int = 10000,
                    maximum_uncompressed_bytes: int = 2 << 30,
                    maximum_compression_ratio: float = 200.0) -> dict:
    entries = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                mode = info.external_attr >> 16
                entries.append((info.filename, info.file_size, info.compress_size,
                                stat.S_ISLNK(mode)))
        kind = "zip"
    elif tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as archive:
            for info in archive.getmembers():
                entries.append((info.name, info.size, max(info.size, 1),
                                info.issym() or info.islnk()))
        kind = "tar"
    else:
        raise ValueError("unsupported or malformed archive")
    if len(entries) > maximum_files:
        raise ValueError("archive file-count limit exceeded")
    total, compressed = 0, 0
    for name, size, stored, is_link in entries:
        if not _safe_name(name):
            raise ValueError(f"archive path traversal rejected: {name!r}")
        if is_link:
            raise ValueError(f"archive link entry rejected: {name!r}")
        total += int(size)
        compressed += max(int(stored), 1)
    if total > maximum_uncompressed_bytes:
        raise ValueError("archive uncompressed-size limit exceeded")
    ratio = total / max(compressed, 1)
    if ratio > maximum_compression_ratio:
        raise ValueError("archive compression-ratio limit exceeded")
    return {"archive_kind": kind, "file_count": len(entries),
            "uncompressed_bytes": total, "compression_ratio": ratio,
            "safe_to_extract_with_bounded_extractor": True}


__all__ = ["inspect_archive"]
