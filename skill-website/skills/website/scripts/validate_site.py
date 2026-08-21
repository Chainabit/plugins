#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check that a generated directory is actually a servable website.

A broken site does not fail the way a broken document does. Every file is valid,
every script exits 0, and the defect only appears when a browser tries to resolve
something: a stylesheet that 404s, a nav link to a page that was renamed, an
`index.html` one directory lower than the thing that was promoted. None of that
raises anywhere, so a generator reports success on a site it has never opened.

This is the exit gate that opens it. It reports, per file:

  * a missing entry point, and separately a nested one, which is the common
    mistake and has a different fix;
  * links and asset references that resolve to nothing on disk;
  * absolute http(s) asset references, which cannot load — the sandbox has no
    network egress, so a CDN stylesheet or a remote font is a guaranteed 404;
  * `<img>` with no `alt`, a missing `<title>`, a missing viewport meta;
  * pages with nothing on them.

Deliberately stdlib-only (html.parser + re). It reads the files themselves rather
than trusting the generator's bookkeeping, so it works on a site this skill did
not produce — hand-written, exported from somewhere else, or assembled by the
model directly.

Usage:
    python3 validate_site.py ./site [--strict]
"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import sys
from html.parser import HTMLParser

# What a browser resolves for a directory, in the order servers try them. This
# list is the API's `WEBSITE_ENTRY_POINTS` restated; a promotion of kind 'website'
# is refused outright when none of these sits at the top of the promoted tree.
ENTRY_POINTS = ("index.html", "index.htm")

PAGE_SUFFIXES = (".html", ".htm")

# Attributes the browser fetches on its own, without the visitor doing anything.
# These are the ones that must resolve locally: a link the visitor clicks is
# loaded by the visitor's browser, which does have a network, but an asset is
# fetched while the page renders, from wherever the page is being rendered.
ASSET_ATTRIBUTES = {
    "script": ("src",),
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "track": ("src",),
    "iframe": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "input": ("src",),
}

# `link` is split out because only some rel values are fetched. A `rel="canonical"`
# or `rel="me"` pointing at a public URL is correct and must not be flagged.
FETCHED_LINK_RELS = {
    "stylesheet",
    "icon",
    "shortcut icon",
    "apple-touch-icon",
    "mask-icon",
    "manifest",
    "preload",
    "prefetch",
    "preconnect",
    "modulepreload",
}

NAVIGATION_ATTRIBUTES = {"a": ("href",), "area": ("href",), "form": ("action",)}

# Schemes that never point at a file in this directory and never need one.
NON_FILE_SCHEMES = ("mailto:", "tel:", "sms:", "javascript:", "data:", "blob:", "about:")

ABSOLUTE_URL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
PROTOCOL_RELATIVE = re.compile(r"^//")

CSS_URL = re.compile(r"""url\(\s*['"]?([^'")]+?)['"]?\s*\)""")
CSS_IMPORT = re.compile(r"""@import\s+['"]([^'"]+)['"]""")

PLACEHOLDER_TITLES = {"document", "untitled", "untitled document", "title", "new page", "page"}

# Below this a page is technically rendered and practically empty. Chosen to be
# well under any real page and well over a stray heading left behind by a
# half-finished generator run.
MIN_VISIBLE_CHARACTERS = 40

NON_RENDERED = {"script", "style", "template", "head", "title"}


class Report:
    """Findings for one file, kept separate so every message can name its source."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str, line: int | None = None) -> None:
        self.errors.append(self._locate(message, line))

    def warn(self, message: str, line: int | None = None) -> None:
        self.warnings.append(self._locate(message, line))

    def _locate(self, message: str, line: int | None) -> str:
        where = f"{self.path}:{line}" if line else self.path
        return f"{where}: {message}"


class Reference:
    __slots__ = ("url", "line", "tag", "attribute", "is_asset")

    def __init__(self, url: str, line: int, tag: str, attribute: str, is_asset: bool) -> None:
        self.url = url
        self.line = line
        self.tag = tag
        self.attribute = attribute
        self.is_asset = is_asset

    def describe(self) -> str:
        return f"<{self.tag} {self.attribute}>"


class PageParser(HTMLParser):
    """Pulls out everything the checks below need, in one pass over the document.

    Uses html.parser rather than a regex sweep because attribute order, quoting
    style and self-closing syntax all vary, and a regex that gets four of those
    right will silently miss the fifth on someone else's HTML.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_viewport = False
        self.has_charset = False
        self.title: str | None = None
        self.lang: str | None = None
        self.headings: list[str] = []
        self.identifiers: list[tuple[str, int]] = []
        self.references: list[Reference] = []
        self.images_without_alt: list[int] = []
        self.visible_text: list[str] = []
        self._capture_title = False
        self._suppressed = 0

    # --- collection -------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(tag, attrs)
        if tag in NON_RENDERED:
            self._suppressed += 1
        if tag == "title":
            self._capture_title = True
            self.title = self.title or ""

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in NON_RENDERED and self._suppressed:
            self._suppressed -= 1
        if tag == "title":
            self._capture_title = False

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self.title = (self.title or "") + data
        elif not self._suppressed:
            self.visible_text.append(data)

    # --- per-tag bookkeeping ----------------------------------------------------

    def _record(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line = self.getpos()[0]
        attributes = {name.lower(): (value or "") for name, value in attrs}

        if "id" in attributes and attributes["id"]:
            self.identifiers.append((attributes["id"], line))
        # A named anchor is still a valid fragment target in every browser.
        if tag == "a" and attributes.get("name"):
            self.identifiers.append((attributes["name"], line))

        if tag == "html":
            self.lang = attributes.get("lang")

        if tag == "meta":
            if "charset" in attributes:
                self.has_charset = True
            if attributes.get("http-equiv", "").lower() == "content-type":
                self.has_charset = True
            if attributes.get("name", "").lower() == "viewport":
                self.has_viewport = True

        if tag in ("h1", "h2", "h3"):
            self.headings.append(tag)

        if tag == "img" and "alt" not in attributes:
            self.images_without_alt.append(line)

        for attribute in ASSET_ATTRIBUTES.get(tag, ()):
            if attributes.get(attribute):
                self._add(attributes[attribute], line, tag, attribute, is_asset=True, split=attribute == "srcset")

        if tag == "link" and attributes.get("href"):
            rels = {value.lower() for value in attributes.get("rel", "").split()}
            joined = attributes.get("rel", "").strip().lower()
            fetched = bool(rels & FETCHED_LINK_RELS) or joined in FETCHED_LINK_RELS
            self._add(attributes["href"], line, tag, "href", is_asset=fetched, split=False)

        for attribute in NAVIGATION_ATTRIBUTES.get(tag, ()):
            if attributes.get(attribute):
                self._add(attributes[attribute], line, tag, attribute, is_asset=False, split=False)

    def _add(self, raw: str, line: int, tag: str, attribute: str, is_asset: bool, split: bool) -> None:
        # A srcset is a comma-separated list of "url descriptor" pairs; each url
        # is fetched independently, so each one has to be resolvable on its own.
        candidates = [part.strip().split()[0] for part in raw.split(",") if part.strip()] if split else [raw.strip()]
        for candidate in candidates:
            if candidate:
                self.references.append(Reference(candidate, line, tag, attribute, is_asset))


# --- resolution -------------------------------------------------------------------


def collect_files(root: str) -> set[str]:
    """Every file under root as a POSIX path relative to it."""
    found: set[str] = set()
    for directory, _, names in os.walk(root):
        for name in names:
            absolute = os.path.join(directory, name)
            found.add(os.path.relpath(absolute, root).replace(os.sep, "/"))
    return found


def resolve(page_path: str, target: str) -> str | None:
    """Where a relative reference from `page_path` lands, or None if it escapes."""
    base = posixpath.dirname(page_path)
    resolved = posixpath.normpath(posixpath.join(base, target))
    if resolved.startswith("..") or resolved == ".":
        return None
    return resolved


def candidates_for(resolved: str, files: set[str]) -> list[str]:
    """The paths a server would try for a reference, in order.

    A bare directory reference resolves to that directory's own entry point, so
    `href="posts/"` is not broken when `posts/index.html` exists.
    """
    if resolved in files:
        return [resolved]
    return [posixpath.join(resolved, entry) for entry in ENTRY_POINTS]


def check_page(
    page_path: str,
    text: str,
    files: set[str],
    anchors: dict[str, set[str]],
    strict: bool,
) -> tuple[Report, list[str]]:
    """Check one HTML page. Returns its report and the internal pages it links to."""
    report = Report(page_path)
    parser = PageParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:  # html.parser is lenient, but never assume
        report.error(f"could not be parsed as HTML: {exc}")
        return report, []

    # --- head requirements ------------------------------------------------------

    if parser.title is None:
        report.error(
            "no <title>. It is the browser tab, the bookmark, the search result and "
            "the first thing a screen reader announces — a page without one is "
            "unidentifiable everywhere it appears."
        )
    elif not parser.title.strip():
        report.error("<title> is empty")
    elif parser.title.strip().lower() in PLACEHOLDER_TITLES:
        report.warn(f"<title> is the placeholder {parser.title.strip()!r}")

    if not parser.has_viewport:
        report.error(
            'no <meta name="viewport">. Without it a phone lays the page out at '
            "980px and scales the result down, so every media query in the "
            "stylesheet is answered against a viewport the reader does not have."
        )

    if not parser.has_charset:
        report.warn(
            'no <meta charset="utf-8">. Browsers guess, and the guess is wrong for '
            "any page with non-ASCII text in it."
        )

    if not parser.lang:
        report.warn(
            "<html> has no lang attribute, so assistive technology has to guess "
            "which language to pronounce the page in."
        )

    if "h1" not in parser.headings:
        report.warn("no <h1>, so the page has no top-level heading to navigate by")
    elif parser.headings.count("h1") > 1:
        report.warn(f"{parser.headings.count('h1')} <h1> elements; a page should have one")

    # --- content ----------------------------------------------------------------

    visible = " ".join(parser.visible_text).strip()
    if not visible:
        report.error(
            "renders no text at all. The file is valid HTML containing nothing — "
            "check that the content actually reached the generator."
        )
    elif len(visible) < MIN_VISIBLE_CHARACTERS:
        report.warn(f"renders only {len(visible)} characters of text; it looks unfinished")

    for line in parser.images_without_alt:
        report.error(
            "<img> has no alt attribute. Add alt=\"...\" describing what the image "
            'conveys, or alt="" if it is purely decorative — but the attribute has '
            "to be there either way.",
            line,
        )

    duplicated = {}
    for identifier, line in parser.identifiers:
        duplicated.setdefault(identifier, []).append(line)
    for identifier, lines in duplicated.items():
        if len(lines) > 1:
            report.warn(
                f"id {identifier!r} is used {len(lines)} times (lines "
                f"{', '.join(str(line) for line in lines)}); fragment links to it "
                "reach only the first",
                lines[1],
            )

    # --- references -------------------------------------------------------------

    linked: list[str] = []

    for reference in parser.references:
        url = reference.url

        if url.startswith(NON_FILE_SCHEMES):
            continue

        if ABSOLUTE_URL.match(url) or PROTOCOL_RELATIVE.match(url):
            if reference.is_asset:
                report.error(
                    f"{reference.describe()} loads {url!r} from another host. The "
                    "sandbox has no network egress, so this cannot resolve and the "
                    "page renders without it. Inline the asset, or copy it into the "
                    "site directory and reference it relatively.",
                    reference.line,
                )
            # A plain <a> to a public URL is not an asset: it is opened later, by
            # the visitor's own browser, which does have a network. Left alone.
            continue

        if url.startswith("#"):
            fragment = url[1:]
            if fragment and fragment not in anchors.get(page_path, set()):
                report.error(
                    f"{url!r} points at an id nothing on this page declares, so it "
                    "scrolls to the top instead of the section it names.",
                    reference.line,
                )
            continue

        target, _, fragment = url.partition("#")
        target = target.split("?", 1)[0]
        if not target:
            continue

        if target.startswith("/"):
            # Root-absolute paths resolve against the server root. A promoted site
            # is served under a version prefix, so the root is not this directory.
            stripped = target.lstrip("/")
            if stripped in files:
                report.warn(
                    f"{reference.describe()} uses the root-absolute path {url!r}. The "
                    "file exists, but a promoted site is served under a version "
                    f"prefix, so this resolves outside it. Use a relative path.",
                    reference.line,
                )
            else:
                report.error(
                    f"{reference.describe()} references {url!r}, which is root-absolute "
                    "and matches no file in the site. A promoted site is not served "
                    "from /; use a path relative to the page.",
                    reference.line,
                )
            continue

        resolved = resolve(page_path, target)
        if resolved is None:
            report.error(
                f"{reference.describe()} references {url!r}, which resolves outside "
                "the site directory. Nothing above the site root is published.",
                reference.line,
            )
            continue

        options = candidates_for(resolved, files)
        found = next((option for option in options if option in files), None)

        if found is None:
            kind = "asset" if reference.is_asset else "link"
            report.error(
                f"broken {kind}: {reference.describe()} references {url!r}, which "
                f"resolves to {resolved!r} — no such file in the site.",
                reference.line,
            )
            continue

        if fragment and found.endswith(PAGE_SUFFIXES):
            if fragment not in anchors.get(found, set()):
                report.error(
                    f"{url!r} points at {found!r}, which declares no id {fragment!r}.",
                    reference.line,
                )

        if not reference.is_asset and found.endswith(PAGE_SUFFIXES):
            linked.append(found)

    return report, linked


def check_stylesheet(css_path: str, text: str, files: set[str]) -> Report:
    report = Report(css_path)

    for match in list(CSS_IMPORT.finditer(text)) + list(CSS_URL.finditer(text)):
        url = match.group(1).strip()
        line = text.count("\n", 0, match.start()) + 1

        if url.startswith(("data:", "#")):
            continue

        if ABSOLUTE_URL.match(url) or PROTOCOL_RELATIVE.match(url):
            report.error(
                f"references {url!r} on another host. The sandbox has no network "
                "egress, so this never loads — a remote font here means the page "
                "renders in a fallback face, silently.",
                line,
            )
            continue

        if url.startswith("/"):
            report.error(
                f"references the root-absolute path {url!r}. A promoted site is "
                "served under a version prefix, so this resolves outside it.",
                line,
            )
            continue

        resolved = resolve(css_path, url)
        if resolved is None or resolved not in files:
            report.error(
                f"references {url!r}, which resolves to "
                f"{resolved or 'a path outside the site'} — no such file in the site.",
                line,
            )

    return report


def read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (UnicodeDecodeError, OSError):
        return None


def collect_anchors(root: str, pages: list[str]) -> dict[str, set[str]]:
    """Every fragment target on every page, so cross-page fragments can be checked."""
    anchors: dict[str, set[str]] = {}
    for page in pages:
        text = read_text(os.path.join(root, *page.split("/")))
        if text is None:
            anchors[page] = set()
            continue
        parser = PageParser()
        try:
            parser.feed(text)
            parser.close()
        except Exception:
            anchors[page] = set()
            continue
        anchors[page] = {identifier for identifier, _ in parser.identifiers}
    return anchors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_site.py",
        description=(
            "Verify a directory is a servable static website: an entry point at the "
            "top, every link and asset resolving to a file that exists, no remote "
            "assets, and the accessibility basics on every page."
        ),
        epilog=(
            "Example:\n"
            "  python3 validate_site.py ./site\n\n"
            "Exit code 0 means the site is deliverable. Exit code 1 means do not hand "
            "it to the user.\n"
            "Runs offline, stdlib only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("site", help="path to the site directory to check")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as failures (exit 1)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.site

    if not os.path.exists(root):
        print(f"ERROR: site: {root} does not exist", file=sys.stderr)
        return 1
    if not os.path.isdir(root):
        print(
            f"ERROR: site: {root} is a file, expected a directory. A website is a "
            "tree, and it is the tree that gets promoted.",
            file=sys.stderr,
        )
        return 1

    files = collect_files(root)
    if not files:
        print(f"ERROR: site: {root} is empty", file=sys.stderr)
        return 1

    # --- the entry point --------------------------------------------------------
    #
    # Reported before anything else, and separately for the nested case. Telling
    # someone whose tree holds site/index.html that there is "no index.html" sends
    # them looking for a file they already wrote; the fix is to promote one
    # directory lower, and the message has to say so.

    entry = next((name for name in ENTRY_POINTS if name in files), None)

    if entry is None:
        nested = sorted(
            path for path in files if posixpath.basename(path).lower() in ENTRY_POINTS
        )
        if nested:
            directory = posixpath.dirname(nested[0])
            print(
                f"ERROR: {root}: a website needs its entry point at the top of what "
                f"is promoted, but the only one here is {nested[0]!r}. Move the "
                f"contents of {directory!r} up to {root!r}, or promote {directory!r} "
                "itself rather than the directory above it.",
                file=sys.stderr,
            )
        else:
            print(
                f"ERROR: {root}: no index.html at the top of the directory. A website "
                "is served by its entry point, and without one the published preview "
                "resolves to nothing. Add index.html, or promote this as kind "
                "'bundle' if it is not a site.",
                file=sys.stderr,
            )
        return 1

    pages = sorted(path for path in files if path.lower().endswith(PAGE_SUFFIXES))
    stylesheets = sorted(path for path in files if path.lower().endswith(".css"))

    anchors = collect_anchors(root, pages)

    reports: list[Report] = []
    linked_from_anywhere: set[str] = {entry}

    for page in pages:
        text = read_text(os.path.join(root, *page.split("/")))
        if text is None:
            report = Report(page)
            report.error("is not readable as UTF-8 text, so a browser cannot render it")
            reports.append(report)
            continue
        report, linked = check_page(page, text, files, anchors, args.strict)
        reports.append(report)
        linked_from_anywhere.update(linked)

    for stylesheet in stylesheets:
        text = read_text(os.path.join(root, *stylesheet.split("/")))
        if text is None:
            report = Report(stylesheet)
            report.error("is not readable as UTF-8 text")
            reports.append(report)
            continue
        reports.append(check_stylesheet(stylesheet, text, files))

    # A page nothing links to is published and unreachable. Not an error — a
    # deliberately unlisted page is a real thing — but it is almost always a nav
    # entry somebody forgot to add.
    for page in pages:
        if page not in linked_from_anywhere:
            orphan = Report(page)
            orphan.warn("is not linked from any other page, so nothing leads a visitor to it")
            reports.append(orphan)

    errors = [message for report in reports for message in report.errors]
    warnings = [message for report in reports for message in report.warnings]

    for message in warnings:
        print(f"WARNING: {message}", file=sys.stderr)
    for message in errors:
        print(f"ERROR: {message}", file=sys.stderr)

    if errors:
        affected = len({report.path for report in reports if report.errors})
        print(
            f"\n{len(errors)} problem(s) across {affected} file(s). The site is not "
            "deliverable until they are fixed.",
            file=sys.stderr,
        )
        return 1

    if warnings and args.strict:
        print(f"\n{len(warnings)} warning(s), failed by --strict.", file=sys.stderr)
        return 1

    total_bytes = sum(
        os.path.getsize(os.path.join(root, *path.split("/"))) for path in files
    )
    print(
        f"OK: {root} is a servable site, entry point {entry}, {len(pages)} page(s), "
        f"{len(files)} file(s), {total_bytes} bytes"
    )
    for page in pages:
        report = next((item for item in reports if item.path == page), None)
        note = f", {len(report.warnings)} warning(s)" if report and report.warnings else ""
        print(f"  {page}: {len(anchors.get(page, set()))} anchor(s){note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
