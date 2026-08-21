---
name: pdf
description: Generates real PDF files inside the sandbox, either from a Markdown source or from a JSON report spec, and verifies the result before it is handed back. Use when the requested deliverable is a PDF - the request mentions PDF, .pdf, "rapor", "report", an invoice, a certificate, a printable handout or one-pager, or asks to convert Markdown to PDF. Also use to check whether an existing PDF file is valid, readable, or blank. Do NOT use when the deliverable is Markdown, plain text, or a document meant to stay editable; do NOT use for a spreadsheet or tabular data file (.xlsx or .csv), and do NOT use for a website or any HTML page meant to be viewed in a browser. Renders Turkish and other Latin Extended-A characters correctly.
license: Apache-2.0
metadata:
  version: 3.0.0
---

# PDF generation

## Overview

Three scripts, one workflow: produce a PDF, then prove it is not blank. They cover
two different jobs and it is worth picking the right one at the start.

- **`md_to_pdf.py`** — content-first. Write the document as Markdown, render it.
  Correct for narrative documents: stories, articles, summaries, README-shaped
  reports, anything where the prose matters and the layout only has to be tidy.
- **`report_pdf.py`** — layout-first. Describe the document as JSON, render it with
  ReportLab. Correct when the structure carries meaning: a title page, tables whose
  column widths have to line up, controlled page breaks.
- **`validate_pdf.py`** — the exit gate for both. A renderer can exit 0 and still
  write a structurally valid PDF containing nothing. Never report success without
  running it.

All script paths in this document are **relative to this skill's own directory**.
If the skill is materialised at `/workspace/.skills/pdf/`, then `scripts/md_to_pdf.py`
means `/workspace/.skills/pdf/scripts/md_to_pdf.py`. Run from wherever is convenient
using the full path; the scripts write only to the output path they are given.

Every library used here — `markdown`, `weasyprint`, `reportlab` — is **already
installed** in the sandbox image, along with the DejaVu fonts. There is no network
at runtime. Do not run `pip install`, `uv pip install`, or `apt-get`: it will fail,
and it was never needed. If an import error appears, the script says so explicitly;
that means the environment is wrong, not that a dependency is missing.

## Quick start

Markdown to PDF, end to end:

```bash
cat > /workspace/masal.md <<'EOF'
# Küçük Işık

Bir zamanlar, dağın eteğinde ışığını kaybetmiş küçük bir ateş böceği yaşarmış.

## Yolculuk

- Çayırı geçti
- Şelaleyi aştı
- Iğdır'a vardı
EOF

python3 scripts/md_to_pdf.py /workspace/masal.md /workspace/masal.pdf --title "Küçük Işık"
python3 scripts/validate_pdf.py /workspace/masal.pdf
```

Expected output from the second command:

```
OK: /workspace/masal.pdf is a PDF 1.7 file, 12894 bytes, 1 page(s)
```

If validation prints `ERROR:` lines, the file is not deliverable. Fix the cause and
re-run — do not describe a failed render as a finished document.

## Task: Markdown to PDF

```bash
python3 scripts/md_to_pdf.py <input.md> <output.pdf> [--title T] [--lang tr] [--css extra.css]
```

Handles headings, ordered and unordered lists, tables, fenced code blocks,
blockquotes, horizontal rules, and images referenced by a path relative to the
input file. Page margins, page numbering, and a font stack that renders Turkish
correctly are built in.

Notes that change the output:

- `--title` sets the PDF metadata title, which is what a viewer shows in its window
  chrome and what a file manager indexes. Without it the input filename is used.
- `--lang` defaults to `tr`. It sets the document language, which affects
  hyphenation and how assistive technology reads the file.
- `--css` appends a stylesheet **after** the built-in one, so its rules win. Read
  `references/typography.md` before writing one; it lists the four built-in rules
  that must survive any override.
- Syntax highlighting is not available (it would need Pygments, which is not in the
  image). Code blocks render as plain monospaced text.

The script validates before rendering and prints one `ERROR: <field>: <reason>` line
per problem: a missing input, a non-`.pdf` output name, an unwritable output
directory, a file that is not UTF-8, an empty source. Fix all reported lines, then
re-run.

## Task: structured report to PDF

```bash
python3 scripts/report_pdf.py <spec.json> <output.pdf> [--validate-only]
```

Use `--validate-only` first when the spec is being generated programmatically: it
checks the whole spec and exits without writing anything, so a malformed spec costs
one fast run instead of a broken PDF.

The spec:

```json
{
  "title": "Q3 Operations Review",
  "subtitle": "Regional summary",
  "author": "Operations",
  "date": "2026-08-18",
  "pageSize": "A4",
  "titlePage": true,
  "blocks": [
    { "type": "heading", "level": 1, "text": "Summary" },
    { "type": "paragraph", "text": "Volume rose 12% quarter over quarter." },
    { "type": "bullets", "items": ["Istanbul led growth", "Izmir was flat"] },
    {
      "type": "table",
      "columns": ["Region", "Volume", "Change"],
      "rows": [["Istanbul", "4,210", "+18%"], ["Izmir", "1,904", "0%"]],
      "widths": [50, 25, 25],
      "caption": "Table 1 — Volume by region"
    },
    { "type": "pagebreak" },
    { "type": "heading", "level": 2, "text": "Detail" },
    { "type": "spacer", "height": 18 }
  ]
}
```

Field rules:

| Field       | Required | Notes                                                        |
|-------------|----------|--------------------------------------------------------------|
| `title`     | yes      | Non-empty string. Also written to the PDF metadata.          |
| `subtitle`  | no       | Shown on the title page and in the PDF subject field.        |
| `author`    | no       | Shown on the title page and in the PDF author field.         |
| `date`      | no       | Free-form string; not parsed.                                |
| `pageSize`  | no       | `"A4"` (default) or `"letter"`.                              |
| `titlePage` | no       | `true` by default. Set `false` to start straight at content. |
| `blocks`    | yes      | At least one block.                                          |

Block types: `heading` (`level` 1–3), `paragraph`, `bullets`, `table`, `spacer`
(`height` in points, 1–500), `pagebreak`.

Table columns accept relative `widths` — the numbers are normalised to the frame
width, so `[50, 25, 25]` and `[2, 1, 1]` produce the same layout. Every row must
have exactly as many cells as there are columns; the validator says which row is
wrong if one does not.

Validation reports **every** problem in a single run, addressed by path:

```
ERROR: title: required, must be a non-empty string
ERROR: blocks[3].rows[1]: has 2 cells but 3 columns are declared
ERROR: blocks[5].type: must be one of heading, paragraph, bullets, table, spacer, pagebreak, found 'quote'
```

Correct all of them at once and re-run. Do not fix one and retry.

## Task: validate a PDF

```bash
python3 scripts/validate_pdf.py <file.pdf> [--strict]
```

Checks that the file exists, begins with a `%PDF-` header, ends with a `%%EOF`
trailer, and has at least one page; then reports the page count and inspects each
page's content stream for anything actually painted on it.

Exit codes:

- `0` — the file is a usable PDF. A `WARNING:` line may still appear if some pages
  look blank; read it before delivering.
- `1` — do not hand the file to the user. Missing, empty, not a PDF, truncated,
  no pages, or every inspected page blank.

`--strict` promotes blank-page warnings to failures. Use it when the document is
supposed to be dense throughout, such as a generated report.

The check is stdlib-only and reads the PDF's own structure, so it works on files
this skill did not produce. On PDFs that store their pages in compressed object
streams it prints a `NOTE:` and reports the page count from the page tree without
the blank-page inspection — an honest partial result rather than a false pass.

## Working pattern

1. **Plan.** Decide Markdown or JSON spec from the shape of the document, not from
   the shape of the content you happen to have.
2. **Validate.** Write the source, then run `--validate-only` (report path) or let
   `md_to_pdf.py`'s own pre-flight run. Fix every `ERROR:` line reported.
3. **Execute.** Render.
4. **Verify.** Run `validate_pdf.py`. Only after it exits 0 is there a document to
   talk about.

## Reference

`references/typography.md` — the fonts that exist in this image, why they are pinned,
page geometry and `@page` overrides, and the stylesheet rules that must not be
dropped. Read it when overriding CSS or when characters render as boxes.

## Script quick reference

| Task                                  | Script                     | Command |
|---------------------------------------|----------------------------|---------|
| Narrative document, prose-heavy        | `scripts/md_to_pdf.py`     | `python3 scripts/md_to_pdf.py in.md out.pdf --title "T"` |
| Convert existing Markdown to PDF       | `scripts/md_to_pdf.py`     | `python3 scripts/md_to_pdf.py notes.md notes.pdf` |
| Custom fonts, margins, page size       | `scripts/md_to_pdf.py`     | `python3 scripts/md_to_pdf.py in.md out.pdf --css style.css` |
| Report with title page and tables      | `scripts/report_pdf.py`    | `python3 scripts/report_pdf.py spec.json out.pdf` |
| Check a spec before rendering          | `scripts/report_pdf.py`    | `python3 scripts/report_pdf.py spec.json out.pdf --validate-only` |
| Confirm a PDF is real and not blank    | `scripts/validate_pdf.py`  | `python3 scripts/validate_pdf.py out.pdf` |
| Same, failing on any blank page        | `scripts/validate_pdf.py`  | `python3 scripts/validate_pdf.py out.pdf --strict` |

Every script accepts `--help`.
