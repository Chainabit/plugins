<!-- SPDX-License-Identifier: Apache-2.0 -->

# Typography and page geometry

Read this before overriding a stylesheet, before debugging characters that render
as boxes, and before changing page size or margins.

## Fonts available in the sandbox

The prepared artifact runtime owns a versioned IBM Plex Sans asset directory,
advertised by `CHAINABIT_ARTIFACT_FONT_DIR`. Regular and semibold TrueType files
are used by PDF renderers; matching WOFF2 files are available to static-web
generators. IBM Plex Sans Arabic is the explicit script companion. Rendering is
offline and fails with typed `font_failure` when declared assets are missing;
an undocumented operating-system font is never accepted as the default.

Code remains intentionally monospaced through the approved mono stack. An
explicit safe user font may override the brand default only when the runtime can
resolve that requested family deterministically.

## Why the font is pinned rather than left to defaults

The Latin Extended-A range — `ç ş ğ ı İ ö ü` and their capitals — is what Turkish
needs and what most default font stacks do not cover in this image. When a glyph
is missing, the renderer substitutes a notdef box, so the text does not error, it
just becomes unreadable. Both scripts therefore name a family explicitly:

- The rich HTML adapter embeds the runtime-provided IBM Plex Sans TrueType
  assets; it does not depend on a host font or network fetch.
- A ReportLab adapter registers the same approved TrueType fonts and must fail
  with `font_failure` when coverage or embedding cannot be verified; it must never
  silently substitute another default for Turkish content.

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
