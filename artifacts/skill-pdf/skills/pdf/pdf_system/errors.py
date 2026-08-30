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
