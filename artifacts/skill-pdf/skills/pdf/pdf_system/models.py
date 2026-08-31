from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from dataclasses import field


PAPER_SIZES_PT = {
    "A0": (2383.94, 3370.39), "A1": (1683.78, 2383.94), "A2": (1190.55, 1683.78),
    "A3": (841.89, 1190.55), "A4": (595.28, 841.89), "A5": (419.53, 595.28),
    "A6": (297.64, 419.53), "A7": (209.76, 297.64), "A8": (147.40, 209.76),
    "A9": (104.88, 147.40), "A10": (73.70, 104.88), "B0": (2834.65, 4008.19),
    "B1": (2004.09, 2834.65), "B2": (1417.32, 2004.09), "B3": (1000.63, 1417.32),
    "B4": (708.66, 1000.63), "B5": (498.90, 708.66), "B6": (354.33, 498.90),
    "LETTER": (612.0, 792.0), "LEGAL": (612.0, 1008.0), "TABLOID": (792.0, 1224.0),
    "LEDGER": (1224.0, 792.0), "EXECUTIVE": (522.0, 756.0),
}


@dataclass(frozen=True)
class Limits:
    max_input_bytes: int = 10 * 1024 * 1024
    max_output_bytes: int = 100 * 1024 * 1024
    max_pages: int = 1000
    max_page_points: float = 14400.0  # 200 inches, PDF spec limit
    max_text_chars: int = 2_000_000
    max_image_bytes: int = 25 * 1024 * 1024
    max_image_pixels: int = 40_000_000
    max_nesting: int = 32
    timeout_seconds: int = 60
    max_css_bytes: int = 256 * 1024


@dataclass(frozen=True)
class SecurityPolicy:
    input_root: Path
    output_root: Path
    limits: Limits = Limits()
    allow_remote_assets: bool = False
    allow_embedded_files: bool = False
    retain_temporary_files: bool = False

    @staticmethod
    def for_paths(input_path: str, output_path: str, limits: Limits | None = None) -> "SecurityPolicy":
        # A caller explicitly gives the source and destination roots; assets are limited
        # to the source sibling tree, outputs to destination sibling tree.
        return SecurityPolicy(Path(input_path).resolve().parent, Path(output_path).resolve().parent, limits or Limits())


@dataclass(frozen=True)
class PageGeometry:
    width: float
    height: float
    margin: tuple[float, float, float, float] = (54.0, 50.0, 48.0, 50.0)

    @classmethod
    def from_spec(cls, value: object = "A4", orientation: str = "portrait", margin: object = None) -> "PageGeometry":
        if isinstance(value, str):
            size = PAPER_SIZES_PT.get(value.upper())
            if not size:
                raise ValueError("unknown paper size")
            width, height = size
        elif isinstance(value, dict):
            width, height = value.get("width"), value.get("height")
            if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
                raise ValueError("custom page size requires numeric width and height")
            width, height = float(width), float(height)
        else:
            raise ValueError("pageSize must be a named size or custom object")
        if orientation == "landscape": width, height = height, width
        elif orientation != "portrait": raise ValueError("orientation must be portrait or landscape")
        if not (1 <= width <= 14400 and 1 <= height <= 14400): raise ValueError("page dimensions must be between 1 and 14400 points")
        if margin is None: margins = (54.0, 50.0, 48.0, 50.0)
        elif isinstance(margin, (int, float)): margins = (float(margin),) * 4
        elif isinstance(margin, dict): margins = tuple(float(margin.get(k, 0)) for k in ("top", "right", "bottom", "left"))
        else: raise ValueError("margin must be a number or object")
        if any(x < 0 for x in margins) or margins[1] + margins[3] >= width or margins[0] + margins[2] >= height:
            raise ValueError("margins do not leave a printable area")
        return cls(width, height, margins)  # type: ignore[arg-type]


@dataclass(frozen=True)
class DocumentRequirements:
    """Capabilities inferred from semantics, not from the chosen backend."""
    required: frozenset[str]
    reasons: dict[str, str] = field(default_factory=dict)
    intent: str = "quality"

    @classmethod
    def infer(cls, kind: str, content: object, intent: str = "quality") -> "DocumentRequirements":
        text = str(content)
        required = {"metadata", "pagination"}; reasons: dict[str, str] = {}
        def need(name: str, why: str) -> None: required.add(name); reasons[name] = why
        need("font_embedding", "Chainabit visual artifacts require deterministic embedded typography")
        if any(ord(c) > 126 for c in text):
            need("unicode", "non-ASCII characters detected")
            if any(c in text for c in "ğĞşŞıİçÇöÖüÜ"): need("turkish", "Turkish characters detected")
            if any("\u0590" <= c <= "\u08ff" for c in text): need("rtl", "RTL characters detected")
            if any("\u3040" <= c <= "\u9fff" for c in text): need("cjk", "CJK characters detected")
            need("font_embedding", "Unicode requires a verified embedded font")
        if kind == "markdown":
            if re_search(r"^\s*(\||[-*+]\s|\d+[.)]\s|#{1,6}\s|>|```|~~~)", text): need("rich_markdown", "Markdown structure requires semantic layout")
            if "![" in text: need("images", "Markdown image syntax detected")
            if re_search(r"(\$\$|\\\(|\\\[|```math|<math\b)", text): need("math", "mathematical expression detected")
            if "\f" in text: need("page_breaks", "explicit page break detected")
        if kind == "report":
            if isinstance(content, dict):
                blocks = content.get("blocks", [])
                if any(isinstance(b, dict) and b.get("type") == "table" for b in blocks): need("tables", "report table block detected")
                if any(isinstance(b, dict) and b.get("type") in {"heading", "bullets", "numbered", "table"} for b in blocks): need("rich_markdown", "structured report semantics detected")
                if any(isinstance(b, dict) and b.get("type") == "image" for b in blocks): need("images", "report image block detected")
                if content.get("header") or content.get("footer"): need("headers_footers", "report header/footer requested")
                if content.get("color") or content.get("colors"): need("colors_rgb", "report color styling requested")
        if intent in {"professional", "print"}: need("print_quality", f"{intent} rendering requested")
        if intent in {"professional", "print"} and kind in {"markdown", "report"}: need("font_embedding", "professional typography requested")
        if intent == "basic" and required - {"metadata", "pagination", "page_breaks"}: reasons["intent"] = "basic intent cannot override document semantics"
        return cls(frozenset(required), reasons, intent)

def re_search(pattern: str, text: str) -> bool:
    import re
    return re.search(pattern, text, re.M | re.I) is not None
