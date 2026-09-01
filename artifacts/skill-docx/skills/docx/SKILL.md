---
name: docx
description: "Builds real .docx documents inside the sandbox from a JSON spec - headings, styled paragraphs, bulleted and numbered lists, tables and page breaks - and verifies the result. Use when the requested deliverable is a Word document: the request mentions Word, docx, .docx, a document, a report, a proposal, a resume or CV, a letter, a contract, \"belge\", \"rapor\", \"özgeçmiş\", or anything meant to be opened in Word, Pages, or Google Docs. Also use to check whether an existing .docx is a valid document or has raw Markdown rendered into its text. Do NOT use when Markdown or plain text is what was asked for - write that file directly instead; do NOT use for a PDF, a spreadsheet, a slide deck, or an HTML page."
license: Apache-2.0
metadata:
  version: 1.2.0
---

# Word document generation

## Overview

Two scripts. One builds a document from a JSON spec, the other proves the result is
a real document that says something.

- **`build_docx.py`** — JSON spec in, `.docx` out. Formatting is declared as block
  types and fields, never typed into the text.
- **`validate_docx.py`** — the exit gate. Confirms the file is a genuine Word
  document and reports the defects that are invisible from a file listing: a
  document that opens blank, and raw Markdown rendered as literal characters.

All script paths in this document are **relative to this skill's own directory**. If
the skill is materialised at `/workspace/.skills/docx/`, then `scripts/build_docx.py`
means `/workspace/.skills/docx/scripts/build_docx.py`. The scripts write only to the
output path they are given.

`python-docx` is **already installed** in the sandbox image, so these scripts
need no setup step. What else is installed, and whether you may install more,
are properties of the container and the lease's policy rather than of this
skill, and they change — call the `workspace.env` tool rather than assuming
either way.

### When this is the wrong tool

If the user asked for **Markdown** or plain text, write that file. Producing one does
not need this skill, and handing back a `.docx` instead is a substitution the user
did not ask for.

The reverse substitution is the failure this skill exists to prevent: **never write
Markdown text and name it `.docx`.** Renaming does not convert. A file containing
`# Experience` saved as `resume.docx` opens as garbage in Word, and
`validate_docx.py` rejects it — as does every guard between here and the user.

## Quick start

```bash
cat > /workspace/spec.json <<'EOF'
{
  "properties": { "title": "Saha Raporu", "author": "Platform Ekibi" },
  "blocks": [
    { "type": "heading",   "text": "Yönetici Özeti", "level": 1 },
    { "type": "paragraph", "text": "Faturalandırma platformu geçişi tamamlandı." },
    { "type": "bullets",   "items": ["Gecikme azaldı", "Yinelenen yazıcılar kaldırıldı"] },
    { "type": "table",
      "columns": ["Bölge", "Adet"],
      "rows": [["İstanbul", "4210"], ["İzmir", "1904"]] }
  ]
}
EOF

python3 scripts/build_docx.py /workspace/spec.json /workspace/out/rapor.docx
python3 scripts/validate_docx.py /workspace/out/rapor.docx
```

## Task: build a document

```bash
python3 scripts/build_docx.py <spec.json> <output.docx> [--validate-only]
```

`--validate-only` checks the spec and writes nothing. Use it when assembling a spec
programmatically and you want the errors before committing to a build.

### Spec structure

```jsonc
{
  "properties": {           // optional
    "title":   "...",
    "author":  "...",
    "subject": "..."
  },
  "blocks": [               // required, at least one non-pagebreak block
    // see block types below
  ]
}
```

The optional top-level `font` is an explicit safe family override. When it is
absent, every Word style and run defaults to the runtime-owned Chainabit
`IBM Plex Sans` family.

### Block types

| `type`      | Fields                                                     | Notes |
|-------------|------------------------------------------------------------|-------|
| `heading`   | `text`, `level` (1–6)                                       | A real Word heading, so it appears in the navigation pane and any generated table of contents. |
| `paragraph` | `text`, `bold`, `italic`, `alignment`                       | `alignment` is `left`, `center`, `right`, or `justify`. `text` may be empty for a spacer. |
| `bullets`   | `items` (array of strings)                                  | Applies the `List Bullet` style — real list formatting, not a `-` character. |
| `numbered`  | `items` (array of strings)                                  | Applies the `List Number` style, so items renumber when edited. |
| `table`     | `columns`, `rows`, `headerBold`                             | Every row must have exactly as many cells as there are columns. |
| `pagebreak` | —                                                           | A document of nothing but page breaks is rejected: it opens blank. |

### Formatting is declared, never typed

The builder **refuses** Markdown syntax inside block text:

```
ERROR: blocks[0].text: contains literal Markdown (heading). Word will render the
characters as typed -- use {"type": "heading", "level": N}.
```

This is not pedantry. A pipeline that writes `**Senior Engineer**` into a paragraph
produces a file that opens, looks populated, and is wrong to the first human who
reads it. Because the refusal happens at spec time, the fix is a field change rather
than a rebuild-and-hope.

### Error output

Every problem in the spec is reported at once, as `ERROR: <field>: <reason>`. Fix
them together rather than rebuilding once per error.

## Task: validate a document

```bash
python3 scripts/validate_docx.py <file.docx> [--strict]
```

Exit `0` means the document is real and has readable content. Exit `1` means it is
not usable, and the reason is printed as `ERROR: <category>: <detail>`:

| Category      | What it caught |
|---------------|----------------|
| `file`        | Missing, empty, unreadable, or a directory. |
| `format`      | Not a ZIP container; a ZIP that is not a Word document; corrupt entries; an unparseable body. |
| `content`     | Structurally valid but every paragraph is empty, or the rendered text contains literal Markdown. |
| `environment` | This Python cannot parse XML, so nothing could be inspected. Not a verdict on the file. |

`WARNING:` lines report a document that is usable but probably not what was intended
— paragraphs beginning with a `-` or `*` character, which read as a list but will not
indent or renumber. `--strict` turns warnings into a failing exit code.

The validator is stdlib-only and does not import `python-docx`, so it works on
documents this skill did not produce.

## Working pattern

1. Write the spec.
2. Build. A non-zero exit means the spec was wrong; the message names the field.
3. **Validate.** A build that succeeded is not yet a document that is right.
4. Only then tell the user the document is ready.

Step 3 is not optional. `build_docx.py` reports that it wrote bytes to a path; only
`validate_docx.py` reports that those bytes are a document.

## Script quick reference

| Script | Purpose | Exit 0 means |
|--------|---------|--------------|
| `scripts/build_docx.py` | Spec → `.docx` | The document was written. |
| `scripts/validate_docx.py` | `.docx` → verdict | The document is real and has content. |
