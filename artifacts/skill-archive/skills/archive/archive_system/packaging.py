"""Selecting what goes into a package, and writing it deterministically."""
from __future__ import annotations

import os
import stat
import zipfile
from dataclasses import dataclass

from .errors import ArchiveError, ErrorCode
from .exclusions import ExclusionRules, is_safe_member_name


@dataclass(frozen=True)
class Limits:
    """Bounds the caller is held to, supplied by the host at run time."""

    max_files: int
    max_total_bytes: int
    max_file_bytes: int

    # A package that decompresses to far more than it occupies is the classic
    # way to hand a recipient a file that costs them far more to open than it
    # cost to send. Checked after the fact, against the written container.
    max_expansion_ratio: int = 100


@dataclass(frozen=True)
class Member:
    """One regular file, and the path it will occupy inside the archive."""

    source: str
    name: str


def collect_members(
    source: str, destination: str, rules: ExclusionRules
) -> list[Member]:
    """Every file the package should carry, in a deterministic order.

    Ordering is by sorted directory and file name, so the same tree always
    produces the same archive. Symlinks are refused rather than followed: a
    link resolves somewhere the caller may not have meant, and a package is
    the wrong place to discover that.
    """
    if not os.path.exists(source):
        raise ArchiveError(ErrorCode.SOURCE_NOT_FOUND, "source path does not exist")
    if os.path.islink(source):
        raise ArchiveError(ErrorCode.UNSAFE_INPUT, "source path is a symbolic link")

    destination_absolute = os.path.abspath(destination)

    if os.path.isfile(source):
        return [Member(os.path.abspath(source), os.path.basename(source))]

    root = os.path.abspath(source)
    members: list[Member] = []
    for directory, subdirectories, filenames in os.walk(root):
        # Pruned in place, so the walk never descends into an excluded tree at
        # all rather than filtering its files one by one afterwards.
        kept: list[str] = []
        for name in sorted(subdirectories):
            path = os.path.join(directory, name)
            if os.path.islink(path):
                raise ArchiveError(
                    ErrorCode.UNSAFE_INPUT,
                    "source contains a symbolic link",
                    {"member": os.path.relpath(path, root)},
                )
            if not rules.excludes(os.path.relpath(path, root)):
                kept.append(name)
        subdirectories[:] = kept

        for name in sorted(filenames):
            path = os.path.join(directory, name)
            # The archive must never contain itself, however it was addressed.
            if os.path.abspath(path) == destination_absolute:
                continue
            if os.path.islink(path):
                raise ArchiveError(
                    ErrorCode.UNSAFE_INPUT,
                    "source contains a symbolic link",
                    {"member": os.path.relpath(path, root)},
                )
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise ArchiveError(
                    ErrorCode.UNSUPPORTED_ENTRY,
                    "source contains an entry that is not a regular file",
                    {"member": os.path.relpath(path, root)},
                )
            member_name = os.path.relpath(path, root)
            if rules.excludes(member_name):
                continue
            members.append(Member(path, member_name))

    return members


def enforce_limits(members: list[Member], limits: Limits) -> None:
    """Refuse a selection that is too large before any bytes are written."""
    if not members:
        raise ArchiveError(ErrorCode.SOURCE_EMPTY, "source selected no files")
    if len(members) > limits.max_files:
        raise ArchiveError(
            ErrorCode.RESOURCE_LIMIT,
            "source contains more files than a package may carry",
            {"entryCount": str(len(members))},
        )

    total_bytes = 0
    for member in members:
        if not is_safe_member_name(member.name):
            raise ArchiveError(
                ErrorCode.UNSAFE_INPUT,
                "member path would escape the archive when extracted",
                {"member": member.name},
            )
        # Re-checked immediately before the size is trusted: the walk and this
        # loop are separated in time, and a path that became a link in between
        # must not be measured as a file and then written as one.
        if os.path.islink(member.source) or not stat.S_ISREG(
            os.lstat(member.source).st_mode
        ):
            raise ArchiveError(
                ErrorCode.UNSAFE_INPUT,
                "member is no longer a regular file",
                {"member": member.name},
            )
        size = os.path.getsize(member.source)
        if size > limits.max_file_bytes:
            raise ArchiveError(
                ErrorCode.RESOURCE_LIMIT,
                "a single file exceeds the per-file limit",
                {"member": member.name, "bytes": str(size)},
            )
        total_bytes += size
        if total_bytes > limits.max_total_bytes:
            raise ArchiveError(
                ErrorCode.RESOURCE_LIMIT,
                "selected files exceed the total size limit",
                {"expandedBytes": str(total_bytes)},
            )


def write_archive(members: list[Member], destination: str) -> None:
    """Write the container. The caller owns removing it if anything later fails."""
    destination_absolute = os.path.abspath(destination)
    parent = os.path.dirname(destination_absolute)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with zipfile.ZipFile(destination_absolute, "w", zipfile.ZIP_DEFLATED) as archive:
        for member in members:
            archive.write(member.source, member.name)
