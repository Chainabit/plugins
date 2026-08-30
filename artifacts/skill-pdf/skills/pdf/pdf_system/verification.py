from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import ErrorCode, PdfError
from .models import Limits

@dataclass(frozen=True)
class Verification:
    bytes: int
    pages: int
    version: str
    warnings: tuple[str, ...] = ()

def verify_pdf(path: Path, limits: Limits, expected_pages: int | None = None) -> Verification:
    data = path.read_bytes() if path.exists() else b""
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
        raise PdfError(ErrorCode.CORRUPTED_OUTPUT, "output is not a complete PDF")
    if not 0 < len(data) <= limits.max_output_bytes: raise PdfError(ErrorCode.RESOURCE_LIMIT, "output violates size limit")
    # Page dictionaries emitted by core backend and most uncompressed producers.
    pages = len(re.findall(rb"/Type\s*/Page(?!s)", data))
    if pages == 0:
        count = re.findall(rb"/Count\s+(\d+)", data); pages = max((int(x) for x in count), default=0)
    if not 0 < pages <= limits.max_pages: raise PdfError(ErrorCode.VALIDATION_FAILURE, "PDF has no valid page count or exceeds page limit")
    if expected_pages is not None and pages != expected_pages: raise PdfError(ErrorCode.VALIDATION_FAILURE, "PDF page count differs from expectation")
    return Verification(len(data), pages, data[5:8].decode("ascii", "replace"))
