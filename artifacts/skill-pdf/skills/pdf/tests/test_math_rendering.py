"""TeX mathematics reaches the PDF as laid-out math, never as its source.

The renderer used to hand an equation to the print engine as text inside a
MathML run. The engine does not implement MathML, so backslashes and braces were
printed as written, and four common commands (`\\frac`, `\\sqrt`, `\\begin`,
`\\end`) were refused by a message that named no equation. A document made of
fractions could neither render nor be repaired: a real run spent its whole time
budget rewriting the same source and delivered nothing.

These tests pin the layout at the HTML boundary, the refusal contract (every bad
equation named once, by line and command), the guarantee that an equation can
never introduce markup, and, where the production renderer is installed, the
printed result.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as etree
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pdf_system import PdfService, SecurityPolicy  # noqa: E402
from pdf_system.errors import ErrorCode, PdfError  # noqa: E402
from pdf_system.math_html import (  # noqa: E402
    MATH_CSS,
    MAX_DEPTH,
    MAX_EXPRESSION_CHARS,
    MathError,
    environments,
    render_math,
    supported_commands,
    symbol_glyphs,
)

SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8")


def markdown_available() -> bool:
    try:
        import markdown  # noqa: F401
    except ImportError:
        return False
    return True


def production_available() -> bool:
    try:
        import pypdf  # noqa: F401
        import weasyprint  # noqa: F401
    except Exception:
        return False
    fonts = Path(os.environ.get("CHAINABIT_ARTIFACT_FONT_DIR", ""))
    return fonts.joinpath("IBMPlexSans-Regular.ttf").is_file()


def html_of(expression: str, display: bool = False, block: bool = False) -> str:
    return etree.tostring(render_math(expression, display=display, block=block), encoding="unicode", method="html")


def tree_of(expression: str, display: bool = False) -> etree.Element:
    return render_math(expression, display=display)


def classes(element: etree.Element) -> set:
    found = set()
    for node in element.iter():
        found.update((node.get("class") or "").split())
    return found


def text_of(element: etree.Element) -> str:
    return "".join(element.itertext()).replace(chr(0x200B), "")


def by_class(element: etree.Element, name: str) -> etree.Element:
    """The first descendant carrying exactly this class (the layout uses spans throughout)."""
    for node in element.iter():
        if node.get("class") == name:
            return node
    raise AssertionError("no element with class %r" % name)


def refusal(expression: str) -> MathError:
    try:
        render_math(expression)
    except MathError as error:
        return error
    raise AssertionError("expected the equation to be refused: %r" % expression)


class MathLayoutTests(unittest.TestCase):
    def test_a_fraction_stacks_numerator_over_denominator(self):
        fraction = by_class(tree_of(r"\frac{a}{b}", display=True), "mfrac")
        self.assertEqual([child.get("class") for child in fraction], ["mnum", "mden"])
        self.assertEqual(text_of(by_class(fraction, "mnum")), "a")
        self.assertEqual(text_of(by_class(fraction, "mden")), "b")

    def test_a_fraction_in_running_text_is_set_smaller_than_a_display_one(self):
        self.assertIn("mfrac mS", html_of(r"\frac{a}{b}"))
        self.assertNotIn("mfrac mS", html_of(r"\frac{a}{b}", display=True))
        self.assertNotIn("mfrac mS", html_of(r"\dfrac{a}{b}"))
        self.assertIn("mfrac mS", html_of(r"\tfrac{a}{b}", display=True))

    def test_a_single_token_argument_needs_no_braces(self):
        self.assertEqual(text_of(tree_of(r"\frac12")), "12")
        self.assertIn("mnum", html_of(r"\frac12"))
        self.assertEqual(text_of(tree_of(r"x^23")), "x23")

    def test_superscripts_subscripts_and_primes(self):
        marked = html_of(r"x^2_i")
        self.assertIn("mss", marked)
        self.assertIn("msup", html_of(r"x^2"))
        self.assertIn("msub", html_of(r"x_i"))
        self.assertEqual(text_of(tree_of("f'(x)")), "f" + chr(0x2032) + "(x)")
        self.assertIn(chr(0x2032) * 2, text_of(tree_of("f''")))

    def test_a_radical_carries_its_index_and_radicand(self):
        root = tree_of(r"\sqrt[3]{x+1}")
        self.assertIn("mri", classes(root))
        self.assertIn("mrb", classes(root))
        self.assertIn(chr(0x221A), text_of(root))

    def test_a_tall_radicand_gets_a_larger_radical(self):
        self.assertIn("mrad-z1", html_of(r"\sqrt{x}"))
        self.assertNotIn("mrad-z1", html_of(r"\sqrt{\frac{a}{b}}"))

    def test_big_operator_limits_stack_in_display_and_sit_beside_in_text(self):
        self.assertIn("mlim", classes(tree_of(r"\sum_{i=1}^{n} i", display=True)))
        beside = tree_of(r"\sum_{i=1}^{n} i")
        self.assertNotIn("mlim", classes(beside))
        self.assertIn("mss", classes(beside))

    def test_integral_limits_stay_beside_the_sign_even_in_display(self):
        integral = tree_of(r"\int_0^1 f", display=True)
        self.assertNotIn("mlim", classes(integral))
        self.assertIn("mss", classes(integral))
        self.assertIn("mbig-i", classes(integral))

    def test_named_operators_take_limits_in_display_style(self):
        self.assertIn("mlim", classes(tree_of(r"\lim_{n\to\infty} a_n", display=True)))
        self.assertNotIn("mlim", classes(tree_of(r"\sin_2 x", display=True)))

    def test_delimiters_grow_with_what_they_enclose(self):
        self.assertIn("mdl-z1", html_of(r"\left( x \right)"))
        self.assertNotIn("mdl-z1", html_of(r"\left( \frac{a}{b} \right)"))
        grown = lambda expression: max(int(name[-1]) for name in re.findall(r"mdl-z\d", html_of(expression)))  # noqa: E731
        self.assertGreater(grown(r"\left( \frac{a}{b} \right)"), grown(r"\left( x \right)"))
        self.assertGreater(grown(r"\left( \frac{\frac{a}{b}}{c} \right)"), grown(r"\left( \frac{a}{b} \right)"))
        self.assertIn("mdl-z2", html_of(r"\big( x"))
        self.assertIn("mdl-z5", html_of(r"\Bigg| x"))

    def test_a_null_delimiter_prints_nothing(self):
        marked = html_of(r"\left. \frac{a}{b} \right|_{x=0}")
        self.assertNotIn("&gt;.", marked)
        self.assertEqual(text_of(tree_of(r"\left. x \right|")).count("."), 0)

    def test_matrix_environments_draw_their_own_brackets(self):
        shapes = {
            "matrix": "none", "pmatrix": "paren", "bmatrix": "square",
            "Bmatrix": "brace", "vmatrix": "bar", "Vmatrix": "dbar",
        }
        for name, shape in shapes.items():
            rendered = tree_of(r"\begin{%s} a & b \\ c & d \end{%s}" % (name, name))
            self.assertIn("mbr-l", classes(rendered), name)
            self.assertIn("mbr-" + shape, classes(rendered), name)
            self.assertEqual(len(rendered.findall(".//*[@class='mrow']")), 2, name)
            self.assertEqual(len(rendered.findall(".//*[@class='mrow']/*")), 4, name)

    def test_a_trailing_row_separator_adds_no_empty_row(self):
        rendered = tree_of(r"\begin{pmatrix} 1 & 0 \\ 0 & 1 \\ \end{pmatrix}")
        self.assertEqual(len(rendered.findall(".//*[@class='mrow']")), 2)

    def test_cases_align_left_and_carry_a_brace(self):
        rendered = tree_of(r"\begin{cases} x & \text{if } x>0 \\ 0 & \text{otherwise} \end{cases}")
        self.assertIn("mcases", classes(rendered))
        self.assertEqual(text_of(rendered).count("{"), 1)
        self.assertIn("mc-l", classes(rendered))
        self.assertIn("if ", text_of(rendered))

    def test_an_aligned_block_right_aligns_before_the_relation(self):
        rendered = tree_of(r"\begin{aligned} a &= b \\ c &= d \end{aligned}", display=True)
        self.assertIn("maligned", classes(rendered))
        first_row = rendered.find(".//*[@class='mrow']")
        cells = [cell.get("class") for cell in first_row]
        self.assertTrue(cells[0].startswith("mcell mc-r"), cells)
        self.assertTrue(cells[1].startswith("mcell mc-l"), cells)

    def test_array_rules_and_column_alignment(self):
        rendered = tree_of(r"\begin{array}{l|cr} a & b & c \\ \hline 1 & 22 & 333 \\ \hline \end{array}")
        used = classes(rendered)
        self.assertIn("mc-rt", used)
        self.assertIn("mc-rb", used)
        self.assertIn("mc-vl", used)
        aligns = [cell.get("class").split()[1] for cell in rendered.findall(".//*[@class='mrow']")[0]]
        self.assertEqual(aligns[:3], ["mc-l", "mc-c", "mc-r"])

    def test_lines_broken_in_display_style_are_centred_lines(self):
        rendered = tree_of(r"a = b \\ c = d", display=True)
        self.assertEqual(len(rendered.findall(".//*[@class='mln']")), 2)

    def test_greek_lower_case_slants_and_upper_case_stands(self):
        self.assertEqual(text_of(tree_of(r"\alpha\Gamma")), "α" + "Γ")
        self.assertIn("mi", classes(tree_of(r"\alpha")))
        self.assertIn("mo", classes(tree_of(r"\Gamma")))

    def test_letters_slant_digits_and_operator_names_stand(self):
        self.assertIn("mi", classes(tree_of("x")))
        self.assertIn("mn", classes(tree_of("42")))
        self.assertIn("mfn", classes(tree_of(r"\sin x")))
        self.assertEqual(text_of(tree_of("3.14")), "3.14")

    def test_binary_operators_are_spaced_and_signs_are_not(self):
        self.assertIn("mbn", classes(tree_of("a-b")))
        self.assertNotIn("mbn", classes(tree_of("-b")))
        self.assertNotIn("mbn", classes(tree_of("a = -b")))
        self.assertNotIn("mbn", classes(tree_of("(-b)")))
        self.assertNotIn("mbn", classes(tree_of("a+")))

    def test_relations_are_spaced_in_text_and_tight_in_a_script(self):
        self.assertIn("mrl", classes(tree_of("a<b")))
        self.assertNotIn("mrl", classes(tree_of("x_{i=1}")))

    def test_a_thousands_comma_is_not_punctuation(self):
        self.assertNotIn("mpu", classes(tree_of("1,000")))
        self.assertIn("mpu", classes(tree_of("a, b")))

    def test_an_operator_name_sits_tight_against_an_opening_delimiter(self):
        self.assertIn("mfn-tight", html_of(r"\sin(x)"))
        self.assertNotIn("mfn-tight", html_of(r"\sin x"))

    def test_text_keeps_its_spaces_and_prints_escapes_literally(self):
        self.assertEqual(text_of(tree_of(r"\text{if } x")).startswith("if "), True)
        self.assertEqual(text_of(tree_of(r"\text{a \% b \& c}")), "a % b & c")
        self.assertEqual(text_of(tree_of(r"\text{turn\,off}")), "turn off")

    def test_fonts(self):
        self.assertIn("mf-bf", classes(tree_of(r"\mathbf{x}")))
        self.assertIn("mf-bfi", classes(tree_of(r"\boldsymbol{\theta}")))
        self.assertIn("mf-rm", classes(tree_of(r"\mathrm{d}x")))
        self.assertEqual(text_of(tree_of(r"\mathbb{R}")), chr(0x211D))
        self.assertEqual(text_of(tree_of(r"\mathbb{E}")), chr(0x1D53C))
        self.assertIn("mf-cal", classes(tree_of(r"\mathcal{L}")))

    def test_an_accent_over_one_letter_is_a_combining_mark(self):
        self.assertEqual(text_of(tree_of(r"\hat{x}")), "x" + chr(0x0302))
        self.assertEqual(text_of(tree_of(r"\bar{y}")), "y" + chr(0x0304))

    def test_an_accent_over_a_run_is_a_stacked_mark(self):
        rendered = tree_of(r"\widehat{xy}")
        self.assertIn("macc", classes(rendered))
        self.assertEqual(text_of(rendered), "^xy")

    def test_over_and_under_lines_and_braces(self):
        self.assertIn("mline-over", classes(tree_of(r"\overline{AB}")))
        self.assertIn("mline-under", classes(tree_of(r"\underline{AB}")))
        labelled = tree_of(r"\underbrace{x+y}_{\text{sum}}")
        self.assertIn("mbrace-under", classes(labelled))
        self.assertIn("mstk-u", classes(labelled))
        self.assertIn("mstk-t", classes(tree_of(r"\overbrace{x+y}^{n}")))

    def test_stacked_relations(self):
        self.assertIn("mstk", classes(tree_of(r"A \overset{\text{def}}{=} B")))
        self.assertIn("mstk-u", classes(tree_of(r"A \underset{x}{=} B")))
        arrow = tree_of(r"A \xrightarrow[\text{eval}]{f} B")
        self.assertIn("mstk-t", classes(arrow))
        self.assertIn("mstk-u", classes(arrow))
        self.assertIn(chr(0x27F6), text_of(arrow))

    def test_substack_and_boxed(self):
        self.assertIn("mlines-sm", classes(tree_of(r"\sum_{\substack{i<n \\ j>m}} x", display=True)))
        self.assertIn("mbox", classes(tree_of(r"\boxed{x=1}")))

    def test_a_tag_is_set_before_the_line_so_it_shares_the_line(self):
        rendered = tree_of(r"a = b \tag{1}", display=True)
        self.assertEqual(rendered[0].get("class"), "mtag")
        self.assertEqual(rendered[0].text, "(1)")

    def test_a_label_and_numbering_switches_print_nothing(self):
        self.assertEqual(text_of(tree_of(r"a = b \label{eq:1} \nonumber")), "a=b")

    def test_spacing_commands(self):
        self.assertIn("msp-thin", html_of(r"a\,b"))
        self.assertIn("msp-quad", html_of(r"a\quad b"))
        self.assertIn("msp-neg", html_of(r"a\!b"))

    def test_a_display_equation_is_a_block_only_on_request(self):
        self.assertEqual(render_math("x", display=True, block=True).tag, "div")
        self.assertEqual(render_math("x", display=True).tag, "span")
        self.assertEqual(render_math("x").get("class"), "m")
        self.assertEqual(render_math("x", display=True).get("class"), "m md")

    def test_a_break_opportunity_follows_each_relation(self):
        self.assertIn(chr(0x200B), html_of("a=b"))

    def test_style_switches_change_the_size_of_a_fraction(self):
        self.assertNotIn("mfrac mS", html_of(r"\displaystyle \frac{a}{b}"))
        self.assertIn("mfrac mS", html_of(r"\textstyle \frac{a}{b}", display=True))

    def test_pmod_and_bmod(self):
        self.assertIn("(mod n)", text_of(tree_of(r"a \equiv b \pmod{n}")))
        self.assertIn("mod", text_of(tree_of(r"a \bmod n")))


class MathRefusalTests(unittest.TestCase):
    def test_an_unsupported_command_is_named(self):
        error = refusal(r"x + \foo{y}")
        self.assertEqual(error.kind, "unsupported")
        self.assertEqual(error.detail, "\\foo")

    def test_macro_definitions_are_refused_by_name_and_reason(self):
        for command in ("newcommand", "def", "let", "renewcommand", "DeclareMathOperator"):
            error = refusal("\\%s{\\x}{1}" % command)
            self.assertEqual(error.kind, "unsupported", command)
            self.assertTrue(error.detail.startswith("\\" + command), command)
            self.assertIn("macro", error.detail)

    def test_an_unknown_environment_is_named(self):
        error = refusal(r"\begin{tikzpicture} x \end{tikzpicture}")
        self.assertEqual(error.kind, "unsupported")
        self.assertIn("tikzpicture", error.detail)

    def test_a_rule_outside_an_array_is_refused(self):
        self.assertEqual(refusal(r"a \hline b").kind, "unsupported")

    def test_a_math_command_inside_text_is_refused_not_printed(self):
        error = refusal(r"\text{the \alpha value}")
        self.assertEqual(error.kind, "unsupported")
        self.assertIn("alpha", error.detail)

    def test_an_unknown_delimiter_is_named(self):
        self.assertEqual(refusal(r"\left\foo x \right)").kind, "unsupported")

    def test_broken_syntax_is_reported_as_syntax(self):
        cases = {
            "unclosed group": r"\frac{a}{b",
            "stray brace": r"a } b",
            "missing argument": r"\frac{a}",
            "double superscript": r"x^1^2",
            "double subscript": r"x_1_2",
            "left without right": r"\left( x",
            "right without left": r"x \right)",
            "environment never ended": r"\begin{pmatrix} a & b",
            "end without begin": r"a \end{pmatrix}",
            "environment mismatch": r"\begin{pmatrix} a \end{bmatrix}",
            "column separator outside": r"a & b",
            "script with nothing to script at the end": r"x^",
            "index never closed": r"\sqrt[3 x",
        }
        for label, expression in cases.items():
            self.assertEqual(refusal(expression).kind, "syntax", label)

    def test_a_refusal_names_a_command_and_never_quotes_the_equation(self):
        secret = "quarterlyrevenue"
        for expression in (r"\foo{%s}" % secret, r"%s \bar{" % secret, r"\text{%s \zzz}" % secret):
            self.assertNotIn(secret, refusal(expression).detail)

    def test_a_very_long_equation_is_refused(self):
        self.assertEqual(refusal("x+" * (MAX_EXPRESSION_CHARS // 2 + 5)).kind, "syntax")

    def test_deep_nesting_is_refused_instead_of_exhausting_the_stack(self):
        self.assertEqual(refusal("{" * (MAX_DEPTH + 10) + "x" + "}" * (MAX_DEPTH + 10)).kind, "syntax")
        self.assertEqual(refusal(r"\frac{" * (MAX_DEPTH + 10)).kind, "syntax")

    def test_a_large_equation_within_the_limits_still_renders(self):
        render_math("+".join("x_{%d}" % index for index in range(400)))


class MathIsNeverMarkupTests(unittest.TestCase):
    CORPUS = [
        r"\frac{\partial L}{\partial x_i} = \sum_{j=1}^{n} \left( w_{ij} - \bar{x} \right)^2",
        r"\begin{bmatrix} \alpha & \beta \\ \gamma & \delta \end{bmatrix}",
        r"\begin{aligned} a &= b \\ &+ c \end{aligned}",
        r"\begin{cases} x & \text{if } x>0 \\ 0 & \text{else} \end{cases}",
        r"\begin{array}{c|c} a & b \\ \hline c & d \end{array}",
        r"\mathbb{R}^{n\times m} \ni \hat{y} \overset{def}{=} \underbrace{x+y}_{\text{sum}}",
        r"\int_0^\infty e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2}",
        r"\lim_{n\to\infty} \max_{i} a_i \xrightarrow[\text{eval}]{f} \overline{AB}",
        r"\binom{n}{k} \sqrt[3]{x} \boxed{y} \operatorname{argmax}_{k} p_k \pmod{n}",
        r"\displaystyle\sum_{\substack{i<n\\j>m}} \mathbf{v}_i \odot \boldsymbol{\mu}",
        r"a < b > c \text{ <b>bold</b> & \% } \tag{7}",
    ]

    def test_only_spans_and_divs_and_only_a_class_attribute(self):
        for expression in self.CORPUS:
            for display in (False, True):
                rendered = render_math(expression, display=display, block=display)
                for node in rendered.iter():
                    self.assertIn(node.tag, ("span", "div"), expression)
                    self.assertLessEqual(set(node.attrib), {"class"}, expression)

    def test_an_authors_tags_stay_text(self):
        rendered = html_of(r"\text{<script>alert(1)</script> & <b>x</b>}")
        self.assertNotIn("<script", rendered)
        self.assertNotIn("<b>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_every_class_the_layout_emits_has_a_rule(self):
        styled = set(re.findall(r"\.([A-Za-z][\w-]*)", MATH_CSS))
        for expression in self.CORPUS:
            for display in (False, True):
                for name in classes(render_math(expression, display=display, block=display)):
                    self.assertIn(name, styled, "%s has no rule (in %s)" % (name, expression))

    def test_the_stylesheet_reaches_out_to_nothing(self):
        for forbidden in ("url(", "@import", "expression(", "javascript:", "http:", "https:"):
            self.assertNotIn(forbidden, MATH_CSS)


class SymbolAndDocumentationTests(unittest.TestCase):
    def test_every_symbol_is_a_printable_character_without_source_syntax(self):
        for glyph in symbol_glyphs():
            self.assertTrue(glyph, "empty glyph")
            self.assertNotIn("\\", glyph if glyph != "\\" else "")
            self.assertTrue(all(ord(char) >= 0x20 for char in glyph), repr(glyph))

    def test_every_documented_command_is_supported(self):
        section = SKILL.split("## Mathematics", 1)[1].split("## Rendering contract", 1)[0]
        supported_section = section.split("Outside the subset", 1)[0]
        documented = set(re.findall(r"\\([A-Za-z]+)", supported_section))
        self.assertGreater(len(documented), 40)
        known = set(supported_commands())
        self.assertEqual(sorted(documented - known), [])
        bullet = supported_section.split("Environments:", 1)[1].split("- Symbols", 1)[0]
        named = [name for name in re.findall(r"`([A-Za-z]+)`", bullet)]
        self.assertGreaterEqual(len(named), 9)
        self.assertEqual([name for name in named if name not in environments()], [])

    def test_every_command_documented_as_unsupported_is_refused(self):
        section = SKILL.split("Outside the subset", 1)[1].split("Images reach", 1)[0]
        for name in ("newcommand", "def", "phantom", "not"):
            self.assertIn("\\" + name, section)
            self.assertEqual(refusal("\\%s{x}" % name).kind, "unsupported", name)

    def test_the_greek_alphabet_and_the_operator_names_are_complete(self):
        for name in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa",
                     "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
                     "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Phi", "Psi", "Omega",
                     "sin", "cos", "tan", "log", "ln", "exp", "max", "min", "lim", "det"):
            render_math("\\" + name)


@unittest.skipUnless(markdown_available(), "the Markdown package is not installed")
class MarkdownEquationTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.policy = SecurityPolicy(Path(self._dir.name), Path(self._dir.name))

    def html(self, source: str) -> str:
        from pdf_system.markdown_html import render_markdown

        return render_markdown(source, self.policy)

    def tree(self, source: str) -> etree.Element:
        return etree.fromstring("<body>" + self.html(source) + "</body>")

    def refused(self, source: str) -> PdfError:
        with self.assertRaises(PdfError) as caught:
            self.html(source)
        return caught.exception

    def test_no_backslash_command_survives_into_the_document(self):
        text = "".join(self.tree(r"Loss $\frac{1}{N}\sum_i \alpha_i$ and $$\int_0^1 \sqrt{x}\,dx$$").itertext())
        self.assertNotIn("\\", text)
        self.assertNotIn("frac", text)
        self.assertNotIn("alpha", text)

    def test_no_mathml_projection_is_emitted(self):
        rendered = self.html(r"$x+1$ and $$y$$")
        self.assertNotIn("<math", rendered)
        self.assertNotIn("<mi>", rendered)

    def test_every_delimiter_form_is_an_equation(self):
        for source in (r"a $x^2$ b", r"a \(x^2\) b", r"a $$x^2$$ b", r"a \[x^2\] b"):
            found = [node for node in self.tree(source).iter() if node.get("class") in ("m", "m md")]
            self.assertEqual(len(found), 1, source)

    def test_inline_forms_are_inline_and_display_forms_are_display(self):
        self.assertIsNotNone(self.tree(r"a $x$ b").find(".//*[@class='m']"))
        self.assertIsNotNone(self.tree(r"a \(x\) b").find(".//*[@class='m']"))
        self.assertIsNotNone(self.tree(r"a $$x$$ b").find(".//*[@class='m md']"))
        self.assertIsNotNone(self.tree("$$\nx\n$$").find(".//div[@class='m md']"))

    def test_prices_and_escaped_dollars_stay_text(self):
        rendered = "".join(self.tree(r"Costs $5 and $10, or \$7 flat").itertext())
        self.assertIn("$5 and $10", rendered)
        self.assertIn("$7", rendered)
        self.assertNotIn("class=\"m\"", self.html(r"Costs $5 and $10"))

    def test_code_is_never_math(self):
        rendered = self.html("Run `echo $x^2$` now\n\n```\n$$\n\\frac{a}{b}\n$$\n```\n")
        self.assertNotIn('class="m', rendered)
        self.assertIn("\\frac{a}{b}", rendered)

    def test_a_display_equation_whose_lines_start_like_list_markers_stays_whole(self):
        rendered = self.html("Before\n\n$$\n\\begin{aligned}\na &= b \\\\\n+ c &= d \\\\\n- e &= f \\\\\n1. g &= h\n\\end{aligned}\n$$\n\nAfter")
        self.assertNotIn("<li", rendered)
        self.assertNotIn("<ul", rendered)
        self.assertNotIn("<ol", rendered)
        text = "".join(etree.fromstring("<body>" + rendered + "</body>").itertext())
        for expected in ("Before", "After", "h"):
            self.assertIn(expected, text)

    def test_a_display_equation_may_hold_blank_lines_no_more(self):
        rendered = self.html("$$\na\n\nb\n$$")
        self.assertNotIn("mfrac", rendered)

    def test_a_math_fence_is_a_display_equation(self):
        rendered = self.tree("```math\n\\frac{a}{b}\n```\n")
        self.assertIsNotNone(rendered.find(".//div[@class='m md']"))
        self.assertIsNotNone(rendered.find(".//*[@class='mfrac']"))

    def test_display_equations_work_inside_lists_and_quotes(self):
        self.assertIn('class="m md"', self.html("- item\n\n  $$\n  a + b\n  $$\n\n- next"))
        self.assertIn('class="m md"', self.html("> $$\n> x^2\n> $$"))

    def test_equations_work_in_tables_and_headings(self):
        rendered = self.html("| a | b |\n|---|---|\n| $\\alpha$ | $\\frac{x}{y}$ |\n\n## The $\\beta$ term\n")
        self.assertIn("mfrac", rendered)
        self.assertIn("<h2>", rendered)
        self.assertIn(chr(0x3B2), rendered)

    def test_markdown_characters_inside_an_equation_are_not_markdown(self):
        rendered = self.html(r"$a_1 * b_2 * c_3$ and $x_i * y_j *k*$")
        self.assertNotIn("<em>", rendered)
        self.assertNotIn("<strong>", rendered)

    def test_a_stray_display_delimiter_is_text_not_an_error(self):
        rendered = self.html("Amounts in $$ are estimates\n\nMore text")
        self.assertIn("More text", rendered)

    def test_every_refused_equation_is_named_once_by_line_and_command(self):
        error = self.refused("ok $x$\n\nbad $\\foo$ here\n\nalso $\\bar{$\n\n$$\n\\newcommand{\\a}{1}\n$$\n")
        self.assertEqual(error.code, ErrorCode.UNSUPPORTED_CAPABILITY)
        self.assertIn("3 equations cannot be laid out", error.message)
        for expected in ("line 3: \\foo", "line 5:", "line 7: \\newcommand"):
            self.assertIn(expected, error.message)
        self.assertLess(error.message.index("line 3"), error.message.index("line 5"))
        self.assertLess(error.message.index("line 5"), error.message.index("line 7"))

    def test_a_refusal_is_plain_invalid_input_when_only_the_syntax_is_wrong(self):
        error = self.refused(r"bad $\frac{a}{b$")
        self.assertEqual(error.code, ErrorCode.INVALID_INPUT)
        self.assertIn("1 equation cannot be laid out", error.message)

    def test_a_refusal_lists_at_most_five_and_counts_the_rest(self):
        error = self.refused("\n\n".join("$\\foo%s$" % suffix for suffix in "abcdefgh"))
        self.assertIn("8 equations cannot be laid out", error.message)
        self.assertIn("and 3 more", error.message)
        self.assertEqual(error.message.count("line "), 5)

    def test_a_refusal_carries_no_equation_text(self):
        error = self.refused(r"$\foo{confidentialfigure}$")
        self.assertNotIn("confidentialfigure", error.message)

    def test_a_refusal_points_at_the_repair(self):
        error = self.refused(r"$\foo$")
        self.assertIn("Rewrite it with supported TeX", error.message)
        self.assertIn("SKILL.md", error.message)

    def test_the_line_of_a_refused_equation_counts_from_the_first_line_given(self):
        from pdf_system.markdown_html import render_markdown

        with self.assertRaises(PdfError) as caught:
            render_markdown("a\n$\\foo$", self.policy, first_line=40)
        self.assertIn("line 41", caught.exception.message)

    def test_diagnose_reports_every_refused_equation_before_a_render(self):
        service = PdfService(self.policy)
        source = "fine\n\n$x$\n\f\n$\\foo$ and $\\bar{$\n"
        report = service.diagnose("markdown", source)["preflight"]
        self.assertFalse(report["ok"])
        self.assertEqual(report["error"]["class"], "invalid_user_input")
        self.assertFalse(report["error"]["retryable"])
        self.assertIn("2 equations cannot be laid out", report["error"]["message"])
        self.assertIn("line 5: \\foo", report["error"]["message"])

    def test_an_equation_free_document_is_unchanged(self):
        rendered = self.html("# Title\n\nPlain **text** with a [link](https://example.com).")
        self.assertNotIn('class="m', rendered)


@unittest.skipUnless(production_available(), "the production renderer is not installed")
class PrintedEquationTests(unittest.TestCase):
    def render(self, source: str, name: str = "doc") -> tuple:
        import pypdf

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / (name + ".md")).write_text(source, encoding="utf-8")
            policy = SecurityPolicy.for_paths(str(root / (name + ".md")), str(root / (name + ".pdf")))
            result = PdfService(policy).generate_markdown(
                root / (name + ".md"), root / (name + ".pdf"), quality_profile="professional"
            )
            reader = pypdf.PdfReader(str(root / (name + ".pdf")))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return result, text

    def test_a_document_of_fractions_renders_and_prints_no_source(self):
        source = (
            "# Gradients\n\n"
            "The loss $L = -\\frac{1}{N}\\sum_{i=1}^{N} \\log p_i$ has gradient $\\frac{\\partial L}{\\partial z_j} = p_j - y_j$.\n\n"
            "$$\n\\frac{\\partial L}{\\partial x} = \\sum_{i=1}^{n} w_i \\sqrt{x_i^2 + 1}\n$$\n\n"
            "```math\n\\begin{bmatrix} a & b \\\\ c & d \\end{bmatrix}\n```\n"
        )
        result, text = self.render(source)
        self.assertGreaterEqual(result.pages, 1)
        for leaked in ("\\frac", "\\sum", "\\partial", "\\sqrt", "\\begin", "\\alpha", "{", "}"):
            self.assertNotIn(leaked, text)
        for printed in (chr(0x2202), chr(0x2211), chr(0x221A), "L", "gradient"):
            self.assertIn(printed, text)

    def test_the_symbols_the_fonts_do_not_carry_still_print(self):
        _, text = self.render("Sets: $x \\in \\mathbb{R}$, $\\nabla f$, $A \\subseteq B \\cup C$, $a \\Rightarrow b$.\n")
        for printed in (chr(0x2208), chr(0x211D), chr(0x2207), chr(0x2286), chr(0x222A), chr(0x21D2)):
            self.assertIn(printed, text)

    def test_a_long_document_of_equations_renders(self):
        body = "\n\n".join(
            "Step %d: $$\\frac{\\partial L}{\\partial w_{%d}} = \\sum_{k} \\left(a_{%d k} - y_k\\right)^2$$" % (i, i, i)
            for i in range(80)
        )
        result, text = self.render("# Steps\n\n" + body)
        self.assertGreaterEqual(result.pages, 2)
        self.assertNotIn("\\frac", text)

    def test_a_refused_equation_is_refused_by_the_generator_before_any_file_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "doc.md").write_text("Bad $\\newcommand{\\x}{1}$\n", encoding="utf-8")
            policy = SecurityPolicy.for_paths(str(root / "doc.md"), str(root / "doc.pdf"))
            with self.assertRaises(PdfError) as caught:
                PdfService(policy).generate_markdown(root / "doc.md", root / "doc.pdf")
            self.assertEqual(caught.exception.code, ErrorCode.UNSUPPORTED_CAPABILITY)
            self.assertFalse((root / "doc.pdf").exists())


def _font_files():
    fonts = Path(os.environ.get("CHAINABIT_ARTIFACT_FONT_DIR", ""))
    plex = fonts / "IBMPlexSans-Regular.ttf"
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ):
        if Path(candidate).is_file() and plex.is_file():
            return plex, Path(candidate)
    return None


@unittest.skipUnless(_font_files() is not None, "the runtime fonts are not installed")
class GlyphCoverageTests(unittest.TestCase):
    def test_every_character_the_symbol_tables_print_exists_in_the_runtime_fonts(self):
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            self.skipTest("fontTools is not installed")
        plex, fallback = _font_files()
        covered = set(TTFont(str(plex)).getBestCmap()) | set(TTFont(str(fallback)).getBestCmap())
        missing = [glyph for glyph in symbol_glyphs() if any(ord(char) not in covered for char in glyph)]
        self.assertEqual(missing, [], "these would print as empty boxes: %r" % missing)


if __name__ == "__main__":
    unittest.main()
