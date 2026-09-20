"""The symbol tables of TeX mathematics: what each command prints, and how it behaves.

Data only. Every character here is an IBM Plex Sans glyph or one the runtime
image's fallback family (DejaVu Sans) carries; `symbol_glyphs` lists them all so
a test can hold the tables to the fonts. Invisible characters are built from
code points, never typed, so they cannot be mistaken for the text around them.
"""
from __future__ import annotations

from typing import List

ZWSP = chr(0x200B)
COMBINING = {
    "hat": chr(0x0302), "check": chr(0x030C), "tilde": chr(0x0303), "bar": chr(0x0304),
    "vec": chr(0x20D7), "dot": chr(0x0307), "ddot": chr(0x0308), "acute": chr(0x0301),
    "grave": chr(0x0300), "breve": chr(0x0306),
}
# Stand-alone marks for an accent over more than one character.
ACCENT_MARK = {
    "widehat": "^", "widetilde": "~", "overrightarrow": "→", "overleftarrow": "←",
}


# ── symbol tables ────────────────────────────────────────────────────────────

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "omicron": "ο", "pi": "π", "varpi": "ϖ",
    "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ",
    "phi": "ϕ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
}
GREEK_UPPER = {
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
BIN = {
    "pm": "±", "mp": "∓", "times": "×", "div": "÷", "cdot": "⋅", "ast": "∗", "star": "⋆",
    "circ": "∘", "bullet": "∙", "oplus": "⊕", "ominus": "⊖", "otimes": "⊗", "oslash": "⊘",
    "odot": "⊙", "cup": "∪", "cap": "∩", "setminus": "∖", "wedge": "∧", "vee": "∨",
    "land": "∧", "lor": "∨", "uplus": "⊎", "sqcup": "⊔", "sqcap": "⊓", "dagger": "†",
    "ddagger": "‡",
}
REL = {
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈",
    "equiv": "≡", "sim": "∼", "simeq": "≃", "cong": "≅", "propto": "∝", "ll": "≪", "gg": "≫",
    "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "supset": "⊃", "subseteq": "⊆",
    "supseteq": "⊇", "nsubseteq": "⊈", "perp": "⊥", "parallel": "∥", "mid": "∣", "vdash": "⊢",
    "models": "⊨", "to": "→", "rightarrow": "→", "leftarrow": "←", "gets": "←",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "iff": "⟺", "implies": "⟹",
    "leftrightarrow": "↔", "mapsto": "↦", "longrightarrow": "⟶", "longleftarrow": "⟵",
    "uparrow": "↑", "downarrow": "↓", "hookrightarrow": "↪", "rightsquigarrow": "⇝",
    "doteq": "≐", "asymp": "≍", "prec": "≺", "succ": "≻", "preceq": "⪯", "succeq": "⪰",
    "lesssim": "≲", "gtrsim": "≳", "nleq": "≰", "ngeq": "≱", "nrightarrow": "↛",
}
ORD = {
    "infty": "∞", "partial": "∂", "nabla": "∇", "forall": "∀", "exists": "∃", "nexists": "∄",
    "emptyset": "∅", "varnothing": "∅", "neg": "¬", "lnot": "¬", "top": "⊤", "bot": "⊥",
    "angle": "∠", "triangle": "△", "hbar": "ℏ", "ell": "ℓ", "Re": "ℜ", "Im": "ℑ",
    "aleph": "ℵ", "prime": "′", "degree": "°", "ldots": "…", "dots": "…", "cdots": "⋯",
    "vdots": "⋮", "ddots": "⋱", "square": "□", "Box": "□", "checkmark": "✓",
    "therefore": "∴", "because": "∵", "imath": "ı", "jmath": "ȷ", "wp": "℘",
    "diamond": "⋄", "sharp": "♯", "flat": "♭", "natural": "♮",
}
# Big operators: (glyph, takes limits above/below in display style).
BIG = {
    "sum": ("∑", True), "prod": ("∏", True), "coprod": ("∐", True), "bigcup": ("⋃", True),
    "bigcap": ("⋂", True), "bigvee": ("⋁", True), "bigwedge": ("⋀", True),
    "bigoplus": ("⨁", True), "bigotimes": ("⨂", True), "bigodot": ("⨀", True),
    "int": ("∫", False), "iint": ("∬", False), "iiint": ("∭", False), "oint": ("∮", False),
}
# Named operators: name -> takes limits above/below in display style.
FUNCTIONS = {
    "sin": False, "cos": False, "tan": False, "cot": False, "sec": False, "csc": False,
    "arcsin": False, "arccos": False, "arctan": False, "sinh": False, "cosh": False,
    "tanh": False, "coth": False, "log": False, "ln": False, "lg": False, "exp": False,
    "deg": False, "dim": False, "ker": False, "hom": False, "arg": False,
    "lim": True, "max": True, "min": True, "sup": True, "inf": True, "det": True,
    "gcd": True, "Pr": True, "limsup": True, "liminf": True,
}
FUNCTION_TEXT = {"limsup": "lim sup", "liminf": "lim inf"}
DELIMITERS = {
    "(": "(", ")": ")", "[": "[", "]": "]", "\\{": "{", "\\}": "}", "\\lbrace": "{",
    "\\rbrace": "}", "\\langle": "⟨", "\\rangle": "⟩", "|": "|", "\\vert": "|",
    "\\lvert": "|", "\\rvert": "|", "\\|": "‖", "\\Vert": "‖", "\\lVert": "‖",
    "\\rVert": "‖", "\\lfloor": "⌊", "\\rfloor": "⌋", "\\lceil": "⌈", "\\rceil": "⌉",
    "/": "/", "\\backslash": "\\", ".": "",
}
OPEN_DELIMS = frozenset({"(", "[", "{", "⟨", "⌊", "⌈"})
CLOSE_DELIMS = frozenset({")", "]", "}", "⟩", "⌋", "⌉"})
SIZED = {"big": 1, "Big": 2, "bigg": 3, "Bigg": 4}
SPACES = {
    ",": "thin", ":": "med", ";": "thick", "!": "neg", " ": "thick", "quad": "quad",
    "qquad": "qquad", "enspace": "en", "thinspace": "thin", "medspace": "med",
    "thickspace": "thick", "negthinspace": "neg",
}
LITERAL_ESCAPES = frozenset("%$&#_{}")
FONT_STYLES = {
    "mathbf": "bf", "boldsymbol": "bfi", "bm": "bfi", "pmb": "bfi", "mathrm": "rm",
    "mathit": "it", "mathsf": "sf", "mathtt": "tt", "mathnormal": "it",
}
TEXT_COMMANDS = {
    "text": "", "mbox": "", "textrm": "", "textnormal": "", "textbf": "bf", "textit": "it",
    "textsf": "sf", "texttt": "tt", "hbox": "",
}
ACCENTS = frozenset(COMBINING) | frozenset(ACCENT_MARK)
# Matrix environments and the bracket shape drawn at each side.
ENV_MATRIX = {
    "matrix": "none", "pmatrix": "paren", "bmatrix": "square", "Bmatrix": "brace",
    "vmatrix": "bar", "Vmatrix": "dbar", "smallmatrix": "none",
}
ENV_ALIGNED = frozenset({"aligned", "align", "align*", "alignat", "split", "eqnarray", "flalign", "flalign*"})
ENV_CENTERED = frozenset({"gathered", "gather", "gather*", "multline", "multline*"})
ENV_PLAIN = frozenset({"equation", "equation*", "displaymath", "math"})
ENV_ALL = (
    frozenset(ENV_MATRIX) | ENV_ALIGNED | ENV_CENTERED | ENV_PLAIN | {"cases", "array", "subarray"}
)
BLACKBOARD_OTHER = {"C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ", "R": "ℝ", "Z": "ℤ"}

def supported_commands() -> List[str]:
    """Every command name the reader acts on, so the documentation can be held to it."""
    names = set(GREEK) | set(GREEK_UPPER) | set(BIN) | set(REL) | set(ORD) | set(BIG)
    names |= set(FUNCTIONS) | set(FONT_STYLES) | set(TEXT_COMMANDS) | set(ACCENTS) | set(SIZED)
    names |= {key[1:] for key in DELIMITERS if key[:1] == "\\" and key[1:].isalpha()}
    names |= {key for key in SPACES if key.isalpha()}
    names |= {
        "frac", "dfrac", "tfrac", "cfrac", "binom", "dbinom", "tbinom", "sqrt", "left", "right",
        "begin", "end", "mathbb", "mathcal", "mathscr", "mathfrak", "operatorname", "overline",
        "underline", "overbrace", "underbrace", "overset", "underset", "stackrel", "substack",
        "xrightarrow", "xleftarrow", "boxed", "displaystyle", "textstyle", "scriptstyle", "tag",
        "label", "notag", "nonumber", "bmod", "pmod", "hline",
    }
    return sorted(names)


def environments() -> List[str]:
    return sorted(ENV_ALL)


def symbol_glyphs() -> List[str]:
    """Every character the symbol tables can print, for the font-coverage check."""
    glyphs = set()
    for table in (GREEK, GREEK_UPPER, BIN, REL, ORD):
        glyphs.update(table.values())
    glyphs.update(glyph for glyph, _ in BIG.values())
    glyphs.update(glyph for glyph in DELIMITERS.values() if glyph)
    glyphs.update(ACCENT_MARK.values())
    glyphs.update(COMBINING.values())
    glyphs.update(BLACKBOARD_OTHER.values())
    # Seven capitals are letterlike characters elsewhere; their slots here are unassigned.
    glyphs.update(chr(0x1D538 + i) for i in range(26) if chr(ord("A") + i) not in BLACKBOARD_OTHER)
    glyphs.update(chr(0x1D552 + i) for i in range(26))
    glyphs.update(("√", "′", "−", "∗", "⟶", "⟵", "→", "←", "…", "˙", "¨", "´", "ˇ", "˜", "¯", "˘", chr(0x1D7D9)))
    return sorted(glyphs)

