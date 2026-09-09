"""The one place a package's whole lifecycle is owned end to end."""
from __future__ import annotations

import os

from .errors import ArchiveError, ErrorCode
from .exclusions import ExclusionRules
from .packaging import Limits, collect_members, enforce_limits, write_archive
from .verification import ArchiveFacts, verify_archive


class ArchiveService:
    """Select, write, verify -- and leave nothing behind if any step fails.

    The partial-output rule is why this is a service rather than three loose
    calls. A container that was created and then failed verification is worse
    than no container at all: it is a plausible-looking file that a later step
    may promote. It is removed here, by the same object that created it, so no
    caller has to remember to.
    """

    def __init__(self, rules: ExclusionRules, limits: Limits) -> None:
        self._rules = rules
        self._limits = limits

    def build(self, source: str, destination: str) -> ArchiveFacts:
        members = collect_members(source, destination, self._rules)
        enforce_limits(members, self._limits)

        created = False
        try:
            write_archive(members, destination)
            created = True
            return verify_archive(destination, self._limits)
        except OSError as error:
            self._discard(destination, created)
            raise ArchiveError(
                ErrorCode.FILESYSTEM_FAILURE, "archive could not be written"
            ) from error
        except ArchiveError:
            self._discard(destination, created)
            raise

    @staticmethod
    def _discard(destination: str, created: bool) -> None:
        if not created:
            return
        absolute = os.path.abspath(destination)
        try:
            if os.path.isfile(absolute):
                os.unlink(absolute)
        except OSError:
            # The original failure is the one worth reporting; a failure to
            # clean up must not replace it with a less useful message.
            pass
