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
import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import sys

HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# The two backgrounds an accent has to survive. Both are in references/design.md
# with their measured ratios; changing one here without changing the document there
# leaves the skill telling the reader something that is no longer true.
LIGHT_BACKGROUND = "#FFFFFF"
DARK_BACKGROUND = "#0F172A"
DEFAULT_ACCENT = "#1D4ED8"
DEFAULT_ACCENT_DARK = "#60A5FA"

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


def check_section(section: object, path: str, errors: SpecErrors) -> dict:
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


def validate_spec(spec: object) -> tuple[dict, SpecErrors]:
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
        if font not in AVAILABLE_WEB_FAMILIES:
            errors.add(
                "site.font",
                "is not available in the offline artifact runtime; supported families are "
                + ", ".join(sorted(AVAILABLE_WEB_FAMILIES)),
            )

    checked_site = {
        "title": require_text(site.get("title"), "site.title", errors, 80),
        "tagline": optional_text(site.get("tagline"), "site.tagline", errors, 200),
        "description": optional_text(site.get("description"), "site.description", errors, 300),
        "lang": optional_text(site.get("lang"), "site.lang", errors, 12) or "en",
        "theme": theme,
        "font": font,
        "fontSource": font_source,
        "footer": optional_text(site.get("footer"), "site.footer", errors, 300),
        # The accent is checked against the background it will actually sit on. On
        # "auto" both apply, because the visitor's system setting decides which one
        # the site is wearing and neither is the one we get to test in isolation.
        "accent": check_colour(
            site.get("accent"), "site.accent", LIGHT_BACKGROUND, errors, DEFAULT_ACCENT
        ),
        "accentDark": check_colour(
            site.get("accentDark"), "site.accentDark", DARK_BACKGROUND, errors, DEFAULT_ACCENT_DARK
        ),
    }

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
                    check_section(section, f"{page_path}.sections[{position}]", errors)
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

    return {"site": checked_site, "pages": checked_pages}, errors


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


def render_section(section: dict) -> str:
    kind = section.get("type")

    if kind == "hero":
        body = f'      <h1>{escape(section["heading"])}</h1>\n'
        if section.get("text"):
            body += f'      <p class="lede">{escape(section["text"])}</p>\n'
        body += render_actions(section.get("actions", []))
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
            inner = f"          <h3>{title_markup}</h3>\n"
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


def render_nav(pages: list[dict], current: str, prefix: str) -> str:
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
    return f'    <nav aria-label="Main">\n      <ul>\n{items}      </ul>\n    </nav>\n'


def render_page(spec: dict, page: dict) -> str:
    site = spec["site"]
    prefix = relative_prefix(page["path"])
    description = page.get("description") or site.get("description") or site.get("tagline") or ""

    title = page["title"]
    document_title = title if title == site["title"] else f"{title} — {site['title']}"

    head = (
        "<!DOCTYPE html>\n"
        f'<html lang="{escape(site["lang"])}">\n'
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
        '  <a class="skip-link" href="#main">Skip to content</a>\n'
        "  <header class=\"site-header\">\n"
        f'    <p class="brand"><a href="{brand_href}">{escape(site["title"])}</a></p>\n'
        + render_nav(spec["pages"], page["path"], prefix)
        + "  </header>\n"
    )

    main = '  <main id="main">\n' + "".join(
        render_section(section) for section in page["sections"]
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

  /* Chainabit typography is explicit and offline. The primary family is either
     the user's validated override or the canonical bundled IBM Plex Sans. */
  --font-sans: {font_family}, "IBM Plex Sans Arabic", sans-serif;
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

  --bg: #FFFFFF;
  --surface: #F8FAFC;
  --ink: #0F172A;
  --body: #1E293B;
  --muted: #475569;
  --rule: #CBD5E1;
  --accent: {accent};
  --accent-ink: #FFFFFF;
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
  left: -9999px;
  top: var(--space-2);
  background: var(--accent);
  color: var(--accent-ink);
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius);
  z-index: 10;
}}
.skip-link:focus {{ left: var(--space-4); }}

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

DARK_TOKENS = """  --bg: #0F172A;
  --surface: #1E293B;
  --ink: #F8FAFC;
  --body: #E2E8F0;
  --muted: #94A3B8;
  --rule: #334155;
  --accent: {accent_dark};
  --accent-ink: #0F172A;
"""


def render_stylesheet(site: dict) -> str:
    theme = site["theme"]
    dark_tokens = DARK_TOKENS.format(accent_dark=site["accentDark"])
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
    font_face = "\n".join((*primary_faces, *fallback_faces))

    if theme == "light":
        return STYLESHEET.format(colour_scheme="light", accent=site["accent"], dark_block="", font_face=font_face, font_family=font_family)
    if theme == "dark":
        # A committed dark site still declares the light tokens first and then
        # overwrites them unconditionally, so every token has exactly one place it
        # is defined and none of them can go missing behind a media query.
        return STYLESHEET.format(
            colour_scheme="dark",
            accent=site["accent"],
            dark_block=":root {\n" + dark_tokens + "}\n",
            font_face=font_face,
            font_family=font_family,
        )
    return STYLESHEET.format(
        colour_scheme="light dark",
        accent=site["accent"],
        font_face=font_face,
        font_family=font_family,
        dark_block="\n@media (prefers-color-scheme: dark) {\n  :root {\n"
        + "".join("  " + line + "\n" for line in dark_tokens.splitlines())
        + "  }\n}\n",
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


def write_site(spec: dict, destination: str, force: bool) -> list[str]:
    if os.path.exists(destination) and not os.path.isdir(destination):
        raise SystemExit(f"ERROR: output: {destination} exists and is not a directory")
    if os.path.isdir(destination) and os.listdir(destination) and not force:
        raise SystemExit(
            f"ERROR: output: {destination} is not empty. Pass --force to write into it "
            "anyway, or choose a new directory — a site written over a half-finished "
            "one leaves files from both."
        )

    written: list[str] = []

    for page in spec["pages"]:
        target = os.path.join(destination, *page["path"].split("/"))
        os.makedirs(os.path.dirname(target) or destination, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(render_page(spec, page))
        written.append(page["path"])

    assets = os.path.join(destination, "assets")
    os.makedirs(assets, exist_ok=True)
    with open(os.path.join(assets, "site.css"), "w", encoding="utf-8") as handle:
        handle.write(render_stylesheet(spec["site"]))
    written.append("assets/site.css")

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

    spec, errors = validate_spec(raw)

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
        written = write_site(spec, args.outdir, args.force)
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
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
