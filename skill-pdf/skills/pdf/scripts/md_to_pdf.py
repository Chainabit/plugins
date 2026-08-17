#!/usr/bin/env python3
"""Render a Markdown file to PDF via WeasyPrint.

Markdown -> HTML -> PDF. The HTML step exists only so WeasyPrint has something to
lay out; the stylesheet below is the part that matters, because WeasyPrint has no
default font stack worth trusting and the container ships exactly one font family
that covers Latin Extended-A. Without pinning DejaVu, Turkish text (ç ş ğ ı ö ü İ)
falls back to a font missing those glyphs and renders as boxes.

Usage:
    python3 md_to_pdf.py input.md output.pdf [--title "Document title"]
"""

from __future__ import annotations

import argparse
import os
import sys

# The one font family guaranteed present in the sandbox image (fonts-dejavu-core)
# that covers the Latin Extended-A range Turkish needs. Serif/Mono are siblings
# from the same package, so all three are safe to name.
STYLESHEET = """
@page {
  size: A4;
  margin: 22mm 18mm 20mm 18mm;
  @bottom-center {
    content: counter(page) " / " counter(pages);
    font-family: "DejaVu Sans", sans-serif;
    font-size: 9pt;
    color: #6b7280;
  }
}

html, body {
  font-family: "DejaVu Sans", sans-serif;
  font-size: 10.5pt;
  line-height: 1.55;
  color: #111827;
}

h1, h2, h3, h4, h5, h6 {
  font-family: "DejaVu Sans", sans-serif;
  color: #0f172a;
  line-height: 1.25;
  margin: 1.2em 0 0.45em;
  /* Keep a heading with the text it introduces instead of stranding it at a
     page foot. */
  page-break-after: avoid;
}
h1 { font-size: 20pt; margin-top: 0; }
h2 { font-size: 15pt; border-bottom: 0.6pt solid #e5e7eb; padding-bottom: 0.2em; }
h3 { font-size: 12.5pt; }
h4, h5, h6 { font-size: 11pt; }

p { margin: 0 0 0.7em; }

ul, ol { margin: 0 0 0.8em; padding-left: 1.4em; }
li { margin-bottom: 0.25em; }
li > ul, li > ol { margin-bottom: 0.2em; }

blockquote {
  margin: 0 0 0.9em;
  padding: 0.2em 0 0.2em 0.9em;
  border-left: 2.5pt solid #cbd5e1;
  color: #374151;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin: 0.5em 0 1em;
  font-size: 9.5pt;
}
th, td {
  border: 0.5pt solid #d1d5db;
  padding: 5pt 7pt;
  text-align: left;
  vertical-align: top;
}
th { background: #f3f4f6; font-weight: bold; }
/* Repeat the header when a table spans pages. */
thead { display: table-header-group; }
tr { page-break-inside: avoid; }

code {
  font-family: "DejaVu Sans Mono", monospace;
  font-size: 9pt;
  background: #f3f4f6;
  padding: 0.5pt 2.5pt;
  border-radius: 2pt;
}
pre {
  font-family: "DejaVu Sans Mono", monospace;
  font-size: 8.5pt;
  background: #f8fafc;
  border: 0.5pt solid #e2e8f0;
  border-radius: 3pt;
  padding: 7pt 9pt;
  margin: 0 0 0.9em;
  /* Long lines wrap rather than run off the page edge — a PDF has no
     horizontal scrollbar. */
  white-space: pre-wrap;
  word-wrap: break-word;
}
pre code { background: none; padding: 0; font-size: inherit; }

hr { border: none; border-top: 0.5pt solid #e5e7eb; margin: 1.2em 0; }

img { max-width: 100%; }

a { color: #1d4ed8; text-decoration: none; }
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{stylesheet}</style>
</head>
<body>
{body}
</body>
</html>
"""


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def validate(input_path: str, output_path: str, css_path: str | None) -> list[str]:
    """Return one human-readable problem per line, empty when the run can proceed."""
    problems = []

    if not os.path.exists(input_path):
        problems.append(f"input: {input_path} does not exist")
    elif os.path.isdir(input_path):
        problems.append(f"input: {input_path} is a directory, expected a Markdown file")
    elif not os.access(input_path, os.R_OK):
        problems.append(f"input: {input_path} is not readable")

    if not output_path.lower().endswith(".pdf"):
        problems.append(f"output: {output_path} must end in .pdf")

    output_dir = os.path.dirname(os.path.abspath(output_path))
    if not os.path.isdir(output_dir):
        problems.append(f"output: directory {output_dir} does not exist")
    elif not os.access(output_dir, os.W_OK):
        problems.append(f"output: directory {output_dir} is not writable")

    if css_path is not None:
        if not os.path.exists(css_path):
            problems.append(f"css: {css_path} does not exist")
        elif os.path.isdir(css_path):
            problems.append(f"css: {css_path} is a directory, expected a .css file")
        elif not os.access(css_path, os.R_OK):
            problems.append(f"css: {css_path} is not readable")

    return problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="md_to_pdf.py",
        description=(
            "Render a Markdown file to PDF. Handles headings, lists, tables, code "
            "blocks, and blockquotes, with a font stack that renders Turkish and "
            "other Latin Extended-A characters correctly."
        ),
        epilog=(
            "Example:\n"
            "  python3 md_to_pdf.py report.md report.pdf --title \"Q3 Report\"\n\n"
            "Runs offline. Every library it needs is already installed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="path to the source .md file")
    parser.add_argument("output", help="path to write the .pdf to")
    parser.add_argument(
        "--title",
        default=None,
        help="document title, written into the PDF metadata (default: the input filename)",
    )
    parser.add_argument(
        "--lang",
        default="tr",
        help="BCP-47 language tag for the document (default: tr)",
    )
    parser.add_argument(
        "--css",
        default=None,
        help=(
            "path to an extra stylesheet appended after the built-in one, so its "
            "rules win. See references/typography.md before writing one."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    problems = validate(args.input, args.output, args.css)
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1

    # The source is read before the renderers are imported, so a problem the caller
    # can actually fix — an empty or mis-encoded input — is reported ahead of an
    # environment fault it cannot.
    try:
        with open(args.input, "r", encoding="utf-8") as handle:
            source = handle.read()
    except FileNotFoundError:
        print(f"ERROR: input: {args.input} disappeared before it could be read", file=sys.stderr)
        return 1
    except PermissionError:
        print(f"ERROR: input: no permission to read {args.input}", file=sys.stderr)
        return 1
    except UnicodeDecodeError as exc:
        print(
            f"ERROR: input: {args.input} is not valid UTF-8 ({exc.reason}). "
            "Re-save it as UTF-8 and retry.",
            file=sys.stderr,
        )
        return 1

    if not source.strip():
        print(f"ERROR: input: {args.input} is empty, there is nothing to render", file=sys.stderr)
        return 1

    try:
        import markdown
    except ImportError:
        print(
            "ERROR: environment: the 'markdown' package is missing. This script only "
            "runs inside the Chainabit sandbox image, where it is pre-installed. Do "
            "not try to install it.",
            file=sys.stderr,
        )
        return 1

    try:
        from weasyprint import HTML
    except ImportError:
        print(
            "ERROR: environment: the 'weasyprint' package is missing. This script "
            "only runs inside the Chainabit sandbox image, where it is pre-installed. "
            "Do not try to install it.",
            file=sys.stderr,
        )
        return 1

    body = markdown.markdown(
        source,
        # 'extra' pulls in tables, fenced code, definition lists, and footnotes.
        # Syntax highlighting is deliberately left out: it would need Pygments,
        # which is not in the image.
        extensions=["extra", "sane_lists", "admonition"],
        output_format="html5",
    )

    stylesheet = STYLESHEET
    if args.css:
        try:
            with open(args.css, "r", encoding="utf-8") as handle:
                # Appended, not substituted: the built-in rules stay in force and
                # the override only has to say what differs.
                stylesheet = stylesheet + "\n/* --css override */\n" + handle.read()
        except PermissionError:
            print(f"ERROR: css: no permission to read {args.css}", file=sys.stderr)
            return 1
        except UnicodeDecodeError as exc:
            print(f"ERROR: css: {args.css} is not valid UTF-8 ({exc.reason})", file=sys.stderr)
            return 1

    title = args.title or os.path.splitext(os.path.basename(args.input))[0]
    document = HTML_TEMPLATE.format(
        lang=escape_html(args.lang),
        title=escape_html(title),
        stylesheet=stylesheet,
        body=body,
    )

    try:
        # base_url is the input's own directory so relative image paths in the
        # Markdown resolve; it is never allowed to reach beyond the given files
        # because nothing here fetches over the network.
        HTML(string=document, base_url=os.path.dirname(os.path.abspath(args.input))).write_pdf(
            args.output
        )
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
