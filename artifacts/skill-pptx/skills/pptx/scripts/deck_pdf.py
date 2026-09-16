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
import hashlib
import json
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path

# The layout engine lives in the sibling script. Importing it rather than
# copying it is the whole point of this file — see the module docstring.
from deck_pptx import (
    ASPECTS,
    BULLET_SPACING_PT,
    CHART_TEXT_PT,
    DEFAULT_FONT,
    IMAGE_FIT,
    LINE_HEIGHT_EM,
    SERIES_COLOURS,
    ValidatedImage,
    build_geometry,
    check_fit,
    image_box,
    palette_mode,
    plan_slide,
    preflight_frame,
    resolve_theme,
    validate_spec,
)

# The sandbox image installs the exact IBM release at this deterministic path.
# A missing asset is an infrastructure failure, never permission to fall back to
# Helvetica and silently change the user's typography or lose Turkish glyphs.
FONT_ROOT = os.environ.get(
    'CHAINABIT_ARTIFACT_FONT_DIR', '/opt/chainabit/artifact-fonts/ibm-plex-sans'
)
IBM_PLEX_REGULAR = os.path.join(FONT_ROOT, 'IBMPlexSans-Regular.ttf')
IBM_PLEX_BOLD = os.path.join(FONT_ROOT, 'IBMPlexSans-SemiBold.ttf')

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


def _fontconfig_file(family: str, style: str = "Regular") -> str:
    result = subprocess.run(
        ["fc-match", "-f", "%{family}\n%{file}\n", f"{family}:style={style}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    lines = result.stdout.splitlines()
    if result.returncode or len(lines) < 2 or family.casefold() not in lines[0].casefold():
        raise RuntimeError(f"requested font {family!r} is not installed in the sandbox")
    if not os.path.isfile(lines[1]):
        raise RuntimeError(f"fontconfig returned a missing file for {family!r}")
    return lines[1]


def register_fonts(family: str) -> tuple[str, str]:
    """Register and return the canonical embedded font pair."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if family == DEFAULT_FONT:
        regular_path, bold_path = IBM_PLEX_REGULAR, IBM_PLEX_BOLD
        if not (os.path.isfile(regular_path) and os.path.isfile(bold_path)):
            raise RuntimeError(
                f'canonical IBM Plex Sans assets are unavailable under {FONT_ROOT}'
            )
    else:
        regular_path = _fontconfig_file(family)
        bold_path = _fontconfig_file(family, "Semibold")

    pdfmetrics.registerFont(TTFont('ChainabitDeckRegular', regular_path))
    pdfmetrics.registerFont(TTFont('ChainabitDeckBold', bold_path))
    return 'ChainabitDeckRegular', 'ChainabitDeckBold'


def wrap(text: str, font: str, size: int, width_pt: float) -> list[str]:
    """Wrap using the font's real metrics.

    `plan_slide` chose the point size with a character-width ESTIMATE, which is
    what keeps it dependency-free and identical across both renderers. Drawing
    is a different job: here the true widths are available, so using them
    produces the tidier line breaks without touching the size that was already
    agreed.

    This does NOT use ReportLab's own `reportlab.lib.utils.simpleSplit`: it
    tokenizes purely on `str.split()`, ASCII whitespace only. A Chinese or
    Japanese sentence is written with no spaces at all, so the whole sentence
    became ONE token that `simpleSplit` places on a line regardless of width
    -- it ran off the slide edge instead of wrapping, silently, at exit 0.
    The walk below breaks at the last whitespace seen since the previous
    break when one exists (ordinary word-wrap for space-delimited scripts,
    Latin/Cyrillic/Arabic alike) and between characters when it does not --
    which is what a script with no word-separating whitespace needs, and
    what CSS `line-break`/`word-break` already do in every browser and in
    WeasyPrint for exactly the same reason.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    def width(value: str) -> float:
        return stringWidth(value, font, size)

    lines: list[str] = []
    for paragraph in text.split('\n'):
        if not paragraph:
            lines.append('')
            continue
        line = ''
        last_space = -1  # index into `line` of the most recent whitespace character
        for char in paragraph:
            candidate = line + char
            if not line or width(candidate) <= width_pt:
                line = candidate
                if char.isspace():
                    last_space = len(line) - 1
                continue
            if last_space >= 0:
                lines.append(line[:last_space].rstrip())
                line = line[last_space + 1:].lstrip() + char
            else:
                lines.append(line)
                line = char
            last_space = len(line) - 1 if char.isspace() else -1
        lines.append(line.rstrip())
    return lines or ['']


def to_pdf_y(top_in: float, slide_height_in: float) -> float:
    """PowerPoint measures down from the top; PDF measures up from the bottom."""
    return (slide_height_in - top_in) * 72.0


# Hebrew + Arabic (and their extension blocks) -- the same range skill-pdf's
# own capability probe uses to detect RTL content. Duplicated here rather
# than imported: independently materialised skill bundles cannot import one
# another at runtime (see scaffold_site.py's identical LIGHT_BACKGROUND/
# DEFAULT_ACCENT precedent), so each keeps its own copy of the small pieces
# of shared reasoning it needs.
_RTL_RANGE = ('֐', 'ࣿ')


def _spec_is_predominantly_rtl(spec: dict) -> bool:
    """Whether the deck's own slide text is majority right-to-left.

    Walks every string value under `slides` -- title, subtitle, bullets,
    notes, meta, chart labels, whatever the layout carries -- and compares
    strong-RTL characters against strong-LTR (Latin) ones, the same
    ratio-not-presence signal as skill-pdf's `resolve_direction`, so a
    handful of embedded Latin words or a URL does not trip this on its own.
    """
    pending: list = list(spec.get('slides', []) if isinstance(spec, dict) else [])
    rtl = latin = 0
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            rtl += sum(1 for c in value if _RTL_RANGE[0] <= c <= _RTL_RANGE[1])
            latin += sum(1 for c in value if c.isascii() and c.isalpha())
        elif isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return rtl > latin


class Page:
    """One slide's worth of drawing, in the geometry the shared engine chose."""

    def __init__(
        self,
        canvas,
        geometry: dict,
        theme: dict,
        fonts: tuple[str, str],
        image_assets: dict[str, ValidatedImage],
    ):
        self.canvas = canvas
        self.geometry = geometry
        self.theme = theme
        self.regular, self.bold = fonts
        self.height_in = geometry['slide'][1]
        #: Exact bytes already opened, bounded and decoded by the input owner.
        self.image_assets = image_assets

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

    def picture(self, rectangle, reference: dict, mode: str) -> None:
        """Draw one validated picture, letterboxed or cropped to fill the box.

        `cover` draws the picture larger than its box and clips to the box, so
        the visible crop matches the crop fractions the .pptx writes. Neither
        mode ever changes the picture's aspect ratio.
        """
        asset = self.image_assets[str(reference['path'])]
        width_px, height_px = asset.width, asset.height
        left, top, width, height = rectangle

        if mode != 'cover':
            drawn = image_box(rectangle, (width_px, height_px), mode)
            self._draw_image(asset, drawn)
            return

        image_ratio = width_px / max(height_px, 1)
        box_ratio = width / max(height, 0.01)
        if image_ratio > box_ratio:
            drawn_width, drawn_height = height * image_ratio, height
        else:
            drawn_width, drawn_height = width, width / image_ratio

        self.canvas.saveState()
        clip = self.canvas.beginPath()
        clip.rect(
            left * 72.0, to_pdf_y(top + height, self.height_in),
            width * 72.0, height * 72.0,
        )
        self.canvas.clipPath(clip, stroke=0, fill=0)
        self._draw_image(asset, (
            left + (width - drawn_width) / 2,
            top + (height - drawn_height) / 2,
            drawn_width,
            drawn_height,
        ))
        self.canvas.restoreState()

    def _draw_image(self, asset: ValidatedImage, rectangle) -> None:
        from reportlab.lib.utils import ImageReader

        left, top, width, height = rectangle
        self.canvas.drawImage(
            ImageReader(BytesIO(asset.data)),
            left * 72.0,
            to_pdf_y(top + height, self.height_in),
            width * 72.0,
            height * 72.0,
            mask='auto',
        )

    def chart(self, rectangle, chart_spec: dict, font_size: int) -> None:
        """A native reportlab chart, drawn from the same numbers the deck plots."""
        from reportlab.graphics import renderPDF

        left, top, width, height = rectangle
        drawing = build_chart_drawing(
            chart_spec, self.theme, width * 72.0, height * 72.0,
            (self.regular, self.bold), font_size,
        )
        renderPDF.draw(
            drawing, self.canvas, left * 72.0, to_pdf_y(top + height, self.height_in)
        )


def build_chart_drawing(chart_spec: dict, theme: dict, width_pt: float,
                        height_pt: float, fonts: tuple[str, str], font_size: int):
    """The chart as a reportlab Drawing, matching what python-pptx will draw.

    Same kinds, same series colours, same value labels on every point, and the
    same rule that a legend appears only when colour alone would otherwise have
    to carry the meaning.
    """
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.legends import Legend
    from reportlab.graphics.charts.linecharts import HorizontalLineChart
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib.colors import HexColor

    regular, _ = fonts
    palette = [HexColor(f'#{value}') for value in SERIES_COLOURS[palette_mode(theme)]]
    ink = HexColor(f"#{theme['body']}")
    rule = HexColor(f"#{theme['rule']}")

    kind = str(chart_spec['kind'])
    categories = [str(category) for category in chart_spec['categories']]
    series = chart_spec['series']
    data = [[float(value) for value in entry['values']] for entry in series]
    multi = len(series) > 1 or kind == 'pie'

    drawing = Drawing(width_pt, height_pt)
    # The legend is one row along the foot of the plot, never a stacked block:
    # the footnote line sits just under this box, and two legend rows reached it.
    legend_height = font_size * 1.8 if multi else 0.0
    plot_height = height_pt - legend_height

    if kind == 'pie':
        pie = Pie()
        pie.width = pie.height = min(plot_height, width_pt) * 0.78
        pie.x = (width_pt - pie.width) / 2
        pie.y = legend_height + (plot_height - pie.height) / 2
        pie.data = data[0]
        pie.labels = [f'{value:g}' for value in data[0]]
        pie.sideLabels = 1
        pie.slices.fontName = regular
        pie.slices.fontSize = font_size
        pie.slices.fontColor = ink
        pie.slices.strokeColor = rule
        for index in range(len(data[0])):
            pie.slices[index].fillColor = palette[index % len(palette)]
        drawing.add(pie)
        pairs = list(zip(palette * len(categories), categories))
    else:
        chart = VerticalBarChart() if kind == 'bar' else HorizontalLineChart()
        chart.x = font_size * 3.0
        chart.y = legend_height + font_size * 2.0
        chart.width = width_pt - chart.x - font_size
        chart.height = plot_height - font_size * 3.0
        chart.data = data
        chart.categoryAxis.categoryNames = categories
        for axis in (chart.categoryAxis, chart.valueAxis):
            axis.labels.fontName = regular
            axis.labels.fontSize = font_size
            axis.labels.fillColor = ink
            axis.strokeColor = rule
        chart.valueAxis.valueMin = min(0.0, min(min(row) for row in data))
        if kind == 'bar':
            for index in range(len(data)):
                chart.bars[index].fillColor = palette[index % len(palette)]
                chart.bars[index].strokeColor = None
            chart.barLabelFormat = '%g'
            chart.barLabels.fontName = regular
            chart.barLabels.fontSize = font_size
            chart.barLabels.fillColor = ink
            chart.barLabels.dy = font_size * 0.6
            chart.barLabels.boxAnchor = 's'
        else:
            for index in range(len(data)):
                chart.lines[index].strokeColor = palette[index % len(palette)]
                chart.lines[index].strokeWidth = 2
            chart.lineLabelFormat = '%g'
            chart.lineLabels.fontName = regular
            chart.lineLabels.fontSize = font_size
            chart.lineLabels.fillColor = ink
            chart.lineLabelNudge = font_size * 0.7
        drawing.add(chart)
        pairs = [
            (palette[index % len(palette)], str(entry['name']))
            for index, entry in enumerate(series)
        ]

    if multi:
        legend = Legend()
        legend.x = font_size
        legend.y = font_size * 0.4
        legend.alignment = 'right'
        legend.columnMaximum = 1
        legend.deltax = font_size * 7
        legend.fontName = regular
        legend.fontSize = font_size
        legend.fillColor = ink
        legend.colorNamePairs = pairs
        drawing.add(legend)

    if chart_spec.get('unit') and kind != 'pie':
        from reportlab.graphics.shapes import String

        label = String(font_size, height_pt - font_size, str(chart_spec['unit']))
        label.fontName = regular
        label.fontSize = font_size
        label.fillColor = ink
        drawing.add(label)

    return drawing


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


def render_title_image(page: Page, slide: dict, sizes: dict) -> None:
    geometry, theme = page.geometry, page.theme
    page.picture(geometry['hero_image'], slide['image'], IMAGE_FIT['title-image'])
    page.fill_rect(geometry['hero_bar'], theme['accent'])
    page.text_block(
        geometry['hero_title'], [slide['title']], sizes['title'], theme['ink'], bold=True
    )
    if slide.get('subtitle'):
        page.text_block(
            geometry['hero_subtitle'], [slide['subtitle']], sizes['subtitle'],
            theme['muted'],
        )
    if slide.get('meta'):
        page.text_block(
            geometry['hero_meta'], [slide['meta']], sizes['meta'], theme['muted']
        )


def render_image_content(page: Page, slide: dict, sizes: dict) -> None:
    geometry, theme = page.geometry, page.theme
    render_heading(page, slide['title'], sizes['title'])
    page.picture(geometry['split_image'], slide['image'], IMAGE_FIT['image-content'])
    page.bullets(geometry['split_body'], list(slide['bullets']), sizes['body'], theme['body'])
    if slide.get('caption'):
        page.text_block(
            geometry['split_caption'], [slide['caption']], sizes['caption'], theme['muted']
        )


def render_image_full(page: Page, slide: dict, sizes: dict) -> None:
    geometry, theme = page.geometry, page.theme
    page.picture(geometry['full_image'], slide['image'], IMAGE_FIT['image-full'])
    page.fill_rect(geometry['full_caption'], theme['surface'])
    page.text_block(
        geometry['full_caption'], [slide['title']], sizes['caption'], theme['ink'],
        bold=True, inset_in=0.85,
    )
    if slide.get('caption'):
        caption_rect = list(geometry['full_caption'])
        caption_rect[1] += sizes['caption'] * LINE_HEIGHT_EM / 72.0
        page.text_block(
            tuple(caption_rect), [slide['caption']], sizes['caption'], theme['muted'],
            inset_in=0.85,
        )


def render_chart(page: Page, slide: dict, sizes: dict) -> None:
    geometry, theme = page.geometry, page.theme
    render_heading(page, slide['title'], sizes['title'])
    if slide.get('chart') is not None:
        page.chart(geometry['plot'], slide['chart'], CHART_TEXT_PT)
    else:
        page.picture(geometry['plot'], slide['image'], IMAGE_FIT['chart'])
    if slide.get('note'):
        page.text_block(geometry['note'], [slide['note']], sizes['note'], theme['muted'])


RENDERERS = {
    'title': render_title,
    'content': render_content,
    'comparison': render_comparison,
    'closing': render_closing,
    'title-image': render_title_image,
    'image-content': render_image_content,
    'image-full': render_image_full,
    'chart': render_chart,
}


def build_pdf(
    spec: dict,
    geometry: dict,
    output: str,
    image_assets: dict[str, ValidatedImage],
) -> None:
    from reportlab.pdfgen import canvas as pdf_canvas

    theme = resolve_theme(spec)
    fonts = register_fonts(spec.get('font') or DEFAULT_FONT)
    width_pt = geometry['slide'][0] * 72.0
    height_pt = geometry['slide'][1] * 72.0

    document = pdf_canvas.Canvas(output, pagesize=(width_pt, height_pt))
    document.setTitle(spec['title'])
    document.setAuthor(spec.get('author') or '')
    document.setSubject(spec.get('subtitle') or '')

    for spec_slide in spec['slides']:
        page = Page(document, geometry, theme, fonts, image_assets)
        page.fill_rect((0, 0, geometry['slide'][0], geometry['slide'][1]), theme['background'])
        sizes, _ = plan_slide(spec_slide, geometry)
        RENDERERS[spec_slide['layout']](page, spec_slide, sizes)
        document.showPage()

    document.save()


# --- CLI --------------------------------------------------------------------------

#: The validator that checks a deck's PDF when it is delivered. PDF validation
#: belongs to the pdf skill, which this plugin composes; this is the identity
#: that skill declares.
PDF_VALIDATOR_ID = 'skill-pdf.validate_pdf'

#: The execution protocol this renderer reports in: the PDF protocol, not the
#: deck's, because its output is a PDF and is delivered as one.
EXECUTION_SCHEMA = 'chainabit.pdf.execution/v1'


def validation_handoff(output: str) -> str:
    """The `Next:` line printed after a successful render.

    A printed line is read as an instruction, so this one states a fact
    instead: the PDF is checked by the pdf capability's validator when it is
    delivered. That validator ships in another bundle and runs as part of
    delivery, so the line names no script for the reader to run and spells no
    path.
    """
    return f'Next: deliver {output}; it is checked by {PDF_VALIDATOR_ID} on delivery.'


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

    root = Path(os.path.abspath(args.spec)).parent
    image_assets: dict[str, ValidatedImage] = {}
    problems.extend(validate_spec(spec, root, image_assets))

    aspect = spec.get('aspect', '16:9') if isinstance(spec, dict) else None
    geometry = build_geometry(aspect) if aspect in ASPECTS else None
    if geometry is not None:
        problems.extend(check_fit(spec, geometry))

    # A genuine renderer limitation, not a spec mistake: this script draws
    # text with `canvas.drawString`, which has no Unicode Bidi reordering and
    # no Arabic contextual shaping (neither is available in the sandbox --
    # no `python-bidi`/`arabic-reshaper` is installed, and none is assumed).
    # Left unchecked, a predominantly right-to-left deck rendered at exit 0
    # with every Arabic/Hebrew line disconnected and in the wrong visual
    # order -- the silent-garbage failure this whole change exists to
    # prevent. deck_pptx.py's own .pptx output is NOT restricted: PowerPoint,
    # Keynote and Google Slides all shape and reorder this text correctly
    # themselves, so only this renderer's own PDF companion refuses.
    if isinstance(spec, dict) and _spec_is_predominantly_rtl(spec):
        problems.append(
            "slides: this deck's text is predominantly right-to-left (Arabic/Hebrew). "
            'deck_pdf.py draws text directly with no bidi reordering or Arabic shaping '
            'and would render it disconnected and in the wrong order, not merely '
            "unstyled. Build the .pptx only -- deck_pptx.py's own output is unaffected, "
            'since PowerPoint/Keynote/Google Slides shape and reorder this text '
            'themselves -- or use the pdf skill directly for a right-to-left document.'
        )

    if problems:
        for problem in problems:
            print(f'ERROR: {problem}', file=sys.stderr)
        return 1

    if args.validate_only:
        print(f"OK: {args.spec} is a valid deck spec ({len(spec['slides'])} slide(s))")
        print(json.dumps(preflight_frame(EXECUTION_SCHEMA), sort_keys=True))
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
        return 2

    try:
        build_pdf(spec, geometry, args.output, image_assets)
    except (RuntimeError, subprocess.SubprocessError) as exc:
        print(f'ERROR: renderer_runtime: {exc}', file=sys.stderr)
        return 2
    except PermissionError:
        print(f'ERROR: output: no permission to write {args.output}', file=sys.stderr)
        return 2
    except OSError as exc:
        print(f'ERROR: output: could not write {args.output}: {exc}', file=sys.stderr)
        return 2

    size = os.path.getsize(args.output)
    print(f"OK: wrote {args.output} ({size} bytes, {len(spec['slides'])} page(s))")
    print(validation_handoff(args.output))
    with open(args.output, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    print(json.dumps({
        "schema": EXECUTION_SCHEMA,
        "success": True,
        "generator": "skill-pptx.deck_pdf",
        "output": {
            "path": os.path.realpath(args.output),
            "shape": "file",
            "sha256": digest,
            "mime": "application/pdf",
            "bytes": size,
        },
        "typography": {
            "family": spec.get("font") or DEFAULT_FONT,
            "source": "user_override" if spec.get("font") else "chainabit_default",
        },
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
