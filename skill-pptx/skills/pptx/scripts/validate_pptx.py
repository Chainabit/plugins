#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check that a generated .pptx is a real presentation, and that it is presentable.

A deck fails differently from a document. It is rarely broken; it is usually just
bad — a slide with nothing on it, a paragraph that runs off the bottom of its box
with no scrollbar to rescue it, 9pt grey-on-white footnotes nobody past the second
row can read, fourteen bullets on one slide. None of that raises an exception, so
a generator exits 0 and the model reports success on a deck it has never seen.

This is the exit gate that looks. It reports, per slide:

  * empty slides — nothing painted, nothing written;
  * text that overflows its own box, with the estimated and available heights;
  * contrast below the readable floor, with the computed WCAG ratio;
  * font sizes under the projection floor;
  * bullet counts and bullet lengths over the density limits;
  * a slide count over the cap.

Deliberately stdlib-only (zipfile + xml.etree). A .pptx is an OPC ZIP of
DrawingML, so the file's own structure is enough, and reading it directly keeps
this working on decks produced by PowerPoint, Keynote, or Google Slides rather
than only on decks this skill built.

Usage:
    python3 validate_pptx.py deck.pptx [--strict]
"""

from __future__ import annotations

import argparse
import colorsys
import math
import os
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

EMU_PER_POINT = 12700
EMU_PER_INCH = 914400

# --- the design floors this gate enforces. references/design.md states the same
# numbers and the reasoning behind each one; the two must never drift apart.
MIN_FONT_PT = 14.0          # below this is unreadable past the second row: hard fail
BODY_FONT_PT = 18.0         # the recommended floor for body text: warn under it
MAX_BULLETS = 6             # per text block
WARN_WORDS_PER_BULLET = 12  # target
MAX_WORDS_PER_BULLET = 20   # hard fail
MAX_SLIDES = 30
CONTRAST_FLOOR = 4.5        # this skill's projection-hardened floor
CONTRAST_AA_LARGE = 3.0     # WCAG AA for large text; below it fails outright
LARGE_TEXT_PT = 18.0        # WCAG large-scale text
LARGE_TEXT_BOLD_PT = 14.0

# Text-measurement constants. python-pptx and the OOXML file alike carry no font
# metrics, and there is no rendering engine in the sandbox, so overflow has to be
# estimated. 0.5em average advance width and 1.2em line height are close for the
# humanist sans faces a deck should use (Arial, Calibri, Helvetica); the check
# allows a tolerance on top so a near-miss is not reported as a failure.
AVG_CHAR_WIDTH_EM = 0.5
LINE_HEIGHT_EM = 1.2
OVERFLOW_TOLERANCE = 1.05

PROMPT_TEXT = re.compile(
    r"^\s*(click to add|klicken sie|haga clic|cliquez pour|başlık eklemek)",
    re.IGNORECASE,
)

TITLE_PH = {"title", "ctrTitle"}


# --- OPC package reading ----------------------------------------------------------


DOCTYPE = re.compile(rb"<!(DOCTYPE|ENTITY)\b", re.IGNORECASE)


def parse_xml(raw: bytes) -> ET.Element:
    """Parse an OPC part, refusing any document that declares a DTD or entities.

    OOXML parts never legitimately carry a DOCTYPE. Rejecting one outright closes
    the entity-expansion attacks (external entities, billion laughs) that make a
    stdlib parser risky on untrusted input, without adding a dependency the
    sandbox image does not have.
    """
    if DOCTYPE.search(raw[:4096]):
        raise ET.ParseError("XML declares a DTD or entity, which OOXML parts never do")
    return ET.fromstring(raw)


class Package:
    """A .pptx as an OPC package: parts addressed by name, relationships resolved."""

    def __init__(self, archive: zipfile.ZipFile) -> None:
        self.archive = archive
        self._parts: dict[str, ET.Element | None] = {}
        self._rels: dict[str, dict[str, str]] = {}

    def part(self, name: str) -> ET.Element | None:
        if name not in self._parts:
            try:
                self._parts[name] = parse_xml(self.archive.read(name))
            except (KeyError, ET.ParseError):
                self._parts[name] = None
        return self._parts[name]

    def rels(self, part_name: str) -> dict[str, str]:
        """rId -> target part name, resolved against the part's own directory."""
        if part_name in self._rels:
            return self._rels[part_name]

        directory = posixpath.dirname(part_name)
        rels_name = posixpath.join(directory, "_rels", posixpath.basename(part_name) + ".rels")
        mapping: dict[str, str] = {}
        try:
            root = parse_xml(self.archive.read(rels_name))
        except (KeyError, ET.ParseError):
            self._rels[part_name] = mapping
            return mapping

        for relationship in root.findall(RELS + "Relationship"):
            target = relationship.get("Target", "")
            if relationship.get("TargetMode") == "External" or not target:
                continue
            resolved = target[1:] if target.startswith("/") else posixpath.normpath(
                posixpath.join(directory, target)
            )
            mapping[relationship.get("Id", "")] = resolved
        self._rels[part_name] = mapping
        return mapping

    def related(self, part_name: str, suffix: str) -> str | None:
        """The first related part whose name matches a directory, e.g. 'slideLayouts'."""
        for target in self.rels(part_name).values():
            if target.startswith(f"ppt/{suffix}/"):
                return target
        return None


# --- colour -----------------------------------------------------------------------


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "".join(f"{int(round(clamp(channel, 0, 255))):02X}" for channel in rgb)


def apply_modifiers(hex_value: str, element: ET.Element) -> str:
    """Apply the DrawingML colour transforms Office actually emits."""
    red, green, blue = hex_to_rgb(hex_value)

    lum_mod = element.find(A + "lumMod")
    lum_off = element.find(A + "lumOff")
    if lum_mod is not None or lum_off is not None:
        hue, lightness, saturation = colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)
        if lum_mod is not None:
            lightness *= int(lum_mod.get("val", "100000")) / 100000
        if lum_off is not None:
            lightness += int(lum_off.get("val", "0")) / 100000
        red, green, blue = (
            channel * 255
            for channel in colorsys.hls_to_rgb(hue, clamp(lightness), saturation)
        )

    shade = element.find(A + "shade")
    if shade is not None:
        factor = int(shade.get("val", "100000")) / 100000
        red, green, blue = (channel * factor for channel in (red, green, blue))

    tint = element.find(A + "tint")
    if tint is not None:
        factor = int(tint.get("val", "100000")) / 100000
        red, green, blue = (
            channel * factor + 255 * (1 - factor) for channel in (red, green, blue)
        )

    return rgb_to_hex((red, green, blue))


class Theme:
    """The colour scheme of one slide master, with its bg/tx mapping applied."""

    def __init__(self, theme_root: ET.Element | None, master_root: ET.Element | None) -> None:
        self.scheme: dict[str, str] = {}
        if theme_root is not None:
            elements = theme_root.find(A + "themeElements")
            scheme = elements.find(A + "clrScheme") if elements is not None else None
            for entry in scheme if scheme is not None else []:
                name = entry.tag[len(A):]
                srgb = entry.find(A + "srgbClr")
                system = entry.find(A + "sysClr")
                if srgb is not None:
                    self.scheme[name] = srgb.get("val", "000000").upper()
                elif system is not None:
                    self.scheme[name] = system.get("lastClr", "000000").upper()

        self.mapping: dict[str, str] = {}
        if master_root is not None:
            colour_map = master_root.find(P + "clrMap")
            if colour_map is not None:
                self.mapping = dict(colour_map.attrib)

    def scheme_colour(self, name: str) -> str | None:
        return self.scheme.get(self.mapping.get(name, name))

    def colour(self, element: ET.Element) -> str | None:
        """Resolve one DrawingML colour element to an RRGGBB string."""
        tag = element.tag[len(A):]
        if tag == "srgbClr":
            base = element.get("val", "").upper()
        elif tag == "sysClr":
            base = element.get("lastClr", "").upper()
        elif tag == "schemeClr":
            base = self.scheme_colour(element.get("val", "")) or ""
        elif tag == "scrgbClr":
            base = rgb_to_hex(
                tuple(int(element.get(c, "0")) / 100000 * 255 for c in ("r", "g", "b"))
            )
        else:
            return None
        if not re.fullmatch(r"[0-9A-F]{6}", base or ""):
            return None
        return apply_modifiers(base, element)

    def solid_fill(self, parent: ET.Element | None) -> tuple[str | None, str]:
        """(colour, kind) for a parent that may hold a fill. kind: solid/none/other/absent."""
        if parent is None:
            return None, "absent"
        if parent.find(A + "noFill") is not None:
            return None, "none"
        for other in ("blipFill", "gradFill", "pattFill"):
            if parent.find(A + other) is not None:
                return None, "other"
        fill = parent.find(A + "solidFill")
        if fill is None:
            return None, "absent"
        for child in fill:
            colour = self.colour(child)
            if colour:
                return colour, "solid"
        return None, "other"


def relative_luminance(hex_value: str) -> float:
    def channel(raw: int) -> float:
        value = raw / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(c) for c in hex_to_rgb(hex_value))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    first, second = relative_luminance(foreground), relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


# --- shape model ------------------------------------------------------------------


class Run:
    __slots__ = ("text", "size", "bold", "colour", "size_known")

    def __init__(self, text: str, size: float | None, bold: bool, colour: str | None) -> None:
        self.text = text
        self.size = size
        self.bold = bold
        self.colour = colour
        self.size_known = size is not None


def attribute_int(element: ET.Element | None, name: str) -> int | None:
    if element is None:
        return None
    raw = element.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def placeholder_key(shape: ET.Element) -> tuple[str, int] | None:
    nv = shape.find(P + "nvSpPr")
    if nv is None:
        return None
    nv_pr = nv.find(P + "nvPr")
    if nv_pr is None:
        return None
    ph = nv_pr.find(P + "ph")
    if ph is None:
        return None
    return ph.get("type", "body"), int(ph.get("idx", "0") or 0)


def shape_name(shape: ET.Element) -> str:
    nv = shape.find(P + "nvSpPr")
    c_nv = nv.find(P + "cNvPr") if nv is not None else None
    return (c_nv.get("name") if c_nv is not None else None) or "unnamed shape"


def iter_shapes(root: ET.Element | None):
    """Every p:sp in a shape tree, descending into groups."""
    if root is None:
        return
    tree = root.find(P + "cSld")
    tree = tree.find(P + "spTree") if tree is not None else None
    if tree is None:
        return

    def walk(node: ET.Element):
        for child in node:
            if child.tag == P + "sp":
                yield child
            elif child.tag == P + "grpSp":
                yield from walk(child)

    yield from walk(tree)


def placeholder_index(root: ET.Element | None) -> dict[tuple[str, int], ET.Element]:
    index: dict[tuple[str, int], ET.Element] = {}
    for shape in iter_shapes(root):
        key = placeholder_key(shape)
        if key is not None:
            index[key] = shape
    return index


def extent(shape: ET.Element) -> tuple[int, int] | None:
    sp_pr = shape.find(P + "spPr")
    xfrm = sp_pr.find(A + "xfrm") if sp_pr is not None else None
    ext = xfrm.find(A + "ext") if xfrm is not None else None
    cx, cy = attribute_int(ext, "cx"), attribute_int(ext, "cy")
    return (cx, cy) if cx and cy else None


def offset(shape: ET.Element) -> tuple[int, int] | None:
    sp_pr = shape.find(P + "spPr")
    xfrm = sp_pr.find(A + "xfrm") if sp_pr is not None else None
    position = xfrm.find(A + "off") if xfrm is not None else None
    x, y = attribute_int(position, "x"), attribute_int(position, "y")
    return (x, y) if x is not None and y is not None else None


def inherited_placeholder(
    key: tuple[str, int],
    layout_placeholders: dict[tuple[str, int], ET.Element],
    master_placeholders: dict[tuple[str, int], ET.Element],
) -> ET.Element | None:
    """The layout's placeholder for this key, or the master's. Never `or`: an
    ElementTree element with no children is falsey, so the obvious idiom drops
    exactly the empty placeholders this needs to find."""
    found = layout_placeholders.get(key)
    return found if found is not None else master_placeholders.get(key)


def default_size_from(shape: ET.Element | None, level: int) -> float | None:
    """The defRPr size declared by a shape's own list style, in points."""
    if shape is None:
        return None
    body = shape.find(P + "txBody")
    style = body.find(A + "lstStyle") if body is not None else None
    if style is None:
        return None
    level_properties = style.find(f"{A}lvl{level + 1}pPr")
    size = attribute_int(level_properties.find(A + "defRPr") if level_properties is not None else None, "sz")
    return size / 100 if size else None


def master_style_size(master: ET.Element | None, placeholder: tuple[str, int] | None, level: int) -> float | None:
    if master is None:
        return None
    styles = master.find(P + "txStyles")
    if styles is None:
        return None
    kind = placeholder[0] if placeholder else "body"
    name = "titleStyle" if kind in TITLE_PH else ("bodyStyle" if kind in {"body", "subTitle", "obj", "outline"} else "otherStyle")
    style = styles.find(P + name)
    if style is None:
        return None
    level_properties = style.find(f"{A}lvl{level + 1}pPr")
    size = attribute_int(level_properties.find(A + "defRPr") if level_properties is not None else None, "sz")
    return size / 100 if size else None


class SlideReport:
    def __init__(self, number: int) -> None:
        self.number = number
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.text_blocks = 0
        self.min_font: float | None = None
        self.min_contrast: float | None = None
        self.has_content = False

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)


# --- the checks -------------------------------------------------------------------


def collect_runs(paragraph: ET.Element, theme: Theme) -> list[Run]:
    runs: list[Run] = []
    for child in paragraph:
        if child.tag not in (A + "r", A + "fld"):
            continue
        text_element = child.find(A + "t")
        text = text_element.text or "" if text_element is not None else ""
        properties = child.find(A + "rPr")
        size = attribute_int(properties, "sz")
        bold = (properties.get("b") if properties is not None else None) == "1"
        colour, _ = theme.solid_fill(properties)
        runs.append(Run(text, size / 100 if size else None, bold, colour))
    return runs


def estimate_height(
    paragraphs: list[tuple[list[Run], float, float]],
    width_pt: float,
    wrap: bool = True,
) -> float:
    """Estimated rendered height in points for a list of paragraphs."""
    total = 0.0
    for runs, spacing_before, spacing_after in paragraphs:
        text = "".join(run.text for run in runs)
        size = max((run.size or BODY_FONT_PT) for run in runs) if runs else BODY_FONT_PT
        if wrap:
            characters_per_line = max(1.0, width_pt / (AVG_CHAR_WIDTH_EM * size))
            lines = max(1, math.ceil(len(text) / characters_per_line)) if text else 1
        else:
            lines = 1
        total += spacing_before + lines * size * LINE_HEIGHT_EM + spacing_after
    return total


def estimate_width(paragraphs: list[tuple[list[Run], float, float]]) -> float:
    """Estimated width in points of the longest unwrapped line."""
    widest = 0.0
    for runs, _, _ in paragraphs:
        text = "".join(run.text for run in runs)
        size = max((run.size or BODY_FONT_PT) for run in runs) if runs else BODY_FONT_PT
        widest = max(widest, len(text) * AVG_CHAR_WIDTH_EM * size)
    return widest


def spacing_points(paragraph_properties: ET.Element | None, tag: str) -> float:
    if paragraph_properties is None:
        return 0.0
    spacing = paragraph_properties.find(A + tag)
    points = spacing.find(A + "spcPts") if spacing is not None else None
    value = attribute_int(points, "val")
    return value / 100 if value else 0.0


def check_shape(
    shape: ET.Element,
    report: SlideReport,
    theme: Theme,
    slide_background: str | None,
    layout_placeholders: dict[tuple[str, int], ET.Element],
    master_placeholders: dict[tuple[str, int], ET.Element],
    master: ET.Element | None,
    limits: argparse.Namespace,
) -> None:
    body = shape.find(P + "txBody")
    if body is None:
        return

    name = shape_name(shape)
    where = f"slide {report.number} / {name!r}"
    placeholder = placeholder_key(shape)
    is_title = placeholder is not None and placeholder[0] in TITLE_PH

    body_properties = body.find(A + "bodyPr")
    fit_scale = 1.0
    auto_grow = False
    if body_properties is not None:
        if body_properties.find(A + "spAutoFit") is not None:
            auto_grow = True
        normalise = body_properties.find(A + "normAutofit")
        if normalise is not None:
            fit_scale = int(normalise.get("fontScale", "100000")) / 100000

    # The background this shape's text is read against: its own fill if it has one,
    # otherwise whatever is behind it.
    fill_colour, fill_kind = theme.solid_fill(shape.find(P + "spPr"))
    if fill_kind == "solid":
        background = fill_colour
    elif fill_kind == "other":
        background = None
        report.note(
            f"{where}: sits on a picture or gradient fill, so its contrast cannot be "
            "computed — check it by eye."
        )
    else:
        background = slide_background

    paragraphs: list[tuple[list[Run], float, float]] = []
    bullets = 0
    non_empty = False

    for index, paragraph in enumerate(body.findall(A + "p")):
        properties = paragraph.find(A + "pPr")
        level = int(properties.get("lvl", "0")) if properties is not None else 0
        runs = collect_runs(paragraph, theme)
        text = "".join(run.text for run in runs).strip()
        paragraphs.append(
            (runs, spacing_points(properties, "spcBef"), spacing_points(properties, "spcAft"))
        )

        if not text:
            continue
        non_empty = True
        report.has_content = True
        bullets += 1

        if PROMPT_TEXT.match(text):
            report.error(
                f"{where}: still holds the placeholder prompt {text!r}. Fill it in or "
                "delete the shape — it will be printed on the slide as written."
            )

        words = len(text.split())
        if words > limits.max_words:
            report.error(
                f"{where}, bullet {index + 1}: {words} words (limit {limits.max_words}). "
                "A slide is a cue, not a paragraph — cut it to a phrase and move the "
                "sentence to the speaker notes."
            )
        elif words > WARN_WORDS_PER_BULLET:
            report.warn(
                f"{where}, bullet {index + 1}: {words} words; aim for "
                f"{WARN_WORDS_PER_BULLET} or fewer."
            )

        for run in runs:
            if not run.text.strip():
                continue
            size = (run.size * fit_scale) if run.size is not None else None
            if size is None:
                size = default_size_from(shape, level)
                if size is None and placeholder is not None:
                    size = default_size_from(
                        inherited_placeholder(
                            placeholder, layout_placeholders, master_placeholders
                        ),
                        level,
                    )
                if size is None:
                    size = master_style_size(master, placeholder, level)
            if size is None:
                report.note(
                    f"{where}: some runs inherit their size from the theme and could "
                    "not be resolved; the font-size floor was not checked for them."
                )
            else:
                report.min_font = size if report.min_font is None else min(report.min_font, size)
                if size < limits.min_font:
                    report.error(
                        f"{where}, bullet {index + 1}: {size:g}pt text is below the "
                        f"{limits.min_font:g}pt floor — unreadable past the second row. "
                        "Raise it, or cut the content so it fits at a readable size."
                    )
                elif not is_title and size < BODY_FONT_PT:
                    report.warn(
                        f"{where}, bullet {index + 1}: {size:g}pt body text is under the "
                        f"{BODY_FONT_PT:g}pt recommended floor."
                    )

            if run.colour and background:
                ratio = contrast_ratio(run.colour, background)
                report.min_contrast = (
                    ratio if report.min_contrast is None else min(report.min_contrast, ratio)
                )
                effective = size or BODY_FONT_PT
                large = effective >= LARGE_TEXT_PT or (run.bold and effective >= LARGE_TEXT_BOLD_PT)
                minimum = CONTRAST_AA_LARGE if large else CONTRAST_FLOOR
                if ratio < minimum:
                    report.error(
                        f"{where}, bullet {index + 1}: #{run.colour} on #{background} is "
                        f"{ratio:.2f}:1, under the {minimum:g}:1 WCAG AA minimum for "
                        f"{'large' if large else 'normal'} text. Darken the text or "
                        "lighten the background — see references/design.md."
                    )
                elif ratio < CONTRAST_FLOOR:
                    report.warn(
                        f"{where}, bullet {index + 1}: #{run.colour} on #{background} is "
                        f"{ratio:.2f}:1. It clears WCAG AA for large text but is under "
                        f"the {CONTRAST_FLOOR:g}:1 projection floor; a washed-out "
                        "projector will lose it."
                    )

    if not non_empty:
        return

    report.text_blocks += 1

    if bullets > limits.max_bullets and not is_title:
        report.error(
            f"{where}: {bullets} bullets, the limit is {limits.max_bullets}. Split the "
            "slide in two, or promote the extras to speaker notes."
        )

    size = extent(shape)
    position = offset(shape)
    if placeholder is not None:
        inherited = inherited_placeholder(
            placeholder, layout_placeholders, master_placeholders
        )
        if inherited is not None:
            size = size if size is not None else extent(inherited)
            position = position if position is not None else offset(inherited)
    if size is None:
        return

    width_emu, height_emu = size
    left_inset = attribute_int(body_properties, "lIns")
    right_inset = attribute_int(body_properties, "rIns")
    top_inset = attribute_int(body_properties, "tIns")
    bottom_inset = attribute_int(body_properties, "bIns")
    left_inset = 91440 if left_inset is None else left_inset
    right_inset = 91440 if right_inset is None else right_inset
    top_inset = 45720 if top_inset is None else top_inset
    bottom_inset = 45720 if bottom_inset is None else bottom_inset

    width_pt = (width_emu - left_inset - right_inset) / EMU_PER_POINT
    height_pt = (height_emu - top_inset - bottom_inset) / EMU_PER_POINT
    if width_pt <= 0 or height_pt <= 0:
        return

    wrap = (body_properties.get("wrap") if body_properties is not None else None) != "none"

    # Unwrapped text does not break at the box edge; it keeps going, straight past
    # the edge of the slide. python-pptx writes wrap="none" on a new text box by
    # default, so this is a defect a generated deck reaches by doing nothing.
    if not wrap:
        needed_width = estimate_width(paragraphs)
        left_pt = (position[0] + left_inset) / EMU_PER_POINT if position else 0.0
        if left_pt + needed_width > limits.slide_width_pt * OVERFLOW_TOLERANCE:
            report.error(
                f"{where}: the text does not wrap and needs about {needed_width:.0f}pt "
                f"of width, running past the {limits.slide_width_pt:.0f}pt slide edge. "
                "Turn wrapping on for this box, or shorten the line."
            )

    needed = estimate_height(paragraphs, width_pt, wrap=wrap)
    if needed <= height_pt * OVERFLOW_TOLERANCE:
        return

    if not auto_grow:
        report.error(
            f"{where}: the text needs about {needed:.0f}pt of height but the box is "
            f"{height_pt:.0f}pt and does not grow. It will spill out of the box — cut "
            "text, drop a bullet, or make the box taller."
        )
        return

    # An auto-fitting box grows downwards instead of clipping, which is only fine
    # while there is slide left to grow into.
    top_pt = (position[1] + top_inset) / EMU_PER_POINT if position else 0.0
    if top_pt + needed > limits.slide_height_pt * OVERFLOW_TOLERANCE:
        report.error(
            f"{where}: the text needs about {needed:.0f}pt of height from {top_pt:.0f}pt "
            f"down, running off the bottom of the {limits.slide_height_pt:.0f}pt slide. "
            "Cut text or split the slide."
        )
    else:
        report.warn(
            f"{where}: the text needs about {needed:.0f}pt but its box is {height_pt:.0f}pt. "
            "The box will grow to fit and may collide with what is under it."
        )


def slide_has_graphics(slide: ET.Element) -> bool:
    return any(
        any(True for _ in slide.iter(tag)) for tag in (P + "pic", P + "graphicFrame")
    )


def graphic_text(slide: ET.Element) -> bool:
    for frame in slide.iter(P + "graphicFrame"):
        for text in frame.iter(A + "t"):
            if (text.text or "").strip():
                return True
    return False


def check_graphic_fonts(slide: ET.Element, report: SlideReport, limits: argparse.Namespace) -> None:
    """Font floor for text inside tables and charts, where an explicit size is present."""
    for frame in slide.iter(P + "graphicFrame"):
        for run in frame.iter(A + "r"):
            text_element = run.find(A + "t")
            if text_element is None or not (text_element.text or "").strip():
                continue
            size = attribute_int(run.find(A + "rPr"), "sz")
            if size is None:
                continue
            points = size / 100
            report.min_font = points if report.min_font is None else min(report.min_font, points)
            if points < limits.min_font:
                report.error(
                    f"slide {report.number} / table or chart: {points:g}pt text is below "
                    f"the {limits.min_font:g}pt floor. Table text on a slide is read from "
                    "the back of the room too."
                )


def check_slide(
    package: Package,
    slide_part: str,
    number: int,
    limits: argparse.Namespace,
) -> SlideReport:
    report = SlideReport(number)
    slide = package.part(slide_part)
    if slide is None:
        report.error(f"slide {number}: {slide_part} is missing or is not parseable XML.")
        return report

    layout_part = package.related(slide_part, "slideLayouts")
    layout = package.part(layout_part) if layout_part else None
    master_part = package.related(layout_part, "slideMasters") if layout_part else None
    master = package.part(master_part) if master_part else None
    theme_part = package.related(master_part, "theme") if master_part else None
    theme = Theme(package.part(theme_part) if theme_part else None, master)

    background = None
    for root in (slide, layout, master):
        if root is None:
            continue
        common = root.find(P + "cSld")
        element = common.find(P + "bg") if common is not None else None
        properties = element.find(P + "bgPr") if element is not None else None
        colour, kind = theme.solid_fill(properties)
        if kind == "solid":
            background = colour
            break
        reference = element.find(P + "bgRef") if element is not None else None
        if reference is not None:
            for child in reference:
                colour = theme.colour(child)
                if colour:
                    background = colour
                    break
            if background:
                break
    if background is None:
        background = theme.scheme_colour("bg1") or "FFFFFF"

    layout_placeholders = placeholder_index(layout)
    master_placeholders = placeholder_index(master)

    for shape in iter_shapes(slide):
        check_shape(
            shape,
            report,
            theme,
            background,
            layout_placeholders,
            master_placeholders,
            master,
            limits,
        )

    check_graphic_fonts(slide, report, limits)

    if graphic_text(slide) or slide_has_graphics(slide):
        report.has_content = True

    if not report.has_content:
        report.error(
            f"slide {number}: nothing on it — no text, no image, no table. Either give "
            "it content or delete it; an empty slide in a delivered deck reads as a "
            "generation failure."
        )

    return report


# --- CLI --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_pptx.py",
        description=(
            "Verify a .pptx is a real presentation and that every slide is presentable: "
            "no empty slides, no text overflowing its box, no low-contrast or "
            "undersized type, no over-dense bullet lists."
        ),
        epilog=(
            "Example:\n"
            "  python3 validate_pptx.py deck.pptx\n\n"
            "Exit code 0 means the deck is deliverable. Exit code 1 means do not hand "
            "it to the user; every ERROR line names the slide and what to change.\n"
            "Runs offline, stdlib only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pptx", help="path to the .pptx file to check")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as failures (exit 1)",
    )
    parser.add_argument(
        "--min-font",
        type=float,
        default=MIN_FONT_PT,
        metavar="PT",
        help=f"hard font-size floor in points (default {MIN_FONT_PT:g})",
    )
    parser.add_argument(
        "--max-bullets",
        type=int,
        default=MAX_BULLETS,
        metavar="N",
        help=f"maximum bullets in one text block (default {MAX_BULLETS})",
    )
    parser.add_argument(
        "--max-words",
        type=int,
        default=MAX_WORDS_PER_BULLET,
        metavar="N",
        help=f"maximum words in one bullet (default {MAX_WORDS_PER_BULLET})",
    )
    parser.add_argument(
        "--max-slides",
        type=int,
        default=MAX_SLIDES,
        metavar="N",
        help=f"maximum slide count (default {MAX_SLIDES})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.pptx

    if not os.path.exists(path):
        print(f"ERROR: file: {path} does not exist", file=sys.stderr)
        return 1
    if os.path.isdir(path):
        print(f"ERROR: file: {path} is a directory, expected a .pptx file", file=sys.stderr)
        return 1

    size = os.path.getsize(path)
    if size == 0:
        print(f"ERROR: file: {path} is empty (0 bytes)", file=sys.stderr)
        return 1

    if not zipfile.is_zipfile(path):
        print(
            f"ERROR: container: {path} is not a ZIP archive, so it is not a .pptx. "
            "Whatever produced this wrote something else, or renamed a file that was "
            "never a presentation.",
            file=sys.stderr,
        )
        return 1

    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        print(f"ERROR: container: {path} is a corrupt ZIP ({exc})", file=sys.stderr)
        return 1

    with archive:
        names = set(archive.namelist())
        for required in ("[Content_Types].xml", "ppt/presentation.xml"):
            if required not in names:
                print(
                    f"ERROR: container: {path} has no {required} — it is a ZIP but not a "
                    "PowerPoint package.",
                    file=sys.stderr,
                )
                return 1

        package = Package(archive)
        presentation = package.part("ppt/presentation.xml")
        if presentation is None:
            print(
                "ERROR: container: ppt/presentation.xml is not parseable XML — the file "
                "is corrupt.",
                file=sys.stderr,
            )
            return 1

        slide_size = presentation.find(P + "sldSz")
        width_emu = attribute_int(slide_size, "cx") or 0
        height_emu = attribute_int(slide_size, "cy") or 0
        # Carried on the same namespace the limits ride in, so every check has the
        # slide's own edges to measure against.
        args.slide_width_pt = width_emu / EMU_PER_POINT
        args.slide_height_pt = height_emu / EMU_PER_POINT

        relationships = package.rels("ppt/presentation.xml")
        slide_parts: list[str] = []
        id_list = presentation.find(P + "sldIdLst")
        for entry in id_list if id_list is not None else []:
            target = relationships.get(entry.get(R + "id", ""))
            if target:
                slide_parts.append(target)

        if not slide_parts:
            print(
                f"ERROR: slides: {path} contains no slides. The presentation is an empty "
                "shell — check that the deck spec actually reached the builder.",
                file=sys.stderr,
            )
            return 1

        reports = [
            check_slide(package, part, number, args)
            for number, part in enumerate(slide_parts, 1)
        ]

    errors = [message for report in reports for message in report.errors]
    warnings = [message for report in reports for message in report.warnings]
    notes = [message for report in reports for message in report.notes]

    if len(slide_parts) > args.max_slides:
        errors.append(
            f"deck: {len(slide_parts)} slides, over the {args.max_slides}-slide cap. "
            "Cut it down, or raise the cap deliberately with --max-slides."
        )

    for message in notes:
        print(f"NOTE: {message}")
    for message in warnings:
        print(f"WARNING: {message}", file=sys.stderr)
    for message in errors:
        print(f"ERROR: {message}", file=sys.stderr)

    if errors:
        affected = sum(1 for report in reports if report.errors)
        scope = f"on {affected} of {len(reports)} slide(s)" if affected else "at deck level"
        print(
            f"\n{len(errors)} problem(s) {scope}. The deck is not deliverable until "
            "they are fixed.",
            file=sys.stderr,
        )
        return 1
    if warnings and args.strict:
        print(
            f"\n{len(warnings)} warning(s), failed by --strict.",
            file=sys.stderr,
        )
        return 1

    width_in = width_emu / EMU_PER_INCH
    height_in = height_emu / EMU_PER_INCH
    ratio = "16:9" if abs(width_in / max(height_in, 0.01) - 16 / 9) < 0.02 else (
        "4:3" if abs(width_in / max(height_in, 0.01) - 4 / 3) < 0.02 else "custom"
    )
    print(
        f"OK: {path} is a .pptx presentation, {size} bytes, {len(slide_parts)} slide(s), "
        f"{width_in:.2f}x{height_in:.2f} in ({ratio})"
    )
    for report in reports:
        font = f"min font {report.min_font:g}pt" if report.min_font else "no sized text"
        contrast = (
            f"min contrast {report.min_contrast:.1f}:1"
            if report.min_contrast
            else "contrast not computable"
        )
        print(f"  slide {report.number}: {report.text_blocks} text block(s), {font}, {contrast}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
