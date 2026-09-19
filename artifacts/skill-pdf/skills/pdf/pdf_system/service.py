from __future__ import annotations

import html
import base64
import json
import os
import re
import subprocess
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterator

from .backends import (CapabilityReport, PypdfManipulator,
                       ReportLabRenderer, WeasyPrintRenderer, capability_registry)
from .errors import ErrorCode, PdfError, RETRYABLE_RUNTIME_ERRORS, failure_class
from .models import DocumentRequirements, PageGeometry, SecurityPolicy, resolve_direction
from .markdown_html import render_markdown
from .safety import (IMAGE_FILE_FORMATS, bounded_read, image_as_text,
                     image_data_uri, local_asset, reject_active_markup,
                     safe_output, validate_image)
from .verification import Verification, verify_pdf

DEFAULT_FONT_FAMILY = os.environ.get(
    "CHAINABIT_ARTIFACT_FONT_FAMILY", "IBM Plex Sans"
).strip() or "IBM Plex Sans"
DEFAULT_ARABIC_FONT_FAMILY = "IBM Plex Sans Arabic"
DEFAULT_FONT_DIR = Path(os.environ.get(
    "CHAINABIT_ARTIFACT_FONT_DIR", "/opt/chainabit/artifact-fonts/ibm-plex-sans"
))
SAFE_FONT_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")
HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Renderer-local projection of skill-brand-defaults' profile. A PDF bundle must
# run without assuming another composed bundle's path; this complete dictionary
# is therefore a Protected Variation, checked against the shared profile in the
# artifact contract tests rather than imported at runtime.
DEFAULT_PALETTE = {
    "background": "#FFFFFF",
    "surface": "#F9FAFB",
    "ink": "#101828",
    "body": "#364153",
    "muted": "#6A7282",
    "rule": "#E5E7EB",
    "accent": "#327B61",
    "accentInk": "#FFFFFF",
}
PALETTE_KEYS = tuple(DEFAULT_PALETTE)


def validate_palette(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, dict):
        return ["palette must be an object with every palette role"]
    missing = [key for key in PALETTE_KEYS if key not in value]
    unknown = sorted(set(value) - set(PALETTE_KEYS))
    errors = []
    if missing:
        errors.append("palette must include every role: " + ", ".join(missing))
    if unknown:
        errors.append("palette contains unsupported role(s): " + ", ".join(unknown))
    for key in PALETTE_KEYS:
        if not isinstance(value.get(key), str) or not HEX_COLOUR.fullmatch(value[key]):
            errors.append(f"palette.{key} must be a #RRGGBB colour")
    return errors


def resolve_palette(value: object) -> dict[str, str]:
    if value is None:
        return dict(DEFAULT_PALETTE)
    return {key: str(value[key]).upper() for key in PALETTE_KEYS}


def _report_strings(spec: dict) -> Iterator[tuple[str, str]]:
    """Every string in a report spec with its JSON path, walked without recursion."""
    pending: deque[tuple[str, object]] = deque([("", spec)])
    while pending:
        where, value = pending.popleft()
        if isinstance(value, str):
            yield where, value
        elif isinstance(value, dict):
            pending.extend((f"{where}.{key}" if where else str(key), item) for key, item in value.items())
        elif isinstance(value, list):
            pending.extend((f"{where}[{index}]", item) for index, item in enumerate(value))

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
        return {"requirements": sorted(req.required), "reasons": req.reasons, "intent": intent, "backends": reports, "preflight": self._preflight(kind, content)}
    def _preflight(self, kind: str, content: object) -> dict[str, Any]:
        """Run the render path's own input checks without rendering.

        Backend availability says nothing about whether this source can render:
        an image reference that names no file passed diagnosis and failed only
        at render time. The checks are the renderer's, not a second copy, so
        diagnosis and rendering cannot disagree about an input.
        """
        try:
            if kind == "markdown":
                text = str(content); self._check_markdown_text(text)
                for page in text.split("\f"): render_markdown(page, self.policy)
            else:
                problems = self.validate_report(content)
                if problems: raise PdfError(ErrorCode.INVALID_INPUT, "; ".join(problems))
                for block in content["blocks"]:
                    if block["type"] == "image": validate_image(local_asset(block["path"], self.policy), self.policy)
        except PdfError as error:
            return {"ok": False, "error": {"code": error.code.value, "class": failure_class(error), "message": error.message, "retryable": error.code in RETRYABLE_RUNTIME_ERRORS}}
        return {"ok": True}
    def _check_markdown_text(self, text: str) -> None:
        written = image_as_text(text)
        if written:
            raise PdfError(ErrorCode.UNSAFE_INPUT, f"Markdown contains {written}, which prints as characters rather than an image; save the image as a {IMAGE_FILE_FORMATS} file in the Markdown file's directory and reference it as ![description](relative/path.png)")
        reject_active_markup(text)
    def generate_markdown(self, source: Path, destination: Path, title: str | None = None, lang: str = "und", page_size: object = "A4", orientation: str = "portrait", deterministic: bool = False, quality_profile: str = "quality", font: str | None = None, palette: object = None, margin: object = None) -> Verification:
        raw = bounded_read(source, self.policy)
        try: text = raw.decode("utf-8")
        except UnicodeDecodeError as exc: raise PdfError(ErrorCode.INVALID_INPUT, "Markdown must be UTF-8") from exc
        if not text.strip(): raise PdfError(ErrorCode.INVALID_INPUT, "Markdown source is empty")
        palette_errors = validate_palette(palette)
        if palette_errors: raise PdfError(ErrorCode.INVALID_INPUT, "; ".join(palette_errors))
        if isinstance(font, str) and font.strip() != DEFAULT_FONT_FAMILY and palette is None:
            raise PdfError(ErrorCode.INVALID_INPUT, "a non-Chainabit font override requires a complete palette")
        self._check_markdown_text(text); req = DocumentRequirements.infer("markdown", text, "basic" if deterministic else quality_profile)
        backend, self.last_decision = self.resolver.resolve(req)
        geometry = self._resolve_geometry(page_size, orientation, margin)
        document = self._markdown_html(text, self.policy, self._font_family(font), resolve_palette(palette), palette is None and font is None, geometry); metadata = {"Title": title or source.stem, "Lang": lang, "Creator": "chainabit-pdf"}
        return self._render(document, backend, destination, metadata, geometry)
    def generate_report(self, source: Path, destination: Path, quality_profile: str = "quality") -> Verification:
        raw = bounded_read(source, self.policy)
        try: spec = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise PdfError(ErrorCode.INVALID_INPUT, "report specification must be valid UTF-8 JSON") from exc
        problems = self.validate_report(spec)
        if problems: raise PdfError(ErrorCode.INVALID_INPUT, "; ".join(problems))
        req = DocumentRequirements.infer("report", spec, quality_profile); backend, self.last_decision = self.resolver.resolve(req)
        geometry = self._resolve_geometry(spec.get("pageSize", "A4"), spec.get("orientation", "portrait"), spec.get("margin"))
        palette = resolve_palette(spec.get("palette"))
        # The controller resolves the complete palette once.  Both adapters
        # receive that same immutable result: ReportLab is a structured
        # renderer, while WeasyPrint receives its HTML projection.  This keeps
        # a user palette from silently mixing with renderer-local defaults.
        document = {**spec, "palette": palette} if backend.capabilities.name == "reportlab" else self._report_html(spec, self.policy, self._font_family(spec.get("font")), palette, spec.get("palette") is None and spec.get("font") is None, geometry); metadata = {"Title": spec["title"], "Author": spec.get("author", ""), "Subject": spec.get("subject", ""), "Lang": spec.get("language", "und"), "Creator": "chainabit-pdf"}
        return self._render(document, backend, destination, metadata, geometry)
    def manipulate(self, operation: str, sources: list[Path], destination: Path, options: dict) -> Verification:
        target = safe_output(destination, self.policy)
        for source in sources: bounded_read(source, self.policy)
        with TemporaryArtifact(self.policy) as temp:
            staged = temp / "result.pdf"; PypdfManipulator().manipulate(operation, sources, staged, options); result = verify_pdf(staged, self.policy.limits); os.replace(staged, target); return result
    def _resolve_geometry(self, page_size: object, orientation: str, margin: object) -> PageGeometry:
        try: return PageGeometry.from_spec(page_size, orientation, margin)
        except ValueError as exc: raise PdfError(ErrorCode.INVALID_INPUT, str(exc)) from exc
    def _render(self, document: Any, backend: Any, destination: Path, metadata: dict[str, str], geometry: PageGeometry) -> Verification:
        target = safe_output(destination, self.policy); started = time.monotonic()
        with TemporaryArtifact(self.policy) as temp:
            staged = temp / "result.pdf"
            # The HTML document already carries its own @page rule, built from
            # this same geometry by _html_document -- there is nothing left to
            # patch here. A post-hoc string replacement previously stood in
            # for that (and only ever touched page size, never margin, so a
            # caller's margin request was silently dropped for every WeasyPrint
            # document; see the fix that added this comment).
            backend.render(document, geometry, {k:v for k,v in metadata.items() if v}, staged, self.policy)
            result = verify_pdf(staged, self.policy.limits); os.replace(staged, target); return Verification(result.bytes, result.pages, result.version, result.sha256, result.mime_type, result.warnings + (f"backend={backend.capabilities.name}", f"duration_ms={(time.monotonic()-started)*1000:.1f}"))
    def _markdown_html(self, text: str, policy: SecurityPolicy, font: str, palette: dict[str, str], show_chainabit_footer: bool, geometry: PageGeometry) -> str:
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
            font, palette, show_chainabit_footer, geometry, resolve_direction(text),
        )
    def _markdown_blocks(self, text: str) -> str:
        # One page of Markdown. The Markdown dialect (CommonMark blocks plus
        # GFM tables) is owned by markdown_html; this controller only binds
        # the page to the request's security policy.
        return render_markdown(text, self.policy)
    def _image_tag(self, alt: str, uri: str) -> str:
        return f'<img alt="{html.escape(alt,quote=True)}" src="{image_data_uri(local_asset(uri, self.policy), self.policy)}">'
    def _report_html(self, spec: dict, policy: SecurityPolicy, font: str, palette: dict[str, str], show_chainabit_footer: bool, geometry: PageGeometry) -> str:
        chunks=[f"<h1>{html.escape(spec['title'])}</h1>"]
        for b in spec["blocks"]:
            kind=b["type"]
            if kind=="heading": chunks.append(f"<h2>{html.escape(b['text'])}</h2>")
            elif kind=="paragraph": chunks.append(f"<p>{html.escape(b['text'])}</p>")
            elif kind in {"bullets","numbered"}: chunks.append("<ul>"+"".join("<li>"+html.escape(x)+"</li>" for x in b["items"])+"</ul>")
            elif kind=="table": chunks.append("<table><thead><tr>"+"".join("<th>"+html.escape(str(x))+"</th>" for x in b["columns"])+"</tr></thead><tbody>"+"".join("<tr>"+"".join("<td>"+html.escape(str(x))+"</td>" for x in row)+"</tr>" for row in b["rows"])+"</tbody></table>")
            elif kind=="image":
                caption=str(b.get("caption",""))
                tag=self._image_tag(caption, str(b["path"]))
                # A report `caption` used to reach only the <img alt>
                # attribute -- accessibility metadata a renderer never paints
                # -- so a caption a caller asked to be visible silently never
                # was. <figcaption> is the element WeasyPrint actually
                # renders as body text.
                chunks.append(f'<figure>{tag}<figcaption>{html.escape(caption)}</figcaption></figure>' if caption else tag)
            elif kind=="pagebreak": chunks.append('<div class="page-break"></div>')
            elif kind=="spacer": chunks.append(f'<div style="height:{int(b.get("height",12))}pt"></div>')
        header = html.escape(str(spec.get("header", ""))); footer = html.escape(str(spec.get("footer", "")))
        prefix = (f'<div class="running-header">{header}</div>' if header else "") + (f'<div class="running-footer">{footer}</div>' if footer else "")
        # Direction is resolved from the report's own textual content -- every
        # string value in the spec, walked the same way `validate_report`
        # already walks it to find an embedded image-as-text -- not from the
        # whole spec `str()`'d, which would count "title", "blocks", "type"
        # and every other JSON key as Latin filler and understate an
        # otherwise Arabic/Hebrew report.
        direction = resolve_direction(" ".join(value for _, value in _report_strings(spec)))
        return self._html_document(prefix + "".join(chunks), font, palette, show_chainabit_footer, geometry, direction).replace("</style>", ".running-header{position:running(header)}.running-footer{position:running(footer)}@page{@top-center{content:element(header)}@bottom-center{content:element(footer)}};</style>")
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
    def _html_document(self, body: str, font: str, palette: dict[str, str], show_chainabit_footer: bool, geometry: PageGeometry, direction: str = "ltr") -> str:
        # One audited, print-first design system.  Callers choose content and
        # page geometry, not arbitrary CSS; that keeps professional output
        # deterministic and prevents a prompt from becoming a styling/security
        # boundary. The @page rule is built from the caller's resolved
        # geometry directly -- there is no later size/margin patch, and no
        # second hardcoded default to drift from PageGeometry's own.
        #
        # `direction` is a LAYOUT property, resolved once by the caller from
        # the document's own content (see resolve_direction in models.py) and
        # applied here in exactly two ways: the `dir` attribute on `<html>`
        # (WeasyPrint implements the same HTML5 `[dir=rtl]{direction:rtl}`
        # mapping every browser does -- verified directly against its own
        # bundled UA stylesheet, not assumed) and the CSS `direction`
        # property set explicitly below as well, so this stylesheet does not
        # depend on that UA default surviving a future WeasyPrint upgrade.
        # Every rule below that used to hardcode a physical `left`/`right` is
        # a logical property (`-inline-start`/`-inline-end`, `text-align:
        # start`) instead, so it flips automatically with `direction` -- the
        # renderer's Unicode Bidi implementation still does 100% of the
        # actual character shaping and reordering; nothing here reverses or
        # rewrites a single character of `body`.
        brand_footer = 'content:"CHAINABIT";font:600 7pt "ChainabitArtifact";letter-spacing:1.5pt;' if show_chainabit_footer else 'content:"";'
        top, right, bottom, left = (f"{value:.2f}pt" for value in geometry.margin)
        style = self._font_css(font) + f'''
@page{{size:{geometry.width:.2f}pt {geometry.height:.2f}pt;margin:{top} {right} {bottom} {left};background:{palette["background"]};
 @bottom-left{{{brand_footer}color:{palette["muted"]}}}
 @bottom-right{{content:counter(page) " / " counter(pages);font:8pt "ChainabitArtifact";color:{palette["muted"]}}}}}
*{{box-sizing:border-box}}body{{direction:{direction};font-family:"ChainabitArtifact","ChainabitArtifactArabic",sans-serif;color:{palette["body"]};font-size:10.5pt;line-height:1.58;margin:0;text-align:start}}
h1,h2,h3,h4,h5,h6{{page-break-after:avoid;line-height:1.16;color:{palette["ink"]};margin:22pt 0 9pt}}
h1{{font-size:28pt;letter-spacing:-.7pt;margin-top:0;padding:0 0 13pt;border-bottom:4pt solid {palette["accent"]}}}
h2{{font-size:18pt;letter-spacing:-.25pt;padding-inline-start:11pt;border-inline-start:4pt solid {palette["accent"]}}}
h3{{font-size:13.5pt;color:{palette["accent"]}}}p{{margin:0 0 10pt;orphans:3;widows:3}}
strong{{color:{palette["ink"]}}}a{{color:{palette["accent"]};text-decoration:none;border-bottom:.5pt solid {palette["rule"]}}}
ul,ol{{margin:6pt 0 14pt;padding-inline-start:20pt}}li{{margin:0 0 5pt}}li::marker{{color:{palette["accent"]}}}
table{{width:100%;border-collapse:separate;border-spacing:0;margin:14pt 0 18pt;font-size:9pt;border:1pt solid {palette["rule"]};border-radius:5pt}}
th{{background:{palette["ink"]};color:{palette["accentInk"]};font-weight:700}}th,td{{padding:7pt 8pt;text-align:start;vertical-align:top;border-inline-end:.5pt solid {palette["rule"]};border-bottom:.5pt solid {palette["rule"]}}}
th:last-child,td:last-child{{border-inline-end:0}}tr:last-child td{{border-bottom:0}}tbody tr:nth-child(even){{background:{palette["surface"]}}}thead{{display:table-header-group}}tr{{page-break-inside:avoid}}
pre{{white-space:pre-wrap;background:{palette["ink"]};color:{palette["accentInk"]};border-inline-start:4pt solid {palette["accent"]};border-radius:5pt;padding:11pt 13pt;font-size:8.5pt;line-height:1.45;page-break-inside:avoid}}
code{{font-family:"Fira Code","Noto Sans Mono",monospace;background:{palette["surface"]};border-radius:2pt;padding:1pt 3pt}}pre code{{background:transparent;padding:0}}
blockquote{{margin:14pt 0;padding:10pt 14pt;background:{palette["surface"]};border-inline-start:4pt solid {palette["accent"]};color:{palette["body"]}}}blockquote>:last-child{{margin-bottom:0}}
hr{{border:0;border-top:1pt solid {palette["rule"]};margin:18pt 0}}li>ul,li>ol{{margin:4pt 0 0}}li>p{{margin:0 0 5pt}}
.page-break{{break-before:page}}img{{display:block;max-width:100%;height:auto;margin:14pt auto;border-radius:5pt}}
figure{{margin:14pt 0;text-align:center;page-break-inside:avoid}}figure img{{margin:0 auto 6pt}}
figcaption{{font-size:8.5pt;color:{palette["muted"]};text-align:center}}
'''
        return f'<!doctype html><html dir="{direction}"><head><meta charset="utf-8"><style>'+style+'</style></head><body>'+body+'</body></html>'
    @staticmethod
    def validate_report(spec: object) -> list[str]:
        if not isinstance(spec,dict): return ["spec must be an object"]
        errors=[]
        if not isinstance(spec.get("title"),str) or not spec["title"].strip(): errors.append("title is required")
        if spec.get("font") is not None and (not isinstance(spec.get("font"), str) or not SAFE_FONT_NAME.fullmatch(spec["font"].strip())): errors.append("font must be a safe non-empty family name")
        if isinstance(spec.get("font"), str) and SAFE_FONT_NAME.fullmatch(spec["font"].strip()) and spec["font"].strip() != DEFAULT_FONT_FAMILY and spec.get("palette") is None:
            errors.append("a non-Chainabit font override requires a complete palette")
        errors.extend(validate_palette(spec.get("palette")))
        # Every report text field is escaped and printed, so image markup or
        # inline image data in one would appear as characters, never a picture.
        written=[f"{where} ({found})" for where, value in _report_strings(spec) if (found := image_as_text(value))]
        if written: errors.append("image written as text in "+", ".join(written)+f": a report prints text fields literally, so it would appear as characters rather than an image; save the image as a {IMAGE_FILE_FORMATS} file in the report spec's directory and add a block {{\"type\": \"image\", \"path\": \"relative/path.png\"}}")
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
