from __future__ import annotations

import base64
import binascii
import os
import re
from pathlib import Path
from typing import Iterator, Sequence
from urllib.parse import urlparse

from .errors import ErrorCode, PdfError
from .models import SecurityPolicy

SAFE_URI_SCHEMES = {"https", "http", "mailto"}

# An image reaches a PDF only through the audited image boundary: a Markdown
# image or a report `image` block naming a file under the input root. Two other
# spellings arrive in practice, and neither is an image to this skill. A raw
# HTML <img> or <svg> tag is not markup here: Markdown rejects raw HTML, and a
# report escapes every text field. An inline data: URI is never an accepted
# asset (see safe_asset_uri). Once escaped and printed, either one turns a chart
# into pages of base64 characters inside an otherwise well-formed PDF. So the
# input boundary rejects both spellings, and verification rejects any PDF that
# prints image data as text.
IMAGE_FILE_FORMATS = "PNG, JPEG, GIF or WebP"
RAW_IMAGE_TAG = re.compile(r"<\s*(img|svg)\b", re.I)
_DATA_IMAGE_PREFIX = re.compile(r"data:\s*image/\s*[a-z0-9.+-]+\s*;\s*base64\s*,\s*", re.I)
# Text extraction returns a wrapped token one line at a time. Join across line
# breaks, never across spaces, so ordinary words never merge into a run.
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]+(?:[ \t]*\r?\n[ \t]*[A-Za-z0-9+/]+)*=*")
_WHITESPACE = re.compile(r"\s+")
# Long enough for an illustrative snippet in prose to pass (a complete 1x1 PNG
# is 92 characters), and short enough that no real picture fits under it.
IMAGE_PAYLOAD_MIN_CHARS = 256
# Shorter runs are words, page numbers and footer marks, not payload.
_PAYLOAD_RUN_MIN_CHARS = 16
_IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"<svg")


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


def _compact(run: str) -> str:
    return _WHITESPACE.sub("", run)


def _starts_with_image(run: str) -> bool:
    try:
        head = base64.b64decode(run[:16], validate=True)
    except (binascii.Error, ValueError):
        return False
    return head.startswith(_IMAGE_SIGNATURES) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")


# Base64 of the first bytes of a PNG, JPEG, GIF, RIFF (WebP) or SVG file,
# where a token starts. A run can also begin with the last word of the line
# before the payload, so its own start is not a reliable payload start.
_IMAGE_BASE64_START = re.compile(r"(?<![A-Za-z0-9+/])(?:iVBORw0KGgo|/9j/|R0lGOD[dl]h|UklGR|PHN2Zy)")


def _payload_starts(text: str) -> Iterator[int]:
    for prefix in _DATA_IMAGE_PREFIX.finditer(text):
        yield prefix.end()
    for signature in _IMAGE_BASE64_START.finditer(text):
        run = _BASE64_RUN.match(text, signature.start())
        compact = _compact(run.group()) if run else ""
        if len(compact) >= _PAYLOAD_RUN_MIN_CHARS and _starts_with_image(compact):
            yield signature.start()


def _payload_run_sizes(text: str, start: int = 0) -> Iterator[int]:
    for run in _BASE64_RUN.finditer(text, start):
        size = len(_compact(run.group()))
        if size >= _PAYLOAD_RUN_MIN_CHARS:
            yield size


def image_payload_page(pages: Sequence[str]) -> int | None:
    """Return the 1-based page whose text first spells out image data, or None.

    A payload is a `data:image/...;base64,` prefix, or a base64 run whose first
    bytes are a PNG, JPEG, GIF, WebP or SVG signature, followed by at least
    IMAGE_PAYLOAD_MIN_CHARS base64 characters. A payload that starts on the
    last lines of a page continues on the next page, after that page's number
    and footer, so the next page's first run counts toward its length.
    """
    for index, text in enumerate(pages):
        for start in _payload_starts(text):
            run = _BASE64_RUN.match(text, start)
            size = len(_compact(run.group())) if run else 0
            end = run.end() if run else start
            if size < IMAGE_PAYLOAD_MIN_CHARS and index + 1 < len(pages) and next(_payload_run_sizes(text, end), None) is None:
                size += next(_payload_run_sizes(pages[index + 1]), 0)
            if size >= IMAGE_PAYLOAD_MIN_CHARS:
                return index + 1
    return None


def image_as_text(text: str) -> str | None:
    """Name the spelling in author text that would print an image as characters."""
    tag = RAW_IMAGE_TAG.search(text)
    if tag:
        return f"a raw HTML <{tag.group(1).lower()}> tag"
    if image_payload_page([text]) is not None:
        return "inline base64 image data"
    return None


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
