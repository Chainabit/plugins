from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

from .errors import ErrorCode, PdfError
from .models import SecurityPolicy

SAFE_URI_SCHEMES = {"https", "http", "mailto"}


def bounded_read(path: Path, policy: SecurityPolicy, root: Path | None = None) -> bytes:
    root = (root or policy.input_root).resolve()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PdfError(ErrorCode.FILESYSTEM_FAILURE, "source artifact cannot be resolved") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise PdfError(ErrorCode.UNSAFE_INPUT, "source path is outside the permitted input root")
    size = resolved.stat().st_size
    if size > policy.limits.max_input_bytes:
        raise PdfError(ErrorCode.RESOURCE_LIMIT, "source exceeds configured size limit")
    return resolved.read_bytes()


def safe_output(path: Path, policy: SecurityPolicy) -> Path:
    candidate = path.resolve(strict=False)
    root = policy.output_root.resolve()
    if candidate.suffix.lower() != ".pdf" or not candidate.is_relative_to(root):
        raise PdfError(ErrorCode.UNSAFE_INPUT, "output must be a .pdf inside the permitted output root")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def safe_asset_uri(uri: str, policy: SecurityPolicy) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme:
        if parsed.scheme in {"file", "data", "javascript"} or not policy.allow_remote_assets:
            raise PdfError(ErrorCode.UNSAFE_INPUT, "external or dangerous asset URI is disabled by policy")
        if parsed.scheme not in SAFE_URI_SCHEMES:
            raise PdfError(ErrorCode.UNSAFE_INPUT, "asset URI scheme is not allowed")
        return None
    return (policy.input_root / uri).resolve()


def reject_active_markup(text: str) -> None:
    # Markdown is converted by a deliberately restricted parser; raw HTML/CSS is
    # rejected rather than rendered by a browser engine with ambient file access.
    if re.search(r"<\s*(script|iframe|object|embed|svg|img|link|style)\b|on\w+\s*=|javascript:\s*", text, re.I):
        raise PdfError(ErrorCode.UNSAFE_INPUT, "active HTML, SVG, CSS, or event handlers are not accepted")

def validate_image(path: Path, policy: SecurityPolicy) -> tuple[str, int, int]:
    """Decode and bound an image before it crosses into a renderer."""
    raw = bounded_read(path, policy)
    if len(raw) > policy.limits.max_image_bytes:
        raise PdfError(ErrorCode.RESOURCE_LIMIT, "image exceeds configured size limit")
    try:
        from PIL import Image
        from io import BytesIO
        with Image.open(BytesIO(raw)) as image:
            image.verify()
        with Image.open(BytesIO(raw)) as image:
            width, height = image.size
            if width * height > policy.limits.max_image_pixels: raise PdfError(ErrorCode.RESOURCE_LIMIT, "image exceeds configured pixel limit")
            mime = Image.MIME.get(image.format)
            if not mime or mime not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                raise PdfError(ErrorCode.UNSUPPORTED_CAPABILITY, "image format is not supported by the audited image boundary")
            return mime, width, height
    except PdfError: raise
    except Exception as exc:
        raise PdfError(ErrorCode.INVALID_INPUT, "image is malformed or cannot be safely decoded") from exc
