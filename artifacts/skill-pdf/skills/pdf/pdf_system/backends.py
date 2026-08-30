from __future__ import annotations

"""Capability registry and isolated renderer/manipulator adapters."""
import importlib
import importlib.util
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ErrorCode, PdfError
from .models import PageGeometry

CAPABILITY_NAMES = ("ascii_basic_text", "unicode", "font_embedding", "turkish", "rtl", "cjk", "images", "tables", "rich_markdown", "html_css", "pagination", "page_breaks", "headers_footers", "math", "vector_graphics", "complex_typography", "colors_rgb", "colors_cmyk", "print_quality", "metadata", "deterministic", "manipulation")

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

def capability_registry() -> list[BackendCapabilities]:
    flags = {n: importlib.util.find_spec(n) is not None for n in ("weasyprint", "reportlab", "pypdf", "PIL")}
    return [
        BackendCapabilities("deterministic-text", True, "1", frozenset({"ascii_basic_text", "pagination", "page_breaks", "metadata", "deterministic"}), ("generate-basic",), ("ASCII printable text only", "no semantic tables/images/math/rich layout"), detail="dependency-free minimal fallback"),
        BackendCapabilities("weasyprint", flags["weasyprint"], _version("weasyprint") if flags["weasyprint"] else None, frozenset({"ascii_basic_text", "unicode", "font_embedding", "turkish", "rtl", "cjk", "images", "tables", "rich_markdown", "html_css", "pagination", "page_breaks", "headers_footers", "math", "complex_typography", "colors_rgb", "print_quality", "metadata"}) if flags["weasyprint"] else frozenset(), ("generate-markdown", "generate-report"), ("network disabled", "active HTML/SVG/remote assets blocked"), "weasyprint", "isolated HTML/CSS professional adapter"),
        BackendCapabilities("reportlab", flags["reportlab"], _version("reportlab") if flags["reportlab"] else None, frozenset({"ascii_basic_text", "unicode", "font_embedding", "turkish", "images", "tables", "rich_markdown", "pagination", "page_breaks", "headers_footers", "colors_rgb", "print_quality", "metadata"}) if flags["reportlab"] else frozenset(), ("generate-report",), ("RTL/CJK/math require another tested adapter",), "reportlab", "programmatic structured-layout adapter"),
        BackendCapabilities("pypdf", flags["pypdf"], _version("pypdf") if flags["pypdf"] else None, frozenset({"manipulation", "metadata"}) if flags["pypdf"] else frozenset(), ("merge", "extract", "remove", "reorder", "rotate", "crop"), ("does not render documents",), "pypdf", "PDF manipulation adapter"),
        BackendCapabilities("pillow", flags["PIL"], _version("PIL") if flags["PIL"] else None, frozenset({"images"}) if flags["PIL"] else frozenset(), ("normalize-image",), ("does not render PDFs",), "Pillow", "bounded image normalization adapter"),
    ]

def capabilities() -> list[CapabilityReport]:
    return [CapabilityReport(c.name, False, c.available, c.version, tuple(sorted(c.supports)), c.operations, c.restrictions, reason=c.detail) for c in capability_registry()]

class PdfRenderer(ABC):
    capabilities: BackendCapabilities
    @abstractmethod
    def render(self, document: Any, geometry: PageGeometry, metadata: dict[str, str], destination: Path, policy: Any) -> None: ...

def _pdf_escape(text: str) -> str: return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

class DeterministicTextRenderer(PdfRenderer):
    capabilities = capability_registry()[0]
    def render(self, pages: list[list[str]], geometry: PageGeometry, metadata: dict[str, str], destination: Path, policy: Any = None) -> None:
        if any(any(ord(c) < 32 and c not in "\t" or ord(c) > 126 for c in line) for page in pages for line in page):
            raise PdfError(ErrorCode.FONT_FAILURE, "minimal renderer accepts ASCII printable text only")
        objects: list[bytes] = []
        def add(value: str | bytes) -> int: objects.append(value.encode("ascii") if isinstance(value, str) else value); return len(objects)
        pages_ref = add(""); font_ref = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"); page_refs = []
        for lines in pages:
            y = geometry.height - geometry.margin[0]; stream = ["BT", "/F1 10 Tf", "12 TL", f"{geometry.margin[3]:.2f} {y:.2f} Td"]
            stream.extend(f"({_pdf_escape(line)}) Tj T*" for line in lines); stream.append("ET"); content = "\n".join(stream).encode("ascii")
            cref = add(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
            page_refs.append(add(f"<< /Type /Page /Parent {pages_ref} 0 R /MediaBox [0 0 {geometry.width:.2f} {geometry.height:.2f}] /Resources << /Font << /F1 {font_ref} 0 R >> >> /Contents {cref} 0 R >>"))
        objects[pages_ref - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{r} 0 R' for r in page_refs)}] /Count {len(page_refs)} >>".encode("ascii")
        info = {"Producer": "chainabit-pdf deterministic backend", **metadata}; iref = add("<< " + " ".join(f"/{k} ({_pdf_escape(v)})" for k, v in sorted(info.items())) + " >>"); root = add(f"<< /Type /Catalog /Pages {pages_ref} 0 R >>")
        result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"); offsets = [0]
        for i, obj in enumerate(objects, 1): offsets.append(len(result)); result.extend(f"{i} 0 obj\n".encode()); result.extend(obj); result.extend(b"\nendobj\n")
        xref = len(result); result.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode()); result.extend(b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets[1:])); result.extend(f"trailer\n<< /Size {len(objects)+1} /Root {root} 0 R /Info {iref} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()); destination.write_bytes(result)

class WeasyPrintRenderer(PdfRenderer):
    capabilities = next(c for c in capability_registry() if c.name == "weasyprint")
    def render(self, document: str, geometry: PageGeometry, metadata: dict[str, str], destination: Path, policy: Any) -> None:
        if not self.capabilities.available: raise PdfError(ErrorCode.DEPENDENCY_UNAVAILABLE, "professional HTML/CSS rendering requires optional dependency 'weasyprint'")
        try:
            from weasyprint import HTML
            from weasyprint.urls import default_url_fetcher
            def fetch(url: str, *args: Any, **kwargs: Any) -> Any:
                if not url.startswith("data:"): raise PdfError(ErrorCode.UNSAFE_INPUT, "network and file assets are disabled for HTML rendering")
                return default_url_fetcher(url, *args, **kwargs)
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
            import os
            font = next((p for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial Unicode.ttf") if os.path.isfile(p)), None)
            all_text = json_text(document)
            if any(ord(c) > 126 for c in all_text):
                if not font: raise PdfError(ErrorCode.FONT_FAILURE, "no approved Unicode font is available for structured report")
                pdfmetrics.registerFont(TTFont("ChainabitUnicode", font)); family = "ChainabitUnicode"
            else: family = "Helvetica"
            styles = getSampleStyleSheet(); body = ParagraphStyle("body", parent=styles["BodyText"], fontName=family, leading=14); heading = ParagraphStyle("heading", parent=styles["Heading2"], fontName=family)
            size = (geometry.width, geometry.height); doc = SimpleDocTemplate(str(destination), pagesize=size, leftMargin=geometry.margin[3], rightMargin=geometry.margin[1], topMargin=geometry.margin[0], bottomMargin=geometry.margin[2], title=metadata.get("Title", ""), author=metadata.get("Author", ""))
            flow = [Paragraph(escape_report(document["title"]), styles["Title"])]
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
