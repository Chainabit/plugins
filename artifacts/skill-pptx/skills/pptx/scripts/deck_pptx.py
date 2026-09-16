#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a .pptx deck from a JSON spec using eight brand-safe layouts.

python-pptx will happily place a 9pt run of grey text in a box too small for it.
Nothing in the library has an opinion about whether the result can be read from
the back of a room, so the opinion lives here: eight fixed layouts — four that
are text only, three that carry a picture, one that carries a chart — whose
geometry, palette and type scale are chosen once, checked against WCAG AA, and
then not negotiable per slide.

What the caller supplies is content. What this script decides is layout.

The spec is fully validated before a byte is written, and every problem is
reported at once as `ERROR: <field>: <reason>`, so a generated spec can be fixed
in one pass rather than one exit code at a time. Density limits are part of that
validation: a slide that cannot hold its own text at a readable size is a spec
error, not something to discover in the rendered file.

A picture and a chart are validated the same way, and for the same reason. A
deck that was asked for with a figure in it used to come back with the figure
described in a bullet — "(Chart inserted here by script)" — because there was no
layout that could hold one. There is now, and the field that carries the figure
is REQUIRED by the layout that declares it: `image.path` must name a readable
picture inside the spec's own directory, and a `chart` slide must carry either
plottable data or a picture of the chart. A slide cannot declare a figure and
then not have one, so the stand-in cannot be written in the first place.

Usage:
    python3 deck_pptx.py spec.json deck.pptx [--validate-only]

Spec shape (see SKILL.md for the annotated version):

    {
      "title": "Q3 Review",                 required, also the file's metadata title
      "subtitle": "Operations",             optional
      "author": "Operations",               optional
      "theme": "light" | "dark",            optional, default light
      "aspect": "16:9" | "4:3",             optional, default 16:9
      "font": "IBM Plex Sans",              optional, Chainabit default
      "slides": [                           required, at least one
        {"layout": "title",      "title": "...", "subtitle": "...", "meta": "..."},
        {"layout": "content",    "title": "...", "bullets": ["..."], "note": "..."},
        {"layout": "comparison", "title": "...",
                                 "left":  {"heading": "...", "bullets": ["..."]},
                                 "right": {"heading": "...", "bullets": ["..."]}},
        {"layout": "closing",    "title": "...", "subtitle": "...", "contact": "..."},
        {"layout": "title-image","title": "...", "subtitle": "...", "meta": "...",
                                 "image": {"path": "...", "alt": "..."}},
        {"layout": "image-content", "title": "...", "bullets": ["..."],
                                 "image": {"path": "...", "alt": "..."},
                                 "caption": "..."},
        {"layout": "image-full", "title": "...", "caption": "...",
                                 "image": {"path": "...", "alt": "..."}},
        {"layout": "chart",      "title": "...", "note": "...",
                                 "chart": {"kind": "bar"|"line"|"pie",
                                           "categories": ["..."],
                                           "series": [{"name": "...", "values": [1]}]}}
      ]
    }

A `chart` slide may carry `"image"` instead of `"chart"` when the figure already
exists as a picture; a native chart is preferred, because it stays editable in
the delivered file.

Any slide may carry "notes": "..." — speaker notes, which is where the sentence
belongs when the bullet has to stay a phrase.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path


#: The validator a deck built here is checked by when it is delivered.
DECK_VALIDATOR_ID = "skill-pptx.validate_pptx"


def validation_handoff(output: str) -> str:
    """The `Next:` line printed after a successful build.

    A printed line is read as an instruction. It used to be a command that ran
    the validator, which readers then chained onto the build in one command
    line -- and a build joined to other commands is no longer a build delivery
    can recognise, so the deck was refused. The deck is validated when it is
    delivered, so the line states that fact and names no command to run.
    """
    return f"Next: deliver {output}; it is checked by {DECK_VALIDATOR_ID} on delivery."

LAYOUTS = (
    "title",
    "content",
    "comparison",
    "closing",
    "title-image",
    "image-content",
    "image-full",
    "chart",
)
#: Layouts whose `image` field is required rather than optional.
IMAGE_LAYOUTS = ("title-image", "image-content", "image-full")
DEFAULT_FONT = os.environ.get("CHAINABIT_ARTIFACT_FONT_FAMILY", "IBM Plex Sans").strip() or "IBM Plex Sans"
ARABIC_FALLBACK_FONT = "IBM Plex Sans Arabic"
EXECUTION_SCHEMA = "chainabit.pptx.execution/v1"

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
#: A caption band sits under or over a picture and holds one or two short lines.
CAPTION_SIZES = (20, 18)

# Same estimator as validate_pptx.py, so a deck this script writes passes that
# gate rather than merely being likely to.
AVG_CHAR_WIDTH_EM = 0.5
LINE_HEIGHT_EM = 1.2
BULLET_SPACING_PT = 12

# Renderer-local projection of skill-brand-defaults' profile. Bundles are
# materialised independently, so importing another skill at runtime would break
# portability. The artifact contract test anchors these values to the shared
# profile; the resolved palette remains this renderer's Information Expert.
THEMES = {
    "light": {
        "background": "FFFFFF",
        "surface": "F9FAFB",
        "ink": "101828",
        "body": "364153",
        "muted": "6A7282",
        "rule": "E5E7EB",
        "accent": "327B61",
    },
    "dark": {
        "background": "010102",
        "surface": "18181B",
        "ink": "D7D7DA",
        "body": "A4A4A9",
        "muted": "85858D",
        "rule": "1F1F22",
        "accent": "70BD9E",
    },
}
PALETTE_KEYS = tuple(THEMES["light"])
HEX_COLOUR = re.compile(r"^#?[0-9A-Fa-f]{6}$")

ASPECTS = {"16:9": (13.333, 7.5), "4:3": (10.0, 7.5)}

# --- the image boundary. A picture reaches a slide only as a regular file that
# this script has opened, decoded and bounded first. The rules are the pdf
# capability's, kept to the same numbers deliberately: a deck and the report
# beside it must not disagree about which picture is safe to embed.
#
# A remote URL is not an input. The renderer runs with no network, so a URL is
# never a picture — it is a silent blank where a picture was promised. It is
# refused at the boundary and named, rather than left to fail at draw time.
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
# A deck or its PDF companion is one hosted file, and the API promotion boundary
# accepts at most 16 MiB per file. Reserve 1 MiB for OOXML/PDF structure, slide
# XML, fonts and metadata; the raw distinct image set cannot consume that space.
MAX_HOSTED_OUTPUT_BYTES = 16 * 1024 * 1024
MIN_PACKAGE_OVERHEAD_BYTES = 1 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = MAX_HOSTED_OUTPUT_BYTES - MIN_PACKAGE_OVERHEAD_BYTES
MAX_DISTINCT_IMAGES = MAX_SLIDES
#: The intersection of what the pdf capability accepts and what an OOXML package
#: can carry. WebP is in the first set and not the second, so it is named as
#: unsupported here rather than failing inside python-pptx.
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif"}
IMAGE_FILE_FORMATS = "PNG, JPEG or GIF"
IMAGE_DECODER_FORMATS = ("PNG", "JPEG", "GIF")
MIN_IMAGE_EDGE_PX = 8


@dataclass(frozen=True)
class ValidatedImage:
    """Image bytes and dimensions established by the input boundary once."""

    data: bytes
    mime: str
    width: int
    height: int

# --- charts. A native chart is data the delivered file still owns: it can be
# recoloured, corrected and re-pointed by whoever opens it. A picture of a chart
# is a screenshot with no numbers behind it. Both are accepted, in that order of
# preference.
CHART_KINDS = ("bar", "line", "pie")
MAX_CATEGORIES = 12
MAX_SERIES = 4
CHART_TEXT_PT = 16

#: Series colours, quoted with their measured ratios in references/design.md.
#: The deck's own accent leads, then the semantic set; never encode meaning in
#: colour alone, which is why every native chart is drawn with value labels.
SERIES_COLOURS = {
    "light": ("327B61", "0F766E", "B45309", "BE123C"),
    "dark": ("70BD9E", "5EEAD4", "FCD34D", "FDA4AF"),
}

# --- unresolved stand-ins. A deck asked for with a figure in it came back with
# the figure written out as a sentence — "(Chart inserted here by script)" —
# because no layout could hold one. The layouts exist now, so a slide that
# announces a figure has somewhere to put it, and text that announces one it is
# not carrying is a spec error.
#
# This matches the SHAPE of a stand-in, not its meaning: a bracketed span that
# names a figure and marks it as not-yet-there. validate_pptx.py already refuses
# PowerPoint's own "Click to add title" on the same principle.
FIGURE_NOUN = r"chart|graph|plot|image|picture|photo|diagram|figure|screenshot|visual|logo|icon|illustration"
STAND_IN = (
    r"insert|inserted|inserting|placeholder|goes here|to be added|to be inserted"
    r"|to follow|tbd|to be determined|todo|pending|will be added|add later"
    r"|coming soon|generated by|by script|here by|xxx"
)
BRACKETED_STAND_IN = re.compile(
    rf"[\(\[\{{<][^\)\]\}}>]*?(?:(?:{FIGURE_NOUN})[^\)\]\}}>]*?(?:{STAND_IN})"
    rf"|(?:{STAND_IN})[^\)\]\}}>]*?(?:{FIGURE_NOUN}))[^\)\]\}}>]*[\)\]\}}>]",
    re.IGNORECASE,
)
BARE_STAND_IN = re.compile(r"\b(?:TODO|TBD|FIXME|lorem ipsum)\b", re.IGNORECASE)


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


def measure_image_bytes(raw: bytes) -> tuple[str, int, int]:
    """Decode and bound exact picture bytes before they cross into a renderer.

    Raises ValueError with the reason. The caller turns that into an `ERROR:`
    line; nothing here writes to stderr or exits, so one bad picture is reported
    beside every other problem in the spec rather than ending the run early.
    """
    size = len(raw)
    if size > MAX_IMAGE_BYTES:
        raise ValueError(f"is {size} bytes, over the {MAX_IMAGE_BYTES}-byte image limit")
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - environment, not input
        raise ValueError(
            "cannot be checked because the image decoder is unavailable in this "
            "container; call the workspace.env tool to see what it has"
        ) from exc
    # Keep the public diagnostic for a real WebP without enabling Pillow's
    # WebP (or every installed) decoder.  Other formats stay deliberately
    # indistinguishable from malformed input at this boundary.
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        raise ValueError(f"is not {IMAGE_FILE_FORMATS}; a slide can only embed those")
    try:
        with Image.open(BytesIO(raw), formats=IMAGE_DECODER_FORMATS) as image:
            image.verify()
        with Image.open(BytesIO(raw), formats=IMAGE_DECODER_FORMATS) as image:
            width, height = image.size
            mime = Image.MIME.get(image.format or "")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("is malformed or cannot be safely decoded") from exc

    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError(
            f"is {width}x{height}, over the {MAX_IMAGE_PIXELS}-pixel image limit"
        )
    if width < MIN_IMAGE_EDGE_PX or height < MIN_IMAGE_EDGE_PX:
        raise ValueError(
            f"is {width}x{height}; a picture under {MIN_IMAGE_EDGE_PX}px on a side is "
            "not a figure on a slide"
        )
    if mime not in IMAGE_MIME_TYPES:
        raise ValueError(f"is not {IMAGE_FILE_FORMATS}; a slide can only embed those")
    return mime, width, height


def measure_image(path: Path) -> tuple[str, int, int]:
    """Compatibility helper for callers that already own a trusted path."""
    return measure_image_bytes(path.read_bytes())


def open_local_image(root: Path, reference: str) -> tuple[int, os.stat_result]:
    """Open a regular file below ``root`` without following any symlink.

    Directory descriptors pin each component while ``O_NOFOLLOW`` applies at
    the actual open, closing the check/open race a ``Path.resolve`` guard leaves.
    The caller owns the returned file descriptor.
    """
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required_flags):
        raise RuntimeError("this platform cannot securely open local image paths")

    components = reference.split("/")
    if not components or any(component in ("", ".", "..") for component in components):
        raise ValueError("is not a normalized relative image path")

    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | close_on_exec
    file_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
        | close_on_exec
    )
    directory_fd = os.open(root, directory_flags)
    try:
        for component in components[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(components[-1], file_flags, dir_fd=directory_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError(
                "uses a symbolic link or non-directory path component; image paths "
                "must name regular local files"
            ) from exc
        raise
    finally:
        os.close(directory_fd)

    metadata = os.fstat(file_fd)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(file_fd)
        raise ValueError("does not name a regular local file")
    return file_fd, metadata


def read_open_image(file_fd: int) -> ValidatedImage:
    """Read and decode one descriptor already pinned by ``open_local_image``."""
    try:
        chunks: list[bytes] = []
        remaining = MAX_IMAGE_BYTES + 1
        while remaining:
            chunk = os.read(file_fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(file_fd)
    mime, width, height = measure_image_bytes(data)
    return ValidatedImage(data=data, mime=mime, width=width, height=height)


def validate_image(
    image: object,
    where: str,
    root: Path | None,
    image_inventory: dict[tuple[int, int], ValidatedImage] | None = None,
    resolved_images: dict[str, ValidatedImage] | None = None,
) -> list[str]:
    """Check one `image` block: its fields, then the file it names.

    `root` is the directory the spec itself sits in, and a picture must resolve
    inside it. That is the same containment the pdf capability applies, and it
    is what keeps a spec from reaching an arbitrary file by relative path.
    `root` is None only when the caller has no spec on disk to anchor to, in
    which case the field checks still run and the file checks are skipped.
    """
    if not isinstance(image, dict):
        return [f"{where}: must be an object with 'path' and 'alt'"]

    problems = text_field(image.get("path"), f"{where}.path", required=True)
    problems.extend(
        text_field(
            image.get("alt"),
            f"{where}.alt",
            required=True,
        )
    )
    if problems:
        return problems

    reference = str(image["path"])
    if "://" in reference or reference.startswith("data:"):
        return [
            f"{where}.path: {reference!r} is a URL. The renderer has no network, so a "
            "picture must already be a file here; copy it in first and name the path "
            "the copy landed at."
        ]
    if os.path.isabs(reference):
        return [
            f"{where}.path: must be relative to the spec's own directory, not an "
            "absolute path"
        ]
    if root is None:
        return []

    if resolved_images is not None and reference in resolved_images:
        return []
    try:
        file_fd, metadata = open_local_image(root, reference)
    except FileNotFoundError:
        return [
            f"{where}.path: {reference!r} does not exist next to the spec. Name the "
            "path the file actually landed at."
        ]
    except (OSError, RuntimeError, ValueError) as exc:
        return [
            f"{where}.path: {reference!r} is outside the spec's own directory, is not "
            f"a regular file, or {exc}"
        ]
    identity = (metadata.st_dev, metadata.st_ino)
    if image_inventory is not None and identity in image_inventory:
        os.close(file_fd)
        asset = image_inventory[identity]
    else:
        current_total = (
            sum(len(item.data) for item in image_inventory.values())
            if image_inventory is not None
            else 0
        )
        if current_total + metadata.st_size > MAX_TOTAL_IMAGE_BYTES:
            os.close(file_fd)
            return [
                f"{where}.path: distinct image set would be "
                f"{current_total + metadata.st_size} bytes, over the "
                f"{MAX_TOTAL_IMAGE_BYTES}-byte aggregate image limit"
            ]
        try:
            asset = read_open_image(file_fd)
        except (OSError, ValueError) as exc:
            return [f"{where}.path: {reference!r} {exc}"]
    if image_inventory is not None:
        asset = image_inventory.setdefault(identity, asset)
    if resolved_images is not None:
        resolved_images[reference] = asset
    return []


def validate_series(series: object, where: str, categories: int, kind: str) -> list[str]:
    if not isinstance(series, list) or not series:
        return [f"{where}: required, must be a non-empty array of series objects"]
    limit = 1 if kind == "pie" else MAX_SERIES
    if len(series) > limit:
        problems = [
            f"{where}: {len(series)} series, the limit for a {kind} chart is {limit}."
            + (
                " A pie shows one whole; use a bar chart to compare several."
                if kind == "pie"
                else " Split the figure, or drop a series."
            )
        ]
    else:
        problems = []
    for index, entry in enumerate(series):
        at = f"{where}[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{at}: must be an object with 'name' and 'values'")
            continue
        problems.extend(text_field(entry.get("name"), f"{at}.name", required=True))
        values = entry.get("values")
        if not isinstance(values, list) or not values:
            problems.append(f"{at}.values: required, must be a non-empty array of numbers")
            continue
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            problems.append(f"{at}.values: every value must be a number")
            continue
        if len(values) != categories:
            problems.append(
                f"{at}.values: {len(values)} values for {categories} categories. A "
                "series plots one value per category."
            )
    return problems


def validate_chart(chart: object, where: str) -> list[str]:
    if not isinstance(chart, dict):
        return [f"{where}: must be an object with 'kind', 'categories' and 'series'"]

    kind = chart.get("kind")
    if kind not in CHART_KINDS:
        return [
            f"{where}.kind: must be one of {', '.join(CHART_KINDS)}, found {kind!r}"
        ]

    categories = chart.get("categories")
    problems: list[str] = []
    if not isinstance(categories, list) or not categories:
        problems.append(f"{where}.categories: required, must be a non-empty array of strings")
        categories = []
    else:
        if len(categories) > MAX_CATEGORIES:
            problems.append(
                f"{where}.categories: {len(categories)} categories, the limit is "
                f"{MAX_CATEGORIES}. Past that the labels stop being readable from the "
                "back of the room; aggregate, or split the figure."
            )
        for index, category in enumerate(categories):
            if not isinstance(category, str) or not category.strip():
                problems.append(f"{where}.categories[{index}]: must be a non-empty string")

    problems.extend(
        validate_series(chart.get("series"), f"{where}.series", len(categories), str(kind))
    )
    problems.extend(text_field(chart.get("unit"), f"{where}.unit", required=False))
    return problems


def unresolved_placeholder(value: str) -> str | None:
    """Name the stand-in in a text field, or None when there is none."""
    bracketed = BRACKETED_STAND_IN.search(value)
    if bracketed:
        return bracketed.group().strip()
    bare = BARE_STAND_IN.search(value)
    return bare.group() if bare else None


def iter_spec_text(node: object, where: str):
    """Every string in the spec, with the path it sits at."""
    if isinstance(node, str):
        yield where, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from iter_spec_text(value, f"{where}.{key}" if where else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_spec_text(value, f"{where}[{index}]")


def validate_no_placeholders(spec: object) -> list[str]:
    """Refuse text that stands in for a figure the deck does not carry."""
    problems: list[str] = []
    for where, value in iter_spec_text(spec, ""):
        # A path is a filename, not prose; `alt` describes a picture that the
        # boundary has already proved exists.
        if where.endswith(".path"):
            continue
        found = unresolved_placeholder(value)
        if found:
            problems.append(
                f"{where}: {found!r} is a stand-in, not content. A figure goes on a "
                "'title-image', 'image-content', 'image-full' or 'chart' slide, which "
                "carries the picture or the data itself; a deck may not describe one "
                "it is not showing."
            )
    return problems


def validate_slide(
    slide: object,
    where: str,
    root: Path | None = None,
    image_inventory: dict[tuple[int, int], ValidatedImage] | None = None,
    resolved_images: dict[str, ValidatedImage] | None = None,
) -> list[str]:
    if not isinstance(slide, dict):
        return [f"{where}: must be an object"]

    layout = slide.get("layout")
    if layout not in LAYOUTS:
        return [f"{where}.layout: must be one of {', '.join(LAYOUTS)}, found {layout!r}"]

    problems = text_field(slide.get("title"), f"{where}.title", required=True)
    problems.extend(text_field(slide.get("notes"), f"{where}.notes", required=False))

    # A layout that declares a figure must carry one. This is the check that
    # makes a stand-in impossible rather than merely discouraged: there is no
    # spelling of these slides that renders without the picture or the data.
    if layout in IMAGE_LAYOUTS:
        if slide.get("image") is None:
            problems.append(
                f"{where}.image: required for a {layout} slide — "
                "{'path': 'relative/path.png', 'alt': 'what it shows'}. A layout that "
                "shows a picture is not usable without one."
            )
        else:
            problems.extend(
                validate_image(
                    slide.get("image"),
                    f"{where}.image",
                    root,
                    image_inventory,
                    resolved_images,
                )
            )

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

    elif layout == "title-image":
        problems.extend(text_field(slide.get("subtitle"), f"{where}.subtitle", required=False))
        problems.extend(text_field(slide.get("meta"), f"{where}.meta", required=False))

    elif layout == "image-content":
        problems.extend(validate_bullets(slide.get("bullets"), f"{where}.bullets"))
        problems.extend(text_field(slide.get("caption"), f"{where}.caption", required=False))

    elif layout == "image-full":
        problems.extend(text_field(slide.get("caption"), f"{where}.caption", required=False))

    elif layout == "chart":
        has_data = slide.get("chart") is not None
        has_picture = slide.get("image") is not None
        if has_data and has_picture:
            problems.append(
                f"{where}: a chart slide carries either 'chart' (data this deck plots) "
                "or 'image' (a picture of the chart), never both. Drop one."
            )
        elif has_data:
            problems.extend(validate_chart(slide.get("chart"), f"{where}.chart"))
        elif has_picture:
            problems.extend(
                validate_image(
                    slide.get("image"),
                    f"{where}.image",
                    root,
                    image_inventory,
                    resolved_images,
                )
            )
        else:
            problems.append(
                f"{where}.chart: required for a chart slide — "
                "{'kind': 'bar'|'line'|'pie', 'categories': [...], 'series': "
                "[{'name': ..., 'values': [...]}]}. Pass 'image' instead when the "
                "figure is already a picture; a native chart is preferred, because it "
                "stays editable in the delivered file."
            )
        problems.extend(text_field(slide.get("note"), f"{where}.note", required=False))

    return problems


def validate_spec(
    spec: object,
    root: Path | None = None,
    resolved_images: dict[str, ValidatedImage] | None = None,
) -> list[str]:
    """One `<field>: <reason>` string per problem; empty means the spec is usable.

    `root` is the directory the spec file itself sits in. Every picture the spec
    names is resolved inside it and opened; without it the field checks still
    run and the file checks are skipped.
    """
    if not isinstance(spec, dict):
        return ["spec: top level must be a JSON object"]

    problems = validate_no_placeholders(spec)
    problems.extend(text_field(spec.get("title"), "title", required=True))
    for optional in ("subtitle", "author", "font"):
        problems.extend(text_field(spec.get(optional), optional, required=False))
    if isinstance(spec.get("font"), str):
        font = spec["font"].strip()
        if len(font) > 128 or any(ord(character) < 32 for character in font):
            problems.append("font: must be a safe typeface name of at most 128 characters")

    theme = spec.get("theme", "light")
    if theme not in THEMES:
        problems.append(f'theme: must be "light" or "dark", found {theme!r}')

    aspect = spec.get("aspect", "16:9")
    if aspect not in ASPECTS:
        problems.append(f'aspect: must be "16:9" or "4:3", found {aspect!r}')

    palette = spec.get("palette")
    if (
        isinstance(spec.get("font"), str)
        and spec["font"].strip() != DEFAULT_FONT
        and palette is None
    ):
        problems.append(
            "palette: required for a non-Chainabit font override; supply every palette role"
        )
    if palette is not None:
        if not isinstance(palette, dict):
            problems.append("palette: must be an object with every palette role")
        else:
            missing = [key for key in PALETTE_KEYS if key not in palette]
            unknown = sorted(set(palette) - set(PALETTE_KEYS))
            if missing:
                problems.append("palette: must include every role: " + ", ".join(missing))
            if unknown:
                problems.append("palette: contains unsupported role(s): " + ", ".join(unknown))
            for key in PALETTE_KEYS:
                value = palette.get(key)
                if not isinstance(value, str) or not HEX_COLOUR.fullmatch(value):
                    problems.append(f"palette.{key}: must be a #RRGGBB colour")

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

    image_inventory: dict[tuple[int, int], ValidatedImage] = {}
    image_assets = resolved_images if resolved_images is not None else {}
    for index, slide in enumerate(slides):
        problems.extend(
            validate_slide(
                slide,
                f"slides[{index}]",
                root,
                image_inventory,
                image_assets,
            )
        )

    if len(image_inventory) > MAX_DISTINCT_IMAGES:
        problems.append(
            f"images: {len(image_inventory)} distinct local files, over the "
            f"{MAX_DISTINCT_IMAGES}-image cap"
        )
    total_image_bytes = sum(len(asset.data) for asset in image_inventory.values())
    if total_image_bytes > MAX_TOTAL_IMAGE_BYTES:
        problems.append(
            f"images: {total_image_bytes} bytes across distinct local files, over the "
            f"{MAX_TOTAL_IMAGE_BYTES}-byte aggregate image limit"
        )

    return problems


def resolve_theme(spec: dict) -> dict[str, str]:
    """Return the complete custom palette or the selected Chainabit default."""
    palette = spec.get("palette")
    if isinstance(palette, dict):
        return {key: str(palette[key]).lstrip("#").upper() for key in PALETTE_KEYS}
    return THEMES[spec.get("theme", "light")]


def palette_mode(theme: dict[str, str]) -> str:
    """"light" or "dark", read off the palette rather than the spec's theme key.

    A custom palette is a complete override, so it may be dark without the spec
    saying "dark". Series colours are chosen against what is actually behind
    them, which is the background this palette declares.
    """
    red, green, blue = (int(theme["background"][index:index + 2], 16) for index in (0, 2, 4))
    return "dark" if (0.299 * red + 0.587 * green + 0.114 * blue) < 128 else "light"


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

    elif layout == "title-image":
        choose("title", [slide["title"]], geometry["hero_title"], COVER_TITLE_SIZES, 0.12,
               "title", "The picture takes the right of the cover, so the title has "
               "less room than on a plain cover — shorten it.")
        if slide.get("subtitle"):
            choose("subtitle", [slide["subtitle"]], geometry["hero_subtitle"],
                   SUBTITLE_SIZES, 0.12, "subtitle", "Shorten it.")
        if slide.get("meta"):
            choose("meta", [slide["meta"]], geometry["hero_meta"], META_SIZES, 0.12,
                   "meta", "Keep it to one short line.")

    elif layout == "image-content":
        choose("title", [slide["title"]], geometry["heading"], HEADING_SIZES, 0.12,
               "title", "Shorten the slide title.")
        choose("body", list(slide["bullets"]), geometry["split_body"], COLUMN_SIZES, 0.12,
               "bullets", "Half a slide holds half as much — shorten the bullets, or "
               "move the picture to its own slide.")
        if slide.get("caption"):
            choose("caption", [slide["caption"]], geometry["split_caption"], META_SIZES,
                   0.12, "caption", "A caption is one short line under the picture.")

    elif layout == "image-full":
        lines = [slide["title"]]
        if slide.get("caption"):
            lines.append(slide["caption"])
        choose("caption", lines, geometry["full_caption"], CAPTION_SIZES, 0.85,
               "title", "The band across the foot of a full-bleed slide holds two "
               "short lines; move the rest into 'notes'.")

    elif layout == "chart":
        choose("title", [slide["title"]], geometry["heading"], HEADING_SIZES, 0.12,
               "title", "Shorten the slide title.")
        if slide.get("note"):
            choose("note", [slide["note"]], geometry["note"], META_SIZES, 0.12,
                   "note", "A footnote is one short line; anything longer belongs in 'notes'.")

    return sizes, problems


def image_box(box, image_size: tuple[int, int], mode: str) -> tuple[float, float, float, float]:
    """Where a picture is drawn, and how much of it, inside `box`.

    Returns (left, top, width, height) in inches for `contain`; for `cover` the
    rectangle IS the box and the caller crops instead, which is why the crop
    fractions come back from `image_crop`. A picture is never stretched: an
    aspect ratio the caller did not choose is a distortion nobody asked for.
    """
    left, top, width, height = box
    image_ratio = image_size[0] / max(image_size[1], 1)
    box_ratio = width / max(height, 0.01)
    if mode == "cover":
        return (left, top, width, height)
    if image_ratio > box_ratio:
        drawn_height = width / image_ratio
        return (left, top + (height - drawn_height) / 2, width, drawn_height)
    drawn_width = height * image_ratio
    return (left + (width - drawn_width) / 2, top, drawn_width, height)


def image_crop(box, image_size: tuple[int, int]) -> tuple[float, float]:
    """(horizontal, vertical) crop fraction per side to fill `box` exactly."""
    _, _, width, height = box
    image_ratio = image_size[0] / max(image_size[1], 1)
    box_ratio = width / max(height, 0.01)
    if image_ratio > box_ratio:
        return ((1 - box_ratio / image_ratio) / 2, 0.0)
    return (0.0, (1 - image_ratio / box_ratio) / 2)


#: How each picture-bearing layout fits its picture. A hero and a full-bleed
#: picture are set decoration and may lose their edges; a picture in a body
#: slide or on a chart slide may be a diagram, and cropping one silently removes
#: part of the figure, so it is letterboxed instead.
IMAGE_FIT = {
    "title-image": "cover",
    "image-content": "contain",
    "image-full": "cover",
    "chart": "contain",
}


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
    # A hero picture takes the right of the cover, edge to edge on three sides;
    # the cover text keeps the left, one gutter clear of it.
    hero_split = width * 0.45
    hero_text = width - hero_split - margin - gutter
    caption_band = 1.0

    return {
        # title-image: the cover, with the picture carrying the right of it
        "hero_image": (width - hero_split, 0.0, hero_split, height),
        "hero_bar": (margin, 2.45, 1.6, 0.09),
        "hero_title": (margin, 2.75, hero_text, 1.5),
        "hero_subtitle": (margin, 4.35, hero_text, 0.9),
        "hero_meta": (margin, height - 1.2, hero_text, 0.5),
        # image-content: the picture on the left, the bullets on the right,
        # under the same heading band every body slide uses
        "split_image": (margin, 1.8, column, height - 1.8 - caption_band - 0.35),
        "split_caption": (margin, height - caption_band - 0.05, column, 0.55),
        "split_body": (margin + column + gutter, 1.8, column, height - 3.1),
        # image-full: the picture fills the slide; the title sits in a solid band
        # across the foot, so the slide still reads in the outline and its text
        # keeps a background whose contrast can be computed
        "full_image": (0.0, 0.0, width, height),
        "full_caption": (0.0, height - caption_band, width, caption_band),
        # chart: the plot takes the body box, so a chart slide and a bullet
        # slide sit on the same grid
        "plot": (margin, 1.8, content, height - 3.1),
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


def render_title(slide, spec_slide, theme, geometry, font, sizes, _image_assets) -> None:
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


def render_content(slide, spec_slide, theme, geometry, font, sizes, _image_assets) -> None:
    render_heading(slide, spec_slide["title"], theme, geometry, font, sizes["title"])

    box = add_textbox(slide, geometry["body"])
    fill_bullets(box, list(spec_slide["bullets"]), font, sizes["body"], theme["body"])

    if spec_slide.get("note"):
        box = add_textbox(slide, geometry["note"])
        style_run(
            box.text_frame.paragraphs[0], spec_slide["note"], font, sizes["note"],
            theme["muted"],
        )


def render_comparison(slide, spec_slide, theme, geometry, font, sizes, _image_assets) -> None:
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


def place_picture(
    slide,
    reference: dict,
    box,
    mode: str,
    image_assets: dict[str, ValidatedImage],
):
    """Draw one validated picture into `box`, letterboxed or cropped to fill.

    The input owner opened, decoded and bounded the bytes before anything was
    written. Rendering consumes those exact bytes and never reopens a
    client-controlled path. `alt` becomes the shape's descriptive text, which
    is what a screen reader and PowerPoint's accessibility check read.
    """
    asset = image_assets[str(reference["path"])]
    width_px, height_px = asset.width, asset.height
    from pptx.util import Inches

    if mode == "cover":
        left, top, width, height = box
        picture = slide.shapes.add_picture(
            BytesIO(asset.data), Inches(left), Inches(top), Inches(width), Inches(height)
        )
        horizontal, vertical = image_crop(box, (width_px, height_px))
        picture.crop_left = horizontal
        picture.crop_right = horizontal
        picture.crop_top = vertical
        picture.crop_bottom = vertical
    else:
        left, top, width, height = image_box(box, (width_px, height_px), mode)
        picture = slide.shapes.add_picture(
            BytesIO(asset.data), Inches(left), Inches(top), Inches(width), Inches(height)
        )

    element = picture._element._nvXxPr.cNvPr
    element.set("descr", str(reference["alt"]))
    return picture


def render_title_image(slide, spec_slide, theme, geometry, font, sizes, image_assets) -> None:
    place_picture(
        slide, spec_slide["image"], geometry["hero_image"], "cover", image_assets
    )
    add_rule(slide, geometry["hero_bar"], theme["accent"])

    box = add_textbox(slide, geometry["hero_title"])
    style_run(
        box.text_frame.paragraphs[0], spec_slide["title"], font, sizes["title"],
        theme["ink"], bold=True,
    )

    if spec_slide.get("subtitle"):
        box = add_textbox(slide, geometry["hero_subtitle"])
        style_run(
            box.text_frame.paragraphs[0], spec_slide["subtitle"], font,
            sizes["subtitle"], theme["muted"],
        )

    if spec_slide.get("meta"):
        box = add_textbox(slide, geometry["hero_meta"])
        style_run(
            box.text_frame.paragraphs[0], spec_slide["meta"], font, sizes["meta"],
            theme["muted"],
        )


def render_image_content(slide, spec_slide, theme, geometry, font, sizes, image_assets) -> None:
    render_heading(slide, spec_slide["title"], theme, geometry, font, sizes["title"])
    place_picture(
        slide, spec_slide["image"], geometry["split_image"], "contain", image_assets
    )

    box = add_textbox(slide, geometry["split_body"])
    fill_bullets(box, list(spec_slide["bullets"]), font, sizes["body"], theme["body"])

    if spec_slide.get("caption"):
        box = add_textbox(slide, geometry["split_caption"])
        style_run(
            box.text_frame.paragraphs[0], spec_slide["caption"], font, sizes["caption"],
            theme["muted"],
        )


def render_image_full(slide, spec_slide, theme, geometry, font, sizes, image_assets) -> None:
    from pptx.util import Pt

    place_picture(
        slide, spec_slide["image"], geometry["full_image"], "cover", image_assets
    )

    # The band is a solid fill, not a translucent overlay: text set over a
    # picture has no computable contrast, and "check it by eye" is not a floor.
    box = add_textbox(slide, geometry["full_caption"], inset_in=0.85, fill=theme["surface"])
    style_run(
        box.text_frame.paragraphs[0], spec_slide["title"], font, sizes["caption"],
        theme["ink"], bold=True,
    )
    if spec_slide.get("caption"):
        paragraph = box.text_frame.add_paragraph()
        paragraph.space_before = Pt(2)
        style_run(paragraph, spec_slide["caption"], font, sizes["caption"], theme["muted"])


def render_chart(slide, spec_slide, theme, geometry, font, sizes, image_assets) -> None:
    render_heading(slide, spec_slide["title"], theme, geometry, font, sizes["title"])

    if spec_slide.get("chart") is not None:
        add_native_chart(slide, spec_slide["chart"], theme, geometry["plot"], font)
    else:
        place_picture(
            slide, spec_slide["image"], geometry["plot"], "contain", image_assets
        )

    if spec_slide.get("note"):
        box = add_textbox(slide, geometry["note"])
        style_run(
            box.text_frame.paragraphs[0], spec_slide["note"], font, sizes["note"],
            theme["muted"],
        )


def add_native_chart(slide, chart_spec: dict, theme, box, font) -> None:
    """A real chart part, not a picture of one, so the deck still owns the data."""
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    from pptx.util import Inches, Pt

    kinds = {
        "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "line": XL_CHART_TYPE.LINE_MARKERS,
        "pie": XL_CHART_TYPE.PIE,
    }
    kind = str(chart_spec["kind"])
    palette = SERIES_COLOURS[palette_mode(theme)]

    data = CategoryChartData()
    data.categories = [str(category) for category in chart_spec["categories"]]
    for series in chart_spec["series"]:
        data.add_series(str(series["name"]), [float(value) for value in series["values"]])

    left, top, width, height = box
    frame = slide.shapes.add_chart(
        kinds[kind], Inches(left), Inches(top), Inches(width), Inches(height), data
    )
    chart = frame.chart
    chart.font.size = Pt(CHART_TEXT_PT)
    chart.font.name = font
    chart.font.color.rgb = RGBColor.from_string(theme["body"])

    # One series needs no legend; a pie is always labelled by category. Several
    # series do, because nothing else says which colour is which.
    multi = len(chart_spec["series"]) > 1 or kind == "pie"
    chart.has_legend = multi
    if multi:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False

    # Every value is printed. Roughly one man in twelve cannot separate the
    # colours, and a chart nobody can read the numbers off is decoration.
    plot = chart.plots[0]
    plot.has_data_labels = True
    plot.data_labels.font.size = Pt(CHART_TEXT_PT)
    plot.data_labels.font.name = font
    plot.data_labels.font.color.rgb = RGBColor.from_string(theme["body"])

    if kind == "pie":
        for index, point in enumerate(plot.series[0].points):
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = RGBColor.from_string(
                palette[index % len(palette)]
            )
    else:
        for index, series in enumerate(chart.series):
            series.format.fill.solid()
            series.format.fill.fore_color.rgb = RGBColor.from_string(
                palette[index % len(palette)]
            )
            if kind == "line":
                series.format.line.color.rgb = RGBColor.from_string(
                    palette[index % len(palette)]
                )
        if chart_spec.get("unit"):
            chart.value_axis.has_title = True
            chart.value_axis.axis_title.text_frame.text = str(chart_spec["unit"])


def render_closing(slide, spec_slide, theme, geometry, font, sizes, _image_assets) -> None:
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
    "title-image": render_title_image,
    "image-content": render_image_content,
    "image-full": render_image_full,
    "chart": render_chart,
}


def build_deck(
    spec: dict,
    geometry: dict,
    output: str,
    image_assets: dict[str, ValidatedImage],
) -> None:
    from pptx import Presentation
    from pptx.util import Inches

    theme = resolve_theme(spec)
    # The explicit spec value is the user override. Only its absence selects
    # Chainabit's canonical runtime-owned default.
    font = spec.get("font") or DEFAULT_FONT

    presentation = Presentation()
    presentation.slide_width = Inches(geometry["slide"][0])
    presentation.slide_height = Inches(geometry["slide"][1])

    for spec_slide in spec["slides"]:
        slide = new_slide(presentation, theme["background"])
        sizes, _ = plan_slide(spec_slide, geometry)
        RENDERERS[spec_slide["layout"]](
            slide, spec_slide, theme, geometry, font, sizes, image_assets
        )
        if spec_slide.get("notes"):
            slide.notes_slide.notes_text_frame.text = spec_slide["notes"]
            for paragraph in slide.notes_slide.notes_text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.name = font

    core = presentation.core_properties
    core.title = spec["title"]
    core.author = spec.get("author") or ""
    core.subject = spec.get("subtitle") or ""

    presentation.save(output)
    apply_theme_font(output, font)


def apply_theme_font(path: str, font: str) -> None:
    """Apply the selected typeface to the OOXML theme and default text styles.

    Explicit text runs alone are insufficient: new text, bullets, notes, charts,
    and Office-created shapes inherit from the theme. Rewriting only the
    package's XML font declarations keeps the presentation self-contained and
    prevents Calibri/Aptos from resurfacing after the deck is opened.
    """
    namespace = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    directory = os.path.dirname(os.path.abspath(path))
    descriptor, temporary = tempfile.mkstemp(prefix=".chainabit-pptx-", suffix=".pptx", dir=directory)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED
        ) as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename.startswith("ppt/") and info.filename.endswith(".xml"):
                    root = ET.fromstring(data)
                    for element in root.iter():
                        if element.tag == namespace + "cs":
                            element.set("typeface", ARABIC_FALLBACK_FONT)
                        elif element.tag in {
                            namespace + "latin",
                            namespace + "ea",
                            namespace + "buFont",
                        }:
                            element.set("typeface", font)
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                target.writestr(info, data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def preflight_frame(schema: str = EXECUTION_SCHEMA) -> dict:
    """The frame a spec check prints: checked, and nothing produced.

    Every run of a registered generator ends in one machine-readable line, and
    a host reads that line as the run's evidence. A `--validate-only` run writes
    no file, so it cannot report an output identity; without a frame of its own
    it printed nothing a host could read, which looks exactly like a render that
    lost its evidence.
    """
    return {"schema": schema, "ok": True, "operation": "preflight", "valid": True}


def artifact_identity(path: str, slides: int, font: str) -> dict:
    with open(path, "rb") as handle:
        data = handle.read()
    return {
        "schema": EXECUTION_SCHEMA,
        "success": True,
        "generator": "skill-pptx.deck",
        "output": {
            "path": path,
            "shape": "file",
            "sha256": hashlib.sha256(data).hexdigest(),
            "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "bytes": len(data),
            "slides": slides,
        },
        "typography": {"family": font, "source": "user" if font != DEFAULT_FONT else "default"},
    }


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

    # A picture is named relative to the spec, and must resolve inside the
    # spec's own directory. That is the same containment the pdf capability
    # applies, so one file is either safe to embed in both or in neither.
    root = Path(os.path.abspath(args.spec)).parent
    image_assets: dict[str, ValidatedImage] = {}
    problems.extend(validate_spec(spec, root, image_assets))

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
        print(json.dumps(preflight_frame(), sort_keys=True))
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
        return 2

    try:
        build_deck(spec, geometry, args.output, image_assets)
    except PermissionError:
        print(f"ERROR: output: no permission to write {args.output}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: output: could not write {args.output}: {exc}", file=sys.stderr)
        return 2

    size = os.path.getsize(args.output)
    print(f"OK: wrote {args.output} ({size} bytes, {len(spec['slides'])} slide(s))")
    print(validation_handoff(args.output))
    print(json.dumps(artifact_identity(args.output, len(spec["slides"]), spec.get("font") or DEFAULT_FONT), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
