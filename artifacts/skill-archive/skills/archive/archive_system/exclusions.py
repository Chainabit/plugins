"""Which paths the host has asked to be left out of a package.

The rules arrive at run time, as an argument. This package never decides that
a name is the host's rather than the caller's -- it cannot know, and guessing
is how a caller's own directory gets silently dropped from their deliverable.
It only applies the two rules it is given, faithfully.
"""
from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass


@dataclass(frozen=True)
class ExclusionRules:
    """Two rules, because the caller distinguishes two different things.

    A DIRECTORY name is matched exactly, so a caller's own ``reports-legacy``
    survives a rule naming ``reports``.

    A NAMESPACE also matches any name that ENTERS it at a word boundary, so a
    rule naming ``vendor`` also covers ``vendor-build.json`` while leaving
    ``vendorish`` alone. Conflating the two silently drops caller content, so
    they stay separate all the way down.
    """

    directories: frozenset[str] = frozenset()
    namespaces: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: object) -> "ExclusionRules":
        if not isinstance(raw, dict):
            return cls()
        directories = raw.get("directories") or ()
        namespaces = raw.get("namespaces") or ()
        return cls(
            directories=frozenset(str(name) for name in directories),
            namespaces=tuple(str(name) for name in namespaces),
        )

    def matches_segment(self, segment: str) -> bool:
        if segment in self.directories:
            return True
        for namespace in self.namespaces:
            if segment == namespace:
                return True
            if segment.startswith(namespace):
                rest = segment[len(namespace) :]
                if rest and not (rest[0].isalnum() or rest[0] == "_"):
                    return True
        return False

    def excludes(self, relative: str) -> bool:
        """True when ANY positioned segment matches.

        Checking only the leading segment let an excluded directory through as
        soon as it was nested one level down, and the package then carried a
        file the host had explicitly asked to keep out of a download.
        """
        return any(
            self.matches_segment(segment)
            for segment in relative.replace(os.sep, "/").split("/")
            if segment not in ("", ".")
        )


def is_safe_member_name(name: str) -> bool:
    """Whether a member name stays inside the archive when it is extracted."""
    normalized = posixpath.normpath(name.replace(os.sep, "/"))
    return (
        normalized not in ("", ".", "..")
        and not normalized.startswith("../")
        and not normalized.startswith("/")
    )
