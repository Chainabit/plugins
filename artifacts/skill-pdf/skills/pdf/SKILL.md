---
name: pdf
description: Create, validate, and manipulate secure PDF artifacts. Inspect required capabilities first and prefer the highest-quality available backend; never silently downgrade rich content.
license: Apache-2.0
metadata:
  version: 5.2.2
---

# PDF artifact system

Use this skill for documents and reports intended to be delivered as PDFs. The production contract requires the audited WeasyPrint renderer, pypdf verification, and runtime-provided IBM Plex Sans assets. A capability probe must succeed before a render is attempted; a missing dependency or font is a platform failure, never invalid user input.

```bash
python3 scripts/pdf_tool.py capabilities
python3 scripts/pdf_tool.py diagnose markdown report.md
python3 scripts/md_to_pdf.py report.md report.pdf
python3 scripts/report_pdf.py report.json report.pdf
python3 scripts/validate_pdf.py report.pdf
```

The two generation commands above are the registered production entrypoints.
Use `pdf_tool.py` for capability inspection and diagnosis only; output produced
through an unregistered wrapper cannot acquire publication proof.

Sources passed to those entrypoints must be regular files inside the workspace.
Write JSON or Markdown inputs first; process substitution and `/dev/fd/*` paths
are intentionally rejected by the filesystem boundary. Run validation as its
own command, or join generation and validation with `&&`. Never place a later
command such as `ls` after validation with `;` or a bare newline, because its
zero exit status can hide a rejected PDF. Promote only after
`scripts/validate_pdf.py` itself exits successfully.

Before rendering, infer the document's requirements: Unicode and fonts, Turkish/RTL/CJK shaping, images, tables, Markdown/HTML/CSS, pagination, headers/footers, mathematics, vector graphics, typography, colors, print quality, metadata, accessibility, or manipulation. Resolve a backend only if it advertises every required capability and has tests for that behavior. Missing dependencies and unsupported features are actionable machine-readable failures; do not print raw math, replace glyphs, omit images, flatten tables, or fall back to the text renderer.

The deterministic backend is a diagnostic/basic-text implementation, not an authorized production fallback for a requested professional PDF. It preflights glyphs and rejects unsupported Unicode; success never means replacement characters were emitted. Turkish and other multilingual content use the installed tested Unicode/font backend.

Successful renderer and validator responses use the versioned JSON contracts `chainabit.pdf.execution/v1` and `chainabit.pdf.validation/v1`. Both include the exact output SHA-256. Exit `1` is a deterministic input/artifact rejection; exit `2` means runtime, dependency, timeout, I/O, or internal output failure. Never infer those categories from human-readable stderr.

`capabilities` is the registry source of truth. `diagnose` explains requirements, availability, missing capabilities, and rejection reasons without exposing document content. Current adapters isolate WeasyPrint HTML/CSS, ReportLab structured layout, Pillow image normalization, and pypdf manipulation. WeasyPrint and pypdf are required for the official Markdown delivery path; the other adapters remain explicitly negotiated capabilities.

Security is enforced at adapter boundaries: canonical roots, bounded inputs/images/pages/CSS, safe temporary directories, no network by default, no file URLs or active HTML/SVG, controlled assets, no shell strings, timeouts, cleanup, atomic persistence after verification, deterministic metadata when requested, and structured errors.

Read the deep design and security contract in `references/architecture.md` and typography guidance in `references/typography.md`. Run `python3 -m unittest discover -s tests -v` for the minimal configuration; rich adapter tests must run in a professional environment with their optional dependencies installed.
