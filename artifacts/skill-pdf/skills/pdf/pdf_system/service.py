from __future__ import annotations

import html
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from .backends import (CapabilityReport, DeterministicTextRenderer, PypdfManipulator,
                       ReportLabRenderer, WeasyPrintRenderer, capability_registry)
from .errors import ErrorCode, PdfError
from .models import DocumentRequirements, PageGeometry, SecurityPolicy
from .safety import bounded_read, reject_active_markup, safe_output, validate_image
from .verification import Verification, verify_pdf

class TemporaryArtifact:
    def __init__(self, policy: SecurityPolicy): self.policy = policy; self._dir = None
    def __enter__(self) -> Path:
        self._dir = tempfile.TemporaryDirectory(prefix="chainabit-pdf-", dir=self.policy.output_root); return Path(self._dir.name)
    def __exit__(self, *_):
        if self._dir and not self.policy.retain_temporary_files: self._dir.cleanup()

class BackendResolver:
    def resolve(self, requirements: DocumentRequirements, preferred: str | None = None) -> tuple[Any, CapabilityReport]:
        registry = capability_registry(); order = ([preferred] if preferred else []) + ["weasyprint", "reportlab", "deterministic-text"]
        rejected: list[str] = []
        for name in order:
            if not name: continue
            descriptor = next((x for x in registry if x.name == name), None)
            if not descriptor: continue
            missing = descriptor.missing(requirements.required)
            if not descriptor.available: rejected.append(f"{name}: dependency unavailable"); continue
            if missing: rejected.append(f"{name}: missing {', '.join(sorted(missing))}"); continue
            if name == "deterministic-text": backend = DeterministicTextRenderer()
            elif name == "weasyprint": backend = WeasyPrintRenderer()
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
    def generate_markdown(self, source: Path, destination: Path, title: str | None = None, lang: str = "und", page_size: object = "A4", orientation: str = "portrait", deterministic: bool = True, quality_profile: str = "quality") -> Verification:
        raw = bounded_read(source, self.policy)
        try: text = raw.decode("utf-8")
        except UnicodeDecodeError as exc: raise PdfError(ErrorCode.INVALID_INPUT, "Markdown must be UTF-8") from exc
        if not text.strip(): raise PdfError(ErrorCode.INVALID_INPUT, "Markdown source is empty")
        reject_active_markup(text); req = DocumentRequirements.infer("markdown", text, "basic" if deterministic else quality_profile)
        backend, self.last_decision = self.resolver.resolve(req, "deterministic-text" if deterministic and not req.required - {"metadata", "pagination", "page_breaks"} else None)
        if backend.capabilities.name == "deterministic-text": document = self._markdown_lines(text); metadata = {"Title": title or source.stem, "Lang": lang, "Creator": "chainabit-pdf"}
        else: document = self._markdown_html(text, self.policy); metadata = {"Title": title or source.stem, "Lang": lang, "Creator": "chainabit-pdf"}
        return self._render(document, backend, destination, metadata, page_size, orientation)
    def generate_report(self, source: Path, destination: Path, quality_profile: str = "quality") -> Verification:
        raw = bounded_read(source, self.policy)
        try: spec = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise PdfError(ErrorCode.INVALID_INPUT, "report specification must be valid UTF-8 JSON") from exc
        problems = self.validate_report(spec)
        if problems: raise PdfError(ErrorCode.INVALID_INPUT, "; ".join(problems))
        req = DocumentRequirements.infer("report", spec, quality_profile); backend, self.last_decision = self.resolver.resolve(req)
        document = spec if backend.capabilities.name == "reportlab" else self._report_html(spec, self.policy); metadata = {"Title": spec["title"], "Author": spec.get("author", ""), "Subject": spec.get("subject", ""), "Lang": spec.get("language", "und"), "Creator": "chainabit-pdf"}
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
            if backend.capabilities.name == "deterministic-text": backend.render(self._paginate(document, geometry), geometry, {k:v for k,v in metadata.items() if v}, staged, self.policy)
            else:
                document = document.replace("@page{size:A4;", f"@page{{size:{geometry.width:.2f}pt {geometry.height:.2f}pt;")
                backend.render(document, geometry, {k:v for k,v in metadata.items() if v}, staged, self.policy)
            result = verify_pdf(staged, self.policy.limits); os.replace(staged, target); return Verification(result.bytes, result.pages, result.version, result.warnings + (f"backend={backend.capabilities.name}", f"duration_ms={(time.monotonic()-started)*1000:.1f}"))
    def _paginate(self, lines: list[str], geometry: PageGeometry) -> list[list[str]]:
        capacity = max(1, int((geometry.height - geometry.margin[0] - geometry.margin[2]) / 12)); pages=[]; current=[]
        for line in lines:
            if line == "\f" or len(current) >= capacity: pages.append(current or [""]); current=[]
            if line != "\f": current.append(line)
        if current: pages.append(current)
        return pages or [[""]]
    def _wrap(self, text: str) -> list[str]: return [text[i:i+100] for i in range(0, len(text), 100)] or [""]
    def _markdown_lines(self, text: str) -> list[str]: return [line for raw in text.splitlines() for line in self._wrap(re.sub(r"^#{1,6}\s+|^[-*+]\s+|^\d+[.)]\s+", "", raw))]
    def _markdown_html(self, text: str, policy: SecurityPolicy) -> str:
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
        return self._html_document("".join(out))
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
    def _report_html(self, spec: dict, policy: SecurityPolicy) -> str:
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
        return self._html_document(prefix + "".join(chunks)).replace("</style>", ".running-header{position:running(header)}.running-footer{position:running(footer)}@page{@top-center{content:element(header)}@bottom-center{content:element(footer)}};</style>")
    def _html_document(self, body: str) -> str: return '<!doctype html><html><head><meta charset="utf-8"><style>@page{size:A4;margin:54pt 50pt 48pt}body{font-family:"DejaVu Sans",sans-serif;color:#111827;line-height:1.35}h1,h2,h3{page-break-after:avoid}table{width:100%;border-collapse:collapse;margin:12pt 0}th{background:#f3f4f6}th,td{border:1px solid #e5e7eb;padding:5pt;text-align:left;vertical-align:top}thead{display:table-header-group}tr{page-break-inside:avoid}pre{white-space:pre-wrap;background:#f3f4f6;padding:8pt}.page-break{break-before:page}img{max-width:100%;height:auto}</style></head><body>'+body+'</body></html>'
    @staticmethod
    def validate_report(spec: object) -> list[str]:
        if not isinstance(spec,dict): return ["spec must be an object"]
        errors=[]
        if not isinstance(spec.get("title"),str) or not spec["title"].strip(): errors.append("title is required")
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
