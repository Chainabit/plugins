from __future__ import annotations

import hashlib
import io
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
    sha256: str
    mime_type: str = "application/pdf"
    warnings: tuple[str, ...] = ()
    fonts: tuple[str, ...] = ()


# Operators which cause visible page content.  This is intentionally a
# content-presence contract, not a visual-design judgement.  pypdf expands
# compressed streams before this scan, so the check works for PDF object
# streams emitted by current WeasyPrint releases as well as classic xref PDFs.
_PAINTING_OPERATOR = re.compile(
    rb"(?:^|\s)(?:Tj|TJ|'|\"|Do|re|m|l|c|v|y|h|S|s|f|F|f\*|B|B\*|b|b\*|sh)(?:\s|$)"
)


def _page_is_painted(page: object) -> bool:
    try:
        contents = page.get_contents()  # type: ignore[attr-defined]
        if contents is None:
            return False
        payload = contents.get_data()
        return bool(_PAINTING_OPERATOR.search(payload))
    except Exception:
        # A parser that can enumerate the page but cannot decode its content
        # has not proved it blank.  Text/image extraction below may still
        # establish content; otherwise the caller records an inspection
        # warning instead of inventing a rejection.
        try:
            if (page.extract_text() or "").strip():  # type: ignore[attr-defined]
                return True
            return bool(getattr(page, "images", ()))
        except Exception:
            return True


def _font_evidence(page_objects: list[object]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    names: set[str] = set()
    unembedded: set[str] = set()
    for page in page_objects:
        try:
            resources = page.get("/Resources") or {}  # type: ignore[attr-defined]
            fonts = resources.get("/Font") or {}
            for font in fonts.values():
                font_object = font.get_object() if hasattr(font, "get_object") else font
                base = str(font_object.get("/BaseFont") or "unknown").lstrip("/")
                names.add(base)
                descendants = font_object.get("/DescendantFonts") or []
                candidates = [font_object] + [
                    item.get_object() if hasattr(item, "get_object") else item
                    for item in descendants
                ]
                embedded = False
                for candidate in candidates:
                    descriptor = candidate.get("/FontDescriptor") if hasattr(candidate, "get") else None
                    descriptor = descriptor.get_object() if hasattr(descriptor, "get_object") else descriptor
                    if descriptor and any(descriptor.get(key) is not None for key in ("/FontFile", "/FontFile2", "/FontFile3")):
                        embedded = True
                        break
                if not embedded:
                    unembedded.add(base)
        except Exception:
            continue
    return tuple(sorted(names)), tuple(sorted(unembedded))


def verify_pdf(
    path: Path,
    limits: Limits,
    expected_pages: int | None = None,
    *,
    strict: bool = False,
) -> Verification:
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise PdfError(ErrorCode.INVALID_INPUT, "PDF input does not exist") from exc
    except OSError as exc:
        raise PdfError(ErrorCode.FILESYSTEM_FAILURE, "PDF input could not be read") from exc
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
        raise PdfError(ErrorCode.CORRUPTED_OUTPUT, "output is not a complete PDF")
    if not 0 < len(data) <= limits.max_output_bytes:
        raise PdfError(ErrorCode.RESOURCE_LIMIT, "output violates size limit")

    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:
        raise PdfError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "authoritative PDF verification requires pypdf",
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise PdfError(
                ErrorCode.VALIDATION_FAILURE,
                "encrypted PDFs are not supported for artifact delivery",
            )
        page_objects = list(reader.pages)
    except PdfError:
        raise
    except (PdfReadError, ValueError, TypeError) as exc:
        raise PdfError(
            ErrorCode.CORRUPTED_OUTPUT, "PDF structure could not be parsed"
        ) from exc
    except Exception as exc:
        raise PdfError(
            ErrorCode.VALIDATION_FAILURE, "PDF structure could not be verified"
        ) from exc

    pages = len(page_objects)
    if not 0 < pages <= limits.max_pages:
        raise PdfError(
            ErrorCode.VALIDATION_FAILURE,
            "PDF has no valid page count or exceeds page limit",
        )
    if expected_pages is not None and pages != expected_pages:
        raise PdfError(
            ErrorCode.VALIDATION_FAILURE,
            "PDF page count differs from expectation",
        )

    blank_pages = tuple(
        index for index, page in enumerate(page_objects, 1) if not _page_is_painted(page)
    )
    if len(blank_pages) == pages:
        raise PdfError(
            ErrorCode.VALIDATION_FAILURE,
            "PDF is structurally readable but every page is blank",
        )
    if strict and blank_pages:
        raise PdfError(
            ErrorCode.VALIDATION_FAILURE,
            "PDF contains one or more blank pages",
        )
    fonts, unembedded_fonts = _font_evidence(page_objects)
    if unembedded_fonts:
        raise PdfError(
            ErrorCode.VALIDATION_FAILURE,
            "PDF references non-embedded fonts: " + ", ".join(unembedded_fonts),
        )
    warnings = (
        ("blank_pages=" + ",".join(str(page) for page in blank_pages),)
        if blank_pages
        else ()
    )
    return Verification(
        len(data),
        pages,
        data[5:8].decode("ascii", "replace"),
        hashlib.sha256(data).hexdigest(),
        warnings=warnings,
        fonts=fonts,
    )
