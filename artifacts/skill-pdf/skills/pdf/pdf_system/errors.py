"""Stable error taxonomy; messages intentionally exclude input contents."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    UNSAFE_INPUT = "unsafe_input"
    RESOURCE_LIMIT = "resource_limit"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    DEPENDENCY_FAILURE = "dependency_failure"
    TIMEOUT = "timeout"
    MALFORMED_ARTIFACT = "malformed_artifact"
    RENDERING_FAILURE = "rendering_failure"
    VALIDATION_FAILURE = "validation_failure"
    FILESYSTEM_FAILURE = "filesystem_failure"
    CORRUPTED_OUTPUT = "corrupted_output"
    FONT_FAILURE = "font_failure"
    COLOR_INCOMPATIBILITY = "color_incompatibility"


@dataclass(frozen=True)
class PdfError(Exception):
    code: ErrorCode
    message: str
    context: dict[str, str] | None = None

    def __str__(self) -> str:
        return self.message


USER_INPUT_ERRORS = frozenset(
    {
        ErrorCode.INVALID_INPUT,
        ErrorCode.UNSAFE_INPUT,
        ErrorCode.RESOURCE_LIMIT,
        ErrorCode.UNSUPPORTED_CAPABILITY,
    }
)

RETRYABLE_RUNTIME_ERRORS = frozenset(
    {
        ErrorCode.TIMEOUT,
        ErrorCode.FILESYSTEM_FAILURE,
    }
)


def renderer_exit_code(error: PdfError) -> int:
    """Stable renderer CLI contract: 1=input, 2=runtime/internal/output."""
    return 1 if error.code in USER_INPUT_ERRORS else 2


def failure_class(error: PdfError) -> str:
    if error.code in USER_INPUT_ERRORS:
        return "invalid_user_input"
    if error.code == ErrorCode.DEPENDENCY_UNAVAILABLE:
        return "missing_runtime_dependency"
    if error.code == ErrorCode.TIMEOUT:
        return "renderer_timeout"
    if error.code in {ErrorCode.FILESYSTEM_FAILURE}:
        return "filesystem_io_failure"
    if error.code in {
        ErrorCode.VALIDATION_FAILURE,
        ErrorCode.CORRUPTED_OUTPUT,
        ErrorCode.MALFORMED_ARTIFACT,
    }:
        return "produced_artifact_rejected"
    return "renderer_process_failure"
