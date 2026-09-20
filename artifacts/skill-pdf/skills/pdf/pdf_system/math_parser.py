"""Reads TeX mathematics into a small syntax tree, or refuses it by name.

The grammar is the subset mathematical writing uses. A construct outside it
raises `MathError` carrying the command and nothing of the author's text, so the
caller can report which command of which equation to repair. Reading is bounded
in length, depth and size: an equation cannot exhaust the stack or the clock.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .math_symbols import (
    ACCENT_MARK, BIG, BIN, BLACKBOARD_OTHER, CLOSE_DELIMS, COMBINING, DELIMITERS,
    ENV_ALL, FONT_STYLES, FUNCTION_TEXT, FUNCTIONS, GREEK, GREEK_UPPER, LITERAL_ESCAPES,
    ORD, OPEN_DELIMS, REL, SIZED, SPACES, TEXT_COMMANDS,
)

MAX_EXPRESSION_CHARS = 8000
MAX_DEPTH = 40
MAX_NODES = 6000


class MathError(Exception):
    """An equation the renderer cannot lay out. `kind` is `unsupported` or `syntax`."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail



# ── syntax tree ──────────────────────────────────────────────────────────────

class Node:
    """One piece of an equation. `kind` selects the fields that are set."""

    __slots__ = ("kind", "text", "cls", "kids", "parts", "meta")

    def __init__(
        self,
        kind: str,
        text: str = "",
        cls: str = "",
        kids: Optional[List["Node"]] = None,
        parts: Optional[List[Optional[List["Node"]]]] = None,
        meta: Optional[dict] = None,
    ):
        self.kind = kind
        self.text = text
        self.cls = cls
        self.kids = kids if kids is not None else []
        self.parts = parts if parts is not None else []
        self.meta = meta if meta is not None else {}


class _Token:
    __slots__ = ("kind", "text")

    def __init__(self, kind: str, text: str):
        self.kind = kind  # cmd | num | char | space
        self.text = text


_TOKEN = re.compile(r"\\([A-Za-z]+\*?|.)|(\d+(?:\.\d+)?)|(\s+)|(.)", re.S)


def _tokenize(expression: str) -> List[_Token]:
    tokens: List[_Token] = []
    for match in _TOKEN.finditer(expression):
        command, number, space, char = match.groups()
        if command is not None:
            tokens.append(_Token("cmd", command))
        elif number is not None:
            tokens.append(_Token("num", number))
        elif space is not None:
            tokens.append(_Token("space", " "))
        else:
            tokens.append(_Token("char", char))
    return tokens


_MACRO_REFUSED = frozenset({
    "newcommand", "renewcommand", "def", "let", "providecommand", "DeclareMathOperator",
    "usepackage", "input", "include",
})


class _Parser:
    def __init__(self, expression: str):
        if len(expression) > MAX_EXPRESSION_CHARS:
            raise MathError("syntax", "the equation is too long")
        self.tokens = _tokenize(expression)
        self.index = 0
        self.depth = 0
        self.count = 0
        self.raw = False  # inside \text, whitespace is content
        self.environments = 0  # depth of begin/end pairs being read

    # -- token helpers
    def _skip_space(self) -> None:
        if not self.raw:
            while self.index < len(self.tokens) and self.tokens[self.index].kind == "space":
                self.index += 1

    def peek(self) -> Optional[_Token]:
        self._skip_space()
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> _Token:
        self._skip_space()
        token = self.tokens[self.index]
        self.index += 1
        return token

    def at_char(self, char: str) -> bool:
        token = self.peek()
        return token is not None and token.kind == "char" and token.text == char

    def at_command(self, *names: str) -> bool:
        token = self.peek()
        return token is not None and token.kind == "cmd" and token.text in names

    def counted(self, node: Node) -> Node:
        self.count += 1
        if self.count > MAX_NODES:
            raise MathError("syntax", "the equation is too large")
        return node

    # -- grammar
    def parse_all(self) -> List[Node]:
        nodes = self.parse_row()
        if self.peek() is not None:
            token = self.peek()
            what = ("\\" + token.text) if token.kind == "cmd" else token.text
            raise MathError("syntax", "unexpected %s" % _named(what))
        return nodes

    def parse_row(self, stop_at_amp: bool = False) -> List[Node]:
        """Atoms until a closer this row does not own. The closer is left unread."""
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise MathError("syntax", "the equation is nested too deeply")
        nodes: List[Node] = []
        while True:
            token = self.peek()
            if token is None:
                break
            if token.kind == "char" and token.text == "}":
                break
            if token.kind == "cmd" and token.text in ("right", "end"):
                break
            if token.kind == "char" and token.text == "&":
                if stop_at_amp:
                    break
                raise MathError("syntax", "a column separator & appears outside a matrix or aligned block")
            if token.kind == "cmd" and token.text == "\\":
                if stop_at_amp:
                    break
                self.take()
                nodes.append(self.counted(Node("break")))
                continue
            atom = self.parse_atom()
            if atom is not None:
                nodes.append(atom)
        self.depth -= 1
        return nodes

    def parse_atom(self) -> Optional[Node]:
        token = self.take()
        if token.kind == "char" and token.text in ("^", "_"):
            # A script with nothing before it scripts an empty base.
            self.index -= 1
            base = self.counted(Node("row"))
        else:
            base = self.parse_primary(token)
            if base is None:
                return None
        return self.parse_scripts(base)

    def parse_scripts(self, base: Node) -> Node:
        sup: Optional[List[Node]] = None
        sub: Optional[List[Node]] = None
        while True:
            token = self.peek()
            if token is None or token.kind != "char":
                break
            if token.text == "^":
                self.take()
                if sup is not None:
                    raise MathError("syntax", "a base carries two superscripts")
                sup = self.parse_argument()
            elif token.text == "_":
                self.take()
                if sub is not None:
                    raise MathError("syntax", "a base carries two subscripts")
                sub = self.parse_argument()
            elif token.text == "'":
                self.take()
                primes = ["′"]
                while self.at_char("'"):
                    self.take()
                    primes.append("′")
                prime = self.counted(Node("sym", "".join(primes), "ord"))
                sup = (sup or []) + [prime]
            else:
                break
        if sup is None and sub is None:
            return base
        return self.counted(Node("scripts", kids=[base], parts=[sup, sub]))

    def parse_argument(self) -> List[Node]:
        """One argument: a braced group, or the single token that follows."""
        token = self.peek()
        if token is None:
            raise MathError("syntax", "an argument is missing at the end of the equation")
        if token.kind == "char" and token.text == "{":
            self.take()
            nodes = self.parse_row()
            self.expect_close()
            return nodes
        if token.kind == "num" and len(token.text) > 1:
            self.take()
            self.tokens.insert(self.index, _Token("num", token.text[1:]))
            return [self.counted(Node("sym", token.text[0], "num"))]
        if token.kind == "char" and token.text in ("}", "&"):
            raise MathError("syntax", "an argument is missing before %s" % token.text)
        self.take()
        node = self.parse_primary(token)
        return [node] if node is not None else []

    def expect_close(self) -> None:
        token = self.peek()
        if token is None or not (token.kind == "char" and token.text == "}"):
            raise MathError("syntax", "a group is not closed (unbalanced braces)")
        self.take()

    def read_group_text(self) -> str:
        """The raw text of a braced group: an environment name or a column spec."""
        if not self.at_char("{"):
            raise MathError("syntax", "a braced argument is missing")
        self.take()
        out: List[str] = []
        while True:
            token = self.peek()
            if token is None:
                raise MathError("syntax", "a group is not closed (unbalanced braces)")
            if token.kind == "char" and token.text == "}":
                self.take()
                return "".join(out)
            self.take()
            out.append(("\\" + token.text) if token.kind == "cmd" else token.text)

    def read_plain_argument(self) -> str:
        """A braced argument read as characters (\\text, \\mathbb): spaces are content."""
        if not self.at_char("{"):
            token = self.peek()
            if token is None:
                raise MathError("syntax", "an argument is missing at the end of the equation")
            self.take()
            return _text_of(token)
        self.take()
        out: List[str] = []
        depth = 1
        self.raw = True
        try:
            while True:
                if self.index >= len(self.tokens):
                    raise MathError("syntax", "a group is not closed (unbalanced braces)")
                token = self.tokens[self.index]
                self.index += 1
                if token.kind == "char" and token.text == "{":
                    depth += 1
                    continue
                if token.kind == "char" and token.text == "}":
                    depth -= 1
                    if depth == 0:
                        return "".join(out)
                    continue
                out.append(_text_of(token))
        finally:
            self.raw = False

    def parse_primary(self, token: _Token) -> Optional[Node]:
        if token.kind == "num":
            return self.counted(Node("sym", token.text, "num"))
        if token.kind == "char":
            return self.parse_character(token.text)
        return self.parse_command(token.text)

    def parse_character(self, char: str) -> Optional[Node]:
        if char == "{":
            nodes = self.parse_row()
            self.expect_close()
            return self.counted(Node("row", kids=nodes))
        if char == "}":
            raise MathError("syntax", "a closing brace has no matching opening brace")
        if char in ("^", "_"):
            raise MathError("syntax", "a script marker is misplaced")
        if char == "&":
            raise MathError("syntax", "a column separator & appears outside a matrix or aligned block")
        if char in "+":
            return self.counted(Node("sym", "+", "bin"))
        if char == "-":
            return self.counted(Node("sym", "−", "bin"))
        if char == "*":
            return self.counted(Node("sym", "∗", "bin"))
        if char in "=":
            return self.counted(Node("sym", "=", "rel"))
        if char in "<>":
            return self.counted(Node("sym", char, "rel"))
        if char in "([":
            return self.counted(Node("sym", char, "open"))
        if char in ")]":
            return self.counted(Node("sym", char, "close"))
        if char == "|":
            return self.counted(Node("sym", "|", "ord"))
        if char in ",;":
            return self.counted(Node("sym", char, "punct"))
        if char == ":":
            return self.counted(Node("sym", ":", "rel"))
        if char == "!":
            return self.counted(Node("sym", "!", "ord"))
        if char == "~":
            return self.counted(Node("space", cls="thick"))
        if char == "'":
            self.index -= 1
            return self.counted(Node("row"))  # a prime with no base scripts an empty base
        if char == "/":
            return self.counted(Node("sym", "/", "ord"))
        if char.isalpha():
            return self.counted(Node("sym", char, "ital"))
        if ord(char) < 32:
            return None
        return self.counted(Node("sym", char, "ord"))

    def parse_command(self, name: str) -> Optional[Node]:
        if name in _MACRO_REFUSED:
            raise MathError("unsupported", "\\%s (macro definitions are not supported)" % name)
        if name in GREEK:
            return self.counted(Node("sym", GREEK[name], "ital"))
        if name in GREEK_UPPER:
            return self.counted(Node("sym", GREEK_UPPER[name], "ord"))
        if name in BIN:
            return self.counted(Node("sym", BIN[name], "bin"))
        if name in REL:
            return self.counted(Node("sym", REL[name], "rel"))
        if name in ORD:
            return self.counted(Node("sym", ORD[name], "ord"))
        if name in BIG:
            glyph, limits = BIG[name]
            return self.counted(Node("bigop", glyph, meta={"limits": limits}))
        if name in FUNCTIONS:
            return self.counted(Node("fn", FUNCTION_TEXT.get(name, name), meta={"limits": FUNCTIONS[name]}))
        if name in LITERAL_ESCAPES:
            return self.counted(Node("sym", name, "open" if name == "{" else "close" if name == "}" else "ord"))
        if name == "|":
            return self.counted(Node("sym", "‖", "ord"))
        if name in SPACES:
            return self.counted(Node("space", cls=SPACES[name]))
        if name in ("frac", "dfrac", "tfrac", "cfrac", "binom", "dbinom", "tbinom"):
            numerator = self.parse_argument()
            denominator = self.parse_argument()
            style = {"dfrac": "D", "dbinom": "D", "tfrac": "T", "tbinom": "T"}.get(name, "")
            return self.counted(Node("frac", parts=[numerator, denominator], meta={"binom": "binom" in name, "style": style}))
        if name == "sqrt":
            index: Optional[List[Node]] = None
            if self.at_char("["):
                self.take()
                index = []
                while not self.at_char("]"):
                    if self.peek() is None:
                        raise MathError("syntax", "the root index is not closed")
                    atom = self.parse_atom()
                    if atom is not None:
                        index.append(atom)
                self.take()
            radicand = self.parse_argument()
            return self.counted(Node("sqrt", parts=[radicand, index]))
        if name == "left":
            return self.parse_delimited()
        if name in SIZED:
            return self.counted(Node("sized", cls=str(SIZED[name]), text=self.read_delimiter()))
        if name in ("bigl", "bigr", "bigm", "Bigl", "Bigr", "Bigm", "biggl", "biggr", "biggm", "Biggl", "Biggr", "Biggm"):
            level = SIZED[re.sub(r"[lrm]$", "", name)]
            return self.counted(Node("sized", cls=str(level), text=self.read_delimiter()))
        if name == "right":
            raise MathError("syntax", "\\right has no matching \\left")
        if name == "begin":
            return self.parse_environment()
        if name == "end":
            raise MathError("syntax", "\\end has no matching \\begin")
        if name in ("mathbb", "mathcal", "mathscr", "mathfrak"):
            return self.parse_alphabet(name)
        if name in FONT_STYLES:
            return self.counted(Node("style", cls=FONT_STYLES[name], kids=self.parse_argument()))
        if name in TEXT_COMMANDS:
            return self.counted(Node("text", text=self.read_plain_argument(), cls=TEXT_COMMANDS[name]))
        if name in ("operatorname", "operatorname*"):
            return self.counted(Node("fn", self.read_plain_argument(), meta={"limits": name.endswith("*")}))
        if name in COMBINING or name in ACCENT_MARK:
            return self.counted(Node("accent", text=name, kids=self.parse_argument()))
        if name in ("overline", "underline"):
            return self.counted(Node("line", text=name, kids=self.parse_argument()))
        if name in ("overbrace", "underbrace"):
            return self.counted(Node("brace", text=name, kids=self.parse_argument()))
        if name in ("overset", "stackrel"):
            top = self.parse_argument()
            base = self.parse_argument()
            return self.counted(Node("stack", parts=[top, base, None]))
        if name == "underset":
            bottom = self.parse_argument()
            base = self.parse_argument()
            return self.counted(Node("stack", parts=[None, base, bottom]))
        if name in ("xrightarrow", "xleftarrow"):
            below: Optional[List[Node]] = None
            if self.at_char("["):
                self.take()
                below = []
                while not self.at_char("]"):
                    if self.peek() is None:
                        raise MathError("syntax", "the label under an arrow is not closed")
                    atom = self.parse_atom()
                    if atom is not None:
                        below.append(atom)
                self.take()
            above = self.parse_argument()
            arrow = Node("sym", "⟶" if name == "xrightarrow" else "⟵", "rel")
            return self.counted(Node("stack", parts=[above, [arrow], below], meta={"arrow": True}))
        if name == "substack":
            return self.parse_substack()
        if name == "boxed":
            return self.counted(Node("boxed", kids=self.parse_argument()))
        if name in ("displaystyle", "textstyle", "scriptstyle", "scriptscriptstyle"):
            return self.counted(Node("styleswitch", cls={"displaystyle": "D", "textstyle": "T"}.get(name, "S")))
        if name == "bmod":
            return self.counted(Node("text", text="mod", cls="", meta={"bin": True}))
        if name == "pmod":
            return self.counted(Node("row", kids=[
                Node("sym", "(", "open"), Node("text", text="mod "), *self.parse_argument(), Node("sym", ")", "close"),
            ]))
        if name == "tag":
            label = self.read_plain_argument()
            return self.counted(Node("tag", text=label))
        if name in ("label", "notag", "nonumber"):
            if name == "label":
                self.read_plain_argument()
            return None
        if ("\\" + name) in DELIMITERS and DELIMITERS["\\" + name]:
            glyph = DELIMITERS["\\" + name]
            role = "open" if glyph in OPEN_DELIMS else "close" if glyph in CLOSE_DELIMS else "ord"
            return self.counted(Node("sym", glyph, role))
        if name == "hline" and self.environments:
            return self.counted(Node("hline"))
        if name in ("hline", "hdashline", "cline"):
            raise MathError("unsupported", "\\%s (table rules are not supported inside an equation)" % name)
        raise MathError("unsupported", "\\%s" % name)

    # -- compound constructs
    def read_delimiter(self) -> str:
        token = self.peek()
        if token is None:
            raise MathError("syntax", "a delimiter is missing after \\left, \\right or a size command")
        self.take()
        key = ("\\" + token.text) if token.kind == "cmd" else token.text
        if key not in DELIMITERS:
            raise MathError("unsupported", "the delimiter %s" % _named(key))
        return DELIMITERS[key]

    def parse_delimited(self) -> Node:
        opening = self.read_delimiter()
        body = self.parse_row()
        if not self.at_command("right"):
            raise MathError("syntax", "\\left has no matching \\right")
        self.take()
        closing = self.read_delimiter()
        return self.counted(Node("delim", kids=body, meta={"open": opening, "close": closing}))

    def parse_alphabet(self, name: str) -> Node:
        source = self.read_plain_argument()
        if name == "mathbb":
            out = []
            for char in source:
                if char in BLACKBOARD_OTHER:
                    out.append(BLACKBOARD_OTHER[char])
                elif "A" <= char <= "Z":
                    out.append(chr(0x1D538 + ord(char) - ord("A")))
                elif "a" <= char <= "z":
                    out.append(chr(0x1D552 + ord(char) - ord("a")))
                elif char == "1":
                    out.append(chr(0x1D7D9))
                else:
                    out.append(char)
            return self.counted(Node("sym", "".join(out), "ord"))
        # Script and fraktur capitals sit outside the fonts the renderer carries;
        # they print as bold italic capitals, which keeps them distinct.
        return self.counted(Node("text", text=source, cls="cal"))

    def parse_substack(self) -> Node:
        if not self.at_char("{"):
            raise MathError("syntax", "\\substack needs a braced argument")
        self.take()
        lines: List[List[Node]] = [[]]
        while True:
            token = self.peek()
            if token is None:
                raise MathError("syntax", "a group is not closed (unbalanced braces)")
            if token.kind == "char" and token.text == "}":
                self.take()
                break
            if token.kind == "cmd" and token.text == "\\":
                self.take()
                lines.append([])
                continue
            atom = self.parse_atom()
            if atom is not None:
                lines[-1].append(atom)
        return self.counted(Node("lines", parts=[line for line in lines], meta={"small": True}))

    def parse_environment(self) -> Node:
        name = self.read_group_text().strip()
        if name not in ENV_ALL:
            raise MathError("unsupported", "the environment %s" % _named(name))
        columns = ""
        if name in ("array", "subarray"):
            columns = self.read_group_text()
        rows: List[List[List[Node]]] = [[[]]]
        self.depth += 1
        self.environments += 1
        if self.depth > MAX_DEPTH:
            raise MathError("syntax", "the equation is nested too deeply")
        while True:
            cell = self.parse_row(stop_at_amp=True)
            rows[-1][-1] = cell
            token = self.peek()
            if token is None:
                raise MathError("syntax", "\\begin{%s} has no matching \\end" % name)
            if token.kind == "char" and token.text == "&":
                self.take()
                rows[-1].append([])
                continue
            if token.kind == "cmd" and token.text == "\\":
                self.take()
                rows.append([[]])
                continue
            if token.kind == "cmd" and token.text == "end":
                self.take()
                closing = self.read_group_text().strip()
                if closing != name:
                    raise MathError("syntax", "\\begin{%s} is closed by \\end{%s}" % (name, closing))
                break
            raise MathError("syntax", "an unexpected %s inside \\begin{%s}" % (_named(token.text), name))
        self.depth -= 1
        self.environments -= 1
        rules: List[bool] = []
        for row in rows:
            lifted = 0
            while row[0] and row[0][0].kind == "hline":
                row[0].pop(0)
                lifted += 1
            rules.append(lifted > 0)
        bottom = False
        if len(rows) > 1 and all(not cell for cell in rows[-1]):
            bottom = rules[-1]  # a trailing row separator leaves an empty last row; its rule closes the table
            rows.pop()
            rules.pop()
        return self.counted(Node("env", text=name, parts=rows, meta={"columns": columns, "rules": rules, "bottom": bottom}))  # type: ignore[arg-type]


_TEXT_ESCAPES = {"%": "%", "$": "$", "&": "&", "#": "#", "_": "_", "{": "{", "}": "}", "\\": " ", "-": "-", " ": " ", ",": " ", ";": " ", ":": " ", "quad": "  ", "qquad": "    "}


def _text_of(token: _Token) -> str:
    """One token of a \\text argument as the characters it prints."""
    if token.kind != "cmd":
        return token.text
    if token.text in _TEXT_ESCAPES:
        return _TEXT_ESCAPES[token.text]
    raise MathError("unsupported", "\\%s inside \\text (text mode prints characters only)" % token.text)


def _named(text: str) -> str:
    """A token, shown as a short name and never as long author text."""
    clean = re.sub(r"[^\w\\|{}()\[\]<>=+*/^_&.,;:!'-]", "?", text)[:28]
    return "'%s'" % clean



def parse(expression: str) -> List[Node]:
    """The syntax tree of one equation. Raises `MathError` for anything outside the subset."""
    return _Parser(expression).parse_all()
