#!/usr/bin/env python3
"""Build a .pptx deck from a JSON spec using four brand-safe layouts.

python-pptx will happily place a 9pt run of grey text in a box too small for it.
Nothing in the library has an opinion about whether the result can be read from
the back of a room, so the opinion lives here: four fixed layouts — title,
content, comparison, closing — whose geometry, palette and type scale are chosen
once, checked against WCAG AA, and then not negotiable per slide.

What the caller supplies is content. What this script decides is layout.

The spec is fully validated before a byte is written, and every problem is
reported at once as `ERROR: <field>: <reason>`, so a generated spec can be fixed
in one pass rather than one exit code at a time. Density limits are part of that
validation: a slide that cannot hold its own text at a readable size is a spec
error, not something to discover in the rendered file.

Usage:
    python3 deck_pptx.py spec.json deck.pptx [--validate-only]

Spec shape (see SKILL.md for the annotated version):

    {
      "title": "Q3 Review",                 required, also the file's metadata title
      "subtitle": "Operations",             optional
      "author": "Operations",               optional
      "theme": "light" | "dark",            optional, default light
      "aspect": "16:9" | "4:3",             optional, default 16:9
      "font": "Arial",                      optional, default Arial
      "slides": [                           required, at least one
        {"layout": "title",      "title": "...", "subtitle": "...", "meta": "..."},
        {"layout": "content",    "title": "...", "bullets": ["..."], "note": "..."},
        {"layout": "comparison", "title": "...",
                                 "left":  {"heading": "...", "bullets": ["..."]},
                                 "right": {"heading": "...", "bullets": ["..."]}},
        {"layout": "closing",    "title": "...", "subtitle": "...", "contact": "..."}
      ]
    }

Any slide may carry "notes": "..." — speaker notes, which is where the sentence
belongs when the bullet has to stay a phrase.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

LAYOUTS = ("title", "content", "comparison", "closing")

# --- design constants. references/design.md states the same numbers with the
# reasoning; validate_pptx.py enforces them. All three must agree.
MAX_BULLETS = 6
MAX_WORDS_PER_BULLET = 20
MAX_SLIDES = 30
# Type ladders. Each box is set at the largest size in its ladder that the text
# actually fits at; the last entry is the floor, and text that will not fit there
# is a spec error rather than something to shrink into illegibility.
BODY_SIZES = (22, 20, 18)
COLUMN_SIZES = (20, 18)
HEADING_SIZES = (32, 28, 24)
COVER_TITLE_SIZES = (44, 38, 32)
CLOSING_TITLE_SIZES = (40, 34, 28)
SUBTITLE_SIZES = (22, 20, 18)
COLUMN_HEADING_SIZES = (22, 20, 18)
META_SIZES = (18,)

# Same estimator as validate_pptx.py, so a deck this script writes passes that
# gate rather than merely being likely to.
AVG_CHAR_WIDTH_EM = 0.5
LINE_HEIGHT_EM = 1.2
BULLET_SPACING_PT = 12

# Every pair below was checked with the WCAG contrast formula; the weakest is
# 5.75:1, which clears AA for normal text with room to spare on a washed-out
# projector. Do not edit one value in isolation — see references/design.md.
THEMES = {
    "light": {
        "background": "FFFFFF",
        "surface": "F8FAFC",
        "ink": "0F172A",       # 17.85:1 on white
        "body": "1E293B",      # 14.63:1 on white
        "muted": "475569",     # 7.58:1 on white
        "rule": "CBD5E1",
        "accent": "1D4ED8",    # 6.41:1 on surface
    },
    "dark": {
        "background": "0F172A",
        "surface": "1E293B",
        "ink": "F8FAFC",       # 17.06:1 on background
        "body": "E2E8F0",      # 14.48:1 on background
        "muted": "CBD5E1",     # 12.02:1 on background
        "rule": "334155",
        "accent": "60A5FA",    # 5.75:1 on surface
    },
}

ASPECTS = {"16:9": (13.333, 7.5), "4:3": (10.0, 7.5)}


# --- validation -------------------------------------------------------------------


def text_field(value: object, where: str, required: bool) -> list[str]:
    if value is None:
        return [f"{where}: required, must be a non-empty string"] if required else []
    if not isinstance(value, str) or not value.strip():
        return [f"{where}: must be a non-empty string"]
    return []


def validate_bullets(items: object, where: str) -> list[str]:
    problems: list[str] = []
    if not isinstance(items, list) or not items:
        return [f"{where}: required, must be a non-empty array of strings"]
    if len(items) > MAX_BULLETS:
        problems.append(
            f"{where}: {len(items)} bullets, the limit is {MAX_BULLETS}. Split this "
            "slide in two, or move the extras into 'notes'."
        )
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            problems.append(f"{where}[{index}]: must be a non-empty string")
            continue
        words = len(item.split())
        if words > MAX_WORDS_PER_BULLET:
            problems.append(
                f"{where}[{index}]: {words} words, the limit is {MAX_WORDS_PER_BULLET}. "
                "A bullet is a cue; put the sentence in 'notes' and leave a phrase here."
            )
    return problems


def validate_column(column: object, where: str) -> list[str]:
    if not isinstance(column, dict):
        return [f"{where}: must be an object with 'heading' and 'bullets'"]
    problems = text_field(column.get("heading"), f"{where}.heading", required=True)
    problems.extend(validate_bullets(column.get("bullets"), f"{where}.bullets"))
    return problems


def validate_slide(slide: object, where: str) -> list[str]:
    if not isinstance(slide, dict):
        return [f"{where}: must be an object"]

    layout = slide.get("layout")
    if layout not in LAYOUTS:
        return [f"{where}.layout: must be one of {', '.join(LAYOUTS)}, found {layout!r}"]

    problems = text_field(slide.get("title"), f"{where}.title", required=True)
    problems.extend(text_field(slide.get("notes"), f"{where}.notes", required=False))

    if layout == "title":
        problems.extend(text_field(slide.get("subtitle"), f"{where}.subtitle", required=False))
        problems.extend(text_field(slide.get("meta"), f"{where}.meta", required=False))

    elif layout == "content":
        problems.extend(validate_bullets(slide.get("bullets"), f"{where}.bullets"))
        problems.extend(text_field(slide.get("note"), f"{where}.note", required=False))

    elif layout == "comparison":
        for side in ("left", "right"):
            if slide.get(side) is None:
                problems.append(f"{where}.{side}: required for a comparison slide")
            else:
                problems.extend(validate_column(slide.get(side), f"{where}.{side}"))

    elif layout == "closing":
        problems.extend(text_field(slide.get("subtitle"), f"{where}.subtitle", required=False))
        problems.extend(text_field(slide.get("contact"), f"{where}.contact", required=False))

    return problems


def validate_spec(spec: object) -> list[str]:
    """One `<field>: <reason>` string per problem; empty means the spec is usable."""
    if not isinstance(spec, dict):
        return ["spec: top level must be a JSON object"]

    problems = text_field(spec.get("title"), "title", required=True)
    for optional in ("subtitle", "author", "font"):
        problems.extend(text_field(spec.get(optional), optional, required=False))

    theme = spec.get("theme", "light")
    if theme not in THEMES:
        problems.append(f'theme: must be "light" or "dark", found {theme!r}')

    aspect = spec.get("aspect", "16:9")
    if aspect not in ASPECTS:
        problems.append(f'aspect: must be "16:9" or "4:3", found {aspect!r}')

    slides = spec.get("slides")
    if not isinstance(slides, list):
        return problems + ["slides: required, must be an array"]
    if not slides:
        return problems + ["slides: must contain at least one slide"]
    if len(slides) > MAX_SLIDES:
        problems.append(
            f"slides: {len(slides)} slides, over the {MAX_SLIDES}-slide cap. A deck "
            "longer than this is a document; write it as one."
        )

    for index, slide in enumerate(slides):
        problems.extend(validate_slide(slide, f"slides[{index}]"))

    return problems


# --- text fitting -----------------------------------------------------------------


def estimated_height(lines: list[str], size: float, width_pt: float, spacing: float) -> float:
    """Rendered height in points for wrapped paragraphs set at `size`."""
    characters_per_line = max(1.0, width_pt / (AVG_CHAR_WIDTH_EM * size))
    total = 0.0
    for line in lines:
        wrapped = max(1, math.ceil(len(line) / characters_per_line))
        total += wrapped * size * LINE_HEIGHT_EM + spacing
    return total - (spacing if lines else 0)


def fit_size(
    lines: list[str],
    candidates: tuple[int, ...],
    width_in: float,
    height_in: float,
    inset_in: float,
) -> int | None:
    """The largest candidate size at which the text fits the box, or None."""
    width_pt = (width_in - 2 * inset_in) * 72
    height_pt = (height_in - 2 * 0.05) * 72
    for size in candidates:
        if estimated_height(lines, size, width_pt, BULLET_SPACING_PT) <= height_pt:
            return size
    return None


def plan_slide(slide: dict, geometry: dict) -> tuple[dict[str, int], list[str]]:
    """Choose a point size for every text box on one slide.

    Returns the chosen sizes and any field that will not fit even at its floor.
    Both the fit check and the renderer call this, so what is validated is exactly
    what is drawn — there is no second opinion about the type scale.
    """
    sizes: dict[str, int] = {}
    problems: list[str] = []

    def choose(key: str, lines: list[str], rectangle, candidates, inset: float, field: str, hint: str):
        size = fit_size(lines, candidates, rectangle[2], rectangle[3], inset)
        if size is None:
            problems.append(
                f"{field}: will not fit its box at the {candidates[-1]}pt floor. {hint}"
            )
            size = candidates[-1]
        sizes[key] = size

    layout = slide["layout"]

    if layout == "title":
        choose("title", [slide["title"]], geometry["cover_title"], COVER_TITLE_SIZES, 0.12,
               "title", "Shorten the cover title.")
        if slide.get("subtitle"):
            choose("subtitle", [slide["subtitle"]], geometry["cover_subtitle"], SUBTITLE_SIZES,
                   0.12, "subtitle", "Shorten it.")
        if slide.get("meta"):
            choose("meta", [slide["meta"]], geometry["cover_meta"], META_SIZES, 0.12,
                   "meta", "Keep it to one short line.")

    elif layout == "content":
        choose("title", [slide["title"]], geometry["heading"], HEADING_SIZES, 0.12,
               "title", "Shorten the slide title.")
        choose("body", list(slide["bullets"]), geometry["body"], BODY_SIZES, 0.12,
               "bullets", "Shorten the bullets or split the slide — shrinking the type "
               "below the floor is not an option.")
        if slide.get("note"):
            choose("note", [slide["note"]], geometry["note"], META_SIZES, 0.12,
                   "note", "A footnote is one short line; anything longer belongs in 'notes'.")

    elif layout == "comparison":
        choose("title", [slide["title"]], geometry["heading"], HEADING_SIZES, 0.12,
               "title", "Shorten the slide title.")
        for side in ("left", "right"):
            column = slide[side]
            choose(f"{side}_heading", [column["heading"]], geometry["column_heading"],
                   COLUMN_HEADING_SIZES, 0.22, f"{side}.heading", "Shorten the column heading.")
            choose(f"{side}_body", list(column["bullets"]), geometry["column_body"],
                   COLUMN_SIZES, 0.22, f"{side}.bullets",
                   "Half a slide holds half as much — shorten it, or use two content "
                   "slides instead of one comparison.")

    elif layout == "closing":
        choose("title", [slide["title"]], geometry["closing_title"], CLOSING_TITLE_SIZES,
               0.12, "title", "A closing slide carries a word or two.")
        if slide.get("subtitle"):
            choose("subtitle", [slide["subtitle"]], geometry["closing_subtitle"],
                   SUBTITLE_SIZES, 0.12, "subtitle", "Shorten it.")
        if slide.get("contact"):
            choose("contact", [slide["contact"]], geometry["closing_contact"], META_SIZES,
                   0.12, "contact", "Keep it to one short line.")

    return sizes, problems


def check_fit(spec: dict, geometry: dict) -> list[str]:
    """Density problems that only show up once the box geometry is known.

    Slides that failed the structural checks are skipped — there is nothing to
    measure until they have the fields they are missing — but every slide that did
    pass is measured now, so one run reports everything that is wrong.
    """
    problems: list[str] = []
    slides = spec.get("slides")
    if not isinstance(slides, list):
        return problems
    for index, slide in enumerate(slides):
        where = f"slides[{index}]"
        if validate_slide(slide, where):
            continue
        _, slide_problems = plan_slide(slide, geometry)
        problems.extend(f"{where}.{problem}" for problem in slide_problems)
    return problems


# --- geometry ---------------------------------------------------------------------


def build_geometry(aspect: str) -> dict:
    """Box rectangles as (left, top, width, height) in inches."""
    width, height = ASPECTS[aspect]
    margin = 0.85 if width > 11 else 0.6
    content = width - 2 * margin
    gutter = 0.4
    column = (content - gutter) / 2

    return {
        "slide": (width, height),
        "margin": margin,
        # title layout
        "cover_bar": (margin, 2.45, 1.6, 0.09),
        "cover_title": (margin, 2.75, content, 1.5),
        "cover_subtitle": (margin, 4.35, content, 0.9),
        "cover_meta": (margin, height - 1.2, content, 0.5),
        # content and comparison share a heading band
        "heading": (margin, 0.6, content, 0.85),
        "rule": (margin, 1.5, content, 0.035),
        "body": (margin, 1.8, content, height - 3.1),
        "note": (margin, height - 1.05, content, 0.55),
        "column_heading": (margin, 1.8, column, 0.7),
        "column_body": (margin, 2.5, column, height - 3.8),
        "column_offset": column + gutter,
        # closing layout
        "closing_title": (margin, 2.6, content, 1.3),
        "closing_subtitle": (margin, 4.0, content, 0.9),
        "closing_contact": (margin, 5.2, content, 0.6),
    }


# --- rendering --------------------------------------------------------------------


def add_textbox(slide, rectangle, inset_in: float = 0.12, fill: str | None = None):
    from pptx.dml.color import RGBColor
    from pptx.enum.text import MSO_AUTO_SIZE
    from pptx.util import Inches

    left, top, width, height = rectangle
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = box.text_frame
    frame.word_wrap = True
    # python-pptx writes wrap="none" plus <a:spAutoFit/> on a new text box, which
    # means the box silently resizes itself around whatever it is given and the
    # declared geometry stops describing the slide. Both are turned off here so the
    # boxes laid out above are the boxes that render, and so validate_pptx.py can
    # hold the text to them.
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.margin_left = Inches(inset_in)
    frame.margin_right = Inches(inset_in)
    frame.margin_top = Inches(0.05)
    frame.margin_bottom = Inches(0.05)

    if fill:
        box.fill.solid()
        box.fill.fore_color.rgb = RGBColor.from_string(fill)
        box.line.fill.background()
    else:
        box.fill.background()
        box.line.fill.background()
    return box


def style_run(paragraph, text: str, font: str, size: int, colour: str, bold: bool = False):
    from pptx.dml.color import RGBColor
    from pptx.util import Pt

    run = paragraph.add_run()
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(colour)
    return run


def make_bullet(paragraph, font: str, indent_in: float = 0.3) -> None:
    """Give a paragraph a real bullet glyph and a hanging indent.

    python-pptx exposes no bullet API, and a plain text box inherits none from the
    blank layout, so without this the 'bullets' are just wrapped lines that lose
    their left edge on the second line.
    """
    from pptx.oxml.ns import qn
    from pptx.util import Inches

    properties = paragraph._pPr if paragraph._pPr is not None else paragraph._p.get_or_add_pPr()
    properties.set("marL", str(int(Inches(indent_in))))
    properties.set("indent", str(int(-Inches(indent_in))))
    bullet_font = properties.makeelement(qn("a:buFont"), {"typeface": font})
    bullet_char = properties.makeelement(qn("a:buChar"), {"char": "•"})
    properties.append(bullet_font)
    properties.append(bullet_char)


def fill_bullets(box, items: list[str], font: str, size: int, colour: str) -> None:
    from pptx.util import Pt

    frame = box.text_frame
    for index, item in enumerate(items):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        if index:
            paragraph.space_before = Pt(BULLET_SPACING_PT)
        style_run(paragraph, item, font, size, colour)
        make_bullet(paragraph, font)


def add_rule(slide, rectangle, colour: str) -> None:
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches

    left, top, width, height = rectangle
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(colour)
    shape.line.fill.background()
    shape.shadow.inherit = False


def new_slide(presentation, palette: str):
    from pptx.dml.color import RGBColor

    # Layout 6 is the blank layout in python-pptx's default template. Every shape on
    # these slides is placed explicitly, so nothing inherits a size or colour from a
    # theme this skill did not choose.
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor.from_string(palette)
    return slide


def render_title(slide, spec_slide, theme, geometry, font, sizes) -> None:
    add_rule(slide, geometry["cover_bar"], theme["accent"])

    box = add_textbox(slide, geometry["cover_title"])
    style_run(
        box.text_frame.paragraphs[0], spec_slide["title"], font, sizes["title"],
        theme["ink"], bold=True,
    )

    if spec_slide.get("subtitle"):
        box = add_textbox(slide, geometry["cover_subtitle"])
        style_run(
            box.text_frame.paragraphs[0], spec_slide["subtitle"], font, sizes["subtitle"],
            theme["muted"],
        )

    if spec_slide.get("meta"):
        box = add_textbox(slide, geometry["cover_meta"])
        style_run(
            box.text_frame.paragraphs[0], spec_slide["meta"], font, sizes["meta"],
            theme["muted"],
        )


def render_heading(slide, text, theme, geometry, font, size) -> None:
    box = add_textbox(slide, geometry["heading"])
    style_run(box.text_frame.paragraphs[0], text, font, size, theme["ink"], bold=True)
    add_rule(slide, geometry["rule"], theme["rule"])


def render_content(slide, spec_slide, theme, geometry, font, sizes) -> None:
    render_heading(slide, spec_slide["title"], theme, geometry, font, sizes["title"])

    box = add_textbox(slide, geometry["body"])
    fill_bullets(box, list(spec_slide["bullets"]), font, sizes["body"], theme["body"])

    if spec_slide.get("note"):
        box = add_textbox(slide, geometry["note"])
        style_run(
            box.text_frame.paragraphs[0], spec_slide["note"], font, sizes["note"],
            theme["muted"],
        )


def render_comparison(slide, spec_slide, theme, geometry, font, sizes) -> None:
    render_heading(slide, spec_slide["title"], theme, geometry, font, sizes["title"])

    for index, side in enumerate(("left", "right")):
        column = spec_slide[side]
        shift = geometry["column_offset"] * index

        heading_rect = list(geometry["column_heading"])
        heading_rect[0] += shift
        box = add_textbox(slide, tuple(heading_rect), inset_in=0.22, fill=theme["surface"])
        style_run(
            box.text_frame.paragraphs[0], column["heading"], font,
            sizes[f"{side}_heading"], theme["accent"], bold=True,
        )

        body_rect = list(geometry["column_body"])
        body_rect[0] += shift
        box = add_textbox(slide, tuple(body_rect), inset_in=0.22, fill=theme["surface"])
        fill_bullets(box, list(column["bullets"]), font, sizes[f"{side}_body"], theme["body"])


def render_closing(slide, spec_slide, theme, geometry, font, sizes) -> None:
    from pptx.enum.text import PP_ALIGN

    box = add_textbox(slide, geometry["closing_title"])
    paragraph = box.text_frame.paragraphs[0]
    paragraph.alignment = PP_ALIGN.CENTER
    style_run(paragraph, spec_slide["title"], font, sizes["title"], theme["ink"], bold=True)

    bar = list(geometry["cover_bar"])
    bar[0] = (geometry["slide"][0] - bar[2]) / 2
    bar[1] = geometry["closing_title"][1] - 0.35
    add_rule(slide, tuple(bar), theme["accent"])

    if spec_slide.get("subtitle"):
        box = add_textbox(slide, geometry["closing_subtitle"])
        paragraph = box.text_frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.CENTER
        style_run(paragraph, spec_slide["subtitle"], font, sizes["subtitle"], theme["muted"])

    if spec_slide.get("contact"):
        box = add_textbox(slide, geometry["closing_contact"])
        paragraph = box.text_frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.CENTER
        style_run(paragraph, spec_slide["contact"], font, sizes["contact"], theme["muted"])


RENDERERS = {
    "title": render_title,
    "content": render_content,
    "comparison": render_comparison,
    "closing": render_closing,
}


def build_deck(spec: dict, geometry: dict, output: str) -> None:
    from pptx import Presentation
    from pptx.util import Inches

    theme = THEMES[spec.get("theme", "light")]
    font = spec.get("font") or "Arial"

    presentation = Presentation()
    presentation.slide_width = Inches(geometry["slide"][0])
    presentation.slide_height = Inches(geometry["slide"][1])

    for spec_slide in spec["slides"]:
        slide = new_slide(presentation, theme["background"])
        sizes, _ = plan_slide(spec_slide, geometry)
        RENDERERS[spec_slide["layout"]](slide, spec_slide, theme, geometry, font, sizes)
        if spec_slide.get("notes"):
            slide.notes_slide.notes_text_frame.text = spec_slide["notes"]

    core = presentation.core_properties
    core.title = spec["title"]
    core.author = spec.get("author") or ""
    core.subject = spec.get("subtitle") or ""

    presentation.save(output)


# --- CLI --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deck_pptx.py",
        description=(
            "Build a .pptx deck from a JSON spec using four fixed layouts — title, "
            "content, comparison, closing — with a palette and type scale that meet "
            "WCAG AA at projection distance."
        ),
        epilog=(
            "Example:\n"
            "  python3 deck_pptx.py spec.json deck.pptx\n\n"
            "The spec is fully validated first, density limits included. Every problem "
            "is printed as an ERROR: line so the spec can be corrected in one pass.\n"
            "Runs offline. python-pptx is already installed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("spec", help="path to the JSON spec file")
    parser.add_argument("output", help="path to write the .pptx to")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="check the spec and exit without writing a deck",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    problems: list[str] = []

    if not args.output.lower().endswith(".pptx"):
        problems.append(f"output: {args.output} must end in .pptx")
    output_directory = os.path.dirname(os.path.abspath(args.output))
    if not os.path.isdir(output_directory):
        problems.append(f"output: directory {output_directory} does not exist")
    elif not os.access(output_directory, os.W_OK):
        problems.append(f"output: directory {output_directory} is not writable")

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

    aspect = spec.get("aspect", "16:9") if isinstance(spec, dict) else None
    geometry = build_geometry(aspect) if aspect in ASPECTS else None
    if geometry is not None:
        problems.extend(check_fit(spec, geometry))

    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1

    if args.validate_only:
        print(f"OK: {args.spec} is a valid deck spec ({len(spec['slides'])} slide(s))")
        return 0

    try:
        import pptx  # noqa: F401
    except ImportError:
        print(
            "ERROR: environment: the 'python-pptx' package is missing. This script only "
            "runs inside the Chainabit sandbox image, where it is pre-installed. Do not "
            "try to install it.",
            file=sys.stderr,
        )
        return 1

    try:
        build_deck(spec, geometry, args.output)
    except PermissionError:
        print(f"ERROR: output: no permission to write {args.output}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: output: could not write {args.output}: {exc}", file=sys.stderr)
        return 1

    size = os.path.getsize(args.output)
    print(f"OK: wrote {args.output} ({size} bytes, {len(spec['slides'])} slide(s))")
    print(f"Next: python3 scripts/validate_pptx.py {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
