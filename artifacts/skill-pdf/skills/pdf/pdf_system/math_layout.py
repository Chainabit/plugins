"""Lays a parsed equation out as HTML and CSS the print renderer can paint.

Plain inline boxes, inline blocks and table boxes; no MathML (the print engine
has none), no script, no asset. Every element is a span or a div carrying a
class, every class has a rule in `MATH_CSS`, and text is only ever a text node,
so an equation cannot introduce markup, a style or a fetch.

Two limits of the fonts shape the layout. Neither embedded family has an
italic, so variables slant with a skew transform; and script and fraktur
capitals are in neither family, so `\\mathcal` prints bold italic capitals.
"""
from __future__ import annotations

from typing import Iterable, List, Optional
from xml.etree.ElementTree import Element

from .math_parser import Node
from .math_symbols import (
    ACCENT_MARK, COMBINING, ENV_ALIGNED, ENV_CENTERED, ENV_MATRIX, ENV_PLAIN, ZWSP,
)

def el(tag: str, cls: str, *kids: Element, text: Optional[str] = None) -> Element:
    element = Element(tag, {"class": cls})
    if text is not None:
        element.text = text
    for kid in kids:
        element.append(kid)
    return element


def span(cls: str, *kids: Element, text: Optional[str] = None) -> Element:
    return el("span", cls, *kids, text=text)


def _opens(node: Optional[Node]) -> bool:
    """Whether a delimiter opens here: a named operator sits tight against one."""
    return node is not None and (
        (node.kind == "sym" and node.cls == "open") or node.kind in ("delim", "sized")
    )


def _height(nodes: Iterable[Node]) -> float:
    """How tall a row stands, in lines: 1 for plain text, more for stacked material."""
    tallest = 1.0
    for node in nodes:
        tallest = max(tallest, _node_height(node))
    return tallest


def _node_height(node: Node) -> float:
    kind = node.kind
    if kind == "frac":
        return _height(node.parts[0] or []) + _height(node.parts[1] or []) * 1.0 + 0.2
    if kind == "sqrt":
        return _height(node.parts[0] or []) + 0.15
    if kind == "scripts":
        base = _node_height(node.kids[0])
        if node.kids[0].kind == "bigop":
            return base + 0.5 * sum(_height(part) for part in node.parts if part)
        return base + (0.35 if node.parts[0] else 0.0) + (0.25 if node.parts[1] else 0.0)
    if kind == "bigop":
        return 1.5
    if kind in ("delim",):
        return _height(node.kids)
    if kind == "env":
        return max(1.0, float(len(node.parts))) * 1.15
    if kind in ("style", "accent", "line", "boxed"):
        return _height(node.kids) + (0.3 if kind in ("accent", "line") else 0.0)
    if kind == "brace":
        return _height(node.kids) + 0.7
    if kind == "stack":
        top, base, bottom = node.parts
        return _height(base or []) + 0.5 * (_height(top or []) if top else 0.0) + 0.5 * (_height(bottom or []) if bottom else 0.0)
    if kind == "lines":
        return max(1.0, float(len(node.parts))) * 0.8
    if kind == "row":
        return _height(node.kids)
    return 1.0


def _scale_class(lines: float) -> str:
    if lines < 1.3:
        return "z1"
    if lines < 2.0:
        return "z2"
    if lines < 2.8:
        return "z3"
    if lines < 3.8:
        return "z4"
    return "z5"


def _z_from_size(level: int) -> str:
    return {1: "z2", 2: "z3", 3: "z4", 4: "z5"}[level]


class Layout:
    """Nodes to elements. `style` is `D` (display), `T` (text) or `S` (script)."""

    def row(self, nodes: List[Node], style: str) -> List[Element]:
        out: List[Element] = []
        classes = self._classes(nodes)
        i = 0
        run_letters: List[str] = []

        def flush() -> None:
            if run_letters:
                out.append(span("mi", text="".join(run_letters)))
                run_letters.clear()

        while i < len(nodes):
            node = nodes[i]
            role = classes[i]
            if node.kind == "styleswitch":
                style = node.cls
                i += 1
                continue
            if node.kind == "sym" and role == "ital":
                run_letters.append(node.text)
                i += 1
                continue
            flush()
            follower = nodes[i + 1] if i + 1 < len(nodes) else None
            out.extend(self.node(node, role, style, follower))
            i += 1
        flush()
        return out

    def _classes(self, nodes: List[Node]) -> List[str]:
        """TeX's atom classes with binary operators demoted where they act as signs."""
        roles = []
        for node in nodes:
            roles.append(node.cls if node.kind == "sym" else node.kind)
        for i, node in enumerate(nodes):
            if node.kind == "sym" and node.cls == "punct" and node.text == ",":
                before = nodes[i - 1] if i else None
                after = nodes[i + 1] if i + 1 < len(nodes) else None
                if before is not None and after is not None and before.cls == "num" and after.cls == "num":
                    roles[i] = "ord"
            if node.kind == "sym" and node.cls == "bin":
                before = roles[i - 1] if i else None
                after_missing = i + 1 >= len(nodes)
                if before in (None, "bin", "rel", "open", "punct", "bigop", "fn") or after_missing:
                    roles[i] = "ord"
            if node.kind == "text" and node.meta.get("bin"):
                roles[i] = "bin" if i and roles[i - 1] not in ("bin", "rel", "open", "punct") else "ord"
        return roles

    def node(self, node: Node, role: str, style: str, follower: Optional[Node]) -> List[Element]:
        kind = node.kind
        if kind == "sym":
            return [self._symbol(node, role, style)]
        if kind == "row":
            return self.row(node.kids, style)
        if kind == "space":
            return [span("msp msp-" + node.cls)]
        if kind == "break":
            return [span("mnl")]
        if kind == "text":
            return [self._text(node, role)]
        if kind == "fn":
            return [self._function(node, follower, style)]
        if kind == "bigop":
            return [self._bigop(node, style, None, None)]
        if kind == "scripts":
            return self._scripts(node, style, follower)
        if kind == "frac":
            return self._fraction(node, style)
        if kind == "sqrt":
            return [self._root(node, style)]
        if kind == "delim":
            return self._delimited(node, style)
        if kind == "sized":
            return [span("mdl mdl-%s" % _z_from_size(int(node.cls)), text=node.text)]
        if kind == "style":
            return [span("mf-" + node.cls, *self.row(node.kids, style))]
        if kind == "accent":
            return [self._accent(node, style)]
        if kind == "line":
            return [span("mline-over" if node.text == "overline" else "mline-under", *self.row(node.kids, style))]
        if kind == "brace":
            return [self._brace(node, style)]
        if kind == "stack":
            return [self._stack(node, style)]
        if kind == "lines":
            return [self._lines(node, style)]
        if kind == "boxed":
            return [span("mbox", *self.row(node.kids, style))]
        if kind == "env":
            return [self._environment(node, style)]
        if kind == "tag":
            return [span("mtag", text="(" + node.text + ")")]
        return []

    # -- atoms
    def _symbol(self, node: Node, role: str, style: str = "T") -> Element:
        if style == "S" and role in ("bin", "rel"):
            role = "ord"  # script style sets operators tight
        cls = {"num": "mn", "ord": "mo", "open": "mo mop", "close": "mo mcl", "punct": "mo mpu", "bin": "mo mbn", "rel": "mo mrl", "ital": "mi"}[role]
        text = node.text
        if role == "punct" and text == ",":
            cls = "mo mpu"
        if role in ("bin", "rel"):
            text = text + ZWSP
        return span(cls, text=text)

    def _text(self, node: Node, role: str) -> Element:
        cls = "mtx"
        if node.cls:
            cls += " mf-" + node.cls
        if role == "bin":
            cls += " mbn"
        return span(cls, text=node.text)

    def _function(self, node: Node, follower: Optional[Node], style: str) -> Element:
        return span("mfn" + (" mfn-tight" if _opens(follower) else ""), text=node.text)

    def _bigop(self, node: Node, style: str, sup: Optional[List[Node]], sub: Optional[List[Node]]) -> Element:
        display = style == "D"
        integral = not node.meta.get("limits")
        glyph = span("mbig" + ((" mbig-i" if integral else " mbig-d") if display else ""), text=node.text)
        if sup is None and sub is None:
            return span("mopwrap", glyph)
        limits = node.meta.get("limits") and display
        if limits:
            parts = []
            if sup:
                parts.append(span("mlim-t mS", *self.row(sup, "S")))
            parts.append(span("mlim-g", glyph))
            if sub:
                parts.append(span("mlim-b mS", *self.row(sub, "S")))
            return span("mlim", *parts)
        # Side limits. In display style an integral's limits sit at its two ends.
        side: List[Element] = []
        if sup:
            side.append(span("mss-t", *self.row(sup, "S")))
        if display and sup and sub:
            side.append(span("mss-gap"))
        if sub:
            side.append(span("mss-b", *self.row(sub, "S")))
        stack = span("mss mS" + (" mss-d" if display else ""), *side)
        return span("mopwrap", glyph, stack)

    def _scripts(self, node: Node, style: str, follower: Optional[Node]) -> List[Element]:
        base = node.kids[0]
        sup, sub = node.parts[0], node.parts[1]
        if base.kind == "bigop":
            return [self._bigop(base, style, sup, sub)]
        if base.kind == "fn" and base.meta.get("limits") and style == "D":
            parts = [span("mlim-g", span("mfn mfn-bare", text=base.text))]
            if sup:
                parts.insert(0, span("mlim-t mS", *self.row(sup, "S")))
            if sub:
                parts.append(span("mlim-b mS", *self.row(sub, "S")))
            return [span("mlim mlim-fn", *parts)]
        if base.kind == "brace":
            labelled = self._labelled_brace(base, sup, sub, style)
            if labelled is not None:
                return [labelled]
        elements = self._base(base, style)
        tall = _height([base]) > 1.4
        if sup and sub:
            scripts = span(
                "mss mS" + (" mss-tall" if tall else ""),
                span("mss-t", *self.row(sup, "S")),
                span("mss-b", *self.row(sub, "S")),
            )
        elif sup:
            scripts = span("msup mS" + (" msup-tall" if tall else ""), *self.row(sup, "S"))
        else:
            scripts = span("msub mS" + (" msub-tall" if tall else ""), *self.row(sub or [], "S"))
        elements.append(scripts)
        if base.kind == "fn" and not _opens(follower):
            elements.append(span("msp msp-thin"))
        return elements

    def _labelled_brace(self, base: Node, sup, sub, style: str) -> Optional[Element]:
        """A brace and the label it carries, centred on the side the brace opens to."""
        over = base.text == "overbrace"
        label = sup if over else sub
        if not label or (sub if over else sup):
            return None
        held = self._brace(base, style)
        text = span("mstk-t mS" if over else "mstk-u mS", *self.row(label, "S"))
        return span("mstk", *( [text, span("mstk-b", held)] if over else [span("mstk-b", held), text] ))

    def _base(self, base: Node, style: str) -> List[Element]:
        if base.kind == "sym":
            return [self._symbol(base, "ord" if base.cls == "bin" else base.cls, style)]
        if base.kind == "fn":
            return [span("mfn mfn-bare", text=base.text)]
        return self.node(base, base.kind, style, None)

    # -- structures
    def _fraction(self, node: Node, style: str) -> List[Element]:
        forced = node.meta.get("style")
        inner = forced or style
        child = "S" if inner in ("T", "S") else "T"
        numerator = self.row(node.parts[0] or [], child)
        denominator = self.row(node.parts[1] or [], child)
        shrink = " mS" if inner in ("T", "S") else ""
        if node.meta.get("binom"):
            lines = _height(node.parts[0] or []) + _height(node.parts[1] or [])
            zoom = _scale_class(lines)
            body = span("mfrac mfrac-nobar" + shrink, span("mnum", *numerator), span("mden", *denominator))
            return [span("mdl mdl-" + zoom, text="("), body, span("mdl mdl-" + zoom, text=")")]
        return [span("mfrac" + shrink, span("mnum", *numerator), span("mden", *denominator))]

    def _root(self, node: Node, style: str) -> Element:
        radicand = node.parts[0] or []
        zoom = _scale_class(_height(radicand))
        body = span("mrb", *self.row(radicand, style))
        index = node.parts[1]
        pieces: List[Element] = []
        if index:
            pieces.append(span("mri mS", *self.row(index, "S")))
        pieces.append(span("mrad mrad-" + zoom, text="√"))
        pieces.append(body)
        return span("msqrt", *pieces)

    def _delimited(self, node: Node, style: str) -> List[Element]:
        opening, closing = node.meta["open"], node.meta["close"]
        zoom = _scale_class(_height(node.kids))
        parts: List[Element] = []
        if opening:
            parts.append(span("mdl mdl-" + zoom, text=opening))
        parts.extend(self.row(node.kids, style))
        if closing:
            parts.append(span("mdl mdl-" + zoom, text=closing))
        return parts

    def _accent(self, node: Node, style: str) -> Element:
        body = node.kids
        name = node.text
        if name in COMBINING and len(body) == 1 and body[0].kind == "sym" and len(body[0].text) == 1:
            role = "mi" if body[0].cls == "ital" else "mo"
            return span(role, text=body[0].text + COMBINING[name])
        mark = COMBINING.get(name)
        symbol = {"hat": "^", "check": "ˇ", "tilde": "˜", "bar": "¯", "vec": "→", "dot": "˙", "ddot": "¨", "acute": "´", "grave": "`", "breve": "˘"}.get(name) if mark else ACCENT_MARK[name]
        return span("macc", span("macc-m", text=symbol), span("macc-b", *self.row(body, style)))

    def _brace(self, node: Node, style: str) -> Element:
        return span("mbrace-" + ("over" if node.text == "overbrace" else "under"), *self.row(node.kids, style))

    def _stack(self, node: Node, style: str) -> Element:
        top, base, bottom = node.parts
        parts: List[Element] = []
        if top:
            parts.append(span("mstk-t mS", *self.row(top, "S")))
        parts.append(span("mstk-b", *self.row(base or [], style)))
        if bottom:
            parts.append(span("mstk-u mS", *self.row(bottom, "S")))
        return span("mstk", *parts)

    def _lines(self, node: Node, style: str) -> Element:
        rows = [span("mln", *self.row(line or [], "S")) for line in node.parts]
        return span("mlines mlines-sm", *rows)

    # -- environments
    def _environment(self, node: Node, style: str) -> Element:
        name = node.text
        rows = node.parts
        if name in ENV_PLAIN:
            merged: List[Node] = []
            for row in rows:
                for cell in row:
                    merged.extend(cell)
            return span("mlines", *self.row(merged, style))
        if name in ENV_CENTERED:
            lines = [span("mln", *self.row([n for cell in row for n in cell], style)) for row in rows]
            return span("mlines", *lines)
        aligned = name in ENV_ALIGNED
        columns = node.meta.get("columns", "")
        letters = [c for c in columns if c in "lcr"]
        verticals = set()
        position = 0
        for char in columns:
            if char in "lcr":
                position += 1
            elif char == "|":
                verticals.add(position)
        rules = node.meta.get("rules", [])
        ruled = bool(any(rules) or node.meta.get("bottom") or verticals)
        table_rows: List[Element] = []
        for i, row in enumerate(rows):
            cells: List[Element] = []
            for j, cell in enumerate(row):
                if name == "cases":
                    align = "l"
                elif aligned:
                    align = "r" if j % 2 == 0 else "l"
                elif letters:
                    align = letters[j] if j < len(letters) else "c"
                else:
                    align = "c"
                extra = ""
                if i < len(rules) and rules[i]:
                    extra += " mc-rt"
                if node.meta.get("bottom") and i == len(rows) - 1:
                    extra += " mc-rb"
                if j in verticals:
                    extra += " mc-vl"
                if j == len(row) - 1 and len(row) in verticals:
                    extra += " mc-vr"
                cells.append(span("mcell mc-" + align + (" mc-gap" if aligned and j % 2 == 0 and j else "") + extra, *self.row(cell, "T" if name in ENV_MATRIX or name == "cases" else style)))
            table_rows.append(span("mrow", *cells))
        grid = span("mgrid" + (" mgrid-ruled" if ruled else ""), *table_rows)
        if name in ENV_MATRIX:
            shape = ENV_MATRIX[name]
            small = " mmat-sm" if name == "smallmatrix" else ""
            return span("mmat" + small, span("mbr mbr-l mbr-" + shape), grid, span("mbr mbr-r mbr-" + shape))
        if name == "cases":
            zoom = _scale_class(max(1.0, float(len(rows))) * 1.05)
            return span("mmat mcases", span("mdl mdl-" + zoom, text="{"), grid)
        if aligned:
            return span("maligned", grid)
        return span("mmat", grid)



# ── stylesheet ───────────────────────────────────────────────────────────────

MATH_CSS = """
.m{font-style:normal;font-weight:400}
.m.md{display:block;text-align:center;margin:10pt 0;font-size:1.1em;line-height:1.5;page-break-inside:avoid}
.mi{display:inline-block;transform:skewX(-11deg);transform-origin:0 78%}
.mn,.mo,.mfn,.mtx{font-style:normal}
.mtx{white-space:pre-wrap}
.mo.mbn{margin:0 .22em}.mo.mrl{margin:0 .28em}.mo.mpu{margin-right:.17em}
.mo.mop{margin-right:.02em}.mo.mcl{margin-left:.02em}
.mtx.mbn{margin:0 .22em}
.mfn{margin-right:.17em}.mfn.mfn-tight{margin-right:.04em}.mfn.mfn-bare{margin-right:0}
.msp{display:inline-block;height:1px}
.msp-thin{width:.17em}.msp-med{width:.22em}.msp-thick{width:.28em}.msp-neg{width:0;margin-left:-.17em}
.msp-quad{width:1em}.msp-qquad{width:2em}.msp-en{width:.5em}
.mnl{display:block;height:0}
.mS{font-size:.72em}
.msup,.msub{line-height:0}
.msup{vertical-align:.62em}.msub{vertical-align:-.32em}
.msup-tall{vertical-align:1.05em}.msub-tall{vertical-align:-.8em}
.mss{display:inline-block;vertical-align:middle;line-height:1.05;text-align:left}
.mss-t,.mss-b{display:block}
.mss-gap{display:block;height:.7em}.mss-d{margin-left:.08em}
.mss-tall{vertical-align:middle}
.mfrac{display:inline-block;vertical-align:middle;text-align:center;margin:0 .12em}
.mnum,.mden{display:block;padding:0 .2em;line-height:1.28}
.mnum{border-bottom:.055em solid currentColor}
.mfrac-nobar .mnum{border-bottom:0}
.msqrt{white-space:nowrap}
.mrad{margin-right:-.06em;line-height:1}
.mrad-z1{font-size:1.1em}.mrad-z2{font-size:1.55em}.mrad-z3{font-size:2.1em}.mrad-z4{font-size:2.75em}.mrad-z5{font-size:3.4em}
.mrb{display:inline-block;vertical-align:baseline;line-height:1.15;border-top:.055em solid currentColor;padding:.1em .1em 0 .06em}
.mri{vertical-align:.7em;margin-right:-.3em}
.mdl{display:inline-block;vertical-align:middle;line-height:1}
.mdl-z1{font-size:1em}.mdl-z2{font-size:1.4em}.mdl-z3{font-size:1.85em}.mdl-z4{font-size:2.4em}.mdl-z5{font-size:3em}
.mopwrap{white-space:nowrap}
.mbig{display:inline-block;vertical-align:middle;line-height:1;margin:0 .05em}
.mbig-d{font-size:1.45em}.mbig-i{font-size:1.9em}
.mlim{display:inline-block;vertical-align:middle;text-align:center;margin:0 .08em}
.mlim-t,.mlim-b,.mlim-g{display:block;line-height:1.15}
.mlim-fn .mlim-g{padding:.1em 0}
.mf-bf{font-weight:600}.mf-bf .mi{transform:none;font-weight:600}
.mf-bfi{font-weight:600}.mf-bfi .mi{font-weight:600}
.mf-rm .mi,.mf-sf .mi,.mf-tt .mi{transform:none}
.mf-sf,.mf-tt{font-style:normal}
.mf-tt{font-family:"Fira Code","Noto Sans Mono",monospace}
.mtx.mf-cal{display:inline-block;font-weight:600;transform:skewX(-11deg);transform-origin:0 78%}
.mtx.mf-it{display:inline-block;transform:skewX(-11deg);transform-origin:0 78%}
.macc{display:inline-block;vertical-align:baseline;text-align:center;line-height:1}
.macc-m,.macc-b{display:block}.macc-m{line-height:.6;height:.6em}
.mline-over{display:inline-block;line-height:1.15;border-top:.055em solid currentColor;padding-top:.04em}
.mline-under{display:inline-block;line-height:1.15;border-bottom:.055em solid currentColor;padding-bottom:.04em}
.mbrace-over,.mbrace-under{display:inline-block;padding:.05em .1em;border-color:currentColor;border-style:solid;border-width:0;border-radius:.4em}
.mbrace-over{border-top-width:.07em}.mbrace-under{border-bottom-width:.07em}
.mstk{display:inline-block;vertical-align:middle;text-align:center}
.mstk-t,.mstk-b,.mstk-u{display:block;line-height:1.1}
.mlines{display:block;text-align:center}.mlines-sm{display:inline-block;vertical-align:middle;line-height:1.1;font-size:.72em}
.mln{display:block}
.mbox{display:inline-block;border:.07em solid currentColor;padding:.15em .35em}
.mtag{float:right;margin-left:1em}
.mmat{display:inline-table;vertical-align:middle;border-spacing:0;margin:0 .15em}
.mgrid{display:table;border-spacing:.9em .28em}
.mrow{display:table-row}.mcell{display:table-cell;vertical-align:middle}
.mc-l{text-align:left}.mc-c{text-align:center}.mc-r{text-align:right}
.mc-gap{padding-left:.4em}
.mgrid-ruled{border-collapse:collapse;border-spacing:0}.mgrid-ruled .mcell{padding:.14em .55em}
.mc-rt{border-top:.06em solid currentColor}.mc-rb{border-bottom:.06em solid currentColor}
.mc-vl{border-left:.06em solid currentColor}.mc-vr{border-right:.06em solid currentColor}
.maligned{display:inline-table;vertical-align:middle;text-align:left}
.maligned .mgrid{border-spacing:0 .3em}
.maligned .mcell{padding:0}
.mmat-sm{font-size:.82em}
.mbr{display:table-cell;width:.34em;border:0 solid currentColor}
.mbr-none{width:.1em}
.mbr-l.mbr-square{border-width:.07em 0 .07em .07em}.mbr-r.mbr-square{border-width:.07em .07em .07em 0}
.mbr-l.mbr-bar{border-left-width:.07em}.mbr-r.mbr-bar{border-right-width:.07em}
.mbr-l.mbr-dbar{border-left-width:.22em;border-left-style:double}.mbr-r.mbr-dbar{border-right-width:.22em;border-right-style:double}
.mbr-l.mbr-paren{border-left-width:.085em;border-radius:.9em 0 0 .9em / 50% 0 0 50%}
.mbr-r.mbr-paren{border-right-width:.085em;border-radius:0 .9em .9em 0 / 0 50% 50% 0}
.mbr-l.mbr-brace{border-left-width:.07em;border-radius:.6em 0 0 .6em}
.mbr-r.mbr-brace{border-right-width:.07em;border-radius:0 .6em .6em 0}
.mcases{display:inline-table}
.mcases .mdl{display:table-cell}
"""

