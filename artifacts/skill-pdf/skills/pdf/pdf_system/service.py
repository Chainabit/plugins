from __future__ import annotations

import html
import base64
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .backends import (CapabilityReport, PypdfManipulator,
                       ReportLabRenderer, WeasyPrintRenderer, capability_registry)
from .errors import ErrorCode, PdfError
from .models import DocumentRequirements, PageGeometry, SecurityPolicy
from .safety import bounded_read, reject_active_markup, safe_output, validate_image
from .verification import Verification, verify_pdf

DEFAULT_FONT_FAMILY = os.environ.get(
    "CHAINABIT_ARTIFACT_FONT_FAMILY", "IBM Plex Sans"
).strip() or "IBM Plex Sans"
DEFAULT_ARABIC_FONT_FAMILY = "IBM Plex Sans Arabic"
DEFAULT_FONT_DIR = Path(os.environ.get(
    "CHAINABIT_ARTIFACT_FONT_DIR", "/opt/chainabit/artifact-fonts/ibm-plex-sans"
))
SAFE_FONT_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")

class TemporaryArtifact:
    def __init__(self, policy: SecurityPolicy): self.policy = policy; self._dir = None
    def __enter__(self) -> Path:
        self._dir = tempfile.TemporaryDirectory(prefix="chainabit-pdf-", dir=self.policy.output_root); return Path(self._dir.name)
    def __exit__(self, *_):
        if self._dir and not self.policy.retain_temporary_files: self._dir.cleanup()

class BackendResolver:
    def resolve(self, requirements: DocumentRequirements, preferred: str | None = None) -> tuple[Any, CapabilityReport]:
        registry = capability_registry(); order = ([preferred] if preferred else []) + ["weasyprint", "reportlab"]
        rejected: list[str] = []
        for name in order:
            if not name: continue
            descriptor = next((x for x in registry if x.name == name), None)
            if not descriptor: continue
            missing = descriptor.missing(requirements.required)
            if not descriptor.available: rejected.append(f"{name}: dependency unavailable"); continue
            if missing: rejected.append(f"{name}: missing {', '.join(sorted(missing))}"); continue
            if name == "weasyprint": backend = WeasyPrintRenderer()
            elif name == "reportlab": backend = ReportLabRenderer()
            else: rejected.append(f"{name}: no audited renderer for this document type"); continue
            return backend, CapabilityReport(name, True, True, descriptor.version, tuple(sorted(descriptor.supports)), descriptor.operations, descriptor.restrictions, reason="; ".join(rejected) or "requirements satisfied")
        code = ErrorCode.DEPENDENCY_UNAVAILABLE if any("dependency unavailable" in x for x in rejected) else ErrorCode.UNSUPPORTED_CAPABILITY
        raise PdfError(code, "no available backend satisfies required capabilities", {"required": ",".join(sorted(requirements.required)), "rejected": " | ".join(rejected)})

class PdfService:
    """Use-case controller; parsing, selection, policy, rendering and persistence stay separate."""
    def __init__(self, policy: SecurityPolicy, resolver: BackendResolver | None = None): self.policy = policy; self.resolver = resolver or BackendResolver(); self.last_decision: CapabilityReport | None = None
    @staticmethod
    def discover_capabilities():
        return [{"name": c.name, "available": c.available, "version": c.version, "supported_capabilities": sorted(c.supports), "operations": list(c.operations), "restrictions": list(c.restrictions), "dependency": c.dependency, "detail": c.detail} for c in capability_registry()]
    def diagnose(self, kind: str, content: object, intent: str = "quality") -> dict[str, Any]:
        req = DocumentRequirements.infer(kind, content, intent); reports = []
        for c in capability_registry(): reports.append({"backend": c.name, "available": c.available, "version": c.version, "required": sorted(req.required), "missing": sorted(c.missing(req.required)), "reason": c.detail or ("available" if c.available else "dependency unavailable")})
        return {"requirements": sorted(req.required), "reasons": req.reasons, "intent": intent, "backends": reports}
    def generate_markdown(self, source: Path, destination: Path, title: str | None = None, lang: str = "und", page_size: object = "A4", orientation: str = "portrait", deterministic: bool = False, quality_profile: str = "quality", font: str | None = None) -> Verification:
        raw = bounded_read(source, self.policy)
        try: text = raw.decode("utf-8")
        except UnicodeDecodeError as exc: raise PdfError(ErrorCode.INVALID_INPUT, "Markdown must be UTF-8") from exc
        if not text.strip(): raise PdfError(ErrorCode.INVALID_INPUT, "Markdown source is empty")
        reject_active_markup(text); req = DocumentRequirements.infer("markdown", text, "basic" if deterministic else quality_profile)
        backend, self.last_decision = self.resolver.resolve(req)
        document = self._markdown_html(text, self.policy, self._font_family(font)); metadata = {"Title": title or source.stem, "Lang": lang, "Creator": "chainabit-pdf"}
        return self._render(document, backend, destination, metadata, page_size, orientation)
    def generate_report(self, source: Path, destination: Path, quality_profile: str = "quality") -> Verification:
        raw = bounded_read(source, self.policy)
        try: spec = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise PdfError(ErrorCode.INVALID_INPUT, "report specification must be valid UTF-8 JSON") from exc
        problems = self.validate_report(spec)
        if problems: raise PdfError(ErrorCode.INVALID_INPUT, "; ".join(problems))
        req = DocumentRequirements.infer("report", spec, quality_profile); backend, self.last_decision = self.resolver.resolve(req)
        document = spec if backend.capabilities.name == "reportlab" else self._report_html(spec, self.policy, self._font_family(spec.get("font"))); metadata = {"Title": spec["title"], "Author": spec.get("author", ""), "Subject": spec.get("subject", ""), "Lang": spec.get("language", "und"), "Creator": "chainabit-pdf"}
        return self._render(document, backend, destination, metadata, spec.get("pageSize", "A4"), spec.get("orientation", "portrait"), spec.get("margin"))
    def manipulate(self, operation: str, sources: list[Path], destination: Path, options: dict) -> Verification:
        target = safe_output(destination, self.policy)
        for source in sources: bounded_read(source, self.policy)
        with TemporaryArtifact(self.policy) as temp:
            staged = temp / "result.pdf"; PypdfManipulator().manipulate(operation, sources, staged, options); result = verify_pdf(staged, self.policy.limits); os.replace(staged, target); return result
    def _render(self, document: Any, backend: Any, destination: Path, metadata: dict[str, str], page_size: object, orientation: str, margin: object = None) -> Verification:
        try: geometry = PageGeometry.from_spec(page_size, orientation, margin)
        except ValueError as exc: raise PdfError(ErrorCode.INVALID_INPUT, str(exc)) from exc
        target = safe_output(destination, self.policy); started = time.monotonic()
        with TemporaryArtifact(self.policy) as temp:
            staged = temp / "result.pdf"
            document = document.replace("@page{size:A4;", f"@page{{size:{geometry.width:.2f}pt {geometry.height:.2f}pt;")
            backend.render(document, geometry, {k:v for k,v in metadata.items() if v}, staged, self.policy)
            result = verify_pdf(staged, self.policy.limits); os.replace(staged, target); return Verification(result.bytes, result.pages, result.version, result.sha256, result.mime_type, result.warnings + (f"backend={backend.capabilities.name}", f"duration_ms={(time.monotonic()-started)*1000:.1f}"))
    def _paginate(self, lines: list[str], geometry: PageGeometry) -> list[list[str]]:
        capacity = max(1, int((geometry.height - geometry.margin[0] - geometry.margin[2]) / 12)); pages=[]; current=[]
        for line in lines:
            if line == "\f" or len(current) >= capacity: pages.append(current or [""]); current=[]
            if line != "\f": current.append(line)
        if current: pages.append(current)
        return pages or [[""]]
    def _wrap(self, text: str) -> list[str]: return [text[i:i+100] for i in range(0, len(text), 100)] or [""]
    def _markdown_lines(self, text: str) -> list[str]: return [line for raw in text.splitlines() for line in self._wrap(re.sub(r"^#{1,6}\s+|^[-*+]\s+|^\d+[.)]\s+", "", raw))]
    def _markdown_html(self, text: str, policy: SecurityPolicy, font: str) -> str:
        # A form feed is the explicit page break this system already claims to
        # understand: models.py raises the `page_breaks` requirement when it
        # sees one, which constrains backend selection. It was then destroyed
        # here, because str.splitlines() splits on \f AND discards it -- so the
        # break was inferred, honoured in the choice of renderer, and silently
        # dropped before any HTML existed. A document written as ten pages came
        # out as two, and validate_pdf.py cannot detect the loss because it
        # only bounds the page count rather than checking it.
        return self._html_document(
            '<div class="page-break"></div>'.join(
                self._markdown_blocks(page) for page in text.split("\f")
            ),
            font,
        )
    def _markdown_blocks(self, text: str) -> str:
        lines=text.splitlines(); out=[]; i=0
        while i<len(lines):
            line=lines[i]
            if not line.strip(): i+=1; continue
            if line.startswith("```"):
                code=[]; i+=1
                while i<len(lines) and not lines[i].startswith("```"): code.append(lines[i]); i+=1
                if i==len(lines): raise PdfError(ErrorCode.INVALID_INPUT, "unclosed Markdown code block")
                out.append("<pre><code>"+html.escape("\n".join(code))+"</code></pre>"); i+=1; continue
            m=re.match(r"^(#{1,6})\s+(.+)$",line)
            if m: out.append(f"<h{len(m.group(1))}>{self._inline(m.group(2))}</h{len(m.group(1))}>"); i+=1; continue
            if line.startswith("|") and i+1<len(lines) and "|" in lines[i+1]:
                rows=[]
                while i<len(lines) and lines[i].startswith("|"):
                    cells=[x.strip() for x in lines[i].strip("|").split("|")]
                    if not all(set(x)<=set("-: ") for x in cells): rows.append(cells)
                    i+=1
                out.append("<table><thead><tr>"+"".join("<th>"+self._inline(x)+"</th>" for x in rows[0])+"</tr></thead><tbody>"+"".join("<tr>"+"".join("<td>"+self._inline(x)+"</td>" for x in row)+"</tr>" for row in rows[1:])+"</tbody></table>"); continue
            if re.match(r"^[-*+]\s+",line):
                items=[]
                while i<len(lines) and re.match(r"^[-*+]\s+",lines[i]): items.append("<li>"+self._inline(re.sub(r"^[-*+]\s+","",lines[i]))+"</li>"); i+=1
                out.append("<ul>"+"".join(items)+"</ul>"); continue
            out.append("<p>"+self._inline(line)+"</p>"); i+=1
        return "".join(out)
    def _inline(self, value: str) -> str:
        if re.search(r"\$[^$]+\$|\\\(|\\\[", value):
            if re.search(r"\\(frac|sqrt|begin|end|newcommand)\b", value):
                raise PdfError(ErrorCode.UNSUPPORTED_CAPABILITY, "equation uses unsupported or unsafe math syntax")
            value = re.sub(r"\$([^$]+)\$", r"<math><mrow><mi>\1</mi></mrow></math>", value)
        value=re.sub(r"!\[([^]]*)\]\(([^)]+)\)", lambda m: self._image_tag(m.group(1),m.group(2)), value)
        math_parts=[]
        value=re.sub(r"<math>.*?</math>", lambda m: (math_parts.append(m.group(0)) or f"@@MATH{len(math_parts)-1}@@"), value)
        value=html.escape(value, quote=True); value=re.sub(r"\*\*(.+?)\*\*",r"<strong>\1</strong>",value); value=re.sub(r"`([^`]+)`",r"<code>\1</code>",value); value=re.sub(r"\[([^]]+)\]\((https?://[^)]+)\)",r'<a href="\2">\1</a>',value)
        for i, part in enumerate(math_parts): value=value.replace(f"@@MATH{i}@@", part)
        return value
    def _image_tag(self, alt: str, uri: str) -> str:
        path=(self.policy.input_root / uri).resolve(); mime,_,_=validate_image(path,self.policy); import base64
        encoded=base64.b64encode(path.read_bytes()).decode("ascii"); return f'<img alt="{html.escape(alt,quote=True)}" src="data:{mime};base64,{encoded}">' 
    def _report_html(self, spec: dict, policy: SecurityPolicy, font: str) -> str:
        chunks=[f"<h1>{html.escape(spec['title'])}</h1>"]
        for b in spec["blocks"]:
            kind=b["type"]
            if kind=="heading": chunks.append(f"<h2>{html.escape(b['text'])}</h2>")
            elif kind=="paragraph": chunks.append(f"<p>{html.escape(b['text'])}</p>")
            elif kind in {"bullets","numbered"}: chunks.append("<ul>"+"".join("<li>"+html.escape(x)+"</li>" for x in b["items"])+"</ul>")
            elif kind=="table": chunks.append("<table><thead><tr>"+"".join("<th>"+html.escape(str(x))+"</th>" for x in b["columns"])+"</tr></thead><tbody>"+"".join("<tr>"+"".join("<td>"+html.escape(str(x))+"</td>" for x in row)+"</tr>" for row in b["rows"])+"</tbody></table>")
            elif kind=="image": chunks.append(self._image_tag(str(b.get("caption", "")), str(b["path"])))
            elif kind=="pagebreak": chunks.append('<div class="page-break"></div>')
            elif kind=="spacer": chunks.append(f'<div style="height:{int(b.get("height",12))}pt"></div>')
        header = html.escape(str(spec.get("header", ""))); footer = html.escape(str(spec.get("footer", "")))
        prefix = (f'<div class="running-header">{header}</div>' if header else "") + (f'<div class="running-footer">{footer}</div>' if footer else "")
        return self._html_document(prefix + "".join(chunks), font).replace("</style>", ".running-header{position:running(header)}.running-footer{position:running(footer)}@page{@top-center{content:element(header)}@bottom-center{content:element(footer)}};</style>")
    def _font_family(self, requested: object) -> str:
        if requested is None:
            return DEFAULT_FONT_FAMILY
        if not isinstance(requested, str) or not SAFE_FONT_NAME.fullmatch(requested.strip()):
            raise PdfError(ErrorCode.INVALID_INPUT, "font must be a safe non-empty family name")
        return requested.strip()
    def _font_file(self, family: str, style: str) -> Path:
        if family == DEFAULT_FONT_FAMILY:
            name = "IBMPlexSans-SemiBold.ttf" if style == "Semibold" else "IBMPlexSans-Regular.ttf"
            path = DEFAULT_FONT_DIR / name
        else:
            try:
                result = subprocess.run(
                    ["fc-match", "-f", "%{family}\n%{file}\n", f"{family}:style={style}"],
                    capture_output=True, text=True, check=False, timeout=5,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise PdfError(ErrorCode.DEPENDENCY_FAILURE, "fontconfig could not resolve the requested font") from exc
            lines = result.stdout.splitlines()
            if result.returncode or len(lines) < 2 or family.casefold() not in lines[0].casefold():
                raise PdfError(ErrorCode.INVALID_INPUT, f"requested font {family!r} is unavailable")
            path = Path(lines[1])
        if not path.is_file():
            raise PdfError(ErrorCode.DEPENDENCY_UNAVAILABLE, f"font asset is unavailable: {path}")
        return path
    def _font_css(self, family: str) -> str:
        faces=[]
        for style, weight in (("Regular", 400), ("Semibold", 600)):
            path=self._font_file(family, style)
            encoded=base64.b64encode(path.read_bytes()).decode("ascii")
            faces.append(f'@font-face{{font-family:"ChainabitArtifact";font-style:normal;font-weight:{weight};src:url(data:font/ttf;base64,{encoded}) format("truetype")}}')
        # The canonical Latin family deliberately stays primary. IBM's
        # licensed Arabic companion supplies the one active product script the
        # Latin face does not cover, and is embedded rather than discovered
        # from an accidental host font.
        for style, weight in (("Regular", 400), ("SemiBold", 600)):
            path = DEFAULT_FONT_DIR / f"IBMPlexSansArabic-{style}.ttf"
            if not path.is_file():
                raise PdfError(ErrorCode.DEPENDENCY_UNAVAILABLE, f"font asset is unavailable: {path}")
            encoded=base64.b64encode(path.read_bytes()).decode("ascii")
            faces.append(f'@font-face{{font-family:"ChainabitArtifactArabic";font-style:normal;font-weight:{weight};src:url(data:font/ttf;base64,{encoded}) format("truetype")}}')
        return "".join(faces)
    def _html_document(self, body: str, font: str) -> str:
        # One audited, print-first design system.  Callers choose content and
        # page geometry, not arbitrary CSS; that keeps professional output
        # deterministic and prevents a prompt from becoming a styling/security
        # boundary.
        style = self._font_css(font) + '''
@page{size:A4;margin:58pt 54pt 54pt;background:#fff;
 @bottom-left{content:"CHAINABIT";font:600 7pt "ChainabitArtifact";letter-spacing:1.5pt;color:#64748b}
 @bottom-right{content:counter(page) " / " counter(pages);font:8pt "ChainabitArtifact";color:#64748b}}
*{box-sizing:border-box}body{font-family:"ChainabitArtifact","ChainabitArtifactArabic",sans-serif;color:#172033;font-size:10.5pt;line-height:1.58;margin:0}
h1,h2,h3,h4,h5,h6{page-break-after:avoid;line-height:1.16;color:#0b1739;margin:22pt 0 9pt}
h1{font-size:28pt;letter-spacing:-.7pt;margin-top:0;padding:0 0 13pt;border-bottom:4pt solid #ffde59}
h2{font-size:18pt;letter-spacing:-.25pt;padding-left:11pt;border-left:4pt solid #ffde59}
h3{font-size:13.5pt;color:#1d4ed8}p{margin:0 0 10pt;orphans:3;widows:3}
strong{color:#0b1739}a{color:#1d4ed8;text-decoration:none;border-bottom:.5pt solid #93c5fd}
ul,ol{margin:6pt 0 14pt;padding-left:20pt}li{margin:0 0 5pt}li::marker{color:#d4a900}
table{width:100%;border-collapse:separate;border-spacing:0;margin:14pt 0 18pt;font-size:9pt;border:1pt solid #dbe3f0;border-radius:5pt}
th{background:#0b1739;color:#fff;font-weight:700}th,td{padding:7pt 8pt;text-align:left;vertical-align:top;border-right:.5pt solid #dbe3f0;border-bottom:.5pt solid #dbe3f0}
th:last-child,td:last-child{border-right:0}tr:last-child td{border-bottom:0}tbody tr:nth-child(even){background:#f7f9fc}thead{display:table-header-group}tr{page-break-inside:avoid}
pre{white-space:pre-wrap;background:#0b1739;color:#e2e8f0;border-left:4pt solid #ffde59;border-radius:5pt;padding:11pt 13pt;font-size:8.5pt;line-height:1.45;page-break-inside:avoid}
code{font-family:"Fira Code","Noto Sans Mono",monospace;background:#eef2f7;border-radius:2pt;padding:1pt 3pt}pre code{background:transparent;padding:0}
blockquote{margin:14pt 0;padding:10pt 14pt;background:#f7f9fc;border-left:4pt solid #ffde59;color:#334155}
.page-break{break-before:page}img{display:block;max-width:100%;height:auto;margin:14pt auto;border-radius:5pt}
'''
        return '<!doctype html><html><head><meta charset="utf-8"><style>'+style+'</style></head><body>'+body+'</body></html>'
    @staticmethod
    def validate_report(spec: object) -> list[str]:
        if not isinstance(spec,dict): return ["spec must be an object"]
        errors=[]
        if not isinstance(spec.get("title"),str) or not spec["title"].strip(): errors.append("title is required")
        if spec.get("font") is not None and (not isinstance(spec.get("font"), str) or not SAFE_FONT_NAME.fullmatch(spec["font"].strip())): errors.append("font must be a safe non-empty family name")
        blocks=spec.get("blocks")
        if not isinstance(blocks,list) or not blocks: return errors+["blocks must be a non-empty array"]
        allowed={"heading","paragraph","bullets","numbered","table","image","spacer","pagebreak"}
        for i,b in enumerate(blocks):
            if not isinstance(b,dict) or b.get("type") not in allowed: errors.append(f"blocks[{i}] has unsupported type"); continue
            if b["type"] in {"heading","paragraph"} and not isinstance(b.get("text"),str): errors.append(f"blocks[{i}].text must be a string")
            if b["type"] in {"bullets","numbered"} and (not isinstance(b.get("items"),list) or not all(isinstance(x,str) for x in b["items"])): errors.append(f"blocks[{i}].items must be strings")
            if b["type"]=="table" and (not isinstance(b.get("columns"),list) or not isinstance(b.get("rows"),list)): errors.append(f"blocks[{i}] table requires columns and rows")
            if b["type"]=="image" and not isinstance(b.get("path"),str): errors.append(f"blocks[{i}].path must be a string")
        return errors
