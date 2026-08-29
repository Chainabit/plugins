#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render the SAME deck spec as a PDF, one page per slide.

A deck is asked for as a PDF at least as often as it is asked for as a .pptx —
usually as well as, not instead of: "make the slides, and a PDF I can send
round". Until this script existed the skill had one output and the request had
two, so the second half was either skipped or answered with a Markdown outline.

It is a RENDERER, not a converter. There is no LibreOffice in the sandbox image
and adding one would cost more disk than the whole image has; more importantly a
converter would take the .pptx as its input and inherit whatever that file
happens to be. This takes the spec, and imports `deck_pptx` for the parts that
decide what a slide looks like — `validate_spec`, `build_geometry`, `plan_slide`,
`THEMES`, and the type ladders. The two outputs therefore agree by construction
rather than by inspection: the same spec is rejected by both for the same
reasons, and a title that fits at 32pt in the .pptx is set at 32pt here.

That also means the two cannot drift. A layout change made in deck_pptx.py
changes this file's output in the same commit, because there is only one copy of
the decision.

Usage:
    python3 deck_pdf.py spec.json deck.pdf [--validate-only]

Exit codes match deck_pptx.py: 0 on success, 1 with `ERROR:` lines on stderr
when the spec is wrong. A spec that builds a .pptx builds a PDF.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# The layout engine lives in the sibling script. Importing it rather than
# copying it is the whole point of this file — see the module docstring.
from deck_pptx import (
    ASPECTS,
    BULLET_SPACING_PT,
    LINE_HEIGHT_EM,
    THEMES,
    build_geometry,
    check_fit,
    plan_slide,
    validate_spec,
)

# DejaVu is what makes Turkish render. ReportLab's built-in Helvetica is
# WinAnsi-encoded, which has ç and ö but NOT ş, ğ, ı or İ — so a Turkish deck
# would come out with holes in exactly the words that carry the meaning. The
# image installs fonts-dejavu-core for this reason (see the Dockerfile), and
# these are the paths that package lays down.
DEJAVU_REGULAR = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
DEJAVU_BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

#: Rough cap-height fraction, used to place the first baseline inside a box so
#: that the block's visual top lands where the .pptx's does. The exact figure
#: differs per face; being a point or two out is invisible next to getting the
#: line spacing right, which comes from the shared LINE_HEIGHT_EM.
ASCENT_FRACTION = 0.80

#: Matches `add_textbox`'s default inset in deck_pptx.py, and the 0.22 the
#: comparison columns pass. Kept as named constants rather than repeated
#: literals so the two files can be diffed for drift.
INSET_DEFAULT_IN = 0.12
INSET_COLUMN_IN = 0.22
INSET_VERTICAL_IN = 0.05


def register_fonts() -> tuple[str, str]:
    """Returns the (regular, bold) font names to draw with.

    Falls back to Helvetica when DejaVu is absent rather than failing: a deck in
    a language Helvetica covers is still worth producing, and a hard failure
    here would make the PDF path less reliable than the .pptx one for no gain.
    The caller warns so the degradation is visible rather than silent.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if not (os.path.isfile(DEJAVU_REGULAR) and os.path.isfile(DEJAVU_BOLD)):
        print(
            'WARNING: fonts: DejaVu not found; falling back to Helvetica. '
            'Characters outside Latin-1 (Turkish ş ğ ı İ among them) will not '
            'render.',
            file=sys.stderr,
        )
        return 'Helvetica', 'Helvetica-Bold'

    pdfmetrics.registerFont(TTFont('DejaVuSans', DEJAVU_REGULAR))
    pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', DEJAVU_BOLD))
    return 'DejaVuSans', 'DejaVuSans-Bold'


def wrap(text: str, font: str, size: int, width_pt: float) -> list[str]:
    """Wrap using the font's real metrics.

    `plan_slide` chose the point size with a character-width ESTIMATE, which is
    what keeps it dependency-free and identical across both renderers. Drawing
    is a different job: here the true widths are available, so using them
    produces the tidier line breaks without touching the size that was already
    agreed.
    """
    from reportlab.lib.utils import simpleSplit

    return simpleSplit(text, font, size, width_pt) or ['']


def to_pdf_y(top_in: float, slide_height_in: float) -> float:
    """PowerPoint measures down from the top; PDF measures up from the bottom."""
    return (slide_height_in - top_in) * 72.0


class Page:
    """One slide's worth of drawing, in the geometry the shared engine chose."""

    def __init__(self, canvas, geometry: dict, theme: dict, fonts: tuple[str, str]):
        self.canvas = canvas
        self.geometry = geometry
        self.theme = theme
        self.regular, self.bold = fonts
        self.height_in = geometry['slide'][1]

    def _set_fill(self, hex_colour: str) -> None:
        from reportlab.lib.colors import HexColor

        self.canvas.setFillColor(HexColor(f'#{hex_colour}'))

    def fill_rect(self, rectangle, hex_colour: str) -> None:
        left, top, width, height = rectangle
        self._set_fill(hex_colour)
        self.canvas.rect(
            left * 72.0,
            to_pdf_y(top + height, self.height_in),
            width * 72.0,
            height * 72.0,
            stroke=0,
            fill=1,
        )

    def text_block(
        self,
        rectangle,
        lines: list[str],
        size: int,
        hex_colour: str,
        *,
        bold: bool = False,
        inset_in: float = INSET_DEFAULT_IN,
        centred: bool = False,
        spacing_pt: float = 0.0,
    ) -> None:
        """Draw wrapped paragraphs from the top of the box downwards."""
        left, top, width, _ = rectangle
        font = self.bold if bold else self.regular
        usable_pt = (width - 2 * inset_in) * 72.0

        self.canvas.setFont(font, size)
        self._set_fill(hex_colour)

        baseline = (
            to_pdf_y(top + INSET_VERTICAL_IN, self.height_in) - size * ASCENT_FRACTION
        )
        for paragraph in lines:
            for wrapped in wrap(paragraph, font, size, usable_pt):
                if centred:
                    self.canvas.drawCentredString(
                        (left + width / 2) * 72.0, baseline, wrapped
                    )
                else:
                    self.canvas.drawString((left + inset_in) * 72.0, baseline, wrapped)
                baseline -= size * LINE_HEIGHT_EM
            baseline -= spacing_pt

    def bullets(self, rectangle, items: list[str], size: int, hex_colour: str,
                inset_in: float = INSET_DEFAULT_IN) -> None:
        """Bulleted list, matching `fill_bullets`'s 0.3in hanging indent."""
        indent_in = 0.3
        left, top, width, _ = rectangle
        usable_pt = (width - 2 * inset_in - indent_in) * 72.0

        self.canvas.setFont(self.regular, size)
        self._set_fill(hex_colour)

        baseline = (
            to_pdf_y(top + INSET_VERTICAL_IN, self.height_in) - size * ASCENT_FRACTION
        )
        for item in items:
            # The marker sits on the FIRST line's baseline only; continuation
            # lines align to the text column, which is what makes a wrapped
            # bullet read as one item rather than as several.
            self.canvas.drawString((left + inset_in) * 72.0, baseline, '•')
            for line in wrap(item, self.regular, size, usable_pt):
                self.canvas.drawString((left + inset_in + indent_in) * 72.0, baseline, line)
                baseline -= size * LINE_HEIGHT_EM
            baseline -= BULLET_SPACING_PT


def render_title(page: Page, slide: dict, sizes: dict) -> None:
    geometry, theme = page.geometry, page.theme
    page.fill_rect(geometry['cover_bar'], theme['accent'])
    page.text_block(
        geometry['cover_title'], [slide['title']], sizes['title'], theme['ink'], bold=True
    )
    if slide.get('subtitle'):
        page.text_block(
            geometry['cover_subtitle'], [slide['subtitle']], sizes['subtitle'], theme['muted']
        )
    if slide.get('meta'):
        page.text_block(
            geometry['cover_meta'], [slide['meta']], sizes['meta'], theme['muted']
        )


def render_heading(page: Page, text: str, size: int) -> None:
    geometry, theme = page.geometry, page.theme
    page.text_block(geometry['heading'], [text], size, theme['ink'], bold=True)
    page.fill_rect(geometry['rule'], theme['rule'])


def render_content(page: Page, slide: dict, sizes: dict) -> None:
    geometry, theme = page.geometry, page.theme
    render_heading(page, slide['title'], sizes['title'])
    page.bullets(geometry['body'], list(slide['bullets']), sizes['body'], theme['body'])
    if slide.get('note'):
        page.text_block(
            geometry['note'], [slide['note']], sizes['note'], theme['muted']
        )


def render_comparison(page: Page, slide: dict, sizes: dict) -> None:
    geometry, theme = page.geometry, page.theme
    render_heading(page, slide['title'], sizes['title'])

    for index, side in enumerate(('left', 'right')):
        column = slide[side]
        shift = geometry['column_offset'] * index

        heading_rect = list(geometry['column_heading'])
        heading_rect[0] += shift
        page.fill_rect(tuple(heading_rect), theme['surface'])
        page.text_block(
            tuple(heading_rect), [column['heading']], sizes[f'{side}_heading'],
            theme['accent'], bold=True, inset_in=INSET_COLUMN_IN,
        )

        body_rect = list(geometry['column_body'])
        body_rect[0] += shift
        page.fill_rect(tuple(body_rect), theme['surface'])
        page.bullets(
            tuple(body_rect), list(column['bullets']), sizes[f'{side}_body'],
            theme['body'], inset_in=INSET_COLUMN_IN,
        )


def render_closing(page: Page, slide: dict, sizes: dict) -> None:
    geometry, theme = page.geometry, page.theme

    bar = list(geometry['cover_bar'])
    bar[0] = (geometry['slide'][0] - bar[2]) / 2
    bar[1] = geometry['closing_title'][1] - 0.35
    page.fill_rect(tuple(bar), theme['accent'])

    page.text_block(
        geometry['closing_title'], [slide['title']], sizes['title'], theme['ink'],
        bold=True, centred=True,
    )
    if slide.get('subtitle'):
        page.text_block(
            geometry['closing_subtitle'], [slide['subtitle']], sizes['subtitle'],
            theme['muted'], centred=True,
        )
    if slide.get('contact'):
        page.text_block(
            geometry['closing_contact'], [slide['contact']], sizes['contact'],
            theme['muted'], centred=True,
        )


RENDERERS = {
    'title': render_title,
    'content': render_content,
    'comparison': render_comparison,
    'closing': render_closing,
}


def build_pdf(spec: dict, geometry: dict, output: str) -> None:
    from reportlab.pdfgen import canvas as pdf_canvas

    theme = THEMES[spec.get('theme', 'light')]
    fonts = register_fonts()
    width_pt = geometry['slide'][0] * 72.0
    height_pt = geometry['slide'][1] * 72.0

    document = pdf_canvas.Canvas(output, pagesize=(width_pt, height_pt))
    document.setTitle(spec['title'])
    document.setAuthor(spec.get('author') or '')
    document.setSubject(spec.get('subtitle') or '')

    for spec_slide in spec['slides']:
        page = Page(document, geometry, theme, fonts)
        page.fill_rect((0, 0, geometry['slide'][0], geometry['slide'][1]), theme['background'])
        sizes, _ = plan_slide(spec_slide, geometry)
        RENDERERS[spec_slide['layout']](page, spec_slide, sizes)
        document.showPage()

    document.save()


# --- CLI --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    return_parser = argparse.ArgumentParser(
        prog='deck_pdf.py',
        description=(
            'Render a deck spec as a PDF, one page per slide, using the same '
            'layouts, palette and type scale as deck_pptx.py.'
        ),
        epilog=(
            'Example:\n'
            '  python3 deck_pdf.py spec.json deck.pdf\n\n'
            'Takes the SPEC, not the .pptx — there is no PowerPoint converter in\n'
            'the sandbox. Build both outputs from one spec and they agree by\n'
            'construction.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    return_parser.add_argument('spec', help='path to the JSON spec file')
    return_parser.add_argument('output', help='path to write the .pdf to')
    return_parser.add_argument(
        '--validate-only',
        action='store_true',
        help='check the spec and exit without writing a PDF',
    )
    return return_parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    problems: list[str] = []

    if not args.output.lower().endswith('.pdf'):
        problems.append(f'output: {args.output} must end in .pdf')
    output_directory = os.path.dirname(os.path.abspath(args.output))
    if not os.path.isdir(output_directory):
        problems.append(f'output: directory {output_directory} does not exist')
    elif not os.access(output_directory, os.W_OK):
        problems.append(f'output: directory {output_directory} is not writable')

    try:
        with open(args.spec, 'r', encoding='utf-8') as handle:
            spec = json.load(handle)
    except FileNotFoundError:
        print(f'ERROR: spec: {args.spec} does not exist', file=sys.stderr)
        return 1
    except IsADirectoryError:
        print(f'ERROR: spec: {args.spec} is a directory, expected a JSON file', file=sys.stderr)
        return 1
    except PermissionError:
        print(f'ERROR: spec: no permission to read {args.spec}', file=sys.stderr)
        return 1
    except UnicodeDecodeError as exc:
        print(f'ERROR: spec: {args.spec} is not valid UTF-8 ({exc.reason})', file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(
            f'ERROR: spec: {args.spec} is not valid JSON — {exc.msg} '
            f'at line {exc.lineno}, column {exc.colno}',
            file=sys.stderr,
        )
        return 1

    problems.extend(validate_spec(spec))

    aspect = spec.get('aspect', '16:9') if isinstance(spec, dict) else None
    geometry = build_geometry(aspect) if aspect in ASPECTS else None
    if geometry is not None:
        problems.extend(check_fit(spec, geometry))

    if problems:
        for problem in problems:
            print(f'ERROR: {problem}', file=sys.stderr)
        return 1

    if args.validate_only:
        print(f"OK: {args.spec} is a valid deck spec ({len(spec['slides'])} slide(s))")
        return 0

    try:
        import reportlab  # noqa: F401
    except ImportError:
        print(
            "ERROR: environment: the 'reportlab' package is missing. Run "
            'chainabit-env to see what this container has, and install it only '
            'if that report says installs are permitted.',
            file=sys.stderr,
        )
        return 1

    try:
        build_pdf(spec, geometry, args.output)
    except PermissionError:
        print(f'ERROR: output: no permission to write {args.output}', file=sys.stderr)
        return 1
    except OSError as exc:
        print(f'ERROR: output: could not write {args.output}: {exc}', file=sys.stderr)
        return 1

    size = os.path.getsize(args.output)
    print(f"OK: wrote {args.output} ({size} bytes, {len(spec['slides'])} page(s))")
    print(f'Next: python3 scripts/validate_pdf.py {args.output} (from the pdf skill)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
