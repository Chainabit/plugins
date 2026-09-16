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

`md_to_pdf.py` uses a print-first `@page` rule and supports the documented
`--page-size`, `--orientation`, `--font`, and `--margin` inputs. `--margin`
takes either a single number of points (applied to all four sides) or a JSON
object with `top`/`right`/`bottom`/`left`. It deliberately rejects arbitrary
`--css`: CSS is not a public styling boundary and accepting it would allow a
prompt to bypass the audited document system.

`report_pdf.py` defaults to a 50pt left/right margin, 54pt top, 48pt bottom,
and honours `"pageSize": "A4" | "letter"` and an optional `"margin"` (the same
number-or-object shape) from the spec. Table column widths are given as
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

The default print palette is the light projection of the composed
`skill-brand-defaults` profile: `#FFFFFF` background, `#101828` headings,
`#364153` body, `#6A7282` muted text, `#E5E7EB` rules, and `#327B61` accent.
Print output intentionally has no dark-mode variant. A competing user brand
must use the structured report specification and supply a complete `palette`
object (including `background`, `surface`, `ink`, `body`, `muted`, `rule`,
`accent`, and `accentInk`); it replaces every default role rather than mixing
with Chainabit colours. Keep text near-black: mid-grey body text that reads
acceptably on a screen is thin and washed out on paper.
