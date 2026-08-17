# Typography and page geometry

Read this before overriding a stylesheet, before debugging characters that render
as boxes, and before changing page size or margins.

## Fonts available in the sandbox

The image installs `fonts-dejavu-core` and nothing else. That gives exactly three
families, and no others may be assumed to exist:

| CSS family name     | Weights available | Use for                 |
|---------------------|-------------------|-------------------------|
| `DejaVu Sans`       | regular, bold     | body text, headings     |
| `DejaVu Serif`      | regular, bold     | body text, if preferred |
| `DejaVu Sans Mono`  | regular, bold     | code, fixed-width data  |

There is no italic face in the core package. Italic markup still renders, but the
result is a synthesised oblique rather than a designed italic — acceptable for
emphasis, not for long passages.

There is no network at runtime, so `@font-face` with a remote `url()` silently
fails and falls back. A webfont can only be used if the font file is already on
disk in the workspace, referenced by a local path.

## Why the font is pinned rather than left to defaults

The Latin Extended-A range — `ç ş ğ ı İ ö ü` and their capitals — is what Turkish
needs and what most default font stacks do not cover in this image. When a glyph
is missing, the renderer substitutes a notdef box, so the text does not error, it
just becomes unreadable. Both scripts therefore name a family explicitly:

- `md_to_pdf.py` sets `font-family: "DejaVu Sans"` on `html, body` and on every
  heading, so nothing inherits an unknown default.
- `report_pdf.py` registers `DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` as TrueType
  fonts with ReportLab. This matters more there: ReportLab's built-in Helvetica is
  a Type 1 font restricted to WinAnsi encoding, which has no `ğ`, `ş`, `İ`, or `ı`
  at all. If the TTF files are not found the script warns and falls back — treat
  that warning as a defect in any Turkish document.

Verify by generating a document containing `Çağrı Şişli İğne ıspanak öğün ürün`
and reading the glyphs back out of the PDF, not by trusting exit code 0.

## Page geometry

`md_to_pdf.py` uses an `@page` rule: A4, margins `22mm 18mm 20mm 18mm`, and a
`counter(page) / counter(pages)` footer. Override any of it with `--css`:

```css
@page {
  size: A4 landscape;
  margin: 15mm;
  @bottom-center { content: ""; }   /* drop the page numbers */
}
```

`report_pdf.py` uses a 50pt left/right margin, 54pt top, 48pt bottom, and honours
`"pageSize": "A4" | "letter"` from the spec. Table column widths are given as
relative numbers in `widths` and normalised to the frame width, so they do not
have to add up to 100.

## Rules worth keeping when overriding

The built-in stylesheet contains four rules that exist because their absence
produces visibly broken output. An override that removes them will look fine in
one test document and fail on the next:

- `page-break-after: avoid` on headings — otherwise a heading strands itself at
  the foot of a page with its section overleaf.
- `thead { display: table-header-group; }` — repeats a table's header row when the
  table crosses a page boundary.
- `tr { page-break-inside: avoid; }` — stops a single row being split in half.
- `pre { white-space: pre-wrap; }` — a PDF has no horizontal scrollbar, so an
  unwrapped long code line simply runs off the paper and is lost.

## Colour

Colours are stated as hex values in a restrained grey scale (`#111827` text,
`#6b7280` secondary, `#e5e7eb` rules, `#f3f4f6` table headers). Print output has no
dark mode to accommodate, so the palette is fixed rather than theme-aware. Keep
text near-black: mid-grey body text that reads acceptably on a screen is thin and
washed out on paper.
