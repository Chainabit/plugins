#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate a static website from a JSON spec, or from one of three starting templates.

A "build me a site" request has no natural exit point. There is no file format to
satisfy, no renderer to fail, and no convention the sandbox enforces — so the shape
of the output is whatever the model improvised that run, and the next run improvises
a different one. This script removes the improvisation from everything except the
words: layout, palette, type scale, breakpoints, link structure and file naming come
from here, and the spec carries only content.

The output is deliberately plain: HTML and one stylesheet, no build step, no
JavaScript, no remote asset of any kind. That is not minimalism for its own sake —
the sandbox has no Node.js toolchain and no network egress, so a bundler could not
run and a CDN link could not resolve. See SKILL.md for the scope this pins.

`index.html` is always written at the top of the output directory. A promoted
website artifact is served by its entry point, and an entry point one level down
publishes to a preview that resolves to nothing.

Deliberately stdlib-only. Jinja2 is not installed in the sandbox image.

Usage:
    python3 scaffold_site.py --template portfolio ./site
    python3 scaffold_site.py --template portfolio --print-spec > spec.json
    python3 scaffold_site.py --spec spec.json ./site
    python3 scaffold_site.py --spec spec.json --validate-only
"""

from __future__ import annotations

import argparse
import datetime
import errno
import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import stat
import sys

HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Primary BCP-47 language subtags whose customary writing system is
# right-to-left, matching the set major browser/OS platforms agree on. A
# language whose script varies by region or era (Kurdish, Hausa) is
# deliberately left out rather than guessed at from a two-letter tag alone.
RTL_LANGUAGE_PREFIXES = frozenset({"ar", "arc", "dv", "fa", "he", "prs", "ps", "sd", "ug", "ur", "yi"})


def resolve_direction(lang: str) -> str:
    """The site's writing direction, from the one language tag this
    generator already declares (`site.lang`) -- not a second, independent
    field. `site.lang` is free-form but BCP-47-shaped ("ar", "ar-EG",
    "he-IL"); only the primary subtag decides direction, so a region or
    script suffix cannot flip it by accident.
    """
    primary = lang.strip().lower().split("-", 1)[0]
    return "rtl" if primary in RTL_LANGUAGE_PREFIXES else "ltr"

# These are renderer-local projections of skill-brand-defaults' profile. They
# stay local because independently materialised skill bundles cannot import one
# another at runtime. The contract test pins them to the profile so this Protected
# Variation cannot drift while each renderer remains independently deployable.
LIGHT_BACKGROUND = "#FFFFFF"
DARK_BACKGROUND = "#010102"
DEFAULT_ACCENT = "#327B61"
DEFAULT_ACCENT_DARK = "#70BD9E"
PALETTE_KEYS = ("background", "surface", "ink", "body", "muted", "rule", "accent", "accentInk")
DEFAULT_PALETTES = {
    "light": {
        "background": "#FFFFFF", "surface": "#F9FAFB", "ink": "#101828",
        "body": "#364153", "muted": "#6A7282", "rule": "#E5E7EB",
        "accent": DEFAULT_ACCENT, "accentInk": "#FFFFFF",
    },
    "dark": {
        "background": DARK_BACKGROUND, "surface": "#18181B", "ink": "#D7D7DA",
        "body": "#A4A4A9", "muted": "#85858D", "rule": "#1F1F22",
        "accent": DEFAULT_ACCENT_DARK, "accentInk": "#0B2118",
    },
}

# WCAG AA for normal-size text. Applied to the accent even though most accent text
# is large, because the accent also sets link colour inside body copy.
CONTRAST_FLOOR = 4.5

THEMES = ("auto", "light", "dark")
SECTION_TYPES = ("hero", "prose", "features", "cards", "list", "contact")

EXECUTION_SCHEMA = "chainabit.website.execution/v1"
DEFAULT_FONT_FAMILY = os.environ.get(
    "CHAINABIT_ARTIFACT_FONT_FAMILY", "IBM Plex Sans"
).strip() or "IBM Plex Sans"
DEFAULT_FONT_DIR = os.environ.get(
    "CHAINABIT_ARTIFACT_FONT_DIR", "/opt/chainabit/artifact-fonts/ibm-plex-sans"
)
FONT_FILES = {
    "IBMPlexSans-Regular.woff2": "400",
    "IBMPlexSans-SemiBold.woff2": "600",
}
FALLBACK_FONT_FILES = {
    "IBMPlexSansArabic-Regular.woff2": "400",
    "IBMPlexSansArabic-SemiBold.woff2": "600",
}
SAFE_FONT_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")
AVAILABLE_WEB_FAMILIES = {"IBM Plex Sans", "IBM Plex Sans Arabic"}

MAX_PAGES = 12
MAX_SECTIONS = 12

# A picture is a local file already sitting beside the spec, never a URL: the
# sandbox that renders a site has no network egress (see the module
# docstring), so there is nothing here to fetch. SVG is left out even though
# it is an image format, because it can carry a <script> and the site's own
# contract (.chainabit-site.json runtime.javascript) promises none.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# The product accepts at most 64 MiB for a published artifact tree. Reserve
# 4 MiB for bounded HTML, CSS, the contract file, and the runtime-provided font
# set; image inputs cannot consume the entire publication envelope themselves.
MAX_TOTAL_IMAGE_BYTES = 60 * 1024 * 1024
# Two fully populated 12-page specs can still carry a broad visual set without
# allowing the structural maximum (more than a thousand paths) to become a
# filesystem/memory amplification vector.
MAX_DISTINCT_IMAGES = 128


# --- contrast ---------------------------------------------------------------------


def relative_luminance(colour: str) -> float:
    channels = [int(colour[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    first, second = relative_luminance(foreground), relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


# --- spec validation --------------------------------------------------------------


class SpecErrors:
    """Collects every problem in one pass, addressed by its path in the spec.

    Reporting one error at a time turns a malformed spec into a sequence of runs,
    and a model correcting one field per run tends to introduce the next one. One
    list, one fix, one re-run.
    """

    def __init__(self) -> None:
        self.messages: list[str] = []

    def add(self, path: str, message: str) -> None:
        self.messages.append(f"ERROR: {path}: {message}")

    def __bool__(self) -> bool:
        return bool(self.messages)


def require_text(value: object, path: str, errors: SpecErrors, limit: int = 300) -> str:
    if not isinstance(value, str) or not value.strip():
        errors.add(path, "required, must be a non-empty string")
        return ""
    if len(value) > limit:
        errors.add(path, f"{len(value)} characters, the limit is {limit}")
    return value.strip()


def optional_text(value: object, path: str, errors: SpecErrors, limit: int = 600) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        errors.add(path, "must be a string")
        return ""
    if len(value) > limit:
        errors.add(path, f"{len(value)} characters, the limit is {limit}")
    return value.strip()


def check_colour(value: object, path: str, background: str, errors: SpecErrors, fallback: str) -> str:
    if value is None:
        return fallback
    if not isinstance(value, str) or not HEX_COLOUR.match(value):
        errors.add(path, f"must be a hex colour like {fallback!r}, found {value!r}")
        return fallback

    colour = value.upper()
    ratio = contrast_ratio(colour, background)
    if ratio < CONTRAST_FLOOR:
        errors.add(
            path,
            f"{colour} on {background} is {ratio:.2f}:1, under the {CONTRAST_FLOOR}:1 "
            "floor. It carries link text inside body copy, so it has to clear AA for "
            "normal text — pick a darker (or, on dark, a lighter) value. "
            "references/design.md lists checked pairs.",
        )
    return colour


def check_palette(value: object, path: str, errors: SpecErrors) -> dict[str, str] | None:
    """Validate a complete custom palette without inheriting Chainabit roles.

    A partial palette is ambiguous: filling its gaps with the platform palette
    would quietly co-brand an explicitly branded artifact. The generator keeps
    the resolved palette local and only renders it when every role is present.
    """
    if not isinstance(value, dict):
        errors.add(path, "must be an object with every palette role")
        return None
    unknown = sorted(set(value) - set(PALETTE_KEYS))
    missing = [key for key in PALETTE_KEYS if key not in value]
    if unknown:
        errors.add(path, "contains unsupported role(s): " + ", ".join(unknown))
    if missing:
        errors.add(path, "must include every role: " + ", ".join(missing))
    palette: dict[str, str] = {}
    for key in PALETTE_KEYS:
        raw = value.get(key)
        if not isinstance(raw, str) or not HEX_COLOUR.match(raw):
            errors.add(f"{path}.{key}", "must be a #RRGGBB colour")
            continue
        palette[key] = raw.upper()
    if len(palette) != len(PALETTE_KEYS):
        return None
    for key in ("ink", "body", "muted", "accent"):
        ratio = contrast_ratio(palette[key], palette["background"])
        if ratio < CONTRAST_FLOOR:
            errors.add(
                f"{path}.{key}",
                f"is {ratio:.2f}:1 on {palette['background']}, under the {CONTRAST_FLOOR}:1 floor",
            )
    accent_ratio = contrast_ratio(palette["accentInk"], palette["accent"])
    if accent_ratio < CONTRAST_FLOOR:
        errors.add(
            f"{path}.accentInk",
            f"is {accent_ratio:.2f}:1 on {palette['accent']}, under the {CONTRAST_FLOOR}:1 floor",
        )
    return palette


def check_page_path(value: object, path: str, errors: SpecErrors) -> str:
    if not isinstance(value, str) or not value.strip():
        errors.add(path, "required, must be a relative path ending in .html")
        return ""

    candidate = value.strip()
    if candidate.startswith("/"):
        errors.add(
            path,
            f"{candidate!r} is absolute. Page paths are relative to the site root, "
            "because a promoted site is served under a version prefix rather than at /.",
        )
        return ""
    if ".." in candidate.split("/"):
        errors.add(path, f"{candidate!r} escapes the site root")
        return ""
    if not candidate.endswith(".html"):
        errors.add(path, f"{candidate!r} must end in .html")
        return ""
    if candidate != posixpath.normpath(candidate):
        errors.add(path, f"{candidate!r} is not a normalised path")
        return ""
    return candidate


def check_link(item: object, path: str, errors: SpecErrors) -> dict[str, str]:
    if not isinstance(item, dict):
        errors.add(path, "must be an object with 'label' and 'href'")
        return {"label": "", "href": ""}
    return {
        "label": require_text(item.get("label"), f"{path}.label", errors, limit=60),
        "href": require_text(item.get("href"), f"{path}.href", errors),
    }


def open_local_image(source_root: str, src: str) -> tuple[int, os.stat_result]:
    """Open a regular image beneath ``source_root`` without following links.

    A prior ``realpath``/``islink`` check is not an access boundary: a client
    can swap a checked component before the later ``open``. Directory file
    descriptors pin every component while ``O_NOFOLLOW`` rejects a link at the
    instant it is opened. The caller owns the returned descriptor.
    """
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required_flags):
        raise RuntimeError("this platform cannot securely open local image paths")

    components = src.split("/")
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

    directory_fd = os.open(source_root, directory_flags)
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


def inspect_local_image(source_root: str, src: str) -> tuple[tuple[int, int], int]:
    file_fd, metadata = open_local_image(source_root, src)
    os.close(file_fd)
    return (metadata.st_dev, metadata.st_ino), metadata.st_size


def read_open_image(file_fd: int) -> bytes:
    """Read one already-pinned descriptor within the per-file ceiling."""
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
    return data


def check_image(
    value: object, path: str, errors: SpecErrors, source_root: str | None
) -> dict | None:
    """A locally staged image, never a URL.

    The sandbox that renders a site has no network egress, so an image
    reference has to already be a file the caller copied in before this ran —
    the same rule skill-pptx enforces for a picture on a slide. Checking the
    file itself here, not only the shape of the reference, turns a typo'd
    path into one error message instead of a site that builds clean and
    404s its own hero image.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.add(path, "must be an object with 'src' and 'alt'")
        return None

    src = value.get("src")
    if not isinstance(src, str) or not src.strip():
        errors.add(f"{path}.src", "required, must be a relative path to a local file")
        return None
    src = src.strip()
    if "://" in src or src.startswith("data:"):
        errors.add(
            f"{path}.src",
            f"{src!r} is a URL or data URI. The sandbox has no network, so an "
            "image has to already be a file here — copy it in first and name "
            "the path the copy landed at.",
        )
        return None
    if src.startswith("/"):
        errors.add(f"{path}.src", f"{src!r} is absolute; give a path relative to the spec")
        return None
    if ".." in src.split("/"):
        errors.add(f"{path}.src", f"{src!r} escapes the directory the spec is in")
        return None

    extension = os.path.splitext(src)[1].lower()
    if extension not in IMAGE_EXTENSIONS:
        errors.add(
            f"{path}.src",
            f"{src!r} has an unsupported extension; use one of "
            f"{', '.join(sorted(IMAGE_EXTENSIONS))}",
        )
        return None

    alt = value.get("alt")
    if not isinstance(alt, str) or not alt.strip():
        errors.add(
            f"{path}.alt",
            "required, must describe the image for a screen-reader user",
        )
        return None
    if len(alt) > 200:
        errors.add(f"{path}.alt", f"{len(alt)} characters, the limit is 200")

    # Existence and size are only checkable once the caller told us where the
    # spec lives (main() passes this for --spec, not for a built-in
    # --template, which never declares an image). --print-spec on a bare
    # template still validates the shape above with no filesystem check.
    if source_root is not None:
        try:
            _, size = inspect_local_image(source_root, src)
        except FileNotFoundError:
            errors.add(f"{path}.src", f"{src!r} does not exist next to the spec")
            return None
        except (OSError, ValueError) as exc:
            errors.add(f"{path}.src", f"{src!r} {exc}")
            return None
        if size > MAX_IMAGE_BYTES:
            errors.add(
                f"{path}.src",
                f"{src!r} is {size} bytes, the limit is {MAX_IMAGE_BYTES}",
            )
            return None

    return {"src": src, "alt": alt.strip()}


def check_section(section: object, path: str, errors: SpecErrors, source_root: str | None) -> dict:
    if not isinstance(section, dict):
        errors.add(path, "must be an object")
        return {}

    kind = section.get("type")
    if kind not in SECTION_TYPES:
        errors.add(
            path + ".type",
            f"must be one of {', '.join(SECTION_TYPES)}, found {kind!r}",
        )
        return {}

    checked: dict = {"type": kind}

    # An id is what makes a same-page link land somewhere. Without one, `#features`
    # in a hero action is a link to the top of the document that looks like it works.
    section_id = section.get("id")
    if section_id is not None:
        if not isinstance(section_id, str) or not SLUG.match(section_id):
            errors.add(
                f"{path}.id",
                f"must be lowercase alphanumerics joined by single hyphens, found {section_id!r}",
            )
            section_id = None
    checked["id"] = section_id or ""

    if kind == "hero":
        checked["heading"] = require_text(section.get("heading"), f"{path}.heading", errors, 120)
        checked["text"] = optional_text(section.get("text"), f"{path}.text", errors)
        checked["image"] = check_image(section.get("image"), f"{path}.image", errors, source_root)
        actions = section.get("actions") or []
        if not isinstance(actions, list):
            errors.add(f"{path}.actions", "must be a list of {label, href} objects")
            actions = []
        if len(actions) > 2:
            errors.add(
                f"{path}.actions",
                f"{len(actions)} actions. A hero has one job; two links at most, and the "
                "second is the quiet one.",
            )
        checked["actions"] = [
            check_link(action, f"{path}.actions[{index}]", errors)
            for index, action in enumerate(actions)
        ]

    elif kind == "prose":
        checked["heading"] = optional_text(section.get("heading"), f"{path}.heading", errors, 120)
        paragraphs = section.get("paragraphs")
        if not isinstance(paragraphs, list) or not paragraphs:
            errors.add(f"{path}.paragraphs", "required, must be a list of at least one string")
            paragraphs = []
        checked["paragraphs"] = [
            require_text(text, f"{path}.paragraphs[{index}]", errors, limit=1200)
            for index, text in enumerate(paragraphs)
        ]

    elif kind in ("features", "cards", "list"):
        checked["heading"] = optional_text(section.get("heading"), f"{path}.heading", errors, 120)
        items = section.get("items")
        if not isinstance(items, list) or not items:
            errors.add(f"{path}.items", "required, must be a list of at least one item")
            items = []
        if len(items) > 9:
            errors.add(f"{path}.items", f"{len(items)} items, the limit is 9")
        collected = []
        for index, item in enumerate(items):
            item_path = f"{path}.items[{index}]"
            if not isinstance(item, dict):
                errors.add(item_path, "must be an object")
                continue
            collected.append(
                {
                    "title": require_text(item.get("title"), f"{item_path}.title", errors, 120),
                    "text": optional_text(item.get("text"), f"{item_path}.text", errors),
                    "meta": optional_text(item.get("meta"), f"{item_path}.meta", errors, 80),
                    "href": optional_text(item.get("href"), f"{item_path}.href", errors),
                    # Only features/cards render an item image; a list item is a
                    # denser row and stays text-only by design (see render_section).
                    "image": (
                        check_image(item.get("image"), f"{item_path}.image", errors, source_root)
                        if kind in ("features", "cards")
                        else None
                    ),
                }
            )
        checked["items"] = collected

    elif kind == "contact":
        checked["heading"] = optional_text(section.get("heading"), f"{path}.heading", errors, 120)
        checked["text"] = optional_text(section.get("text"), f"{path}.text", errors)
        links = section.get("links")
        if not isinstance(links, list) or not links:
            errors.add(f"{path}.links", "required, must be a list of at least one {label, href}")
            links = []
        checked["links"] = [
            check_link(link, f"{path}.links[{index}]", errors)
            for index, link in enumerate(links)
        ]

    return checked


def _declared_images(spec: dict) -> list[dict]:
    """Every image dict in the spec, hero and item images alike, in one list."""
    found: list[dict] = []
    for page in spec["pages"]:
        for section in page["sections"]:
            if section.get("image"):
                found.append(section["image"])
            for item in section.get("items", []):
                if item.get("image"):
                    found.append(item["image"])
    return found


def validate_spec(spec: object, source_root: str | None = None) -> tuple[dict, SpecErrors]:
    errors = SpecErrors()
    if not isinstance(spec, dict):
        errors.add("spec", "must be a JSON object")
        return {}, errors

    site = spec.get("site")
    if not isinstance(site, dict):
        errors.add("site", "required, must be an object")
        site = {}

    theme = site.get("theme", "auto")
    if theme not in THEMES:
        errors.add("site.theme", f"must be one of {', '.join(THEMES)}, found {theme!r}")
        theme = "auto"

    requested_font = site.get("font")
    if requested_font is None:
        font = DEFAULT_FONT_FAMILY
        font_source = "chainabit_default"
    elif not isinstance(requested_font, str) or not SAFE_FONT_NAME.fullmatch(requested_font.strip()):
        errors.add("site.font", "must be a non-empty font family without control characters")
        font = DEFAULT_FONT_FAMILY
        font_source = "chainabit_default"
    else:
        font = requested_font.strip()
        font_source = "user_override"

    requested_palette = site.get("palette")
    if (
        font_source == "user_override"
        and font not in AVAILABLE_WEB_FAMILIES
        and requested_palette is None
    ):
        errors.add(
            "site.palette",
            "is required for a non-Chainabit font override; supply complete active palettes",
        )
    active_modes = ("light", "dark") if theme == "auto" else (theme,)
    palettes = {mode: dict(DEFAULT_PALETTES[mode]) for mode in THEMES if mode != "auto"} if requested_palette is None else {}
    palette_source = "chainabit_default"
    if requested_palette is not None:
        if not isinstance(requested_palette, dict):
            errors.add("site.palette", "must be an object with light and/or dark palettes")
        else:
            unsupported_modes = sorted(set(requested_palette) - {"light", "dark"})
            if unsupported_modes:
                errors.add("site.palette", "contains unsupported mode(s): " + ", ".join(unsupported_modes))
            for mode in active_modes:
                if mode not in requested_palette:
                    errors.add(f"site.palette.{mode}", "is required for the selected site.theme")
                    continue
                checked = check_palette(requested_palette[mode], f"site.palette.{mode}", errors)
                if checked is not None:
                    palettes[mode] = checked
            palette_source = "user_override"

    has_legacy_accent = site.get("accent") is not None or site.get("accentDark") is not None
    if requested_palette is not None and has_legacy_accent:
        errors.add("site", "must not combine palette with accent/accentDark; palette is the complete override")

    checked_site = {
        "title": require_text(site.get("title"), "site.title", errors, 80),
        "tagline": optional_text(site.get("tagline"), "site.tagline", errors, 200),
        "description": optional_text(site.get("description"), "site.description", errors, 300),
        "lang": (checked_lang := optional_text(site.get("lang"), "site.lang", errors, 12) or "en"),
        # Direction is a layout property derived from the one language tag
        # already declared above, not a separate field the caller must also
        # set. See resolve_direction's docstring for why the primary subtag
        # decides and a region/script suffix does not.
        "dir": resolve_direction(checked_lang),
        # The skip-link and nav landmark are platform-authored accessibility
        # chrome, not user content -- but they are read aloud to a
        # screen-reader user on every page, so a site declared in Arabic or
        # Turkish should not carry English boilerplate the model never wrote
        # and cannot see to fix. Rather than this generator guessing a
        # translation from `lang` (which does not scale past a handful of
        # hardcoded languages and drifts from whatever the model actually
        # said), the caller -- which already knows the requested language --
        # may supply the label; English remains the default so an
        # unspecified-language site is unaffected.
        "skipLinkLabel": optional_text(site.get("skipLinkLabel"), "site.skipLinkLabel", errors, 60) or "Skip to content",
        "navLabel": optional_text(site.get("navLabel"), "site.navLabel", errors, 60) or "Main",
        "theme": theme,
        "font": font,
        "fontSource": font_source,
        "footer": optional_text(site.get("footer"), "site.footer", errors, 300),
        "palettes": palettes,
        "paletteSource": "artifact_specific" if has_legacy_accent and requested_palette is None else palette_source,
        # Retained for compatibility with existing content specs. A complete
        # palette is the non-co-branding override; this narrower option changes
        # only the accent role and leaves the default visual system intact.
        "accent": check_colour(
            site.get("accent"), "site.accent", LIGHT_BACKGROUND, errors, DEFAULT_ACCENT
        ),
        "accentDark": check_colour(
            site.get("accentDark"), "site.accentDark", DARK_BACKGROUND, errors, DEFAULT_ACCENT_DARK
        ),
    }
    if requested_palette is None:
        checked_site["palettes"]["light"]["accent"] = checked_site["accent"]
        checked_site["palettes"]["dark"]["accent"] = checked_site["accentDark"]

    pages = spec.get("pages")
    if not isinstance(pages, list) or not pages:
        errors.add("pages", "required, must be a list of at least one page")
        return {"site": checked_site, "pages": []}, errors
    if len(pages) > MAX_PAGES:
        errors.add("pages", f"{len(pages)} pages, the limit is {MAX_PAGES}")

    checked_pages = []
    seen: set[str] = set()

    for index, page in enumerate(pages):
        page_path = f"pages[{index}]"
        if not isinstance(page, dict):
            errors.add(page_path, "must be an object")
            continue

        relative = check_page_path(page.get("path"), f"{page_path}.path", errors)
        if relative and relative in seen:
            errors.add(f"{page_path}.path", f"{relative!r} is declared twice")
        seen.add(relative)

        sections = page.get("sections")
        if not isinstance(sections, list) or not sections:
            errors.add(f"{page_path}.sections", "required, must be a list of at least one section")
            sections = []
        if len(sections) > MAX_SECTIONS:
            errors.add(f"{page_path}.sections", f"{len(sections)} sections, the limit is {MAX_SECTIONS}")

        # The hero is what renders the page's <h1>, so requiring one first is how
        # every page ends up with exactly one top-level heading. Leaving it optional
        # produced pages whose first heading was an <h2> — valid HTML that gives a
        # screen-reader user no title to land on and no outline to skim.
        kinds = [
            section.get("type") for section in sections if isinstance(section, dict)
        ]
        if kinds and kinds[0] != "hero":
            errors.add(
                f"{page_path}.sections[0]",
                f"must be a 'hero'; found {kinds[0]!r}. The hero renders the page's "
                "single <h1>, and a page without one has no heading to navigate by.",
            )
        if kinds.count("hero") > 1:
            errors.add(
                f"{page_path}.sections",
                f"{kinds.count('hero')} hero sections. One page, one <h1>.",
            )

        checked_pages.append(
            {
                "path": relative,
                "title": require_text(page.get("title"), f"{page_path}.title", errors, 80),
                "nav": optional_text(page.get("nav"), f"{page_path}.nav", errors, 40),
                "description": optional_text(
                    page.get("description"), f"{page_path}.description", errors, 300
                ),
                "sections": [
                    check_section(section, f"{page_path}.sections[{position}]", errors, source_root)
                    for position, section in enumerate(sections)
                ],
            }
        )

    # The rule the promotion endpoint enforces on the far side. Catching it here
    # costs one validation run; catching it there costs a whole generate-and-promote
    # cycle and surfaces as a published artifact nobody can open.
    if "index.html" not in seen:
        nested = sorted(path for path in seen if path.endswith("index.html"))
        hint = (
            f" The closest thing here is {nested[0]!r}, which is one level down."
            if nested
            else ""
        )
        errors.add(
            "pages",
            "no page at 'index.html'. A website is served by its entry point, and the "
            "entry point has to sit at the top of what is promoted." + hint,
        )

    # Every internal href has to name a page that exists, and every fragment has to
    # name a section that exists. A relative link to a page renamed one edit ago is
    # the defect that survives every check a generator can make on its own output:
    # the file it writes is perfectly valid HTML pointing at nothing.
    anchors = {
        page["path"]: {section["id"] for section in page["sections"] if section.get("id")}
        for page in checked_pages
    }

    for index, page in enumerate(checked_pages):
        base = posixpath.dirname(page["path"])
        for position, section in enumerate(page["sections"]):
            where = f"pages[{index}].sections[{position}]"
            hrefs = [
                link.get("href", "")
                for link in section.get("actions", []) + section.get("links", [])
            ]
            hrefs += [item.get("href", "") for item in section.get("items", [])]

            for href in hrefs:
                if not href or href.startswith(("mailto:", "tel:", "//")) or "://" in href:
                    continue

                target, _, fragment = href.partition("#")
                if not target:
                    # A same-page link. It lands at the top of the document unless
                    # something on this page carries the id, which is the silent
                    # version of a broken link: it scrolls, so it looks like it worked.
                    if fragment and fragment not in anchors[page["path"]]:
                        errors.add(
                            f"{where}.href",
                            f"{href!r} points at a section id that no section on this "
                            f'page declares. Add "id": "{fragment}" to the section it '
                            "should reach.",
                        )
                    continue

                resolved = posixpath.normpath(posixpath.join(base, target.split("?", 1)[0]))
                if resolved not in seen:
                    errors.add(
                        f"{where}.href",
                        f"{href!r} resolves to {resolved!r}, which is not one of the "
                        "declared pages",
                    )
                elif fragment and fragment not in anchors.get(resolved, set()):
                    errors.add(
                        f"{where}.href",
                        f"{href!r} points at {resolved!r}, which declares no section "
                        f"with id {fragment!r}",
                    )

    checked_spec = {"site": checked_site, "pages": checked_pages}
    if source_root is not None:
        canonical_sources: dict[tuple[int, int], int] = {}
        for image in _declared_images(checked_spec):
            try:
                identity, size = inspect_local_image(source_root, image["src"])
                canonical_sources.setdefault(identity, size)
            except (KeyError, OSError, ValueError):
                # The field-level boundary already emitted the actionable path
                # problem. Aggregate diagnostics only operate on valid files.
                continue
        if len(canonical_sources) > MAX_DISTINCT_IMAGES:
            errors.add(
                "images",
                f"{len(canonical_sources)} distinct local files, the limit is "
                f"{MAX_DISTINCT_IMAGES}",
            )
        total_image_bytes = sum(canonical_sources.values())
        if total_image_bytes > MAX_TOTAL_IMAGE_BYTES:
            errors.add(
                "images",
                f"{total_image_bytes} bytes across distinct local files, the limit is "
                f"{MAX_TOTAL_IMAGE_BYTES}",
            )

    return checked_spec, errors


# --- rendering --------------------------------------------------------------------


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def relative_prefix(page_path: str) -> str:
    """`../` repeated to climb from a page back to the site root."""
    depth = page_path.count("/")
    return "../" * depth


def render_actions(actions: list[dict], primary_class: str = "button") -> str:
    if not actions:
        return ""
    parts = []
    for position, action in enumerate(actions):
        variant = primary_class if position == 0 else f"{primary_class} {primary_class}--quiet"
        parts.append(
            f'        <a class="{variant}" href="{escape(action["href"])}">'
            f'{escape(action["label"])}</a>'
        )
    return '      <p class="actions">\n' + "\n".join(parts) + "\n      </p>\n"


def open_section(class_name: str, section: dict) -> str:
    identifier = f' id="{escape(section["id"])}"' if section.get("id") else ""
    return f'    <section{identifier} class="{class_name}">\n'


def render_image(image: dict | None, prefix: str, css_class: str, loading: str = "lazy") -> str:
    """An `<img>` for a resolved image reference, or '' if the section has none.

    `resolvedSrc` is written by `resolve_image_assets`/`write_site` before any
    page renders — it is the content-addressed `assets/images/...` path the
    file was actually copied to, never the spec-relative `src` a model wrote,
    which write_site never promises to preserve.
    """
    if not image:
        return ""
    src = escape(prefix + image["resolvedSrc"])
    alt = escape(image["alt"])
    return f'      <p class="{css_class}"><img src="{src}" alt="{alt}" loading="{loading}"></p>\n'


def render_section(section: dict, prefix: str = "") -> str:
    kind = section.get("type")

    if kind == "hero":
        body = f'      <h1>{escape(section["heading"])}</h1>\n'
        if section.get("text"):
            body += f'      <p class="lede">{escape(section["text"])}</p>\n'
        body += render_actions(section.get("actions", []))
        # Eager, not lazy: the hero image is above the fold on every page that
        # has one, so deferring its load only delays the largest paint.
        body += render_image(section.get("image"), prefix, "hero-media", loading="eager")
        return open_section("hero", section) + body + "    </section>\n"

    heading = ""
    if section.get("heading"):
        heading = f'      <h2>{escape(section["heading"])}</h2>\n'

    if kind == "prose":
        paragraphs = "".join(
            f"      <p>{escape(text)}</p>\n" for text in section.get("paragraphs", [])
        )
        return open_section("prose", section) + heading + paragraphs + "    </section>\n"

    if kind in ("features", "cards"):
        items = ""
        for item in section.get("items", []):
            title = escape(item["title"])
            # The whole card is not the link. A link wrapping a heading plus a
            # paragraph reads as one enormous, unnamed target to a screen reader;
            # the heading carries the link and the card carries the heading.
            title_markup = (
                f'<a href="{escape(item["href"])}">{title}</a>' if item.get("href") else title
            )
            inner = render_image(item.get("image"), prefix, "card-media")
            inner += f"          <h3>{title_markup}</h3>\n"
            if item.get("meta"):
                inner += f'          <p class="meta">{escape(item["meta"])}</p>\n'
            if item.get("text"):
                inner += f'          <p>{escape(item["text"])}</p>\n'
            items += f'        <li class="card">\n{inner}        </li>\n'
        return (
            open_section(kind, section)
            + heading
            + f'      <ul class="grid">\n{items}      </ul>\n'
            + "    </section>\n"
        )

    if kind == "list":
        items = ""
        for item in section.get("items", []):
            title = escape(item["title"])
            title_markup = (
                f'<a href="{escape(item["href"])}">{title}</a>' if item.get("href") else title
            )
            inner = f"          <h3>{title_markup}</h3>\n"
            if item.get("meta"):
                inner += f'          <p class="meta">{escape(item["meta"])}</p>\n'
            if item.get("text"):
                inner += f'          <p>{escape(item["text"])}</p>\n'
            items += f'        <li class="entry">\n{inner}        </li>\n'
        return (
            open_section("list", section)
            + heading
            + f'      <ul class="stack">\n{items}      </ul>\n'
            + "    </section>\n"
        )

    if kind == "contact":
        body = heading
        if section.get("text"):
            body += f'      <p>{escape(section["text"])}</p>\n'
        links = "".join(
            f'        <li><a href="{escape(link["href"])}">{escape(link["label"])}</a></li>\n'
            for link in section.get("links", [])
        )
        body += f'      <ul class="inline">\n{links}      </ul>\n'
        return open_section("contact", section) + body + "    </section>\n"

    return ""


def render_nav(pages: list[dict], current: str, prefix: str, nav_label: str = "Main") -> str:
    entries = [page for page in pages if page.get("nav")]
    if len(entries) < 2:
        return ""
    items = ""
    for page in entries:
        label = escape(page["nav"])
        if page["path"] == current:
            # aria-current is the only thing that tells a screen-reader user which
            # of these links is the page they are already on; the visual style below
            # is keyed off the same attribute so the two can never disagree.
            items += f'        <li><a href="{escape(prefix + page["path"])}" aria-current="page">{label}</a></li>\n'
        else:
            items += f'        <li><a href="{escape(prefix + page["path"])}">{label}</a></li>\n'
    return f'    <nav aria-label="{escape(nav_label)}">\n      <ul>\n{items}      </ul>\n    </nav>\n'


def render_page(spec: dict, page: dict) -> str:
    site = spec["site"]
    prefix = relative_prefix(page["path"])
    description = page.get("description") or site.get("description") or site.get("tagline") or ""

    title = page["title"]
    document_title = title if title == site["title"] else f"{title} — {site['title']}"

    head = (
        "<!DOCTYPE html>\n"
        f'<html lang="{escape(site["lang"])}" dir="{escape(site["dir"])}">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        # Without this every phone renders the page at 980px and scales it down,
        # which silently defeats every media query below it.
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{escape(document_title)}</title>\n"
    )
    if description:
        head += f'  <meta name="description" content="{escape(description)}">\n'
    head += f'  <link rel="stylesheet" href="{escape(prefix)}assets/site.css">\n</head>\n'

    brand_href = escape(prefix + "index.html")
    header = (
        "<body>\n"
        f'  <a class="skip-link" href="#main">{escape(site["skipLinkLabel"])}</a>\n'
        "  <header class=\"site-header\">\n"
        f'    <p class="brand"><a href="{brand_href}">{escape(site["title"])}</a></p>\n'
        + render_nav(spec["pages"], page["path"], prefix, site["navLabel"])
        + "  </header>\n"
    )

    main = '  <main id="main">\n' + "".join(
        render_section(section, prefix) for section in page["sections"]
    ) + "  </main>\n"

    year = datetime.date.today().year
    footer_note = f"    <p>{escape(site['footer'])}</p>\n" if site.get("footer") else ""
    footer = (
        '  <footer class="site-footer">\n'
        f"    <p>© {year} {escape(site['title'])}</p>\n"
        f"{footer_note}"
        "  </footer>\n"
        "</body>\n"
        "</html>\n"
    )

    return head + header + main + footer


# --- stylesheet -------------------------------------------------------------------

# One stylesheet, emitted with the accent substituted. The values and the reasoning
# behind them are in references/design.md; that file and this template are one
# decision written twice, so a change to either belongs in both.
STYLESHEET = """/* Generated by scaffold_site.py. Self-contained: no @import, no remote font,
   no external asset of any kind — the sandbox that renders this has no network. */

{font_face}

:root {{
  color-scheme: {colour_scheme};

  /* Chainabit typography is explicit and offline by default. A user family
     stays first in its own local/system stack; do not append Chainabit fonts
     to a competing identity. */
  --font-sans: {font_stack};
  --font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
    "Liberation Mono", monospace;

  /* Fluid type: a 1.200 scale at 320px opening to 1.250 at 1280px. The clamp
     bounds are the two ends, so nothing is ever smaller than the mobile step or
     larger than the desktop one however wide the window gets. */
  --step--1: clamp(0.833rem, 0.827rem + 0.028vw, 0.85rem);
  --step-0: clamp(1rem, 0.979rem + 0.104vw, 1.0625rem);
  --step-1: clamp(1.2rem, 1.157rem + 0.213vw, 1.328rem);
  --step-2: clamp(1.44rem, 1.367rem + 0.367vw, 1.66rem);
  --step-3: clamp(1.728rem, 1.612rem + 0.578vw, 2.075rem);
  --step-4: clamp(2.074rem, 1.901rem + 0.867vw, 2.594rem);
  --step-5: clamp(2.488rem, 2.237rem + 1.257vw, 3.242rem);

  /* 4px rhythm. Every margin and gap below is one of these, which is what stops
     a generated page drifting into 13px here and 27px there. */
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-5: 1.5rem;
  --space-6: 2rem;
  --space-7: 3rem;
  --space-8: 4rem;
  --space-9: 6rem;

  --content: 72rem;
  --measure: 68ch;
  --radius: 0.5rem;

  --bg: {light_background};
  --surface: {light_surface};
  --ink: {light_ink};
  --body: {light_body};
  --muted: {light_muted};
  --rule: {light_rule};
  --accent: {light_accent};
  --accent-ink: {light_accent_ink};
}}
{dark_block}
* {{ box-sizing: border-box; }}

body {{
  margin: 0;
  background: var(--bg);
  color: var(--body);
  font-family: var(--font-sans);
  font-size: var(--step-0);
  line-height: 1.6;
  -webkit-text-size-adjust: 100%;
}}

h1, h2, h3 {{
  color: var(--ink);
  line-height: 1.2;
  text-wrap: balance;
  margin: 0 0 var(--space-4);
}}

h1 {{ font-size: var(--step-5); letter-spacing: -0.02em; }}
h2 {{ font-size: var(--step-3); letter-spacing: -0.01em; }}
h3 {{ font-size: var(--step-1); }}

p {{ margin: 0 0 var(--space-4); max-width: var(--measure); }}
code, pre {{ font-family: var(--font-mono); font-size: 0.9em; }}

a {{ color: var(--accent); text-underline-offset: 0.15em; }}
a:hover {{ text-decoration-thickness: 2px; }}

/* A visible focus ring on every interactive element, in a colour that clears
   contrast on both themes. Removing this is the single most common way a
   hand-written site becomes unusable by keyboard. */
:where(a, button, input, textarea, select):focus-visible {{
  outline: 3px solid var(--accent);
  outline-offset: 2px;
  border-radius: 2px;
}}

.skip-link {{
  position: absolute;
  inset-inline-start: -9999px;
  top: var(--space-2);
  background: var(--accent);
  color: var(--accent-ink);
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius);
  z-index: 10;
}}
.skip-link:focus {{ inset-inline-start: var(--space-4); }}

.site-header {{
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3) var(--space-6);
  align-items: baseline;
  justify-content: space-between;
  max-width: var(--content);
  margin: 0 auto;
  padding: var(--space-5) var(--space-5) var(--space-4);
  border-bottom: 1px solid var(--rule);
}}

.brand {{ margin: 0; font-size: var(--step-1); font-weight: 600; }}
.brand a {{ color: var(--ink); text-decoration: none; }}

.site-header nav ul {{
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-5);
  list-style: none;
  margin: 0;
  padding: 0;
}}
.site-header nav a {{ color: var(--muted); text-decoration: none; }}
.site-header nav a:hover {{ color: var(--accent); text-decoration: underline; }}
.site-header nav a[aria-current="page"] {{ color: var(--ink); font-weight: 600; }}

main {{
  max-width: var(--content);
  margin: 0 auto;
  padding: 0 var(--space-5);
}}

section {{ padding: var(--space-7) 0; border-bottom: 1px solid var(--rule); }}
section:last-child {{ border-bottom: 0; }}

.hero {{ padding: var(--space-8) 0 var(--space-7); }}
.lede {{ font-size: var(--step-1); color: var(--muted); max-width: var(--measure); }}
.hero-media {{ margin: var(--space-6) 0 0; }}
.hero-media img {{
  display: block;
  max-width: 100%;
  height: auto;
  border-radius: var(--radius);
}}

.actions {{ display: flex; flex-wrap: wrap; gap: var(--space-3); margin-top: var(--space-5); }}

.button {{
  display: inline-block;
  background: var(--accent);
  color: var(--accent-ink);
  padding: var(--space-3) var(--space-5);
  border-radius: var(--radius);
  text-decoration: none;
  font-weight: 600;
}}
.button:hover {{ text-decoration: underline; }}
.button--quiet {{
  background: transparent;
  color: var(--accent);
  box-shadow: inset 0 0 0 1px var(--rule);
}}

.grid {{
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-5);
  list-style: none;
  margin: 0;
  padding: 0;
}}

.card {{
  background: var(--surface);
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  padding: var(--space-5);
}}
.card-media {{ margin: calc(var(--space-5) * -1) calc(var(--space-5) * -1) var(--space-4); }}
.card-media img {{
  display: block;
  width: 100%;
  height: auto;
  border-radius: var(--radius) var(--radius) 0 0;
}}
.card h3 {{ margin-bottom: var(--space-2); }}
.card p:last-child {{ margin-bottom: 0; }}

.stack {{ list-style: none; margin: 0; padding: 0; }}
.entry {{ padding: var(--space-5) 0; border-top: 1px solid var(--rule); }}
.entry:first-child {{ border-top: 0; padding-top: 0; }}
.entry h3 {{ margin-bottom: var(--space-1); }}
.entry p:last-child {{ margin-bottom: 0; }}

.meta {{ color: var(--muted); font-size: var(--step--1); }}

.inline {{ display: flex; flex-wrap: wrap; gap: var(--space-5); list-style: none; margin: 0; padding: 0; }}

.site-footer {{
  max-width: var(--content);
  margin: 0 auto;
  padding: var(--space-6) var(--space-5) var(--space-8);
  border-top: 1px solid var(--rule);
  color: var(--muted);
  font-size: var(--step--1);
}}
.site-footer p {{ margin: 0 0 var(--space-1); }}

/* 40rem: enough width for two cards. 60rem: three, and the hero can breathe.
   Both are content breakpoints — they are where this layout stops working, not
   where a particular device happens to be. */
@media (min-width: 40rem) {{
  .grid {{ grid-template-columns: repeat(2, 1fr); }}
}}

@media (min-width: 60rem) {{
  .grid {{ grid-template-columns: repeat(3, 1fr); }}
  .hero {{ padding: var(--space-9) 0 var(--space-8); }}
  main, .site-header, .site-footer {{ padding-inline: var(--space-6); }}
}}

/* Respect a reader who has asked the OS for less motion. There is no animation
   here today, but a starting template that omits this teaches the wrong habit. */
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }}
}}
"""

DARK_TOKENS = """  --bg: {background};
  --surface: {surface};
  --ink: {ink};
  --body: {body};
  --muted: {muted};
  --rule: {rule};
  --accent: {accent};
  --accent-ink: {accentInk};
"""


def render_stylesheet(site: dict) -> str:
    theme = site["theme"]
    # A complete override carries only its active themes. Reuse that active
    # palette to satisfy the stylesheet's base-token shape for a single-theme
    # site, rather than reintroducing dormant Chainabit values into its CSS or
    # contract evidence.
    light = site["palettes"].get("light") or site["palettes"]["dark"]
    dark = site["palettes"].get("dark") or light
    dark_tokens = DARK_TOKENS.format(**dark)
    font_family = json.dumps(site["font"], ensure_ascii=False)
    primary_faces = (
            "@font-face {{ font-family: 'IBM Plex Sans'; font-style: normal; "
            f"font-weight: {weight}; font-display: swap; "
            f"src: url('fonts/{filename}') format('woff2'); }}"
            for filename, weight in FONT_FILES.items()
    )
    fallback_faces = (
            "@font-face {{ font-family: 'IBM Plex Sans Arabic'; font-style: normal; "
            f"font-weight: {weight}; font-display: swap; "
            f"src: url('fonts/{filename}') format('woff2'); }}"
            for filename, weight in FALLBACK_FONT_FILES.items()
    )
    # The packaged IBM files are an implementation of the Chainabit default,
    # not an implicit fallback for a customer's explicit font.  A named user
    # family resolves from the browser's local/system fonts without a remote
    # request; supplied webfont files belong in a future explicit asset field.
    uses_packaged_font = site["font"] in AVAILABLE_WEB_FAMILIES
    font_face = "\n".join((*primary_faces, *fallback_faces)) if uses_packaged_font else ""
    font_stack = (
        f'{font_family}, "IBM Plex Sans Arabic", sans-serif'
        if site["font"] == DEFAULT_FONT_FAMILY
        else f"{font_family}, sans-serif"
    )

    arguments = {
        "font_face": font_face,
        "font_stack": font_stack,
        "light_background": light["background"],
        "light_surface": light["surface"],
        "light_ink": light["ink"],
        "light_body": light["body"],
        "light_muted": light["muted"],
        "light_rule": light["rule"],
        "light_accent": light["accent"],
        "light_accent_ink": light["accentInk"],
    }
    if theme == "light":
        return STYLESHEET.format(colour_scheme="light", dark_block="", **arguments)
    if theme == "dark":
        # A committed dark site still declares the light tokens first and then
        # overwrites them unconditionally, so every token has exactly one place it
        # is defined and none of them can go missing behind a media query.
        return STYLESHEET.format(
            colour_scheme="dark",
            dark_block=":root {\n" + dark_tokens + "}\n",
            **arguments,
        )
    return STYLESHEET.format(
        colour_scheme="light dark",
        dark_block="\n@media (prefers-color-scheme: dark) {\n  :root {\n"
        + "".join("  " + line + "\n" for line in dark_tokens.splitlines())
        + "  }\n}\n",
        **arguments,
    )


# --- the starting templates -------------------------------------------------------
#
# Three shapes, not three flavours. `landing` is one page with no navigation;
# `portfolio` is a flat multi-page site; `blog` nests article pages under a
# directory, which is where an entry point most often ends up in the wrong place.
# Everything else is content, and content is what the caller replaces.

TEMPLATES: dict[str, dict] = {
    "landing": {
        "site": {
            "title": "Northwind",
            "tagline": "Scheduling that fits the way your crew actually works.",
            "description": "Northwind is shift scheduling for field teams: build a week in minutes, publish once, and let the crew see it on their phones.",
            "theme": "auto",
            "footer": "Built as a starting point. Replace this copy before publishing.",
        },
        "pages": [
            {
                "path": "index.html",
                "title": "Northwind",
                "sections": [
                    {
                        "type": "hero",
                        "heading": "Scheduling that fits the way your crew actually works",
                        "text": "Build a week in minutes, publish once, and let everyone see the same roster on their phone.",
                        "actions": [
                            {"label": "Talk to us", "href": "#contact"},
                            {"label": "See how it works", "href": "#features"},
                        ],
                    },
                    {
                        "type": "features",
                        "id": "features",
                        "heading": "What you get",
                        "items": [
                            {
                                "title": "One roster, everywhere",
                                "text": "Publish a week and it is on every phone within seconds. No spreadsheet version to keep straight.",
                            },
                            {
                                "title": "Swaps that settle themselves",
                                "text": "A crew member offers a shift, a colleague takes it, and the roster updates once both have agreed.",
                            },
                            {
                                "title": "Hours you can bill",
                                "text": "Every published shift becomes a timesheet line, so payroll starts from what was worked, not from memory.",
                            },
                        ],
                    },
                    {
                        "type": "prose",
                        "heading": "Why another scheduler",
                        "paragraphs": [
                            "Most scheduling tools were written for offices, where everyone is at a desk and a calendar invite is enough. Field work is not like that: the roster changes on the morning it runs, and the person it changes for is holding a phone in the rain.",
                            "Northwind is built around that moment. The published roster is the single source of truth, changes are visible the second they are made, and nothing needs a laptop.",
                        ],
                    },
                    {
                        "type": "contact",
                        "id": "contact",
                        "heading": "Talk to us",
                        "text": "Tell us how your crew is scheduled today and we will tell you honestly whether this helps.",
                        "links": [
                            {"label": "hello@example.com", "href": "mailto:hello@example.com"},
                            {"label": "Back to the top", "href": "#features"},
                        ],
                    },
                ],
            }
        ],
    },
    "portfolio": {
        "site": {
            "title": "Aylin Demir",
            "tagline": "Product designer working on tools for people who fix things.",
            "description": "Portfolio of Aylin Demir, a product designer working on field service, logistics and repair tooling.",
            "theme": "auto",
            "footer": "Replace this copy and these projects before publishing.",
        },
        "pages": [
            {
                "path": "index.html",
                "title": "Aylin Demir",
                "nav": "Home",
                "sections": [
                    {
                        "type": "hero",
                        "heading": "Aylin Demir",
                        "text": "Product designer working on tools for people who fix things — field service, logistics, repair.",
                        "actions": [
                            {"label": "See selected work", "href": "work.html"},
                            {"label": "About me", "href": "about.html"},
                        ],
                    },
                    {
                        "type": "cards",
                        "heading": "Selected work",
                        "items": [
                            {
                                "title": "Dispatch board rebuild",
                                "meta": "Field service · 2026",
                                "text": "Cut the time to assign an emergency call from four minutes to under thirty seconds.",
                                "href": "work.html",
                            },
                            {
                                "title": "Parts catalogue search",
                                "meta": "Logistics · 2025",
                                "text": "Redesigned search around part numbers people half-remember rather than the ones printed on the box.",
                                "href": "work.html",
                            },
                            {
                                "title": "Repair intake on paper",
                                "meta": "Research · 2025",
                                "text": "Two weeks in three workshops, and the finding that the paper form was better than the app.",
                                "href": "work.html",
                            },
                        ],
                    },
                ],
            },
            {
                "path": "work.html",
                "title": "Work",
                "nav": "Work",
                "sections": [
                    {
                        "type": "hero",
                        "heading": "Work",
                        "text": "Three projects, described by what changed rather than by what was shipped.",
                    },
                    {
                        "type": "list",
                        "items": [
                            {
                                "title": "Dispatch board rebuild",
                                "meta": "Field service · 2026 · Lead designer",
                                "text": "The old board sorted by job age, which meant the most urgent call was rarely at the top. We rebuilt it around the dispatcher's actual question — who can be there soonest — and assignment time fell from four minutes to under thirty seconds.",
                            },
                            {
                                "title": "Parts catalogue search",
                                "meta": "Logistics · 2025 · Design and research",
                                "text": "Technicians searched for parts by the number stamped on the old part, not the one in the catalogue. Indexing the superseded numbers removed most of the support calls the search was generating.",
                            },
                            {
                                "title": "Repair intake on paper",
                                "meta": "Research · 2025",
                                "text": "Two weeks of observation across three workshops. The recommendation was to keep the paper form and digitise it at the counter, which is not what anyone hoped to hear.",
                            },
                        ],
                    },
                ],
            },
            {
                "path": "about.html",
                "title": "About",
                "nav": "About",
                "sections": [
                    {
                        "type": "hero",
                        "heading": "About",
                        "text": "Eight years designing for people whose work does not happen at a desk.",
                    },
                    {
                        "type": "prose",
                        "paragraphs": [
                            "I design tools for field and workshop teams: dispatchers, technicians, warehouse staff. The common thread is that the software is never the point — it is in the way of something physical that needs doing, and the best version of it asks for the least attention.",
                            "Before design I spent three years in logistics operations, which is where I learned that the person using a tool badly is usually right about something the tool got wrong.",
                        ],
                    },
                    {
                        "type": "contact",
                        "heading": "Get in touch",
                        "text": "Open to freelance and contract work.",
                        "links": [
                            {"label": "aylin@example.com", "href": "mailto:aylin@example.com"},
                            {"label": "Selected work", "href": "work.html"},
                        ],
                    },
                ],
            },
        ],
    },
    "blog": {
        "site": {
            "title": "Field Notes",
            "tagline": "Short pieces on maintenance, tooling and the work behind the work.",
            "description": "Field Notes: short essays on maintenance, tooling, and the unglamorous work that keeps systems running.",
            "theme": "auto",
            "footer": "Replace these posts before publishing.",
        },
        "pages": [
            {
                "path": "index.html",
                "title": "Field Notes",
                "nav": "Home",
                "sections": [
                    {
                        "type": "hero",
                        "heading": "Field Notes",
                        "text": "Short pieces on maintenance, tooling, and the work behind the work.",
                    },
                    {
                        "type": "list",
                        "heading": "Recent",
                        "items": [
                            {
                                "title": "The checklist nobody reads",
                                "meta": "18 August 2026",
                                "text": "A checklist that is never wrong is also never read. What happens when you let one fail loudly.",
                                "href": "posts/checklist.html",
                            },
                            {
                                "title": "Spare parts as a design problem",
                                "meta": "2 August 2026",
                                "text": "Every decision about a spare is a bet on a failure that has not happened yet.",
                                "href": "posts/spares.html",
                            },
                        ],
                    },
                ],
            },
            {
                "path": "about.html",
                "title": "About",
                "nav": "About",
                "sections": [
                    {
                        "type": "hero",
                        "heading": "About Field Notes",
                        "text": "Why maintenance is worth writing about.",
                    },
                    {
                        "type": "prose",
                        "paragraphs": [
                            "Field Notes is a small collection of essays about maintenance: the discipline of keeping working things working, and why it is so consistently undervalued relative to building new ones.",
                            "Posts arrive when there is something to say, which is not often.",
                        ],
                    },
                    {
                        "type": "contact",
                        "heading": "Contact",
                        "links": [
                            {"label": "notes@example.com", "href": "mailto:notes@example.com"},
                        ],
                    },
                ],
            },
            {
                "path": "posts/checklist.html",
                "title": "The checklist nobody reads",
                "sections": [
                    {
                        "type": "hero",
                        "heading": "The checklist nobody reads",
                        "text": "18 August 2026",
                    },
                    {
                        "type": "prose",
                        "paragraphs": [
                            "A checklist earns its authority by occasionally stopping someone. If every item on it has passed every time for two years, the people running it have learned — correctly, from evidence — that running it is ceremony.",
                            "The fix is not more discipline. It is to remove the items that have never once caught anything and replace them with the two that would have caught last quarter's incident.",
                        ],
                    },
                    {
                        "type": "contact",
                        "heading": "Elsewhere",
                        "links": [
                            {"label": "All posts", "href": "../index.html"},
                            {"label": "About", "href": "../about.html"},
                        ],
                    },
                ],
            },
            {
                "path": "posts/spares.html",
                "title": "Spare parts as a design problem",
                "sections": [
                    {
                        "type": "hero",
                        "heading": "Spare parts as a design problem",
                        "text": "2 August 2026",
                    },
                    {
                        "type": "prose",
                        "paragraphs": [
                            "A spare part on a shelf is capital sitting still against a failure that may never come. A spare part not on the shelf is a machine stopped for six weeks. Every stocking decision is a bet, and most organisations place it once and never revisit the odds.",
                            "The interesting move is to make the bet visible: which failures the current stock covers, and which it quietly does not.",
                        ],
                    },
                    {
                        "type": "contact",
                        "heading": "Elsewhere",
                        "links": [
                            {"label": "All posts", "href": "../index.html"},
                            {"label": "About", "href": "../about.html"},
                        ],
                    },
                ],
            },
        ],
    },
}


# --- output -----------------------------------------------------------------------


def resolve_image_assets(spec: dict, source_root: str | None) -> dict[str, bytes]:
    """Return content-addressed destination bytes and resolve every image dict.

    Canonical source paths own count/byte accounting. Content digests own the
    output, so identical bytes from the same or different local paths are held
    and written once, and the writer never reopens a client-controlled path.
    """
    if source_root is None:
        return {}

    references: list[tuple[dict, str]] = []
    source_identities: dict[str, tuple[int, int]] = {}
    canonical_sources: dict[tuple[int, int], tuple[str, bytes]] = {}
    for image in _declared_images(spec):
        src = image["src"]
        references.append((image, src))
        if src in source_identities:
            continue
        file_fd, metadata = open_local_image(source_root, src)
        identity = (metadata.st_dev, metadata.st_ino)
        source_identities[src] = identity
        if identity in canonical_sources:
            os.close(file_fd)
            continue
        canonical_sources[identity] = (src, read_open_image(file_fd))

    if len(canonical_sources) > MAX_DISTINCT_IMAGES:
        raise RuntimeError(
            f"image set changed after validation and now exceeds {MAX_DISTINCT_IMAGES} files"
        )

    source_assets: dict[tuple[int, int], tuple[str, bytes]] = {}
    digest_assets: dict[str, tuple[str, bytes]] = {}
    total_image_bytes = 0
    for identity, (src, data) in canonical_sources.items():
        if len(data) > MAX_IMAGE_BYTES:
            raise RuntimeError(f"image {src!r} changed after validation and is now too large")
        total_image_bytes += len(data)
        if total_image_bytes > MAX_TOTAL_IMAGE_BYTES:
            raise RuntimeError(
                "image set changed after validation and now exceeds the aggregate byte limit"
            )
        digest = hashlib.sha256(data).hexdigest()
        asset = digest_assets.get(digest)
        if asset is None:
            basename = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(src))
            asset = (f"assets/images/{digest}-{basename}", data)
            digest_assets[digest] = asset
        source_assets[identity] = asset

    for image, src in references:
        image["resolvedSrc"] = source_assets[source_identities[src]][0]
    return {destination: data for destination, data in digest_assets.values()}


def write_site(
    spec: dict, destination: str, force: bool, source_root: str | None = None
) -> list[str]:
    if os.path.exists(destination) and not os.path.isdir(destination):
        raise SystemExit(f"ERROR: output: {destination} exists and is not a directory")
    if os.path.isdir(destination) and os.listdir(destination) and not force:
        raise SystemExit(
            f"ERROR: output: {destination} is not empty. Pass --force to write into it "
            "anyway, or choose a new directory — a site written over a half-finished "
            "one leaves files from both."
        )

    written: list[str] = []

    # Resolved before any page renders: render_section reads image["resolvedSrc"].
    image_assets = resolve_image_assets(spec, source_root)

    for page in spec["pages"]:
        target = os.path.join(destination, *page["path"].split("/"))
        os.makedirs(os.path.dirname(target) or destination, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(render_page(spec, page))
        written.append(page["path"])

    if image_assets:
        images_dir = os.path.join(destination, "assets", "images")
        os.makedirs(images_dir, exist_ok=True)
        for dest_relative, data in image_assets.items():
            dest_path = os.path.join(destination, *dest_relative.split("/"))
            if not os.path.isfile(dest_path):
                with open(dest_path, "wb") as handle:
                    handle.write(data)
            written.append(dest_relative)

    assets = os.path.join(destination, "assets")
    os.makedirs(assets, exist_ok=True)
    with open(os.path.join(assets, "site.css"), "w", encoding="utf-8") as handle:
        handle.write(render_stylesheet(spec["site"]))
    written.append("assets/site.css")

    if spec["site"]["font"] in AVAILABLE_WEB_FAMILIES:
        font_destination = os.path.join(assets, "fonts")
        os.makedirs(font_destination, exist_ok=True)
        for filename in (*FONT_FILES, *FALLBACK_FONT_FILES):
            source = os.path.join(DEFAULT_FONT_DIR, filename)
            if not os.path.isfile(source):
                raise RuntimeError(
                    f"canonical font asset is unavailable: {source}. The sandbox "
                    "runtime must provide the declared Chainabit typography bundle."
                )
            with open(source, "rb") as handle:
                if handle.read(4) != b"wOF2":
                    raise RuntimeError(f"canonical font asset is not WOFF2: {source}")
            shutil.copyfile(source, os.path.join(font_destination, filename))
            written.append(f"assets/fonts/{filename}")

    contract_path = ".chainabit-site.json"
    with open(os.path.join(destination, contract_path), "w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": "chainabit.website.contract/v1",
                "format": "static-website",
                "entryPoint": "index.html",
                "typography": {
                    "family": spec["site"]["font"],
                    "source": spec["site"]["fontSource"],
                },
                "branding": {
                    "source": spec["site"]["paletteSource"],
                    "palettes": spec["site"]["palettes"],
                },
                "runtime": {"network": "offline", "javascript": False},
            },
            handle,
            ensure_ascii=False,
            sort_keys=True,
        )
        handle.write("\n")
    written.append(contract_path)

    return written


def tree_identity(root: str) -> tuple[str, int, int]:
    entries: list[bytes] = []
    total = 0
    files: list[str] = []
    for directory, dirs, names in os.walk(root):
        dirs.sort()
        for name in names:
            files.append(os.path.relpath(os.path.join(directory, name), root).replace(os.sep, "/"))
    for relative in sorted(files):
        absolute = os.path.join(root, *relative.split("/"))
        if os.path.islink(absolute) or not os.path.isfile(absolute):
            raise RuntimeError(f"site output contains an unsupported entry: {absolute}")
        with open(absolute, "rb") as handle:
            data = handle.read()
        total += len(data)
        entries.append(
            relative.encode("utf-8") + b"\0" + hashlib.sha256(data).hexdigest().encode("ascii")
            + b"\0" + str(len(data)).encode("ascii") + b"\n"
        )
    return hashlib.sha256(b"".join(entries)).hexdigest(), total, len(files)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scaffold_site.py",
        description=(
            "Generate a static HTML/CSS website from a JSON spec, or from a built-in "
            "starting template. No build step, no JavaScript, no remote assets."
        ),
        epilog=(
            "Examples:\n"
            "  python3 scaffold_site.py --template portfolio ./site\n"
            "  python3 scaffold_site.py --template landing --print-spec > spec.json\n"
            "  python3 scaffold_site.py --spec spec.json --validate-only\n"
            "  python3 scaffold_site.py --spec spec.json ./site\n\n"
            "Always run validate_site.py over the output before reporting a site as done.\n"
            "Runs offline, stdlib only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--template",
        choices=sorted(TEMPLATES),
        help="start from a built-in template instead of a spec file",
    )
    source.add_argument("--spec", metavar="FILE", help="path to a JSON site spec")
    parser.add_argument(
        "outdir",
        nargs="?",
        help="directory to write the site into (omit with --print-spec or --validate-only)",
    )
    parser.add_argument(
        "--print-spec",
        action="store_true",
        help="print the spec as JSON and exit, so it can be edited and fed back in",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="check the spec and exit without writing anything",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="write into an output directory that already has files in it",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    source_root: str | None = None
    if args.template:
        raw = TEMPLATES[args.template]
    else:
        if not os.path.exists(args.spec):
            print(f"ERROR: spec: {args.spec} does not exist", file=sys.stderr)
            return 1
        try:
            with open(args.spec, encoding="utf-8") as handle:
                raw = json.load(handle)
        except UnicodeDecodeError:
            print(f"ERROR: spec: {args.spec} is not UTF-8 text", file=sys.stderr)
            return 1
        except json.JSONDecodeError as exc:
            print(f"ERROR: spec: {args.spec} is not valid JSON: {exc}", file=sys.stderr)
            return 1
        # An image `src` is relative to the spec, so an image can only be
        # checked (and later copied) once we know what directory that is —
        # a built-in --template never declares one and needs no root.
        source_root = os.path.dirname(os.path.abspath(args.spec))

    spec, errors = validate_spec(raw, source_root)

    if args.print_spec:
        if errors:
            # Still print it. A caller asking for the spec wants something to edit,
            # and a spec that fails is exactly the one worth looking at.
            for message in errors.messages:
                print(message, file=sys.stderr)
        print(json.dumps(raw, indent=2, ensure_ascii=False))
        return 1 if errors else 0

    if errors:
        for message in errors.messages:
            print(message, file=sys.stderr)
        print(
            f"\n{len(errors.messages)} problem(s) in the spec. Fix all of them and re-run.",
            file=sys.stderr,
        )
        return 1

    if args.validate_only:
        print(
            f"OK: spec describes {len(spec['pages'])} page(s), entry point index.html, "
            f"theme {spec['site']['theme']}"
        )
        return 0

    if not args.outdir:
        parser.error("an output directory is required unless --print-spec or --validate-only")

    try:
        written = write_site(spec, args.outdir, args.force, source_root)
        digest, total_bytes, file_count = tree_identity(args.outdir)
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: website_runtime: {exc}", file=sys.stderr)
        return 2
    print(f"OK: wrote {len(written)} file(s) to {args.outdir}")
    for path in written:
        print(f"  {path}")
    print(f"\nNow run: python3 validate_site.py {args.outdir}")
    print(json.dumps({
        "schema": EXECUTION_SCHEMA,
        "success": True,
        "generator": "skill-static-website.scaffold",
        "output": {
            "path": os.path.realpath(args.outdir),
            "shape": "tree",
            "mime": "application/vnd.chainabit.static-site",
            "sha256": digest,
            "bytes": total_bytes,
            "files": file_count,
        },
        "typography": {
            "family": spec["site"]["font"],
            "source": spec["site"]["fontSource"],
        },
        "branding": {"source": spec["site"]["paletteSource"]},
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
