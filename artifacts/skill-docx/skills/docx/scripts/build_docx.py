#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a .docx document from a JSON spec, using python-docx.

The point of the spec is that formatting is DECLARED, not written into the text.
The defect this exists to prevent is the one the validator catches: a pipeline
that emits `# Experience` and `**Senior Engineer**` as literal characters
produces a file that opens, looks populated, and is obviously wrong to the first
human who reads it. A spec with a `heading` block type cannot express that
mistake -- the heading level is a field, so it becomes a real Word heading.

For that reason the builder REFUSES Markdown syntax in block text rather than
passing it through. Writing it out would produce exactly the artifact the
validator rejects, and a builder that emits what its own validator fails is not
a toolchain.

The whole spec is validated before the document is created, and every problem is
printed at once as `ERROR: <field>: <reason>`.

Usage:
    python3 build_docx.py spec.json output.docx [--validate-only]

Spec shape (see SKILL.md for the annotated version):

    {
      "properties": {"title": "...", "author": "...", "subject": "..."},  optional
      "blocks": [                                    required, at least one
        {"type": "heading", "text": "Experience", "level": 1},
        {"type": "paragraph", "text": "Led the billing migration.",
         "style": "Normal", "bold": false, "italic": false,
         "alignment": "left"},
        {"type": "bullets", "items": ["First", "Second"]},
        {"type": "numbered", "items": ["Step one", "Step two"]},
        {"type": "table",
         "columns": ["Region", "Volume"],
         "rows": [["Istanbul", "4210"], ["Izmir", "1904"]],
         "headerBold": true},
        {"type": "pagebreak"}
      ]
    }
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

DEFAULT_FONT = os.environ.get("CHAINABIT_ARTIFACT_FONT_FAMILY", "IBM Plex Sans").strip() or "IBM Plex Sans"
ARABIC_FALLBACK_FONT = "IBM Plex Sans Arabic"
SAFE_FONT_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")

BLOCK_TYPES = ("heading", "paragraph", "bullets", "numbered", "table", "pagebreak")

ALIGNMENTS = ("left", "center", "right", "justify")

MAX_HEADING_LEVEL = 6

# Mirrors the structural rules in validate_docx.py. Kept as a literal duplicate
# rather than a shared import: the two scripts run independently in the sandbox
# and a shared module would be a third file to materialize for no behavioural
# gain. The validator remains the authority; this is an early, friendlier "no".
MARKDOWN_PATTERNS = (
    ("heading", re.compile(r"^#{1,6}\s+\S"), 'use {"type": "heading", "level": N}'),
    (
        "emphasis",
        re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1"),
        'use "bold": true or "italic": true on the block',
    ),
    ("code_fence", re.compile(r"```"), "use a paragraph with the Consolas style"),
)


def markdown_complaint(field: str, text: str) -> str | None:
    for name, pattern, remedy in MARKDOWN_PATTERNS:
        if pattern.search(text.strip()):
            return (
                f"ERROR: {field}: contains literal Markdown ({name}). Word will "
                f"render the characters as typed -- {remedy}."
            )
    return None


def require_text(errors: list[str], field: str, value, allow_empty: bool = False):
    if not isinstance(value, str):
        errors.append(f"ERROR: {field}: must be a string, got {type(value).__name__}")
        return None
    if not allow_empty and not value.strip():
        errors.append(f"ERROR: {field}: must not be empty")
        return None
    complaint = markdown_complaint(field, value)
    if complaint:
        errors.append(complaint)
        return None
    return value


def validate_block(errors: list[str], index: int, block) -> None:
    where = f"blocks[{index}]"
    if not isinstance(block, dict):
        errors.append(f"ERROR: {where}: must be an object")
        return

    kind = block.get("type")
    if kind not in BLOCK_TYPES:
        errors.append(
            f"ERROR: {where}.type: must be one of {', '.join(BLOCK_TYPES)}, "
            f"got {kind!r}"
        )
        return

    if kind == "pagebreak":
        return

    if kind == "heading":
        require_text(errors, f"{where}.text", block.get("text"))
        level = block.get("level", 1)
        if not isinstance(level, int) or not 1 <= level <= MAX_HEADING_LEVEL:
            errors.append(
                f"ERROR: {where}.level: must be an integer 1-{MAX_HEADING_LEVEL}, "
                f"got {level!r}"
            )
        return

    if kind == "paragraph":
        require_text(errors, f"{where}.text", block.get("text"), allow_empty=True)
        alignment = block.get("alignment", "left")
        if alignment not in ALIGNMENTS:
            errors.append(
                f"ERROR: {where}.alignment: must be one of "
                f"{', '.join(ALIGNMENTS)}, got {alignment!r}"
            )
        return

    if kind in ("bullets", "numbered"):
        items = block.get("items")
        if not isinstance(items, list) or not items:
            errors.append(f"ERROR: {where}.items: must be a non-empty array")
            return
        for position, item in enumerate(items):
            require_text(errors, f"{where}.items[{position}]", item)
        return

    if kind == "table":
        columns = block.get("columns")
        if not isinstance(columns, list) or not columns:
            errors.append(f"ERROR: {where}.columns: must be a non-empty array")
            columns = []
        for position, column in enumerate(columns):
            require_text(errors, f"{where}.columns[{position}]", column)

        rows = block.get("rows")
        if not isinstance(rows, list) or not rows:
            errors.append(f"ERROR: {where}.rows: must be a non-empty array")
            return
        for position, row in enumerate(rows):
            if not isinstance(row, list):
                errors.append(f"ERROR: {where}.rows[{position}]: must be an array")
                continue
            if columns and len(row) != len(columns):
                # A short row is not padded silently: a table whose cells have
                # shifted one column left is wrong in a way nobody notices until
                # they read the numbers.
                errors.append(
                    f"ERROR: {where}.rows[{position}]: has {len(row)} cell(s) but "
                    f"the table declares {len(columns)} column(s)"
                )
                continue
            for cell_index, cell in enumerate(row):
                require_text(
                    errors,
                    f"{where}.rows[{position}][{cell_index}]",
                    cell,
                    allow_empty=True,
                )


def validate_spec(spec) -> list[str]:
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["ERROR: spec: top level must be a JSON object"]

    if spec.get("font") is not None and (
        not isinstance(spec.get("font"), str)
        or not SAFE_FONT_NAME.fullmatch(spec["font"].strip())
    ):
        errors.append("ERROR: font: must be a safe non-empty family name")

    properties = spec.get("properties", {})
    if not isinstance(properties, dict):
        errors.append("ERROR: properties: must be an object")
    else:
        for key in ("title", "author", "subject"):
            if key in properties:
                require_text(errors, f"properties.{key}", properties[key])

    blocks = spec.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        errors.append("ERROR: blocks: must be a non-empty array")
        return errors

    for index, block in enumerate(blocks):
        validate_block(errors, index, block)

    if not any(
        isinstance(block, dict) and block.get("type") != "pagebreak"
        for block in blocks
    ):
        # A document of nothing but page breaks is structurally valid and opens
        # blank -- the exact failure validate_docx.py reports as `content`.
        errors.append("ERROR: blocks: contains no content, only page breaks")

    return errors


def build(spec, output_path: str) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml.ns import qn

    alignment_map = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }

    document = Document()
    font_family = spec.get("font") or DEFAULT_FONT
    for style in document.styles:
        if not hasattr(style, "font"):
            continue
        style.font.name = font_family
        properties = style.element.get_or_add_rPr()
        fonts = properties.get_or_add_rFonts()
        for attribute in ("ascii", "hAnsi", "eastAsia"):
            fonts.set(qn(f"w:{attribute}"), font_family)
        fonts.set(qn("w:cs"), ARABIC_FALLBACK_FONT)

    properties = spec.get("properties", {})
    core = document.core_properties
    if properties.get("title"):
        core.title = properties["title"]
    if properties.get("author"):
        core.author = properties["author"]
    if properties.get("subject"):
        core.subject = properties["subject"]

    for block in spec["blocks"]:
        kind = block["type"]

        if kind == "pagebreak":
            document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

        elif kind == "heading":
            document.add_heading(block["text"], level=block.get("level", 1))

        elif kind == "paragraph":
            paragraph = document.add_paragraph()
            paragraph.alignment = alignment_map[block.get("alignment", "left")]
            run = paragraph.add_run(block["text"])
            run.bold = bool(block.get("bold", False))
            run.italic = bool(block.get("italic", False))

        elif kind == "bullets":
            for item in block["items"]:
                document.add_paragraph(item, style="List Bullet")

        elif kind == "numbered":
            for item in block["items"]:
                document.add_paragraph(item, style="List Number")

        elif kind == "table":
            columns = block["columns"]
            table = document.add_table(rows=1, cols=len(columns))
            table.style = "Table Grid"
            header = table.rows[0].cells
            for index, column in enumerate(columns):
                header[index].text = column
                if block.get("headerBold", True):
                    for paragraph in header[index].paragraphs:
                        for run in paragraph.runs:
                            run.bold = True
            for row in block["rows"]:
                cells = table.add_row().cells
                for index, value in enumerate(row):
                    cells[index].text = value

    document.save(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a .docx document from a JSON spec."
    )
    parser.add_argument("spec", help="Path to the JSON spec")
    parser.add_argument("output", nargs="?", help="Path to write the .docx to")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Check the spec and exit without writing a document.",
    )
    args = parser.parse_args()

    if not args.validate_only and not args.output:
        print("ERROR: output: an output path is required", file=sys.stderr)
        return 1

    if not os.path.exists(args.spec):
        print(f"ERROR: spec: {args.spec} does not exist", file=sys.stderr)
        return 1

    try:
        with open(args.spec, encoding="utf-8") as handle:
            spec = json.load(handle)
    except json.JSONDecodeError as exc:
        print(f"ERROR: spec: {args.spec} is not valid JSON ({exc})", file=sys.stderr)
        return 1

    errors = validate_spec(spec)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    if args.validate_only:
        print(f"OK: {args.spec} is a valid document spec")
        return 0

    try:
        build(spec, args.output)
    except ImportError:
        print(
            "ERROR: environment: python-docx is not installed. Run this inside "
            "the Chainabit sandbox image, where it is pre-installed.",
            file=sys.stderr,
        )
        return 2
    except PermissionError:
        print(f"ERROR: output: no permission to write {args.output}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: output: could not write {args.output}: {exc}", file=sys.stderr)
        return 2

    size = os.path.getsize(args.output)
    print(f"OK: wrote {args.output}, {size} bytes, {len(spec['blocks'])} block(s)")
    print(
        f"Next: python3 validate_docx.py {args.output} "
        "-- a build that succeeded is not yet a document that is right."
    )
    with open(args.output, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    font = spec.get("font") or DEFAULT_FONT
    print(json.dumps({
        "schema": "chainabit.docx.execution/v1",
        "success": True,
        "generator": "skill-docx.build_docx",
        "output": {
            "path": os.path.realpath(args.output),
            "shape": "file",
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "sha256": digest,
            "bytes": size,
        },
        "typography": {
            "family": font,
            "source": "user_override" if spec.get("font") else "chainabit_default",
        },
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
