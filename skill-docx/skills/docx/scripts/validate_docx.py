#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check that a generated .docx is a real Word document, and that it says something.

Three failures matter here and none is visible from the file listing:

1. The file is not a Word document at all. A model asked for a .docx that writes
   Markdown text and names it `resume.docx` produces a file that exists, has a
   plausible size, and opens as garbage in Word.
2. It is a valid OOXML package but has no readable body text. A document whose
   paragraphs are all empty passes every structural check and is still blank on
   screen.
3. It opens, it has text, and the text is raw Markdown. `# Experience` and
   `**Senior Engineer**` rendered as literal characters is the single most common
   way a "successful" document generation is actually a failed one -- the pipeline
   wrote source where it should have written formatting.

The third is why this validator exists rather than a magic-byte check. Byte
inspection answers (1) alone; a document can clear it and still be unusable.

Deliberately stdlib-only (zipfile + ElementTree): a .docx is a ZIP of XML parts,
so the file's own structure answers all three questions without importing
python-docx. That also means this works on documents this skill did not produce.

Usage:
    python3 validate_docx.py document.docx [--strict]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from xml.etree import ElementTree

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

REQUIRED_PARTS = ("[Content_Types].xml", "word/document.xml")

# Ceiling on the decompressed size of any single XML part. A document big enough
# to exceed this is beyond what this validator is for, and a "document" that
# claims to is a zip bomb.
MAX_PART_BYTES = 64 * 1024 * 1024

# Reading every run of a very long document to hunt for stray Markdown is not
# worth the time; the first few thousand establish the pattern.
TEXT_SCAN_LIMIT = 20000

# Markdown that survived into the rendered text. Each pattern is anchored or
# bounded so ordinary prose does not trip it:
#
#   heading     '# ', '## ' at the START of a paragraph -- prose does not begin
#               with a hash followed by a space.
#   emphasis    '**bold**' / '__bold__' with non-space inside, so a row of
#               asterisks used as a divider is not a match.
#   link        '[label](target)' -- the full inline-link shape, not a bare
#               bracket.
#   fence       '```' anywhere, which has no legitimate reading in prose.
#   bullet      '- ' / '* ' at the START of a paragraph. Reported separately and
#               only as a warning: a real bulleted list exported without list
#               formatting reads identically, and that is a styling complaint
#               rather than proof the pipeline emitted source.
MARKDOWN_STRUCTURAL = (
    ("heading", re.compile(r"^#{1,6}\s+\S")),
    ("emphasis", re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")),
    ("link", re.compile(r"\[[^\]\n]+\]\([^)\s]+\)")),
    ("code_fence", re.compile(r"```")),
)
MARKDOWN_BULLET = re.compile(r"^\s*[-*]\s+\S")


def xml_parser_available() -> bool:
    """Whether this interpreter can actually parse XML.

    ElementTree imports fine on a Python whose pyexpat extension is broken or
    absent, and only fails when a parser is constructed. Checked once up front so
    that shows up as an environment error rather than a stack trace half way
    through a document.
    """
    try:
        ElementTree.fromstring("<probe/>")
        return True
    except ImportError:
        return False


def read_xml(archive: zipfile.ZipFile, name: str):
    """Parse an XML part, refusing anything that could be a decompression or
    entity-expansion attack.

    The file under inspection is untrusted by assumption -- the whole reason to
    run a validator is that something else produced it. defusedxml is not in the
    image, so the two guards are applied by hand: a byte cap on the decompressed
    part, and an outright refusal of any DOCTYPE, which is what a billion-laughs
    payload needs. No legitimate document part declares one.
    """
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None

    if info.file_size > MAX_PART_BYTES:
        return None

    try:
        with archive.open(name) as handle:
            payload = handle.read(MAX_PART_BYTES + 1)
    except (KeyError, zipfile.BadZipFile, EOFError):
        return None

    # The header's declared size lied about how much the entry expands to.
    if len(payload) > MAX_PART_BYTES:
        return None

    if b"<!DOCTYPE" in payload[:4096] or b"<!ENTITY" in payload:
        return None

    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return None


def paragraph_text(paragraph) -> str:
    """The visible text of one paragraph.

    Word splits a single sentence across several <w:r> runs whenever formatting
    changes mid-line, so the runs must be joined before any inspection: a bold
    word makes `**` land in one run and the word in the next, and a per-run scan
    would miss exactly the case this validator is for. <w:tab> and <w:br> are
    rendered as whitespace for the same reason -- joining without them would weld
    two words together and invent matches that are not on the page.
    """
    pieces: list[str] = []
    for node in paragraph.iter():
        tag = node.tag
        if tag == f"{W_NS}t":
            pieces.append(node.text or "")
        elif tag == f"{W_NS}tab":
            pieces.append("\t")
        elif tag in (f"{W_NS}br", f"{W_NS}cr"):
            pieces.append("\n")
    return "".join(pieces)


def inspect_document(root):
    """Return (paragraph count, non-empty count, table count, markdown findings).

    `findings` maps a rule name to the first offending paragraph text, which is
    what makes the failure message actionable: naming the rule tells the caller
    what is wrong, quoting the line tells them where.
    """
    body = root.find(f"{W_NS}body")
    if body is None:
        return None

    paragraphs = 0
    populated = 0
    scanned = 0
    findings: dict[str, str] = {}
    bullet_hits = 0

    for paragraph in body.iter(f"{W_NS}p"):
        paragraphs += 1
        text = paragraph_text(paragraph)
        if text.strip():
            populated += 1

        if scanned >= TEXT_SCAN_LIMIT:
            continue
        scanned += 1

        stripped = text.strip()
        if not stripped:
            continue

        for name, pattern in MARKDOWN_STRUCTURAL:
            if name in findings:
                continue
            if pattern.search(stripped):
                findings[name] = stripped[:120]

        if MARKDOWN_BULLET.match(stripped):
            bullet_hits += 1

    tables = sum(1 for _ in body.iter(f"{W_NS}tbl"))
    return paragraphs, populated, tables, findings, bullet_hits


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate that a .docx is a real, non-empty Word document."
    )
    parser.add_argument("path", help="Path to the .docx file")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures.",
    )
    args = parser.parse_args()
    path = args.path

    if not xml_parser_available():
        print(
            "ERROR: environment: this Python cannot parse XML (pyexpat is "
            "unavailable), so no document can be inspected.",
            file=sys.stderr,
        )
        return 1

    if not os.path.exists(path):
        print(f"ERROR: file: {path} does not exist", file=sys.stderr)
        return 1
    if os.path.isdir(path):
        print(
            f"ERROR: file: {path} is a directory, expected a .docx file",
            file=sys.stderr,
        )
        return 1
    if not os.access(path, os.R_OK):
        print(f"ERROR: file: no permission to read {path}", file=sys.stderr)
        return 1

    size = os.path.getsize(path)
    if size == 0:
        print(f"ERROR: file: {path} is empty (0 bytes)", file=sys.stderr)
        return 1

    if not zipfile.is_zipfile(path):
        print(
            f"ERROR: format: {path} is not a ZIP container, so it is not a "
            ".docx. A .docx is a ZIP of XML parts -- Markdown or plain text "
            "renamed to .docx will fail here and in Word.",
            file=sys.stderr,
        )
        return 1

    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        print(
            f"ERROR: format: {path} is a corrupt ZIP container ({exc})",
            file=sys.stderr,
        )
        return 1

    with archive:
        names = set(archive.namelist())
        missing = [part for part in REQUIRED_PARTS if part not in names]
        if missing:
            print(
                f"ERROR: format: {path} is a ZIP but not a Word document -- "
                f"missing {', '.join(missing)}.",
                file=sys.stderr,
            )
            return 1

        bad = archive.testzip()
        if bad is not None:
            print(
                f"ERROR: format: {path} has a corrupt entry ({bad})",
                file=sys.stderr,
            )
            return 1

        root = read_xml(archive, "word/document.xml")
        if root is None:
            print(
                f"ERROR: format: word/document.xml in {path} could not be "
                "parsed, so the document body is unreadable.",
                file=sys.stderr,
            )
            return 1

        inspected = inspect_document(root)
        if inspected is None:
            print(
                f"ERROR: format: {path} declares no <w:body>, so it has no "
                "document content at all.",
                file=sys.stderr,
            )
            return 1

        paragraphs, populated, tables, findings, bullet_hits = inspected

        if populated == 0:
            print(
                f"ERROR: content: {path} is a structurally valid Word document "
                f"with {paragraphs} paragraph(s) but no text in any of them -- "
                "it opens blank.",
                file=sys.stderr,
            )
            return 1

        # Raw Markdown in the rendered text is a failure, not a warning: the
        # document opens, looks populated, and is wrong in the one way a reader
        # notices immediately. Reported before the summary so it never reads as
        # a pass with a footnote.
        if findings:
            print(
                f"ERROR: content: {path} contains literal Markdown in its "
                "rendered text, so the source was written into the document "
                "instead of being converted to Word formatting.",
                file=sys.stderr,
            )
            for name, sample in sorted(findings.items()):
                print(f"ERROR: content: {name} syntax: {sample!r}", file=sys.stderr)
            return 1

    summary = [
        f"OK: {path} is a Word document, {size} bytes, "
        f"{populated}/{paragraphs} populated paragraph(s), {tables} table(s)"
    ]
    warnings: list[str] = []

    if bullet_hits:
        warnings.append(
            f"{bullet_hits} paragraph(s) start with a '-' or '*' bullet "
            "character. If these are meant to be a list, apply Word list "
            "formatting instead so they indent and renumber correctly."
        )

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    if warnings and args.strict:
        return 1

    for line in summary:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
