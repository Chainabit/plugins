from __future__ import annotations

"""Capability registry and isolated renderer/manipulator adapters."""
import importlib
import importlib.util
import json
import os
import subprocess
from functools import lru_cache
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ErrorCode, PdfError
from .models import PageGeometry

CAPABILITY_NAMES = ("ascii_basic_text", "unicode", "font_embedding", "turkish", "rtl", "cjk", "images", "tables", "rich_markdown", "pagination", "page_breaks", "headers_footers", "math", "vector_graphics", "complex_typography", "colors_rgb", "colors_cmyk", "print_quality", "metadata", "deterministic", "manipulation")

@dataclass(frozen=True)
class BackendCapabilities:
    name: str
    available: bool
    version: str | None
    supports: frozenset[str]
    operations: tuple[str, ...]
    restrictions: tuple[str, ...] = ()
    dependency: str | None = None
    detail: str = ""
    def missing(self, required: frozenset[str]) -> frozenset[str]: return required - self.supports

@dataclass(frozen=True)
class CapabilityReport:
    backend: str
    selected: bool
    available: bool
    version: str | None
    supports: tuple[str, ...]
    operations: tuple[str, ...]
    restrictions: tuple[str, ...]
    missing: tuple[str, ...] = ()
    reason: str = ""

def _version(name: str) -> str | None:
    try: return str(getattr(importlib.import_module(name), "__version__", "unknown"))
    except Exception: return None

@lru_cache(maxsize=None)
def _dependency(name: str) -> tuple[bool, str | None]:
    """Import-probe native dependencies; module discovery alone is a false pass."""
    if importlib.util.find_spec(name) is None: return False, None
    try:
        module = importlib.import_module(name)
        return True, str(getattr(module, "__version__", "unknown"))
    except Exception:
        # WeasyPrint can be importable as Python source while Pango/Cairo is
        # absent. That is a missing runtime dependency, not a renderer crash.
        return False, None

# WHY `html_css` IS NOT ADVERTISED, EVEN THOUGH WeasyPrint HAS IT.
#
# A capability report is a promise about what a CALLER can ask for, not an
# inventory of what a library can do. No registered generator on this skill
# accepts HTML or CSS: `md_to_pdf.py` takes Markdown, `report_pdf.py` takes a
# fixed JSON block schema, and both funnel through one immutable stylesheet in
# `service._html_document`. `--css` exists as a flag and always errors.
#
# Advertising `html_css` sent agents down a road that does not exist: they
# would write an HTML document, have `reject_active_markup` refuse it as
# unsafe input, fall back to invoking `weasyprint` directly, and then be unable
# to promote the result because an unregistered wrapper cannot acquire
# production proof. The flag was also unreachable by construction --
# `DocumentRequirements.infer` never emits it, so nothing could ever select on
# it. Removing it makes the probe honest; restoring it requires a registered,
# tested HTML entrypoint, not a change here.

def capability_registry() -> list[BackendCapabilities]:
    dependencies = {n: _dependency(n) for n in ("weasyprint", "reportlab", "pypdf", "PIL")}
    flags = {name: state[0] for name, state in dependencies.items()}
    return [
        BackendCapabilities("weasyprint", flags["weasyprint"], dependencies["weasyprint"][1], frozenset({"ascii_basic_text", "unicode", "font_embedding", "turkish", "rtl", "cjk", "images", "tables", "rich_markdown", "pagination", "page_breaks", "headers_footers", "math", "complex_typography", "colors_rgb", "print_quality", "metadata"}) if flags["weasyprint"] else frozenset(), ("generate-markdown", "generate-report"), ("network disabled", "active HTML/SVG/remote assets blocked"), "weasyprint", "isolated print renderer for this skill's Markdown and report inputs"),
        BackendCapabilities("reportlab", flags["reportlab"], dependencies["reportlab"][1], frozenset({"ascii_basic_text", "unicode", "font_embedding", "turkish", "images", "tables", "rich_markdown", "pagination", "page_breaks", "headers_footers", "colors_rgb", "print_quality", "metadata"}) if flags["reportlab"] else frozenset(), ("generate-report",), ("RTL/CJK/math require another tested adapter",), "reportlab", "programmatic structured-layout adapter"),
        BackendCapabilities("pypdf", flags["pypdf"], dependencies["pypdf"][1], frozenset({"manipulation", "metadata"}) if flags["pypdf"] else frozenset(), ("merge", "extract", "remove", "reorder", "rotate", "crop"), ("does not render documents",), "pypdf", "PDF manipulation adapter"),
        BackendCapabilities("pillow", flags["PIL"], dependencies["PIL"][1], frozenset({"images"}) if flags["PIL"] else frozenset(), ("normalize-image",), ("does not render PDFs",), "Pillow", "bounded image normalization adapter"),
    ]

def capabilities() -> list[CapabilityReport]:
    return [CapabilityReport(c.name, False, c.available, c.version, tuple(sorted(c.supports)), c.operations, c.restrictions, reason=c.detail) for c in capability_registry()]

class PdfRenderer(ABC):
    capabilities: BackendCapabilities
    @abstractmethod
    def render(self, document: Any, geometry: PageGeometry, metadata: dict[str, str], destination: Path, policy: Any) -> None: ...

class WeasyPrintRenderer(PdfRenderer):
    capabilities = next(c for c in capability_registry() if c.name == "weasyprint")
    def render(self, document: str, geometry: PageGeometry, metadata: dict[str, str], destination: Path, policy: Any) -> None:
        if not self.capabilities.available: raise PdfError(ErrorCode.DEPENDENCY_UNAVAILABLE, "professional HTML/CSS rendering requires optional dependency 'weasyprint'")
        try:
            from weasyprint import HTML
            from weasyprint.urls import URLFetcher
            restricted_fetcher = URLFetcher(
                allowed_protocols=("data",),
                allow_redirects=False,
                fail_on_errors=True,
            )
            def fetch(url: str, *args: Any, **kwargs: Any) -> Any:
                if not url.startswith("data:"): raise PdfError(ErrorCode.UNSAFE_INPUT, "network and file assets are disabled for HTML rendering")
                return restricted_fetcher.fetch(url, *args, **kwargs)
            HTML(string=document, url_fetcher=fetch).write_pdf(str(destination), presentational_hints=False)
        except PdfError: raise
        except Exception as exc: raise PdfError(ErrorCode.RENDERING_FAILURE, "WeasyPrint failed to render the document") from exc

class ReportLabRenderer(PdfRenderer):
    capabilities = next(c for c in capability_registry() if c.name == "reportlab")
    def render(self, document: dict, geometry: PageGeometry, metadata: dict[str, str], destination: Path, policy: Any) -> None:
        if not self.capabilities.available: raise PdfError(ErrorCode.DEPENDENCY_UNAVAILABLE, "structured report rendering requires optional dependency 'reportlab'")
        try:
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_LEFT
            from reportlab.lib.pagesizes import landscape, portrait
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import inch
            from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            requested = str(document.get("font") or os.environ.get("CHAINABIT_ARTIFACT_FONT_FAMILY", "IBM Plex Sans"))
            font_root = Path(os.environ.get("CHAINABIT_ARTIFACT_FONT_DIR", "/opt/chainabit/artifact-fonts/ibm-plex-sans"))
            if requested == "IBM Plex Sans":
                regular = font_root / "IBMPlexSans-Regular.ttf"
                semibold = font_root / "IBMPlexSans-SemiBold.ttf"
            else:
                def resolve(style: str) -> Path:
                    result = subprocess.run(["fc-match", "-f", "%{family}\n%{file}\n", f"{requested}:style={style}"], capture_output=True, text=True, timeout=5, check=False)
                    lines = result.stdout.splitlines()
                    if result.returncode or len(lines) < 2 or requested.casefold() not in lines[0].casefold():
                        raise PdfError(ErrorCode.INVALID_INPUT, f"requested font {requested!r} is unavailable")
                    return Path(lines[1])
                regular, semibold = resolve("Regular"), resolve("Semibold")
            if not regular.is_file() or not semibold.is_file():
                raise PdfError(ErrorCode.FONT_FAILURE, "approved artifact font assets are unavailable")
            pdfmetrics.registerFont(TTFont("ChainabitArtifact", str(regular)))
            pdfmetrics.registerFont(TTFont("ChainabitArtifactSemiBold", str(semibold)))
            styles = getSampleStyleSheet(); body = ParagraphStyle("body", parent=styles["BodyText"], fontName="ChainabitArtifact", leading=14); heading = ParagraphStyle("heading", parent=styles["Heading2"], fontName="ChainabitArtifactSemiBold")
            size = (geometry.width, geometry.height); doc = SimpleDocTemplate(str(destination), pagesize=size, leftMargin=geometry.margin[3], rightMargin=geometry.margin[1], topMargin=geometry.margin[0], bottomMargin=geometry.margin[2], title=metadata.get("Title", ""), author=metadata.get("Author", ""))
            title_style = ParagraphStyle("artifactTitle", parent=styles["Title"], fontName="ChainabitArtifactSemiBold")
            flow = [Paragraph(escape_report(document["title"]), title_style)]
            for block in document["blocks"]:
                kind = block["type"]
                if kind == "heading": flow.append(Paragraph(escape_report(block["text"]), heading))
                elif kind == "paragraph": flow.append(Paragraph(escape_report(block["text"]), body))
                elif kind in {"bullets", "numbered"}: flow.extend(Paragraph(("• " if kind == "bullets" else "1. ") + escape_report(item), body) for item in block["items"])
                elif kind == "table":
                    rows = [[Paragraph(escape_report(str(x)), body) for x in block["columns"]]] + [[Paragraph(escape_report(str(x)), body) for x in row] for row in block["rows"]]
                    table = Table(rows, repeatRows=1, hAlign="LEFT"); table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f3f4f6")), ("GRID", (0,0), (-1,-1), .5, colors.HexColor("#d1d5db")), ("VALIGN", (0,0), (-1,-1), "TOP"), ("LEFTPADDING", (0,0), (-1,-1), 5)])); flow.append(table)
                elif kind == "image":
                    from .safety import validate_image
                    image_path = (policy.input_root / str(block["path"])).resolve(); validate_image(image_path, policy)
                    flow.append(Image(str(image_path), width=min(geometry.width - geometry.margin[1] - geometry.margin[3], 400), height=200, kind="proportional"))
                elif kind == "spacer": flow.append(Spacer(1, float(block.get("height", 12))))
                elif kind == "pagebreak": flow.append(PageBreak())
            doc.build(flow)
        except PdfError: raise
        except Exception as exc: raise PdfError(ErrorCode.RENDERING_FAILURE, "ReportLab failed to render the structured report") from exc

def json_text(value: Any) -> str:
    if isinstance(value, dict): return " ".join(json_text(v) for v in value.values())
    if isinstance(value, list): return " ".join(json_text(v) for v in value)
    return str(value)

def escape_report(value: str) -> str:
    from xml.sax.saxutils import escape
    return escape(value)

class PypdfManipulator:
    def _module(self):
        try: return importlib.import_module("pypdf")
        except ImportError as exc: raise PdfError(ErrorCode.DEPENDENCY_UNAVAILABLE, "PDF manipulation requires optional dependency 'pypdf'") from exc
    def manipulate(self, operation: str, inputs: list[Path], destination: Path, options: dict) -> None:
        pypdf = self._module(); writer = pypdf.PdfWriter()
        if operation == "merge":
            for source in inputs:
                for page in pypdf.PdfReader(str(source), strict=True).pages: writer.add_page(page)
        elif operation in {"extract", "remove", "reorder", "rotate", "crop"}:
            if len(inputs) != 1: raise PdfError(ErrorCode.INVALID_INPUT, f"{operation} requires exactly one input PDF")
            reader = pypdf.PdfReader(str(inputs[0]), strict=True); selected = options.get("pages", [])
            if operation == "remove": selected = [i for i in range(len(reader.pages)) if i not in set(selected)]
            if not selected: raise PdfError(ErrorCode.INVALID_INPUT, "operation requires non-empty page selection")
            for index in selected:
                if not isinstance(index, int) or index < 0 or index >= len(reader.pages): raise PdfError(ErrorCode.INVALID_INPUT, "page index is out of range")
                page = reader.pages[index]
                if operation == "rotate": page.rotate(int(options.get("degrees", 0)))
                if operation == "crop": page.cropbox.lower_left = (options["box"]["left"], options["box"]["bottom"]); page.cropbox.upper_right = (options["box"]["right"], options["box"]["top"])
                writer.add_page(page)
        else: raise PdfError(ErrorCode.UNSUPPORTED_CAPABILITY, f"unsupported manipulation operation: {operation}")
        with destination.open("wb") as handle: writer.write(handle)
