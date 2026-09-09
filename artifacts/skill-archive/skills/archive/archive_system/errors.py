"""Stable error taxonomy; messages intentionally exclude member contents."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    UNSAFE_INPUT = "unsafe_input"
    RESOURCE_LIMIT = "resource_limit"
    SOURCE_NOT_FOUND = "source_not_found"
    SOURCE_EMPTY = "source_empty"
    UNSUPPORTED_ENTRY = "unsupported_entry"
    MALFORMED_ARTIFACT = "malformed_artifact"
    CORRUPTED_OUTPUT = "corrupted_output"
    FILESYSTEM_FAILURE = "filesystem_failure"
    VALIDATION_FAILURE = "validation_failure"


@dataclass(frozen=True)
class ArchiveError(Exception):
    code: ErrorCode
    message: str
    context: dict[str, str] | None = None

    def __str__(self) -> str:
        return self.message


# A caller can fix every one of these by asking for a different source, a
# different destination, or fewer bytes. Nothing about the host is wrong.
USER_INPUT_ERRORS = frozenset(
    {
        ErrorCode.INVALID_INPUT,
        ErrorCode.UNSAFE_INPUT,
        ErrorCode.RESOURCE_LIMIT,
        ErrorCode.SOURCE_NOT_FOUND,
        ErrorCode.SOURCE_EMPTY,
        ErrorCode.UNSUPPORTED_ENTRY,
    }
)

RETRYABLE_RUNTIME_ERRORS = frozenset({ErrorCode.FILESYSTEM_FAILURE})


def builder_exit_code(error: ArchiveError) -> int:
    """Stable CLI contract: 1=input rejection, 2=runtime/IO/output failure."""
    return 1 if error.code in USER_INPUT_ERRORS else 2


def failure_class(error: ArchiveError) -> str:
    if error.code in USER_INPUT_ERRORS:
        return "invalid_user_input"
    if error.code == ErrorCode.FILESYSTEM_FAILURE:
        return "filesystem_io_failure"
    if error.code in {
        ErrorCode.VALIDATION_FAILURE,
        ErrorCode.CORRUPTED_OUTPUT,
        ErrorCode.MALFORMED_ARTIFACT,
    }:
        return "produced_artifact_rejected"
    return "builder_process_failure"
