#!/usr/bin/env python3
"""Build a laid-out PDF report from a JSON spec, using ReportLab Platypus.

Use this instead of md_to_pdf.py when the layout itself carries meaning: a title
page, column widths that have to line up, tables that must not be reflowed by a
Markdown renderer's idea of a table.

The spec is validated before a single byte is written, and every problem is
reported at once as `ERROR: <field>: <reason>`. That is deliberate: a caller
fixing its own JSON should see the whole list in one pass rather than discovering
faults one exit code at a time.

Usage:
    python3 report_pdf.py spec.json output.pdf

Spec shape (see SKILL.md for the annotated version):

    {
      "title": "Quarterly Report",          required
      "subtitle": "Q3 2026",                optional
      "author": "Analytics",                optional
      "date": "2026-08-18",                 optional
      "pageSize": "A4" | "letter",          optional, default A4
      "titlePage": true,                    optional, default true
      "blocks": [                           required, at least one
        {"type": "heading",   "level": 1, "text": "..."},
        {"type": "paragraph", "text": "..."},
        {"type": "bullets",   "items": ["...", "..."]},
        {"type": "table",     "columns": ["A", "B"],
                              "rows": [["1", "2"]],
                              "widths": [60, 40],        percentages, optional
                              "caption": "..."},         optional
        {"type": "spacer",    "height": 18},             points
        {"type": "pagebreak"}
      ]
    }
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Candidate locations for the DejaVu family, which is what makes Turkish and other
# Latin Extended-A characters render. ReportLab's built-in Helvetica is a Type 1
# font limited to WinAnsi, so ğ, ş, İ and ı come out wrong or missing under it.
DEJAVU_DIRS = (
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts/TTF",
)

BLOCK_TYPES = ("heading", "paragraph", "bullets", "table", "spacer", "pagebreak")


def escape(text: str) -> str:
    """Escape for ReportLab's mini-HTML paragraph markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --- validation -------------------------------------------------------------------


def validate_spec(spec: object) -> list[str]:
    """Return one `<field>: <reason>` string per problem; empty means the spec is usable."""
    problems: list[str] = []

    if not isinstance(spec, dict):
        return ["spec: top level must be a JSON object"]

    title = spec.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append("title: required, must be a non-empty string")

    for optional in ("subtitle", "author", "date"):
        value = spec.get(optional)
        if value is not None and not isinstance(value, str):
            problems.append(f"{optional}: must be a string when present")

    page_size = spec.get("pageSize", "A4")
    if page_size not in ("A4", "letter"):
        problems.append('pageSize: must be "A4" or "letter"')

    title_page = spec.get("titlePage", True)
    if not isinstance(title_page, bool):
        problems.append("titlePage: must be true or false")

    blocks = spec.get("blocks")
    if not isinstance(blocks, list):
        problems.append("blocks: required, must be an array")
        return problems
    if not blocks:
        problems.append("blocks: must contain at least one block")
        return problems

    for index, block in enumerate(blocks):
        problems.extend(validate_block(block, f"blocks[{index}]"))

    return problems


def validate_block(block: object, where: str) -> list[str]:
    problems: list[str] = []

    if not isinstance(block, dict):
        return [f"{where}: must be an object"]

    kind = block.get("type")
    if kind not in BLOCK_TYPES:
        return [f"{where}.type: must be one of {', '.join(BLOCK_TYPES)}, found {kind!r}"]

    if kind == "heading":
        text = block.get("text")
        if not isinstance(text, str) or not text.strip():
            problems.append(f"{where}.text: required, must be a non-empty string")
        level = block.get("level", 1)
        if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 3:
            problems.append(f"{where}.level: must be an integer 1, 2, or 3")

    elif kind == "paragraph":
        text = block.get("text")
        if not isinstance(text, str) or not text.strip():
            problems.append(f"{where}.text: required, must be a non-empty string")

    elif kind == "bullets":
        items = block.get("items")
        if not isinstance(items, list) or not items:
            problems.append(f"{where}.items: required, must be a non-empty array of strings")
        else:
            for i, item in enumerate(items):
                if not isinstance(item, str) or not item.strip():
                    problems.append(f"{where}.items[{i}]: must be a non-empty string")

    elif kind == "table":
        columns = block.get("columns")
        if not isinstance(columns, list) or not columns:
            problems.append(f"{where}.columns: required, must be a non-empty array of strings")
            columns = []
        else:
            for i, column in enumerate(columns):
                if not isinstance(column, str):
                    problems.append(f"{where}.columns[{i}]: must be a string")

        rows = block.get("rows")
        if not isinstance(rows, list) or not rows:
            problems.append(f"{where}.rows: required, must be a non-empty array of arrays")
        else:
            for i, row in enumerate(rows):
                if not isinstance(row, list):
                    problems.append(f"{where}.rows[{i}]: must be an array of cell values")
                    continue
                if columns and len(row) != len(columns):
                    problems.append(
                        f"{where}.rows[{i}]: has {len(row)} cells but "
                        f"{len(columns)} columns are declared"
                    )
                for j, cell in enumerate(row):
                    if not isinstance(cell, (str, int, float)) or isinstance(cell, bool):
                        problems.append(
                            f"{where}.rows[{i}][{j}]: must be a string or a number"
                        )

        widths = block.get("widths")
        if widths is not None:
            if not isinstance(widths, list):
                problems.append(f"{where}.widths: must be an array of percentages")
            elif columns and len(widths) != len(columns):
                problems.append(
                    f"{where}.widths: has {len(widths)} entries but "
                    f"{len(columns)} columns are declared"
                )
            else:
                for i, width in enumerate(widths):
                    if not isinstance(width, (int, float)) or isinstance(width, bool):
                        problems.append(f"{where}.widths[{i}]: must be a number")
                    elif width <= 0:
                        problems.append(f"{where}.widths[{i}]: must be greater than 0")

        caption = block.get("caption")
        if caption is not None and not isinstance(caption, str):
            problems.append(f"{where}.caption: must be a string when present")

    elif kind == "spacer":
        height = block.get("height", 12)
        if not isinstance(height, (int, float)) or isinstance(height, bool):
            problems.append(f"{where}.height: must be a number of points")
        elif not 0 < height <= 500:
            problems.append(f"{where}.height: must be greater than 0 and at most 500")

    return problems


# --- rendering --------------------------------------------------------------------


def register_fonts() -> tuple[str, str]:
    """Register DejaVu if it is on disk; return the (regular, bold) font names to use."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for directory in DEJAVU_DIRS:
        regular = os.path.join(directory, "DejaVuSans.ttf")
        bold = os.path.join(directory, "DejaVuSans-Bold.ttf")
        if os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("DejaVuSans", regular))
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold))
            pdfmetrics.registerFontFamily(
                "DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold"
            )
            return "DejaVuSans", "DejaVuSans-Bold"

    # Falling back rather than failing: an English-only report is still worth
    # producing. The warning is what tells the caller why the Turkish characters
    # in its output look wrong.
    print(
        "WARNING: DejaVu fonts not found; falling back to Helvetica. Characters "
        "outside Latin-1 (ç ş ğ ı İ) will not render correctly.",
        file=sys.stderr,
    )
    return "Helvetica", "Helvetica-Bold"


def build_styles(regular: str, bold: str):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    styles = {
        "body": ParagraphStyle(
            "Body", parent=base["BodyText"], fontName=regular, fontSize=10.5,
            leading=15.5, spaceAfter=7, textColor=colors.HexColor("#111827"),
        ),
        "h1": ParagraphStyle(
            "H1", parent=base["Heading1"], fontName=bold, fontSize=18, leading=23,
            spaceBefore=16, spaceAfter=8, textColor=colors.HexColor("#0f172a"),
        ),
        "h2": ParagraphStyle(
            "H2", parent=base["Heading2"], fontName=bold, fontSize=14, leading=19,
            spaceBefore=13, spaceAfter=6, textColor=colors.HexColor("#0f172a"),
        ),
        "h3": ParagraphStyle(
            "H3", parent=base["Heading3"], fontName=bold, fontSize=11.5, leading=16,
            spaceBefore=11, spaceAfter=5, textColor=colors.HexColor("#0f172a"),
        ),
        "bullet": ParagraphStyle(
            "Bullet", parent=base["BodyText"], fontName=regular, fontSize=10.5,
            leading=15.5, spaceAfter=3, leftIndent=14, bulletIndent=4,
        ),
        "cell": ParagraphStyle(
            "Cell", parent=base["BodyText"], fontName=regular, fontSize=9,
            leading=12.5, spaceAfter=0,
        ),
        "cellHeader": ParagraphStyle(
            "CellHeader", parent=base["BodyText"], fontName=bold, fontSize=9,
            leading=12.5, spaceAfter=0,
        ),
        "caption": ParagraphStyle(
            "Caption", parent=base["BodyText"], fontName=regular, fontSize=8.5,
            leading=12, spaceBefore=3, spaceAfter=10,
            textColor=colors.HexColor("#6b7280"), alignment=TA_CENTER,
        ),
        "titlePageTitle": ParagraphStyle(
            "TitlePageTitle", parent=base["Title"], fontName=bold, fontSize=26,
            leading=32, spaceAfter=10, alignment=TA_CENTER,
        ),
        "titlePageSub": ParagraphStyle(
            "TitlePageSub", parent=base["BodyText"], fontName=regular, fontSize=13,
            leading=18, spaceAfter=6, alignment=TA_CENTER,
            textColor=colors.HexColor("#374151"),
        ),
        "titlePageMeta": ParagraphStyle(
            "TitlePageMeta", parent=base["BodyText"], fontName=regular, fontSize=10,
            leading=14, alignment=TA_CENTER, textColor=colors.HexColor("#6b7280"),
        ),
    }
    return styles


def build_table(block: dict, styles, frame_width: float):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    columns = block["columns"]
    header = [Paragraph(escape(str(c)), styles["cellHeader"]) for c in columns]
    body = [
        [Paragraph(escape(str(cell)), styles["cell"]) for cell in row]
        for row in block["rows"]
    ]

    widths = block.get("widths")
    if widths:
        total = float(sum(widths))
        col_widths = [frame_width * (w / total) for w in widths]
    else:
        col_widths = [frame_width / len(columns)] * len(columns)

    table = Table([header] + body, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
            ]
        )
    )
    return table


def build_story(spec: dict, styles, frame_width: float):
    from reportlab.platypus import ListFlowable, ListItem, PageBreak, Paragraph, Spacer

    story = []

    if spec.get("titlePage", True):
        story.append(Spacer(1, 150))
        story.append(Paragraph(escape(spec["title"]), styles["titlePageTitle"]))
        if spec.get("subtitle"):
            story.append(Paragraph(escape(spec["subtitle"]), styles["titlePageSub"]))
        meta = [spec.get("author"), spec.get("date")]
        meta_line = "  ·  ".join(escape(m) for m in meta if m)
        if meta_line:
            story.append(Spacer(1, 18))
            story.append(Paragraph(meta_line, styles["titlePageMeta"]))
        story.append(PageBreak())

    for block in spec["blocks"]:
        kind = block["type"]

        if kind == "heading":
            story.append(
                Paragraph(escape(block["text"]), styles[f"h{block.get('level', 1)}"])
            )

        elif kind == "paragraph":
            story.append(Paragraph(escape(block["text"]), styles["body"]))

        elif kind == "bullets":
            story.append(
                ListFlowable(
                    [
                        ListItem(Paragraph(escape(item), styles["bullet"]), leftIndent=14)
                        for item in block["items"]
                    ],
                    bulletType="bullet",
                    start="•",
                    leftIndent=14,
                    # The bullet glyph is drawn outside the paragraph's own
                    # style, so without this it uses ReportLab's default font
                    # rather than the document's. Latin-1 markers survive that
                    # either way; anything outside it would not.
                    bulletFontName=styles["bullet"].fontName,
                )
            )
            story.append(Spacer(1, 7))

        elif kind == "table":
            story.append(build_table(block, styles, frame_width))
            if block.get("caption"):
                story.append(Paragraph(escape(block["caption"]), styles["caption"]))
            else:
                story.append(Spacer(1, 10))

        elif kind == "spacer":
            story.append(Spacer(1, block.get("height", 12)))

        elif kind == "pagebreak":
            story.append(PageBreak())

    return story


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="report_pdf.py",
        description=(
            "Build a PDF report from a JSON spec: title page, headings, paragraphs, "
            "bullet lists, and tables with controlled column widths."
        ),
        epilog=(
            "Example:\n"
            "  python3 report_pdf.py spec.json report.pdf\n\n"
            "The spec is fully validated first. Every problem is printed as an "
            "ERROR: line so the spec can be corrected in one pass.\n"
            "Runs offline. Every library it needs is already installed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("spec", help="path to the JSON spec file")
    parser.add_argument("output", help="path to write the .pdf to")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="check the spec and exit without writing a PDF",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    problems: list[str] = []

    if not args.output.lower().endswith(".pdf"):
        problems.append(f"output: {args.output} must end in .pdf")
    output_dir = os.path.dirname(os.path.abspath(args.output))
    if not os.path.isdir(output_dir):
        problems.append(f"output: directory {output_dir} does not exist")
    elif not os.access(output_dir, os.W_OK):
        problems.append(f"output: directory {output_dir} is not writable")

    try:
        with open(args.spec, "r", encoding="utf-8") as handle:
            spec = json.load(handle)
    except FileNotFoundError:
        print(f"ERROR: spec: {args.spec} does not exist", file=sys.stderr)
        return 1
    except IsADirectoryError:
        print(f"ERROR: spec: {args.spec} is a directory, expected a JSON file", file=sys.stderr)
        return 1
    except PermissionError:
        print(f"ERROR: spec: no permission to read {args.spec}", file=sys.stderr)
        return 1
    except UnicodeDecodeError as exc:
        print(f"ERROR: spec: {args.spec} is not valid UTF-8 ({exc.reason})", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(
            f"ERROR: spec: {args.spec} is not valid JSON — {exc.msg} "
            f"at line {exc.lineno}, column {exc.colno}",
            file=sys.stderr,
        )
        return 1

    problems.extend(validate_spec(spec))

    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1

    if args.validate_only:
        print(f"OK: {args.spec} is a valid report spec ({len(spec['blocks'])} block(s))")
        return 0

    try:
        from reportlab.lib.pagesizes import A4, letter
        from reportlab.platypus import SimpleDocTemplate
    except ImportError:
        print(
            "ERROR: environment: the 'reportlab' package is missing. This script only "
            "runs inside the Chainabit sandbox image, where it is pre-installed. Do "
            "not try to install it.",
            file=sys.stderr,
        )
        return 1

    styles = build_styles(*register_fonts())

    page_size = A4 if spec.get("pageSize", "A4") == "A4" else letter
    margin = 50
    frame_width = page_size[0] - (2 * margin)

    document = SimpleDocTemplate(
        args.output,
        pagesize=page_size,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=54,
        bottomMargin=48,
        title=spec["title"],
        author=spec.get("author") or "",
        subject=spec.get("subtitle") or "",
    )

    story = build_story(spec, styles, frame_width)

    try:
        document.build(story)
    except PermissionError:
        print(f"ERROR: output: no permission to write {args.output}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: output: could not write {args.output}: {exc}", file=sys.stderr)
        return 1

    size = os.path.getsize(args.output)
    print(f"OK: wrote {args.output} ({size} bytes)")
    print(f"Next: python3 scripts/validate_pdf.py {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
