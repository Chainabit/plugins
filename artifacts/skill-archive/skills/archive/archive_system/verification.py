"""Reading a finished package back, and saying exactly what it is.

Shared by the builder and the validator on purpose: a claim the builder makes
about its own output and a claim the validator makes about the same file must
come from one implementation, or the two can disagree about one artifact.
"""
from __future__ import annotations

import hashlib
import os
import zipfile
from dataclasses import dataclass

from .errors import ArchiveError, ErrorCode
from .exclusions import is_safe_member_name
from .packaging import Limits

MIME_TYPE = "application/zip"
_READ_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ArchiveFacts:
    path: str
    sha256: str
    bytes: int
    entry_count: int
    entries: tuple[str, ...]
    mime_type: str = MIME_TYPE


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(path: str, limits: Limits) -> ArchiveFacts:
    """Reopen a written package and prove it reads back before reporting it.

    Every check here is about the container that now exists on disk, not about
    the selection that produced it. A builder that reports success without
    reopening its own output is reporting an intention.
    """
    absolute = os.path.abspath(path)
    if not os.path.isfile(absolute):
        raise ArchiveError(ErrorCode.CORRUPTED_OUTPUT, "archive was not written")

    try:
        with zipfile.ZipFile(absolute) as archive:
            broken = archive.testzip()
            infos = archive.infolist()
    except zipfile.BadZipFile as error:
        raise ArchiveError(
            ErrorCode.MALFORMED_ARTIFACT, "archive is not a readable container"
        ) from error

    if broken is not None:
        raise ArchiveError(
            ErrorCode.CORRUPTED_OUTPUT,
            "archive contains a corrupt member",
            {"member": broken},
        )

    names = [info.filename for info in infos]
    if any(not is_safe_member_name(name) for name in names):
        raise ArchiveError(
            ErrorCode.UNSAFE_INPUT,
            "archive contains a member path that would escape extraction",
        )

    expanded_bytes = sum(info.file_size for info in infos)
    if expanded_bytes > limits.max_total_bytes:
        raise ArchiveError(
            ErrorCode.RESOURCE_LIMIT,
            "archive expands beyond the total size limit",
            {"expandedBytes": str(expanded_bytes)},
        )

    compressed_bytes = max(1, sum(info.compress_size for info in infos))
    if expanded_bytes / compressed_bytes > limits.max_expansion_ratio:
        raise ArchiveError(
            ErrorCode.RESOURCE_LIMIT,
            "archive expansion ratio exceeds the permitted maximum",
            {"expandedBytes": str(expanded_bytes)},
        )

    written_bytes = os.path.getsize(absolute)
    if written_bytes > limits.max_file_bytes:
        raise ArchiveError(
            ErrorCode.RESOURCE_LIMIT,
            "archive exceeds the per-file size limit",
            {"bytes": str(written_bytes)},
        )

    return ArchiveFacts(
        path=absolute,
        sha256=sha256_of(absolute),
        bytes=written_bytes,
        entry_count=len(names),
        entries=tuple(names),
    )
