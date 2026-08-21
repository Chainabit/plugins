#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check that a generated .xlsx is a real workbook, and that its data is usable.

Two failures matter here and neither one is visible by opening the file:

1. The file is not a workbook at all. A renamed CSV, a truncated write, or an HTML
   error page saved with an .xlsx extension all "exist" at the expected path.
2. The workbook opens fine but every number is stored as text. It looks right,
   and it breaks the moment anyone sorts, sums, or charts it.

Deliberately stdlib-only (zipfile + ElementTree): an .xlsx is a ZIP of XML parts,
so the file's own structure answers both questions without importing openpyxl. That
also means this works on workbooks this skill did not produce.

Usage:
    python3 validate_xlsx.py workbook.xlsx [--strict]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from xml.etree import ElementTree

MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
DOC_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

REQUIRED_PARTS = ("[Content_Types].xml", "xl/workbook.xml")

NUMERIC_TEXT = re.compile(r"^\s*[-+]?[\d.,]+\s*%?\s*$")

# Reading every cell of a very large sheet to hunt for numbers-as-text is not worth
# the time; the first few thousand are enough to establish the pattern.
CELL_SCAN_LIMIT = 20000

# Ceiling on the decompressed size of any single XML part. A workbook big enough to
# exceed this is beyond what this validator is for, and a "workbook" that claims to
# is a zip bomb.
MAX_PART_BYTES = 64 * 1024 * 1024


def xml_parser_available() -> bool:
    """Whether this interpreter can actually parse XML.

    ElementTree imports fine on a Python whose pyexpat extension is broken or
    absent, and only fails when a parser is constructed. Checked once up front so
    that shows up as an environment error rather than a stack trace half way
    through a sheet.
    """
    try:
        ElementTree.fromstring("<probe/>")
        return True
    except ImportError:
        return False


def read_xml(archive: zipfile.ZipFile, name: str):
    """Parse an XML part, refusing anything that could be a decompression or
    entity-expansion attack.

    The file under inspection is untrusted by assumption — the whole reason to run
    a validator is that something else produced it. defusedxml is not in the image,
    so the two guards are applied by hand: a byte cap on the decompressed part, and
    an outright refusal of any DOCTYPE, which is what a billion-laughs payload needs.
    No legitimate spreadsheet part declares one.
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


def shared_strings(archive: zipfile.ZipFile) -> list[str]:
    root = read_xml(archive, "xl/sharedStrings.xml")
    if root is None:
        return []
    strings = []
    for item in root.findall(f"{MAIN_NS}si"):
        # A string item is either one <t>, or several inside <r> runs.
        strings.append("".join(node.text or "" for node in item.iter(f"{MAIN_NS}t")))
    return strings


def sheet_parts(archive: zipfile.ZipFile) -> list[tuple[str, str | None]]:
    """Return (sheet name, worksheet part path) in workbook order."""
    workbook = read_xml(archive, "xl/workbook.xml")
    if workbook is None:
        return []

    relationships = {}
    rels = read_xml(archive, "xl/_rels/workbook.xml.rels")
    if rels is not None:
        for relationship in rels.findall(f"{PKG_REL_NS}Relationship"):
            target = relationship.get("Target", "")
            if target.startswith("/"):
                target = target.lstrip("/")
            elif not target.startswith("xl/"):
                target = f"xl/{target}"
            relationships[relationship.get("Id")] = target

    parts = []
    for sheet in workbook.iter(f"{MAIN_NS}sheet"):
        name = sheet.get("name", "(unnamed)")
        parts.append((name, relationships.get(sheet.get(f"{DOC_REL_NS}id"))))
    return parts


def inspect_sheet(archive: zipfile.ZipFile, part: str, strings: list[str]):
    """Return (row count, populated cell count, numbers-stored-as-text count)."""
    root = read_xml(archive, part)
    if root is None:
        return None

    rows = 0
    populated = 0
    numeric_text = 0
    scanned = 0

    for row in root.iter(f"{MAIN_NS}row"):
        rows += 1
        for cell in row.iter(f"{MAIN_NS}c"):
            scanned += 1
            if scanned > CELL_SCAN_LIMIT:
                break

            cell_type = cell.get("t")
            value_node = cell.find(f"{MAIN_NS}v")
            inline = cell.find(f"{MAIN_NS}is")

            if value_node is None and inline is None:
                continue
            populated += 1

            if cell_type == "s" and value_node is not None:
                try:
                    text = strings[int(value_node.text or "0")]
                except (ValueError, IndexError):
                    continue
            elif cell_type in ("str", "inlineStr"):
                if inline is not None:
                    text = "".join(n.text or "" for n in inline.iter(f"{MAIN_NS}t"))
                else:
                    text = value_node.text or ""
            else:
                continue

            # The header row is text by design, so it is not counted.
            if rows > 1 and NUMERIC_TEXT.match(text) and any(ch.isdigit() for ch in text):
                numeric_text += 1

        if scanned > CELL_SCAN_LIMIT:
            break

    return rows, populated, numeric_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_xlsx.py",
        description=(
            "Verify an .xlsx file is a real workbook, report its sheets and row "
            "counts, and warn about empty sheets or numbers stored as text."
        ),
        epilog=(
            "Example:\n"
            "  python3 validate_xlsx.py sales.xlsx\n\n"
            "Exit code 0 means the workbook is usable. Exit code 1 means do not hand "
            "it to the user.\n"
            "Runs offline, stdlib only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("xlsx", help="path to the .xlsx file to check")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat empty-sheet and numbers-as-text warnings as failures (exit 1)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.xlsx

    if not xml_parser_available():
        print(
            "ERROR: environment: this Python has no working XML parser (pyexpat), so "
            "the workbook's parts cannot be read. Run this inside the Chainabit "
            "sandbox image.",
            file=sys.stderr,
        )
        return 1

    if not os.path.exists(path):
        print(f"ERROR: file: {path} does not exist", file=sys.stderr)
        return 1
    if os.path.isdir(path):
        print(f"ERROR: file: {path} is a directory, expected an .xlsx file", file=sys.stderr)
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
            f"ERROR: format: {path} is not a ZIP container, so it is not an .xlsx. "
            "An .xlsx is a ZIP of XML parts — a CSV or plain text file renamed to "
            ".xlsx will fail here and in Excel.",
            file=sys.stderr,
        )
        return 1

    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        print(f"ERROR: format: {path} is a corrupt ZIP container ({exc})", file=sys.stderr)
        return 1

    with archive:
        names = set(archive.namelist())
        missing = [part for part in REQUIRED_PARTS if part not in names]
        if missing:
            print(
                f"ERROR: format: {path} is a ZIP but not a workbook — missing "
                f"{', '.join(missing)}.",
                file=sys.stderr,
            )
            return 1

        bad = archive.testzip()
        if bad is not None:
            print(f"ERROR: format: {path} has a corrupt entry ({bad})", file=sys.stderr)
            return 1

        parts = sheet_parts(archive)
        if not parts:
            print(
                f"ERROR: sheets: no sheets declared in {path} — the workbook has "
                "nothing in it.",
                file=sys.stderr,
            )
            return 1

        # The summary is assembled here and printed at the end. An "OK:" line above an
        # ERROR line reads as a pass with a footnote, which is the wrong impression.
        summary = [f"OK: {path} is an .xlsx workbook, {size} bytes, {len(parts)} sheet(s)"]
        warnings: list[str] = []
        empty_sheets = 0
        strings = shared_strings(archive)

        for name, part in parts:
            if part is None or part not in names:
                warnings.append(f'sheet "{name}": its worksheet part is missing from the archive')
                empty_sheets += 1
                continue

            result = inspect_sheet(archive, part, strings)
            if result is None:
                warnings.append(f'sheet "{name}": worksheet XML could not be parsed')
                continue

            rows, populated, numeric_text = result
            data_rows = max(0, rows - 1)
            summary.append(f"  {name}: {rows} row(s), {populated} populated cell(s)")

            if populated == 0:
                warnings.append(f'sheet "{name}": is completely empty')
                empty_sheets += 1
            elif data_rows == 0:
                warnings.append(f'sheet "{name}": has a header row but no data rows')
                empty_sheets += 1

            if numeric_text:
                warnings.append(
                    f'sheet "{name}": {numeric_text} cell(s) hold a number stored as '
                    "text. Excel will not sort, sum, or chart these. Declare the "
                    'column as "number" or "integer" in the spec and rebuild.'
                )

    if empty_sheets == len(parts):
        print(
            "ERROR: sheets: every sheet is empty — the workbook is structurally "
            "valid but contains no data.",
            file=sys.stderr,
        )
        return 1

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    if warnings and args.strict:
        return 1

    for line in summary:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
