#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check that a generated PDF is actually a PDF, and that it is not blank.

The failure this exists to catch is the quiet one: a renderer exits 0, a file
appears at the expected path, and it is four kilobytes of structurally valid PDF
containing nothing. Reporting "done" on that is worse than reporting an error, so
every generation run should end here.

Deliberately stdlib-only (re + zlib). It reads the file's own structure rather
than importing a PDF library, which keeps it working regardless of which renderer
produced the file.

Usage:
    python3 validate_pdf.py output.pdf [--strict]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zlib

OBJECT_HEADER = re.compile(rb"(?:^|[\r\n\s])(\d+)\s+(\d+)\s+obj\b")
# /Type /Page but not /Type /Pages — the tree node is not a page.
PAGE_TYPE = re.compile(rb"/Type\s*/Page(?![sA-Za-z])")
PAGES_COUNT = re.compile(rb"/Count\s+(\d+)")
CONTENTS_REF = re.compile(rb"/Contents\s+(\d+)\s+(\d+)\s+R")
CONTENTS_ARRAY = re.compile(rb"/Contents\s*\[([^\]]*)\]")
ARRAY_REF = re.compile(rb"(\d+)\s+(\d+)\s+R")

# Content-stream operators that put something on the page. If a decoded stream
# contains none of these, the page is blank no matter how many bytes it has.
PAINTING_OPERATORS = (
    b"Tj", b"TJ", b"'", b'"',     # text showing
    b"Do",                        # XObject (images, forms)
    b" re", b"\nre",              # rectangles
    b" l\n", b" l ", b" c\n", b" c ",  # lines and curves
    b"sh",                        # shadings
)


def find_objects(data: bytes) -> dict[int, int]:
    """Map object number -> byte offset of its `obj` keyword."""
    objects: dict[int, int] = {}
    for match in OBJECT_HEADER.finditer(data):
        objects[int(match.group(1))] = match.end()
    return objects


def object_body(data: bytes, offset: int) -> bytes:
    end = data.find(b"endobj", offset)
    return data[offset:end] if end != -1 else data[offset:offset + 65536]


def stream_payload(body: bytes) -> bytes | None:
    """Return the decoded content stream of an object body, or None if undecodable."""
    start = body.find(b"stream")
    if start == -1:
        return None

    dictionary = body[:start]
    cursor = start + len(b"stream")
    # The spec allows CRLF or LF after the keyword; nothing else.
    if body[cursor:cursor + 2] == b"\r\n":
        cursor += 2
    elif body[cursor:cursor + 1] in (b"\n", b"\r"):
        cursor += 1

    end = body.find(b"endstream", cursor)
    if end == -1:
        return None
    raw = body[cursor:end]

    if b"/FlateDecode" in dictionary:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return None
    if b"/Filter" in dictionary:
        # Some other filter (LZW, DCT, ...). Not worth implementing; treat the
        # page as uninspectable rather than guessing it is blank.
        return None
    return raw


def expand_object_streams(data: bytes, objects: dict[int, int]) -> list[bytes]:
    """
    Decode every `/Type /ObjStm` payload in the file.

    PDF 1.5 onwards may pack most indirect objects — page objects and the page
    tree among them — into compressed object streams. WeasyPrint does exactly
    this, so a scan of the raw bytes finds neither `/Type /Page` nor the page
    tree's `/Count`, and a validator that only reads the clear text concludes a
    perfectly good document has no pages. That false negative is worse than no
    check at all: it tells the model its output failed when the file it just
    wrote is fine.
    """
    payloads: list[bytes] = []
    for offset in objects.values():
        body = object_body(data, offset)
        if b"/ObjStm" not in body[: body.find(b"stream") if b"stream" in body else len(body)]:
            continue
        decoded = stream_payload(body)
        if decoded:
            payloads.append(decoded)
    return payloads


# Objects inside an ObjStm are concatenated with no `obj`/`endobj` keywords, so
# a page's dictionary cannot be delimited exactly. A window around the match is
# enough to catch the `/Contents` reference that follows it, and the content
# streams themselves stay outside the ObjStm where they can still be resolved.
PACKED_PAGE_WINDOW = 2048


def packed_page_bodies(payloads: list[bytes]) -> list[bytes]:
    bodies: list[bytes] = []
    for payload in payloads:
        for match in PAGE_TYPE.finditer(payload):
            start = max(0, match.start() - PACKED_PAGE_WINDOW // 2)
            bodies.append(payload[start : match.end() + PACKED_PAGE_WINDOW])
    return bodies


def content_object_numbers(page_body: bytes) -> list[int]:
    direct = CONTENTS_REF.search(page_body)
    if direct:
        return [int(direct.group(1))]

    array = CONTENTS_ARRAY.search(page_body)
    if array:
        return [int(m.group(1)) for m in ARRAY_REF.finditer(array.group(1))]

    return []


def looks_painted(content: bytes) -> bool:
    return any(operator in content for operator in PAINTING_OPERATORS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_pdf.py",
        description=(
            "Verify a PDF exists, is structurally a PDF, report its page count, and "
            "warn about pages that appear blank."
        ),
        epilog=(
            "Example:\n"
            "  python3 validate_pdf.py report.pdf\n\n"
            "Exit code 0 means the file is a usable PDF. Exit code 1 means do not "
            "hand it to the user.\n"
            "Runs offline, stdlib only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pdf", help="path to the .pdf file to check")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat blank-page warnings as failures (exit 1)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.pdf

    if not os.path.exists(path):
        print(f"ERROR: file: {path} does not exist", file=sys.stderr)
        return 1
    if os.path.isdir(path):
        print(f"ERROR: file: {path} is a directory, expected a PDF file", file=sys.stderr)
        return 1

    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except PermissionError:
        print(f"ERROR: file: no permission to read {path}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: file: could not read {path}: {exc}", file=sys.stderr)
        return 1

    size = len(data)
    if size == 0:
        print(f"ERROR: file: {path} is empty (0 bytes)", file=sys.stderr)
        return 1

    if not data.startswith(b"%PDF-"):
        head = data[:16]
        print(
            f"ERROR: header: {path} does not begin with %PDF- (found {head!r}). "
            "Whatever produced this wrote something that is not a PDF.",
            file=sys.stderr,
        )
        return 1

    version = data[5:8].decode("ascii", "replace")

    # The trailer marker is allowed trailing whitespace after it, so search the tail.
    if b"%%EOF" not in data[-2048:]:
        print(
            f"ERROR: trailer: {path} has no %%EOF marker near the end of the file — "
            "it is truncated, and most readers will refuse it.",
            file=sys.stderr,
        )
        return 1

    objects = find_objects(data)

    # (label, dictionary bytes) for every page, from wherever it was stored. The
    # label is the object number when it is known and a position otherwise; it
    # only ever appears in a message.
    pages: list[tuple[str, bytes]] = []
    for number, offset in sorted(objects.items(), key=lambda pair: pair[1]):
        body = object_body(data, offset)
        if PAGE_TYPE.search(body):
            pages.append((str(number), body))

    if not pages:
        for index, body in enumerate(packed_page_bodies(expand_object_streams(data, objects)), 1):
            pages.append((f"#{index}", body))

    page_count = len(pages)
    inspectable = 0
    blank_pages = []

    if page_count == 0:
        # Last resort before failing: the page tree's own /Count, in case the
        # pages live behind a filter this script does not decode.
        counts = [int(m.group(1)) for m in PAGES_COUNT.finditer(data)]
        if counts:
            page_count = max(counts)
            print(
                "NOTE: page objects could not be decoded; page count read from "
                "the page tree and blank-page detection skipped."
            )
        else:
            print(
                f"ERROR: pages: no page objects found in {path} — the document has "
                "no pages.",
                file=sys.stderr,
            )
            return 1
    else:
        for label, page_body in pages:
            content_numbers = content_object_numbers(page_body)

            if not content_numbers:
                blank_pages.append(label)
                inspectable += 1
                continue

            payloads = []
            undecodable = False
            for content_number in content_numbers:
                content_offset = objects.get(content_number)
                if content_offset is None:
                    undecodable = True
                    continue
                payload = stream_payload(object_body(data, content_offset))
                if payload is None:
                    undecodable = True
                else:
                    payloads.append(payload)

            if not payloads:
                if not undecodable:
                    blank_pages.append(label)
                    inspectable += 1
                continue

            inspectable += 1
            if not looks_painted(b"".join(payloads)):
                blank_pages.append(label)

    # The summary is deliberately printed last. An "OK:" line above an ERROR line
    # reads as a pass with a footnote, which is exactly the wrong impression.
    if blank_pages:
        if inspectable and len(blank_pages) == inspectable:
            print(
                f"ERROR: pages: every inspected page ({len(blank_pages)}) is blank — "
                "the file is structurally valid but contains nothing. Check that the "
                "source content actually reached the renderer.",
                file=sys.stderr,
            )
            return 1
        print(
            f"WARNING: {len(blank_pages)} page(s) appear blank "
            f"(object(s) {', '.join(str(n) for n in blank_pages)}).",
            file=sys.stderr,
        )
        if args.strict:
            return 1

    print(f"OK: {path} is a PDF {version} file, {size} bytes, {page_count} page(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
