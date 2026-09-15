"""CommonMark Markdown, plus GFM tables, projected into the renderer's HTML.

The runtime pins Python-Markdown. Its block parser nests list content at one
fixed four-column tab stop, and it needs a blank line before a list or a table
that follows a paragraph. CommonMark nests by each item's content column (two
columns under ``- item``, three under ``1. item``) and lets a list, a table, a
fence or a heading interrupt a paragraph. Model-written Markdown follows
CommonMark. Read by Python-Markdown alone, nested items flattened into sibling
lines, a numbered list under a lead-in sentence stayed one paragraph, a table
under a lead-in line printed its pipes, and a fence inside a list item printed
its backticks.

`_BlockNormalizer` is the one place that knows both dialects. It rewrites
container indentation, list delimiters, paragraph interruptions and setext
headings from CommonMark's rules into the layout Python-Markdown parses the same
way, and it changes no text. Everything inline (emphasis, code spans, links,
images, escapes) stays with Python-Markdown.

A rule of dashes is the case where the two dialects disagree in public. In
CommonMark ``---`` on the line directly after a paragraph is that paragraph's
setext underline, not a thematic break; only a blank line before it makes it a
rule. Python-Markdown reads a setext underline solely beneath a one-line
paragraph at the document root, so the same three dashes under a list item were
neither a heading nor a rule and printed as characters. The normalizer therefore
resolves a setext heading itself, into the ATX form the parser reads alike at
every depth, and no underline reaches it.

Raw HTML is text here, never markup. The renderer is not an HTML sanitizer, and
an author's tag must not become a styling or asset boundary. The single
exception is a bare ``<br>``, which a table cell needs for a line break. Images
cross the audited image boundary and are embedded as data URIs. Links keep web
and mail targets only.
"""
from __future__ import annotations

import re
import string
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from .errors import ErrorCode, PdfError
from .models import SecurityPolicy
from .safety import image_data_uri, safe_asset_uri

_THEMATIC_BREAK = re.compile(r"^(?:(?:\*[ ]*){3,}|(?:-[ ]*){3,}|(?:_[ ]*){3,})$")
_SETEXT_UNDERLINE = re.compile(r"^(?:=+|-+)[ ]*$")
_ATX_HEADING = re.compile(r"^#{1,6}(?:[ ]|$)")
_FENCE_OPEN = re.compile(r"^(?P<fence>`{3,}(?=[^`]*$)|~{3,})")
_FENCE_CLOSE = re.compile(r"^[ ]{0,3}(?P<fence>`{3,}|~{3,})[ ]*$")
_LIST_ITEM = re.compile(
    r"^(?P<marker>[-+*]|(?P<number>\d{1,9})[.)])(?P<gap>[ ]+|$)(?P<rest>.*)$"
)
_TABLE_DELIMITER = re.compile(r"^\|?[ ]*:?-+:?[ ]*(?:\|[ ]*:?-+:?[ ]*)*\|?[ ]*$")
_QUOTE_MARKER = re.compile(r"^[ ]{0,3}>[ ]?")
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_TRAILING_HASHES = re.compile(r"#+$")

# Math keeps the renderer's projection: the expression's text in a MathML run,
# without its delimiters. A single-dollar span needs no space inside either
# delimiter and no digit after the closing one, so "$5 and $10" stays text.
_MATH = (
    r"(?<!\\)\$\$(?P<display>.+?)\$\$"
    r"|\\\((?P<paren>.+?)\\\)"
    r"|\\\[(?P<bracket>.+?)\\\]"
    r"|(?<![\\$\w])\$(?![\s$])(?P<inline>[^$\n]*?[^\s\\$])\$(?![\d$])"
)
_UNSUPPORTED_MATH = re.compile(r"\\(?:frac|sqrt|begin|end|newcommand)\b")

LINK_SCHEMES = frozenset({"http", "https", "mailto"})


@dataclass(frozen=True)
class _Item:
    content: int  # source column where the item's content starts
    ordered: bool


@dataclass(frozen=True)
class _Paragraph:
    """The open paragraph, as the one thing a setext underline can close."""

    depth: int  # container depth its lines are emitted at
    start: int  # index of its first line in the output
    on_marker: bool  # it began on its list item's marker line


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _can_interrupt_paragraph(item: re.Match) -> bool:
    """CommonMark: only a non-empty bullet, or a list starting at 1, ends a paragraph."""
    if not item.group("rest").strip():
        return False
    number = item.group("number")
    return number is None or int(number) == 1


def _cells(row: str) -> int:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return len(_UNESCAPED_PIPE.split(row))


def _literal(text: str) -> str:
    """Paragraph text that Python-Markdown would otherwise read as a block start.

    A continuation line is re-indented to its paragraph's container, so a
    marker CommonMark reads as text (``2. x`` continuing a sentence, or
    ``#tag``) would become a list item or a heading there.
    """
    item = _LIST_ITEM.match(text)
    if item:
        number = item.group("number")
        return number + "\\" + text[len(number):] if number else "\\" + text
    if text.startswith("#") and not _ATX_HEADING.match(text):
        return "\\" + text
    if _SETEXT_UNDERLINE.match(text.rstrip()) and text.startswith("="):
        return "\\" + text
    return text


class _BlockNormalizer:
    """Rewrite CommonMark block structure into the layout Python-Markdown parses alike.

    Python-Markdown nests every container at four columns, so a line inside
    ``n`` open list items is written at ``4 * n`` spaces plus whatever
    indentation it has beyond its item's content column. A block that
    CommonMark lets interrupt a paragraph is separated by a blank line, which
    is what Python-Markdown needs to start a block.
    """

    def __init__(self, nested: bool = False):
        # Python-Markdown supports fences only at the document root, so a
        # fence inside a list item or a block quote becomes an indented code
        # block. Only the language hint is lost, and nothing here highlights.
        self.nested = nested
        self.out: list[str] = []
        self.items: list[_Item] = []
        self.paragraph: _Paragraph | None = None
        self.table: int | None = None  # container depth of an open table
        self.block_depth: int | None = None  # depth of the current block's first line

    def run(self, lines: list[str]) -> list[str]:
        index = 0
        while index < len(lines):
            index = self._step(lines, index)
        return self.out

    def _blank(self) -> None:
        if self.out and self.out[-1] != "":
            self.out.append("")
        self.block_depth = None

    def _emit(self, depth: int, text: str) -> None:
        # A shallower line inside a block that began deeper would be read
        # as part of the deeper container, so it starts a block of its own.
        if self.block_depth is not None and depth < self.block_depth:
            self._blank()
        if self.block_depth is None:
            self.block_depth = depth
        self.out.append(" " * (4 * depth) + text)

    def _starts_block(self, line: str, column: int) -> bool:
        if _indent(line) - column > 3:
            return False
        text = line.lstrip(" ")
        probe = text.rstrip()
        item = _LIST_ITEM.match(text)
        return bool(
            _THEMATIC_BREAK.match(probe) or _ATX_HEADING.match(probe)
            or _FENCE_OPEN.match(probe) or probe.startswith(">")
            or (item and _can_interrupt_paragraph(item))
        )

    def _table_header(self, lines: list[str], index: int, column: int) -> bool:
        if "|" not in lines[index] or index + 1 >= len(lines):
            return False
        delimiter = lines[index + 1]
        if not delimiter.strip() or "|" not in delimiter:
            return False
        if _indent(delimiter) < column or _indent(delimiter) - column > 3:
            return False
        probe = delimiter.strip()
        return bool(_TABLE_DELIMITER.match(probe)) and _cells(probe) == _cells(lines[index])

    def _step(self, lines: list[str], index: int) -> int:
        line = lines[index]
        if not line.strip():
            # Kept one for one: Python-Markdown splits blocks on blank lines
            # and relies on the two it appends to end the last block cleanly.
            self.out.append("")
            self.block_depth = None
            self.paragraph = None
            self.table = None
            return index + 1

        indent = _indent(line)
        keep = len(self.items)
        while keep and indent < self.items[keep - 1].content:
            keep -= 1
        column = self.items[keep - 1].content if keep else 0
        relative = indent - column
        text = line.lstrip(" ")
        probe = text.rstrip()
        opens = relative <= 3
        rule = opens and bool(_THEMATIC_BREAK.match(probe))
        fence = _FENCE_OPEN.match(probe) if opens and not rule else None
        heading = opens and bool(_ATX_HEADING.match(probe))
        quote = opens and probe.startswith(">")
        item = _LIST_ITEM.match(text) if opens and not rule else None
        table = opens and self._table_header(lines, index, column)

        if self.table is not None:
            if keep >= self.table and not (rule or fence or heading or quote or item):
                self._emit(self.table, text)
                return index + 1
            self.table = None

        if self.paragraph is not None:
            if opens and self.paragraph.depth == keep and _SETEXT_UNDERLINE.match(probe):
                self._setext(self.paragraph, probe)  # the paragraph is a heading
                return index + 1
            interrupts = rule or fence or heading or quote or table or bool(
                item and (self.paragraph.depth != keep or _can_interrupt_paragraph(item))
            )
            if not interrupts:
                # CommonMark lazy continuation: the line belongs to the open
                # paragraph whatever its indentation.
                self._emit(self.paragraph.depth, _literal(text))
                return index + 1

        open_paragraph = self.paragraph
        closed = self.items[keep:]
        del self.items[keep:]
        self.paragraph = None

        if fence:
            return self._fence(lines, index, keep, column, fence.group("fence"), relative)
        if rule or heading:
            self._blank()
            self._emit(keep, text)
            self._blank()
            return index + 1
        if quote:
            return self._quote(lines, index, keep, column)
        if table:
            self._blank()
            self._emit(keep, text)
            self._emit(keep, lines[index + 1].lstrip(" "))
            self.table = keep
            return index + 2
        if item:
            return self._item(item, index, keep, indent, closed, open_paragraph)
        if relative > 3:
            self._emit(keep, " " * relative + text)  # indented code
        else:
            self._emit(keep, _literal(text))
            self.paragraph = _Paragraph(keep, len(self.out) - 1, False)
        return index + 1

    def _setext(self, paragraph: _Paragraph, underline: str) -> None:
        """Rewrite an open paragraph as the ATX heading its underline declares.

        Python-Markdown reads an underline only beneath a one-line paragraph at
        the document root, so the heading is resolved here rather than forwarded:
        the paragraph's lines become the heading's text, and one that began on a
        list item's marker line keeps that marker.
        """
        lines = self.out[paragraph.start:]
        del self.out[paragraph.start:]
        head = lines[0]
        marker = _LIST_ITEM.match(head.lstrip(" ")) if paragraph.on_marker else None
        opening = head[:_indent(head) + (marker.end("gap") if marker else 0)]
        words = " ".join(" ".join(line.split()) for line in [head[len(opening):], *lines[1:]]).strip()
        # Python-Markdown reads a trailing run of hashes as an ATX closing
        # sequence and drops it; CommonMark keeps it as the heading's own text.
        words = _TRAILING_HASHES.sub(lambda run: "".join("\\" + char for char in run.group()), words)
        text = f"{opening}{'#' if underline.startswith('=') else '##'} {words}"
        self.paragraph = None
        if marker:
            # The heading is that item's own first block, so the line stays a
            # list item line and the list around it is left unbroken.
            self.out.append(text)
            self.block_depth = paragraph.depth - 1
            return
        self._blank()
        self.out.append(text)
        self._blank()

    def _item(self, item: re.Match, index: int, depth: int, indent: int,
              closed: list[_Item], open_paragraph: _Paragraph | None) -> int:
        number = item.group("number")
        ordered = number is not None
        if closed and closed[0].ordered != ordered:
            self._blank()  # a different list type starts a new list
        elif open_paragraph is not None and open_paragraph.depth == depth and not open_paragraph.on_marker:
            self._blank()  # the list interrupts a paragraph
        marker, gap, rest = item.group("marker"), item.group("gap"), item.group("rest")
        if rest and len(gap) <= 4:
            content = indent + len(marker) + len(gap)
        else:
            content = indent + len(marker) + 1
            rest = gap[1:] + rest if rest else ""
        self._emit(depth, f"{number}. {rest}" if ordered else f"{marker} {rest}")
        self.items.append(_Item(content, ordered))
        self.paragraph = _Paragraph(depth + 1, len(self.out) - 1, True) if rest.strip() else None
        return index + 1

    def _fence(self, lines: list[str], index: int, depth: int, column: int,
               marker: str, fence_indent: int) -> int:
        convert = self.nested or depth > 0
        self._blank()
        if not convert:
            # A bare fence: Python-Markdown matches a one-word info string
            # only, and a closing fence must repeat the opening one exactly.
            self._emit(0, marker)
        index += 1
        while index < len(lines):
            line = lines[index]
            if line.strip() and _indent(line) < column:
                break  # the enclosing list item ended, and its code block with it
            body = line[column:]
            closing = _FENCE_CLOSE.match(body)
            if closing and closing.group("fence")[0] == marker[0] and len(closing.group("fence")) >= len(marker):
                if not convert:
                    self.out.append(marker)
                self._blank()
                return index + 1
            content = body[min(fence_indent, _indent(body)):]
            if convert:
                self.out.append(" " * (4 * depth + 4) + content if content else "")
            else:
                self.out.append(content)
            index += 1
        if not convert:
            raise PdfError(ErrorCode.INVALID_INPUT, "unclosed Markdown code block")
        self._blank()
        return index

    def _quote(self, lines: list[str], index: int, depth: int, column: int) -> int:
        inner: list[str] = []
        text_open = False
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                break
            body = line[column:] if _indent(line) >= column else ""
            marker = _QUOTE_MARKER.match(body)
            if marker:
                content = body[marker.end():]
                probe = content.strip()
                text_open = bool(probe) and not (
                    _ATX_HEADING.match(probe) or _THEMATIC_BREAK.match(probe)
                    or _FENCE_OPEN.match(probe)
                )
                inner.append(content)
            elif text_open and not self._starts_block(line, column):
                inner.append(line.lstrip(" "))  # lazy continuation inside the quote
            else:
                break
            index += 1
        quoted = _BlockNormalizer(nested=True).run(inner)
        while quoted and quoted[-1] == "":
            quoted.pop()
        self._blank()
        for text in quoted:
            self._emit(depth, "> " + text if text else ">")
        self._blank()
        return index


def _embedded_image(uri: str, policy: SecurityPolicy) -> str:
    path = safe_asset_uri(unquote(uri), policy)
    if path is None:
        raise PdfError(
            ErrorCode.UNSAFE_INPUT,
            "remote images are not fetched; save the image beside the Markdown file and reference it by a relative path",
        )
    return image_data_uri(path, policy)


def _linkable(href: str) -> bool:
    return urlparse(href).scheme.lower() in LINK_SCHEMES


def render_markdown(text: str, policy: SecurityPolicy) -> str:
    """Return the HTML body for Markdown text; images are read under `policy`."""
    try:
        import xml.etree.ElementTree as etree

        import markdown
        from markdown.extensions import Extension
        from markdown.inlinepatterns import InlineProcessor, SimpleTagInlineProcessor
        from markdown.preprocessors import Preprocessor
        from markdown.treeprocessors import Treeprocessor
        from markdown.util import AtomicString
    except ImportError as exc:
        raise PdfError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Markdown rendering requires the Python package 'markdown'",
        ) from exc

    class CommonMarkBlocks(Preprocessor):
        def run(self, lines: list[str]) -> list[str]:
            return _BlockNormalizer().run(lines)

    class LineBreakTag(InlineProcessor):
        def handleMatch(self, match, data):  # noqa: N802 - Python-Markdown API
            return etree.Element("br"), match.start(0), match.end(0)

    class InlineMath(InlineProcessor):
        def handleMatch(self, match, data):  # noqa: N802 - Python-Markdown API
            expression = next(value for value in match.groupdict().values() if value is not None)
            if _UNSUPPORTED_MATH.search(expression):
                raise PdfError(ErrorCode.UNSUPPORTED_CAPABILITY, "equation uses unsupported or unsafe math syntax")
            math = etree.Element("math")
            etree.SubElement(etree.SubElement(math, "mrow"), "mi").text = AtomicString(expression)
            return math, match.start(0), match.end(0)

    class ExternalResources(Treeprocessor):
        def run(self, root):
            for image in root.iter("img"):
                image.set("src", _embedded_image(image.get("src", ""), policy))
            for link in root.iter("a"):
                if not _linkable(link.get("href", "")):
                    link.tag = "span"
                    link.attrib.clear()

    class Dialect(Extension):
        def extendMarkdown(self, md):  # noqa: N802 - Python-Markdown API
            md.preprocessors.deregister("html_block")
            md.inlinePatterns.deregister("html")
            # After whitespace normalization (30), before fences are stashed (25).
            md.preprocessors.register(CommonMarkBlocks(md), "commonmark_blocks", 27)
            # Below code spans (190), above backslash escapes (180), so `\(` is math.
            md.inlinePatterns.register(InlineMath(_MATH, md), "math", 185)
            md.inlinePatterns.register(LineBreakTag(r"<br[ ]*/?>", md), "line_break_tag", 90)
            md.inlinePatterns.register(
                SimpleTagInlineProcessor(r"(~~)(?!~)(.+?)(?<!~)~~", "del"), "strikethrough", 65
            )
            # CommonMark lets a backslash escape any ASCII punctuation.
            md.ESCAPED_CHARS = sorted(set(md.ESCAPED_CHARS) | set(string.punctuation))
            # After inline processing (20) has created the links and images.
            md.treeprocessors.register(ExternalResources(md), "external_resources", 15)

    parser = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", Dialect()])
    return parser.convert(text)
