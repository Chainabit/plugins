#!/usr/bin/env python3
"""Build a React page into ONE self-contained HTML file, offline.

The page is bundled with the toolchain the host provides (React, ReactDOM and
esbuild), so nothing is fetched at build time and the finished file loads
nothing from another address when it is opened or previewed: its JavaScript and
CSS are inline, and images and fonts are `data:` URIs.

Exit codes: 0 built; 1 input error (bad arguments or entry); 2 a declared
runtime asset is missing (the toolchain or the fonts); 3 the bundle or the
finished page failed its checks.

The last line of stdout is a JSON evidence record
(`chainabit.react-page.build/v1`) carrying the exact SHA-256 and byte count of
the file that was written.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from html import escape
from html.parser import HTMLParser
from pathlib import Path

PROTOCOL = "chainabit.react-page.build/v1"

TOOLCHAIN_ENV = "CHAINABIT_WEB_TOOLCHAIN_DIR"
TOOLCHAIN_DEFAULT = "/opt/chainabit/web-toolchain"
FONT_ENV = "CHAINABIT_ARTIFACT_FONT_DIR"
FONT_DEFAULT = "/opt/chainabit/artifact-fonts/ibm-plex-sans"

# Kept under the 5 MiB single-file page contract so an accepted build is never
# refused at delivery for its size.
MAX_PAGE_BYTES = 4 * 1024 * 1024
BUILD_TIMEOUT_SECONDS = 120

ENTRY_SUFFIXES = (".jsx", ".tsx", ".js", ".ts")
# Local assets a component may import; each becomes a data: URI in the bundle.
DATA_URL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg", ".woff2")

LATIN_FONTS = (
    ("IBM Plex Sans", "IBMPlexSans-Regular.woff2", 400),
    ("IBM Plex Sans", "IBMPlexSans-SemiBold.woff2", 600),
)
ARABIC_FONTS = (
    ("IBM Plex Sans Arabic", "IBMPlexSansArabic-Regular.woff2", 400),
    ("IBM Plex Sans Arabic", "IBMPlexSansArabic-SemiBold.woff2", 600),
)
ARABIC_SCRIPT_LANGUAGES = frozenset({"ar", "fa", "ur", "ps", "sd", "ug"})
RTL_LANGUAGES = ARABIC_SCRIPT_LANGUAGES | frozenset({"he", "yi", "dv", "prs"})

# Elements that load a resource, and the attributes that name it.
RESOURCE_ATTRIBUTES = {
    "script": ("src",),
    "link": ("href",),
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "iframe": ("src",),
    "frame": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "audio": ("src",),
    "video": ("src", "poster"),
    "track": ("src",),
}
LOCAL_REFERENCE_PREFIXES = ("data:", "#")


class BuildError(Exception):
    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class Parser(argparse.ArgumentParser):
    # argparse exits 2 on a bad argument; 2 is this tool's "runtime asset
    # missing", so an input error is reported as 1 instead.
    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        raise SystemExit(1)


def valid_language_tag(tag: str) -> bool:
    parts = tag.split("-")
    if not tag or len(tag) > 35 or not all(parts):
        return False
    if not (parts[0].isascii() and parts[0].isalpha() and len(parts[0]) in (2, 3, 5, 6, 7, 8)):
        return False
    return all(part.isascii() and part.isalnum() and len(part) <= 8 for part in parts[1:])


def text_direction(lang: str) -> str:
    return "rtl" if lang.split("-")[0].lower() in RTL_LANGUAGES else "ltr"


def resolve_toolchain() -> tuple[Path, Path, dict[str, str]]:
    root = Path(os.environ.get(TOOLCHAIN_ENV) or TOOLCHAIN_DEFAULT)
    modules = root / "node_modules"
    esbuild = modules / ".bin" / "esbuild"
    if not esbuild.is_file() or not os.access(esbuild, os.X_OK):
        raise BuildError(
            2,
            f"runtime asset web.toolchain is missing: no esbuild under {root}. "
            f"Set {TOOLCHAIN_ENV} to the toolchain directory. This build has no "
            "network fallback, so do not load React from a CDN instead.",
        )
    versions: dict[str, str] = {}
    for package in ("react", "react-dom", "esbuild"):
        try:
            manifest = json.loads((modules / package / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise BuildError(2, f"runtime asset web.toolchain is incomplete: {package} is not installed under {root}.")
        versions[package] = str(manifest.get("version", "unknown"))
    return esbuild, modules, versions


def load_fonts(lang: str, font_dir: Path) -> list[tuple[str, int, str]]:
    wanted = LATIN_FONTS
    if lang.split("-")[0].lower() in ARABIC_SCRIPT_LANGUAGES:
        wanted = wanted + ARABIC_FONTS
    missing = [name for _, name, _ in wanted if not (font_dir / name).is_file()]
    if missing:
        raise BuildError(
            2,
            f"runtime asset fonts.ibm-plex is missing: {', '.join(missing)} not found in {font_dir}. "
            f"Set {FONT_ENV}, or pass --font none to use the system font stack.",
        )
    return [
        (family, weight, base64.b64encode((font_dir / name).read_bytes()).decode("ascii"))
        for family, name, weight in wanted
    ]


def font_face_css(fonts: list[tuple[str, int, str]]) -> str:
    return "".join(
        f'@font-face{{font-family:"{family}";font-style:normal;font-weight:{weight};font-display:swap;'
        f'src:url(data:font/woff2;base64,{data}) format("woff2")}}'
        for family, weight, data in fonts
    )


def base_css(fonts: list[tuple[str, int, str]]) -> str:
    families = ['"IBM Plex Sans"'] if fonts else []
    if any(family == "IBM Plex Sans Arabic" for family, _, _ in fonts):
        families.append('"IBM Plex Sans Arabic"')
    stack = ",".join(families + ["system-ui", "-apple-system", '"Segoe UI"', "Roboto", "sans-serif"])
    return f"html{{-webkit-text-size-adjust:100%}}body{{margin:0;font-family:{stack}}}"


def bundle(entry: Path, esbuild: Path, modules: Path) -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix="react-page-") as scratch:
        work = Path(scratch)
        # The mount file is generated so the page's source is only its own
        # components: the default export of the entry is the root component.
        (work / "mount.jsx").write_text(
            "import { createRoot } from 'react-dom/client';\n"
            f"import App from {json.dumps(entry.as_posix())};\n"
            "createRoot(document.getElementById('root')).render(<App />);\n",
            encoding="utf-8",
        )
        command = [
            str(esbuild),
            "mount.jsx",
            "--bundle",
            "--minify",
            "--format=iife",
            "--target=es2020",
            "--jsx=automatic",
            "--charset=utf8",
            "--legal-comments=none",
            '--define:process.env.NODE_ENV="production"',
            "--log-level=error",
            "--outfile=out.js",
            *(f"--loader:{suffix}=dataurl" for suffix in DATA_URL_SUFFIXES),
        ]
        # esbuild resolves `react` and `react-dom` from here, whatever directory
        # the page's own source lives in.
        env = {**os.environ, "NODE_PATH": str(modules), "NO_COLOR": "1"}
        try:
            result = subprocess.run(
                command, cwd=work, env=env, capture_output=True, text=True, timeout=BUILD_TIMEOUT_SECONDS, check=False
            )
        except subprocess.TimeoutExpired:
            raise BuildError(3, f"the bundle did not finish within {BUILD_TIMEOUT_SECONDS}s.")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:2000]
            raise BuildError(3, f"the bundle failed:\n{detail}")
        css_path = work / "out.css"
        css = css_path.read_text(encoding="utf-8") if css_path.is_file() else ""
        return (work / "out.js").read_text(encoding="utf-8"), css


def neutralize_script(js: str) -> str:
    # Inside <script>, `</script` ends the element and `<!--` opens an escaped
    # state; both are harmless once the character after `<` is escaped, in a
    # string and in a regular expression alike.
    return js.replace("</script", "<\\/script").replace("<!--", "<\\!--")


def assemble(lang: str, title: str, description: str, css: str, js: str) -> str:
    head = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(title)}</title>",
    ]
    if description:
        head.append(f'<meta name="description" content="{escape(description, quote=True)}">')
    head.append(f"<style>{css}</style>")
    return (
        "<!doctype html>\n"
        f'<html lang="{escape(lang, quote=True)}" dir="{text_direction(lang)}">\n'
        f"<head>\n{chr(10).join(head)}\n</head>\n"
        f'<body>\n<div id="root"></div>\n<script>{neutralize_script(js)}</script>\n</body>\n</html>\n'
    )


class PageAudit(HTMLParser):
    """Findings for what the page loads, judged the way a browser reads it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.findings: list[str] = []
        self.lang = ""
        self.scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: (value or "") for name, value in attrs}
        if tag == "html":
            self.lang = values.get("lang", "").strip()
        if tag == "base":
            self.findings.append("a <base> element changes how every reference resolves")
        if tag == "script":
            self.scripts += 1
        if tag == "img" and "alt" not in values:
            self.findings.append("an <img> has no alt attribute")
        for attribute in RESOURCE_ATTRIBUTES.get(tag, ()):
            reference = values.get(attribute, "").strip()
            if reference and not reference.lower().startswith(LOCAL_REFERENCE_PREFIXES):
                self.findings.append(f"<{tag} {attribute}> loads {reference[:60]!r} from outside the file")


def css_remote_references(css: str) -> list[str]:
    """`url(...)` and `@import` targets that are not `data:` URIs."""
    found: list[str] = []
    lowered = css.lower()
    cursor = 0
    while (start := lowered.find("url(", cursor)) != -1:
        end = css.find(")", start)
        if end == -1:
            found.append("an unterminated url(")
            break
        target = css[start + 4 : end].strip().strip("'\"")
        if target and not target.lower().startswith(LOCAL_REFERENCE_PREFIXES):
            found.append(f"CSS url() loads {target[:60]!r} from outside the file")
        cursor = end
    if "@import" in lowered:
        found.append("CSS @import loads a stylesheet from outside the file")
    return found


def audit(document: str, css: str) -> list[str]:
    parser = PageAudit()
    parser.feed(document)
    parser.close()
    findings = list(parser.findings)
    if not parser.lang:
        findings.append("<html> has no lang attribute")
    if parser.scripts != 1:
        findings.append(f"expected one inline <script>, found {parser.scripts}")
    findings.extend(css_remote_references(css))
    if "</style" in css.lower():
        findings.append("the CSS contains a closing </style> tag")
    return findings


def build(args: argparse.Namespace) -> dict[str, object]:
    entry = Path(args.entry).resolve()
    if not entry.is_file() or entry.suffix not in ENTRY_SUFFIXES:
        raise BuildError(1, f"--entry must be an existing {'/'.join(ENTRY_SUFFIXES)} file: {args.entry}")
    out = Path(args.out)
    if out.suffix.lower() != ".html":
        raise BuildError(1, f"--out must be a .html file: {args.out}")
    if not valid_language_tag(args.lang):
        raise BuildError(1, f"--lang must be a BCP-47 language tag such as en, tr or ar-EG: {args.lang!r}")
    if not args.title.strip():
        raise BuildError(1, "--title must not be empty")

    esbuild, modules, versions = resolve_toolchain()
    fonts = load_fonts(args.lang, Path(args.font_dir or os.environ.get(FONT_ENV) or FONT_DEFAULT)) if args.font == "ibm-plex" else []

    js, page_css = bundle(entry, esbuild, modules)
    css = font_face_css(fonts) + base_css(fonts) + page_css
    document = assemble(args.lang, args.title.strip(), args.description.strip(), css, js)

    findings = audit(document, css)
    payload = document.encode("utf-8")
    if len(payload) > MAX_PAGE_BYTES:
        findings.append(f"the page is {len(payload)} bytes; the limit is {MAX_PAGE_BYTES}")
    if findings:
        raise BuildError(3, "the page failed its checks:\n- " + "\n- ".join(findings))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    return {
        "protocol": PROTOCOL,
        "status": "ok",
        "output": {
            "path": out.as_posix(),
            "shape": "file",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        "page": {"lang": args.lang, "dir": text_direction(args.lang), "title": args.title.strip(), "font": args.font},
        "toolchain": versions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = Parser(description="Build a React page into one self-contained HTML file, offline.")
    parser.add_argument("--entry", required=True, help="component file whose default export is the page's root component")
    parser.add_argument("--out", required=True, help="the .html file to write")
    parser.add_argument("--lang", required=True, help="content language, a BCP-47 tag; becomes <html lang> and sets dir")
    parser.add_argument("--title", required=True, help="document title")
    parser.add_argument("--description", default="", help="optional meta description")
    parser.add_argument("--font", choices=("ibm-plex", "none"), default="ibm-plex", help="embed IBM Plex Sans, or use the system font stack")
    parser.add_argument("--font-dir", default="", help=f"font directory (default: ${FONT_ENV})")
    args = parser.parse_args(argv)
    try:
        record = build(args)
    except BuildError as failure:
        sys.stderr.write(f"react-page: {failure}\n")
        return failure.exit_code
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
