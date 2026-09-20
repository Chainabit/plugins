"""TeX mathematics, laid out as HTML and CSS the print renderer can paint.

The renderer used to project an equation as the expression's own text inside a
MathML run. The print engine does not implement MathML, so the source
characters reached the PDF as they were written: backslashes, braces and all.
Only four commands were ever refused, by a message that named no equation, so
a document made of fractions could neither render nor be repaired.

This is the public face of the math reader, in three parts with one direction of
dependency: `math_symbols` (what each command prints), `math_parser` (the
syntax tree, or a refusal by command name) and `math_layout` (the boxes and the
stylesheet that paints them). It needs no third-party package and no font beyond
the two the renderer already embeds.

A construct the subset does not cover is refused by name (`MathError`), never
printed as source. The caller reports which equation and which command, so the
author can repair that equation alone.
"""
from __future__ import annotations

from typing import List
from xml.etree.ElementTree import Element

from .math_layout import MATH_CSS, Layout, el, span
from .math_parser import MAX_DEPTH, MAX_EXPRESSION_CHARS, MAX_NODES, MathError, Node, parse
from .math_symbols import environments, supported_commands, symbol_glyphs

__all__ = [
    "MATH_CSS", "MAX_DEPTH", "MAX_EXPRESSION_CHARS", "MAX_NODES", "MathError",
    "environments", "render_math", "supported_commands", "symbol_glyphs",
]


# ── public entry point ───────────────────────────────────────────────────────

def render_math(expression: str, display: bool = False, block: bool = False) -> Element:
    """Lay out one equation. Raises `MathError` for anything the subset does not cover.

    `display` is TeX's display style (large operators, stacked limits); `block`
    additionally makes the result a block box for an equation on its own line.
    """
    nodes = parse(expression.strip())
    layout = Layout()
    style = "D" if display else "T"
    pieces: List[Element] = []
    lines: List[List[Node]] = [[]]
    tags: List[Node] = []
    for node in nodes:
        if node.kind == "break":
            lines.append([])
        elif node.kind == "tag":
            tags.append(node)
        else:
            lines[-1].append(node)
    if len(lines) > 1 and display:
        for line in lines:
            pieces.append(span("mln", *layout.row(line, style)))
        body: List[Element] = [span("mlines", *pieces)]
    else:
        body = layout.row([n for line in lines for n in line], style)
    for tag in reversed(tags):
        body.insert(0, span("mtag", text="(" + tag.text + ")"))
    tag_name = "div" if block else "span"
    return el(tag_name, "m md" if display else "m", *body)

